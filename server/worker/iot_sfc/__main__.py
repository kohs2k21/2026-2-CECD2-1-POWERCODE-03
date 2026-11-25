"""실제 IoT-SFC 소량 입력의 검증·학습·PG 재생 시험 명령."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .adapter import InputError, iter_rows, summary


def main():
    parser = argparse.ArgumentParser(description='IoT-SFC v6 conn 소량 시험. 기존 ESB 운영 구성은 변경하지 않음.')
    sub = parser.add_subparsers(dest='command', required=True)
    sample = sub.add_parser('sample', help='ZIP 첫 1MB만 받아 허용 필드 CSV 생성')
    sample.add_argument('--output', type=Path, required=True)
    sample.add_argument('--zip-prefix', type=Path)
    sample.add_argument('--limit', type=int, default=20_000)
    for name in ('inspect', 'prepare'):
        command = sub.add_parser(name)
        command.add_argument('--csv', type=Path, required=True)
        command.add_argument('--limit', type=int, default=20_000)
        if name == 'prepare':
            command.add_argument('--output', type=Path, required=True)
    for name in ('test', 'replay', 'detect', 'report'):
        command = sub.add_parser(name)
        command.add_argument('--bundle', type=Path, required=True)
        if name == 'replay':
            command.add_argument('--rate', type=float, default=100)
            command.add_argument('--apply', action='store_true', help='별도 IoT 표와 큐에 기록')
    args = parser.parse_args()
    try:
        if args.command == 'sample':
            from .sample import make_sample
            result = make_sample(args.output, prefix=args.zip_prefix, limit=args.limit)
        elif args.command == 'inspect':
            result = summary(list(iter_rows(args.csv, args.limit)))
        elif args.command == 'prepare':
            from .pipeline import prepare
            result = prepare(args.csv, args.output, args.limit)
        elif args.command == 'test':
            from .pipeline import test_bundle
            result = test_bundle(args.bundle)
        elif args.command == 'replay' and not args.apply:
            from .pipeline import load_bundle
            manifest, _ = load_bundle(args.bundle)
            result = {'mode': 'dry-run', 'database_written': False, **manifest['summary']}
        else:
            import psycopg
            from dotenv import load_dotenv
            from . import store
            load_dotenv(Path(__file__).resolve().parents[1] / '.env')
            dsn = os.environ.get('DATABASE_URL')
            if not dsn:
                raise InputError('DATABASE_URL_missing')
            # 각 작업의 transaction()이 실제 커밋 경계가 되도록 바깥 묵시적 트랜잭션을 만들지 않는다.
            with psycopg.connect(dsn, autocommit=True) as conn:
                result = (store.replay(conn, args.bundle, args.rate) if args.command == 'replay'
                          else store.detect(conn, args.bundle) if args.command == 'detect'
                          else store.report(conn, args.bundle))
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except InputError as exc:
        print('IoT 시험 실패: ' + str(exc))
    except Exception as exc:
        # 접속 URI·원문 로그·외부 라이브러리 오류 내용은 출력하지 않는다.
        print('IoT 시험 실패: ' + type(exc).__name__)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
