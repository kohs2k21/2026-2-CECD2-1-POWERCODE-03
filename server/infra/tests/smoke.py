"""Isolated Compose smoke test; never uses real credentials or existing volumes."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compose-file', required=True)
    parser.add_argument('--web-image', required=True)
    parser.add_argument('--api-image', required=True)
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--worker-tag', default='baseline')
    args = parser.parse_args()
    project = 'powercode-ci-21-' + secrets.token_hex(5)
    password = secrets.token_urlsafe(24)
    with tempfile.TemporaryDirectory(prefix=project) as temporary:
        folder = Path(temporary)
        auth = folder / 'auth'
        auth.mkdir()
        files = folder / 'files'
        files.mkdir()
        db_password_file = folder / 'db-password'
        db_password_file.write_text(password, encoding='utf-8')
        db_password_file.chmod(0o600)
        compose_env_file = folder / 'compose.env'
        compose_env_file.write_text('', encoding='utf-8')
        env_file = folder / 'test.env'
        env_file.write_text(
            f'JWT_SECRET={secrets.token_urlsafe(48)}\n'
            'SEED_ADMIN_EMAIL=cd-smoke@example.test\n'
            f'SEED_ADMIN_PASSWORD={password}\n', encoding='utf-8')
        env_file.chmod(0o600)
        env = {**{key: value for key, value in os.environ.items()
                  if not key.startswith('COMPOSE_')}, 'COMPOSE_PROJECT_NAME': project,
               'WEB_IMAGE': args.web_image, 'API_IMAGE': args.api_image,
               'API_ENV_FILE': str(env_file), 'AUTH_DATA_DIR': str(auth),
               'FILE_STORAGE_DIR': str(files), 'TEST_DB_PASSWORD_FILE': str(db_password_file),
               'DATABASE_URL': (f'postgresql://baseline_test:{password}@postgres:5432/baseline_test'
                                if args.baseline else ''),
               **{role.upper() + '_IMAGE': f'powercode-{role}:{args.worker_tag}'
                  for role in ('collector', 'detector', 'trainer', 'llm')},
               'WEB_BIND_ADDRESS': '127.0.0.1', 'WEB_PORT': '0',
               'COMPOSE_ENV_FILES': '', 'COMPOSE_DISABLE_ENV_FILE': 'true'}
        command = ['docker', 'compose', '--project-name', project,
                   '--env-file', str(compose_env_file),
                   '--file', str(Path(args.compose_file).resolve())]
        if args.baseline:
            command += ['--file', str(Path(args.compose_file).resolve().with_name('compose.test.yaml')),
                        '--profile', 'test-db']

        def compose(*parts, expected_returncode=0):
            result = subprocess.run(command + list(parts), env=env,
                                    capture_output=True, text=True, timeout=210)
            if result.returncode != expected_returncode:
                # Compose output can contain environment values; never print it.
                raise RuntimeError('Compose operation failed: ' + parts[0])
            return result.stdout.strip()

        try:
            print('CHECK: isolated application startup', flush=True)
            compose('up', '-d', '--wait', '--wait-timeout', '120',
                    *(['web', 'api', 'postgres'] if args.baseline else ['web', 'api']))
            postgres_id = compose('ps', '-q', 'postgres') if args.baseline else None
            address = compose('port', 'web', '8080').splitlines()[0]
            base = 'http://' + address

            def request(path, body=None, token=None, method=None):
                headers = {'Content-Type': 'application/json'}
                if token:
                    headers['Authorization'] = 'Bearer ' + token
                req = urllib.request.Request(base + path,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers=headers, method=method)
                return urllib.request.urlopen(req, timeout=15)

            with request('/') as response:
                assert response.status == 200
                assert b'<html' in response.read(100000).lower()
            with request('/health') as response:
                assert json.load(response)['status'] == 'OK'
            if args.baseline:
                print('CHECK: empty PostgreSQL and worker image boundaries', flush=True)
                assert compose('exec', '-T', 'postgres', 'psql', '-U', 'baseline_test',
                               '-d', 'baseline_test', '-Atc',
                               "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema')") == '0'
                compose('exec', '-T', 'api', 'node', '-e',
                        "fetch('http://127.0.0.1:5001/health/database').then(r => {if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))")
                for role in ('collector', 'detector', 'trainer', 'llm'):
                    print('CHECK: ' + role + ' unavailable guard and imports', flush=True)
                    compose('run', '--rm', '--no-deps', role, expected_returncode=2)
                    compose('run', '--rm', '--no-deps', role, 'python', '-c',
                            "import pg_queue, collector.storage, detector.input_reader, trainer.lease; "
                            "from pathlib import Path; "
                            "assert not any(p.name == '.env' or p.suffix in {'.pem','.key','.csv','.pt','.safetensors'} for p in Path('/app').rglob('*'))")
                print('CHECK: shared file permissions and CSV dry-run', flush=True)
                compose('run', '--rm', '--no-deps', 'trainer', 'python', '-c',
                        "from pathlib import Path; Path('/app/files/model-fixture').write_text('synthetic-artifact')")
                compose('run', '--rm', '--no-deps', 'detector', 'python', '-c',
                        "from pathlib import Path; import errno; "
                        "assert Path('/app/files/model-fixture').read_text() == 'synthetic-artifact'\n"
                        "try: Path('/app/files/write-forbidden').write_text('x')\n"
                        "except OSError as error: assert error.errno == errno.EROFS\n"
                        "else: raise AssertionError('read-only mount allowed write')")
                compose('exec', '-T', 'api', 'node', '-e',
                        "const fs=require('fs'); if(fs.readFileSync('/app/files/model-fixture','utf8')!=='synthetic-artifact')process.exit(1); "
                        "try{fs.writeFileSync('/app/files/write-forbidden','x');process.exit(1)}catch(error){if(error.code!=='EROFS')process.exit(1)}")
                (files / 'process.csv').write_text(
                    'TRANSACTION_ID,PROCESS_ID,RETRY_COUNT\nsynthetic-t,synthetic-p,0\n', encoding='utf-8')
                compose('run', '--rm', '--no-deps', 'collector', 'python', '-m',
                        'collector.replay_csv', '--process-csv', '/app/files/process.csv',
                        '--dataset-key', 'synthetic-check', '--replay-key', 'check-1', '--rate', '0')
                (files / 'process.csv').unlink()
            with request('/api/auth/login', {
                    'email': 'cd-smoke@example.test', 'password': password}) as response:
                login = json.load(response)
            token = login['token']
            user_id = login['user']['id']
            with request('/api/auth/me', token=token) as response:
                assert json.load(response)['user']['id'] == user_id
            event_id = 'cd-smoke-' + secrets.token_hex(5)
            with request('/api/anomaly/realtime-stream', token=token) as stream:
                assert 'text/event-stream' in stream.headers['Content-Type']
                with request('/api/anomaly/logs', {
                        'logId': event_id, 'anomalyScore': 0.9,
                        'processTimeMs': 2000, 'responseCode': '500'}, token) as response:
                    assert response.status == 201
                # Connection must remain open and the event must pass nginx buffering.
                received = False
                for _ in range(20):
                    line = stream.readline().decode()
                    if line.startswith('data:'):
                        assert json.loads(line[5:])['eventId'] == event_id
                        received = True
                        break
                assert received, 'SSE event not received'
            changed_password = secrets.token_urlsafe(24)
            with request('/api/users/' + user_id,
                         {'password': changed_password}, token, 'PUT') as response:
                assert response.status == 200
            print('CHECK: application recreation and persistence', flush=True)
            compose('up', '-d', '--force-recreate', '--wait', '--wait-timeout', '120', 'api', 'web')
            base = 'http://' + compose('port', 'web', '8080').splitlines()[0]
            with request('/api/auth/login', {
                    'email': 'cd-smoke@example.test', 'password': changed_password}) as response:
                assert json.load(response)['user']['id'] == user_id
            if args.baseline:
                assert compose('ps', '-q', 'postgres') == postgres_id
                assert (files / 'model-fixture').read_text() == 'synthetic-artifact'
                assert compose('exec', '-T', 'postgres', 'psql', '-U', 'baseline_test',
                               '-d', 'baseline_test', '-Atc',
                               "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema')") == '0'
                print('PASS: empty isolated PostgreSQL connection, unchanged DB lifecycle, worker guards/imports/CSV validation and file permissions')
            print('PASS: web/API/login/SSE and account persistence after recreation')
        finally:
            # Unique project created by this test only; no volume/image prune.
            compose('down', '--timeout', '15')


if __name__ == '__main__':
    main()
