/** Mirrors the bounded Python feature_runtime / derived_expression grammar. */
type Obj = Record<string, any>;
const numeric = ['P.success_count', 'P.error_count', 'P.total_count', 'P.retry_count'];
const countColumns = ['SUCCESS_COUNT', 'ERROR_COUNT', 'TOTAL_COUNT', 'RETRY_COUNT'];
const timeUnits = ['ms', 's', 'us'];
function object(value: unknown): value is Obj { return !!value && typeof value === 'object' && !Array.isArray(value); }
function keys(value: Obj, expected: string[]): boolean {
  return Object.keys(value).length === expected.length && expected.every(key => Object.hasOwn(value, key));
}
function fail(): never { throw new Error('unsupported_feature_definition'); }

function derived(ast: unknown, depth = 0, budget = { value: 0 }): { kind: string; unit: string | null } {
  if (!object(ast) || depth > 8 || ++budget.value > 64) return fail();
  const op = ast.op;
  if (op === 'column' && keys(ast, ['op', 'table', 'column'])) {
    if (ast.table !== 'EC_PROCESS_MONITORING') return fail();
    if (countColumns.includes(ast.column)) return { kind: 'number', unit: 'count' };
    if (['START_TIME', 'END_TIME'].includes(ast.column)) return { kind: 'timestamp', unit: null };
    return fail();
  }
  if (op === 'literal' && keys(ast, ['op', 'value'])) {
    if (typeof ast.value === 'boolean') return { kind: 'boolean', unit: 'boolean' };
    if (typeof ast.value === 'number' && Number.isFinite(ast.value)) return { kind: 'number', unit: null };
    return fail();
  }
  const binary = ['subtract', 'divide', 'eq', 'lt', 'lte', 'gt', 'gte', 'and', 'datetime_diff'];
  if (![...binary, 'is_missing', 'convert_unit'].includes(op)) return fail();
  const extra = op === 'datetime_diff' ? ['unit'] : op === 'convert_unit' ? ['from_unit', 'to_unit'] : [];
  if (!keys(ast, ['op', 'args', ...extra]) || !Array.isArray(ast.args) || ast.args.length !== (binary.includes(op) ? 2 : 1)) return fail();
  const children: Array<{ kind: string; unit: string | null }> = ast.args.map((child: unknown) => derived(child, depth + 1, budget));
  if (op === 'is_missing') return { kind: 'boolean', unit: 'boolean' };
  if (op === 'datetime_diff') {
    if (children.some(x => x.kind !== 'timestamp') || !timeUnits.includes(ast.unit)) return fail();
    return { kind: 'number', unit: ast.unit };
  }
  if (op === 'and') {
    if (children.some(x => x.kind !== 'boolean')) return fail();
    return { kind: 'boolean', unit: 'boolean' };
  }
  if (children.some(x => x.kind !== 'number')) return fail();
  if (op === 'convert_unit') {
    if (!timeUnits.includes(ast.from_unit) || !timeUnits.includes(ast.to_unit) || children[0].unit !== ast.from_unit) return fail();
    return { kind: 'number', unit: ast.to_unit };
  }
  if (op === 'subtract') {
    if (children[0].unit !== children[1].unit) return fail();
    return { kind: 'number', unit: children[0].unit };
  }
  if (op === 'divide') return { kind: 'number', unit: children[0].unit !== null && children[0].unit === children[1].unit ? 'ratio' : null };
  return { kind: 'boolean', unit: 'boolean' };
}

export function validateFeatureExpression(value: unknown, depth = 0): void {
  if (!object(value) || depth > 2) return fail();
  if (['group_median', 'group_centered'].includes(value.op) && depth === 0 && keys(value, ['op', 'groupBy', 'value'])) {
    if (value.groupBy !== 'P.adapter_type' || !object(value.value) || ['group_median', 'group_centered'].includes(value.value.op)) return fail();
    validateFeatureExpression(value.value, 0);
    return;
  }
  if (value.op === 'derived' && depth === 0 && keys(value, ['op', 'ast', 'outputType', 'unit', 'provenance'])) {
    const type = derived(value.ast);
    if (!['number', 'boolean'].includes(type.kind) || type.kind !== value.outputType || type.unit !== value.unit || !object(value.provenance)) return fail();
    return;
  }
  if (value.op === 'field' && keys(value, ['op', 'field']) && numeric.includes(value.field)) return;
  if (['add', 'subtract', 'ratio'].includes(value.op) && keys(value, ['op', 'left', 'right'])) {
    validateFeatureExpression(value.left, depth + 1);
    validateFeatureExpression(value.right, depth + 1);
    return;
  }
  return fail();
}
