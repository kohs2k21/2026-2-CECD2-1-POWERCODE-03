import assert from "node:assert/strict";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

// 설정 › 연동 관리 › 메일 알림 시험(contracts/email-alerts.md 8·11절, 로컬 가짜 fetch만 — 실제 SMTP·메일 없음):
// 관리자/일반 사용자, 전송 상태 문구·잠긴 켜기, 받는 사람 검사(형식·중복·최대 10), 저장 실패 시 서버 값 유지·키 오류 문구, 비밀번호 비노출,
// 저장 결과(연결 확인 성공·실패·확인 안 함), 시험 발송 확인·결과 3종·요청 오류, 최근 발송 상태 문구, 사건 묶음 처리 대기, 연동 관리 상단 저장 미지원 없음.
register("./svg-loader.mjs", import.meta.url);
globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
await import("react-hot-toast");
const storage = new Map();
globalThis.window = {
  location: { origin: "http://local.test" },
  localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
  addEventListener() {},
  removeEventListener() {},
  setTimeout,
  clearTimeout,
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
};
globalThis.HTMLFormElement = class {};
globalThis.document = { addEventListener() {}, removeEventListener() {} };
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { MemoryRouter } = await import("react-router-dom");
const { AuthSessionProvider, useAuthSession } = await import("../src/services/auth/AuthSessionProvider.tsx");
const api = await import("../src/features/settings/email/emailAlertsApi.ts");
const labels = await import("../src/features/settings/email/emailAlertLabels.ts");
const { TestSendConfirm, updateOf } = await import("../src/features/settings/email/EmailAlertsCard.tsx");
const { resetProcessingObservations } = await import("../src/features/settings/email/useEmailAlerts.ts");
const { IntegrationsSettings } = await import("../src/features/settings/sections/IntegrationsSettings.tsx");
const { NotificationSettings } = await import("../src/features/settings/sections/NotificationSettings.tsx");
const { SettingsPage } = await import("../src/features/settings/SettingsPage.tsx");

const SECRET = "fake-pw-never-shown";
const settingsBody = (over = {}) => ({
  enabled: false,
  rules: { kinds: ["incident"], minRiskLevel: "critical" },
  recipients: [],
  smtp: { host: null, port: null, security: null, username: null, passwordSet: false, fromAddress: null },
  publicBaseUrl: null,
  transport: { status: "not_configured", code: null, checkedAt: null },
  processing: { processedThroughSeq: null, latestSeq: "0", armedAt: null, lastTickAt: null },
  rateLimit: { newAlertsPerHour: 10, newAlertsLastHour: 0 },
  limits: { recipients: 10, ports: [25, 465, 587, 2525], security: ["tls", "starttls"] },
  isDefault: true,
  updatedAt: null,
  updatedBy: null,
  ...over,
});
const configured = (over = {}) => settingsBody({
  recipients: ["ops@example.test"],
  smtp: { host: "smtp.example.test", port: 587, security: "starttls", username: "mailer", passwordSet: true, fromAddress: "alerts@example.test" },
  publicBaseUrl: "http://dashboard.example.test:8080",
  transport: { status: "configured", code: null, checkedAt: "2026-11-21T01:02:03.000000Z" },
  isDefault: false,
  ...over,
});
const delivery = (status, over = {}) => ({ id: `d-${status}`, kind: "incident", reason: "opened", alertKey: null, targetId: null, riskLevel: "critical",
  recipient: "ops@example.test", status, attempts: 1, errorCode: null, smtpCode: null, createdAt: "2026-11-21T01:00:00.000000Z", sentAt: null, finishedAt: null,
  nextAttemptAt: null, ...over });

// 1) 문구·판단(순수 함수)
{
  assert.deepEqual(api.transportStatuses.map((status) => labels.transportLabels[status]), ["미설정", "설정 오류", "설정됨", "인증 실패", "연결 오류"]);
  assert.deepEqual(api.deliveryStatuses.map((status) => labels.deliveryStatusLabels[status]),
    ["대기", "보내는 중", "보냄", "다시 시도 대기", "실패", "확인 불가", "발송 제한으로 생략"], "every delivery status has a label");
  assert.deepEqual(api.deliveryKinds.map((kind) => labels.deliveryKindLabels[kind]), ["사건", "판정", "시험"]);
  assert.deepEqual([labels.securityLabels.tls, labels.securityLabels.starttls], ["TLS 접속", "STARTTLS 필수"]);
  const stamp = (iso) => `T(${iso})`;
  assert.equal(labels.transportDetail("2026-11-21T01:02:03.000000Z", null, stamp), "마지막 확인 T(2026-11-21T01:02:03.000000Z)");
  assert.equal(labels.transportDetail("x", "smtp_auth_failed", stamp), "마지막 확인 T(x) · smtp_auth_failed");
  assert.equal(labels.transportDetail(null, "smtp_password_missing", stamp), "smtp_password_missing");
  assert.equal(labels.transportDetail(null, null, stamp), "");
  assert.deepEqual([labels.verifyText({ result: "ok", code: null }), labels.verifyText({ result: "error", code: "smtp_tls_failed" }),
    labels.verifyText({ result: "auth_failed", code: "smtp_auth_failed" }), labels.verifyText({ result: "skipped", code: null })],
  ["저장됨 · 연결 확인 성공", "저장됨 · 연결 확인 실패 · smtp_tls_failed", "저장됨 · 연결 확인 실패 · smtp_auth_failed", "저장됨"]);
  const result = (status, errorCode = null) => ({ deliveryId: "t", status, errorCode, smtpCode: null, finishedAt: null });
  assert.deepEqual([labels.testResultText(result("sent")), labels.testResultText(result("failed", "smtp_recipient_rejected")),
    labels.testResultText(result("unknown", "smtp_result_unknown"))], ["시험 발송 완료", "시험 발송 실패 · smtp_recipient_rejected", "확인 불가"],
  "unknown is the delivery label, not a failure");
  assert.equal(labels.testConfirmText("a@example.test"), "a@example.test로 시험 메일을 보냅니다.");
  assert.equal(labels.saveErrorDetail("email_secret_key_unavailable"), "암호화 키를 쓸 수 없어 비밀번호를 저장하지 못했습니다.");
  assert.equal(labels.saveErrorDetail("email_secret_key_invalid"), "암호화 키를 쓸 수 없어 비밀번호를 저장하지 못했습니다.");
  assert.equal(labels.saveErrorDetail("email_transport_not_configured"), "email_transport_not_configured");
  // 받는 사람 검사: 서버와 같은 형식 규칙·정규화, 대소문자 무시 중복, 최대 수.
  assert.deepEqual(api.checkRecipient("  Ops@Example.TEST ", [], 10), { address: "Ops@example.test" });
  for (const bad of ["ops", "ops@", "@example.test", "a b@example.test", "ops@example", "ops@example.123", "a@b@example.test", "<ops@example.test>", "한글@example.test"]) {
    assert.deepEqual(api.checkRecipient(bad, [], 10), { problem: "format" }, bad);
  }
  assert.deepEqual(api.checkRecipient("OPS@example.test", ["ops@example.test"], 10), { problem: "duplicate" });
  assert.deepEqual(api.checkRecipient("new@example.test", Array.from({ length: 10 }, (_, i) => `u${i}@example.test`), 10), { problem: "max" });
  // 처리 대기: 경계가 최신보다 뒤 + 주기 작업이 다시 돌았는데 같은 경계 + 5분 이상.
  const on = (seq, latest, tick) => ({ enabled: true, processing: { processedThroughSeq: seq, latestSeq: latest, armedAt: null, lastTickAt: tick } });
  let step = api.observeProcessing(undefined, on("10", "12", "t1"), 0);
  assert.equal(step.lagging, false, "first sight is not a delay");
  step = api.observeProcessing(step.observation, on("10", "15", "t2"), api.PROCESSING_LAG_MS - 1);
  assert.equal(step.lagging, false, "under 5 minutes");
  step = api.observeProcessing(step.observation, on("10", "15", "t3"), api.PROCESSING_LAG_MS);
  assert.equal(step.lagging, true, "same boundary after ticks for 5 minutes");
  assert.equal(api.observeProcessing(step.observation, on("10", "15", "t1"), api.PROCESSING_LAG_MS * 2).lagging, false, "no tick since first sight: not the incident wait");
  assert.equal(api.observeProcessing(step.observation, on("11", "15", "t4"), api.PROCESSING_LAG_MS * 2).lagging, false, "boundary moved: watch again");
  assert.deepEqual(api.observeProcessing(step.observation, on("15", "15", "t4"), api.PROCESSING_LAG_MS * 2), { observation: undefined, lagging: false }, "caught up");
  assert.equal(api.observeProcessing(step.observation, { ...on("10", "15", "t9"), enabled: false }, api.PROCESSING_LAG_MS * 2).lagging, false, "off: no indicator");
  assert.equal(api.PROCESSING_LAG_MS, 300_000);
  // PUT 본문: 비밀번호 생략(유지)·문자열(새 값)·null(지움), 계정이 비면 비밀번호를 보내지 않는다.
  const draft = { enabled: true, kinds: ["decision", "incident"], minRiskLevel: "warning", recipients: ["a@example.test"], host: " smtp.example.test ", port: "465",
    security: "tls", username: "mailer", password: "", passwordCleared: false, fromAddress: "f@example.test", publicBaseUrl: "http://x:8080" };
  assert.deepEqual(updateOf(draft), { enabled: true, rules: { kinds: ["incident", "decision"], minRiskLevel: "warning" }, recipients: ["a@example.test"],
    smtp: { host: "smtp.example.test", port: 465, security: "tls", username: "mailer", fromAddress: "f@example.test" }, publicBaseUrl: "http://x:8080" });
  assert.equal(updateOf({ ...draft, password: "pw" }).smtp.password, "pw");
  assert.equal(updateOf({ ...draft, passwordCleared: true }).smtp.password, null);
  assert.equal("password" in updateOf({ ...draft, username: " ", password: "pw" }).smtp, false);
  assert.deepEqual(updateOf({ ...draft, host: "", port: "", security: "", username: "", fromAddress: "", publicBaseUrl: "" }).smtp,
    { host: null, port: null, security: null, username: null, fromAddress: null });
  // 응답 해석: 머리 값이 틀리면 오류, 비밀번호 값은 받아도 버린다.
  assert.throws(() => api.parseEmailAlertSettings({ enabled: true }), /메일 알림 설정 응답/);
  assert.throws(() => api.parseEmailAlertSettings(settingsBody({ transport: { status: "ok" } })), /메일 알림 설정 응답/);
  const parsed = api.parseEmailAlertSettings(configured({ smtp: { ...configured().smtp, password: SECRET } }));
  assert.ok(!JSON.stringify(parsed).includes(SECRET), "a stray password field is dropped");
  assert.deepEqual(api.parseDeliveryList({ items: [delivery("sent"), delivery("bounced"), { id: "x" }], nextCursor: null }).items.map((item) => item.id), ["d-sent"]);
}

// 2) 화면: 가짜 fetch(계정 토큰별 역할).
const requests = [];
let routes = {};
globalThis.fetch = async (url, options = {}) => {
  const target = new URL(String(url), "http://local.test");
  const body = options.body ? JSON.parse(options.body) : undefined;
  const auth = new Headers(options.headers).get("Authorization");
  requests.push({ method: options.method ?? "GET", path: target.pathname, search: target.search, body, auth });
  const handler = routes[`${options.method ?? "GET"} ${target.pathname}`];
  if (!handler) return new Response(JSON.stringify({ code: "email_alert_storage_unavailable" }), { status: 503 });
  const [status, value] = await handler(target, body);
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
};
const rawText = (node) => (!node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(rawText).join("") : rawText(node.children));
const text = (node) => rawText(node).replace(/\s+/g, " ").trim();
const tick = (ms = 10) => act(async () => new Promise((resolve) => setTimeout(resolve, ms)));
const wait = async (check, label, tries = 300) => {
  for (let i = 0; i < tries && !check(); i++) await tick();
  assert.ok(check(), label);
};
const mount = async (render, role = "admin", initialEntries = undefined) => {
  let session;
  const Probe = () => {
    session = useAuthSession();
    return null;
  };
  const Body = () => {
    const { user } = useAuthSession();
    return user ? render(user) : null;
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { gcTime: Infinity } } });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(MemoryRouter, initialEntries ? { initialEntries } : null, React.createElement(QueryClientProvider, { client },
      React.createElement(AuthSessionProvider, null, React.createElement(Probe), React.createElement(Body)))));
  });
  await act(async () => session.login({ token: `${role}-token`, user: { id: `${role}-1`, email: `${role}@example.test`, userType: role, createdAt: "2026-10-01T00:00:00Z" } }));
  return { renderer, client, shown: () => text(renderer.toJSON()), json: () => JSON.stringify(renderer.toJSON()),
    cleanup: async () => { await act(async () => renderer.unmount()); client.clear(); resetProcessingObservations(); } };
};
const integrations = (user) => React.createElement(IntegrationsSettings, { currentUser: user });
const buttons = (app, label) => app.renderer.root.findAll((node) => node.type === "button" && text(node.children) === label);
const byLabel = (app, label) => app.renderer.root.findAll((node) => node.type === "button" && node.props["aria-label"] === label);
const click = async (button) => act(async () => button.props.onClick?.({ preventDefault() {} }));
const emailSwitch = (app) => app.renderer.root.findAll((node) => node.type === "button" && node.props.role === "switch" && node.props.id
  && app.renderer.root.findAll((label) => label.type === "label" && label.props.htmlFor === node.props.id && text(label.children) === "메일 알림 켜기").length === 1)[0];
const inputFor = (app, label) => {
  const target = app.renderer.root.findAll((node) => node.type === "label" && text(node.children) === label)[0];
  return app.renderer.root.findAll((node) => (node.type === "input" || node.type === "select") && node.props.id === target.props.htmlFor)[0];
};
const recipientInput = (app) => inputFor(app, "받는 사람");
const type = async (input, value) => act(async () => input.props.onChange({ target: { value } }));
const puts = () => requests.filter((r) => r.method === "PUT");

// 2-1) 일반 사용자: 안내 한 줄만, 메일 알림 API를 부르지 않는다. 관리자는 승인 설명·켜기 라벨.
{
  routes = {};
  requests.length = 0;
  const app = await mount(integrations, "user");
  await tick(50);
  assert.ok(app.shown().includes("메일 알림 설정은 관리자만 바꿀 수 있습니다."));
  assert.ok(!app.shown().includes("메일 알림 켜기") && !app.shown().includes("위험 사건 알림을 메일로 보냅니다."), "only the notice for non-admins");
  assert.equal(requests.filter((r) => r.path.startsWith("/api/email-alerts")).length, 0, "no admin API calls for a user");
  assert.ok(app.shown().includes("Slack 알림 연동") && app.shown().includes("커스텀 Webhook 연동"), "other cards stay");
  await app.cleanup();
}

// 2-2) 관리자 · 미설정: 미설정 배지, 켜기 잠김(API 409와 같음), 기본 규칙(사건·위험), 빈 발송 목록, 0/10.
{
  routes = {
    "GET /api/email-alerts/settings": () => [200, settingsBody()],
    "GET /api/email-alerts/deliveries": () => [200, { items: [], nextCursor: null }],
  };
  requests.length = 0;
  const app = await mount(integrations);
  await wait(() => app.shown().includes("미설정"), "default settings loaded");
  assert.ok(app.shown().includes("위험 사건 알림을 메일로 보냅니다."));
  assert.ok(!app.shown().includes("이메일 알림 연동은 현재 서버에서 지원하지 않습니다."), "old placeholder text is gone");
  assert.equal(emailSwitch(app).props.disabled, true, "switch locked while transport is not configured");
  assert.equal(emailSwitch(app).props["aria-checked"], false);
  assert.ok(app.shown().includes("0/10"));
  await wait(() => app.shown().includes("보낸 메일이 없습니다."), "empty deliveries");
  assert.ok(requests.some((r) => r.path === "/api/email-alerts/deliveries" && r.search === "?limit=20"), "recent 20");
  assert.equal(inputFor(app, "위험도 하한").props.value, "critical");
  assert.deepEqual(inputFor(app, "위험도 하한").findAllByType("option").map((o) => text(o.children)), ["위험", "주의 이상", "참고 이상"]);
  assert.deepEqual(inputFor(app, "보안").findAllByType("option").map((o) => text(o.children)), ["", "TLS 접속", "STARTTLS 필수"]);
  assert.deepEqual(inputFor(app, "포트").findAllByType("option").map((o) => text(o.children)), ["", "25", "465", "587", "2525"]);
  assert.equal(inputFor(app, "비밀번호").props.disabled, true, "password needs an account");
  assert.equal(inputFor(app, "비밀번호").props.placeholder, undefined, "no saved placeholder without a password");
  assert.equal(buttons(app, "비밀번호 지우기").length, 0);
  assert.ok(app.shown().includes("메일 링크 앞부분 (예: http://서버:8080)"));
  assert.equal(buttons(app, "저장")[0].props.disabled, true, "nothing to save");
  for (const status of ["invalid", "configured", "auth_failed", "error"]) {
    const body = settingsBody({ transport: { status, code: status === "configured" ? null : "smtp_x", checkedAt: null } });
    app.client.setQueryData(["email-alert-settings", "admin-1"], api.parseEmailAlertSettings(body));
    await tick();
    assert.equal(emailSwitch(app).props.disabled, status === "invalid", `switch lock for ${status}`);
    assert.ok(app.shown().includes(labels.transportLabels[status]), `badge for ${status}`);
  }
  await app.cleanup();
}

// 2-3) 관리자 · 설정됨: 비밀번호 비노출, 받는 사람 검사, 저장 실패 시 서버 값 유지·오류 문구, 성공 시 연결 확인 결과.
{
  let stored = configured();
  let putStatus = null;
  let verify = { result: "ok", code: null };
  routes = {
    "GET /api/email-alerts/settings": () => [200, stored],
    "GET /api/email-alerts/deliveries": () => [200, { items: [], nextCursor: null }],
    "PUT /api/email-alerts/settings": (_target, body) => {
      if (putStatus) return [putStatus[0], { code: putStatus[1] }];
      stored = configured({ enabled: body.enabled, rules: body.rules, recipients: body.recipients,
        smtp: { ...body.smtp, passwordSet: body.smtp.password === undefined ? stored.smtp.passwordSet : body.smtp.password !== null, password: undefined },
        publicBaseUrl: body.publicBaseUrl, transport: { status: verify.result === "ok" || verify.result === "skipped" ? "configured" : verify.result, code: verify.code,
          checkedAt: "2026-11-21T02:00:00.000000Z" } });
      return [200, { ...stored, verify }];
    },
  };
  requests.length = 0;
  const app = await mount(integrations);
  await wait(() => app.shown().includes("설정됨"), "configured loaded");
  assert.ok(/마지막 확인 \d\d\/\d\d \d\d:\d\d:\d\d/.test(app.shown()), "last checked time");
  assert.equal(emailSwitch(app).props.disabled, false, "switch unlocked when configured");
  const password = () => inputFor(app, "비밀번호");
  assert.deepEqual([password().props.type, password().props.value, password().props.placeholder], ["password", "", "저장됨 · 바꿀 때만 입력"]);
  assert.equal(buttons(app, "비밀번호 지우기").length, 1);
  assert.ok(app.shown().includes("1/10") && app.shown().includes("ops@example.test"));
  assert.equal(byLabel(app, "ops@example.test 삭제").length, 1, "remove button accessible name");

  // 받는 사람: 형식·중복 오류(입력 아래), 추가·삭제, 최대 10.
  await type(recipientInput(app), "not-an-address");
  await click(buttons(app, "추가")[0]);
  assert.ok(app.shown().includes("메일 주소 형식이 아닙니다."));
  assert.equal(recipientInput(app).props["aria-invalid"], true);
  await type(recipientInput(app), "OPS@EXAMPLE.TEST");
  assert.ok(!app.shown().includes("메일 주소 형식이 아닙니다."), "typing clears the error");
  await click(buttons(app, "추가")[0]);
  assert.ok(app.shown().includes("이미 있는 주소입니다."));
  for (let i = 0; i < 9; i++) {
    await type(recipientInput(app), `user${i}@Example.test`);
    await click(buttons(app, "추가")[0]);
  }
  assert.ok(app.shown().includes("10/10") && app.shown().includes("user0@example.test"), "normalized domain");
  await type(recipientInput(app), "eleven@example.test");
  await click(buttons(app, "추가")[0]);
  assert.ok(app.shown().includes("최대 10개까지 넣을 수 있습니다."));
  assert.equal(buttons(app, "시험 발송").filter((b) => !b.props.disabled).length, 1, "only the saved recipient can get a test mail");
  for (let i = 1; i < 9; i++) await click(byLabel(app, `user${i}@example.test 삭제`)[0]);
  assert.ok(app.shown().includes("2/10"));

  // 새 비밀번호 + 저장 실패(키 없음 503): 서버 값(캐시·배지)은 그대로, 입력값은 남아 다시 저장할 수 있다.
  await type(password(), SECRET);
  putStatus = [503, "email_secret_key_unavailable"];
  const before = app.client.getQueryData(["email-alert-settings", "admin-1"]);
  await click(buttons(app, "저장")[0]);
  await wait(() => app.shown().includes("메일 알림 설정을 저장하지 못했습니다"), "save failure shown");
  assert.ok(app.shown().includes("암호화 키를 쓸 수 없어 비밀번호를 저장하지 못했습니다."));
  assert.equal(puts().at(-1).body.smtp.password, SECRET, "new password sent once in the body");
  assert.deepEqual(puts().at(-1).body.recipients, ["ops@example.test", "user0@example.test"]);
  assert.equal(app.client.getQueryData(["email-alert-settings", "admin-1"]), before, "server values stay in the cache");
  assert.deepEqual(stored.recipients, ["ops@example.test"], "fake server unchanged");
  assert.ok(app.shown().includes("설정됨"), "status badge still from the server");
  assert.ok(app.shown().includes("2/10"), "draft kept for another try");
  assert.equal(buttons(app, "저장")[0].props.disabled, false);
  // 다른 오류는 코드 그대로.
  putStatus = [409, "email_transport_not_configured"];
  await click(buttons(app, "저장")[0]);
  await wait(() => puts().length === 2 && app.shown().includes("email_transport_not_configured"), "409 code shown");

  // 저장 중 잠금 → 성공: 연결 확인 실패 문구, 입력 비움, 비밀번호는 어디에도 보이지 않음.
  putStatus = null;
  verify = { result: "auth_failed", code: "smtp_auth_failed" };
  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  const put = routes["PUT /api/email-alerts/settings"];
  routes["PUT /api/email-alerts/settings"] = async (target, body) => { await slow; return put(target, body); };
  await click(buttons(app, "저장")[0]);
  await tick();
  assert.equal(buttons(app, "저장")[0].props.disabled, true, "save disabled while pending");
  assert.equal(emailSwitch(app).props.disabled, true, "switch locked while pending");
  assert.equal(recipientInput(app).props.disabled, true);
  release();
  await wait(() => app.shown().includes("저장됨 · 연결 확인 실패 · smtp_auth_failed"), "verify failure line");
  routes["PUT /api/email-alerts/settings"] = put;
  assert.ok(app.shown().includes("인증 실패") && app.shown().includes("smtp_auth_failed"), "badge from the saved response");
  assert.ok(!app.shown().includes("메일 알림 설정을 저장하지 못했습니다"), "error cleared");
  assert.deepEqual([password().props.value, password().props.placeholder], ["", "저장됨 · 바꿀 때만 입력"]);
  assert.ok(!app.json().includes(SECRET), "password never rendered after save");
  assert.ok(requests.filter((r) => r.path === "/api/email-alerts/deliveries").length >= 2, "deliveries read again after save");

  // 확인 안 함(SMTP 값 그대로) → `저장됨`, 연결 확인 성공 → `저장됨 · 연결 확인 성공`.
  verify = { result: "skipped", code: null };
  await act(async () => inputFor(app, "위험도 하한").props.onChange({ target: { value: "warning" } }));
  await click(buttons(app, "저장")[0]);
  await wait(() => text(app.renderer.root.findAll((n) => n.type === "p" && n.props.role === "status").at(-1).children) === "저장됨", "skipped verify line");
  assert.equal(puts().at(-1).body.rules.minRiskLevel, "warning");
  assert.equal("password" in puts().at(-1).body.smtp, false, "password omitted = keep");
  verify = { result: "ok", code: null };
  await click(buttons(app, "비밀번호 지우기")[0]);
  assert.equal(password().props.placeholder, undefined, "cleared: no saved placeholder");
  await click(buttons(app, "저장")[0]);
  await wait(() => app.shown().includes("저장됨 · 연결 확인 성공"), "verify ok line");
  assert.equal(puts().at(-1).body.smtp.password, null, "clear sends null");
  assert.equal(stored.smtp.passwordSet, false);
  await app.cleanup();
}

// 2-4) 시험 발송: 확인 창(취소하면 보내지 않음) → 결과 3종(완료·실패 · 코드·확인 불가) + 요청 오류(429), 끝나면 상태·발송 목록 다시 조회.
{
  let next = [200, { deliveryId: "t1", status: "sent", errorCode: null, smtpCode: 250, finishedAt: "2026-11-21T03:00:00.000000Z" }];
  let settingsReads = 0;
  routes = {
    "GET /api/email-alerts/settings": () => { settingsReads += 1; return [200, configured()]; },
    "GET /api/email-alerts/deliveries": () => [200, { items: [], nextCursor: null }],
    "POST /api/email-alerts/test": () => next,
  };
  requests.length = 0;
  const app = await mount(integrations);
  await wait(() => buttons(app, "시험 발송").length === 1, "recipient row");
  const confirm = () => app.renderer.root.findByType(TestSendConfirm);
  assert.equal(confirm().props.address, null, "dialog closed");
  await click(buttons(app, "시험 발송")[0]);
  assert.equal(confirm().props.address, "ops@example.test", "confirm opened for the recipient");
  await act(async () => confirm().props.onCancel());
  assert.equal(confirm().props.address, null);
  assert.equal(requests.filter((r) => r.method === "POST").length, 0, "cancel sends nothing");
  const resultLine = () => app.renderer.root.findAll((n) => n.props?.["data-test-result"] === "ops@example.test" && n.type === "p").map((n) => text(n.children))[0];
  const cases = [
    [[200, { deliveryId: "t1", status: "sent", errorCode: null, smtpCode: 250, finishedAt: "x" }], "시험 발송 완료"],
    [[200, { deliveryId: "t2", status: "failed", errorCode: "smtp_recipient_rejected", smtpCode: 550, finishedAt: "x" }], "시험 발송 실패 · smtp_recipient_rejected"],
    [[200, { deliveryId: "t3", status: "unknown", errorCode: "smtp_result_unknown", smtpCode: null, finishedAt: "x" }], "확인 불가"],
    [[429, { code: "email_test_rate_limited" }], "시험 발송 실패 · email_test_rate_limited"],
  ];
  for (const [response, expected] of cases) {
    next = response;
    const reads = settingsReads;
    await click(buttons(app, "시험 발송")[0]);
    await act(async () => confirm().props.onSend(confirm().props.address));
    await wait(() => resultLine() === expected, `test result ${expected}`);
    await wait(() => settingsReads > reads, "settings read again after a test send");
  }
  assert.deepEqual(requests.filter((r) => r.method === "POST").map((r) => r.body), Array(4).fill({ to: "ops@example.test" }));
  assert.equal(labels.testConfirmText(confirm().props.address ?? "ops@example.test"), "ops@example.test로 시험 메일을 보냅니다.");
  await app.cleanup();
}

// 2-5) 최근 발송: 상태 7종 문구·종류 문구·열 이름.
{
  routes = {
    "GET /api/email-alerts/settings": () => [200, configured()],
    "GET /api/email-alerts/deliveries": () => [200, { items: [...api.deliveryStatuses.map((status) => delivery(status)), delivery("sent", { id: "d-test", kind: "test", reason: "test" }),
      delivery("sent", { id: "d-dec", kind: "decision" })], nextCursor: null }],
  };
  const app = await mount(integrations);
  await wait(() => app.renderer.root.findAll((n) => n.props?.["data-delivery-status"] && n.type === "td").length === 9, "deliveries rendered");
  assert.deepEqual(app.renderer.root.findAll((n) => n.props?.["data-delivery-status"] && n.type === "td").slice(0, 7).map((n) => text(n.children)),
    ["대기", "보내는 중", "보냄", "다시 시도 대기", "실패", "확인 불가", "발송 제한으로 생략"]);
  assert.deepEqual(app.renderer.root.findAll((n) => n.type === "th").map((n) => text(n.children)), ["시각", "종류", "받는 사람", "결과"]);
  const kinds = app.renderer.root.findAll((n) => n.type === "tr").slice(1).map((row) => text(row.findAll((n) => n.type === "td")[1].children));
  assert.deepEqual([...new Set(kinds)], ["사건", "시험", "판정"]);
  assert.ok(app.shown().includes("최근 발송"));
  await app.cleanup();
}

// 2-6) 사건 묶음 처리 대기: 같은 경계가 주기 작업 뒤에도 5분 넘게 머물면 표시(다시 조회할 때 판단, 주기 조회 없음).
{
  const realNow = Date.now;
  let now = 1_800_000_000_000;
  Date.now = () => now;
  try {
    let tickAt = "2026-11-21T03:00:00.000000Z";
    routes = {
      "GET /api/email-alerts/settings": () => [200, configured({ enabled: true, processing: { processedThroughSeq: "100", latestSeq: "140", armedAt: "x", lastTickAt: tickAt } })],
      "GET /api/email-alerts/deliveries": () => [200, { items: [], nextCursor: null }],
    };
    const app = await mount(integrations);
    await wait(() => app.shown().includes("설정됨"), "loaded");
    assert.ok(!app.shown().includes("사건 묶음 처리 대기 중"), "first look is not a delay");
    now += 5 * 60_000;
    tickAt = "2026-11-21T03:05:00.000000Z";
    await act(async () => app.client.invalidateQueries({ queryKey: ["email-alert-settings"] }));
    await wait(() => app.shown().includes("사건 묶음 처리 대기 중"), "delay shown after 5 minutes on the same boundary");
    await app.cleanup();
  } finally {
    Date.now = realNow;
  }
}

// 2-7) 설정 화면: 연동 관리에는 상단 `저장 미지원`이 없고(메일 카드 자체 저장), 사용자 환경에는 그대로. 알림 설정 채널 카드 설명은 승인 문구 한 줄.
{
  routes = {
    "GET /api/email-alerts/settings": () => [200, settingsBody()],
    "GET /api/email-alerts/deliveries": () => [200, { items: [], nextCursor: null }],
    "GET /api/me/settings": () => [200, { notifications: { decisionBasis: "final_any", minRiskLevel: "critical", includeUnrated: false, windowDays: 7, includeJobs: false,
      isDefault: true, updatedAt: null }, widgetLayout: { layout: null, updatedAt: null } }],
  };
  for (const [section, unsupported] of [["integrations", 0], ["profile", 1]]) {
    const app = await mount(() => React.createElement(SettingsPage), "admin", [`/settings?section=${section}`]);
    await tick(30);
    assert.equal(buttons(app, "저장 미지원").length, unsupported, `${section}: top unsupported save`);
    await app.cleanup();
  }
  const app = await mount((user) => React.createElement(NotificationSettings, { currentUser: user }));
  await wait(() => app.shown().includes("위험도별 알림 채널 지원"), "notification settings");
  const shown = app.shown();
  assert.equal(shown.split("위험도별 알림 채널은 지원하지 않습니다. 메일 알림은 연동 관리에서 설정합니다.").length - 1, 1, "approved description once");
  assert.ok(!shown.includes("위험도별 알림 채널과 외부 전송은 현재 서버에서 지원하지 않습니다."), "old three lines removed");
  await app.cleanup();
}

console.log("settings email QA passed: admin/user, locked switch, recipient checks, save failure keeps server values, password hidden, verify lines, test send results, delivery labels, processing delay");
