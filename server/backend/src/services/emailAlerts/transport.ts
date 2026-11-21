import dns from 'node:dns';
import net from 'node:net';
import nodemailer from 'nodemailer';
import { isBlockedAddress, type SmtpSecurity } from './config.js';

/**
 * SMTP 전송(contracts/email-alerts.md "전송" 절). 레거시 emailService.ts(4ec85af)에서 SMTP 연결 생각만 가져왔다:
 * 인증서 검사는 끄지 않고(rejectUnauthorized 기본 true, TLS 1.2 이상), 실패를 성공으로 바꾸지 않고, 값·비밀번호를 로그에 남기지 않는다.
 * - 보안: tls = 접속부터 TLS(465 등), starttls = STARTTLS 필수(requireTLS — 서버가 STARTTLS를 안 주면 실패, 평문으로 내려가지 않음).
 * - 연결 대상: 호스트 이름은 보내기 전에 직접 풀어(5초) 금지 대역(루프백·링크 로컬·멀티캐스트 등) 주소를 빼고, 남은 첫 주소에 연결한다.
 *   인증서는 원래 호스트 이름으로 검사한다(servername). 확인과 연결 사이에 이름이 다른 주소로 바뀌어도(DNS rebinding) 확인한 주소로만 간다.
 * - 짧은 시간 제한(연결·인사 10초, 소켓 20초), 메시지 파일·URL 읽기 끔(disableFileAccess·disableUrlAccess), SMTP 말고 다른 전송·프록시 없음.
 * 오류는 classifySmtpError가 코드·일시 여부로 바꾼다(서버 응답 원문은 쓰지 않음).
 */
export type SmtpConfig = { host: string; port: number; security: SmtpSecurity; username: string | null; password: string | null; fromAddress: string };
export type MailMessage = { to: string; subject: string; text: string; html: string; messageId: string };
export type SendResult = { smtpCode: number | null };
export interface MailTransport {
  verify(): Promise<void>;
  send(message: MailMessage): Promise<SendResult>;
  close(): void;
}
export type TransportFactory = (config: SmtpConfig) => Promise<MailTransport>;

/** 메일 보내는 사람 이름(사용자 11/21 승인된 짧은 이름 `ESB 알림`). */
export const FROM_NAME = 'ESB 알림';
export const SMTP_TIMEOUTS = { connection: 10_000, greeting: 10_000, socket: 20_000, dns: 5_000 } as const;

/** 전송 계층 실패. transient = 다시 시도할 만함, transport = 연결 확인 결과에 남길 상태(null = 받는 사람·메시지 문제라 남기지 않음). */
export class SmtpError extends Error {
  constructor(readonly code: string, readonly transient: boolean, readonly transport: 'auth_failed' | 'error' | null, readonly smtpCode: number | null = null) { super(code); }
}

type Lookup = (host: string) => Promise<{ address: string; family: number }[]>;
const defaultLookup: Lookup = (host) => dns.promises.lookup(host, { all: true, verbatim: true });

/** 호스트 → 연결할 주소. IP는 그대로(금지 대역이면 거절), 이름은 풀어서 금지 대역을 뺀 첫 주소. */
export async function resolveSmtpAddress(host: string, lookup: Lookup = defaultLookup, timeoutMs: number = SMTP_TIMEOUTS.dns): Promise<string> {
  if (net.isIP(host)) {
    if (isBlockedAddress(host)) throw new SmtpError('smtp_host_blocked', false, 'error');
    return host;
  }
  let timer: ReturnType<typeof setTimeout> | undefined;
  let addresses: { address: string }[];
  try {
    addresses = await Promise.race([lookup(host), new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('dns timeout')), timeoutMs); })]);
  } catch {
    throw new SmtpError('smtp_host_unresolved', true, 'error');
  } finally { clearTimeout(timer); }
  const allowed = addresses.map((entry) => entry.address).filter((address) => net.isIP(address) && !isBlockedAddress(address));
  if (!allowed.length) throw new SmtpError(addresses.length ? 'smtp_host_blocked' : 'smtp_host_unresolved', !addresses.length, 'error');
  return allowed[0];
}

/** nodemailer SMTP 전송 옵션(시험이 옵션 자체를 검사한다). address = resolveSmtpAddress 결과. */
export function nodemailerOptions(config: SmtpConfig, address: string) {
  return {
    host: address,
    port: config.port,
    secure: config.security === 'tls',
    requireTLS: config.security === 'starttls',
    ignoreTLS: false,
    opportunisticTLS: false,
    ...(net.isIP(config.host) ? {} : { servername: config.host }),
    ...(config.username ? { auth: { user: config.username, pass: config.password ?? '' } } : {}),
    tls: { rejectUnauthorized: true, minVersion: 'TLSv1.2' as const },
    connectionTimeout: SMTP_TIMEOUTS.connection,
    greetingTimeout: SMTP_TIMEOUTS.greeting,
    socketTimeout: SMTP_TIMEOUTS.socket,
    dnsTimeout: SMTP_TIMEOUTS.dns,
    disableFileAccess: true,
    disableUrlAccess: true,
    logger: false,
    debug: false,
  };
}

/** 운영 전송: 호스트 확인 뒤 nodemailer 전송 하나(연결 풀 없음). 실패는 SmtpError로. */
export function createNodemailerFactory(lookup: Lookup = defaultLookup): TransportFactory {
  return async (config) => {
    const address = await resolveSmtpAddress(config.host, lookup);
    const transporter = nodemailer.createTransport(nodemailerOptions(config, address));
    return {
      async verify() {
        try { await transporter.verify(); } catch (error) { throw classifySmtpError(error); }
      },
      async send(message) {
        try {
          const info = await transporter.sendMail({
            from: { name: FROM_NAME, address: config.fromAddress }, to: message.to, subject: message.subject, text: message.text, html: message.html,
            messageId: message.messageId, headers: { 'Auto-Submitted': 'auto-generated', 'X-Auto-Response-Suppress': 'All' },
            disableFileAccess: true, disableUrlAccess: true,
          });
          if (Array.isArray(info.rejected) && info.rejected.length) throw new SmtpError('smtp_recipient_rejected', false, null);
          return { smtpCode: responseCode(info.response) };
        } catch (error) { throw classifySmtpError(error); }
      },
      close() { transporter.close(); },
    };
  };
}

const responseCode = (value: unknown) => {
  const code = typeof value === 'string' ? Number(/^(\d{3})/.exec(value)?.[1]) : typeof value === 'number' ? value : NaN;
  return Number.isInteger(code) && code >= 200 && code <= 599 ? code : null;
};
const CONNECTION = new Set(['ECONNECTION', 'ESOCKET', 'ECONNREFUSED', 'ECONNRESET', 'EHOSTUNREACH', 'ENETUNREACH', 'EPIPE', 'EDNS', 'ENOTFOUND', 'EAI_AGAIN']);
const TIMEOUT = new Set(['ETIMEDOUT', 'CONNECT_TIMEOUT', 'GREETING_TIMEOUT', 'SOCKET_TIMEOUT']);
const TLS = /^(ETLS|EREQUIRETLS|CERT_|ERR_TLS_|ERR_SSL_|UNABLE_TO_|DEPTH_ZERO_|SELF_SIGNED_|ERR_OSSL)/;

/**
 * 오류 → 코드. 일시(다시 시도) = 연결·시간 초과·이름 풀기 실패·4xx 응답. 영구 = 인증 실패(535 등)·TLS·인증서·5xx 응답·메시지 오류.
 * 서버 응답 원문(err.message·err.response)은 버린다. 응답 번호(3자리)만 남긴다.
 */
export function classifySmtpError(error: unknown): SmtpError {
  if (error instanceof SmtpError) return error;
  const e = (error ?? {}) as { code?: unknown; originalCode?: unknown; responseCode?: unknown };
  const code = typeof e.code === 'string' ? e.code : '';
  // nodemailer는 시스템 오류 코드(인증서 오류 등)를 originalCode로 옮기고 code에 단계(ESOCKET 등)를 넣는다.
  const original = typeof e.originalCode === 'string' ? e.originalCode : '';
  const smtpCode = responseCode(e.responseCode);
  if (code === 'EAUTH' || code === 'ENOAUTH') return new SmtpError('smtp_auth_failed', false, 'auth_failed', smtpCode);
  if (TLS.test(code) || TLS.test(original)) return new SmtpError('smtp_tls_failed', false, 'error', smtpCode);
  if (TIMEOUT.has(code)) return new SmtpError('smtp_timeout', true, 'error', smtpCode);
  if (CONNECTION.has(code)) return new SmtpError('smtp_connection_failed', true, 'error', smtpCode);
  if (code === 'EMESSAGE' || code === 'EFILEACCESS' || code === 'EURLACCESS') return new SmtpError('smtp_message_failed', false, null, smtpCode);
  if (smtpCode !== null && smtpCode >= 500) return new SmtpError(code === 'EENVELOPE' ? 'smtp_recipient_rejected' : 'smtp_rejected', false, null, smtpCode);
  if (smtpCode !== null && smtpCode >= 400) return new SmtpError('smtp_temporary_failure', true, null, smtpCode);
  return new SmtpError('smtp_error', true, 'error', smtpCode);
}
