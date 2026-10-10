import type { FeatureReadiness, RawSource, ValueType } from "./types";

export type FormulaValueType = ValueType | "boolean";
export type FormulaValue = number | string | boolean | null;
export type FormulaFeature = {
  id: string;
  name: string;
  type: FormulaValueType;
  unit: string;
  source?: RawSource | "derived";
  expression?: string;
  inputs?: string[];
  fitRequired?: boolean;
  readiness?: FeatureReadiness;
  reason?: string;
  timezone?: string;
  missingPolicy?: "preserve" | "reject";
};
export type FormulaAst =
  | { kind: "literal"; value: FormulaValue }
  | { kind: "reference"; id: string }
  | { kind: "unary"; operator: "+" | "-"; operand: FormulaAst }
  | {
      kind: "binary";
      operator: string;
      left: FormulaAst;
      right: FormulaAst;
      comparisonType?: FormulaValueType;
    }
  | { kind: "call"; name: string; args: FormulaAst[] };
export type CompiledFormula = {
  version: "formula-v1";
  expression: string;
  ast: FormulaAst | null;
  outputType: FormulaValueType | null;
  unit: string;
  inputIds: string[];
  sampleInputIds: string[];
  fitRequired: boolean;
  requiresTimezone: boolean;
  errors: string[];
  features: FormulaFeature[];
  dependencyAsts: Record<string, FormulaAst>;
};
export type FormulaEvaluationOptions = {
  timezone?: string;
  missingPolicy?: "preserve" | "reject";
};
export const FORMULA_LIMITS = { length: 2048, tokens: 512, depth: 32 } as const;
