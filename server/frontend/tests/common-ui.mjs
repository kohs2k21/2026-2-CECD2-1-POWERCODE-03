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
console.log(
  "common UI QA passed: notification dedup/replacement/durations/live regions, immediate clear, button/link semantics and inline retry, motion tokens CSS = Motion",
);
