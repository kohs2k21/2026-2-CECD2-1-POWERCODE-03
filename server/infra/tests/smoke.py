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
    args = parser.parse_args()
    project = 'powercode-ci-21-' + secrets.token_hex(5)
    password = secrets.token_urlsafe(24)
    with tempfile.TemporaryDirectory(prefix=project) as temporary:
        folder = Path(temporary)
        auth = folder / 'auth'
        auth.mkdir()
        env_file = folder / 'test.env'
        env_file.write_text(
            f'JWT_SECRET={secrets.token_urlsafe(48)}\n'
            'SEED_ADMIN_EMAIL=cd-smoke@example.test\n'
            f'SEED_ADMIN_PASSWORD={password}\n', encoding='utf-8')
        env_file.chmod(0o600)
        env = {**os.environ, 'COMPOSE_PROJECT_NAME': project,
               'WEB_IMAGE': args.web_image, 'API_IMAGE': args.api_image,
               'API_ENV_FILE': str(env_file), 'AUTH_DATA_DIR': str(auth),
               'WEB_BIND_ADDRESS': '127.0.0.1', 'WEB_PORT': '0',
               'COMPOSE_ENV_FILES': '', 'COMPOSE_DISABLE_ENV_FILE': 'true'}
        command = ['docker', 'compose', '--project-name', project,
                   '--file', str(Path(args.compose_file).resolve())]

        def compose(*parts):
            result = subprocess.run(command + list(parts), env=env,
                                    capture_output=True, text=True, timeout=210)
            if result.returncode:
                # Compose output can contain environment values; never print it.
                raise RuntimeError('Compose operation failed: ' + parts[0])
            return result.stdout.strip()

        try:
            compose('up', '-d', '--wait', '--wait-timeout', '120')
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
            compose('up', '-d', '--force-recreate', '--wait', '--wait-timeout', '120', 'api', 'web')
            base = 'http://' + compose('port', 'web', '8080').splitlines()[0]
            with request('/api/auth/login', {
                    'email': 'cd-smoke@example.test', 'password': changed_password}) as response:
                assert json.load(response)['user']['id'] == user_id
            print('PASS: web/API/login/SSE and account persistence after recreation')
        finally:
            # Unique project created by this test only; no volume/image prune.
            compose('down', '--timeout', '15')


if __name__ == '__main__':
    main()
