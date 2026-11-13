import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { versionLabel } from "@/features/detection/models/VersionList";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { modelsText } from "./ActiveSetDialogs";
import type { ActiveModel, ActiveSetChanged } from "./operationsApi";

/** 운영 모델 변경 결과 안내(와이어프레임 작업 상세의 결과 문구를 운영 설정 화면 안내로). 운영 설정·모델 교체가 함께 쓴다. */
export type ActiveSetBanner =
  | { kind: "changed"; lines: string[] }
  | { kind: "failed"; text: string; code: string };
export type ActiveSetOperation = "replace" | "retire" | "restore";

/** 응답의 변경 목록으로 문구를 만든다. 이름은 변경 전 운영 모델과 응답의 운영 모델에서 찾는다. */
export const changedBanner = (result: ActiveSetChanged, before: ActiveModel[]): ActiveSetBanner => {
  const known = [...before, ...result.models];
  const name = (id: string) => versionLabel({ id, displayName: known.find((model) => model.configVersionId === id)?.displayName ?? null });
  const lines = result.changes.map((change) => change.type === "retired"
    ? `운영 모델에서 ${name(change.configVersionId)} 제외됨 · 이후 판정부터 사용 안 함${result.models.length ? "" : " · 현재 룰만으로 판정"}`
    : `운영 모델 ${name(change.configVersionId)}로 전환됨 · 이후 판정부터 ${name(change.configVersionId)} 사용`);
  return { kind: "changed", lines };
};
export const failedBanner = (_operation: ActiveSetOperation, error: unknown, before: ActiveModel[]): ActiveSetBanner => {
  // 와이어프레임 작업 실패 문구: <사유> · 운영 모델 <현재> 유지. 사유는 서버 코드 그대로.
  const code = errorCode(error);
  return { kind: "failed", code, text: `${code} · 운영 모델 ${before.length ? modelsText(before) : "없음"} 유지` };
};

export const ActiveSetBannerView = ({ banner }: { banner: ActiveSetBanner }) =>
  banner.kind === "changed" ? (
    <Alert role="status" data-banner="changed">
      <AlertDescription className="grid gap-1">
        {banner.lines.map((line) => <span key={line} className="flex flex-wrap items-center gap-2"><Badge variant="success">적용됨</Badge>{line}</span>)}
      </AlertDescription>
    </Alert>
  ) : (
    <Alert variant="destructive" role="alert" data-banner="failed" data-code={banner.code}>
      <AlertDescription>{banner.text}</AlertDescription>
    </Alert>
  );
