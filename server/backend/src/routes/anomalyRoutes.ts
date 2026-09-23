import { Router } from "express";
import {
  evaluate_logs,
  register_stream_client,
  receive_realtime_log,
} from "../controllers/anomalyController.js";
import { authenticateToken, requireAdmin } from "../middleware/auth.js";

const anomalyRouter = Router();

// Every anomaly operation requires a valid Bearer token.
anomalyRouter.use(authenticateToken as any);

// POST /api/anomaly/evaluate-risk (authenticated user)
anomalyRouter.post("/evaluate-risk", evaluate_logs);

// GET /api/anomaly/realtime-stream (SSE, authenticated user)
anomalyRouter.get("/realtime-stream", register_stream_client);

// POST /api/anomaly/logs (admin-only ingestion)
anomalyRouter.post("/logs", requireAdmin as any, receive_realtime_log);

export default anomalyRouter;
