import express from 'express';
import cors from 'cors';
import { PORT } from './config/jwt.js';
import authRoutes from './routes/authRoutes.js';
import userRoutes from './routes/userRoutes.js';
import anomalyRouter from './routes/anomalyRoutes.js';
import ruleRouter from './routes/ruleRoutes.js';
import jobRouter from './routes/jobRoutes.js';
import featureSetRouter from './routes/featureSetRoutes.js';
import configRouter from './routes/configRoutes.js';
import pipelineRouter from './routes/pipelineRoutes.js';
import inputRouter from './routes/inputRoutes.js';
import operationsRouter from './routes/operationsRoutes.js';
import analysisRouter from './routes/analysisRoutes.js';
import ruleSetRouter from './routes/ruleSetRoutes.js';
import preparedDatasetRouter from './routes/preparedDatasetRoutes.js';
import { createMeRouter, createNotificationRouter } from './routes/notificationRoutes.js';
import { createActiveModelRouter, createOperationalStateRouter } from './routes/activeModelRoutes.js';
import { checkDatabaseConnection } from './config/database.js';

const app = express();

// 공통 미들웨어
app.use(cors({
  origin: '*', // 개발·테스트용 전체 출처 허용. 운영 환경에서는 허용 출처를 제한한다.
  methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
  allowedHeaders: ['Content-Type', 'Authorization', 'If-Match', 'Idempotency-Key', 'Last-Event-ID'],
  exposedHeaders: ['ETag', 'Location']
}));
app.use('/api/prepared-datasets', preparedDatasetRouter);
// 이상 분석 쓰기 API는 자체 JSON 파서(16KB)·400 오류 형식을 쓰므로 공통 express.json보다 먼저 연결한다.
app.use('/api/analysis', analysisRouter);
// 룰 세트 API도 자체 JSON 파서·오류 형식을 쓴다.
app.use('/api/rule-set', ruleSetRouter);
// 사용자별 설정·알림 API도 자체 JSON 파서(16KB)·오류 형식을 쓴다.
app.use('/api/me', createMeRouter());
app.use('/api/notifications', createNotificationRouter());
app.use(express.json());

// 상태 확인 API
app.get('/health', (req, res) => {
  res.status(200).json({ status: 'OK', timestamp: new Date().toISOString() });
});

app.get('/health/database', async (_req, res) => {
  const connected = await checkDatabaseConnection();
  res.status(connected ? 200 : 503).json({ status: connected ? 'OK' : 'UNAVAILABLE' });
});

// API 라우트 연결
app.use('/api/auth', authRoutes);
app.use('/api/users', userRoutes);
app.use('/api/anomaly', anomalyRouter);
app.use('/api/rules', ruleRouter);
// 입력 변수 세트 등록·필드 목록. 처리하지 않는 요청(세트 목록)은 아래 jobRouter로 넘어간다.
app.use('/api/jobs/feature-sets', featureSetRouter);
app.use('/api/jobs', jobRouter);
app.use('/api/configurations', configRouter);
app.use('/api/pipeline', pipelineRouter);
app.use('/api/inputs', inputRouter);
app.use('/api/operations', operationsRouter);
app.use('/api/active-models', createActiveModelRouter());
app.use('/api/operational-state', createOperationalStateRouter());

// 공통 오류 처리
app.use((err: any, req: express.Request, res: express.Response, next: express.NextFunction) => {
  console.error('Unhandled error:', err);
  res.status(500).json({ message: 'An unexpected error occurred on the server' });
});

// 서버 시작
app.listen(PORT, () => {
  console.log(`==========================================`);
  console.log(`  User Management Service running on:`);
  console.log(`  http://localhost:${PORT}`);
  console.log(`==========================================`);
});
