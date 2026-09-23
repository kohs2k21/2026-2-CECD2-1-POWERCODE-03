import crypto from "node:crypto";
import { Request, Response } from "express";
import {
  calculate_single_risk_score,
  classify_risk_level,
  isRawLogData,
  process_log_batch,
  RawLogData,
} from "../services/anomalyService.js";

const streamClients = new Set<Response>();
const CONTRACT_VERSION = 1 as const;

interface AnomalyEvent {
  schemaVersion: typeof CONTRACT_VERSION;
  eventId: string;
  provenance: "backend-ingest";
  detectedAt: string;
  processName?: string;
  channelName?: string;
  transactionId?: string;
  status?: string;
  responseCode: string;
  anomalyScore: number;
  processTimeMs: number;
  riskScore: number;
  riskLevel: 1 | 2 | 3;
  severity: "Info" | "Warning" | "Critical";
}

function toAnomalyEvent(rawLog: RawLogData): AnomalyEvent {
  const riskScore = calculate_single_risk_score(
    rawLog.anomalyScore,
    rawLog.processTimeMs,
    rawLog.responseCode,
  );
  const { riskLevel, severity } = classify_risk_level(riskScore);

  return {
    schemaVersion: CONTRACT_VERSION,
    eventId: rawLog.logId?.trim() || crypto.randomUUID(),
    provenance: "backend-ingest",
    detectedAt: rawLog.detectedAt?.trim() || new Date().toISOString(),
    ...(rawLog.processName ? { processName: rawLog.processName } : {}),
    ...(rawLog.channelName ? { channelName: rawLog.channelName } : {}),
    ...(rawLog.transactionId ? { transactionId: rawLog.transactionId } : {}),
    ...(rawLog.status ? { status: rawLog.status } : {}),
    responseCode: rawLog.responseCode,
    anomalyScore: rawLog.anomalyScore,
    processTimeMs: rawLog.processTimeMs,
    riskScore,
    riskLevel,
    severity,
  };
}

function sendSseEvent(client: Response, event: AnomalyEvent): void {
  client.write(`id: ${event.eventId}\nevent: anomaly\ndata: ${JSON.stringify(event)}\n\n`);
}

export function evaluate_logs(req: Request, res: Response): void {
  try {
    const logs: unknown = req.body?.logs;
    if (!Array.isArray(logs) || !logs.every(isRawLogData)) {
      res.status(400).json({
        code: "invalid_log_payload",
        message: "logs must be an array of valid raw log objects.",
      });
      return;
    }

    const processedLogs = process_log_batch(logs);
    res.status(200).json({
      schemaVersion: CONTRACT_VERSION,
      message: "Successfully evaluated anomaly risk levels.",
      results: processedLogs,
    });
  } catch (error) {
    console.error(
      "Error evaluating logs:",
      error instanceof Error ? error.message : "unknown error",
    );
    res.status(500).json({
      code: "anomaly_evaluation_failed",
      message: "An error occurred while evaluating log anomaly risk levels.",
    });
  }
}

export function register_stream_client(req: Request, res: Response): void {
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");
  res.flushHeaders();

  streamClients.add(res);
  req.on("close", () => {
    streamClients.delete(res);
  });
}

export function receive_realtime_log(req: Request, res: Response): void {
  try {
    const rawLog: unknown = req.body;
    if (!isRawLogData(rawLog)) {
      res.status(400).json({
        code: "invalid_log_payload",
        message: "The request body must be a valid raw log object.",
      });
      return;
    }

    const event = toAnomalyEvent(rawLog);
    for (const client of streamClients) {
      sendSseEvent(client, event);
    }

    res.status(201).json({
      schemaVersion: CONTRACT_VERSION,
      message: "Real-time log received and streamed.",
      log: event,
    });
  } catch (error) {
    console.error(
      "Error receiving real-time log:",
      error instanceof Error ? error.message : "unknown error",
    );
    res.status(500).json({
      code: "anomaly_ingest_failed",
      message: "Failed to process real-time log.",
    });
  }
}
