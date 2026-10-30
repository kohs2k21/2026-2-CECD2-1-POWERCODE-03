"""result-view1.0: explicit Model-selected inspection, never intent-based AST correction."""
import copy
VERSION='result-view1.0'
MODES=['value','unavailable','unavailable_or_zero']
def effective_plan(plan):
 p=copy.deepcopy(plan);mode=p.pop('result_use','value');expr=p['expression']
 if mode not in MODES:raise ValueError('Unknown result_use')
 if mode=='unavailable':p['expression']={'op':'is_missing','args':[copy.deepcopy(expr)]}
 elif mode=='unavailable_or_zero':p['expression']={'op':'if','args':[{'op':'is_missing','args':[copy.deepcopy(expr)]},{'op':'literal','value':True},{'op':'eq','args':[copy.deepcopy(expr),{'op':'literal','value':0}]}]}
 return p
def trace(plan):
 return {'policy':VERSION,'selected_by':'explicit_plan_field' if 'result_use'in plan else'legacy_explicit_value_compatibility','result_use':plan.get('result_use','value'),'Model_expression':copy.deepcopy(plan['expression']),'compiled_expression':effective_plan(plan)['expression'],'not_intent_repair':True,'meaning':'unavailable tests a computed result; unavailable_or_zero tests a numeric result for unavailable or known zero; filtered-out rows have no output; execution errors are not caught'}
