import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from automation.services.sync.app import Engine, State, SyncError, CREATE, UPDATE, Notion, render_blocks, value
from automation.tests.test_sync import CFG, FakeNotion, FakeGitHub


def text(content, **annotations):
    return {"type": "text", "text": {"content": content}, "annotations": annotations}


def table(rows, header=True, row_header=False):
    block = {"id": "synthetic-table", "type": "table", "has_children": True,
             "table": {"table_width": len(rows[0]), "has_column_header": header,
                       "has_row_header": row_header}}
    children = [{"type": "table_row", "table_row": {"cells": [
        [text(cell)] if isinstance(cell, str) else cell for cell in row]}} for row in rows]
    return block, children


class TableTests(unittest.TestCase):
    def test_header_and_all_rows_preserved_without_mutating_input(self):
        block, rows = table([["평가셋", "이상 사례 수", "생성 방식"],
                             ["개별 지연", "5,000", "Process 실행 시간 증가"],
                             ["혼합 Hard", "1,000", "6유형 포함"]])
        before = copy.deepcopy((block, rows))
        children = unittest.mock.Mock(return_value=rows)
        result = render_blocks([block], children)
        self.assertEqual(result, "| 평가셋 | 이상 사례 수 | 생성 방식 |\n| --- | --- | --- |\n"
                         "| 개별 지연 | 5,000 | Process 실행 시간 증가 |\n| 혼합 Hard | 1,000 | 6유형 포함 |")
        self.assertEqual((block, rows), before)
        children.assert_called_once_with("synthetic-table")

    def test_headerless_first_row_is_data(self):
        block, rows = table([["첫 행", "값"], ["둘째 행", ""]], header=False)
        self.assertEqual(render_blocks([block], lambda _: rows),
                         "|  |  |\n| --- | --- |\n| 첫 행 | 값 |\n| 둘째 행 |  |")

    def test_first_column_header_emphasis(self):
        block, rows = table([["항목", "값"], ["지연", "3초"]], row_header=True)
        self.assertIn("| <strong>지연</strong> | 3초 |", render_blocks([block], lambda _: rows))

    def test_pipes_backslashes_linebreaks_and_inline_formatting(self):
        cell = [text("강조", bold=True), text("\n"),
                {"type": "text", "text": {"content": "문서", "link": {"url": "https://example.com"}}},
                text(" | \\경로\r\n끝")]
        block, rows = table([["열", "코드"], [cell, [text("a|b", code=True)]]])
        result = render_blocks([block], lambda _: rows)
        self.assertIn("**강조**<br>[문서](https://example.com) \\| \\\\경로<br>끝", result)
        self.assertIn("<code>a&#124;b</code>", result)

    def test_code_keeps_literal_backslashes_backticks_and_linebreaks(self):
        block, rows = table([["코드"], [[text("C:\\path | a`b\nsecond", code=True)]]])
        self.assertIn("<code>C&#58;&#92;path &#124; a&#96;b<br>second</code>",
                      render_blocks([block], lambda _: rows))

    def test_code_punctuation_is_literal_in_gfm(self):
        block, rows = table([["코드"], [[text("**x** _y_ a`b`c \\* <br>", code=True)]]])
        self.assertIn("<code>&#42;&#42;x&#42;&#42; &#95;y&#95; a&#96;b&#96;c &#92;&#42; &#60;br&#62;</code>",
                      render_blocks([block], lambda _: rows))

    def test_pipe_in_link_url_is_not_a_cell_boundary(self):
        cell = [{"type": "text", "text": {"content": "문서", "link": {
            "url": "https://example.com/a|b"}}}]
        block, rows = table([["열", "다음 열"], [cell, "보존"]])
        self.assertIn("| [문서](https://example.com/a%7Cb) | 보존 |",
                      render_blocks([block], lambda _: rows))

    def test_first_column_header_with_whitespace(self):
        block, rows = table([["열"], [" leading "]], row_header=True)
        self.assertIn("<strong> leading </strong>", render_blocks([block], lambda _: rows))

    def test_literal_html_in_cell_stays_text(self):
        block, rows = table([["열"], ["<br> & <script>"]])
        self.assertIn("&lt;br&gt; &amp; &lt;script&gt;", render_blocks([block], lambda _: rows))

    def test_table_nested_under_paragraph_and_quote(self):
        block, rows = table([["열"], ["값"]])
        for kind, prefix in (("paragraph", ""), ("quote", "> ")):
            parent = {"id": "parent", "type": kind, "has_children": True,
                      kind: {"rich_text": [text("참고")]}}
            result = render_blocks([parent], lambda key: [block] if key == "parent" else rows)
            self.assertIn(prefix + "| 열 |\n" + prefix + "| --- |\n" + prefix + "| 값 |", result)
            self.assertNotIn("    |", result)

    def test_secret_in_table_cell_still_blocked_without_echo(self):
        block, rows = table([["열"], [[text("ghp_" + "A" * 32, code=True)]]])
        with self.assertRaisesRegex(SyncError, "^POSSIBLE_SECRET_IN_TASK$"):
            render_blocks([block], lambda _: rows)

    def test_table_nested_under_list(self):
        block, rows = table([["열"], ["값"]])
        parent = {"id": "list", "type": "bulleted_list_item", "has_children": True,
                  "bulleted_list_item": {"rich_text": [text("참고")]}}
        result = render_blocks([parent], lambda key: [block] if key == "list" else rows)
        self.assertIn("- 참고\n\n    | 열 |\n    | --- |\n    | 값 |", result)

    def test_invalid_table_refuses_partial_output(self):
        block, rows = table([["열1", "열2"], ["정상", "값"]])
        cases = [(block, []), (block, [rows[0], {"type": "image"}]),
                 (block, [rows[0], {"type": "table_row", "table_row": {"cells": [[]]}}]),
                 (block, [rows[0], {"type": "table_row", "has_children": True,
                                   "table_row": {"cells": [[], []]}}])]
        for width in (0, True, "2"):
            invalid = copy.deepcopy(block)
            invalid["table"]["table_width"] = width
            cases.append((invalid, rows))
        for item, children in cases:
            with self.subTest(item=item, children=children), self.assertRaisesRegex(SyncError, "^INVALID_TABLE$"):
                render_blocks([item], lambda _: children)

    def test_table_preserves_existing_mention_rejection(self):
        block, rows = table([["열"], [[{"type": "mention"}]]])
        with self.assertRaisesRegex(SyncError, "UNSUPPORTED_MENTION"):
            render_blocks([block], lambda _: rows)

    def test_notion_children_pagination_includes_every_table_row(self):
        block, rows = table([["열"], ["값"]])
        notion = Notion("synthetic-token", CFG)
        with patch.object(notion.api, "request", side_effect=[
                {"results": [block], "has_more": False},
                {"results": rows[:1], "has_more": True, "next_cursor": "next"},
                {"results": rows[1:], "has_more": False}]) as request:
            self.assertIn("| 값 |", notion.body("synthetic-page"))
            self.assertIn("start_cursor=next", request.call_args.args[1])


class TableSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = State(Path(self.temp.name) / "state.db")
        self.addCleanup(self.state.db.close)
        self.n, self.g = FakeNotion(), FakeGitHub()
        self.engine = Engine(copy.deepcopy(CFG), self.n, self.g, self.state)

    def test_create_update_and_retry_use_table_without_editing_source(self):
        block, rows = table([["평가셋", "수"], ["개별 지연", "5,000"]])
        before = copy.deepcopy((block, rows))
        with patch.object(self.n, "body", lambda _: render_blocks([block], lambda _: rows)):
            self.assertEqual(self.engine.run()[0]["result"], "synced")
            self.assertIn("| 개별 지연 | 5,000 |", self.g.data[1]["body"])
            self.assertFalse(value(self.n.page, CREATE))
            rows[1]["table_row"]["cells"][1] = [text("6,000")]
            self.n.set(UPDATE, "checkbox", True)
            self.assertEqual(self.engine.run()[0]["result"], "synced")
            self.assertIn("| 개별 지연 | 6,000 |", self.g.data[1]["body"])
            self.assertFalse(value(self.n.page, UPDATE))
            self.n.set(CREATE, "checkbox", True)
            self.assertEqual(self.engine.run()[0]["result"], "synced")
            self.assertEqual(len(self.g.data), 1)
            self.assertEqual(len(self.g.branches), 1)
            self.assertEqual(value(self.n.page, "상태"), "백로그")
        self.assertEqual(block, before[0])
        self.assertEqual(rows[0], before[1][0])

    def test_invalid_table_leaves_existing_issue_and_request_intact(self):
        self.engine.run()
        before = copy.deepcopy(self.g.data)
        block, _ = table([["열"]])
        self.n.set(UPDATE, "checkbox", True)
        with patch.object(self.n, "body", lambda _: render_blocks([block], lambda _: [])):
            result = self.engine.run()[0]
        self.assertEqual(result["code"], "INVALID_TABLE")
        self.assertEqual(self.g.data, before)
        self.assertTrue(value(self.n.page, UPDATE))


if __name__ == "__main__":
    unittest.main()
