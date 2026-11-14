import type { DecisionDetail, FeedbackRecord, InterimHistory, InterimRecord } from "../data/analysisApi";
import { readEvidenceV4 } from "../data/evidence";
import { DASH, feedbackKindLabels, feedbackOutcomeLabels, feedbackOutcomeTitle, methodLabels, stampText } from "../labels";
import { cn } from "@/lib/utils";

/** 판정 기록 한 줄. `title`은 사유 코드 등(화면에는 표시 이름, 코드는 툴팁 — 와이어프레임 규칙). */
/** `note` = 조사·조치 사유(읽기 전용 기록 줄만, 줄바꿈 그대로). */
export type TimelineEntry = {
  key: string;
  at: string | null;
  tone: "provisional" | "final" | "void" | "progress" | "record";
  text: string;
  title?: string;
  note?: string;
};

const attempt = (record: InterimRecord) => (record.attemptNo === null ? "" : `시도 ${record.attemptNo} · `);
const basisOf = (record: InterimRecord | undefined) => (record ? (readEvidenceV4(record.payload)?.basisMethods ?? []) : []);
const failedText = (attemptNo: number | null | undefined) =>
  `${attemptNo ? `시도 ${attemptNo} 실패 · ` : ""}최종 없음(판정 실패)`;

/**
 * 판정 기록(중간 → 최종): 중간 판정 기록(시도·순번 순) + 최종 판정 또는 작업 실패 또는 진행 중.
 * 문구는 와이어프레임 판정 기록 형식: `시도 N · 중간 판정 · 근거 <방법 표시 이름>`, `근거 추가 · <방법 표시 이름>`(labels.ts methodLabel, ID는 툴팁), `시도 N 결과 · 대체됨`,
 * `시도 N 실패 · 최종 없음(판정 실패)`, `최종 · 모든 방법 종료`(검사 완료일 때만, 그 밖은 `최종`).
 */
export const timelineEntries = (detail: DecisionDetail, history: InterimHistory | undefined): TimelineEntry[] => {
  const items = history?.items ?? [];
  const entries: TimelineEntry[] = items.map((record, index) => {
    switch (record.kind) {
      case "first_anomaly": {
        // 와이어프레임 `중간 판정 · 근거 이상 규칙(상태가 S가 아님)`: 표시 이름, 방법 ID는 툴팁
        const methods = basisOf(record), names = methodLabels(methods);
        return {
          key: record.id,
          at: record.createdAt,
          tone: "provisional",
          // 근거 방법이 없으면(v3 등) `중간 판정`, 있으나 표시 이름이 없으면(알 수 없는 접두) `중간 판정 · 근거`
          text: `${attempt(record)}중간 판정${methods.length ? ` · 근거${names.length ? ` ${names.join(", ")}` : ""}` : ""}`,
          title: methods.length ? methods.join(", ") : undefined,
        };
      }
      case "evidence_added": {
        // 와이어프레임 `근거 추가 · <방법>`: 같은 시도의 직전 기록보다 늘어난 근거 방법. 알 수 없으면 `근거 추가`만.
        const previous = items.slice(0, index).reverse().find((item) => item.attemptNo === record.attemptNo && item.kind !== "superseded");
        const before = new Set(basisOf(previous));
        const added = previous && before.size ? basisOf(record).filter((method) => !before.has(method)) : [];
        const names = methodLabels(added);
        return {
          key: record.id,
          at: record.createdAt,
          tone: "provisional",
          text: `${attempt(record)}근거 추가${names.length ? ` · ${names.join(", ")}` : ""}`,
          title: added.length ? added.join(", ") : undefined,
        };
      }
      case "superseded":
        return {
          key: record.id,
          at: record.createdAt,
          tone: "void",
          text: `${record.attemptNo === null ? "" : `시도 ${record.attemptNo} `}결과 · 대체됨`,
        };
      case "no_final":
        return { key: record.id, at: record.createdAt, tone: "void", text: failedText(record.attemptNo), title: record.reasonCode ?? undefined };
    }
  });
  const attemptCount = detail.job?.attemptCount ?? null;
  const attempts = attemptCount !== null && attemptCount > 1 ? `시도 ${attemptCount} · ` : "";
  if (detail.kind === "final")
    entries.push({
      key: "final",
      at: detail.decidedAt,
      tone: "final",
      // 검사 완료(complete) = 선택 경로가 모두 끝남. 부분·판정불가는 기한 도달·실패를 구분할 저장값이 없어 `최종`만.
      text: `${attempts}최종${detail.status === "complete" ? " · 모든 방법 종료" : ""}`,
    });
  else if (detail.kind === "failed" && !history?.items.some((record) => record.kind === "no_final"))
    entries.push({
      key: "failed",
      at: detail.job?.finishedAt ?? detail.occurredAt,
      tone: "void",
      text: failedText(attemptCount),
      title: detail.job?.errorCode ?? undefined,
    });
  // 와이어프레임 진행 중 줄(`진행 중` · `남은 방법 결과 대기`)은 작업이 대기·실행 중일 때만. 그 밖의 상태(blocked·작업 없음)는 줄을 두지 않는다.
  else if ((detail.kind === "pending" || detail.kind === "interim") && (detail.job?.status === "queued" || detail.job?.status === "running"))
    entries.push({ key: "progress", at: null, tone: "progress", text: "남은 방법 결과 대기" });
  return entries;
};

/**
 * 관리자 읽기 전용 줄(사용자 11/13 "판정 기록에 읽기전용으로"): 저장된 조사·조치 기록 `<유형> · <작성자 email>` + 사유.
 * 확인 결과가 있으면 유형 뒤에 `확인 결과 <값>`(029), 미입력이면 줄에 두지 않는다.
 * 현재 업무 분류는 판정 요약에 있으므로 여기에 두지 않는다(변경 이력 API가 없어 마지막 변경만 보이면 이력처럼 읽힘).
 */
export const recordEntries = (records: FeedbackRecord[]): TimelineEntry[] =>
  records.map((record) => ({
    key: `record-${record.id}`,
    at: record.createdAt,
    tone: "record",
    text: [feedbackKindLabels[record.kind], record.outcome && `${feedbackOutcomeTitle} ${feedbackOutcomeLabels[record.outcome]}`, record.actorEmail ?? record.actorId ?? DASH]
      .filter(Boolean)
      .join(" · "),
    note: record.reason,
  }));

/**
 * 판정 기록 줄 + 읽기 전용 줄을 시각 순으로 합친다. 판정 기록 줄의 순서는 그대로 두고, 읽기 전용 줄(시각 오름차순)을 그보다 늦은 첫 판정 기록 줄 앞에 넣는다.
 * 진행 중 줄은 지금이므로 그 앞에 남은 읽기 전용 줄을 모두 넣는다. 같은 시각이면 받은 순서.
 */
export const mergeTimeline = (base: TimelineEntry[], extra: TimelineEntry[]): TimelineEntry[] => {
  const ms = (entry: TimelineEntry) => (entry.at ? Date.parse(entry.at) : Number.NaN);
  const queue = extra
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => (ms(a.entry) || 0) - (ms(b.entry) || 0) || a.index - b.index)
    .map(({ entry }) => entry);
  const merged: TimelineEntry[] = [];
  for (const entry of base) {
    const at = ms(entry);
    while (queue.length && (entry.tone === "progress" || (Number.isFinite(at) && ms(queue[0]) < at))) merged.push(queue.shift() as TimelineEntry);
    merged.push(entry);
  }
  return [...merged, ...queue];
};

export const DecisionTimeline = ({ entries }: { entries: TimelineEntry[] }) => (
  <ol className="grid gap-2 border-l pl-4 text-sm">
    {entries.map((entry) => (
      <li
        key={entry.key}
        className={cn(
          "grid gap-0.5",
          entry.tone === "final" && "font-medium",
          entry.tone === "void" && "text-destructive-text",
          entry.tone === "progress" && "text-muted-foreground",
        )}
        data-entry={entry.tone === "record" ? "record" : undefined}
      >
        <span className="text-xs text-muted-foreground tabular-nums">
          {entry.at ? stampText(entry.at) : entry.tone === "progress" ? "진행 중" : "—"}
        </span>
        <span title={entry.title} className={entry.tone === "record" ? "text-muted-foreground" : undefined}>
          {entry.text}
        </span>
        {entry.note && <span className="whitespace-pre-wrap break-words">{entry.note}</span>}
      </li>
    ))}
  </ol>
);
