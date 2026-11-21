import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

/**
 * 설정 비밀값(SMTP 비밀번호) 암호화(contracts/email-alerts.md "비밀번호 저장" 절). DB에는 암호문만 두고 키는 DB 밖에 둔다.
 * - 형식 v1: `v1.<키 id 8 hex>.<IV 12바이트>.<tag 16바이트>.<암호문>`(base64url). AES-256-GCM, 쓸 때마다 새 무작위 IV,
 *   AAD = 용도 이름(다른 칸의 암호문을 옮겨 붙여도 풀리지 않음). 키 id = sha256(키) 앞 8 hex(키가 바뀌었는지 풀기 전에 안다).
 * - 키 출처(앞이 이김): 환경 변수 SETTINGS_SECRET_KEY(base64 32바이트) → 키 파일(SETTINGS_SECRET_KEY_FILE, 기본 = API 데이터 폴더
 *   `dirname(USER_DATA_PATH)/settings-secret.key`). 파일이 없으면 비밀번호를 처음 저장할 때 한 번 만든다(임시 파일 0600에 쓰고 linkSync로 원자적으로 걸음).
 *   API 데이터 폴더는 compose의 AUTH_DATA_DIR bind mount라 컨테이너를 다시 만들어도 남는다. DB·Git에는 키가 없다.
 * - 키를 잃으면 저장된 비밀번호는 풀 수 없다 → 관리자가 비밀번호를 다시 넣는다(평문 대체·성공 대체 없음).
 * 키·평문은 로그·오류에 넣지 않는다. 오류는 SecretBoxError 코드만.
 */
export const SEALED_VERSION = 'v1';
const AAD = Buffer.from('powercode/email-alerts/smtp-password/v1', 'utf8');
const SEALED = /^v1\.([0-9a-f]{8})\.([A-Za-z0-9_-]{16})\.([A-Za-z0-9_-]{22})\.([A-Za-z0-9_-]+)$/;

export class SecretBoxError extends Error {
  constructor(readonly code: 'email_secret_key_unavailable' | 'email_secret_key_invalid' | 'smtp_password_unreadable') { super(code); }
}

export type SecretKey = { key: Buffer; id: string; source: 'env' | 'file' };
export type KeySource = {
  /** 저장(암호화) 때: 키가 없으면 파일을 만든다. 읽기(풀기·상태) 때: 만들지 않는다. */
  load(create: boolean): SecretKey | null;
};

const keyId = (key: Buffer) => crypto.createHash('sha256').update(key).digest('hex').slice(0, 8);
function decodeKey(text: string): Buffer | null {
  const trimmed = text.trim();
  if (!/^[A-Za-z0-9+/_-]+={0,2}$/.test(trimmed)) return null;
  const key = Buffer.from(trimmed.replace(/-/g, '+').replace(/_/g, '/'), 'base64');
  return key.length === 32 ? key : null;
}

export type KeySourceOptions = { envKey?: string | undefined; filePath: string; warn?: (code: string) => void };

/**
 * 키 출처. envKey가 비어 있지 않으면 그 값만 쓴다(형식이 틀리면 email_secret_key_invalid — 파일로 넘어가지 않는다).
 * 파일: 있으면 읽고(형식이 틀리면 email_secret_key_invalid), 없고 create이면 새 키를 원자적으로 만든다(동시 생성은 먼저 걸린 쪽 키를 다시 읽음).
 * 만들 수 없으면(읽기 전용 폴더 등) null → 비밀번호 저장은 email_secret_key_unavailable로 실패한다.
 * 파일 권한이 그룹·기타에게 열려 있으면 경고 코드 한 번(값 없이).
 */
export function createKeySource({ envKey, filePath, warn = () => {} }: KeySourceOptions): KeySource {
  let cached: SecretKey | null = null;
  let warned = false;
  return {
    load(create) {
      if (cached) return cached;
      if (envKey !== undefined && envKey.trim() !== '') {
        const key = decodeKey(envKey);
        if (!key) throw new SecretBoxError('email_secret_key_invalid');
        return (cached = { key, id: keyId(key), source: 'env' });
      }
      const read = (): SecretKey | null => {
        let text: string;
        try { text = fs.readFileSync(filePath, 'utf8'); } catch (error) {
          if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
          throw new SecretBoxError('email_secret_key_unavailable');
        }
        const key = decodeKey(text);
        if (!key) throw new SecretBoxError('email_secret_key_invalid');
        if (process.platform !== 'win32' && !warned) {
          try { if ((fs.statSync(filePath).mode & 0o077) !== 0) { warned = true; warn('email_secret_key_file_permissions_open'); } } catch { /* 경고만 */ }
        }
        return (cached = { key, id: keyId(key), source: 'file' });
      };
      const existing = read();
      if (existing || !create) return existing;
      // 원자적 생성: 임시 파일(0600)에 다 쓰고 fsync한 뒤 linkSync로 제자리에 건다. 이름이 생기는 순간 내용이 이미 다 있으므로
      // 도중에 멈춰도 빈·반쪽 키 파일이 남지 않고, 동시에 만든 쪽(EEXIST)은 먼저 걸린 완성된 키를 읽는다. 임시 파일은 항상 지운다.
      const key = crypto.randomBytes(32);
      const temporary = `${filePath}.${process.pid}.${crypto.randomBytes(6).toString('hex')}.tmp`;
      let linked = false;
      try {
        fs.mkdirSync(path.dirname(filePath), { recursive: true });
        const fd = fs.openSync(temporary, 'wx', 0o600);
        try { fs.writeFileSync(fd, `${key.toString('base64')}\n`); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
        fs.linkSync(temporary, filePath);
        linked = true;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'EEXIST') return null;
      } finally {
        try { fs.unlinkSync(temporary); } catch { /* 없으면 그대로 */ }
      }
      if (!linked) return read();
      if (process.platform !== 'win32') {
        // 폴더 항목도 디스크에 남긴다(전원이 끊겨도 키 파일 이름이 사라지지 않게). 실패해도 키는 이미 제자리에 있다.
        try { const dir = fs.openSync(path.dirname(filePath), 'r'); try { fs.fsyncSync(dir); } finally { fs.closeSync(dir); } } catch { /* 무시 */ }
      }
      return (cached = { key, id: keyId(key), source: 'file' });
    },
  };
}

/** API 기본 키 출처: SETTINGS_SECRET_KEY → SETTINGS_SECRET_KEY_FILE → API 데이터 폴더(USER_DATA_PATH와 같은 폴더)의 settings-secret.key. */
export function defaultKeySource(warn?: (code: string) => void): KeySource {
  const dataPath = path.resolve(process.env.USER_DATA_PATH?.trim() || path.join(process.cwd(), 'data', 'users.json'));
  const filePath = path.resolve(process.env.SETTINGS_SECRET_KEY_FILE?.trim() || path.join(path.dirname(dataPath), 'settings-secret.key'));
  return createKeySource({ envKey: process.env.SETTINGS_SECRET_KEY, filePath, warn });
}

export function seal(plaintext: string, key: SecretKey): string {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key.key, iv);
  cipher.setAAD(AAD);
  const body = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()]);
  return [SEALED_VERSION, key.id, iv.toString('base64url'), cipher.getAuthTag().toString('base64url'), body.toString('base64url')].join('.');
}

/** 저장된 암호문의 키 id(형식이 아니면 null). */
export const sealedKeyId = (sealed: string) => SEALED.exec(sealed)?.[1] ?? null;

export function open(sealed: string, key: SecretKey): string {
  const parts = SEALED.exec(sealed);
  if (!parts || parts[1] !== key.id) throw new SecretBoxError('smtp_password_unreadable');
  try {
    const decipher = crypto.createDecipheriv('aes-256-gcm', key.key, Buffer.from(parts[2], 'base64url'));
    decipher.setAAD(AAD);
    decipher.setAuthTag(Buffer.from(parts[3], 'base64url'));
    return Buffer.concat([decipher.update(Buffer.from(parts[4], 'base64url')), decipher.final()]).toString('utf8');
  } catch {
    throw new SecretBoxError('smtp_password_unreadable');
  }
}
