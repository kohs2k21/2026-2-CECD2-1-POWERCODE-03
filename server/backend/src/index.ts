import express from 'express';
import cors from 'cors';
import { PORT } from './config/jwt.js';
import authRoutes from './routes/authRoutes.js';
import userRoutes from './routes/userRoutes.js';
import anomalyRouter from './routes/anomalyRoutes.js';
import { checkDatabaseConnection } from './config/database.js';

const app = express();

// 공통 미들웨어
app.use(cors({
  origin: '*', // 개발·테스트용 전체 출처 허용. 운영 환경에서는 허용 출처를 제한한다.
  methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
  allowedHeaders: ['Content-Type', 'Authorization']
}));
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
