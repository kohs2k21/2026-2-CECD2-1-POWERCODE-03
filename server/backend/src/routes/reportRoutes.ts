import { Router, Request, Response, NextFunction } from 'express';
import type { Pool } from 'pg';
import { getDatabasePool } from '../config/database.js';
import { requireAdmin } from '../middleware/auth.js';
import { parseReportFilter, parseCandidateFilter, reportParameters, ReportRequestError, OPERATIONAL_SQL, COVERAGE_SQL,
  DETECTOR_COST_SQL, DETECTOR_COST_KEYS, COST_KEYS, COHORT_SUMMARY_SQL, BACKLOG_SQL, BACKLOG_AT_CUTOFF_SQL, costReport, dailyReport,
  measurement } from '../services/operationalReport.js';
import {JOB_COST_SQL,JOB_COST_SUMMARY_SQL} from '../services/executionCostReport.js';

function route(handler: (req: Request, res: Response) => Promise<void>) {
  return (req: Request, res: Response, next: NextFunction) => {
    void handler(req, res).catch(error => {
      if (error instanceof ReportRequestError) res.status(400).json({code: error.code});
      else next(error);
    });
  };
}

export function createReportRouter(database: () => Pick<Pool, 'connect'> = getDatabasePool) {
  const router = Router();
  router.use(requireAdmin as any);
  router.get('/operational', route(async (req, res) => {
    const filter = parseReportFilter(req.query), parameters = reportParameters(filter);
    const client = await database().connect();
    try {
      await client.query('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY');
      const refs = await client.query(`SELECT c.id FROM ml_config_versions c WHERE c.id=$2 AND c.source_id=$1`, parameters.slice(0,2));
      if (!refs.rowCount) { await client.query('ROLLBACK'); res.status(404).json({code:'source_configuration_not_found'}); return; }
      const zone = await client.query('SELECT 1 FROM pg_timezone_names WHERE name=$1', [filter.timezone]);
      if (!zone.rowCount) throw new ReportRequestError('invalid_report_timezone');
      const days = await client.query(OPERATIONAL_SQL, parameters);
      const coverage = await client.query(COVERAGE_SQL, parameters.slice(0,6));
      const costs = await client.query(DETECTOR_COST_SQL, [...parameters.slice(0,6), DETECTOR_COST_KEYS]);
      const total = await client.query(COHORT_SUMMARY_SQL, parameters.slice(0,6));
      const backlog = await client.query(BACKLOG_SQL, parameters.slice(0,6));
      const atCutoff = (await client.query(BACKLOG_AT_CUTOFF_SQL, parameters.slice(0,6))).rows[0];
      const generated = await client.query('SELECT clock_timestamp() AS "generatedAt"');
      await client.query('COMMIT');
      // Cost coverage uses the full period, independently of the daily page.
      const costTargets = total.rows[0].decisionCount;
      res.json({metricVersion:'operational-v1',reportScope:filter.scope,filter,
        generatedAt:generated.rows[0].generatedAt,cutoff:filter.until,
        policy:{identity:'source/execution_id',revision:'latest_persisted_revision_at_cutoff',
          decision:'latest_same_config_on_selected_revision_before_cutoff',cohort:'decided_at_in_half_open_period',
          day:'decided_at_in_report_timezone',pending:'latest_revision_without_decision; persisted_at_in_period',
          sourceClock:'unverified; source timestamps not used for report buckets',
          crossTab:'both_checker_status_succeeded_and_boolean; descriptive_not_accuracy'},
        items:days.rows.map(dailyReport),pagination:{limit:filter.limit,offset:filter.offset,total:total.rows[0].totalDays},
        decisionCount:costTargets,excluded:coverage.rows[0],
        queue:{scope:'current_snapshot_for_scoped_observations_before_cutoff',observedAt:generated.rows[0].generatedAt,
          queued:measurement(backlog.rows[0].targets ? backlog.rows[0].queued : null,'job',backlog.rows[0].queued,backlog.rows[0].targets,
            backlog.rows[0].targets ? 1 : null,backlog.rows[0].targets ? null : 'no_targets'),
          running:measurement(backlog.rows[0].targets ? backlog.rows[0].running : null,'job',backlog.rows[0].running,backlog.rows[0].targets,
            backlog.rows[0].targets ? 1 : null,backlog.rows[0].targets ? null : 'no_targets'),
          failedJobsInPeriod:measurement(backlog.rows[0].targets ? backlog.rows[0].failed : null,'job',backlog.rows[0].failed,backlog.rows[0].targets,
            backlog.rows[0].targets ? 1 : null,backlog.rows[0].targets ? null : 'no_targets'),
          oldestWaitSeconds:backlog.rows[0].oldestWaitSeconds === null ? null : Number(backlog.rows[0].oldestWaitSeconds),
          atCutoff:{scope:'reconstructed_at_min_cutoff_and_now',asOf:atCutoff.asOf,
            method:'job_created_finished_and_attempt_intervals; queued_and_blocked_not_distinguished',
            waiting:measurement(atCutoff.waiting,'job',atCutoff.waiting,null,1),
            running:measurement(atCutoff.running,'job',atCutoff.running,null,1),
            oldestWaitSeconds:measurement(atCutoff.oldestWaitSeconds === null ? null : Number(atCutoff.oldestWaitSeconds),'seconds',null,null,
              atCutoff.oldestWaitSeconds === null ? null : 1,atCutoff.oldestWaitSeconds === null ? 'no_open_jobs_at_cutoff' : null)}},
        executionCost:{scope:'selected_unique_process_decisions',metrics:costReport(costs.rows,costTargets,DETECTOR_COST_KEYS),
          conditions:{clock:'worker_monotonic',memoryScope:'process_current_rss_sample',warmCold:'mixed_unclassified',
            concurrency:'see_saved_decision_telemetry',resourceLimits:'not_observed',samplingPeriod:'per_decision_completion'}},
        esbOccurrenceToDecisionLatency:measurement(null,'ms',null,null,null,'source_clock_unverified'),
        externalNotificationCount:measurement(null,'notification',null,null,null,'external_notification_delivery_not_instrumented')});
    } catch(error) { await client.query('ROLLBACK'); throw error; }
    finally { client.release(); }
  }));
  router.get('/execution-cost', route(async (req, res) => {
    if (req.query.scope !== 'offline') throw new ReportRequestError('invalid_report_scope');
    if (req.query.limit !== undefined || req.query.offset !== undefined) throw new ReportRequestError('unknown_report_filter');
    const filter = parseReportFilter({...req.query,scope:'live'});
    const parameters = reportParameters(filter).slice(0,4), client = await database().connect();
    try {
      await client.query('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY');
      const refs = await client.query('SELECT 1 FROM ml_config_versions WHERE id=$2 AND source_id=$1', parameters.slice(0,2));
      if (!refs.rowCount) {await client.query('ROLLBACK');res.status(404).json({code:'source_configuration_not_found'});return;}
      const keys = COST_KEYS;
      const costs = await client.query(JOB_COST_SQL,[...parameters,keys]);
      const summaries = await client.query(JOB_COST_SUMMARY_SQL,parameters);
      const generated = await client.query('SELECT clock_timestamp() AS "generatedAt"');
      await client.query('COMMIT');
      const {limit:_limit,offset:_offset,...costFilter} = filter;
      res.json({metricVersion:'execution-cost-v1',reportScope:'offline',filter:{...costFilter,scope:'offline'},generatedAt:generated.rows[0].generatedAt,
        policy:{cohort:'finished_at_in_half_open_period',sample:'one_saved_completed_job_batch; final_attempt_only',
          quantiles:'across_saved_batch_samples; not_combined_item_percentiles',memory:'process_current_rss_samples; not_peak'},
        items:['train','evaluate'].map(kind=>{
          const summary = summaries.rows.find(row=>row.kind===kind), total=summary?.total ?? 0;
          return {kind,targets:total,failed:summary?.failed ?? 0,lastFinishedAt:summary?.lastFinishedAt ?? null,
            conditions:summary?.conditions ?? [],metrics:costReport(costs.rows.filter(row=>row.kind===kind),total,keys)};
        })});
    } catch(error) {await client.query('ROLLBACK');throw error;} finally {client.release();}
  }));
  router.get('/candidates', route(async (req, res) => {
    const filter = parseCandidateFilter(req.query);
    const client = await database().connect();
    try {
      const result = await client.query(`SELECT c.id AS "configVersionId",c.source_id AS "sourceId",c.created_at AS "createdAt",
        to_char(c.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS "cursorCreatedAt",
        c.configuration_spec AS "configurationSpec",c.content_hash AS "contentHash",
        j.result_summary AS "trainingSummary",s.id AS "trainingSnapshotId",
        s.selection_spec AS "selectionSpec",e.summary AS "latestEvaluation"
        FROM ml_config_versions c LEFT JOIN LATERAL (
          SELECT result_summary,input_snapshot_id FROM run_jobs WHERE result_config_version_id=c.id AND kind='train'
          AND status='succeeded' ORDER BY finished_at DESC,id DESC LIMIT 1) j ON true
        LEFT JOIN data_snapshots s ON s.id::text=c.configuration_spec->>'trainingSnapshotId'
        LEFT JOIN LATERAL (SELECT jsonb_build_object('evaluationId',id,'snapshotId',snapshot_id,'purpose',purpose,
          'metricVersion',evaluation_spec_version,'evaluationSpecHash',evaluation_spec_hash,'applicability',applicability,
          'populationCount',population_count,'scoredCount',scored_count,'unavailableCount',unavailable_count,
          'metrics',metrics,'reasonCodes',reason_codes,'completedAt',completed_at) AS summary
          FROM run_evaluations WHERE config_version_id=c.id ORDER BY completed_at DESC,id DESC LIMIT 1) e ON true
        WHERE c.source_id=$1 AND ($2::timestamptz IS NULL OR (c.created_at,c.id)<($2::timestamptz,$3::uuid))
        ORDER BY c.created_at DESC,c.id DESC LIMIT $4`, [filter.sourceId,filter.after?.createdAt ?? null,filter.after?.id ?? null,filter.limit+1]);
      const hasMore = result.rows.length>filter.limit, page = result.rows.slice(0,filter.limit);
      const last = page.at(-1), items = page.map(({cursorCreatedAt,...item})=>item);
      res.json({reportScope:'offline_candidate_history',manualApprovalRequired:true,items,
        pagination:{limit:filter.limit,hasMore,nextCursor:hasMore && last ? Buffer.from(JSON.stringify({createdAt:last.cursorCreatedAt,
          id:last.configVersionId})).toString('base64url') : null}});
    } finally { client.release(); }
  }));
  return router;
}
export default createReportRouter();
