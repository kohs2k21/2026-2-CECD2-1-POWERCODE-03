import type { UserRole, ViewId } from "../types/app";
export const viewPaths: Record<ViewId, string> = {
  home: "/operations",
  analysis: "/analysis",
  settings: "/settings",
  model: "/detection/create?tab=data-features",
  system: "/detection/collection",
};
export const createTabs = [
  { id: "data-features", label: "데이터·피처" },
  { id: "training", label: "모델 학습" },
  { id: "rules", label: "룰 설정" },
] as const;
export const detectionPages = [
  { path: "/detection/create", label: "모델·룰 만들기", group: "실험" },
  { path: "/detection/evaluation", label: "성능 평가·비교", group: "실험" },
  { path: "/detection/versions", label: "운영 버전 관리", group: "운영" },
  { path: "/detection/collection", label: "데이터 수집 현황", group: "운영" },
] as const;
const knownPaths: string[] = [
  "/operations",
  "/analysis",
  "/settings",
  ...detectionPages.map((page) => page.path),
];
export const safeReturnPath = (value: unknown, fallback = "/operations") => {
  if (
    typeof value !== "string" ||
    !value.startsWith("/") ||
    value.startsWith("//")
  )
    return fallback;
  try {
    const parsed = new URL(value, "https://local.invalid");
    return parsed.origin === "https://local.invalid" &&
      knownPaths.includes(parsed.pathname)
      ? parsed.pathname + parsed.search + parsed.hash
      : fallback;
  } catch {
    return fallback;
  }
};
export const settingsReturnPath = (value: unknown) => {
  const path = safeReturnPath(value);
  return new URL(path, "https://local.invalid").pathname === "/settings"
    ? "/operations"
    : path;
};
export const loginReturnPath = (value: unknown, role: UserRole) => {
  const path = safeReturnPath(value);
  return role !== "admin" &&
    new URL(path, "https://local.invalid").pathname.startsWith("/detection")
    ? "/operations"
    : path;
};
