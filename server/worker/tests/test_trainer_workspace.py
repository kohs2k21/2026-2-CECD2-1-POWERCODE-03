"""학습 attempt 임시 폴더 정리 회귀 시험. DB·모델 학습 없이 임시 디렉터리와 가짜 커서만 사용한다."""
import os
import sys
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trainer import workspace


class FakeConn:
    """`run_job_attempts` 조회만 흉내 낸다. 전달한 id 집합을 '임대 유효 running'으로 돌려준다."""

    def __init__(self, live):
        self.live = set(live)
        self.queries = []

    @contextmanager
    def cursor(self):
        conn = self

        class Cursor:
            def execute(self, sql, params):
                conn.queries.append((sql, params))
                self.rows = [(value,) for value in params[0] if value in conn.live]

            def fetchall(self):
                return self.rows

        yield Cursor()


class TrainerWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='powercode-workspace-')
        # Windows TEMP는 8.3 단축 경로일 수 있어 resolve된 경로와 비교한다.
        self.root = Path(self.directory.name).resolve()

    def tearDown(self):
        self.directory.cleanup()

    def test_success_removes_temporary_folder_but_keeps_promoted_files(self):
        attempt = uuid.uuid4()
        with workspace.training_workspace(self.root, attempt) as (output, promoted):
            self.assertEqual(output, self.root / 'tmp' / str(attempt))
            (output / 'model.joblib').write_bytes(b'model')
            (self.root / 'models').mkdir()
            published = self.root / 'models' / 'published.joblib'
            os.replace(output / 'model.joblib', published)
            promoted.append(str(published))
            (output / 'leftover.bin').write_bytes(b'partial')
        self.assertFalse((self.root / 'tmp' / str(attempt)).exists())
        self.assertTrue(published.exists())

    def test_failure_removes_partial_files_and_unpublished_promoted_artifacts(self):
        attempt = uuid.uuid4()
        (self.root / 'fit').mkdir()
        registered = self.root / 'fit' / 'registered-earlier.joblib'
        registered.write_bytes(b'owned by a committed transaction')
        with self.assertRaisesRegex(RuntimeError, 'training_ownership_lost'):
            with workspace.training_workspace(self.root, attempt) as (output, promoted):
                (output / 'fit.joblib').write_bytes(b'fit')
                promoted_fit = self.root / 'fit' / 'unregistered.joblib'
                os.replace(output / 'fit.joblib', promoted_fit)
                promoted.append(str(promoted_fit))
                (output / 'model.joblib').write_bytes(b'partial model')
                raise RuntimeError('training_ownership_lost')
        self.assertFalse((self.root / 'tmp' / str(attempt)).exists())
        self.assertFalse(promoted_fit.exists())
        self.assertTrue(registered.exists(), 'files not promoted by this attempt are never touched')

    def test_connection_loss_keeps_promoted_files_but_still_removes_temporary_folder(self):
        # in-doubt commit: COMMIT 응답 전 연결 단절이면 서버에 등록됐을 수 있으므로 승격 파일은 보존한다.
        import psycopg
        attempt = uuid.uuid4()
        (self.root / 'models').mkdir(); (self.root / 'fit').mkdir()
        with self.assertRaises(psycopg.OperationalError):
            with workspace.training_workspace(self.root, attempt) as (output, promoted):
                for name, directory in (('fit.joblib', 'fit'), ('model.joblib', 'models')):
                    (output / name).write_bytes(b'x')
                    published = self.root / directory / f'{attempt}.joblib'
                    os.replace(output / name, published)
                    promoted.append(str(published))
                (output / 'leftover.bin').write_bytes(b'partial')
                raise psycopg.OperationalError('consuming input failed: server closed the connection unexpectedly')
        self.assertFalse((self.root / 'tmp' / str(attempt)).exists())
        self.assertTrue((self.root / 'fit' / f'{attempt}.joblib').exists())
        self.assertTrue((self.root / 'models' / f'{attempt}.joblib').exists())

    def test_unpublished_cleanup_only_touches_joblib_under_models_or_fit(self):
        (self.root / 'models').mkdir()
        outside = self.root / 'outside.joblib'
        outside.write_bytes(b'x')
        wrong_suffix = self.root / 'models' / 'notes.txt'
        wrong_suffix.write_bytes(b'x')
        self.assertEqual(workspace.discard_unpublished(self.root, [str(outside), str(wrong_suffix), None, 7]), [])
        self.assertTrue(outside.exists() and wrong_suffix.exists())

    def test_discard_never_leaves_tmp_root(self):
        temporary = self.root / 'tmp'
        temporary.mkdir()
        (self.root / 'models').mkdir()
        self.assertFalse(workspace.discard_workspace(self.root, temporary))
        self.assertFalse(workspace.discard_workspace(self.root, self.root / 'models'))
        self.assertFalse(workspace.discard_workspace(self.root, self.root))
        self.assertTrue(temporary.exists() and (self.root / 'models').exists())

    def test_restart_removes_orphans_but_keeps_live_attempt_and_unknown_entries(self):
        temporary = self.root / 'tmp'
        orphan, live, finished = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        for attempt in (orphan, live, finished):
            (temporary / str(attempt)).mkdir(parents=True)
            (temporary / str(attempt) / 'model.joblib').write_bytes(b'partial')
        (temporary / 'not-an-attempt').mkdir()
        (temporary / 'stray.tmp').write_bytes(b'unknown origin')
        conn = FakeConn({live})
        removed = workspace.cleanup_orphan_workspaces(conn, self.root)
        self.assertEqual(sorted(removed), sorted([str(orphan), str(finished)]))
        self.assertTrue((temporary / str(live) / 'model.joblib').exists())
        self.assertFalse((temporary / str(orphan)).exists())
        self.assertFalse((temporary / str(finished)).exists())
        self.assertTrue((temporary / 'not-an-attempt').exists())
        self.assertTrue((temporary / 'stray.tmp').exists())
        self.assertEqual(len(conn.queries), 1)
        self.assertIn("status = 'running'", conn.queries[0][0])
        self.assertIn('lease_until > clock_timestamp()', conn.queries[0][0])

    def test_restart_without_tmp_or_candidates_queries_nothing(self):
        conn = FakeConn(set())
        self.assertEqual(workspace.cleanup_orphan_workspaces(conn, self.root), [])
        (self.root / 'tmp').mkdir()
        (self.root / 'tmp' / 'plain-file').write_bytes(b'')
        self.assertEqual(workspace.cleanup_orphan_workspaces(conn, self.root), [])
        self.assertEqual(conn.queries, [])

    def test_stale_folder_for_same_attempt_is_emptied_before_training(self):
        attempt = uuid.uuid4()
        stale = self.root / 'tmp' / str(attempt)
        stale.mkdir(parents=True)
        (stale / 'model.joblib').write_bytes(b'stale')
        output = workspace.attempt_workspace(self.root, attempt)
        self.assertEqual(output, stale)
        self.assertEqual(list(output.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
