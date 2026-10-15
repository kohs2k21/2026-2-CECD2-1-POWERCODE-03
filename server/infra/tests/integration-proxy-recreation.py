"""Isolated Docker test: proxy keeps working after API recreation at a new IP.

Uses existing images only, no database, GPU, credentials or server connection.
Override PROXY_TEST_WEB_IMAGE / PROXY_TEST_API_IMAGE for locally available images.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import Request, urlopen
import uuid

SERVER = '''
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self): self.reply()
    def do_POST(self): self.reply()
    def reply(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        content = json.dumps({'path': self.path, 'method': self.command,
            'body': body.decode(), 'auth': self.headers.get('Authorization')}).encode()
        stream = self.path.startswith('/api/pipeline/events')
        if stream: content = b'id: 1\\nevent: decision\\ndata: {"status":"complete"}\\n\\n'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream' if stream else 'application/json')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)
        self.wfile.flush()
ThreadingHTTPServer(('0.0.0.0', 5001), Handler).serve_forever()
'''


def docker(*args, check=True):
    result = subprocess.run(['docker', *args], capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip())
    if args and args[0] == 'logs':
        return (result.stdout + result.stderr).strip()
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'nginx.conf')
    args = parser.parse_args()
    web_image = os.environ.get('PROXY_TEST_WEB_IMAGE', 'powercode-web:latest')
    api_image = os.environ.get('PROXY_TEST_API_IMAGE', 'powercode-worker:local')
    for image in (web_image, api_image):
        docker('image', 'inspect', image)
    prefix = 'pwc-proxy-qa-' + uuid.uuid4().hex[:10]
    network, api, web, holder = (prefix + suffix for suffix in ('-net', '-api', '-web', '-holder'))
    containers = [api, web, holder]
    try:
        subnet = f'10.253.{int(uuid.uuid4().hex[:2], 16)}.0/24'
        docker('network', 'create', '--subnet', subnet, network)
        def start_api():
            docker('run', '-d', '--name', api, '--network', network, '--network-alias', 'api',
                   '--entrypoint', 'python', api_image, '-u', '-c', SERVER)
        def api_ip():
            info = json.loads(docker('inspect', api))[0]
            return info['NetworkSettings']['Networks'][network]['IPAddress']
        start_api()
        old_ip = api_ip()
        docker('run', '-d', '--name', web, '--network', network, '-p', '127.0.0.1::80',
               '--mount', f'type=bind,src={args.config.resolve(strict=True)},dst=/etc/nginx/conf.d/default.conf,readonly', web_image)
        info = json.loads(docker('inspect', web))[0]
        port = info['NetworkSettings']['Ports']['80/tcp'][0]['HostPort']
        base = 'http://127.0.0.1:' + port
        def request(path, body=None):
            req = Request(base + path, data=body,
                          headers={'Authorization': 'Bearer local-proxy-test', 'Content-Type': 'application/json'})
            with urlopen(req, timeout=3) as response:
                return response.read()
        def ready(check_all=False):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    result = json.loads(request('/api/pipeline/metrics?probe=1'))
                    assert result['path'] == '/api/pipeline/metrics?probe=1'
                    assert result['auth'] == 'Bearer local-proxy-test'
                    if check_all:
                        result = json.loads(request('/api/inputs/source/mode?probe=2', b'{"mode":"paused"}'))
                        assert result['path'] == '/api/inputs/source/mode?probe=2'
                        assert result['method'] == 'POST' and result['body'] == '{"mode":"paused"}'
                        assert json.loads(request('/health/database'))['path'] == '/health/database'
                        assert b'event: decision\n' in request('/api/pipeline/events')
                    return
                except (OSError, AssertionError, ValueError):
                    time.sleep(.5)
            raise AssertionError('proxy did not reach the API within 15 seconds')
        ready()
        docker('rm', '-f', api)
        # Occupy the old address so recreation cannot accidentally reuse it.
        docker('run', '-d', '--name', holder, '--network', network, '--ip', old_ip,
               '--entrypoint', 'python', api_image, '-c', 'import time; time.sleep(120)')
        start_api()
        assert api_ip() != old_ip
        ready(check_all=True)
        print('PASS: API IP changed; proxy recovered without web restart; paths/query/body/auth and SSE preserved')
    except Exception:
        print(docker('logs', '--tail', '30', web, check=False))
        raise
    finally:
        for name in containers:
            docker('rm', '-f', name, check=False)
        docker('network', 'rm', network, check=False)


if __name__ == '__main__':
    main()
