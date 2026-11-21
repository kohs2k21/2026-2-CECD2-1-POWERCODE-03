// 메일 알림(contracts/email-alerts.md) 단위 시험: 설정 값 검사·비밀번호 암호화·전송 옵션·오류 분류·재시도·메일 문구·전송 상태. DB·네트워크·실제 메일 없음.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const config = await import('../dist/services/emailAlerts/config.js');
const box = await import('../dist/services/emailAlerts/secretBox.js');
const transport = await import('../dist/services/emailAlerts/transport.js');
const template = await import('../dist/services/emailAlerts/template.js');
const store = await import('../dist/services/emailAlerts/settingsStore.js');
const outbox = await import('../dist/services/emailAlerts/outbox.js');
const runner = await import('../dist/services/emailAlerts/runner.js');
const here = path.dirname(fileURLToPath(import.meta.url));
const code = (fn) => { try { fn(); } catch (error) { return error.code; } return 'accepted'; };
const fixedKey = (seed = 7) => { const key = Buffer.alloc(32, seed); return { key, id: box.createKeySource({ envKey: key.toString('base64'), filePath: 'unused' }).load(false).id, source: 'env' }; };
const keysOf = (key) => ({ load: () => key });

test('email addresses: ASCII local@domain, trims, lowercases the domain, rejects header injection and lists', () => {
  assert.equal(config.normalizeEmail('  Ops.Team+alerts@Example.CO.kr '), 'Ops.Team+alerts@example.co.kr');
  for (const bad of ['ops', 'ops@', '@example.test', 'a@b', 'a b@example.test', 'a@example.test\r\nBcc: x@evil.test', 'a@example.test,b@example.test',
    '"a"@example.test', '<a@example.test>', 'a..b@example.test', 'a@-x.example.test', 'a@example.123', 'ü@example.test', `${'a'.repeat(65)}@example.test`, 12]) {
    assert.equal(config.normalizeEmail(bad), null, String(bad));
  }
  assert.deepEqual(config.parseRecipients(['a@example.test', 'B@Example.test']), ['a@example.test', 'B@example.test']);
  assert.equal(code(() => config.parseRecipients(['a@example.test', 'A@EXAMPLE.TEST'])), 'duplicate_email_recipient');
  const error = (() => { try { config.parseRecipients(['a@example.test', 'nope']); } catch (e) { return e; } })();
  assert.deepEqual([error.code, error.index], ['invalid_email_recipient', 1]);
  assert.equal(code(() => config.parseRecipients(Array.from({ length: 11 }, (_, i) => `r${i}@example.test`))), 'too_many_email_recipients');
  assert.equal(code(() => config.parseRecipients('a@example.test')), 'invalid_email_settings');
});

test('SMTP host: hostname or IP only; loopback, link-local, multicast and localhost are blocked; private relays allowed', () => {
  assert.equal(config.parseSmtpHost(' SMTP.Example.TEST. '), 'smtp.example.test');
  assert.equal(config.parseSmtpHost('mailrelay'), 'mailrelay');
  assert.equal(config.parseSmtpHost('10.1.2.3'), '10.1.2.3');
  assert.equal(config.parseSmtpHost('fd00::25'), 'fd00::25');
  for (const blocked of ['127.0.0.1', '127.8.8.8', '0.0.0.0', '169.254.169.254', '224.0.0.1', '::1', '::', 'fe80::1', '::ffff:127.0.0.1', 'localhost', 'mail.localhost']) {
    assert.equal(code(() => config.parseSmtpHost(blocked)), 'smtp_host_blocked', blocked);
  }
  for (const bad of ['', 'smtp://smtp.example.test', 'smtp.example.test:587', 'user@smtp.example.test', 'smtp.example.test/path', '[::1]', '-bad.example.test',
    'a_b.example.test', '1.2.3', 'x'.repeat(254), 42, null]) {
    assert.equal(code(() => config.parseSmtpHost(bad)), 'invalid_smtp_host', String(bad));
  }
  assert.equal(config.isBlockedAddress('192.168.0.10'), false);
  assert.equal(config.isBlockedAddress('::ffff:169.254.1.1'), true);
  // 클라우드 메타데이터·IPv4를 품은 IPv6 형식(IPv4 호환·변환·NAT64)·16진 IPv4 대응 주소.
  for (const blocked of ['100.100.100.200', 'fd00:ec2::254', '::127.0.0.1', '::ffff:0:7f00:1', '64:ff9b::7f00:1', '64:ff9b::808:808', '::ffff:7f00:1']) {
    assert.equal(config.isBlockedAddress(blocked), true, blocked);
    assert.equal(code(() => config.parseSmtpHost(blocked)), 'smtp_host_blocked', blocked);
  }
  for (const allowed of ['100.100.100.201', 'fd00::254', '::ffff:8.8.8.8', '2001:db8::25']) assert.equal(config.isBlockedAddress(allowed), false, allowed);
});

test('dashboard URL: http(s) origin + optional path, trailing slash removed; no credentials, query, fragment or other schemes', () => {
  assert.equal(config.parsePublicBaseUrl('http://192.168.21.2:8080/'), 'http://192.168.21.2:8080');
  assert.equal(config.parsePublicBaseUrl('https://Dash.Example.test/esb/'), 'https://dash.example.test/esb');
  for (const bad of ['dash.example.test', 'javascript:alert(1)', 'ftp://dash.example.test', 'http://u:p@dash.example.test', 'http://dash.example.test/?x=1',
    'http://dash.example.test/?', 'http://dash.example.test/#a', `http://dash.example.test/${'a'.repeat(520)}`, '']) {
    assert.equal(code(() => config.parsePublicBaseUrl(bad)), 'invalid_public_base_url', bad);
  }
});

const body = (patch = {}, smtp = {}) => ({
  enabled: false, rules: { kinds: ['incident'], minRiskLevel: 'critical' }, recipients: ['ops@example.test'],
  smtp: { host: 'smtp.example.test', port: 587, security: 'starttls', username: 'alerts', fromAddress: 'alerts@example.test', ...smtp },
  publicBaseUrl: 'http://dash.example.test:8080', ...patch,
});

test('settings body: exact keys, port allowlist, TLS-only security, password keep/clear/set semantics, enable prerequisites', () => {
  const kept = config.parseSettingsUpdate(body());
  assert.deepEqual(kept.smtp.password, { action: 'keep' });
  assert.deepEqual(kept.kinds, { incident: true, decision: false });
  assert.deepEqual(config.parseSettingsUpdate(body({}, { password: null })).smtp.password, { action: 'clear' });
  assert.deepEqual(config.parseSettingsUpdate(body({}, { password: ' s3cret with spaces ' })).smtp.password, { action: 'set', value: ' s3cret with spaces ' });
  assert.equal(code(() => config.parseSettingsUpdate(body({}, { password: 'x', username: null }))), 'invalid_smtp_password', 'a password needs a username');
  assert.equal(code(() => config.parseSettingsUpdate(body({}, { password: '' }))), 'invalid_smtp_password');
  assert.equal(code(() => config.parseSettingsUpdate(body({}, { password: 'a\r\nb' }))), 'invalid_smtp_password');
  assert.equal(code(() => config.parseSettingsUpdate(body({}, { password: 'p'.repeat(513) }))), 'invalid_smtp_password');
  for (const port of [5432, 0, 65536, '587', 2526]) assert.equal(code(() => config.parseSettingsUpdate(body({}, { port }))), 'invalid_smtp_port', String(port));
  for (const port of [25, 465, 587, 2525]) assert.equal(config.parseSettingsUpdate(body({}, { port })).smtp.port, port);
  assert.equal(code(() => config.parseSettingsUpdate(body({}, { security: 'none' }))), 'invalid_smtp_security');
  assert.equal(code(() => config.parseSettingsUpdate(body({}, { fromAddress: 'not-an-address' }))), 'invalid_email_from');
  assert.equal(code(() => config.parseSettingsUpdate(body({}, { url: 'smtp://x' }))), 'invalid_email_settings', 'unknown SMTP key');
  assert.equal(code(() => config.parseSettingsUpdate({ ...body(), extra: 1 })), 'invalid_email_settings');
  assert.equal(code(() => config.parseSettingsUpdate(body({ rules: { kinds: ['incident', 'incident'], minRiskLevel: 'critical' } }))), 'invalid_email_settings');
  assert.equal(code(() => config.parseSettingsUpdate(body({ rules: { kinds: ['job'], minRiskLevel: 'critical' } }))), 'invalid_email_settings');
  assert.equal(code(() => config.parseSettingsUpdate(body({ enabled: true, recipients: [] }))), 'email_recipients_required');
  assert.equal(code(() => config.parseSettingsUpdate(body({ enabled: true, rules: { kinds: [], minRiskLevel: 'warning' } }))), 'email_kinds_required');
  const all = config.parseSettingsUpdate(body({ enabled: true, rules: { kinds: ['decision', 'incident'], minRiskLevel: 'warning' }, publicBaseUrl: null },
    { host: null, port: null, security: null, username: null, fromAddress: null }));
  assert.deepEqual([all.kinds, all.minRiskLevel, all.smtp.host, all.publicBaseUrl], [{ incident: true, decision: true }, 'warning', null, null]);
  // 오류에 입력값(비밀번호)이 실리지 않는다.
  const error = (() => { try { config.parseSettingsUpdate(body({}, { password: 'TopSecret\n' })); } catch (e) { return e; } })();
  assert.equal(JSON.stringify({ message: error.message, code: error.code }).includes('TopSecret'), false);
});

test('secret box: AES-256-GCM v1 format matching the 033 CHECK, fresh IV per seal, key id, tamper and wrong key rejected', () => {
  const key = fixedKey(1);
  const sealed = box.seal('p@ss wörd', key);
  assert.match(sealed, /^v1\.[0-9a-f]{8}\.[A-Za-z0-9_-]{16}\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]+$/);
  assert.equal(sealed.includes('p@ss'), false);
  assert.notEqual(box.seal('p@ss wörd', key), sealed, 'random IV per write');
  assert.equal(box.open(sealed, key), 'p@ss wörd');
  assert.equal(box.sealedKeyId(sealed), key.id);
  assert.equal(code(() => box.open(sealed, fixedKey(2))), 'smtp_password_unreadable', 'other key');
  const parts = sealed.split('.');
  const flipped = [...parts.slice(0, 4), (parts[4][0] === 'A' ? 'B' : 'A') + parts[4].slice(1)].join('.');
  assert.equal(code(() => box.open(flipped, key)), 'smtp_password_unreadable', 'tampered ciphertext');
  assert.equal(code(() => box.open('v1.zz', key)), 'smtp_password_unreadable');
});

test('key source: env key wins (invalid env is an error, no fallback), file key created once with 0600, read-only lookups never create', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'powercode-email-key-'));
  try {
    const file = path.join(dir, 'nested', 'settings-secret.key');
    const env = Buffer.alloc(32, 9).toString('base64');
    assert.equal(box.createKeySource({ envKey: env, filePath: file }).load(true).source, 'env');
    assert.equal(fs.existsSync(file), false, 'env key does not create a file');
    assert.equal(code(() => box.createKeySource({ envKey: 'short', filePath: file }).load(true)), 'email_secret_key_invalid');
    assert.equal(box.createKeySource({ envKey: '  ', filePath: file }).load(false), null, 'blank env = unset; no file and no create');
    assert.equal(fs.existsSync(file), false);
    const created = box.createKeySource({ envKey: undefined, filePath: file }).load(true);
    assert.equal(created.source, 'file');
    if (process.platform !== 'win32') assert.equal(fs.statSync(file).mode & 0o777, 0o600);
    const reread = box.createKeySource({ envKey: undefined, filePath: file }).load(false);
    assert.equal(reread.id, created.id, 'the same key is read back');
    assert.deepEqual(fs.readdirSync(path.dirname(file)), ['settings-secret.key'], 'no temporary file is left behind');
    assert.match(fs.readFileSync(file, 'utf8'), /^[A-Za-z0-9+/]{43}=\n$/, 'complete key line');
    // 이미 있으면(다른 프로세스가 먼저 걸었음) 새로 만들지 않고 그 키를 쓴다.
    const second = box.createKeySource({ envKey: undefined, filePath: file }).load(true);
    assert.equal(second.id, created.id);
    // 만들 수 없는 곳(파일 아래 경로): null, 반쪽 파일·임시 파일 없음.
    const blocker = path.join(dir, 'blocker');
    fs.writeFileSync(blocker, '');
    assert.equal(box.createKeySource({ envKey: undefined, filePath: path.join(blocker, 'k.key') }).load(true) ?? null, null);
    fs.writeFileSync(file, 'garbage\n');
    assert.equal(code(() => box.createKeySource({ envKey: undefined, filePath: file }).load(false)), 'email_secret_key_invalid');
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('SMTP address resolution: blocked addresses dropped, all blocked → permanent, lookup failure → transient', async () => {
  const lookup = (answers) => async () => answers.map((address) => ({ address, family: address.includes(':') ? 6 : 4 }));
  assert.equal(await transport.resolveSmtpAddress('smtp.example.test', lookup(['127.0.0.1', '10.0.0.5'])), '10.0.0.5');
  const blocked = await transport.resolveSmtpAddress('smtp.example.test', lookup(['127.0.0.1', '169.254.169.254'])).catch((e) => e);
  assert.deepEqual([blocked.code, blocked.transient], ['smtp_host_blocked', false]);
  const failed = await transport.resolveSmtpAddress('smtp.example.test', async () => { throw new Error('ENOTFOUND'); }).catch((e) => e);
  assert.deepEqual([failed.code, failed.transient], ['smtp_host_unresolved', true]);
  const slow = await transport.resolveSmtpAddress('smtp.example.test', () => new Promise(() => {}), 20).catch((e) => e);
  assert.equal(slow.code, 'smtp_host_unresolved');
  assert.equal(await transport.resolveSmtpAddress('10.0.0.9', lookup([])), '10.0.0.9');
  assert.equal((await transport.resolveSmtpAddress('127.0.0.1', lookup([])).catch((e) => e)).code, 'smtp_host_blocked');
});

test('nodemailer options: TLS verified, STARTTLS required, servername = configured name, short timeouts, no file/URL access, no auth without username', () => {
  const base = { host: 'smtp.example.test', port: 587, security: 'starttls', username: 'alerts', password: 'pw', fromAddress: 'alerts@example.test' };
  const starttls = transport.nodemailerOptions(base, '10.0.0.5');
  assert.deepEqual([starttls.host, starttls.port, starttls.secure, starttls.requireTLS, starttls.ignoreTLS, starttls.opportunisticTLS, starttls.servername],
    ['10.0.0.5', 587, false, true, false, false, 'smtp.example.test']);
  assert.deepEqual(starttls.tls, { rejectUnauthorized: true, minVersion: 'TLSv1.2' });
  assert.deepEqual(starttls.auth, { user: 'alerts', pass: 'pw' });
  assert.deepEqual([starttls.connectionTimeout, starttls.greetingTimeout, starttls.socketTimeout], [10_000, 10_000, 20_000]);
  assert.deepEqual([starttls.disableFileAccess, starttls.disableUrlAccess, starttls.logger, starttls.debug], [true, true, false, false]);
  const tls = transport.nodemailerOptions({ ...base, security: 'tls', port: 465, username: null, password: null }, '10.0.0.5');
  assert.deepEqual([tls.secure, tls.requireTLS, 'auth' in tls], [true, false, false]);
  assert.equal('servername' in transport.nodemailerOptions({ ...base, host: '10.0.0.5' }, '10.0.0.5'), false);
  for (const key of ['service', 'url', 'proxy', 'pool', 'transport']) assert.equal(key in starttls, false, key);
});

test('SMTP error classification: codes only, transient vs permanent, transport state', () => {
  const c = (error) => { const e = transport.classifySmtpError(error); return [e.code, e.transient, e.transport, e.smtpCode]; };
  assert.deepEqual(c({ code: 'EAUTH', responseCode: 535, response: '535 secret detail', message: 'Invalid login: user x' }), ['smtp_auth_failed', false, 'auth_failed', 535]);
  assert.deepEqual(c({ code: 'ESOCKET', originalCode: 'CERT_HAS_EXPIRED' }), ['smtp_tls_failed', false, 'error', null]);
  assert.deepEqual(c({ code: 'ESOCKET', originalCode: 'ERR_TLS_CERT_ALTNAME_INVALID' }), ['smtp_tls_failed', false, 'error', null]);
  assert.deepEqual(c({ code: 'EREQUIRETLS' }), ['smtp_tls_failed', false, 'error', null]);
  assert.deepEqual(c({ code: 'ETIMEDOUT' }), ['smtp_timeout', true, 'error', null]);
  assert.deepEqual(c({ code: 'ECONNECTION', originalCode: 'ECONNREFUSED' }), ['smtp_connection_failed', true, 'error', null]);
  assert.deepEqual(c({ code: 'EENVELOPE', responseCode: 550 }), ['smtp_recipient_rejected', false, null, 550]);
  assert.deepEqual(c({ code: 'EENVELOPE', responseCode: 452 }), ['smtp_temporary_failure', true, null, 452]);
  assert.deepEqual(c({ code: 'EMESSAGE', responseCode: 554 }), ['smtp_message_failed', false, null, 554]);
  assert.deepEqual(c({ code: 'EPROTOCOL', responseCode: 503 }), ['smtp_rejected', false, null, 503]);
  assert.deepEqual(c(new Error('boom')), ['smtp_error', true, 'error', null]);
  assert.deepEqual(c(null), ['smtp_error', true, 'error', null]);
  // 보내기 중(stage send): 연결 전으로 확실한 실패만 다시 시도, 연결 뒤 응답 번호 없는 끊김·소켓 시간 초과는 결과 모름(다시 보내지 않음).
  const s = (error) => { const e = transport.classifySmtpError(error, 'send'); return [e.code, e.transient, e.transport, e.uncertain]; };
  assert.deepEqual(s({ code: 'ETIMEDOUT', timeoutType: 'SOCKET_TIMEOUT' }), ['smtp_result_unknown', false, null, true]);
  assert.deepEqual(s({ code: 'ETIMEDOUT' }), ['smtp_result_unknown', false, null, true], 'timeout without a phase is treated as after connect');
  assert.deepEqual(s({ code: 'ESOCKET', originalCode: 'ECONNRESET' }), ['smtp_result_unknown', false, null, true]);
  assert.deepEqual(s({ code: 'ESOCKET', originalCode: 'EPIPE' }), ['smtp_result_unknown', false, null, true]);
  assert.deepEqual(s({ code: 'ECONNECTION' }), ['smtp_result_unknown', false, null, true], 'connection closed without a reply');
  assert.deepEqual(s({ code: 'ESTREAM' }), ['smtp_result_unknown', false, null, true]);
  assert.deepEqual(s(new Error('boom')), ['smtp_result_unknown', false, null, true]);
  for (const timeoutType of ['CONNECT_TIMEOUT', 'GREETING_TIMEOUT', 'UPGRADE_TIMEOUT']) {
    assert.deepEqual(s({ code: 'ETIMEDOUT', timeoutType }), ['smtp_timeout', true, 'error', false], timeoutType);
  }
  for (const original of ['ECONNREFUSED', 'EHOSTUNREACH', 'ENETUNREACH']) {
    assert.deepEqual(s({ code: 'ESOCKET', originalCode: original }), ['smtp_connection_failed', true, 'error', false], original);
  }
  assert.deepEqual(s({ code: 'EDNS' }), ['smtp_connection_failed', true, 'error', false]);
  assert.deepEqual(s({ code: 'ESOCKET', originalCode: 'CERT_HAS_EXPIRED' }), ['smtp_tls_failed', false, 'error', false], 'TLS certificate errors stay permanent');
  assert.deepEqual(s({ code: 'ETLS' }), ['smtp_tls_failed', false, 'error', false], 'STARTTLS negotiation failure stays permanent');
  assert.deepEqual(s({ code: 'ESOCKET', originalCode: 'ERR_TLS_CERT_ALTNAME_INVALID' }), ['smtp_tls_failed', false, 'error', false]);
  // TLS 레코드 오류: 보내기 중이면 그 행만 결과 모름(transport null → 묶음을 멈추지 않음), 연결 확인이면 영구 TLS 실패.
  assert.deepEqual(s({ code: 'ESOCKET', originalCode: 'ERR_SSL_DECRYPTION_FAILED_OR_BAD_RECORD_MAC' }), ['smtp_result_unknown', false, null, true]);
  for (const record of ['ERR_SSL_SSLV3_ALERT_BAD_RECORD_MAC', 'ERR_SSL_DECRYPTION_FAILED', 'ERR_SSL_TLSV1_ALERT_DECRYPTION_FAILED']) {
    assert.deepEqual(s({ code: 'ESOCKET', originalCode: record }), ['smtp_result_unknown', false, null, true], record);
  }
  // 협상 실패·설정 문제(잘못된 포트·보안 방식, 버전·암호 불일치)는 보내기 중이어도 영구 TLS 실패 + 연결 상태 error(결과 모름으로 숨기지 않음).
  // 평문 SMTP에 TLS로 접속한 실제 nodemailer 오류 모양: code ESOCKET, originalCode ERR_SSL_WRONG_VERSION_NUMBER, command CONN.
  for (const handshake of ['ERR_SSL_WRONG_VERSION_NUMBER', 'ERR_SSL_SSLV3_ALERT_HANDSHAKE_FAILURE', 'ERR_SSL_TLSV1_ALERT_PROTOCOL_VERSION',
    'ERR_SSL_UNSUPPORTED_PROTOCOL', 'ERR_SSL_TLSV1_ALERT_DECRYPT_ERROR', 'ERR_SSL_PACKET_LENGTH_TOO_LONG']) {
    assert.deepEqual(s({ code: 'ESOCKET', originalCode: handshake, command: 'CONN' }), ['smtp_tls_failed', false, 'error', false], handshake);
  }
  assert.deepEqual(s({ code: 'ERR_OSSL_BAD_DECRYPT' }), ['smtp_tls_failed', false, 'error', false], 'other OpenSSL errors stay permanent');
  assert.deepEqual(c({ code: 'ESOCKET', originalCode: 'ERR_SSL_DECRYPTION_FAILED_OR_BAD_RECORD_MAC' }), ['smtp_tls_failed', false, 'error', null]);
  assert.deepEqual(s({ code: 'EENVELOPE', responseCode: 451 }), ['smtp_temporary_failure', true, null, false], '4xx reply = not accepted, retry');
  assert.deepEqual(s({ code: 'EMESSAGE', responseCode: 554 }), ['smtp_message_failed', false, null, false]);
  assert.deepEqual(s({ code: 'EAUTH', responseCode: 535 }), ['smtp_auth_failed', false, 'auth_failed', false]);
  // 연결 확인(stage verify)은 메일이 없으므로 같은 끊김을 일시 실패로 본다.
  assert.deepEqual(c({ code: 'ETIMEDOUT', timeoutType: 'SOCKET_TIMEOUT' }), ['smtp_timeout', true, 'error', null]);
  assert.deepEqual(c({ code: 'ESOCKET', originalCode: 'ECONNRESET' }), ['smtp_connection_failed', true, 'error', null]);
  const own = new transport.SmtpError('smtp_host_blocked', false, 'error');
  assert.equal(transport.classifySmtpError(own), own);
});

test('retry policy: 1/5/15/60 minutes for transient failures, failed after 5 attempts or on permanent errors', () => {
  const transient = new transport.SmtpError('smtp_timeout', true, 'error');
  assert.deepEqual([1, 2, 3, 4].map((n) => outbox.failureOutcome(transient, n)).map((o) => [o.status, o.delayMs]),
    [['retrying', 60_000], ['retrying', 300_000], ['retrying', 900_000], ['retrying', 3_600_000]]);
  assert.equal(outbox.failureOutcome(transient, 5).status, 'failed');
  assert.equal(outbox.failureOutcome(new transport.SmtpError('smtp_auth_failed', false, 'auth_failed'), 1).status, 'failed');
  const unknown = outbox.failureOutcome(transport.classifySmtpError({ code: 'ETIMEDOUT', timeoutType: 'SOCKET_TIMEOUT' }, 'send'), 1);
  assert.deepEqual([unknown.status, unknown.delayMs, unknown.error.code], ['unknown', null, 'smtp_result_unknown'], 'result unknown is never retried');
});

test('incident mail: subject, plain + HTML body, escaped values, KST start, link, suppressed lines; no transaction ids', () => {
  const payload = { type: 'incident', reason: 'opened', riskLevel: 'critical', incidentId: '11111111-2222-4333-8444-555555555555',
    criteria: { kind: 'response_code', value: 'E500<script>\r\nBcc: x@evil.test', longValue: false }, transactions: 3, decisions: 5,
    startAt: '2026-11-21T05:03:00.000000Z', startBasis: 'decided_at', suppressed: { incident: 2, decision: 0 } };
  const m = template.renderMessage(payload, { to: 'ops@example.test', baseUrl: 'http://dash.example.test:8080', deliveryId: 'd-1', fromAddress: 'alerts@example.test' });
  assert.equal(m.subject.includes('\n') || m.subject.includes('\r'), false);
  assert.match(m.subject, /^\[ESB 알림\] 위험 · 사건 · 응답코드 E500<script> Bcc: x@evil\.test$/);
  assert.ok(m.text.includes('새 사건\n기준: 응답코드 E500<script> Bcc: x@evil.test\n위험도: 위험\n묶인 판정 5건 · 거래 3건\n시작: 2026-11-21 14:03 KST (판정 시각 기준)'));
  assert.ok(m.text.includes('사건 보기: http://dash.example.test:8080/analysis/incidents/11111111-2222-4333-8444-555555555555'));
  assert.ok(m.text.includes('발송 제한으로 생략된 사건 2건이 있습니다.'));
  assert.equal(m.text.includes('판정 0건'), false);
  assert.equal(m.html.includes('<script>'), false);
  assert.ok(m.html.includes('E500&lt;script&gt;'));
  assert.ok(m.html.includes('<a href="http://dash.example.test:8080/analysis/incidents/11111111-2222-4333-8444-555555555555">사건 보기</a>'));
  assert.equal(m.messageId, '<d-1@example.test>');
  const raised = template.renderMessage({ ...payload, reason: 'risk_raised', riskLevel: 'warning', criteria: { kind: 'channel_id', value: null, longValue: true },
    startBasis: 'process_time_raw', suppressed: { incident: 0, decision: 0 } }, { to: 'a@example.test', baseUrl: 'http://d', deliveryId: 'x', fromAddress: 'a@example.test' });
  assert.equal(raised.subject, '[ESB 알림] 주의 · 사건 · 채널 값 생략(긴 값) · 위험도 상승');
  assert.ok(raised.text.startsWith('위험도 상승\n'));
  assert.ok(raised.text.includes('시작: 2026-11-21 14:03 KST\n'));
  assert.equal(raised.text.includes('발송 제한'), false);
});

test('decision and test mails; KST formatting; clipped values', () => {
  const m = template.renderMessage({ type: 'decision', reason: 'opened', riskLevel: 'critical', decisionId: 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee', grade: 'anomaly_sign',
    processId: 'P01', decidedAt: '2026-11-20T15:30:00.000000Z', suppressed: { incident: 0, decision: 1 } },
  { to: 'a@example.test', baseUrl: 'http://dash/', deliveryId: 'x', fromAddress: 'a@example.test' });
  assert.equal(m.subject, '[ESB 알림] 위험 · 판정 · 프로세스 P01');
  assert.ok(m.text.includes('새 판정\n결과: 이상 징후\n위험도: 위험\n프로세스: P01\n판정 시각: 2026-11-21 00:30 KST'));
  assert.ok(m.text.includes('판정 보기: http://dash/analysis/d/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'));
  assert.ok(m.text.includes('발송 제한으로 생략된 판정 1건이 있습니다.'));
  const t = template.renderMessage({ type: 'test' }, { to: 'a@example.test', baseUrl: null, deliveryId: 'x', fromAddress: 'a@example.test' });
  assert.deepEqual([t.subject, t.text.split('\n')[0]], ['[ESB 알림] 시험 메일', '메일 알림 시험 메일입니다.']);
  assert.equal(template.kst('nope'), null);
  assert.equal([...template.clipValue('가'.repeat(300))].length, 200);
});

const row = (patch = {}) => ({ enabled: true, notify_incidents: true, notify_decisions: false, min_risk_level: 'critical', recipients: ['ops@example.test'],
  smtp_host: 'smtp.example.test', smtp_port: 587, smtp_security: 'starttls', smtp_username: null, smtp_password_enc: null, from_address: 'alerts@example.test',
  public_base_url: 'http://dash', smtp_changed_at: '2026-11-21T00:00:00.000000Z', transport_checked_at: null, transport_result: null, transport_error_code: null,
  updated_by_actor_id: '1', updated_at: '2026-11-21T00:00:00.000000Z', ...patch });

test('transport status from stored settings: not_configured / invalid (incomplete, password, key) / configured + last check overlay', () => {
  const key = fixedKey(3);
  const keys = keysOf(key);
  const sealed = box.seal('pw', key);
  const s = (r, k = keys) => { const t = store.transportState(r, k); return [t.status, t.code]; };
  assert.deepEqual(s(null), ['not_configured', null]);
  assert.deepEqual(s(row({ smtp_host: null })), ['not_configured', null]);
  assert.deepEqual(s(row({ from_address: null })), ['invalid', 'smtp_settings_incomplete']);
  assert.deepEqual(s(row({ smtp_username: 'alerts' })), ['invalid', 'smtp_password_missing']);
  assert.deepEqual(s(row({ smtp_username: 'alerts', smtp_password_enc: sealed })), ['configured', null]);
  assert.deepEqual(s(row({ smtp_username: 'alerts', smtp_password_enc: sealed }), keysOf(null)), ['invalid', 'email_secret_key_unavailable']);
  assert.deepEqual(s(row({ smtp_username: 'alerts', smtp_password_enc: sealed }), keysOf(fixedKey(4))), ['invalid', 'smtp_password_unreadable']);
  assert.deepEqual(s(row({ transport_checked_at: 'x', transport_result: 'auth_failed', transport_error_code: 'smtp_auth_failed' })), ['auth_failed', 'smtp_auth_failed']);
  assert.deepEqual(s(row({ transport_checked_at: 'x', transport_result: 'ok' })), ['configured', null]);
  assert.equal(store.usable(store.transportState(row({ transport_checked_at: 'x', transport_result: 'error', transport_error_code: 'smtp_timeout' }), keys)), true,
    'a failed check does not block sending (it is recorded, not hidden)');
  const cfg = store.smtpConfigOf(row({ smtp_username: 'alerts', smtp_password_enc: sealed }), keys);
  assert.deepEqual([cfg.username, cfg.password, cfg.fromAddress], ['alerts', 'pw', 'alerts@example.test']);
  assert.equal((() => { try { store.smtpConfigOf(row({ smtp_host: null }), keys); } catch (e) { return e.code; } })(), 'smtp_not_configured');
  assert.equal((() => { try { store.smtpConfigOf(row({ smtp_username: 'alerts', smtp_password_enc: sealed }), keysOf(fixedKey(5))); } catch (e) { return e.code; } })(),
    'smtp_password_unreadable');
});

test('runner log codes: SQLSTATE or lowercase codes only', () => {
  assert.equal(runner.safeCode({ code: '42P01', message: 'relation "x" does not exist' }), '42P01');
  assert.equal(runner.safeCode({ code: 'smtp_auth_failed' }), 'smtp_auth_failed');
  assert.equal(runner.safeCode(new Error('password=hunter2')), 'unexpected');
});

test('legacy anti-patterns are absent from the email alert sources (no TLS bypass, no mock success, no console output of values)', () => {
  const dir = path.resolve(here, '../src/services/emailAlerts');
  const sources = fs.readdirSync(dir).map((name) => fs.readFileSync(path.join(dir, name), 'utf8')).join('\n')
    + fs.readFileSync(path.resolve(here, '../src/routes/emailAlertRoutes.ts'), 'utf8');
  assert.equal(/rejectUnauthorized:\s*false/.test(sources), false);
  assert.equal(/console\.log/.test(sources), false);
  assert.equal(/MOCK|mock mode/i.test(sources), false);
  assert.equal(/err(or)?\.message/.test(sources.replace(/\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '')), false, 'server messages are not used');
});
