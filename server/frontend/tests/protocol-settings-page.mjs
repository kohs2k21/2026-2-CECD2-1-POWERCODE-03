import assert from "node:assert/strict";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

// 프로토콜 설정(contracts/protocol-settings-ui.md) 화면 시험(로컬 가짜 fetch만, 합성 인터페이스·채널): 룰 설정 진입 카드, 목록(등록 + 관측 미등록·검색·초안 없음 사유),
// 미등록 인터페이스 규칙 켜기 → 서버 미리보기 확인창 → 저장 본문, 상세(관측 경로 초안 → 고른 경로만 추가, 첫 항목은 미등록 규칙 켬 미리 선택, 경로 삭제, 저장 본문 v3),
// 항목 삭제, 규칙 추천 승인(관계 후보 → relationRules 새 항목, 이미 있으면 승인됨), 일반 사용자 접근 불가.
register("./svg-loader.mjs", import.meta.url);
register("data:text/javascript," + encodeURIComponent('export const load = (url, context, next) => url.endsWith(".css") ? { format: "module", source: "", shortCircuit: true } : next(url, context);'));
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
globalThis.requestAnimationFrame = (callback) => setTimeout(callback, 0);
globalThis.cancelAnimationFrame = (handle) => clearTimeout(handle);
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { createMemoryRouter, RouterProvider } = await import("react-router-dom");
const { AuthSessionProvider } = await import("../src/services/auth/AuthSessionProvider.tsx");
const { appRoutes } = await import("../src/app/appRoutes.tsx");
const { isAppPath, paths } = await import("../src/app/routes.ts");
const { RuleSaveDialog } = await import("../src/features/detection/rules/RuleDialogs.tsx");
const labels = await import("../src/features/detection/rules/protocols/protocolLabels.ts");
const ruleDraft = await import("../src/features/detection/rules/ruleDraft.ts");
const approvalLib = await import("../src/features/detection/rules/candidates/candidateApproval.ts");

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const STATUS_DEFAULT = { PROCESS_STATUS_F: "critical", PROCESS_STATUS_P: "warning", PROCESS_STATUS_Z: "warning", PROCESS_STATUS_H: "info", PROCESS_STATUS_N: "info" };
const hardRules = (unregisteredOn = false) => [
  { id: "hard:process-status", methodVersion: "process-status-hr-v2", displayName: "상태가 S가 아님", conditionText: "STATUS ≠ S", severity: { byCode: STATUS_DEFAULT }, enabled: true, editable: ["enabled", "severity"] },
  { id: "hard:negative-duration", methodVersion: "negative-duration-v1", displayName: "시작 시각이 종료 시각보다 늦음", conditionText: "x", severity: "critical", enabled: true, editable: ["enabled"] },
  { id: "hard:transaction-failed", methodVersion: "transaction-failed-v1", displayName: "거래 실패", conditionText: "y", severity: { byCode: {}, default: "warning" }, enabled: false, editable: ["enabled", "severity"] },
  { id: "hard:unregistered-interface", methodVersion: "unregistered-interface-v1", displayName: "등록되지 않은 인터페이스",
    conditionText: "프로토콜 설정에 없는 인터페이스의 거래(켜진 프로토콜 항목이 1개 이상일 때만)", severity: "critical", enabled: unregisteredOn, editable: ["enabled", "severity"] },
];
const entry = (interfaceId, channelsList, extra = {}) => ({ id: `hard:proto-${interfaceId === "IF_SYN_PAY" ? "0000000a" : "0000000b"}`, interfaceId,
  paths: channelsList.map((channels) => ({ channels, source: null })), severity: "critical", enabled: true, source: null, createdAtRevision: 3, updatedAtRevision: 3, ...extra });
const state = (revision = 12, extra = {}) => ({
  contractVersion: "rule-set-v1", revision, contentHash: "a".repeat(64), contentHashVersion: 3, savedAt: "2026-11-13T08:00:00Z", savedBy: { actorId: "1", displayName: "admin@example.test" },
  restoredFromRevision: null, catalogVersion: "method-catalog-v2",
  counts: { hardEnabled: 2, hardTotal: 4, softEnabled: 0, softTotal: 0, relationEnabled: 0, relationTotal: 0, protocolEnabled: 0, protocolTotal: 0 },
  hardRules: hardRules(), softRules: [], relationRules: [], protocolRules: [], ...extra,
});
const draftJob = { id: "11111111-2222-4333-8444-555555555555", status: "succeeded", errorCode: null, createdAt: "2026-11-13T01:00:00.000000Z", finishedAt: "2026-11-13T01:01:00.000000Z", snapshotId: "snap-1" };
const draftPath = (channels, transactions, extra = {}) => ({ channels, pathKey: channels.map((c) => c.replaceAll("%", "%25").replaceAll(":", "%3A")).join(":"), transactions,
  ratio: transactions / 157, rare: false, fromBranching: 0, exampleTransactionIds: ["TX000001"], ...extra });
const summary = (interfaceId, extra = {}) => ({ interfaceId, status: "draft", transactions: 157, pathTransactions: 157, branchingTransactions: 6, distinctPaths: 3,
  excludedReasons: {}, endpoints: { compared: 157, mismatch: 6, notCompared: 0 }, omittedPaths: { paths: 0, transactions: 0 }, listedPaths: 3, ...extra });
const draftsHead = { state: "available", latestJob: draftJob, job: draftJob, snapshotId: "snap-1", sourceId: "src-1", period: { from: "2026-10-01T00:00:00Z", until: "2026-11-01T00:00:00Z" },
  split: "train", parameters: { minTransactions: 30, rarePercent: 1, maxPathSteps: 64, maxRoutes: 64, maxProcessRows: 200, maxOutputPaths: 64, reasonOrder: ["channel_absent"] },
  excludedReasons: {}, transactionIdMissingRows: 0, endpointMismatch: 6, interfaces: [summary("IF_SYN_ORDER"), summary("IF_SYN_PAY", { distinctPaths: 2, pathTransactions: 47 })] };
const orderDraft = { ...summary("IF_SYN_ORDER"), excludedReasons: { channel_absent: 2 }, omittedPaths: { paths: 1, transactions: 1 },
  paths: [draftPath(["CH_GW", "CH_ORDER", "CH_DB"], 120), draftPath(["CH_GW", "CH_ORDER", "CH_CACHE", "CH_DB"], 30), draftPath(["CH_GW", "채널_외부", "CH_DB"], 1, { rare: true })] };

let routes = {};
const requested = [];
globalThis.fetch = async (url, options = {}) => {
  const target = new URL(String(url), "http://local.test");
  const method = options.method ?? "GET";
  requested.push({ method, path: target.pathname, search: target.search, body: options.body ? JSON.parse(options.body) : undefined });
  if (target.pathname === "/api/auth/me") return json({ user: currentUser });
  if (target.pathname === "/api/pipeline/events") return new Response(new ReadableStream({ start() {} }), { headers: { "Content-Type": "text/event-stream" } });
  const handler = routes[`${method} ${target.pathname}`];
  return handler ? handler(target, options) : json({ code: "rule_set_storage_unavailable" }, 503);
};
const calls = (method, path) => requested.filter((item) => item.method === method && item.path === path);
const rawText = (node) => (!node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(rawText).join("") : rawText(node.children));
const text = (node) => rawText(node).replace(/\s+/g, " ").trim();
const tick = (ms = 10) => act(async () => new Promise((resolve) => setTimeout(resolve, ms)));
const wait = async (check, label, tries = 200) => {
  for (let i = 0; i < tries && !check(); i++) await tick();
  assert.ok(check(), label);
};
let currentUser;
const mount = async (path, userType = "admin", locationState = undefined) => {
  currentUser = { id: `${userType}-1`, email: `${userType}@example.test`, userType, createdAt: "2026-10-01T00:00:00Z" };
  storage.set("token", `token-${userType}`);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { gcTime: Infinity } } });
  const router = createMemoryRouter(appRoutes, { initialEntries: [locationState ? { pathname: path, state: locationState } : path] });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(QueryClientProvider, { client }, React.createElement(AuthSessionProvider, null, React.createElement(RouterProvider, { router }))));
  });
  const shown = () => text(renderer.toJSON());
  const cleanup = async () => { await act(async () => renderer.unmount()); router.dispose(); client.clear(); };
  const click = async (label, index = 0) => {
    const buttons = renderer.root.findAll((n) => n.type === "button" && text(n.children).includes(label) && !n.props.disabled);
    assert.ok(buttons[index], `button ${label}`);
    await act(async () => buttons[index].props.onClick?.({ stopPropagation() {}, preventDefault() {} }));
  };
  return { renderer, router, client, shown, cleanup, click };
};
const byData = (app, key) => app.renderer.root.findAll((node) => node.props?.[key] !== undefined && typeof node.type === "string");
const rowsOf = (app, key) => byData(app, key).map((row) => row.findAllByType("td").map((td) => text(td.children)));
const pendingOf = (app) => app.renderer.root.findByType(RuleSaveDialog).props.pending;
const previewOk = (changes, warnings = []) => () => json({ valid: true, summary: {}, hardDisabledAfterSave: [], changes, warnings });

// 1) 문구·주소·비교 규칙
{
  assert.equal(paths.detectionRuleProtocol("IF A/%"), "/detection/rules/protocols/IF%20A%2F%25");
  assert.ok(isAppPath("/detection/rules/protocols") && isAppPath("/detection/rules/protocols/IF_A"));
  assert.deepEqual(labels.parseChannelLines("CH_A\r\n 채널 B \n\nCH:C\n"), ["CH_A", " 채널 B ", "CH:C"], "no trim, CR removed, blank lines dropped");
  assert.ok(labels.samePath(["A", "B"], ["A", "B"]) && !labels.samePath(["A", "B"], ["A", "B "]) && !labels.samePath(["A"], ["A", "A"]));
  assert.deepEqual([0, 0.0004, 0.764331, 0.9996, 1].map(labels.ratioText), ["0%", "0.1%", "76.4%", "99.9%", "100%"]);
  assert.equal(labels.UNREGISTERED_WITHOUT_ENTRIES, "켜진 프로토콜 항목이 없어 등록되지 않은 인터페이스 규칙이 아무것도 판정하지 않습니다.", "same sentence as the API warning");
}

// 2) 룰 설정 진입 카드
{
  routes = {
    "GET /api/rule-set": () => json(state(12, { protocolRules: [entry("IF_SYN_PAY", [["CH_GW", "CH_PAY"]])] })),
    "GET /api/rule-set/history": () => json({ items: [], pagination: { limit: 50, hasMore: false, nextCursor: null } }),
    "GET /api/jobs/capabilities": () => json({ methodCatalog: { softRuleFields: [] } }),
  };
  const app = await mount(paths.detectionRules);
  // 사이드바 목차에도 "프로토콜 설정"이 있으므로 카드 설명으로 기다린다.
  await wait(() => app.shown().includes("인터페이스별 허용 경로"), "entry card");
  assert.ok(app.shown().includes("인터페이스별 허용 경로 · 정상 프로토콜 1개(켜짐 1개)"), app.shown().slice(0, 400));
  const link = app.renderer.root.findAll((n) => n.type === "a" && n.props.href === "/detection/rules/protocols");
  assert.equal(link.length, 1);
  assert.ok(app.shown().includes("관계 규칙: 표시할 항목이 없습니다."));
  await app.cleanup();
}

// 3) 목록: 등록 항목 + 관측된 미등록 인터페이스, 검색, 미등록 규칙 켜기 → 확인창 → 저장 본문
{
  let current = state(12, { protocolRules: [entry("IF_SYN_PAY", [["CH_GW", "CH_PAY"], ["CH_GW", "CH_PAY%"]], { enabled: false })] });
  routes = {
    "GET /api/rule-set": () => json(current),
    "GET /api/rule-set/protocol-drafts": () => json(draftsHead),
    "POST /api/rule-set/preview": previewOk([{ type: "hard_enabled", id: "hard:unregistered-interface", displayName: "등록되지 않은 인터페이스" }],
      [{ code: "protocol_unregistered_without_entries", ruleId: "hard:unregistered-interface", message: labels.UNREGISTERED_WITHOUT_ENTRIES }]),
    "PUT /api/rule-set": () => { current = state(13, { hardRules: hardRules(true), protocolRules: current.protocolRules }); return json({ revision: 13, contentHash: "b".repeat(64), appliedAt: "x", changes: [] }); },
  };
  const app = await mount(paths.detectionRuleProtocols);
  await wait(() => byData(app, "data-protocol-row").length === 2, "registered + observed rows");
  assert.deepEqual(rowsOf(app, "data-protocol-row"), [
    ["IF_SYN_PAY", "2개", "위험", "꺼짐", "경로 2개 · 거래 47건"],
    ["IF_SYN_ORDER", "—", "—", "미등록", "경로 3개 · 거래 157건"],
  ]);
  assert.ok(app.shown().includes("관측 경로 초안 · 후보 작업 완료"));
  const search = app.renderer.root.findAll((n) => n.type === "input" && n.props["aria-label"] === "인터페이스 검색")[0];
  await act(async () => search.props.onChange({ target: { value: "order" } }));
  assert.deepEqual(rowsOf(app, "data-protocol-row").map((row) => row[0]), ["IF_SYN_ORDER"], "case-insensitive substring");
  await act(async () => search.props.onChange({ target: { value: "nothing" } }));
  assert.ok(app.shown().includes("검색 결과가 없습니다."));
  const toggle = app.renderer.root.findAll((n) => n.props?.["aria-label"] === "등록되지 않은 인터페이스 켜기" && typeof n.props.onCheckedChange === "function")[0];
  await act(async () => toggle.props.onCheckedChange(true));
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "confirm dialog");
  const preview = calls("POST", "/api/rule-set/preview").at(-1).body;
  assert.deepEqual(preview.hardRules.find((rule) => rule.id === "hard:unregistered-interface"), { id: "hard:unregistered-interface", enabled: true, severity: "critical" });
  assert.deepEqual(preview.protocolRules, [{ id: "hard:proto-0000000a", interfaceId: "IF_SYN_PAY", paths: [{ channels: ["CH_GW", "CH_PAY"] }, { channels: ["CH_GW", "CH_PAY%"] }], severity: "critical", enabled: false }],
    "other items are sent as they are");
  assert.deepEqual(pendingOf(app).warnings.map((warning) => warning.message), [labels.UNREGISTERED_WITHOUT_ENTRIES], "server warning goes to the dialog");
  await act(async () => pendingOf(app).run(false));
  await wait(() => app.shown().includes("운영에 적용됨 · 버전 13"), "applied");
  assert.equal(calls("PUT", "/api/rule-set").length, 1);
  await wait(() => byData(app, "data-unregistered-idle").length === 1, "idle warning: rule on, no enabled entries");
  await app.cleanup();
}

// 4) 초안 없음 사유는 빈 목록이 아니라 명시 문구
{
  routes = {
    "GET /api/rule-set": () => json(state()),
    "GET /api/rule-set/protocol-drafts": () => json({ state: "protocol_drafts_unavailable", reason: "not_in_result", latestJob: draftJob, job: draftJob }),
  };
  const app = await mount(paths.detectionRuleProtocols);
  await wait(() => byData(app, "data-drafts-unavailable").length === 1, "unavailable reason");
  assert.equal(text(byData(app, "data-drafts-unavailable")[0].children), "관측 경로 초안 없음 · 최근 후보 작업 결과에 경로 집계가 없습니다(경로 집계 전 Trainer 결과)");
  assert.ok(app.shown().includes("정상 프로토콜: 표시할 항목이 없습니다."));
  await app.cleanup();
}

// 4-1) 목록에서 후보 작업 요청(규칙 추천과 같은 확인창·API) → 진행 중 상태·5초 조회 → 성공하면 초안 표가 들어온다, 실패는 `실패 · <코드>`
{
  const { Dialog } = await import("../src/components/ui/dialog.tsx");
  const { NativeSelect } = await import("../src/components/ui/native-select.tsx");
  const candidateLabels = await import("../src/features/detection/rules/candidates/candidateLabels.ts");
  const elements = (node) => (Array.isArray(node) ? node.flatMap(elements) : React.isValidElement(node) ? [node, ...elements(node.props.children)] : []);
  const openDialog = (app) => app.renderer.root.findAllByType(Dialog).find((node) => node.props.open);
  const dialogPart = (app, match) => elements(openDialog(app)?.props.children ?? []).find(match);
  const pageButton = (app, label) => app.renderer.root.findAll((n) => n.type === "button" && text(n.children) === label)[0];
  const SNAP = "33333333-4444-4555-8666-777777777777";
  const NEW_JOB = "44444444-5555-4666-8777-888888888888";
  const newJob = { ...draftJob, id: NEW_JOB, snapshotId: SNAP };
  let phase = "old";
  const posted = [];
  routes = {
    "GET /api/rule-set": () => json(state()),
    "GET /api/rule-set/protocol-drafts": () => json(phase === "old" ? { state: "protocol_drafts_unavailable", reason: "not_in_result", latestJob: draftJob, job: draftJob }
      : phase === "queued" ? { state: "protocol_drafts_unavailable", reason: "not_in_result", latestJob: { ...newJob, status: "queued", finishedAt: null }, job: draftJob }
        : phase === "failed" ? { state: "protocol_drafts_unavailable", reason: "not_in_result", latestJob: { ...newJob, status: "failed", errorCode: "relation_snapshot_not_ready" }, job: draftJob }
          : { ...draftsHead, latestJob: newJob, job: newJob, snapshotId: SNAP }),
    "GET /api/rule-set/candidates/snapshots": () => json({ items: [{ id: SNAP, sourceId: "src-1", createdAt: "2026-11-13T01:00:00.000000Z", readyAt: null, from: "2026-10-01T00:00:00Z", until: "2026-11-01T00:00:00Z" }] }),
    "POST /api/rule-set/candidates/jobs": (_target, init) => {
      posted.push({ body: JSON.parse(init.body), key: new Headers(init.headers).get("Idempotency-Key") });
      phase = "queued";
      return json({ id: NEW_JOB, status: "queued", kind: "recommend_relations", inputSpec: { kind: "recommend_relations", snapshotId: SNAP, sourceId: "src-1" } }, 201);
    },
  };
  const app = await mount(paths.detectionRuleProtocols);
  await wait(() => byData(app, "data-drafts-unavailable").length === 1 && Boolean(pageButton(app, candidateLabels.JOB_REQUEST)), "request button next to the unavailable reason");
  assert.equal(byData(app, "data-latest-job").length, 0, "latest job is the result job");
  await act(async () => pageButton(app, candidateLabels.JOB_REQUEST).props.onClick());
  await wait(() => Boolean(dialogPart(app, (n) => n.type === NativeSelect)), "dialog lists saved datasets");
  await act(async () => dialogPart(app, (n) => typeof n.props.onClick === "function" && n.props.children === candidateLabels.JOB_REQUEST_SUBMIT).props.onClick());
  await wait(() => !openDialog(app), "dialog closes after acceptance");
  assert.deepEqual(posted.map((p) => p.body), [{ snapshotId: SNAP }], "same request body as rule candidates (snapshotId only)");
  assert.ok(posted[0].key, "idempotency key");
  await wait(() => byData(app, "data-latest-job").length === 1, "drafts re-read: queued job shown");
  const latest = byData(app, "data-latest-job")[0];
  assert.ok(text(latest.children).startsWith("최근 작업 대기"), text(latest.children));
  assert.equal(latest.findAllByType("a")[0].props.href, `/detection/operations/jobs/${NEW_JOB}`, "job detail link");
  assert.equal(pageButton(app, candidateLabels.JOB_REQUEST).props.disabled, true, "no second request while a candidate job runs");
  assert.equal(text(byData(app, "data-candidate-running")[0].children), candidateLabels.JOB_REQUEST_RUNNING);
  phase = "succeeded";
  await wait(() => byData(app, "data-protocol-row").length === 2 && byData(app, "data-latest-job").length === 0, "drafts table filled after polling", 800);
  assert.equal(byData(app, "data-drafts-unavailable").length, 0);
  await app.cleanup();
  phase = "failed";
  const failed = await mount(paths.detectionRuleProtocols);
  await wait(() => byData(failed, "data-latest-job").length === 1, "failed job shown");
  assert.ok(text(byData(failed, "data-latest-job")[0].children).startsWith("최근 작업 실패 · relation_snapshot_not_ready"), "failure keeps the code; old reason stays");
  assert.equal(byData(failed, "data-drafts-unavailable").length, 1);
  assert.equal(pageButton(failed, candidateLabels.JOB_REQUEST).props.disabled, false, "request is available again after failure");
  await failed.cleanup();
}

// 5) 상세(첫 항목): 초안을 불러와 고른 경로만 추가, 미등록 규칙 켬 미리 선택, 저장 본문
{
  let current = state(12);
  routes = {
    "GET /api/rule-set": () => json(current),
    "GET /api/rule-set/protocol-drafts": (target) => json({ ...draftsHead, interface: target.searchParams.get("interfaceId") === "IF_SYN_ORDER" ? orderDraft : null }),
    "POST /api/rule-set/preview": previewOk([{ type: "hard_enabled", id: "hard:unregistered-interface", displayName: "등록되지 않은 인터페이스" }, { type: "protocol_added", id: null, clientRef: "protocol-new", interfaceId: "IF_SYN_ORDER", paths: 2 }]),
    "PUT /api/rule-set": () => {
      current = state(13, { hardRules: hardRules(true), protocolRules: [entry("IF_SYN_ORDER", [["CH_GW", "CH_ORDER", "CH_CACHE", "CH_DB"], ["CH_GW", "CH_ORDER", "CH_DB"]])] });
      return json({ revision: 13, contentHash: "b".repeat(64), appliedAt: "x", changes: [] });
    },
  };
  requested.length = 0;
  const app = await mount(paths.detectionRuleProtocol("IF_SYN_ORDER"), "admin", { returnTo: paths.detectionRuleProtocols });
  await wait(() => app.shown().includes("새 항목"), "new entry");
  assert.ok(!calls("GET", "/api/rule-set/protocol-drafts").length, "drafts are read only on request");
  assert.ok(app.shown().includes("확인 필요 · 허용 경로를 1개 이상 등록해 주세요"));
  const back = app.renderer.root.findAll((n) => n.type === "a" && n.props["aria-label"]?.startsWith("뒤로"))[0];
  assert.equal(back.props.href, "/detection/rules/protocols");
  await app.click("관측 경로로 초안 만들기");
  await wait(() => byData(app, "data-draft-path").length === 3, "draft rows");
  assert.equal(calls("GET", "/api/rule-set/protocol-drafts").at(-1).search, "?interfaceId=IF_SYN_ORDER");
  assert.deepEqual(rowsOf(app, "data-draft-path").map((row) => row.slice(1)), [
    ["CH_GW→CH_ORDER→CH_DB", "120건", "76.4%", ""], ["CH_GW→CH_ORDER→CH_CACHE→CH_DB", "30건", "19.1%", ""], ["CH_GW→채널_외부→CH_DB", "1건", "0.6%", "드묾 · 1% 미만"],
  ].map(([path, ...rest]) => [path.replaceAll("→", ""), ...rest]));
  assert.ok(text(byData(app, "data-draft-excluded")[0].children).includes("채널 없음 2건"));
  assert.ok(text(byData(app, "data-draft-omitted")[0].children).includes("표시하지 않은 경로 1개 · 거래 1건"));
  // 같은 props가 Checkbox와 안쪽 Radix 요소에 함께 있으므로 aria-label로 하나씩만 고른다.
  const pick = (index) => {
    const found = app.renderer.root.findAll((n) => typeof n.props?.onCheckedChange === "function" && n.props["aria-label"]?.endsWith(" 선택"));
    return [...new Map(found.map((n) => [n.props["aria-label"], n])).values()][index];
  };
  await act(async () => pick(0).props.onCheckedChange(true));
  await act(async () => pick(1).props.onCheckedChange(true));
  await app.click("선택한 경로 2개 추가");
  await wait(() => byData(app, "data-allowed-path").length === 2, "two allowed paths");
  assert.equal(byData(app, "data-draft-path").filter((row) => text(row.children).includes("허용됨")).length, 2, "added paths are marked in the draft table");
  const first = app.renderer.root.findAll((n) => n.props?.id === "protocol-first-unregistered" && typeof n.props.onCheckedChange === "function")[0];
  assert.equal(first.props.checked, true, "unregistered-interface rule pre-selected on the first entry");
  await app.click("저장");
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "confirm");
  const body = calls("POST", "/api/rule-set/preview").at(-1).body;
  assert.deepEqual(body.protocolRules, [{ id: null, clientRef: "protocol-new", interfaceId: "IF_SYN_ORDER", severity: "critical", enabled: true,
    paths: [{ channels: ["CH_GW", "CH_ORDER", "CH_DB"], source: { kind: "draft", jobId: draftJob.id } }, { channels: ["CH_GW", "CH_ORDER", "CH_CACHE", "CH_DB"], source: { kind: "draft", jobId: draftJob.id } }],
    source: { jobId: draftJob.id, snapshotId: "snap-1", period: draftsHead.period } }]);
  assert.equal(body.hardRules.find((rule) => rule.id === "hard:unregistered-interface").enabled, true);
  assert.deepEqual([body.relationRules, body.softRules, body.restoreFromRevision], [[], [], null]);
  await act(async () => pendingOf(app).run(false));
  await wait(() => app.shown().includes("운영에 적용됨 · 버전 13") && app.shown().includes("등록됨") && !app.shown().includes("새 항목"), "saved and reloaded");
  assert.ok(!app.shown().includes("hard:proto-"), "no internal rule id on screen");
  assert.ok(!app.shown().includes("확인 필요"), "edit reset to the saved state");
  await app.cleanup();
}

// 6) 상세(기존 항목): 경로 직접 추가·삭제, 꺼짐 유지 → 미등록 규칙 선택 없음, 항목 삭제 → 목록으로
{
  let current = state(20, { hardRules: hardRules(true), protocolRules: [entry("IF_SYN_PAY", [["CH_GW", "CH_PAY"], ["CH_GW", "CH_BANK:01"]])] });
  routes = {
    "GET /api/rule-set": () => json(current),
    "GET /api/rule-set/protocol-drafts": () => json(draftsHead),
    "POST /api/rule-set/preview": previewOk([{ type: "protocol_updated", id: "hard:proto-0000000a", interfaceId: "IF_SYN_PAY", diff: { pathsAdded: [["CH_GW", " CH_X"]], pathsRemoved: [["CH_GW", "CH_BANK:01"]] } }]),
    "PUT /api/rule-set": () => { current = state(21, { hardRules: hardRules(true) }); return json({ revision: 21, contentHash: "c".repeat(64), appliedAt: "x", changes: [] }); },
  };
  const app = await mount(paths.detectionRuleProtocol("IF_SYN_PAY"));
  await wait(() => byData(app, "data-allowed-path").length === 2, "stored paths");
  assert.equal(app.renderer.root.findAll((n) => n.props?.id === "protocol-first-unregistered").length, 0, "not the first entry");
  const saveButton = () => app.renderer.root.findAll((n) => n.type === "button" && text(n.children) === "저장")[0];
  assert.equal(saveButton().props.disabled, true, "no change yet");
  await app.click("경로 추가");
  // 대화상자 본문(포털)은 시험 렌더러에 그려지지 않으므로 본문은 따로 시험하고(9), 여기서는 추가 명령만 부른다.
  const addDialog = app.renderer.root.findAll((n) => typeof n.props?.onAdd === "function" && Array.isArray(n.props.existing))[0];
  await act(async () => addDialog.props.onAdd(["CH_GW", " CH_X"]));
  await wait(() => byData(app, "data-allowed-path").length === 3, "manual path added");
  await act(async () => app.renderer.root.findAll((n) => n.type === "button" && n.props["aria-label"] === "CH_GW → CH_BANK:01 삭제")[0].props.onClick());
  assert.equal(byData(app, "data-removed-path").length, 1, "removed stored path shown until saved");
  await act(async () => saveButton().props.onClick());
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "confirm");
  assert.deepEqual(calls("POST", "/api/rule-set/preview").at(-1).body.protocolRules, [{ id: "hard:proto-0000000a", interfaceId: "IF_SYN_PAY", severity: "critical", enabled: true,
    paths: [{ channels: ["CH_GW", "CH_PAY"] }, { channels: ["CH_GW", " CH_X"] }] }], "raw channel values, no source for manual paths");
  assert.deepEqual(pendingOf(app).changes.map(ruleDraft.changeLine), ["정상 프로토콜 수정 · IF_SYN_PAY · 허용 경로 추가 CH_GW →  CH_X, 허용 경로 삭제 CH_GW → CH_BANK:01"]);
  await act(async () => app.renderer.root.findByType(RuleSaveDialog).props.onCancel());
  await app.click("항목 삭제");
  const confirm = app.renderer.root.findAll((n) => typeof n.props?.onConfirm === "function" && n.props.actionText === "다음")[0];
  assert.equal(confirm.props.message, "IF_SYN_PAY 항목(허용 경로 2개)을 삭제합니다. 다음 확인창에서 저장하면 운영에 반영됩니다.");
  await act(async () => confirm.props.onConfirm());
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "delete confirm");
  assert.deepEqual(calls("POST", "/api/rule-set/preview").at(-1).body.protocolRules, [], "entry removed from the full state");
  await act(async () => pendingOf(app).run(false));
  await wait(() => app.router.state.location.pathname === paths.detectionRuleProtocols, "back to the list after delete");
  await app.cleanup();
}

// 7) 규칙 추천 승인: 관계 후보 → relationRules 새 항목(서버 id 발급), 이미 같은 조건이 있으면 승인됨
{
  const JOB = "8cc86810-673b-4886-87b1-e026856fe636";
  const draft = { kind: "precedes", scope: { interfaceId: "IF-A", adapterType: "HTTP" }, from: { processId: "P1" }, to: { processId: "P2" }, match: null, enabled: true, source: { jobId: JOB } };
  const detail = { job: { ...draftJob, id: JOB }, snapshotId: "snap-1", sourceId: "s-1", period: { from: null, until: null, timeBasis: null }, generatedAt: null, experimental: true,
    parameters: { minApplicable: 30, candidatePercent: 99, reviewPercent: 95, maxCounterexamples: 5 }, isLatest: true, approval: { available: true, reason: null, contentHashVersion: 3 },
    type: "relation", relation: { candidateId: `${JOB}.r0`, kind: "precedes", status: "candidate", approvable: true, listedForPairing: false, pairApprovable: null, interfaceId: "IF-A",
      scope: { adapterType: "HTTP" }, from: { processId: "P1" }, to: { processId: "P2" }, match: null, counts: { applicable: 100, satisfied: 100, violated: 0, undetermined: 0 },
      satisfactionRate: 1, evaluatedAt: "successor", patternEdgeId: null, nonStandardProcessId: false, violationReasons: {}, undeterminedReasons: {}, violationsByTransactionStatus: {},
      fromAdapterTypes: [], bySuffixClass: null },
    counterexamples: [], pairedPrecedes: null, ruleDraft: draft };
  let current = state(30);
  routes = {
    "GET /api/rule-set": () => json(current),
    [`GET /api/rule-set/candidates/${JOB}.r0`]: () => json(detail),
    "POST /api/rule-set/preview": previewOk([{ type: "relation_added", id: null, clientRef: "candidate", kind: "precedes", interfaceId: "IF-A", from: "P1", to: "P2" }]),
    "PUT /api/rule-set": () => {
      current = state(31, { relationRules: [{ ...draft, id: "hard:rel-0000000c", severity: "warning", scope: { interfaceId: "IF-A", adapterType: "HTTP" }, createdAtRevision: 31, updatedAtRevision: 31 }] });
      return json({ revision: 31, contentHash: "d".repeat(64), appliedAt: "x", changes: [] });
    },
  };
  const app = await mount(paths.detectionRuleCandidate(`${JOB}.r0`));
  await wait(() => app.shown().includes("승인하면 룰 세트 새 버전으로 저장되고 되돌리기할 수 있습니다"), "approve enabled");
  await app.click("승인");
  const ask = app.renderer.root.findAll((n) => typeof n.props?.onNext === "function" && n.props.kind === "relation")[0];
  await act(async () => ask.props.onNext({ name: "", severity: "warning" }));
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "approve confirm");
  const sent = calls("POST", "/api/rule-set/preview").at(-1).body;
  assert.deepEqual(sent.relationRules, [{ ...draft, id: null, clientRef: "candidate", severity: "warning", enabled: true }]);
  assert.deepEqual(pendingOf(app).changes.map(ruleDraft.changeLine), ["관계 규칙 추가 · 선행 관계 P1 → P2"]);
  await act(async () => pendingOf(app).run(false));
  await wait(() => byData(app, "data-approved").length === 1, "approved after save (same condition exists)");
  assert.equal(approvalLib.approvedRelation(current, { ...draft, scope: { adapterType: "HTTP", interfaceId: "OTHER" } }), true, "display interfaceId is outside the condition");
  assert.equal(approvalLib.approvedRelation(current, { ...draft, scope: null }), false, "adapter scope is part of the condition");
  await app.cleanup();
}

// 9) 경로 추가 본문: 한 줄에 채널 하나, 없음 값·중복·단계 상한은 먼저 막는다.
{
  const { AddPathBody } = await import("../src/features/detection/rules/protocols/ProtocolDetailPage.tsx");
  const added = [];
  let body;
  await act(async () => { body = create(React.createElement(AddPathBody, { existing: [["CH_GW", "CH_PAY"]], onAdd: (channels) => added.push(channels), onCancel() {} })); });
  const area = () => body.root.findAll((n) => n.type === "textarea")[0];
  const add = () => body.root.findAll((n) => n.type === "button" && text(n.children) === "추가")[0];
  const problem = () => body.root.findAll((n) => n.props?.role === "alert").map((n) => text(n.children)).join("");
  assert.equal(add().props.disabled, true, "empty input");
  const absentMessage = "빈 값·\\N인 채널은 저장할 수 없습니다.";
  for (const [value, message] of [["CH_GW\nCH_PAY", "이미 있는 허용 경로입니다."], ["CH_GW\n \\N ", absentMessage], ["CH_GW\n  ", absentMessage],
    [Array.from({ length: 65 }, (_, index) => `C${index}`).join("\n"), "경로 하나는 64단계까지입니다."]]) {
    await act(async () => area().props.onChange({ target: { value } }));
    assert.equal(problem(), message, JSON.stringify(value).slice(0, 30));
    assert.equal(add().props.disabled, true);
  }
  await act(async () => area().props.onChange({ target: { value: "CH_GW\r\n CH_X\nCH_GW" } }));
  assert.equal(problem(), "");
  await act(async () => add().props.onClick());
  assert.deepEqual(added, [["CH_GW", " CH_X", "CH_GW"]], "raw values, repeated channels are not merged");
  await act(async () => body.unmount());
}

// 8) 일반 사용자는 탐지 설정에 들어올 수 없다
{
  requested.length = 0;
  const app = await mount(paths.detectionRuleProtocols, "user");
  await tick(30);
  assert.equal(calls("GET", "/api/rule-set/protocol-drafts").length, 0, "no admin request as a user");
  assert.ok(!app.shown().includes("정상 프로토콜 목록"), "admin screen is not shown");
  await app.cleanup();
}
// 10) 알림 [정상 프로토콜에 추가]: 넘겨받은 경로를 초안에 더해 열고(출처 alert), 저장은 같은 확인창 → 룰 세트 새 버전.
{
  const DECISION = "44444444-4444-4444-8444-444444444444";
  routes = {
    "GET /api/rule-set": () => json(state(40, { hardRules: hardRules(true), protocolRules: [entry("IF_SYN_PAY", [["CH_GW", "CH_PAY"]])] })),
    "POST /api/rule-set/preview": previewOk([{ type: "protocol_updated", id: "hard:proto-0000000a", interfaceId: "IF_SYN_PAY", diff: { pathsAdded: [["CH_GW", "CH:X"]], pathsRemoved: [] } }]),
  };
  const app = await mount(paths.detectionRuleProtocol("IF_SYN_PAY"), "admin", { returnTo: paths.detectionRuleProtocols, alertPath: { channels: ["CH_GW", "CH:X"], decisionId: DECISION } });
  await wait(() => byData(app, "data-allowed-path").length === 2, "alert path added to the draft");
  assert.equal(byData(app, "data-alert-path").length, 1);
  await app.click("저장");
  await wait(() => app.renderer.root.findAllByType(RuleSaveDialog).length === 1, "confirm");
  assert.deepEqual(calls("POST", "/api/rule-set/preview").at(-1).body.protocolRules[0].paths,
    [{ channels: ["CH_GW", "CH_PAY"] }, { channels: ["CH_GW", "CH:X"], source: { kind: "alert", decisionId: DECISION } }]);
  await app.cleanup();
  // 이미 허용된 경로면 더하지 않는다.
  const again = await mount(paths.detectionRuleProtocol("IF_SYN_PAY"), "admin", { alertPath: { channels: ["CH_GW", "CH_PAY"], decisionId: DECISION } });
  await wait(() => byData(again, "data-allowed-path").length === 1, "already allowed");
  assert.equal(byData(again, "data-alert-path").length, 0);
  // 같은 상세가 열린 채로 알림에서 다시 열어도(같은 주소, 다른 경로) 초안을 새로 만든다.
  await act(async () => again.router.navigate(paths.detectionRuleProtocol("IF_SYN_PAY"), { state: { alertPath: { channels: ["CH_GW", "CH_NEW"], decisionId: DECISION } } }));
  await wait(() => byData(again, "data-allowed-path").length === 2 && byData(again, "data-alert-path").length === 1, "same-route navigation adds the new alert path");
  await again.cleanup();
}

// 11) 경로 설명(protocol-explanation-job.md 1·6·7·11절): 초안을 읽은 뒤에만 "LLM 설명" 칸 [경로 설명 보기] → POST explain_protocol 관측 경로 대상
// (jobId = 초안 작업, ruleSetRevision = 현재 운영 버전, 멱등키) → "설명을 만드는 중입니다…" → 결과(경로 id 칩·확인할 점·버전 N 안내).
// 같은 대상의 기존 작업은 요청 없이 이어서 보이고(확인할 점 없으면 숨김), 실패는 `경로 설명 실패 · <코드>`(점검 실패 사유 코드 포함) + 새 키로 다시 요청,
// 접수 거절은 코드 그대로, LLM 꺼짐은 버튼 비활성 + `경로 설명 · <코드>`, 초안에도 운영 버전에도 없는 인터페이스는 칸 없음.
{
  const EXPLAIN_JOB = "55555555-6666-4777-8888-999999999999";
  const target = { kind: "observed_paths", jobId: draftJob.id, interfaceId: "IF_SYN_ORDER", ruleSetRevision: 30 };
  const body = { kind: "explain_protocol", sourceId: "src-1", target };
  const resultOf = (reviewPoints) => ({ format: "protocol-explanation-v1", target, interfaceId: "IF_SYN_ORDER", ruleSetRevision: 30,
    explanation: "인터페이스 IF_SYN_ORDER의 관측 경로 P2는 허용 경로에 없습니다. P1은 허용 경로입니다. XP1·P9는 경로 id가 아닙니다.", reviewPoints,
    paths: { P1: ["CH_GW", "CH_ORDER", "CH_DB"], P2: ["CH_GW", "채널_외부", "CH_DB"] },
    facts: { format: "protocol-explain-facts-v1", kind: "observed_paths", sha256: "c".repeat(64) },
    promptVersion: "protocol-explain-prompt-v5", modelVersion: "qwen-base-6a9e13bd", baseRevision: "r", check: { passed: true, failures: [], coverage: {} } });
  const listItem = (id, status, extra = {}) => ({ id, kind: "explain_protocol", status, inputSpec: body, attemptCount: 1, maxAttempts: 3, nextRunAt: null, errorCode: null,
    resultConfigVersionId: null, createdAt: "2026-11-14T01:00:00.000000Z", finishedAt: null, result: null, ...extra });
  let jobs = [];
  let jobState = { status: "queued", errorCode: null, resultSummary: null };
  let llm = { available: true, checkedAt: "2026-11-14T01:00:00.000Z", errorCode: null };
  let postReply = () => json({ id: EXPLAIN_JOB, status: "queued", kind: "explain_protocol", inputSpec: body }, 201);
  const posted = [];
  routes = {
    "GET /api/rule-set": () => json(state(30)),
    "GET /api/rule-set/protocol-drafts": (url) => json({ ...draftsHead, interface: url.searchParams.get("interfaceId") === "IF_SYN_ORDER" ? orderDraft : null }),
    "GET /api/llm/status": () => json(llm),
    "GET /api/jobs": () => json({ items: jobs, pagination: { limit: 100, hasMore: false, nextCursor: null } }),
    "POST /api/jobs": (_url, init) => { posted.push({ body: JSON.parse(init.body), key: new Headers(init.headers).get("Idempotency-Key") }); return postReply(); },
    [`GET /api/jobs/${EXPLAIN_JOB}`]: () => json({ ...listItem(EXPLAIN_JOB, jobState.status), ...jobState, attempts: [] }),
  };
  const card = (app) => app.renderer.root.findAll((n) => n.type === "div" && n.props["data-slot"] === "section-card" && n.props["aria-label"] === "LLM 설명")[0];
  const explainButton = (app) => app.renderer.root.findAll((n) => n.type === "button" && text(n.children) === "경로 설명 보기")[0];
  const openDrafts = async (app) => {
    await wait(() => app.shown().includes("새 항목"), "detail");
    assert.ok(!card(app), "no explanation box before the drafts are read");
    await app.click("관측 경로로 초안 만들기");
    await wait(() => Boolean(card(app)) && !card(app).findAll((n) => n.props?.["data-slot"] === "loading-state").length, "explanation box after drafts");
  };

  requested.length = 0;
  const app = await mount(paths.detectionRuleProtocol("IF_SYN_ORDER"));
  await openDrafts(app);
  assert.equal(calls("GET", "/api/jobs").at(-1).search, "?kind=explain_protocol&limit=100", "existing jobs looked up by kind");
  assert.equal(explainButton(app).props.disabled, false);
  await act(async () => explainButton(app).props.onClick());
  await wait(() => text(card(app).children).includes("설명을 만드는 중입니다…"), "running");
  assert.deepEqual(posted.map((p) => p.body), [body], "observed_paths request: draft job, raw interface id, current version");
  assert.match(posted[0].key, /^[0-9a-f-]{36}$/, "idempotency key");
  const firstKey = posted[0].key;
  await wait(() => calls("GET", `/api/jobs/${EXPLAIN_JOB}`).length > 0 && text(card(app).children).includes("설명을 만드는 중입니다…"), "queued job read");
  await wait(() => !explainButton(app), "no second request while running");
  assert.equal(byData(app, "data-explanation-result").length, 0);
  jobState = { status: "succeeded", errorCode: null, resultSummary: resultOf(["경로 P2가 정상 거래 흐름인지 확인합니다."]) };
  await act(async () => app.client.invalidateQueries({ queryKey: ["protocol-explanation-job"] }));
  await wait(() => byData(app, "data-explanation-result").length === 1, "result shown");
  const box = text(card(app).children);
  assert.ok(box.startsWith("LLM 설명"), box);
  assert.ok(!box.includes("설명을 만드는 중입니다…"));
  const chips = byData(app, "data-explanation-path");
  assert.deepEqual(chips.map((n) => n.props["data-explanation-path"]), ["P2", "P1", "P2"], "path ids from paths only (XP1·P9 stay text)");
  assert.deepEqual(chips[0].findAll((n) => n.type === "span" && n.props["data-channel"] !== undefined).map((n) => n.props["data-channel"]), ["CH_GW", "채널_외부", "CH_DB"], "channel chips");
  assert.ok(text(byData(app, "data-explanation-text")[0].children).includes("XP1·P9는 경로 id가 아닙니다."));
  const points = text(byData(app, "data-review-points")[0].children);
  assert.ok(points.startsWith("확인할 점") && points.endsWith("가 정상 거래 흐름인지 확인합니다."), points);
  assert.equal(text(byData(app, "data-explanation-note")[0].children), "코드가 집계한 사실만으로 쓴 설명입니다 · 버전 30 기준");
  assert.ok(!explainButton(app), "no request button over a result");
  await app.cleanup();

  // 같은 대상의 기존 성공 작업(다른 버전 대상 작업은 무시): 요청 없이 바로 보이고, 확인할 점이 없으면 그 칸을 숨긴다.
  jobs = [listItem("66666666-6666-4666-8666-666666666666", "succeeded", { inputSpec: { ...body, target: { ...target, ruleSetRevision: 29 } } }), listItem(EXPLAIN_JOB, "succeeded")];
  jobState = { status: "succeeded", errorCode: null, resultSummary: resultOf([]) };
  posted.length = 0;
  requested.length = 0;
  const reused = await mount(paths.detectionRuleProtocol("IF_SYN_ORDER"));
  await openDrafts(reused);
  await wait(() => byData(reused, "data-explanation-result").length === 1, "existing result");
  assert.equal(calls("GET", "/api/jobs/66666666-6666-4666-8666-666666666666").length, 0, "other target's job not read");
  assert.equal(byData(reused, "data-review-points").length, 0, "no review points → box hidden");
  assert.equal(posted.length, 0, "existing job shown without a request");
  await reused.cleanup();

  // 점검 실패: 코드 + 사유 코드, 문장 없음 → 다시 요청은 새 멱등키(같은 키는 실패 작업을 돌려준다).
  jobs = [listItem(EXPLAIN_JOB, "failed")];
  jobState = { status: "failed", errorCode: "protocol_explanation_check_failed",
    resultSummary: { format: "protocol-explanation-v1", target, interfaceId: "IF_SYN_ORDER", ruleSetRevision: 30, check: { passed: false, failures: ["numbers_subset", "paths_in_facts"], coverage: {} } } };
  const failed = await mount(paths.detectionRuleProtocol("IF_SYN_ORDER"));
  await openDrafts(failed);
  await wait(() => text(card(failed).children).includes("경로 설명 실패 · protocol_explanation_check_failed · numbers_subset · paths_in_facts"), "check failure codes");
  assert.equal(byData(failed, "data-explanation-result").length, 0, "no sentence for a failed check");
  jobState = { status: "queued", errorCode: null, resultSummary: null };
  await act(async () => explainButton(failed).props.onClick());
  await wait(() => posted.length === 1, "retry posted");
  assert.deepEqual(posted[0].body, body);
  assert.notEqual(posted[0].key, firstKey, "retry after a failure uses a new idempotency key");
  await failed.cleanup();
  jobState = { status: "failed", errorCode: "generation_busy", resultSummary: null };
  const busy = await mount(paths.detectionRuleProtocol("IF_SYN_ORDER"));
  await openDrafts(busy);
  await wait(() => text(card(busy).children).includes("경로 설명 실패 · generation_busy"), "plain failure code");
  assert.equal(explainButton(busy).props.disabled, false, "request again after a failure");
  await busy.cleanup();

  // 접수 거절(400): 코드 그대로, 다시 누를 수 있다.
  jobs = [];
  posted.length = 0;
  postReply = () => json({ code: "protocol_explanation_facts_unavailable" }, 400);
  const rejected = await mount(paths.detectionRuleProtocol("IF_SYN_ORDER"));
  await openDrafts(rejected);
  await act(async () => explainButton(rejected).props.onClick());
  await wait(() => text(card(rejected).children).includes("경로 설명 실패 · protocol_explanation_facts_unavailable"), "rejection code");
  assert.equal(explainButton(rejected).props.disabled, false);
  await rejected.cleanup();

  // LLM 꺼짐(#168): 버튼 비활성 + `경로 설명 · <코드>`, 요청 없음.
  llm = { available: false, checkedAt: "2026-11-14T01:00:00.000Z", errorCode: "generation_service_unavailable" };
  posted.length = 0;
  const off = await mount(paths.detectionRuleProtocol("IF_SYN_ORDER"));
  await openDrafts(off);
  await wait(() => byData(off, "data-llm-unavailable").length === 1, "unavailable code");
  assert.equal(text(byData(off, "data-llm-unavailable")[0].children), "경로 설명 · generation_service_unavailable");
  assert.equal(explainButton(off).props.disabled, true, "button disabled while the generation service is off");
  assert.equal(posted.length, 0);
  await off.cleanup();
  llm = { available: true, checkedAt: "2026-11-14T01:00:00.000Z", errorCode: null };

  // 초안에도 운영 버전에도 없는 인터페이스: 설명할 사실이 없어 칸을 그리지 않는다(접수 400과 같은 조건).
  const absent = await mount(paths.detectionRuleProtocol("IF_SYN_NONE"));
  await wait(() => absent.shown().includes("새 항목"), "detail");
  await absent.click("관측 경로로 초안 만들기");
  await wait(() => absent.shown().includes(labels.DRAFT_NOT_OBSERVED), "not observed");
  assert.ok(!card(absent), "no explanation box without facts");
  await absent.cleanup();
}

console.log("protocol settings page tests passed");
