import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { useToasterStore } from "react-hot-toast";
import { notify } from "../src/lib/notify.ts";
import { Button } from "../src/components/ui/button.tsx";
import { ErrorState } from "../src/components/ui/feedback.tsx";

globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let notifications;
const Probe = () => {
  notifications = useToasterStore().toasts;
  return null;
};
let renderer;
await act(async () => {
  renderer = create(React.createElement(Probe));
});
await act(async () => {
  notify.error("조회 실패", "fetch:items");
  notify.error("조회 실패", "fetch:items");
});
assert.equal(
  notifications.length,
  1,
  "same cause must not stack duplicate toasts",
);
assert.equal(notifications[0].duration, 6000);
assert.deepEqual(notifications[0].ariaProps, {
  role: "alert",
  "aria-live": "assertive",
});
await act(async () => notify.loading("저장 중", "save:settings"));
assert.equal(
  notifications.find((item) => item.id === "save:settings").duration,
  Infinity,
);
await act(async () => notify.success("저장 완료", "save:settings"));
const completed = notifications.filter((item) => item.id === "save:settings");
assert.equal(
  completed.length,
  1,
  "completion must replace its loading notification",
);
assert.equal(completed[0].type, "success");
assert.equal(completed[0].duration, 2400);
assert.deepEqual(completed[0].ariaProps, {
  role: "status",
  "aria-live": "polite",
});
await act(async () => notify.dismiss("save:settings"));
assert.equal(
  notifications.find((item) => item.id === "save:settings").visible,
  false,
);
await act(async () => notify.clear());
assert.deepEqual(notifications, []);
await act(async () => renderer.unmount());

let retried = 0;
await act(async () => {
  renderer = create(
    React.createElement(
      "form",
      null,
      React.createElement(Button, null, "작업"),
      React.createElement(
        Button,
        { asChild: true, variant: "outline" },
        React.createElement("a", { href: "/analysis?q=delay" }, "이상 분석"),
      ),
      React.createElement(ErrorState, {
        title: "조회 실패",
        onRetry: () => retried++,
      }),
    ),
  );
});
assert.equal(
  renderer.root.findAllByType("button")[0].props.type,
  "button",
  "default action must not submit enclosing form",
);
const link = renderer.root.findByType("a");
assert.equal(link.props.href, "/analysis?q=delay");
assert.equal(link.props.type, undefined, "asChild navigation remains a link");
const retry = renderer.root
  .findAllByType("button")
  .find((button) => button.children.includes("다시 시도"));
await act(async () => retry.props.onClick());
assert.equal(retried, 1, "inline error must expose its actual retry action");
await act(async () => renderer.unmount());

// 움직임 토큰(ui.md "움직임"): CSS(lib/tokens.css)와 Motion(lib/motion.ts) 값이 같아야 한다.
{
  const { readFileSync } = await import("node:fs");
  const { MOTION_MS, MOTION_EASE, motionClasses, motionTransition } = await import("../src/lib/motion.ts");
  const css = readFileSync(new URL("../src/lib/tokens.css", import.meta.url), "utf8");
  const token = (name) => css.match(new RegExp(`--${name}:\\s*([^;]+);`))?.[1].trim();
  for (const [key, ms] of Object.entries(MOTION_MS)) assert.equal(token(`motion-duration-${key}`), `${ms}ms`, `duration ${key}`);
  for (const [key, curve] of Object.entries(MOTION_EASE)) assert.equal(token(`motion-ease-${key}`), `cubic-bezier(${curve.join(", ")})`, `ease ${key}`);
  assert.deepEqual(motionTransition("base"), { duration: 0.18, ease: [0.2, 0, 0, 1] });
  for (const [key, classes] of Object.entries(motionClasses)) {
    assert.ok(!/undefined|\$\{/.test(classes), `${key} classes are literal`);
    assert.ok(/duration-\(--motion-duration-(fast|base|slow)\)/.test(classes), `${key} uses a duration token`);
  }
}
// 메뉴·스크롤·로딩 움직임 값(ui.md "움직임", 사용자 11/28): 스프링·스크롤 길이·곡선·로딩 지연.
{
  const { MOTION_SPRING, SCROLL_MS, scrollDuration, cubicBezier, PENDING_MS, GRID_SETTLE_MS } = await import("../src/lib/motion.ts");
  assert.deepEqual(MOTION_SPRING, { grow: { stiffness: 600, damping: 34 }, stretchLead: { stiffness: 760, damping: 48 }, stretchTrail: { stiffness: 240, damping: 30 } });
  assert.deepEqual([scrollDuration(0), scrollDuration(300), scrollDuration(1500), scrollDuration(-30000)], [SCROLL_MS.min, 316, 509, SCROLL_MS.max], "distance-based scroll length, capped");
  const standard = cubicBezier(0.4, 0, 0.2, 1);
  assert.deepEqual([standard(0), standard(1)], [0, 1]);
  const samples = [0.1, 0.25, 0.5, 0.75, 0.9].map(standard);
  assert.ok(samples.every((value, index) => index === 0 || value > samples[index - 1]), "monotonic");
  assert.ok(samples[0] < 0.1 && samples[4] > 0.9 && Math.abs(samples[2] - 0.776) < 0.01, "slow start, slow end (cubic-bezier(0.4, 0, 0.2, 1))");
  assert.deepEqual(PENDING_MS, { delay: 250, min: 350 });
  assert.ok(GRID_SETTLE_MS > 200, "grid motion outlasts the library's 200ms transition");
}

// 조회 대기 표시(components/ui/pending.tsx): 짧은 대기는 표시 없음, 길면 delay 뒤 표시·최소 min 유지. 조건이 바뀌면 뼈대, 같은 조건의 쪽 넘김이면 흐리게.
{
  const { useListPending, PendingRegion } = await import("../src/components/ui/pending.tsx");
  const { PENDING_MS } = await import("../src/lib/motion.ts");
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let state;
  let set;
  const Probe = () => {
    const [input, setInput] = React.useState({ pending: false, condition: "a", page: "1" });
    set = setInput;
    state = useListPending(input);
    return React.createElement(PendingRegion, { show: state.show, kind: state.kind, label: "목록" }, "rows");
  };
  let view;
  await act(async () => {
    view = create(React.createElement(Probe));
  });
  // 짧은 대기(delay 전 응답): 표시 없음
  await act(async () => set({ pending: true, condition: "b", page: "1" }));
  await act(async () => sleep(PENDING_MS.delay / 2));
  await act(async () => set({ pending: false, condition: "b", page: "1" }));
  await act(async () => sleep(PENDING_MS.delay));
  assert.equal(state.show, false, "a short wait shows nothing");
  // 조건 변경 → 뼈대, delay 뒤 표시
  await act(async () => set({ pending: true, condition: "c", page: "1" }));
  await act(async () => sleep(PENDING_MS.delay + 50));
  assert.deepEqual([state.show, state.kind], [true, "skeleton"]);
  assert.equal(view.root.findAll((node) => node.type === "section" && node.props["data-pending"] === "skeleton").length, 1, "skeleton rows replace the old rows");
  assert.ok(view.root.findAll((node) => node.props?.role === "status").some((node) => node.children.includes("불러오는 중...")), "existing loading text for screen readers");
  // 응답이 와도 최소 min 동안 유지(종류 그대로)
  await act(async () => set({ pending: false, condition: "c", page: "1" }));
  assert.deepEqual([state.show, state.kind], [true, "skeleton"], "kept for the minimum time");
  await act(async () => sleep(PENDING_MS.min + 50));
  assert.equal(state.show, false);
  // 같은 조건 쪽 넘김 → 이전 행 흐리게
  await act(async () => set({ pending: true, condition: "c", page: "2" }));
  await act(async () => sleep(PENDING_MS.delay + 50));
  assert.deepEqual([state.show, state.kind], [true, "dim"]);
  const dimmed = view.root.findAll((node) => node.type === "div" && String(node.props.className).includes("opacity-50"));
  assert.ok(dimmed.length === 1 && dimmed[0].children.includes("rows"), "old rows stay, dimmed");
  await act(async () => view.unmount());
}

// 카드 머리(SectionCard): 설명 없이 오른쪽 명령이 있으면 한 줄(빈 둘째 줄 없음)·세로 가운데·최소 28px(이전 머리 높이), 설명이 있으면 두 줄 그대로.
{
  const { SectionCard } = await import("../src/components/layout/SectionCard.tsx");
  const headerOf = async (props) => {
    let view;
    await act(async () => {
      view = create(React.createElement(SectionCard, { title: "제목", ...props }, "본문"));
    });
    const header = view.root.findAll((node) => node.type === "div" && node.props["data-slot"] === "card-header")[0].props.className.split(" ");
    await act(async () => view.unmount());
    return header;
  };
  const withAction = await headerOf({ action: React.createElement("button", null, "버튼") });
  assert.ok(["grid-rows-[auto]", "min-h-7", "items-center"].every((name) => withAction.includes(name)) && !withAction.includes("grid-rows-[auto_auto]"), withAction.join(" "));
  const withDescription = await headerOf({ action: React.createElement("button", null, "버튼"), description: "설명" });
  assert.ok(withDescription.includes("grid-rows-[auto_auto]") && !withDescription.includes("min-h-7"), "two rows with a description");
  const plain = await headerOf({});
  assert.ok(plain.includes("grid-rows-[auto_auto]") && !plain.includes("min-h-7"), "no action: unchanged");
}

// 화면 전환(PageFade): DOM이 없는 환경은 효과 없이 바로 바꾸고, 전환 대기 중인 처리도 바로 실행한다.
{
  const { PageFade, afterPageSwap } = await import("../src/components/layout/PageFade.tsx");
  const { MemoryRouter } = await import("react-router-dom");
  const inRouter = (key, child) => React.createElement(MemoryRouter, null, React.createElement(PageFade, { transitionKey: key }, child));
  let view;
  await act(async () => {
    view = create(inRouter("/a", "A"));
  });
  await act(async () => view.update(inRouter("/b", "B")));
  assert.equal(view.toJSON(), "B");
  let ran = 0;
  afterPageSwap(() => (ran += 1));
  assert.equal(ran, 1, "no transition pending: runs at once");
  await act(async () => view.unmount());
}

console.log(
  "common UI QA passed: notification dedup/replacement/durations/live regions, immediate clear, button/link semantics and inline retry, motion tokens CSS = Motion, spring/scroll/pending values, delayed pending (short wait hidden, skeleton vs dim, minimum time), page fade without DOM",
);
