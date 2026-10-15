"""Operator CLI for the shared input (no separate demo model/training shortcut)."""
import argparse
import json
import os
import sys
import psycopg
from collector.input_store import ensure_source,stage_csv,switch


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-key',default=os.environ.get('COLLECTOR_SOURCE_KEY','customer-esb'))
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('init');commands.add_parser('status')
    stage=commands.add_parser('stage-csv');stage.add_argument('--replay-key',required=True)
    for flag,env,filename in [('transaction','INPUT_TRANSACTION_CSV','EC_TRANSACTION_MONITORING.csv'),
        ('process','INPUT_PROCESS_CSV','EC_PROCESS_MONITORING.csv'),('message','INPUT_MESSAGE_CSV','EC_MESSAGE_MONITORING.csv'),
        ('body','INPUT_BODY_CSV','EC_MESSAGE_BODY.csv')]:
        stage.add_argument('--'+flag,default=os.environ.get(env,'/inputs/'+filename))
    stage.add_argument('--limit-per-file',type=int,default=1000000)
    choose=commands.add_parser('select');choose.add_argument('mode',choices=['api','csv','paused'])
    choose.add_argument('--expected-revision',required=True,type=int)
    choose.add_argument('--replay-id');choose.add_argument('--rate',type=float,default=10)
    choose.add_argument('--repeat',type=int,default=1);choose.add_argument('--poll-seconds',type=float,default=2)
    choose.add_argument('--restart',action='store_true');choose.add_argument('--config',help='Config UUID for validation, or active for approved global config')
    args=parser.parse_args()
    try:
        with psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=10) as conn:
            sid=ensure_source(conn,args.source_key);conn.commit()
            if args.command=='stage-csv':
                rid=stage_csv(conn,sid,args.replay_key,{k:getattr(args,a) for k,a in [('T','transaction'),('P','process'),('M','message'),('B','body')] if getattr(args,a)},args.limit_per_file)
                conn.commit();output={'sourceId':str(sid),'replayId':str(rid),'modeChanged':False}
            elif args.command=='select':
                settings={'pollSeconds':args.poll_seconds} if args.mode=='api' else {'replayId':args.replay_id,'rate':args.rate,'repeat':args.repeat}
                output=switch(conn,sid,mode=args.mode,expected_revision=args.expected_revision,actor='input-operator-cli',
                    settings=settings,config='keep' if args.config is None else None if args.config=='active' else args.config,restart=args.restart)
                conn.commit()
            else:
                control=conn.execute("SELECT mode,revision,run_id,config_version_id FROM ops_input_controls WHERE source_id=%s",(sid,)).fetchone()
                runs=conn.execute('SELECT id,mode,status,next_position,accepted_count,duplicate_count,last_error FROM ops_input_runs WHERE source_id=%s ORDER BY created_at DESC LIMIT 10',(sid,)).fetchall()
                replays=conn.execute('SELECT id,replay_key,record_count,layer_counts FROM data_input_replays WHERE source_id=%s AND status=%s',(sid,'ready')).fetchall()
                output={'sourceId':str(sid),'mode':control[0],'revision':control[1],'runId':str(control[2]) if control[2] else None,
                    'configVersionId':str(control[3]) if control[3] else None,'runs':runs,'replays':replays}
        print(json.dumps(output,default=str,ensure_ascii=False));return 0
    except Exception as exc:
        code=str(exc) if type(exc) is ValueError and str(exc).replace('_','').isalnum() else type(exc).__name__
        print(json.dumps({'errorCode':code}),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
