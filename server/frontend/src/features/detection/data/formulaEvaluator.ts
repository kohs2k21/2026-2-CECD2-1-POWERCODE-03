import {
  FORMULA_LIMITS,
  type CompiledFormula,
  type FormulaAst,
  type FormulaEvaluationOptions,
  type FormulaValue,
} from "./formulaTypes";

const timestamp = (value: FormulaValue): number => {
  if (typeof value !== "string")
    throw new Error("시각은 ISO 문자열과 UTC Z 또는 offset으로 입력해 주세요.");
  const match =
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,3})?(Z|([+-])(\d{2}):(\d{2}))$/.exec(
      value,
    );
  if (!match)
    throw new Error("시각에 유효한 ISO 형식과 UTC Z 또는 offset이 필요합니다.");
  const [year, month, day, hour, minute, second] = match
    .slice(1, 7)
    .map(Number);
  const calendar = new Date(0);
  calendar.setUTCFullYear(year, month, 0);
  if (
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > calendar.getUTCDate() ||
    hour > 23 ||
    minute > 59 ||
    second > 59 ||
    Number(match[9] ?? 0) > 14 ||
    Number(match[10] ?? 0) > 59 ||
    (Number(match[9] ?? 0) === 14 && Number(match[10] ?? 0) !== 0)
  )
    throw new Error("유효하지 않은 원본 시각입니다.");
  const result = Date.parse(value);
  if (!Number.isFinite(result))
    throw new Error("유효하지 않은 원본 시각입니다.");
  return result;
};
const numeric = (value: FormulaValue): number => {
  if (typeof value !== "number" || !Number.isFinite(value))
    throw new Error("유효한 수치 입력이 필요합니다.");
  return value;
};
const finite = (value: number) => {
  if (!Number.isFinite(value)) throw new Error("계산 범위를 벗어났습니다.");
  return value;
};
export const evaluateFormula = (
  compiled: CompiledFormula,
  samples: Readonly<Record<string, FormulaValue>>,
  options: FormulaEvaluationOptions = {},
): { value: FormulaValue; reason: string; unit: string } => {
  const result = (value: FormulaValue, reason: string) => ({
    value,
    reason,
    unit: compiled.unit,
  });
  if (compiled.errors.length || !compiled.ast)
    return result(null, compiled.errors[0] ?? "수식을 확인해 주세요.");
  if (compiled.fitRequired)
    return result(null, "학습 구간의 fit 통계가 없습니다. 계산을 보류합니다.");
  if (
    compiled.requiresTimezone &&
    !["UTC", "Asia/Seoul"].includes(options.timezone ?? "")
  )
    return result(null, "원본 시각의 기준 시간대를 선택해 주세요.");
  const features = new Map(
    compiled.features.map((feature) => [feature.id.toLowerCase(), feature]),
  );
  const cache = new Map<string, FormulaValue>();
  const visited = new Set<string>();
  const read = (id: string, depth: number): FormulaValue => {
    const feature = features.get(id.toLowerCase());
    if (!feature) throw new Error(`입력 속성을 찾을 수 없습니다: ${id}`);
    if (cache.has(feature.id)) return cache.get(feature.id)!;
    if (visited.has(feature.id) || depth > FORMULA_LIMITS.depth)
      throw new Error("순환 참조 또는 의존성 중첩 제한을 확인해 주세요.");
    if (feature.fitRequired || feature.readiness === "fit-required")
      throw new Error("입력 피처의 학습된 전처리 통계가 없습니다.");
    if (feature.readiness === "unconfirmed" || feature.readiness === "deferred")
      throw new Error(`입력 정의를 확인해 주세요: ${feature.name}`);
    visited.add(feature.id);
    let value: FormulaValue;
    if (feature.expression !== undefined) {
      const ast = compiled.dependencyAsts[feature.id];
      if (!ast)
        throw new Error(`컴파일된 입력 정의를 확인해 주세요: ${feature.name}`);
      value = run(ast, depth + 1, {
        timezone: feature.timezone,
        missingPolicy: feature.missingPolicy,
      });
      if (value === null && feature.missingPolicy === "reject")
        throw new Error(`필수 입력 결측으로 계산 보류: ${feature.name}`);
    } else {
      const sample = Object.prototype.hasOwnProperty.call(samples, feature.id)
        ? samples[feature.id]
        : null;
      if (
        sample === null ||
        sample === undefined ||
        (typeof sample === "string" && !sample.trim())
      )
        value = null;
      else if (feature.type === "number") {
        if (
          typeof sample === "string" &&
          !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(sample.trim())
        )
          throw new Error(`유효하지 않은 수치 입력: ${feature.name}`);
        if (typeof sample !== "number" && typeof sample !== "string")
          throw new Error(`수치 타입을 확인해 주세요: ${feature.name}`);
        value = finite(Number(sample));
      } else if (feature.type === "timestamp") {
        timestamp(sample);
        value = sample;
      } else if (feature.type === "boolean") {
        if (typeof sample !== "boolean")
          throw new Error(`boolean 타입을 확인해 주세요: ${feature.name}`);
        value = sample;
      } else {
        if (typeof sample !== "string")
          throw new Error(`문자열 타입을 확인해 주세요: ${feature.name}`);
        value = sample;
      }
    }
    visited.delete(feature.id);
    cache.set(feature.id, value);
    return value;
  };
  const run = (
    node: FormulaAst,
    depth: number,
    scope: FormulaEvaluationOptions = options,
  ): FormulaValue => {
    if (depth > FORMULA_LIMITS.depth)
      throw new Error("수식 중첩은 32단계 이하여야 합니다.");
    if (node.kind === "literal") return node.value;
    if (node.kind === "reference") return read(node.id, depth + 1);
    if (node.kind === "unary") {
      const value = run(node.operand, depth + 1, scope);
      return value === null
        ? null
        : finite((node.operator === "-" ? -1 : 1) * numeric(value));
    }
    if (node.kind === "binary") {
      const a = run(node.left, depth + 1, scope),
        b = run(node.right, depth + 1, scope);
      if (a === null || b === null) return null;
      if (node.operator === "==" || node.operator === "!=") {
        const equal =
          node.comparisonType === "timestamp"
            ? timestamp(a) === timestamp(b)
            : a === b;
        return node.operator === "==" ? equal : !equal;
      }
      if (["<", "<=", ">", ">="].includes(node.operator)) {
        const left = typeof a === "string" ? timestamp(a) : numeric(a),
          right = typeof b === "string" ? timestamp(b) : numeric(b);
        return node.operator === "<"
          ? left < right
          : node.operator === "<="
            ? left <= right
            : node.operator === ">"
              ? left > right
              : left >= right;
      }
      const left = numeric(a),
        right = numeric(b);
      if (node.operator === "/" && right === 0)
        throw new Error("0 분모로 계산할 수 없습니다.");
      return finite(
        node.operator === "+"
          ? left + right
          : node.operator === "-"
            ? left - right
            : node.operator === "*"
              ? left * right
              : left / right,
      );
    }
    if (node.name === "if") {
      const condition = run(node.args[0], depth + 1, scope);
      if (condition === null) return null;
      if (typeof condition !== "boolean")
        throw new Error("if 조건 타입을 확인해 주세요.");
      return run(node.args[condition ? 1 : 2], depth + 1, scope);
    }
    const a = run(node.args[0], depth + 1, scope);
    if (node.name === "is_missing") return a === null;
    if (a === null) return null;
    if (["duration_ms", "duration_s"].includes(node.name)) {
      const b = run(node.args[1], depth + 1, scope);
      if (b === null) return null;
      const duration = timestamp(a) - timestamp(b);
      if (duration < 0) throw new Error("음수 처리시간은 계산을 보류합니다.");
      return finite(duration / (node.name === "duration_s" ? 1000 : 1));
    }
    if (["hour", "month", "weekday", "weekend"].includes(node.name)) {
      const date = new Date(
        timestamp(a) + (scope.timezone === "Asia/Seoul" ? 9 * 3600000 : 0),
      );
      return node.name === "hour"
        ? date.getUTCHours()
        : node.name === "month"
          ? date.getUTCMonth() + 1
          : node.name === "weekday"
            ? date.getUTCDay()
            : [0, 6].includes(date.getUTCDay())
              ? 1
              : 0;
    }
    if (node.name === "log1p") {
      const value = numeric(a);
      if (value < 0) throw new Error("log1p는 비음수 입력만 허용합니다.");
      return finite(Math.log1p(value));
    }
    throw new Error("학습 구간의 fit 통계가 없습니다. 계산을 보류합니다.");
  };
  try {
    const value = run(compiled.ast, 0);
    return result(
      value,
      value === null
        ? options.missingPolicy === "reject"
          ? "필수 입력 결측으로 계산 보류"
          : "원본 NULL은 NULL로 유지"
        : "검증 입력 계산",
    );
  } catch (error) {
    return result(
      null,
      error instanceof Error ? error.message : "계산할 수 없습니다.",
    );
  }
};
