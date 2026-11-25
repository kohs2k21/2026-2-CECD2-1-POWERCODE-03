import assert from "node:assert/strict";
import { createNotificationMonitor, NOTIFICATION_REFRESH_MS, NOTIFICATION_RESUME_MS } from "../src/features/notifications/notificationLive.ts";

// 서버 응답 경계·가짜 시계·구독만 사용한다. 기업 서버나 실제 알림 수신자는 호출하지 않는다.
const stamp = (second) => `2026-11-25T00:00:${String(second).padStart(2, "0")}.000000Z`;
const item = (seq, over = {}) => ({ key: `decision:${seq}`, type: "decision", seq: String(seq), reason: "opened", unread: true, notifiedAt: stamp(1), ...over });
const snapshot = (seq, items = [], over = {}) => ({
  latestSeq: String(seq), latestJobAt: null, readThrough: "0", items,
  settings: { decisionBasis: "interim", minRiskLevel: "info", includeUnrated: true, windowDays: 7, includeJobs: true },
  window: { events: 5000, days: 7, fromSeq: "1", truncated: false, truncatedBy: null },
  total: items.length, unreadCount: items.filter((row) => row.unread).length, nextCursor: null, ...over,
});
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function fixture() {
  let clock = 100_000, timerId = 0;
  const timers = new Map(), subscriptions = [], states = [], signals = [];
  const context = { current: true, visible: true, online: true };
  let value = snapshot(10, [item(10)]), readCount = 0, invalidations = 0;
  let read = () => Promise.resolve(value);
  const monitor = createNotificationMonitor({
    after: "10", current: () => context.current, visible: () => context.visible, online: () => context.online,
    now: () => clock,
    setTimer(call, delay) { const id = ++timerId; timers.set(id, { call, due: clock + delay }); return id; },
    clearTimer(id) { timers.delete(id); },
    read(signal) { readCount++; signals.push(signal); return read(signal); },
    invalidate() { invalidations++; }, publish(state) { states.push(state); },
    subscribe(subscriber) {
      const subscription = { ...subscriber, stopped: false };
      subscriptions.push(subscription);
      return () => { subscription.stopped = true; };
    },
  });
  const advance = async (ms) => {
    const target = clock + ms;
    while (true) {
      const next = [...timers].filter(([, timer]) => timer.due <= target).sort((a, b) => a[1].due - b[1].due)[0];
      if (!next) break;
      clock = next[1].due; timers.delete(next[0]); next[1].call(); await settle();
    }
    clock = target; await settle();
  };
  return {
    monitor, context, subscriptions, signals, timers, states, advance,
    get state() { return states.at(-1); }, get reads() { return readCount; }, get invalidations() { return invalidations; },
    setValue(next) { value = next; }, setRead(next) { read = next; },
    async start() { monitor.start(); subscriptions.at(-1)?.onOpen(); await advance(NOTIFICATION_RESUME_MS); },
  };
}

// 최초 이력은 무음 기준. 이벤트 폭주는 10초 안에 한 번만 조회하며 같은 경계를 재수신해도 재표시하지 않는다.
{
  const f = fixture(); await f.start();
  assert.equal(f.state.banner, null); assert.equal(f.reads, 1); assert.equal(f.state.connection, "connected");
  const firstChecked = f.state.lastCheckedAt;
  f.setValue(snapshot(13, [item(13, { reason: "risk_raised" }), item(12), item(11, { reason: "withdrawn" })]));
  for (let seq = 11; seq <= 13; seq++) f.subscriptions[0].onEvent({}, String(seq));
  await f.advance(NOTIFICATION_REFRESH_MS - 1); assert.equal(f.reads, 1);
  await f.advance(1); assert.equal(f.reads, 2); assert.deepEqual(f.state.banner, { riskRaised: true });
  assert.ok(f.state.lastCheckedAt > firstChecked);
  f.monitor.dismiss(); assert.equal(f.state.banner, null); assert.equal(f.invalidations, 2);
  f.subscriptions[0].onEvent({}, "13"); await f.advance(NOTIFICATION_REFRESH_MS);
  assert.equal(f.state.banner, null, "같은 알림을 다시 받아도 배너는 재표시하지 않는다");
  f.monitor.stop(); assert.equal(f.timers.size, 0); assert.ok(f.subscriptions[0].stopped);
}

// 근거 증가·철회·읽은 항목·처리 완료 프로토콜은 배너에서 제외한다. 서버 설정 변경도 조용히 기준을 갱신한다.
{
  const f = fixture(); await f.start();
  f.setValue(snapshot(15, [item(11, { type: "incident", reason: "grew" }), item(12, { reason: "withdrawn" }),
    item(13, { unread: false }), item(14, { type: "protocol", protocol: { resolution: "kept" } })]));
  f.monitor.refresh(); await settle(); assert.equal(f.state.banner, null);
  f.setValue(snapshot(16, [item(16, { type: "protocol", protocol: { resolution: null } })]));
  f.monitor.refresh(); await settle(); assert.deepEqual(f.state.banner, { riskRaised: false });
  f.setValue(snapshot(17, [item(17)], { settings: { ...snapshot(0).settings, windowDays: 30 } }));
  f.monitor.refresh(); await settle(); assert.equal(f.state.banner, null);
  f.setValue(snapshot(18, [item(18)])); f.monitor.reset(); await settle();
  assert.equal(f.state.banner, null, "명시적 설정 저장 뒤 첫 조회도 기준으로만 사용한다");
  f.setValue(snapshot(19, [item(19)])); f.monitor.refresh(); await settle(); assert.ok(f.state.banner);
  f.setValue(snapshot(19)); f.monitor.refresh(); await settle(); assert.equal(f.state.banner, null);
  f.monitor.stop();
}

// 작업 종료 시각은 판정 순번과 독립이다. 새 작업이 있어도 판정 순번이 같을 수 있다.
{
  const f = fixture(); f.setValue(snapshot(10, [], { latestJobAt: stamp(1) })); await f.start();
  f.setValue(snapshot(10, [item(0, { type: "job", reason: "finished", notifiedAt: stamp(2) })], { latestJobAt: stamp(2) }));
  f.monitor.refresh(); await settle(); assert.ok(f.state.banner);
  f.monitor.dismiss(); f.monitor.refresh(); await settle(); assert.equal(f.state.banner, null);
  f.setValue(snapshot(9, [item(9)])); f.monitor.refresh(); await settle();
  assert.equal(f.state.refreshFailed, true, "되돌아간 응답 경계는 채택하지 않는다");
  f.setValue(snapshot(10, [], { latestJobAt: stamp(2) })); f.monitor.refresh(); await settle();
  assert.equal(f.state.refreshFailed, false); assert.equal(f.state.banner, null);
  f.monitor.stop();
}

// 숨김·오프라인 중 읽기와 구독을 중단하고 복귀 때만 확인한다. 취소를 무시하는 늦은 응답도 버린다.
{
  const f = fixture(); await f.start(); const initial = f.state.lastCheckedAt;
  const delayed = deferred(); f.setRead(() => delayed.promise); f.monitor.refresh(); await settle();
  const oldStream = f.subscriptions[0];
  f.context.visible = false; f.monitor.resume(); assert.equal(f.state.connection, "paused");
  assert.ok(f.signals.at(-1).aborted); assert.ok(oldStream.stopped);
  delayed.resolve(snapshot(20, [item(20)])); await settle();
  assert.equal(f.state.lastCheckedAt, initial); assert.equal(f.state.banner, null);
  oldStream.onOpen(); oldStream.onEvent({}, "100"); assert.equal(f.timers.size, 0);
  f.context.online = false; f.monitor.resume(); assert.equal(f.state.connection, "offline");
  f.context.visible = true; f.monitor.resume(); await f.advance(1000); assert.equal(f.reads, 2);
  f.setRead(() => Promise.resolve(snapshot(20, [item(20)])));
  f.context.online = true; f.monitor.resume(); f.monitor.resume(); f.subscriptions.at(-1).onOpen();
  assert.equal(f.subscriptions.at(-1).after, "10", "취소된 구독의 커서를 채택하지 않는다");
  await f.advance(NOTIFICATION_RESUME_MS); assert.equal(f.reads, 3); assert.ok(f.state.banner);
  f.monitor.stop();
}

// 새 이벤트 없이도 SSE가 다시 열리면 재조회한다. 오류는 마지막 성공 시각을 유지하며 다음 성공으로 복구한다.
{
  const f = fixture(); await f.start(); const initial = f.state.lastCheckedAt;
  f.subscriptions[0].onDisconnect("민감한 외부 오류"); assert.equal(f.state.connection, "reconnecting");
  f.setRead(() => Promise.reject(new Error("외부 주소/인증 정보")));
  f.subscriptions[0].onOpen(); await f.advance(NOTIFICATION_RESUME_MS);
  assert.equal(f.state.refreshFailed, true); assert.equal(f.state.lastCheckedAt, initial);
  assert.ok(!JSON.stringify(f.state).includes("인증 정보"));
  f.setRead(() => Promise.resolve(snapshot(11, [item(11)])));
  f.monitor.resume(); await f.advance(NOTIFICATION_RESUME_MS);
  assert.equal(f.state.refreshFailed, false); assert.ok(f.state.banner); f.monitor.stop();
}

// 동시에 여러 번 요청해도 읽기는 하나씩 진행한다. 계정 변경·종료 이후 응답·이벤트는 상태와 캐시를 바꾸지 않는다.
{
  const f = fixture(); await f.start();
  const delayed = deferred(); f.setRead(() => delayed.promise);
  f.monitor.refresh(); f.monitor.refresh(); f.monitor.refresh(); await settle(); assert.equal(f.reads, 2);
  delayed.resolve(snapshot(11, [item(11)])); await settle();
  f.setRead(() => Promise.resolve(snapshot(12, [item(12)])));
  await f.advance(NOTIFICATION_RESUME_MS); assert.equal(f.reads, 3);
  const stale = deferred(); f.setRead(() => stale.promise); f.monitor.refresh(); await settle();
  const publications = f.states.length, invalidations = f.invalidations;
  f.context.current = false; stale.resolve(snapshot(100, [item(100)])); await settle();
  f.subscriptions[0].onOpen(); f.subscriptions[0].onEvent({}, "101"); f.monitor.dismiss();
  assert.equal(f.states.length, publications); assert.equal(f.invalidations, invalidations);
  f.monitor.stop(); assert.equal(f.timers.size, 0); assert.ok(f.signals.at(-1).aborted === false);
}
console.log("notification live monitor QA passed: baseline, filtered banners, replay, settings, job boundary, debounce, pause/resume, offline, reconnect, errors, stale responses, session and cleanup");
