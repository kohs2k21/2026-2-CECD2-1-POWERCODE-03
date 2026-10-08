import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { useAnalysisWorkspace } from "../src/features/analysis/hooks/useAnalysisWorkspace.tsx";
import { settingsReturnPath } from "../src/app/routePaths.ts";

globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.window = { localStorage: { getItem: () => null, setItem() {} } };
let workspace;
const events = [];
const Probe = () => {
  workspace = useAnalysisWorkspace(events);
  return null;
};
const routes = [
  { path: "/analysis", element: React.createElement(Probe) },
  { path: "/settings", element: React.createElement("p", null, "settings") },
];
let router = createMemoryRouter(routes, {
  initialEntries: ["/analysis?category=Critical&q=delay"],
});
let renderer;
await act(async () => {
  renderer = create(React.createElement(RouterProvider, { router }));
});
assert.equal(workspace.activeCategory, "Critical");
assert.equal(workspace.query, "delay");
await act(async () => workspace.handleCategoryChange("Warning"));
assert.equal(workspace.activeCategory, "Warning");
await act(async () => router.navigate(-1));
assert.equal(workspace.activeCategory, "Critical");
await act(async () => router.navigate(1));
assert.equal(workspace.activeCategory, "Warning");
await act(async () => workspace.setQuery("timeout"));
await act(async () => workspace.setActiveDetailId("received-event"));
const saved = router.state.location.pathname + router.state.location.search;
await act(async () =>
  router.navigate("/settings?returnTo=" + encodeURIComponent(saved)),
);
const returnTo = new URLSearchParams(router.state.location.search).get(
  "returnTo",
);
await act(async () => router.navigate(settingsReturnPath(returnTo)));
assert.equal(workspace.activeCategory, "Warning");
assert.equal(workspace.query, "timeout");
assert.equal(
  new URLSearchParams(router.state.location.search).get("event"),
  "received-event",
);
await act(async () => renderer.unmount());
router.dispose();
// New router instance simulates refresh; state comes from URL, not an old component instance.
router = createMemoryRouter(routes, { initialEntries: [saved] });
await act(async () => {
  renderer = create(React.createElement(RouterProvider, { router }));
});
assert.equal(workspace.activeCategory, "Warning");
assert.equal(workspace.query, "timeout");
assert.equal(workspace.activeRealtimeEvent, null);
await act(async () => renderer.unmount());
router.dispose();
console.log(
  "analysis URL QA passed: category history, search/detail URL, settings return and refresh restore without invented historical event",
);
