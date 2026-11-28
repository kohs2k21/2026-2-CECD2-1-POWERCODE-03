import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import type { CatalogMethod } from "./modelsApi";
import { methodTrainable } from "./modelFrames";
import { METHOD_DESCRIPTIONS, modelKeyOf, parameterRows, parameterValue } from "./trainDraft";
import { MethodStatus } from "./VersionList";

const PARAM_HEADS = ["파라미터", "의미", "코드 기본값", "허용 범위", "설정값"];

const ParameterTable = ({ method, values, onChange }: { method: CatalogMethod; values?: Record<string, string>; onChange: (key: string, value: string) => void }) => (
  <Table>
    <TableHeader>
      <TableRow>{PARAM_HEADS.map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow>
    </TableHeader>
    <TableBody>
      {parameterRows(method).map((row) => {
        const value = parameterValue(row, values);
        const changed = !row.fixed && row.defaultValue !== null && value !== row.defaultValue;
        return (
          <TableRow key={row.key}>
            <TableCell><span className="grid"><span>{row.label}</span><span className="font-mono text-xs text-muted-foreground">{row.key}</span></span></TableCell>
            <TableCell className="whitespace-normal">{row.meaning}</TableCell>
            <TableCell>{row.defaultValue ?? <Badge variant="warning">기본값 미확정</Badge>}</TableCell>
            <TableCell>{row.rangeLabel}</TableCell>
            <TableCell>
              {row.fixed ? (
                <span className="text-muted-foreground">{row.defaultValue} · 조정 안 함</span>
              ) : (
                <span className="flex items-center gap-2">
                  <Input
                    type="number"
                    className="w-32"
                    aria-label={row.label}
                    value={value}
                    min={row.rule.minimum}
                    max={row.rule.maximum}
                    step={row.rule.type === "integer" ? 1 : "any"}
                    placeholder={row.defaultValue === null ? "미확정" : undefined}
                    onChange={(event) => onChange(row.key, event.target.value)}
                  />
                  {changed && <Badge variant="warning">조정됨</Badge>}
                </span>
              )}
            </TableCell>
          </TableRow>
        );
      })}
    </TableBody>
  </Table>
);

/**
 * 3단계 모델·파라미터: 받은 `methods`(새 학습의 틀에 속한 카탈로그 방법, TrainArea가 거른다) 표시, 학습할 수 없는 방법(modelFrames.ts `methodTrainable`)은
 * 선택 불가(사유 문구 없이 비활성). 파라미터는 선택한 모델만.
 */
export const ModelStep = ({ methods, method, values, onSelect, onChange, onReset }: {
  methods: CatalogMethod[];
  method: CatalogMethod | null;
  values?: Record<string, string>;
  onSelect: (modelKey: string) => void;
  onChange: (key: string, value: string) => void;
  onReset: () => void;
}) => (
  <div className="grid gap-4">
    <RadioGroup value={method ? modelKeyOf(method) : ""} onValueChange={onSelect} aria-label="모델">
      <Table>
        <TableHeader>
          <TableRow>{["선택", "모델", "설명", "상태", "입력 단위"].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow>
        </TableHeader>
        <TableBody>
          {methods.map((item) => (
            <TableRow key={item.methodId} className={cn(!methodTrainable(item) && "text-muted-foreground")}>
              <TableCell><RadioGroupItem value={modelKeyOf(item)} disabled={!methodTrainable(item)} aria-label={`${item.displayName} 선택`} /></TableCell>
              <TableCell><span className="grid"><span>{item.displayName}</span><span className="font-mono text-xs text-muted-foreground">{item.methodId}</span></span></TableCell>
              <TableCell className="whitespace-normal">{METHOD_DESCRIPTIONS[item.methodId] ?? ""}</TableCell>
              <TableCell><MethodStatus status={item.status} /></TableCell>
              <TableCell>{item.inputRequirements.unit ?? "—"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </RadioGroup>
    {method && (
      <div className="grid gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-medium">파라미터 · {method.displayName}</h3>
          <Button size="sm" variant="outline" onClick={onReset}>전체 기본값으로</Button>
        </div>
        <ParameterTable method={method} values={values} onChange={onChange} />
      </div>
    )}
  </div>
);
