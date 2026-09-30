import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { ProfileSettings } from "../src/features/settings/sections/ProfileSettings.tsx";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.React = React;

const adminUser = {
  id: "admin-user-1",
  email: "admin@example.test",
  userType: "admin",
  createdAt: "2026-09-20T00:00:00.000Z",
};
const regularUser = {
  id: "regular-user-2",
  email: "user@example.test",
  userType: "user",
  createdAt: "2026-09-21T00:00:00.000Z",
};

const getText = (node) => {
  if (node === null || node === undefined) return [];
  if (typeof node === "string" || typeof node === "number") return [String(node)];
  if (Array.isArray(node)) return node.flatMap(getText);
  return getText(node.children);
};

let renderer;
await act(async () => {
  renderer = create(React.createElement(ProfileSettings, { currentUser: adminUser }));
});

const findAdminMenuSwitch = () =>
  renderer.root.find(
    (node) =>
      node.props.role === "switch" &&
      typeof node.props["aria-label"] === "string" &&
      node.props["aria-label"].startsWith("관리자 메뉴 "),
  );

let adminMenuSwitch = findAdminMenuSwitch();
assert.equal(adminMenuSwitch.props.disabled, true);
assert.equal(adminMenuSwitch.props["aria-checked"], true);
assert.ok(getText(renderer.toJSON()).includes(adminUser.email));
assert.ok(getText(renderer.toJSON()).includes("관리자"));

await act(async () => {
  renderer.update(React.createElement(ProfileSettings, { currentUser: regularUser }));
});

adminMenuSwitch = findAdminMenuSwitch();
assert.equal(adminMenuSwitch.props.disabled, true);
assert.equal(adminMenuSwitch.props["aria-checked"], false);
assert.equal(adminMenuSwitch.props["aria-label"], "관리자 메뉴 꺼짐");
const regularProfileText = getText(renderer.toJSON());
assert.ok(regularProfileText.includes(regularUser.email));
assert.ok(regularProfileText.includes("일반 사용자"));
assert.ok(!regularProfileText.includes(adminUser.email));

await act(async () => renderer.unmount());

console.log("settings profile QA passed: disabled role indicator follows current AuthUser across prop updates");
