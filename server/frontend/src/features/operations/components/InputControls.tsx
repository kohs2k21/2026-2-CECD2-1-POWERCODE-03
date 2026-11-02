import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { SectionCard } from "@/components/layout/SectionCard";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { getStoredToken } from "@/services/auth/session";
import {
  inputErrorText,
  requestInputMode,
  useInputStateQuery,
  type InputMode,
  type InputModeRequest,
} from "../data/inputs";

const modeLabel: Record<InputMode, string> = { api: "API", csv: "CSV", paused: "일시정지" };

/**
 * 입력 모드(API / CSV 재생 / 일시정지) — 관리자. 조회는 Query(진행 중만 5초), 변경은 Mutation(expectedRevision).
 * 실패는 성공으로 바꾸지 않고, 최신 상태를 다시 조회해 보여 준다.
 */
export function InputControls({ sourceId }: { sourceId: string }) {
  const query = useInputStateQuery(sourceId);
  const state = query.data;
  const [mode, setMode] = useState<InputMode>("csv");
  const [replay, setReplay] = useState("");
  const [rate, setRate] = useState(10);
  const [repeat, setRepeat] = useState(1);
  const [poll, setPoll] = useState(2);
  const [restart, setRestart] = useState(false);
  const [config, setConfig] = useState("");
  const [actionError, setActionError] = useState("");
  const initialized = useRef(false);
  useEffect(() => {
    if (initialized.current || !state?.control) return;
    initialized.current = true;
    setMode(state.control.mode);
    setConfig(state.control.configVersionId ?? "");
    setReplay(state.control.settings?.replayId ?? state.csvResume?.settings.replayId ?? state.replays[0]?.id ?? "");
    setRate(state.control.settings?.rate ?? state.csvResume?.settings.rate ?? 10);
    setRepeat(state.control.settings?.repeat ?? state.csvResume?.settings.repeat ?? 1);
    setPoll(state.control.settings?.pollSeconds ?? 2);
  }, [state]);
  // 요청 시작 시점의 토큰을 context로 들고, 세션(계정)이 바뀐 뒤 도착한 성공·실패 결과는 무시한다.
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
      setActionError(inputErrorText(error));
    },
    onSettled: (_result, _error, _body, context) => {
      if (context?.token !== getStoredToken()) return;
      void query.refetch();
    },
  });
  const select = (target: InputMode) => {
    if (!state?.control || mutation.isPending) return;
    mutation.mutate({
      mode: target,
      expectedRevision: state.control.revision,
      ...(target === "api"
        ? { pollSeconds: poll }
        : target === "csv"
          ? { replayId: replay, rate, repeat, restart }
          : {}),
      ...(target === "paused" ? {} : { configVersionId: config || null }),
    });
  };
  if (!sourceId) return null;
  const busy = mutation.isPending;
  const invalid =
    (mode === "csv" &&
      (!replay ||
        !Number.isFinite(rate) ||
        rate < 0.1 ||
        rate > 10000 ||
        !Number.isInteger(repeat) ||
        repeat < 1 ||
        repeat > 100)) ||
    (mode === "api" && (!Number.isFinite(poll) || poll < 0.1 || poll > 3600));
  return (
    <SectionCard
      title="입력 선택 · API / CSV"
      description="입력만 교체하며 같은 원천의 피처 생성·데이터 변환·Rule 추천 및 선택·모델 학습·탐지 경로를 사용합니다."
    >
      {query.isPending && <LoadingState />}
      {query.isError && <ErrorState title={inputErrorText(query.error)} onRetry={() => void query.refetch()} />}
      {actionError && (
        <Alert variant="destructive">
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      )}
      {state && !state.enabled && (
        <p className="text-sm text-muted-foreground">
          이 원천은 입력 전환에 등록되어 있지 않습니다. Collector를 시작하거나 입력 초기화를 실행하세요.
        </p>
      )}
      {state?.control && (
        <>
          <DescriptionList
            items={[
              { label: "현재 입력", value: <strong>{modeLabel[state.control.mode]}</strong> },
              { label: "실행 상태", value: state.control.status ?? "대기" },
              {
                label: "원천 데이터",
                value: `T ${state.storedCounts.T ?? 0} · P ${state.storedCounts.P ?? 0} · M ${state.storedCounts.M ?? 0} · BODY 참조 ${state.storedCounts.B ?? 0}`,
              },
              {
                label: "누적 저장",
                value: `${state.control.accepted ?? 0}행 · 중복 ${state.control.duplicates ?? 0}행 · 식별자 부족 ${state.control.unidentified ?? 0}행`,
              },
            ]}
          />
          {state.control.lastError && (
            <Alert variant="destructive">
              <AlertDescription>
                입력 오류: {state.control.lastError}. 다른 모드로 자동 전환하지 않습니다.
              </AlertDescription>
            </Alert>
          )}
          {!state.control.workerHeartbeatAt && (
            <p className="text-sm text-muted-foreground">
              Collector 처리 기록이 아직 없습니다. 모드 선택 후 실제 저장 건수를 확인하세요.
            </p>
          )}
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            <div className="grid gap-2">
              <Label htmlFor="input-mode">입력 방식</Label>
              <NativeSelect id="input-mode" value={mode} onChange={(e) => setMode(e.target.value as InputMode)}>
                <NativeSelectOption value="api">API 실시간 조회</NativeSelectOption>
                <NativeSelectOption value="csv">CSV 순차 재생</NativeSelectOption>
                <NativeSelectOption value="paused">일시정지</NativeSelectOption>
              </NativeSelect>
            </div>
            <div className="grid gap-2">
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
            {mode === "api" && (
              <div className="grid gap-2">
                <Label htmlFor="input-poll">API 조회 간격(초)</Label>
                <Input
                  id="input-poll"
                  type="number"
                  min="0.1"
                  max="3600"
                  step="0.1"
                  value={poll}
                  onChange={(e) => setPoll(Number(e.target.value))}
                />
              </div>
            )}
            {mode === "csv" && (
              <>
                <div className="grid gap-2">
                  <Label htmlFor="input-replay">등록된 CSV</Label>
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
                  <Input
                    id="input-rate"
                    type="number"
                    min="0.1"
                    max="10000"
                    step="0.1"
                    value={rate}
                    onChange={(e) => setRate(Number(e.target.value))}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="input-repeat">반복 횟수</Label>
                  <Input
                    id="input-repeat"
                    type="number"
                    min="1"
                    max="100"
                    value={repeat}
                    onChange={(e) => setRepeat(Number(e.target.value))}
                  />
                </div>
                <div className="flex items-center gap-2 self-end pb-2">
                  <Checkbox
                    id="input-restart"
                    checked={restart}
                    onCheckedChange={(value) => setRestart(value === true)}
                  />
                  <Label htmlFor="input-restart">처음부터 새 재생 실행</Label>
                </div>
              </>
            )}
          </div>
          <div className="grid gap-1 text-sm text-muted-foreground">
            {mode === "csv" && (
              <p>
                T·P·M·BODY 참조 행을 합산한 전송 속도입니다. 같은 묶음·속도·횟수는 중단 위치부터 재개하고, 완료된
                재생은 새 실행을 선택해야 다시 보냅니다.
              </p>
            )}
            <p>
              {config
                ? "선택한 학습 완료 후보로 검증합니다. 운영 활성 구성이나 정확도 검증·승인 상태는 변경하지 않습니다."
                : "운영 구성이 적용되기 전에도 데이터를 축적할 수 있습니다. 탐지는 구성이 준비될 때까지 큐에서 대기합니다."}
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Button disabled={busy || invalid} onClick={() => select(mode)}>
              선택한 입력 적용
            </Button>
            <Button variant="outline" disabled={busy} onClick={() => select("paused")}>
              입력 일시정지
            </Button>
          </div>
          <p className="text-sm text-muted-foreground">
            이미 등록된 작업은 고정된 구성으로 처리를 마칩니다. BODY 원문은 현재 수집하지 않으며 Message 모니터링과
            구분해 참조만 보관합니다.
          </p>
        </>
      )}
    </SectionCard>
  );
}
