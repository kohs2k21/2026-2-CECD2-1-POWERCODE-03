import { parseFormula } from "./formulaParser";
import {
  FORMULA_LIMITS,
  type CompiledFormula,
  type FormulaAst,
  type FormulaFeature,
  type FormulaValueType,
} from "./formulaTypes";

export const formulaFunctions = [
  {
    name: "if",
    description: "조건에 따른 값 선택",
    example: "if([process.ERROR_COUNT] > 0, 1, 0)",
  },
  {
    name: "log1p",
    description: "비음수 값의 로그 변환",
    example: "log1p([process.TOTAL_COUNT])",
  },
  {
    name: "is_missing",
    description: "결측 여부",
    example: "is_missing([process.END_TIME])",
  },
  {
    name: "duration_ms",
    description: "두 ISO 시각의 차이(ms)",
    example: "duration_ms([process.END_TIME], [process.START_TIME])",
  },
  {
    name: "duration_s",
    description: "두 ISO 시각의 차이(s)",
    example: "duration_s([process.END_TIME], [process.START_TIME])",
  },
  {
    name: "hour",
    description: "기준 시간대의 시간",
    example: "hour([process.START_TIME])",
  },
  {
    name: "month",
    description: "기준 시간대의 월",
    example: "month([process.START_TIME])",
  },
  {
    name: "weekday",
    description: "요일(일요일 0~토요일 6)",
    example: "weekday([process.START_TIME])",
  },
  {
    name: "weekend",
    description: "주말 여부(0/1)",
    example: "weekend([process.START_TIME])",
  },
  {
    name: "train_median",
    description: "학습 구간 중앙값 필요",
    example: "train_median([process.TOTAL_COUNT])",
  },
  {
    name: "frequency_encode",
    description: "학습 구간 빈도 통계 필요",
    example: "frequency_encode([process.PROCESS_ID])",
  },
  {
    name: "robust_z",
    description: "학습 구간 중앙값·산포 통계 필요",
    example: "robust_z([process.TOTAL_COUNT])",
  },
] as const;
type Inferred = {
  type: FormulaValueType | "null";
  unit: string;
  literal?: boolean;
};
const textTypes = new Set(["text", "category", "identifier"]);
export const compileFormula = (
  expression: string,
  inputFeatures: readonly FormulaFeature[],
): CompiledFormula => {
  const features = inputFeatures.map((feature) => ({
    ...feature,
    inputs: feature.inputs && [...feature.inputs],
  }));
  const result: CompiledFormula = {
    version: "formula-v1",
    expression,
    ast: null,
    outputType: null,
    unit: "unitless",
    inputIds: [],
    sampleInputIds: [],
    fitRequired: false,
    requiresTimezone: false,
    errors: [],
    features,
    dependencyAsts: Object.create(null) as Record<string, FormulaAst>,
  };
  const map = new Map<string, FormulaFeature>();
  for (const feature of features) {
    if (map.has(feature.id.toLowerCase())) {
      result.errors.push(`속성 ID가 중복됩니다: ${feature.id}`);
      return result;
    }
    map.set(feature.id.toLowerCase(), feature);
  }
  const visited = new Set<string>();
  const resolved = new Map<string, Inferred>();
  const fitFunctions = ["train_median", "frequency_encode", "robust_z"];
  const times = ["hour", "month", "weekday", "weekend"];
  const addId = (id: string) => {
    if (!result.inputIds.includes(id)) result.inputIds.push(id);
  };
  const numeric = (value: Inferred) => {
    if (value.type !== "number")
      throw new Error("산술 연산에는 수치 속성이 필요합니다.");
  };
  const units = (a: Inferred, b: Inferred) => {
    if (a.unit === b.unit) return a.unit;
    if (a.literal && a.unit === "unitless") return b.unit;
    if (b.literal && b.unit === "unitless") return a.unit;
    throw new Error("두 입력의 단위가 일치해야 합니다.");
  };
  const featureInfo = (feature: FormulaFeature, depth: number): Inferred => {
    if (depth > FORMULA_LIMITS.depth)
      throw new Error("의존성 중첩은 32단계 이하여야 합니다.");
    addId(feature.id);
    if (visited.has(feature.id))
      throw new Error(`순환 참조가 있습니다: ${feature.id}`);
    if (resolved.has(feature.id)) return resolved.get(feature.id)!;
    if (feature.readiness === "unconfirmed" || feature.readiness === "deferred")
      throw new Error(
        `입력 정의를 확인해 주세요: ${feature.name} (${feature.reason ?? feature.readiness})`,
      );
    if (feature.fitRequired || feature.readiness === "fit-required")
      result.fitRequired = true;
    visited.add(feature.id);
    let info: Inferred;
    if (feature.expression !== undefined) {
      const ast = parseFormula(feature.expression);
      info = infer(ast, depth + 1, feature);
      result.dependencyAsts[feature.id] = ast;
    } else {
      for (const id of feature.inputs ?? []) {
        const input = map.get(id.toLowerCase());
        if (!input) throw new Error(`입력 속성을 찾을 수 없습니다: ${id}`);
        featureInfo(input, depth + 1);
      }
      if (!result.sampleInputIds.includes(feature.id))
        result.sampleInputIds.push(feature.id);
      info = { type: feature.type, unit: feature.unit || "unitless" };
    }
    visited.delete(feature.id);
    resolved.set(feature.id, info);
    return info;
  };
  const infer = (
    node: FormulaAst,
    depth: number,
    scope?: FormulaFeature,
  ): Inferred => {
    if (depth > FORMULA_LIMITS.depth)
      throw new Error("수식 중첩은 32단계 이하여야 합니다.");
    if (node.kind === "literal")
      return {
        type:
          node.value === null
            ? "null"
            : typeof node.value === "number"
              ? "number"
              : typeof node.value === "boolean"
                ? "boolean"
                : "text",
        unit: "unitless",
        literal: true,
      };
    if (node.kind === "reference") {
      const feature = map.get(node.id.toLowerCase());
      if (!feature) throw new Error(`입력 속성을 찾을 수 없습니다: ${node.id}`);
      node.id = feature.id;
      return featureInfo(feature, depth + 1);
    }
    if (node.kind === "unary") {
      const info = infer(node.operand, depth + 1, scope);
      numeric(info);
      return info;
    }
    if (node.kind === "binary") {
      const a = infer(node.left, depth + 1, scope),
        b = infer(node.right, depth + 1, scope);
      if (["==", "!=", "<", "<=", ">", ">="].includes(node.operator)) {
        const equality = ["==", "!="].includes(node.operator);
        if (!(
          a.type === b.type ||
          (equality &&
            (a.type === "null" ||
              b.type === "null" ||
              (textTypes.has(a.type) && textTypes.has(b.type))))
        ))
          throw new Error("비교 입력의 타입이 일치해야 합니다.");
        if (!equality && !["number", "timestamp"].includes(a.type))
          throw new Error("크기 비교에는 수치 또는 시각 속성이 필요합니다.");
        if (a.type === "number" && b.type === "number") units(a, b);
        node.comparisonType =
          a.type === "null" ? (b.type === "null" ? undefined : b.type) : a.type;
        return { type: "boolean", unit: "unitless" };
      }
      numeric(a);
      numeric(b);
      const unit = ["+", "-"].includes(node.operator)
        ? units(a, b)
        : node.operator === "/"
          ? a.unit === b.unit
            ? "unitless"
            : b.unit === "unitless"
              ? a.unit
              : `${a.unit}/${b.unit}`
          : a.unit === "unitless"
            ? b.unit
            : b.unit === "unitless"
              ? a.unit
              : `${a.unit}*${b.unit}`;
      return { type: "number", unit };
    }
    if (!formulaFunctions.some((item) => item.name === node.name))
      throw new Error(`허용되지 않는 함수입니다: ${node.name}`);
    if (
      node.args.length !==
      (node.name === "if"
        ? 3
        : ["duration_ms", "duration_s"].includes(node.name)
          ? 2
          : 1)
    )
      throw new Error(`함수 입력 개수를 확인해 주세요: ${node.name}`);
    const args = node.args.map((arg) => infer(arg, depth + 1, scope));
    if (node.name === "if") {
      if (args[0].type !== "boolean")
        throw new Error("if의 조건은 비교 또는 결측 여부여야 합니다.");
      const a = args[1],
        b = args[2];
      if (a.type === "null") return b;
      if (b.type === "null") return a;
      if (a.type !== b.type)
        throw new Error("if의 결과 값 타입이 일치해야 합니다.");
      return { type: a.type, unit: units(a, b) };
    }
    if (node.name === "is_missing")
      return { type: "boolean", unit: "unitless" };
    if (
      ["duration_ms", "duration_s"].includes(node.name) ||
      times.includes(node.name)
    ) {
      if (args.some((arg) => arg.type !== "timestamp"))
        throw new Error("시간 함수에는 시각 속성이 필요합니다.");
      if (times.includes(node.name)) {
        if (scope && !["UTC", "Asia/Seoul"].includes(scope.timezone ?? ""))
          throw new Error(
            `입력 피처의 기준 시간대를 확인해 주세요: ${scope.name}`,
          );
        if (!scope) result.requiresTimezone = true;
      }
      return {
        type: "number",
        unit:
          node.name === "duration_ms"
            ? "ms"
            : node.name === "duration_s"
              ? "s"
              : "unitless",
      };
    }
    if (node.name !== "frequency_encode") numeric(args[0]);
    if (fitFunctions.includes(node.name)) result.fitRequired = true;
    return {
      type: "number",
      unit: node.name === "train_median" ? args[0].unit : "unitless",
    };
  };
  try {
    result.ast = parseFormula(expression);
    const info = infer(result.ast, 0);
    if (info.type === "null")
      throw new Error("최종 수식 결과는 NULL 이외의 타입이 필요합니다.");
    result.outputType = info.type;
    result.unit = info.unit;
  } catch (error) {
    result.errors.push(
      error instanceof Error ? error.message : "수식을 확인해 주세요.",
    );
  }
  return result;
};
