import { Router, Request, Response, NextFunction } from 'express';
import { authenticateToken } from '../middleware/auth.js';
import { GENERATION_SERVICE_UNAVAILABLE, generationAvailability } from '../services/generationAvailability.js';

/**
 * LLM 생성 서비스 가용성 조회(llm-recommendation-job.md '생성 서비스 가용성' 절). 로그인 사용자.
 * GET /api/llm/status → `{available, checkedAt, errorCode}`. 꺼짐이면 errorCode는 늘 `generation_service_unavailable`.
 * 확인 결과는 POST /api/jobs의 LLM 작업 접수 거절과 같은 10초 캐시를 쓴다.
 */
const router = Router();
router.use(authenticateToken as any);

router.get('/status', (_req: Request, res: Response, next: NextFunction) => {
  generationAvailability().check().then(({ available, checkedAt }) => {
    res.set('Cache-Control', 'no-store');
    res.json({ available, checkedAt, errorCode: available ? null : GENERATION_SERVICE_UNAVAILABLE });
  }).catch(next);
});

export default router;
