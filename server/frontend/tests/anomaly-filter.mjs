import assert from "node:assert/strict";
import React, { useState } from "react";
import { act, create } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { useAnalysisWorkspace } from "../src/features/analysis/hooks/useAnalysisWorkspace.tsx";
import { AnalysisFilterDialog } from "../src/features/analysis/components/AnalysisFilterDialog.tsx";
globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.window = { localStorage: { getItem: () => null, setItem() {} } };
const events = ["Critical", "Warning", "Info"].map((severity, index) => ({
  schemaVersion: 1,
  eventId: `event-${index}`,
  provenance: "backend-ingest",
  processName: "delay",
  responseCode: "R1",
  anomalyScore: 0.5,
  processTimeMs: 50,
  riskScore: 40,
  riskLevel: 2,
  severity,
}));
let workspace;
let dialog;
const Probe = () => {
  workspace = useAnalysisWorkspace(events);
  const [isOpen, onOpenChange] = useState(true);
  dialog = AnalysisFilterDialog({
    isOpen,
    onOpenChange,
    activeCategory: workspace.activeCategory,
    onCategoryChange: workspace.handleCategoryChange,
  });
  return null;
};
const router = createMemoryRouter(
  [{ path: "/analysis", element: React.createElement(Probe) }],
  { initialEntries: ["/analysis?category=Warning&q=delay&event=event-0"] },
);
let renderer;
await act(async () => {
  renderer = create(React.createElement(RouterProvider, { router }));
});
const descendants = (node) =>
  Array.isArray(node)
    ? node.flatMap(descendants)
    : React.isValidElement(node)
      ? [node, ...descendants(node.props.children)]
      : [];
const options = () =>
  descendants(dialog.props.children).filter(
    (node) => typeof node.props["aria-pressed"] === "boolean",
  );
assert.equal(options().length, 4);
assert.deepEqual(
  options()
    .filter((node) => node.props["aria-pressed"])
    .map((node) => node.props.children),
  ["주의"],
);
for (const [category, label, count] of [
  ["Critical", "위험", 1],
  ["Warning", "주의", 1],
  ["Info", "참고", 1],
  ["All", "전체", 3],
]) {
  await act(async () => dialog.props.onOpenChange(true));
  const button = options().find((node) => node.props.children === label);
  assert.equal(
    button.props.type,
    "button",
    "native button supports Enter and Space without custom key handlers",
  );
  await act(async () => button.props.onClick());
  assert.equal(workspace.activeCategory, category);
  assert.equal(workspace.filteredDetails.length, count);
  const params = new URLSearchParams(router.state.location.search);
  assert.equal(
    params.get("q"),
    "delay",
    "filter selection preserves search text",
  );
  assert.equal(params.get("category"), category);
  assert.equal(
    params.has("event"),
    false,
    "severity selection leaves the old detail",
  );
  assert.equal(
    dialog.props.isOpen,
    false,
    "selecting severity closes the modal",
  );
  assert.deepEqual(
    options()
      .filter((node) => node.props["aria-pressed"])
      .map((node) => node.props.children),
    [label],
  );
}
await act(async () => router.navigate(-1));
assert.equal(workspace.activeCategory, "Info");
assert.equal(workspace.query, "delay");
await act(async () => renderer.unmount());
router.dispose();
console.log(
  "anomaly filter QA passed: all/severity actions, URL and search preservation, detail exit, modal close, selected state, history and native keyboard buttons",
);
