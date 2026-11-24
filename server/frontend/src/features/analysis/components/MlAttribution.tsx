import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Attribution } from "../data/attribution";
import { ATTRIBUTION_TITLE, attributionFailureLabels, attributionReasonLabels, attributionStatusWords } from "../attributionLabels";
import { attributionSentences, attributionTableRows } from "../attributionText";
import { DASH } from "../labels";

/**
 * 판정 상세 `세부 방법별 결과`의 머신러닝 모델 행 아래 기여도(analysis-ui.md 판정 상세). 저장된 `attribution`만 읽는다.
 * - 성공: 요약 문장 + 상위 변수 표(변수·실제값·학습 데이터 중앙값·비율) + 반사실·안내 문장 + 한계 문장
 * - 그 밖: 상태 낱말(키 없음 미계산·계산 안 함·계산 실패·표시 불가), 사유·코드 이름은 옆 글자와 툴팁
 * - 문장을 만들다 예상 못 한 예외가 나면 판정 상세가 내려가지 않게 `기여도 표시 불가`로 보인다
 */
type StatusAttribution = Exclude<Attribution, { status: "succeeded" }>;
const statusLine = (attribution: StatusAttribution) => {
  if (attribution.status === "absent") return { word: attributionStatusWords.absent, detail: null };
  if (attribution.status === "skipped")
    return { word: attributionStatusWords.skipped, detail: attribution.reason ? attributionReasonLabels[attribution.reason] : null };
  if (attribution.status === "failed")
    return { word: attributionStatusWords.failed, detail: attribution.failureCode ? attributionFailureLabels[attribution.failureCode] : null };
  return { word: attributionStatusWords.unreadable, detail: null };
};
const StatusLine = ({ attribution }: { attribution: StatusAttribution }) => {
  const line = statusLine(attribution);
  return (
    <p className="text-sm text-muted-foreground" data-attribution-status={attribution.status} title={line.detail ?? undefined}>
      {line.detail ? `${line.word} · ${line.detail}` : line.word}
    </p>
  );
};
const build = (attribution: Extract<Attribution, { status: "succeeded" }>) => {
  try {
    return { sentences: attributionSentences(attribution), rows: attributionTableRows(attribution) };
  } catch {
    return null;
  }
};

export const MlAttribution = ({ attribution }: { attribution: Attribution }) => {
  if (attribution.status !== "succeeded") return <StatusLine attribution={attribution} />;
  const built = build(attribution);
  if (!built) return <StatusLine attribution={{ status: "unreadable" }} />;
  const { sentences, rows } = built;
  const notes = [sentences.shareNull, sentences.negative, ...sentences.groups, sentences.baselineWarning].filter((s): s is string => Boolean(s));
  return (
    <div className="grid gap-2 whitespace-normal" data-attribution-status="succeeded">
      <h4 className="text-sm font-medium">{ATTRIBUTION_TITLE}</h4>
      <p className="text-sm" data-attribution="summary">{sentences.summary}</p>
      {rows.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              {["변수", "실제값", "학습 데이터 중앙값", "비율"].map((head) => (
                <TableHead key={head}>{head}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.playerId} data-attribution-player="true">
                {/* 묶음 참가자는 구성원 줄이 여럿이라 모든 칸을 위 맞춤(머리 칸도 본문 칸과 같은 위 여백) */}
                <TableHead scope="row" className="h-auto py-2 align-top font-normal">
                  {row.members.map((m) => <div key={m.feature}>{m.name}</div>)}
                </TableHead>
                <TableCell className="align-top">{row.members.map((m) => <div key={m.feature}>{m.actual}</div>)}</TableCell>
                <TableCell className="align-top">{row.members.map((m) => <div key={m.feature}>{m.baseline}</div>)}</TableCell>
                <TableCell className="align-top">{row.share ?? DASH}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {sentences.counterfactual && <p className="text-sm" data-attribution="counterfactual">{sentences.counterfactual}</p>}
      {notes.map((note, index) => (
        <p key={index} className="text-sm text-muted-foreground" data-attribution="note">{note}</p>
      ))}
      <p className="text-sm text-muted-foreground" data-attribution="limitation">{sentences.limitation}</p>
    </div>
  );
};
