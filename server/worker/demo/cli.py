"""CPU replay commands. Credentials are environment variables, never CLI output."""
import argparse
import json
import os
import sys
import time
from pathlib import Path
import psycopg
from demo.runtime import prepare, play, preflight, status
from demo.sources import postgres_sources, csv_rows, numeric_features
from collector.replay_csv import CsvInput, _check_headers


def main(argv=None):
    parser = argparse.ArgumentParser(description='Historical Process replay into the real CPU Detector')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('preflight')
    commands.add_parser('sources')
    check = commands.add_parser('check-csv')
    capture = commands.add_parser('prepare')
    for command in (check, capture):
        command.add_argument('--process-csv', default=os.environ.get('DEMO_PROCESS_CSV'))
        command.add_argument('--transaction-csv', default=os.environ.get('DEMO_TRANSACTION_CSV'))
        command.add_argument('--body-csv', default=os.environ.get('DEMO_BODY_CSV'))
        command.add_argument('--baseline-limit', type=int, default=5000)
        command.add_argument('--limit', type=int, default=1000000)
    capture.add_argument('--input', choices=['csv', 'postgres'], required=True)
    capture.add_argument('--run-key', required=True)
    capture.add_argument('--source-id')
    capture.add_argument('--actor-id', default='demo-operator')
    capture.add_argument('--deadline-ms', type=int, default=3000)
    sender = commands.add_parser('play')
    sender.add_argument('--run-key', required=True)
    sender.add_argument('--pass-key', required=True)
    sender.add_argument('--rate', type=float, default=10)
    sender.add_argument('--repeat', type=int, default=1)
    report = commands.add_parser('status')
    report.add_argument('--run-key', required=True)
    report.add_argument('--pass-key')
    report.add_argument('--watch-seconds', type=float, default=0)
    report.add_argument('--every-seconds', type=float, default=2)
    args = parser.parse_args(argv)
    dsn = os.environ.get('DATABASE_URL')
    source_dsn = os.environ.get('DEMO_SOURCE_DATABASE_URL') or dsn
    try:
        if args.command == 'check-csv':
            if not args.process_csv:
                raise ValueError('process_csv_filename_required')
            inputs = [CsvInput(kind, Path(path)) for kind, path in
                (('P',args.process_csv), ('T',args.transaction_csv), ('BODY',args.body_csv)) if path]
            _check_headers(inputs)
            rows = []
            count = 0
            for row in csv_rows(Path(args.process_csv), args.limit):
                count += 1
                if len(rows) < args.baseline_limit:
                    rows.append(row)
            features = numeric_features(rows)
            output = {'ready': count >= 1000, 'processRows': count,
                'referenceRows':len(rows), 'featureNames':[x['name'] for x in features],
                'contextFilesChecked':len(inputs)-1, 'writes':False}
            print(json.dumps(output, ensure_ascii=False))
            return 0 if output['ready'] else 2
        if not dsn:
            raise ValueError('DATABASE_URL_required')
        if args.command == 'preflight':
            with psycopg.connect(dsn, options='-c default_transaction_read_only=on') as conn:
                output = preflight(conn)
            print(json.dumps(output))
            return 0 if output['ready'] else 2
        if args.command == 'sources':
            output = postgres_sources(source_dsn)
        elif args.command == 'prepare':
            if args.input == 'csv':
                if not args.process_csv:
                    raise ValueError('process_csv_filename_required')
                _check_headers([CsvInput(kind,Path(path)) for kind,path in
                    (('P',args.process_csv),('T',args.transaction_csv),('BODY',args.body_csv)) if path])
            elif not args.source_id:
                raise ValueError('original_source_id_required')
            output = prepare(dsn,Path(os.environ.get('ARTIFACT_ROOT','/artifacts')),
                key=args.run_key,kind=args.input,process_csv=args.process_csv if args.input=='csv' else None,
                source_dsn=source_dsn,origin_source=args.source_id,limit=args.limit,
                baseline_limit=args.baseline_limit,actor=args.actor_id,deadline_ms=args.deadline_ms,
                transaction_csv=args.transaction_csv if args.input=='csv' else None,
                body_csv=args.body_csv if args.input=='csv' else None)
        elif args.command == 'play':
            output = play(dsn,key=args.run_key,pass_key=args.pass_key,rate=args.rate,repeat=args.repeat)
        else:
            if not 0 <= args.watch_seconds <= 86400 or not .1 <= args.every_seconds <= 60:
                raise ValueError('invalid_watch_settings')
            until = time.monotonic()+args.watch_seconds
            while True:
                output = status(dsn,args.run_key,args.pass_key)
                if time.monotonic() >= until:
                    break
                print(json.dumps(output,ensure_ascii=False),flush=True)
                time.sleep(min(args.every_seconds,max(0,until-time.monotonic())))
        print(json.dumps(output,ensure_ascii=False),flush=True)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # Never print DB URLs, SQL parameters, raw values or psycopg's connection details.
        code = str(exc) if type(exc) is ValueError and str(exc).replace('_','').isalnum() else type(exc).__name__
        print(json.dumps({'errorCode':code}),file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
