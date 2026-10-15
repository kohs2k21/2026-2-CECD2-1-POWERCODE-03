import { Router, Request, Response, NextFunction } from 'express';
import { authenticateToken, AuthenticatedRequest } from '../middleware/auth.js';
import { InvalidRuleError, parseExpectedVersion, parseRuleDraft, parseRuleKey } from '../rules/ruleContract.js';
import { getRule, getRuleVersion, listRules, listRuleVersions, RuleConflictError, writeRule } from '../rules/ruleRepository.js';

const router = Router();
router.use(authenticateToken as any);

function asyncRoute(handler: (req: AuthenticatedRequest, res: Response) => Promise<void>) {
  return (req: Request, res: Response, next: NextFunction): void => {
    void handler(req as AuthenticatedRequest, res).catch(next);
  };
}

function page(value: unknown, defaultValue: number, maximum: number): number {
  if (value === undefined) return defaultValue;
  if (typeof value !== 'string' || !/^(0|[1-9]\d*)$/.test(value)) {
    throw new InvalidRuleError('invalid_pagination');
  }
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number > maximum) throw new InvalidRuleError('invalid_pagination');
  return number;
}

function sendRule(res: Response, rule: Awaited<ReturnType<typeof getRule>>, status = 200): void {
  if (!rule) {
    res.status(404).json({ code: 'rule_not_found' });
    return;
  }
  res.setHeader('ETag', `"${rule.version}"`);
  res.status(status).json(rule);
}

router.get('/', asyncRoute(async (req, res) => {
  const limit = page(req.query.limit, 50, 100);
  const offset = page(req.query.offset, 0, 100000);
  if (limit < 1 || (req.query.includeRetired !== undefined && req.query.includeRetired !== 'true' && req.query.includeRetired !== 'false')) {
    throw new InvalidRuleError('invalid_pagination');
  }
  res.json({ items: await listRules(limit, offset, req.query.includeRetired === 'true'), limit, offset });
}));

router.post('/', asyncRoute(async (req, res) => {
  if (!req.body || typeof req.body !== 'object' || Array.isArray(req.body)) {
    throw new InvalidRuleError('invalid_rule_payload');
  }
  const { ruleKey, ...draft } = req.body;
  const key = parseRuleKey(ruleKey);
  const documents = parseRuleDraft(draft);
  const rule = await writeRule(key, req.user!.id, 'create', documents);
  res.setHeader('Location', `/api/rules/${key}`);
  sendRule(res, rule, 201);
}));

router.get('/:ruleKey/versions', asyncRoute(async (req, res) => {
  const key = parseRuleKey(req.params.ruleKey);
  const limit = page(req.query.limit, 50, 100);
  const offset = page(req.query.offset, 0, 100000);
  if (limit < 1) throw new InvalidRuleError('invalid_pagination');
  if (!(await getRule(key))) {
    res.status(404).json({ code: 'rule_not_found' });
    return;
  }
  res.json({ items: await listRuleVersions(key, limit, offset), limit, offset });
}));

router.get('/:ruleKey/versions/:version', asyncRoute(async (req, res) => {
  const key = parseRuleKey(req.params.ruleKey);
  const version = page(req.params.version, 0, 2147483647);
  if (version < 1) throw new InvalidRuleError('invalid_rule_version');
  sendRule(res, await getRuleVersion(key, version));
}));

router.get('/:ruleKey', asyncRoute(async (req, res) => {
  sendRule(res, await getRule(parseRuleKey(req.params.ruleKey)));
}));

router.put('/:ruleKey', asyncRoute(async (req, res) => {
  const key = parseRuleKey(req.params.ruleKey);
  const expected = parseExpectedVersion(req.headers['if-match']);
  const documents = parseRuleDraft(req.body);
  sendRule(res, await writeRule(key, req.user!.id, 'replace', documents, expected));
}));

router.delete('/:ruleKey', asyncRoute(async (req, res) => {
  const key = parseRuleKey(req.params.ruleKey);
  const expected = parseExpectedVersion(req.headers['if-match']);
  sendRule(res, await writeRule(key, req.user!.id, 'retire', undefined, expected));
}));

router.use((error: unknown, _req: Request, res: Response, _next: NextFunction) => {
  if (error instanceof InvalidRuleError) {
    res.status(error.code === 'if_match_required' ? 428 : 400).json({ code: error.code });
    return;
  }
  if (error instanceof RuleConflictError) {
    const status = error.code === 'rule_not_found' ? 404
      : error.code === 'rule_version_conflict' ? 412 : 409;
    res.status(status).json({ code: error.code });
    return;
  }
  // DB 오류에는 접속 정보가 포함될 수 있으므로 상세 내용을 응답에 싣지 않는다.
  res.status(503).json({ code: 'rule_storage_unavailable' });
});

export default router;
