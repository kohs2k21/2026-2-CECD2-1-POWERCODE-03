import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { SectionCard } from "@/components/layout/SectionCard";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Progress } from "@/components/ui/progress";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { HttpError } from "@/services/api/client";
import { getStoredToken } from "@/services/auth/session";
import {
  inputErrorText,
  requestInputMode,
  useInputStateQuery,
  type InputMode,
  type InputModeRequest,
  type InputState,
} from "../data/inputs";
import type { CollectionView } from "../data/operationsReads";
import { DASH, numberText, percentText } from "../format";
import { inputModeLabel } from "./inputModeText";

/** 화면 표시용 입력 상태(관리자: /api/inputs, 일반 사용자: /api/operations/collection 읽기). */
export type InputView = NonNullable<CollectionView["input"]>;
export const inputViewFromState = (state: InputState): InputView | null => {
  const control = state.control;
  if (!control) return null;
  const replay = state.replays.find((item) => item.id === control.settings?.replayId);
  const repeat = control.settings?.repeat ?? null;
  return {
    mode: control.mode,
    revision: control.revision,
    status: control.status,
    csv:
      control.mode === "csv"
        ? {
            replayKey: replay?.replayKey ?? null,
            recordCount: replay?.recordCount ?? null,
            rate: control.settings?.rate ?? null,
            repeat,
            position: control.position,
            total: replay && repeat ? replay.recordCount * repeat : null,
          }
        : null,
    pollSeconds: control.mode === "api" ? (control.settings?.pollSeconds ?? null) : null,
  };
};

/** 모드 변경 실패 문구(와이어프레임 requestInputMode). 실패는 성공으로 바꾸지 않고 현재 모드를 그대로 둔다. */
export const inputFailureText = (error: unknown, current: InputMode | undefined) => {
  if (error instanceof HttpError && error.code) return `입력 모드 변경 실패 · ${error.code} · ${inputErrorText(error)}`;
  return `입력 모드 변경 실패 · 입력 모드는 바뀌지 않았습니다(현재: ${current ? inputModeLabel[current] : DASH}).`;
};

/** 현재 입력 요약: 모드·revision, CSV면 재생 대상·속도·진행률. 일반 사용자는 이 요약만 본다. */
export const InputModeSummary = ({ input, pending = false }: { input: InputView | null; pending?: boolean }) => {
  if (!input) return <p className="text-sm">현재: <strong>{DASH} 확인 불가</strong></p>;
  const csv = input.csv;
  const ratio = csv?.position != null && csv.total ? Math.min(1, csv.position / csv.total) : null;
  return (
    <div className="grid gap-3">
      <p className="text-sm">
        현재: <strong>{pending ? "요청 처리 중" : inputModeLabel[input.mode]}</strong>{" "}
        <span className="text-muted-foreground">revision {input.revision ?? DASH}</span>
      </p>
      {csv && (
        <DescriptionList
          items={[
            { label: "재생 대상", value: csv.replayKey ? `${csv.replayKey}${csv.recordCount === null ? "" : ` · ${numberText(csv.recordCount)}행`}` : DASH },
            { label: "재생 속도", value: csv.rate === null ? DASH : `초당 ${numberText(csv.rate)}행${csv.repeat ? ` · 반복 ${csv.repeat}회` : ""}` },
            {
              label: "진행률",
              value:
                ratio === null || csv.position === null || csv.total === null ? (
                  DASH
                ) : (
                  <span className="grid gap-1">
                    <span>
                      {numberText(csv.position)} / {numberText(csv.total)}행 ({percentText(ratio)})
                    </span>
                    <Progress value={Math.round(ratio * 1000) / 10} aria-label="CSV 재생 진행률" />
                  </span>
                ),
            },
          ]}
        />
      )}
    </div>
  );
};

type Ask = { target: InputMode; expectedRevision: number };
const askText = (target: InputMode, current: InputMode) =>
  target === "paused"
    ? ["입력을 일시정지할까요", "정지하는 동안 새 판정이 생기지 않습니다", "일시정지"]
    : target === "api"
      ? ["실서버 API 입력으로 바꿀까요",
          current === "paused" ? "일시정지를 풀고 실서버 API 입력을 받습니다." : "입력은 하나만 받습니다. CSV 재생은 멈추고 실서버 API 입력을 받습니다.",
          "실서버 API로 전환"]
      : current === "csv"
        ? ["CSV 재생 설정을 다시 적용할까요", "선택한 재생 대상·속도로 다시 적용합니다.", "다시 적용"]
        : current === "paused"
          ? ["CSV 재생으로 바꿀까요", "일시정지를 풀고 선택한 CSV를 재생합니다.", "CSV 재생으로 전환"]
          : ["CSV 재생으로 바꿀까요", "입력은 하나만 받습니다. 실서버 API 입력은 멈추고 선택한 CSV를 재생합니다.", "CSV 재생으로 전환"];

/**
 * 입력 모드(실서버 API / CSV 재생 / 일시정지) — 변경은 관리자만. 조회는 Query(진행 중만 5초), 변경은 Mutation(expectedRevision).
 * 선택 시 확인 창에서 설정을 고른 뒤 요청한다. 실패는 성공으로 바꾸지 않고 최신 상태를 다시 조회한다.
 * 세션(계정)이 바뀐 뒤 도착한 성공·실패 결과는 무시한다.
 */
export function InputControls({ sourceId }: { sourceId: string }) {
  const query = useInputStateQuery(sourceId);
  const state = query.data;
  const [ask, setAsk] = useState<Ask | null>(null);
  const [replay, setReplay] = useState("");
  const [rate, setRate] = useState(10);
  const [repeat, setRepeat] = useState(1);
  const [poll, setPoll] = useState(2);
  const [restart, setRestart] = useState(false);
  const [config, setConfig] = useState("");
  const [actionError, setActionError] = useState("");
  const mutation = useMutation({
    mutationFn: (body: InputModeRequest) => requestInputMode(sourceId, body),
    onMutate: () => {
      setActionError("");
      return { token: getStoredToken() };
    },
    onSuccess: (_result, _body, context) => {
      if (context?.token !== getStoredToken()) return;
      setRestart(false);
    },
    onError: (error, _body, context) => {
      if (context?.token !== getStoredToken()) return;
      setActionError(inputFailureText(error, state?.control?.mode));
    },
    onSettled: (_result, _error, _body, context) => {
      if (context?.token !== getStoredToken()) return;
      void query.refetch();
    },
  });
  if (!sourceId) return null;
  const control = state?.control ?? null;
  const busy = mutation.isPending;
  const open = (target: InputMode) => {
    if (!control || busy) return;
    // 확인 창의 기본값: 현재(또는 마지막 CSV) 설정. 요청 revision은 창을 연 시점 값으로 고정한다.
    setConfig(control.configVersionId ?? "");
    setReplay(control.settings?.replayId ?? state?.csvResume?.settings.replayId ?? state?.replays[0]?.id ?? "");
    setRate(control.settings?.rate ?? state?.csvResume?.settings.rate ?? 10);
    setRepeat(control.settings?.repeat ?? state?.csvResume?.settings.repeat ?? 1);
    setPoll(control.settings?.pollSeconds ?? 2);
    setAsk({ target, expectedRevision: control.revision });
  };
  const confirm = () => {
    if (!ask) return;
    const { target, expectedRevision } = ask;
    setAsk(null);
    mutation.mutate({
      mode: target,
      expectedRevision,
      ...(target === "api" ? { pollSeconds: poll } : target === "csv" ? { replayId: replay, rate, repeat, restart } : {}),
      ...(target === "paused" ? {} : { configVersionId: config || null }),
    });
  };
  const invalid =
    (ask?.target === "csv" &&
      (!replay || !Number.isFinite(rate) || rate < 0.1 || rate > 10000 || !Number.isInteger(repeat) || repeat < 1 || repeat > 100)) ||
    (ask?.target === "api" && (!Number.isFinite(poll) || poll < 0.1 || poll > 3600));
  const [title, description, confirmLabel] = ask && control ? askText(ask.target, control.mode) : ["", "", ""];
  return (
    <SectionCard title="입력 모드">
      {query.isPending && <LoadingState />}
      {query.isError && <ErrorState title={inputErrorText(query.error)} onRetry={() => void query.refetch()} />}
      {state && !state.enabled && (
        <p className="text-sm text-muted-foreground">
          이 원천은 입력 전환에 등록되어 있지 않습니다. Collector를 시작하거나 입력 초기화를 실행하세요.
        </p>
      )}
      {state && control && (
        <>
          <InputModeSummary input={inputViewFromState(state)} pending={busy} />
          <RadioGroup
            aria-label="입력 모드"
            value={control.mode}
            onValueChange={(value) => value !== control.mode && open(value as InputMode)}
            disabled={busy}
            className="flex flex-wrap gap-x-6 gap-y-2"
          >
            {(["api", "csv", "paused"] as const).map((mode) => (
              <div key={mode} className="flex items-center gap-2">
                <RadioGroupItem id={`input-mode-${mode}`} value={mode} />
                <Label htmlFor={`input-mode-${mode}`}>{inputModeLabel[mode]}</Label>
              </div>
            ))}
          </RadioGroup>
          {control.mode === "csv" && (
            <div>
              <Button variant="outline" size="sm" disabled={busy} onClick={() => open("csv")}>
                재생 설정 변경
              </Button>
            </div>
          )}
          {control.lastError && (
            <Alert variant="destructive">
              <AlertDescription>입력 오류: {control.lastError}. 다른 모드로 자동 전환하지 않습니다.</AlertDescription>
            </Alert>
          )}
        </>
      )}
      {actionError && (
        <Alert variant="destructive">
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      )}
      <Dialog open={Boolean(ask)} onOpenChange={(value) => !value && setAsk(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          {state && ask && ask.target !== "paused" && (
            <div className="grid gap-4 sm:grid-cols-2">
              {ask.target === "csv" && (
                <>
                  <div className="grid gap-2 sm:col-span-2">
                    <Label htmlFor="input-replay">재생 대상</Label>
                    <NativeSelect id="input-replay" value={replay} onChange={(e) => setReplay(e.target.value)}>
                      <NativeSelectOption value="">재생할 파일 묶음 선택</NativeSelectOption>
                      {state.replays.map((r) => (
                        <NativeSelectOption key={r.id} value={r.id}>
                          {r.replayKey} · {r.recordCount.toLocaleString()}행
                        </NativeSelectOption>
                      ))}
                    </NativeSelect>
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="input-rate">초당 전송 행 수</Label>
                    <Input id="input-rate" type="number" min="0.1" max="10000" step="0.1" value={rate} onChange={(e) => setRate(Number(e.target.value))} />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="input-repeat">반복 횟수</Label>
                    <Input id="input-repeat" type="number" min="1" max="100" value={repeat} onChange={(e) => setRepeat(Number(e.target.value))} />
                  </div>
                  <div className="flex items-center gap-2 sm:col-span-2">
                    <Checkbox id="input-restart" checked={restart} onCheckedChange={(value) => setRestart(value === true)} />
                    <Label htmlFor="input-restart">처음부터 새 재생 실행</Label>
                  </div>
                </>
              )}
              {ask.target === "api" && (
                <div className="grid gap-2">
                  <Label htmlFor="input-poll">API 조회 간격(초)</Label>
                  <Input id="input-poll" type="number" min="0.1" max="3600" step="0.1" value={poll} onChange={(e) => setPoll(Number(e.target.value))} />
                </div>
              )}
              <div className="grid gap-2 sm:col-span-2">
                <Label htmlFor="input-config">공통 탐지 구성</Label>
                <NativeSelect id="input-config" value={config} onChange={(e) => setConfig(e.target.value)}>
                  <NativeSelectOption value="">승인·적용된 운영 구성 사용</NativeSelectOption>
                  {state.configurations.map((c) => (
                    <NativeSelectOption key={c.id} value={c.id}>
                      검증용 후보 · {c.mode ?? "이전 구성"} · {new Date(c.createdAt).toLocaleString()}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setAsk(null)}>
              취소
            </Button>
            <Button disabled={busy || invalid} onClick={confirm}>
              {confirmLabel}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SectionCard>
  );
}
