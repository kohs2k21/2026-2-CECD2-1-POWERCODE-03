import type { ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState, ErrorState } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { stampText } from "@/features/operations/format";
import { cn } from "@/lib/utils";
import { writeCompareDraft, type CompareDraft, type ProfileKey, type SetForm } from "./compareDraft";
import { NOT_CONNECTED, evaluationCapabilities, type EvaluationSet } from "./evaluationsApi";
import { SET_STATUS_LABELS, fixedInfoText, rowCountText, scenarioText, setKindLabel } from "./evaluationFormat";
import { useSnapshots } from "./useEvaluations";

const COLUMNS = ["선택", "세트", "생성 방식", "시나리오", "행 수(이상 정답)", "고정 정보"];

type ScenarioOption = { value: string; label: string; disabled?: boolean };
const PROFILES: Record<ProfileKey, { label: string; scenarios: ScenarioOption[]; note: string }> = {
  esb: {
    label: "ESB 시나리오 · esb-process-research-v1",
    scenarios: [
      { value: "P02", label: "P02 · PROCESS 개별 지연" },
      { value: "P03", label: "P03 · 연속 지연" },
      { value: "P04", label: "P04 · 건수 계약 변형" },
      { value: "P01", label: "P01 · 업무 실패(의미 미확정 · 보류)", disabled: true },
    ],
    note: "원본 복사본의 허용 필드만 바꾸고 같은 파생 변수·같은 판정 경로로 계산합니다. 지연 배수 1.5/2/3, 건수 증가 1/2/3. 합성 변형 탐지율은 고객 장애 진단 성능이 아닙니다.",
  },
  adbench: {
    label: "ADBench 수치 합성 · adbench-numeric-v1",
    scenarios: [
      { value: "local", label: "local · 국소 밀도 이탈" },
      { value: "cluster", label: "cluster · 군집 이동" },
      { value: "global", label: "global · 전역 이탈" },
    ],
    note: "고정 피처 공간의 수치 행을 생성합니다. 라벨은 분포 소속이며 ESB 정상/장애 정답이 아닙니다. PROCESS 식별자·룰 입력 필드가 없어 룰과 함께 판정한 결과는 미측정이 됩니다.",
  },
};

/** 시나리오 수·반복 상한(synthetic-evaluation.md). 최종 검증은 서버가 한다. */
const generationLimit = (count: number, repetitions: number) =>
  count === 0 ? "시나리오를 1개 이상 선택해 주세요." : count > 4 ? "시나리오는 최대 4개입니다." : count * repetitions > 20 ? `반복 × 시나리오는 최대 20입니다(현재 ${count * repetitions}).` : null;

const Field = ({ label, id, children }: { label: string; id: string; children: ReactNode }) => (
  <div className="grid gap-1.5"><Label htmlFor={id}>{label}</Label>{children}</div>
);
const updateForm = (update: (form: SetForm) => SetForm) => writeCompareDraft((current) => ({ ...current, setForm: update(current.setForm) }));
const setField = (field: Exclude<keyof SetForm, "profile" | "scenarios">, value: string) => updateForm((form) => ({ ...form, [field]: value }));

/** 새 평가 세트 생성 입력. 값은 비교 초안에 있어 단계를 오가도 남는다. 생성 요청은 연결 전이라 비활성이다. */
const GenerationForm = ({ form }: { form: SetForm }) => {
  const snapshots = useSnapshots();
  const spec = PROFILES[form.profile];
  const limit = generationLimit(form.scenarios.length, Number(form.repetitions) || 0);
  const snapshotId = form.snapshotId ?? snapshots.data?.[0]?.id ?? "";
  const changeProfile = (profile: ProfileKey) =>
    updateForm((current) => ({ ...current, profile, scenarios: [PROFILES[profile].scenarios[0].value], severity: profile === "adbench" ? "1" : current.severity }));
  return (
    <div className="grid gap-4 rounded-lg border p-4">
      <h3 className="text-sm font-medium">새 평가 세트 생성 · 합성 이상 생성기</h3>
      <div className="grid gap-4 md:grid-cols-3">
        <Field label="세트 이름" id="set-name"><Input id="set-name" value={form.name} maxLength={80} onChange={(event) => setField("name", event.target.value)} /></Field>
        <Field label="생성 프로필" id="set-profile">
          <NativeSelect id="set-profile" value={form.profile} onChange={(event) => changeProfile(event.target.value as ProfileKey)}>
            {(Object.keys(PROFILES) as ProfileKey[]).map((key) => <NativeSelectOption key={key} value={key}>{PROFILES[key].label}</NativeSelectOption>)}
          </NativeSelect>
        </Field>
        <Field label="기준 저장 데이터셋" id="set-snapshot">
          <NativeSelect id="set-snapshot" value={snapshotId} disabled={!snapshots.data?.length} onChange={(event) => setField("snapshotId", event.target.value)}>
            {snapshots.data?.map((snapshot) => <NativeSelectOption key={snapshot.id} value={snapshot.id}>{snapshot.displayName}</NativeSelectOption>)}
          </NativeSelect>
        </Field>
      </div>
      {snapshots.isError && <ErrorState title={`저장 데이터셋 조회 실패 · ${errorCode(snapshots.error)}`} onRetry={() => void snapshots.refetch()} retryLabel="다시 조회" />}
      <fieldset className="grid gap-2">
        <legend className="text-sm font-medium">시나리오 · 1~4개</legend>
        {spec.scenarios.map(({ value, label, disabled }) => (
          <Label key={value} className={cn("font-normal", disabled && "text-muted-foreground")}>
            <Checkbox
              checked={form.scenarios.includes(value)}
              disabled={disabled}
              onCheckedChange={(checked) => updateForm((current) => ({ ...current, scenarios: checked === true ? [...current.scenarios, value] : current.scenarios.filter((item) => item !== value) }))}
            />
            {label}
          </Label>
        ))}
      </fieldset>
      <div className="grid gap-4 md:grid-cols-4">
        <Field label="강도" id="set-severity">
          <NativeSelect id="set-severity" value={form.severity} disabled={form.profile !== "esb"} onChange={(event) => setField("severity", event.target.value)}>
            {["1", "2", "3"].map((value) => <NativeSelectOption key={value} value={value}>{value}</NativeSelectOption>)}
          </NativeSelect>
        </Field>
        <Field label="반복 수 · 1~5" id="set-repetitions"><Input id="set-repetitions" type="number" min={1} max={5} step={1} value={form.repetitions} onChange={(event) => setField("repetitions", event.target.value)} /></Field>
        <Field label="대상 행 수 · 1~100" id="set-rows"><Input id="set-rows" type="number" min={1} max={100} step={1} value={form.targetRows} onChange={(event) => setField("targetRows", event.target.value)} /></Field>
        <Field label="seed" id="set-seed"><Input id="set-seed" type="number" min={0} max={2147483647} step={1} value={form.seed} onChange={(event) => setField("seed", event.target.value)} /></Field>
      </div>
      <p className="text-sm text-muted-foreground">{spec.note}</p>
      {limit && <p role="status" className="text-sm text-warning-text">{limit}</p>}
      {!evaluationCapabilities.createEvaluationSet && <p className="text-sm text-warning-text">확인 필요 · 평가 세트 생성: {NOT_CONNECTED}</p>}
      <div className="flex gap-2">
        <Button disabled={!evaluationCapabilities.createEvaluationSet || Boolean(limit) || !snapshotId}>평가 세트 생성</Button>
        <Button variant="outline" onClick={() => writeCompareDraft((current) => ({ ...current, setFormOpen: false }))}>닫기</Button>
      </div>
    </div>
  );
};

/** 1단계 평가 세트: 고정 세트(`status=ready`)만 고를 수 있다. 생성 중·실패 세트는 표시만 한다. */
export const SetStep = ({ sets, draft }: { sets: EvaluationSet[]; draft: CompareDraft }) => (
  <div className="grid gap-4">
    <h3 className="text-sm font-medium">평가 세트 선택</h3>
    {sets.length === 0 ? <EmptyState>평가 세트가 없습니다.</EmptyState> : (
      <RadioGroup value={draft.setId ?? ""} onValueChange={(setId) => writeCompareDraft((current) => ({ ...current, setId }))} aria-label="평가 세트">
        <Table>
          <TableCaption>고정 세트 · 같은 세트·분할·정답·생성 버전을 모든 비교 모델에 재사용</TableCaption>
          <TableHeader>
            <TableRow>{COLUMNS.map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow>
          </TableHeader>
          <TableBody>
            {sets.map((set) => (
              <TableRow key={set.id} className={cn(set.status !== "ready" && "text-muted-foreground")}>
                <TableCell><RadioGroupItem value={set.id} disabled={set.status !== "ready"} aria-label={`${set.displayName} 선택`} /></TableCell>
                <TableCell>
                  <span className="grid gap-0.5">
                    <span className="flex items-center gap-2">{set.displayName}{set.status !== "ready" && <Badge variant={set.status === "failed" ? "critical" : "info"}>{SET_STATUS_LABELS[set.status]}</Badge>}</span>
                    <span className="font-mono text-xs text-muted-foreground">{set.id.slice(0, 8)} · {stampText(set.createdAt)}</span>
                  </span>
                </TableCell>
                <TableCell>{setKindLabel(set)}</TableCell>
                <TableCell>{scenarioText(set)}</TableCell>
                <TableCell>{rowCountText(set)}</TableCell>
                <TableCell className="font-mono text-xs">{fixedInfoText(set)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </RadioGroup>
    )}
    <div><Button variant="outline" onClick={() => writeCompareDraft((current) => ({ ...current, setFormOpen: true }))}>새 평가 세트 생성</Button></div>
    {draft.setFormOpen && <GenerationForm form={draft.setForm} />}
  </div>
);
