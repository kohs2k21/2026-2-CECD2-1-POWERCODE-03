// 019: incident grouping tables, called from erd-v03.mjs inside the same disposable cluster.
// One database erd019: 001-018 + 019 (one string each), catalog shape, role grants, real LOGIN roles (detector writes and reads, api reads only),
// append-only for every role and the owner, deferred run FK at COMMIT, and the uniqueness/CHECK boundaries Detector relies on. Small fixture rows only.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

const TABLES = ['run_incident_runs', 'run_incidents', 'run_incident_members', 'run_incident_events'];
const ROLES = ['api', 'collector', 'detector', 'trainer', 'llm'];
const PRIVILEGES = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'];
const EXPECTED_GRANTS = { api: ['SELECT'], detector: ['SELECT', 'INSERT'], collector: [], trainer: [], llm: [] };

export async function incidents019({ Client, port, connection, check, failures, migrations, migrationNames, rolesSql, parity, insert }) {
  const idx = migrationNames.findIndex(n => n.startsWith('019_'));
  assert(idx > 0, '019 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  const rejected = async (db, label, code, sql, params = []) => {
    try { await db.query(sql, params); fail(label, 'accepted'); }
    catch (e) { verdict(e.code === code, `${label} (${code})`, `${e.code} ${e.message}`); }
  };
  const db = 'erd019';
  const admin = connection('postgres'); await admin.connect();
  try { await admin.query(`CREATE DATABASE ${db}`); } finally { await admin.end(); }

  const c = connection(db); await c.connect();
  const logins = {};
  const login = async role => { const a = new Client({ host:'127.0.0.1', port, user:logins[role].user, password:logins[role].password, database:db }); await a.connect(); return a; };
  let d, api;
  try {
    for (const sql of migrations) await c.query(sql);
    check(`${db}: 001 through ${migrationNames.at(-1)} apply, each file as one string`);
    await parity(c);
    await c.query(rolesSql);
    for (const role of ROLES) {
      logins[role] = { user:`qa019_${role}`, password:randomUUID() };
      await c.query(`CREATE ROLE ${logins[role].user} LOGIN PASSWORD '${logins[role].password}' IN ROLE powercode_${role}`);
    }

    // Catalog: constraints, partial unique indexes, deferred run FKs, statement-level immutable triggers.
    const constraints = (await c.query(`SELECT conrelid::regclass::text AS t, conname, contype, condeferrable, condeferred, pg_get_constraintdef(oid) AS def
      FROM pg_constraint WHERE conrelid::regclass::text = ANY($1)`, [TABLES])).rows;
    const runFks = constraints.filter(k => k.contype === 'f' && /\(added_by_run\) REFERENCES run_incident_runs\(id\)/.test(k.def));
    verdict(runFks.length === 3 && runFks.every(k => k.condeferrable && k.condeferred) && new Set(runFks.map(k => k.t)).size === 3,
      `${db}: added_by_run FK on incidents, members and events is DEFERRABLE INITIALLY DEFERRED`, JSON.stringify(runFks));
    const otherFks = constraints.filter(k => k.contype === 'f' && !runFks.includes(k));
    verdict(otherFks.length === 7 && otherFks.every(k => !k.condeferrable), `${db}: the 7 other FKs are immediate`, JSON.stringify(otherFks.map(k => k.def)));
    const pk = Object.fromEntries(constraints.filter(k => k.contype === 'p').map(k => [k.t, k.def]));
    verdict(pk.run_incident_members === 'PRIMARY KEY (decision_id)' && pk.run_incident_events === 'PRIMARY KEY (id)',
      `${db}: members keyed by decision_id alone, events by identity id`, JSON.stringify(pk));
    verdict(constraints.some(k => k.conname === 'run_incidents_identity_key' && k.def === 'UNIQUE (source_id, criteria_version, group_key, opened_at)'),
      `${db}: incident identity UNIQUE (source_id, criteria_version, group_key, opened_at)`);
    const indexes = Object.fromEntries((await c.query(`SELECT indexrelid::regclass::text AS n, pg_get_indexdef(indexrelid) AS def FROM pg_index
      WHERE indrelid::regclass::text = ANY($1)`, [TABLES])).rows.map(r => [r.n, r.def]));
    verdict(/UNIQUE INDEX .* \(incident_id\) WHERE \(kind = 'merged_into'::text\)$/.test(indexes.run_incident_events_merged_idx ?? '')
      && /UNIQUE INDEX .* \(continues_from\) WHERE \(continues_from IS NOT NULL\)$/.test(indexes.run_incidents_continues_idx ?? '')
      && /\(incident_id, occurred_at\)$/.test(indexes.run_incident_members_incident_idx ?? '')
      && /\(source_id, criteria_version, cursor_to DESC\)$/.test(indexes.run_incident_runs_cursor_idx ?? ''),
    `${db}: partial UNIQUE (merged_into, continues_from) and read indexes`, JSON.stringify(indexes));
    verdict(indexes.run_incidents_recent_idx === 'CREATE INDEX run_incidents_recent_idx ON public.run_incidents USING btree (source_id, opened_at DESC, id DESC)',
      `${db}: recent-incidents index (source_id, opened_at DESC, id DESC)`, indexes.run_incidents_recent_idx);
    const triggers = (await c.query(`SELECT tgrelid::regclass::text AS t, tgfoid::regproc::text AS f, tgtype FROM pg_trigger
      WHERE NOT tgisinternal AND tgrelid::regclass::text = ANY($1)`, [TABLES])).rows;
    // tgtype bits: 1 row, 2 before, 4 insert, 8 delete, 16 update, 32 truncate. Expected: statement, before, delete+update+truncate = 2+8+16+32.
    verdict(triggers.length === 4 && new Set(triggers.map(t => t.t)).size === 4 && triggers.every(t => t.f === 'erd_v03_immutable' && t.tgtype === 58),
      `${db}: one BEFORE UPDATE OR DELETE OR TRUNCATE statement trigger (erd_v03_immutable) per table`, JSON.stringify(triggers));

    // Grants: exact matrix over the five runtime roles.
    const grants = {};
    for (const role of ROLES) for (const table of TABLES) {
      const held = [];
      for (const p of PRIVILEGES) if ((await c.query('SELECT has_table_privilege($1,$2,$3) AS ok', [`powercode_${role}`, table, p])).rows[0].ok) held.push(p);
      grants[`${role}.${table}`] = held.join(',');
    }
    const wrong = Object.entries(grants).filter(([k, v]) => v !== EXPECTED_GRANTS[k.split('.')[0]].join(','));
    verdict(wrong.length === 0, `${db}: grants exactly detector SELECT,INSERT and api SELECT on the 4 tables, nothing for collector, trainer, llm`, JSON.stringify(wrong));

    // Fixture rows (owner): one source, two decisions written through the triggers.
    const source = await insert(c, 'ops_sources', { dataset_key:`qa019-${randomUUID()}` });
    const decisions = [];
    for (let i = 0; i < 2; i++) {
      const execution = await insert(c, 'data_executions', { source_id:source.id, layer:'P' });
      const observation = await insert(c, 'data_observations', { source_id:source.id, layer:'P', execution_id:execution.id, revision_no:1 });
      await insert(c, 'data_processes', { observation_id:observation.id });
      const config = await insert(c, 'ml_config_versions', { source_id:source.id });
      decisions.push(await insert(c, 'run_process_decisions', { execution_id:execution.id, process_observation_id:observation.id, config_version_id:config.id,
        status:'complete', rule_status:'succeeded', ml_status:'succeeded', latency_status:'unavailable', source_latency_ms:null, evidence:{ anomalous:true }, decided_at:new Date() }));
    }
    const cursorTo = Number((await c.query('SELECT max(id) AS m FROM run_decision_events')).rows[0].m);

    d = await login('detector');
    api = await login('api');
    const ids = { run:randomUUID(), a:randomUUID(), b:randomUUID(), c:randomUUID() };
    const incident = (id, groupKey, extra = {}) => d.query(`INSERT INTO run_incidents(id,source_id,criteria,criteria_version,group_key,time_basis,opened_at,continues_from,added_by_run)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)`, [id, source.id, extra.criteria ?? '{"windowMinutes":5}', 'incident-v1', groupKey, extra.timeBasis ?? 'decided_at',
      extra.openedAt ?? '2026-11-12T00:00:00Z', extra.continuesFrom ?? null, extra.run ?? ids.run]);

    // Writer order Detector uses: incidents, members, events, then the run row last, one transaction.
    await d.query('BEGIN');
    await d.query('SELECT pg_advisory_xact_lock(81741, 0)');
    await incident(ids.a, 'hub-a');
    await incident(ids.b, 'hub-b');
    await d.query(`INSERT INTO run_incident_members(decision_id,incident_id,occurred_at,occurred_basis,added_by_run) VALUES($1,$2,$3,'decided_at',$4)`, [decisions[0].id, ids.a, decisions[0].decided_at, ids.run]);
    const event = await d.query(`INSERT INTO run_incident_events(incident_id,kind,target_incident_id,added_by_run) VALUES($1,'merged_into',$2,$3) RETURNING id`, [ids.a, ids.b, ids.run]);
    await d.query(`INSERT INTO run_incident_runs(id,source_id,criteria_version,cursor_from,cursor_to,added_members,opened_incidents,merged_incidents,started_at)
      VALUES($1,$2,'incident-v1',0,$3,1,2,1,clock_timestamp())`, [ids.run, source.id, cursorTo]);
    await d.query('COMMIT');
    verdict(event.rows[0].id !== undefined, `${db}: detector login commits incidents, member, event (identity id), then the run row last in one transaction`);
    const counts = Object.fromEntries(await Promise.all(TABLES.map(async t => [t, (await d.query(`SELECT count(*)::int AS n FROM ${t}`)).rows[0].n])));
    verdict(JSON.stringify(counts) === JSON.stringify({ run_incident_runs:1, run_incidents:2, run_incident_members:1, run_incident_events:1 }), `${db}: detector login reads the 4 tables`, JSON.stringify(counts));

    // Deferred FK: a run id that never gets its row fails at COMMIT and nothing stays.
    const ghost = randomUUID(), orphan = randomUUID();
    await d.query('BEGIN');
    await incident(orphan, 'hub-orphan', { run:ghost });
    let commitError = null;
    try { await d.query('COMMIT'); } catch (e) { commitError = e; }
    const left = (await d.query('SELECT count(*)::int AS n FROM run_incidents WHERE id=$1', [orphan])).rows[0].n;
    verdict(commitError?.code === '23503' && left === 0, `${db}: added_by_run without a run row fails at COMMIT (23503), incident not kept`, `${commitError?.code} left=${left}`);

    // Members: one incident per decision.
    const conflict = await d.query(`INSERT INTO run_incident_members(decision_id,incident_id,occurred_at,occurred_basis,added_by_run) VALUES($1,$2,now(),'decided_at',$3) ON CONFLICT (decision_id) DO NOTHING`,
      [decisions[0].id, ids.b, ids.run]);
    verdict(conflict.rowCount === 0, `${db}: duplicate decision_id with ON CONFLICT (decision_id) DO NOTHING inserts 0 rows`, `rowCount=${conflict.rowCount}`);
    await rejected(d, `${db}: duplicate decision_id plain INSERT`, '23505',
      `INSERT INTO run_incident_members(decision_id,incident_id,occurred_at,occurred_basis,added_by_run) VALUES($1,$2,now(),'decided_at',$3)`, [decisions[0].id, ids.b, ids.run]);

    await rejected(d, `${db}: member occurred_basis outside process_time_raw|decided_at|source_log_occurred_at`, '23514',
      `INSERT INTO run_incident_members(decision_id,incident_id,occurred_at,occurred_basis,added_by_run) VALUES($1,$2,now(),'persisted_at',$3)`, [decisions[1].id, ids.b, ids.run]);

    // Events.
    const eventSql = 'INSERT INTO run_incident_events(incident_id,kind,target_incident_id,added_by_run) VALUES($1,$2,$3,$4)';
    await rejected(d, `${db}: second merged_into for the same incident`, '23505', eventSql, [ids.a, 'merged_into', ids.b, ids.run]);
    await rejected(d, `${db}: merged_into self target`, '23514', eventSql, [ids.b, 'merged_into', ids.b, ids.run]);
    await rejected(d, `${db}: merged_into without target`, '23514', eventSql, [ids.b, 'merged_into', null, ids.run]);
    await rejected(d, `${db}: event kind outside merged_into`, '23514', eventSql, [ids.b, 'continues', ids.a, ids.run]);

    // Incidents.
    await rejected(d, `${db}: criteria that is not a JSON object`, '23514', ...incidentArgs(ids, source, { id:randomUUID(), groupKey:'hub-x', criteria:'[1]' }));
    await rejected(d, `${db}: duplicate (source_id, criteria_version, group_key, opened_at)`, '23505', ...incidentArgs(ids, source, { id:randomUUID(), groupKey:'hub-a' }));
    await rejected(d, `${db}: time_basis outside process_time_raw|decided_at|source_log_occurred_at`, '23514', ...incidentArgs(ids, source, { id:randomUUID(), groupKey:'hub-x', timeBasis:'persisted_at' }));
    await incident(ids.c, 'hub-a', { openedAt:'2026-11-12T01:00:00Z', continuesFrom:ids.a, timeBasis:'process_time_raw' });
    check(`${db}: next 1-hour segment continues_from the previous incident (time_basis process_time_raw accepted)`);
    await rejected(d, `${db}: second incident continuing the same predecessor`, '23505', ...incidentArgs(ids, source, { id:randomUUID(), groupKey:'hub-a', openedAt:'2026-11-12T02:00:00Z', continuesFrom:ids.a }));
    const self = randomUUID();
    await rejected(d, `${db}: incident continuing itself`, '23514', ...incidentArgs(ids, source, { id:self, groupKey:'hub-self', continuesFrom:self }));

    // Runs.
    await rejected(d, `${db}: cursor_to < cursor_from`, '23514', `INSERT INTO run_incident_runs(id,source_id,criteria_version,cursor_from,cursor_to,added_members,opened_incidents,merged_incidents,started_at)
      VALUES($1,$2,'incident-v1',5,4,0,0,0,clock_timestamp())`, [randomUUID(), source.id]);

    // Append-only: detector and api lack UPDATE/DELETE (42501), the owner is stopped by the trigger (P0001), TRUNCATE included.
    const api42501 = [], det42501 = [], owner = [];
    for (const table of TABLES) {
      for (const [who, list] of [[d, det42501], [api, api42501]]) for (const sql of [`UPDATE ${table} SET added_by_run=added_by_run`, `DELETE FROM ${table}`]) {
        const statement = table === 'run_incident_runs' ? sql.replace('added_by_run=added_by_run', 'criteria_version=criteria_version') : sql;
        try { await who.query(statement); list.push(`${statement}: accepted`); } catch (e) { if (e.code !== '42501') list.push(`${statement}: ${e.code}`); }
      }
      for (const sql of [`UPDATE ${table} SET created_at=created_at WHERE false`, `DELETE FROM ${table} WHERE false`, `TRUNCATE ${table} CASCADE`]) {
        const statement = table === 'run_incident_runs' ? sql.replace('created_at=created_at', 'finished_at=finished_at') : table === 'run_incident_members' ? sql.replace('created_at=created_at', 'added_at=added_at') : sql;
        try { await c.query(statement); owner.push(`${statement}: accepted`); } catch (e) { if (e.code !== 'P0001') owner.push(`${statement}: ${e.code} ${e.message}`); }
      }
    }
    verdict(det42501.length === 0 && api42501.length === 0, `${db}: detector and api logins cannot UPDATE or DELETE the 4 tables (42501)`, JSON.stringify([...det42501, ...api42501]));
    verdict(owner.length === 0, `${db}: owner UPDATE, DELETE (0-row statements too) and TRUNCATE rejected by the immutable trigger (P0001)`, JSON.stringify(owner));
    await rejected(api, `${db}: api login INSERT`, '42501', `INSERT INTO run_incident_runs(id,source_id,criteria_version,cursor_from,cursor_to,added_members,opened_incidents,merged_incidents,started_at)
      VALUES($1,$2,'incident-v1',0,0,0,0,0,clock_timestamp())`, [randomUUID(), source.id]);
    const apiRead = await Promise.all(TABLES.map(t => api.query(`SELECT count(*)::int AS n FROM ${t}`)));
    verdict(apiRead.every(r => r.rows[0].n > 0), `${db}: api login reads the 4 tables`);
    const total = (await c.query(`SELECT (SELECT count(*) FROM run_incident_runs)+(SELECT count(*) FROM run_incidents)+(SELECT count(*) FROM run_incident_members)+(SELECT count(*) FROM run_incident_events) AS n`)).rows[0].n;
    verdict(Number(total) === 6, `${db}: only the committed rows remain (1 run, 3 incidents, 1 member, 1 event)`, String(total));
  } finally {
    for (const client of [d, api]) await client?.end();
    await c.end();
  }
}

function incidentArgs(ids, source, { id, groupKey, criteria = '{"windowMinutes":5}', timeBasis = 'decided_at', openedAt = '2026-11-12T00:00:00Z', continuesFrom = null }) {
  return [`INSERT INTO run_incidents(id,source_id,criteria,criteria_version,group_key,time_basis,opened_at,continues_from,added_by_run) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
    [id, source.id, criteria, 'incident-v1', groupKey, timeBasis, openedAt, continuesFrom, ids.run]];
}
