import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { versionLabel } from "@/features/detection/models/VersionList";
import { cn } from "@/lib/utils";
import { MAX_COMPARE } from "./compareDraft";
import type { CompareVersion } from "./evaluationsApi";

/** 2단계 비교 모델. 현재 운영 버전은 고르는 값이 아니다: 고른 후보와 같은 종류의 운영 버전을 서버가 자동으로 넣는다. */
export const TargetStep = ({ versions, picked, included, setLabel, onToggle }: {
  versions: CompareVersion[];
  picked: CompareVersion[];
  included: CompareVersion[];
  setLabel: string;
  onToggle: (id: string, checked: boolean) => void;
}) => {
  const total = picked.length + included.length;
  const over = total > MAX_COMPARE;
  return (
    <div className="grid gap-3">
      <h3 className="text-sm font-medium">비교 모델 · 현재 운영 포함 최대 {MAX_COMPARE}개</h3>
      <p className="text-sm">평가 세트: <strong className="font-semibold">{setLabel}</strong></p>
      <div className="grid gap-2" role="group" aria-label="모델 버전 목록">
        {versions.filter((version) => version.active).map((version) => {
          const inside = included.includes(version);
          return (
            <Label key={version.id} className="font-normal">
              <Checkbox checked={inside} disabled aria-label={`${versionLabel(version)} 현재 운영`} />
              {versionLabel(version)} · {version.modelName} · 현재 운영{inside ? " · 항상 포함" : ""}
            </Label>
          );
        })}
        {versions.filter((version) => !version.active).map((version) => (
          <Label key={version.id} className={cn("font-normal", !version.executable && "text-muted-foreground")}>
            <Checkbox
              checked={picked.includes(version)}
              disabled={!version.executable}
              aria-label={`${versionLabel(version)} 선택`}
              onCheckedChange={(checked) => onToggle(version.id, checked === true)}
            />
            {versionLabel(version)} · {version.modelName}{version.latestEvaluation ? " · 평가 이력 있음" : ""}
          </Label>
        ))}
      </div>
      <p className={cn("text-sm", over ? "text-warning-text" : "text-muted-foreground")}>선택 {total} / {MAX_COMPARE}</p>
      {over && <p role="alert" className="text-sm text-warning-text">현재 운영 포함 최대 {MAX_COMPARE}개까지 비교합니다.</p>}
    </div>
  );
};
