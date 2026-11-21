import net from 'node:net';

/**
 * 메일 알림 설정 값 검사(contracts/email-alerts.md "설정" 절). DB 입출력 없음.
 * - 받는 사람: 관리자가 넣는 주소 목록(계정과 무관), 최대 RECIPIENT_MAX개.
 * - SMTP: 호스트 이름 또는 IP만(주소·경로·포트 붙임 금지), 포트는 표준 SMTP 포트만, 보안은 tls(접속부터 TLS) | starttls(STARTTLS 필수). 평문 SMTP 없음.
 * - 대시보드 주소: 메일 링크 앞부분(http(s) + 호스트 + 선택 경로). 쿼리·조각·계정 정보 금지.
 * 오류는 값 없이 코드만 낸다(EmailSettingsError) — 비밀번호를 포함한 입력값을 오류에 담지 않는다.
 */
export const RECIPIENT_MAX = 10;
/** 표준 SMTP 포트(25 relay, 465 암묵 TLS, 587 submission, 2525 대체 submission). 다른 포트로 내부 서비스를 두드리지 못하게 한다(033 CHECK와 같음). */
export const SMTP_PORTS = [25, 465, 587, 2525] as const;
export const SMTP_SECURITY = ['tls', 'starttls'] as const;
export const ALERT_KINDS = ['incident', 'decision'] as const;
export const RISK_LEVELS = ['info', 'warning', 'critical'] as const;
export const PASSWORD_MAX = 512;
export const USERNAME_MAX = 320;
export const PUBLIC_URL_MAX = 512;
export type SmtpSecurity = typeof SMTP_SECURITY[number];
export type AlertKind = typeof ALERT_KINDS[number];
export type RiskLevel = typeof RISK_LEVELS[number];
export const RISK_RANK: Record<RiskLevel, number> = { info: 1, warning: 2, critical: 3 };
export const RANK_LEVEL: Record<number, RiskLevel> = { 1: 'info', 2: 'warning', 3: 'critical' };

export class EmailSettingsError extends Error {
  constructor(readonly code: string, readonly status = 400, readonly index?: number) { super(code); }
}

const plainObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const keysExactly = (value: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []) =>
  required.every((key) => Object.hasOwn(value, key)) && Object.keys(value).every((key) => required.includes(key) || optional.includes(key));
const hasControl = (value: string) => /[\u0000-\u001f\u007f]/.test(value);

const LOCAL = /^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$/;
const LABEL = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/;
/**
 * 메일 주소: ASCII만, local@domain(점으로 나뉜 라벨 2개 이상), local 64자·전체 254자 이하. 공백·쉼표·꺾쇠·따옴표·줄바꿈이 없어 헤더에 그대로 넣어도 주소 1개다.
 * 정규화: 앞뒤 공백 제거, 도메인 소문자. 형식이 아니면 null.
 */
export function normalizeEmail(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const text = value.trim();
  if (!text || text.length > 254 || hasControl(text)) return null;
  const at = text.lastIndexOf('@');
  if (at <= 0 || at !== text.indexOf('@')) return null;
  const local = text.slice(0, at), domain = text.slice(at + 1).toLowerCase();
  const labels = domain.split('.');
  if (local.length > 64 || !LOCAL.test(local) || labels.length < 2 || !labels.every((label) => LABEL.test(label)) || /^\d+$/.test(labels.at(-1)!)) return null;
  return `${local}@${domain}`;
}

/** 받는 사람 목록: 배열, 최대 RECIPIENT_MAX개, 형식·중복(대소문자 무시) 검사. 오류는 그 위치(index)와 함께. */
export function parseRecipients(value: unknown): string[] {
  if (!Array.isArray(value)) throw new EmailSettingsError('invalid_email_settings');
  if (value.length > RECIPIENT_MAX) throw new EmailSettingsError('too_many_email_recipients');
  const seen = new Set<string>();
  return value.map((entry, index) => {
    const address = normalizeEmail(entry);
    if (!address) throw new EmailSettingsError('invalid_email_recipient', 400, index);
    if (seen.has(address.toLowerCase())) throw new EmailSettingsError('duplicate_email_recipient', 400, index);
    seen.add(address.toLowerCase());
    return address;
  });
}

const BLOCKED = new net.BlockList();
// 연결하면 안 되는 주소: 지정 안 됨·루프백(컨테이너 자신)·링크 로컬·클라우드 메타데이터(169.254.169.254는 링크 로컬, AWS IPv6 fd00:ec2::254,
// Alibaba 100.100.100.200)·멀티캐스트·예약, IPv4를 품은 IPv6 형식(IPv4 호환 ::/96, IPv4 변환 ::ffff:0:0:0/96, NAT64 64:ff9b::/96 — 안의 IPv4가
// 루프백·메타데이터일 수 있어 통째로 막는다). 사설 대역은 내부 메일 중계 때문에 허용한다.
// IPv4 대응 IPv6(::ffff:a.b.c.d, ::ffff:7f00:1)는 BlockList가 IPv4 규칙으로 검사한다(아래 정규식은 점 표기를 한 번 더 확인).
for (const [address, prefix] of [['0.0.0.0', 8], ['127.0.0.0', 8], ['169.254.0.0', 16], ['100.100.100.200', 32], ['224.0.0.0', 4], ['240.0.0.0', 4]] as const) {
  BLOCKED.addSubnet(address, prefix, 'ipv4');
}
for (const [address, prefix] of [['::', 96], ['::ffff:0:0:0', 96], ['64:ff9b::', 96], ['fd00:ec2::254', 128], ['fe80::', 10], ['ff00::', 8]] as const) {
  BLOCKED.addSubnet(address, prefix, 'ipv6');
}
/** IP 주소가 연결 금지 대역인가(IPv4 대응 IPv6는 IPv4로 본다). IP가 아니면 false. */
export function isBlockedAddress(address: string): boolean {
  const mapped = /^::ffff:(\d{1,3}(?:\.\d{1,3}){3})$/i.exec(address);
  if (mapped) return BLOCKED.check(mapped[1], 'ipv4');
  if (net.isIPv4(address)) return BLOCKED.check(address, 'ipv4');
  if (net.isIPv6(address)) return BLOCKED.check(address, 'ipv6');
  return false;
}

/**
 * SMTP 호스트: 호스트 이름(라벨 1개 이상, 253자 이하) 또는 IPv4·IPv6 주소(꺾쇠 없이). 앞뒤 공백 제거, 이름은 소문자.
 * 주소 형식(scheme://)·경로·포트 붙임·계정(@)은 형식 오류. 루프백·링크 로컬 등 금지 대역 주소와 localhost는 smtp_host_blocked.
 * 이름이 가리키는 주소는 보낼 때 다시 확인한다(transport.ts resolveSmtpAddress).
 */
export function parseSmtpHost(value: unknown): string {
  if (typeof value !== 'string') throw new EmailSettingsError('invalid_smtp_host');
  const text = value.trim();
  if (!text || text.length > 253 || hasControl(text)) throw new EmailSettingsError('invalid_smtp_host');
  if (net.isIP(text)) {
    if (isBlockedAddress(text)) throw new EmailSettingsError('smtp_host_blocked');
    return text.toLowerCase();
  }
  const name = text.toLowerCase().replace(/\.$/, '');
  if (!name.split('.').every((label) => LABEL.test(label)) || /^\d+(\.\d+)*$/.test(name)) throw new EmailSettingsError('invalid_smtp_host');
  if (name === 'localhost' || name.endsWith('.localhost')) throw new EmailSettingsError('smtp_host_blocked');
  return name;
}

/** 대시보드 주소: http(s), 계정·쿼리·조각 없음, 경로 끝 / 제거. 저장값 = origin + 경로. */
export function parsePublicBaseUrl(value: unknown): string {
  if (typeof value !== 'string' || !value.trim() || value.length > PUBLIC_URL_MAX || hasControl(value)) throw new EmailSettingsError('invalid_public_base_url');
  let url: URL;
  try { url = new URL(value.trim()); } catch { throw new EmailSettingsError('invalid_public_base_url'); }
  if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password || url.search || url.hash
    || value.includes('?') || value.includes('#')) throw new EmailSettingsError('invalid_public_base_url');
  const normalized = `${url.origin}${url.pathname.replace(/\/+$/, '')}`;
  if (normalized.length > PUBLIC_URL_MAX) throw new EmailSettingsError('invalid_public_base_url');
  return normalized;
}

/** PUT 본문의 SMTP 부분. password: 생략 = 저장값 유지, null = 지움, 문자열 = 새 값(1~512자). */
export type SmtpUpdate = {
  host: string | null; port: number | null; security: SmtpSecurity | null; username: string | null; fromAddress: string | null;
  password: { action: 'keep' } | { action: 'clear' } | { action: 'set'; value: string };
};
export type SettingsUpdate = {
  enabled: boolean;
  kinds: { incident: boolean; decision: boolean };
  minRiskLevel: RiskLevel;
  recipients: string[];
  smtp: SmtpUpdate;
  publicBaseUrl: string | null;
};

function parseSmtp(value: unknown): SmtpUpdate {
  if (!plainObject(value) || !keysExactly(value, ['host', 'port', 'security', 'username', 'fromAddress'], ['password'])) throw new EmailSettingsError('invalid_email_settings');
  const host = value.host === null ? null : parseSmtpHost(value.host);
  const port = value.port;
  if (port !== null && (typeof port !== 'number' || !(SMTP_PORTS as readonly number[]).includes(port))) throw new EmailSettingsError('invalid_smtp_port');
  const security = value.security;
  if (security !== null && (typeof security !== 'string' || !(SMTP_SECURITY as readonly string[]).includes(security))) throw new EmailSettingsError('invalid_smtp_security');
  let username: string | null = null;
  if (value.username !== null) {
    if (typeof value.username !== 'string' || !value.username.trim() || value.username.trim().length > USERNAME_MAX || hasControl(value.username)) {
      throw new EmailSettingsError('invalid_smtp_username');
    }
    username = value.username.trim();
  }
  const fromAddress = value.fromAddress === null ? null : normalizeEmail(value.fromAddress);
  if (value.fromAddress !== null && !fromAddress) throw new EmailSettingsError('invalid_email_from');
  let password: SmtpUpdate['password'] = { action: 'keep' };
  if (Object.hasOwn(value, 'password')) {
    const raw = value.password;
    if (raw === null) password = { action: 'clear' };
    // 비밀번호는 다듬지 않는다(앞뒤 공백도 비밀번호일 수 있음). NUL·줄바꿈만 막는다(SMTP AUTH 명령 줄을 깨지 않게).
    else if (typeof raw === 'string' && raw.length >= 1 && raw.length <= PASSWORD_MAX && !/[\u0000\r\n]/.test(raw)) password = { action: 'set', value: raw };
    else throw new EmailSettingsError('invalid_smtp_password');
  }
  if (password.action === 'set' && username === null) throw new EmailSettingsError('invalid_smtp_password');
  return { host, port: port as number | null, security: security as SmtpSecurity | null, username, fromAddress, password };
}

/** PUT /api/email-alerts/settings 본문 전체(보낸 값이 저장값을 모두 바꾼다, 비밀번호만 생략 가능). */
export function parseSettingsUpdate(body: unknown): SettingsUpdate {
  if (!plainObject(body) || !keysExactly(body, ['enabled', 'rules', 'recipients', 'smtp', 'publicBaseUrl'])) throw new EmailSettingsError('invalid_email_settings');
  if (typeof body.enabled !== 'boolean') throw new EmailSettingsError('invalid_email_settings');
  const rules = body.rules;
  if (!plainObject(rules) || !keysExactly(rules, ['kinds', 'minRiskLevel']) || !Array.isArray(rules.kinds)
    || rules.kinds.some((kind) => typeof kind !== 'string' || !(ALERT_KINDS as readonly string[]).includes(kind))
    || new Set(rules.kinds).size !== rules.kinds.length
    || typeof rules.minRiskLevel !== 'string' || !(RISK_LEVELS as readonly string[]).includes(rules.minRiskLevel)) throw new EmailSettingsError('invalid_email_settings');
  const recipients = parseRecipients(body.recipients);
  const smtp = parseSmtp(body.smtp);
  const publicBaseUrl = body.publicBaseUrl === null ? null : parsePublicBaseUrl(body.publicBaseUrl);
  const kinds = { incident: rules.kinds.includes('incident'), decision: rules.kinds.includes('decision') };
  if (body.enabled) {
    if (!kinds.incident && !kinds.decision) throw new EmailSettingsError('email_kinds_required');
    if (!recipients.length) throw new EmailSettingsError('email_recipients_required');
  }
  return { enabled: body.enabled, kinds, minRiskLevel: rules.minRiskLevel as RiskLevel, recipients, smtp, publicBaseUrl };
}
