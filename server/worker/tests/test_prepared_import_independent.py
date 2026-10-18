"""Independent CSV boundary cases, using controlled values only."""
import copy
import csv
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trainer.prepared_contract import MAX_BYTES, validate_manifest
from trainer.prepared_parser import parse_csv, training_eligible
from trainer.prepared_storage import safe_path
from test_prepared_csv import fixture, content


class IndependentPreparedImportTests(unittest.TestCase):
    def test_bom_and_quoted_numeric_values_preserve_vectors(self):
        manifest, rows = fixture()
        data = content(manifest, rows)
        normal = parse_csv(data, manifest)
        self.assertEqual(parse_csv(b'\xef\xbb\xbf'+data, manifest)['normalizedBytes'], normal['normalizedBytes'])
        stream = io.StringIO(newline='')
        writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
        writer.writerow([*manifest['columns'].values(), 'x', 'y']); writer.writerows(rows)
        self.assertEqual(parse_csv(stream.getvalue().encode(), manifest)['normalizedBytes'], normal['normalizedBytes'])

    def test_empty_duplicate_header_false_text_and_unbounded_input_reject(self):
        manifest, rows = fixture()
        data = content(manifest, rows)
        for invalid in (b'', b'\xff', b'\x00', data.replace(b'x,y', b'x,x', 1), b'x'*(MAX_BYTES+1)):
            with self.subTest(case=invalid[:20]), self.assertRaises(ValueError):
                parse_csv(invalid, manifest)
        for value in ('False', '0', ' false ', 'TRUE'):
            changed = copy.deepcopy(rows); changed[0][4] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_csv(content(manifest, changed), manifest)
        changed = copy.deepcopy(manifest); changed['rowCount'] = 10001
        with self.assertRaises(ValueError): validate_manifest(changed)

    def test_missing_or_non_success_status_never_enters_training(self):
        manifest, rows = fixture()
        for status in ('', 'unknown', 'F', 'P', 'N'):
            changed = copy.deepcopy(rows); changed[0][3] = status
            parsed = parse_csv(content(manifest, changed), manifest)
            with self.subTest(status=status): self.assertFalse(training_eligible(parsed['rows'][0]))
        rows[0][4] = ''
        self.assertFalse(training_eligible(parse_csv(content(manifest, rows), manifest)['rows'][0]))

    def test_explicit_row_origin_alias_and_transitive_group_split_reject(self):
        manifest, rows = fixture()
        rows[0][5] = ''
        rows[-1][5] = rows[0][0]
        with self.assertRaisesRegex(ValueError, 'lineage_split_overlap'):
            parse_csv(content(manifest, rows), manifest)
        manifest, rows = fixture()
        rows[1][5] = rows[0][0]
        rows[-1][6] = rows[1][6]
        with self.assertRaisesRegex(ValueError, 'lineage_split_overlap'):
            parse_csv(content(manifest, rows), manifest)

    def test_paths_cannot_cross_root_or_follow_symlink(self):
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            root = Path(temporary); (root/'prepared').mkdir()
            target = Path(outside)/'input.csv'; target.write_bytes(b'controlled')
            for relative in ('../input.csv', 'prepared/../../input.csv', str(target), 'prepared\\input.csv'):
                with self.subTest(relative=relative), self.assertRaises(ValueError): safe_path(root, relative)
            link = root/'prepared'/'link'
            try: link.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest('this Windows account cannot create symlinks: '+str(error.winerror))
            with self.assertRaisesRegex(ValueError, 'path_invalid'): safe_path(root, 'prepared/link/input.csv')


if __name__ == '__main__': unittest.main()
