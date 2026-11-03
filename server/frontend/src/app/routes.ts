import type { UserRole } from "@/types/app";

/**
 * 화면 경로 단일 기준(navigation.md). 와이어프레임 #93 구조:
 * 상단 메뉴 운영 현황 / 이상 분석 / 탐지 설정(관리자), 탐지 설정 사이드바 4메뉴.
 */
export const paths = {
  login: "/login",
  operations: "/operations",
  analysis: "/analysis",
  analysisDecision: (decisionId: string) => `/analysis/d/${encodeURIComponent(decisionId)}`,
  settings: "/settings",
  detection: "/detection",
  detectionOperations: "/detection/operations",
  detectionJob: (jobId: string) => `/detection/operations/jobs/${encodeURIComponent(jobId)}`,
  detectionReplace: "/detection/operations/replace",
  detectionRules: "/detection/rules",
  detectionModels: "/detection/models",
  detectionModel: (versionId: string) => `/detection/models/${encodeURIComponent(versionId)}`,
  detectionEvaluations: "/detection/evaluations",
  detectionEvaluation: (evaluationId: string) => `/detection/evaluations/${encodeURIComponent(evaluationId)}`,
} as const;

export type TopNavId = "operations" | "analysis" | "detection";
export const topNavItems: { id: TopNavId; label: string; to: string; adminOnly?: boolean }[] = [
  { id: "operations", label: "운영 현황", to: paths.operations },
  { id: "analysis", label: "이상 분석", to: paths.analysis },
  { id: "detection", label: "탐지 설정", to: paths.detectionOperations, adminOnly: true },
];
export const visibleTopNavItems = (role: UserRole) =>
  topNavItems.filter((item) => role === "admin" || !item.adminOnly);

export const activeTopNav = (pathname: string): TopNavId | "settings" | null =>
  pathname === paths.settings
    ? "settings"
    : pathname.startsWith(paths.analysis)
      ? "analysis"
      : pathname.startsWith(paths.detection)
        ? "detection"
        : pathname.startsWith(paths.operations)
          ? "operations"
          : null;

export type DetectionMenuId = "operations" | "rules" | "models" | "evaluations";
export const detectionMenu: { id: DetectionMenuId; label: string; to: string }[] = [
  { id: "operations", label: "운영 설정", to: paths.detectionOperations },
  { id: "rules", label: "룰 설정", to: paths.detectionRules },
  { id: "models", label: "머신러닝 모델", to: paths.detectionModels },
  { id: "evaluations", label: "평가 결과", to: paths.detectionEvaluations },
];
export const activeDetectionMenu = (pathname: string): DetectionMenuId | null =>
  detectionMenu.find((item) => pathname === item.to || pathname.startsWith(item.to + "/"))?.id ?? null;

/** URL 선택값(탭·단계·항목). 첫 값이 기본값. */
export const urlOptions = {
  operationsTab: ["overview", "collect"],
  settingsSection: ["notifications", "integrations", "display", "profile"],
  modelsTab: ["active", "all"],
  evaluationsTab: ["current", "all"],
} as const;
/** 작업 영역 단계 수(navigation.md). 단계 화면은 각 하위 Issue에서 붙인다. */
export const workStepCount = { train: 3, compare: 3, replace: 3 } as const;

/** 미지정은 기본값(주소 그대로), 잘못된 값은 기본값으로 replace 정규화 대상. */
export const resolveOption = <T extends string>(
  value: string | null,
  allowed: readonly T[],
): { value: T; invalid: boolean } =>
  value === null
    ? { value: allowed[0], invalid: false }
    : allowed.includes(value as T)
      ? { value: value as T, invalid: false }
      : { value: allowed[0], invalid: true };

/** 단계(1..max): 미지정은 1, 범위 밖·숫자 아님은 가까운 유효 단계로 replace 정규화 대상. */
export const resolveStep = (value: string | null, max: number): { value: number; invalid: boolean } => {
  if (value === null) return { value: 1, invalid: false };
  const parsed = Number(value);
  if (!/^\d+$/.test(value) || !Number.isInteger(parsed)) return { value: 1, invalid: true };
  const clamped = Math.min(max, Math.max(1, parsed));
  return { value: clamped, invalid: clamped !== parsed };
};

/** 현재 쿼리를 보존하면서 한 값만 바꾼 주소(기타 쿼리 보존 규칙). */
export const withSearchParam = (search: string | URLSearchParams, key: string, value: string | null) => {
  const next = new URLSearchParams(search);
  if (value === null) next.delete(key);
  else next.set(key, value);
  const text = next.toString();
  return text ? `?${text}` : "";
};

/**
 * 옛 주소 → 새 주소(replace). 폐지 화면: /notifications 페이지, /detection/pipeline,
 * 탐지 관리 실험·운영 그룹(create·evaluation·versions·collection), 저장된 탐지 판정(/analysis/process-decisions → 이상 분석, #105).
 */
export const legacyRedirect = (pathname: string, search: string): string | null => {
  const params = new URLSearchParams(search);
  switch (pathname.replace(/\/+$/, "") || "/") {
    case "/notifications":
    case "/analysis/process-decisions":
      return paths.analysis;
    case "/detection":
    case "/detection/pipeline":
    case "/detection/versions":
      return paths.detectionOperations;
    case "/detection/create":
      return params.get("tab") === "rules" ? paths.detectionRules : paths.detectionModels;
    case "/detection/evaluation":
      return paths.detectionEvaluations;
    case "/detection/collection":
      return `${paths.operations}?tab=collect`;
    default:
      return null;
  }
};

const appPathPatterns: RegExp[] = [
  /^\/operations$/,
  /^\/analysis$/,
  /^\/analysis\/d\/[^/]+$/,
  /^\/settings$/,
  /^\/detection\/operations$/,
  /^\/detection\/operations\/replace$/,
  /^\/detection\/operations\/jobs\/[^/]+$/,
  /^\/detection\/rules$/,
  /^\/detection\/models$/,
  /^\/detection\/models\/[^/]+$/,
  /^\/detection\/evaluations$/,
  /^\/detection\/evaluations\/[^/]+$/,
];
export const isAppPath = (pathname: string) => appPathPatterns.some((pattern) => pattern.test(pathname));

/** 로그인·설정 복귀 주소: 앱 내부 허용 경로만(외부 URL·프로토콜 상대 URL 금지). 옛 주소는 새 주소로 바꿔 허용. */
export const safeReturnPath = (value: unknown, fallback: string = paths.operations) => {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return fallback;
  try {
    const parsed = new URL(value, "https://local.invalid");
    if (parsed.origin !== "https://local.invalid") return fallback;
    const legacy = legacyRedirect(parsed.pathname, parsed.search);
    if (legacy) return legacy;
    return isAppPath(parsed.pathname) ? parsed.pathname + parsed.search + parsed.hash : fallback;
  } catch {
    return fallback;
  }
};
export const settingsReturnPath = (value: unknown) => {
  const path = safeReturnPath(value);
  return new URL(path, "https://local.invalid").pathname === paths.settings ? paths.operations : path;
};
export const loginReturnPath = (value: unknown, role: UserRole) => {
  const path = safeReturnPath(value);
  return role !== "admin" && new URL(path, "https://local.invalid").pathname.startsWith(paths.detection)
    ? paths.operations
    : path;
};
