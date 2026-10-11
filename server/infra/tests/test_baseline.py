"""Render baseline contracts with synthetic paths; no Docker daemon or DB needed."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('docker'), 'Docker CLI unavailable; Compose contract tests not run')
class BaselineConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='powercode-baseline-config-')
        self.addCleanup(self.temporary.cleanup)
        folder = Path(self.temporary.name)
        for name in ('auth', 'files'):
            (folder / name).mkdir()
        (folder / 'api.env').write_text('JWT_SECRET=synthetic-config-only\n', encoding='utf-8')
        (folder / 'password').write_text('synthetic-config-only', encoding='utf-8')
        (folder / 'compose.env').write_text('', encoding='utf-8')
        self.infra = Path(__file__).resolve().parents[1]
        self.env = {**{key: value for key, value in os.environ.items()
                      if not key.startswith('COMPOSE_')},
                    'API_ENV_FILE': str(folder / 'api.env'), 'AUTH_DATA_DIR': str(folder / 'auth'),
                    'FILE_STORAGE_DIR': str(folder / 'files'),
                    'TEST_DB_PASSWORD_FILE': str(folder / 'password'),
                    'DATABASE_NETWORK': 'synthetic-existing-network',
                    'DATABASE_URL': 'postgresql://synthetic:synthetic@postgres:5432/baseline_test',
                    'WEB_IMAGE': 'powercode-web:config-test', 'API_IMAGE': 'powercode-api:config-test',
                    'WEB_BIND_ADDRESS': '127.0.0.1', 'WEB_PORT': '18080',
                    'COMPOSE_DISABLE_ENV_FILE': 'true'}
        self.command = ['docker', 'compose', '--env-file', str(folder / 'compose.env'),
                        '--project-name', 'powercode-config-review', '--profile', '*',
                        '-f', str(self.infra / 'compose.baseline.yaml')]

    def render(self, override=None):
        command = self.command + (['-f', str(self.infra / override)] if override else [])
        result = subprocess.run(command + ['config', '--format', 'json'], env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, 'Compose rendering failed; output withheld')
        return json.loads(result.stdout)

    def test_app_has_six_services_without_db_lifecycle(self):
        services = self.render()['services']
        self.assertEqual(set(services), {'web', 'api', 'collector', 'detector', 'trainer', 'llm'})
        for role in ('collector', 'detector', 'trainer', 'llm'):
            self.assertEqual(services[role]['profiles'], [role])
            self.assertEqual(services[role]['build']['args']['SERVICE'], role)
            self.assertEqual(services[role]['restart'], 'no')
            self.assertNotIn('healthcheck', services[role])
            mount = services[role]['volumes'][0]
            self.assertEqual(mount.get('read_only', False), role != 'trainer')
            self.assertFalse(mount['bind']['create_host_path'])
        self.assertEqual(services['web']['depends_on'],
                         {'api': {'condition': 'service_healthy', 'required': True}})
        self.assertNotIn('profiles', services['web'])
        self.assertNotIn('profiles', services['api'])

    def test_isolated_database_has_no_host_data_or_sql(self):
        model = self.render('compose.test.yaml')
        self.assertEqual(len(model['services']), 7)
        postgres = model['services']['postgres']
        self.assertEqual(postgres['profiles'], ['test-db'])
        self.assertEqual(postgres['tmpfs'], ['/var/lib/postgresql/data'])
        self.assertFalse(postgres.get('ports'))
        self.assertFalse(postgres.get('volumes'))
        self.assertEqual(postgres['environment']['POSTGRES_PASSWORD_FILE'],
                         '/run/secrets/test_db_password')
        self.assertNotIn('POSTGRES_PASSWORD', postgres['environment'])
        for service in model['services'].values():
            for mount in service.get('volumes', []):
                self.assertNotIn('docker-entrypoint-initdb.d', mount['target'])

    def test_existing_db_network_is_external_without_db_service(self):
        model = self.render('compose.external-db.yaml')
        self.assertNotIn('postgres', model['services'])
        self.assertTrue(model['networks']['database']['external'])
        self.assertEqual(model['networks']['database']['name'], 'synthetic-existing-network')
        for role in ('api', 'collector', 'detector', 'trainer', 'llm'):
            self.assertEqual(set(model['services'][role]['networks']), {'default', 'database'})

    def test_gpu_reservation_is_opt_in_and_keeps_profiles(self):
        baseline = self.render()
        model = self.render('compose.gpu.yaml')
        for role in ('detector', 'trainer', 'llm'):
            self.assertNotIn('deploy', baseline['services'][role])
            device = model['services'][role]['deploy']['resources']['reservations']['devices'][0]
            self.assertEqual(device, {'capabilities': ['gpu'], 'driver': 'nvidia', 'count': 1})
            self.assertEqual(model['services'][role]['profiles'], [role])


if __name__ == '__main__':
    unittest.main()
