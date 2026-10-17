export const JOB_COST_CTE = `WITH jobs AS (
 SELECT j.*,j.result_summary#>'{evaluationEnvelope,execution_cost}' AS cost
 FROM run_jobs j WHERE j.kind IN ('train','evaluate') AND lower(j.input_spec->>'sourceId')=lower($1::text)
 AND (j.input_config_version_id=$2 OR j.result_config_version_id=$2)
 AND j.finished_at >= $3::timestamptz AND j.finished_at < $4::timestamptz
)`;
export const JOB_COST_SQL = `${JOB_COST_CTE}
 SELECT j.kind,m.key,count(*)::integer AS samples,sum(sample.value) AS sum,
 percentile_cont(.5) WITHIN GROUP(ORDER BY sample.value) AS p50,
 percentile_cont(.95) WITHIN GROUP(ORDER BY sample.value) AS p95
 FROM jobs j CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(cost->'metrics')='object'
 THEN cost->'metrics' ELSE '{}'::jsonb END) m
 CROSS JOIN LATERAL (SELECT ${COST_VALUE_SQL} AS value) sample
 WHERE cost->>'metricVersion'='execution-cost-v1' AND cost->>'status'='measured'
 AND m.key=ANY($5::text[]) AND sample.value IS NOT NULL
 AND m.value->>'unit'=CASE WHEN m.key='processRssBytes' THEN 'bytes' WHEN m.key='throughput' THEN 'rows/s' ELSE 'ms' END
 GROUP BY j.kind,m.key`;
export const JOB_COST_SUMMARY_SQL = `${JOB_COST_CTE}
 SELECT kind,count(*)::integer AS total,count(*) FILTER(WHERE status='failed')::integer AS failed,
 max(finished_at) AS "lastFinishedAt",jsonb_agg(DISTINCT cost->'details') FILTER(WHERE cost->'details' IS NOT NULL) AS conditions
 FROM jobs GROUP BY kind`;
import {COST_VALUE_SQL} from './operationalReport.js';

