import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/** 룰 세트 저장 결과 안내(rules-ui.md). 룰 설정·프로토콜 설정·규칙 추천 승인이 같은 문구를 쓴다. */
export type Banner =
  | { kind: "saving" }
  | { kind: "applied"; revision: number }
  | { kind: "conflict"; code: string; revision: number }
  | { kind: "failed"; code: string; revision: number; editsKept: boolean };

export const RuleBanner = ({ banner, onReload }: { banner: Banner; onReload: () => void }) => {
  if (banner.kind === "saving") {
    return (
      <Alert role="status" data-banner="saving">
        <AlertDescription className="flex flex-row flex-wrap items-center gap-2"><Badge variant="info">저장 중</Badge>운영에 적용하는 중입니다</AlertDescription>
      </Alert>
    );
  }
  if (banner.kind === "applied") {
    return (
      <Alert role="status" data-banner="applied">
        <AlertDescription className="flex flex-row flex-wrap items-center gap-2"><Badge variant="success">적용됨</Badge>운영에 적용됨 · 버전 {banner.revision}</AlertDescription>
      </Alert>
    );
  }
  return (
    <Alert variant="destructive" data-banner={banner.kind}>
      <AlertDescription className="flex flex-row flex-wrap items-center gap-2">
        {banner.kind === "conflict"
          ? `저장 거절 · ${banner.code} · 다른 사람이 먼저 저장했습니다(현재 버전 ${banner.revision}). 운영 룰은 버전 ${banner.revision} 그대로이며 이 변경은 적용되지 않았습니다. 새로 불러온 뒤 다시 변경하세요.`
          : `저장 실패 · ${banner.code} · 운영 룰은 버전 ${banner.revision} 그대로입니다.${banner.editsKept ? " 편집 내용은 남아 있습니다." : ""}`}
        {banner.kind === "conflict" && <Button size="sm" variant="outline" onClick={onReload}>새로 불러오기</Button>}
      </AlertDescription>
    </Alert>
  );
};
