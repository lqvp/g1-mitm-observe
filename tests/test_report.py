"""report.py（CLI）のテスト。標準ライブラリのみ・ネットワークなし。

実際に子プロセスで起動し、終了コードと標準出力／標準エラーを見る。
"""

import ast
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PY = REPO_ROOT / "report.py"

FIXTURE = {
    "schema": "g1-observe.v1",
    "updatedAt": "2026-11-25T10:23:00+0900",
    "responseCount": 7,
    "matchedResponses": 5,
    "nonTextResponses": 2,
    "skuCount": 4,
    "skus": [
        {"id": "g1.2tb.ai.1month_eft", "count": 1, "lastSeen": "2026-11-25T10:20:01+0900"},
        {"id": "g1.200gb.ai.1month_eft", "count": 3, "lastSeen": "2026-11-25T10:21:02+0900"},
        {"id": "g1.2tb", "count": 3, "lastSeen": "2026-11-25T10:22:03+0900"},
        {"id": "com.google.one.plan.free", "count": 5, "lastSeen": "2026-11-25T10:23:04+0900"},
    ],
    "recent": [],
}

# count 降順、同数なら SKU 昇順。
EXPECTED_ORDER = [
    "com.google.one.plan.free",
    "g1.200gb.ai.1month_eft",
    "g1.2tb",
    "g1.2tb.ai.1month_eft",
]

_ROW_RE = re.compile(r"^(?P<id>\S+)\s+(?P<count>\d+)\s+(?P<lastSeen>\S+)$")


def table_rows(text):
    """表の行を (SKU, count, lastSeen) で返す。見出し行の次から読む。"""
    rows = []
    in_table = False
    for line in text.splitlines():
        if not in_table:
            if line.split()[:1] == ["SKU"]:
                in_table = True
            continue
        match = _ROW_RE.match(line)
        if not match:
            break
        rows.append((match.group("id"), int(match.group("count")), match.group("lastSeen")))
    return rows


class ReportCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.path = self.tmpdir / "g1-observe.json"
        self.write_json(self.path, FIXTURE)

    @staticmethod
    def write_json(path, data):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_cli(self, *args):
        # cwd を一時ディレクトリにして、リポジトリ側に何も作らないことを保証する。
        return subprocess.run(
            [sys.executable, str(REPORT_PY)] + [str(arg) for arg in args],
            capture_output=True,
            text=True,
            cwd=str(self.tmpdir),
            timeout=60,
        )

    def test_table_has_header_and_known_sku(self):
        result = self.run_cli(self.path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("responseCount: 7", result.stdout)
        self.assertIn("matchedResponses: 5", result.stdout)
        self.assertIn("nonTextResponses: 2", result.stdout)
        self.assertIn("g1.2tb.ai.1month_eft", result.stdout)
        self.assertIn("lastSeen", result.stdout)

        rows = table_rows(result.stdout)
        self.assertEqual([row[0] for row in rows], EXPECTED_ORDER)
        by_id = {row[0]: row for row in rows}
        self.assertEqual(by_id["com.google.one.plan.free"][1], 5)
        self.assertEqual(by_id["com.google.one.plan.free"][2], "2026-11-25T10:23:04+0900")

    def test_rows_are_sorted_by_count_desc(self):
        rows = table_rows(self.run_cli(self.path).stdout)

        counts = [row[1] for row in rows]
        self.assertEqual(counts, sorted(counts, reverse=True))
        # 同数のときは SKU 昇順で安定させる。
        ties = [row[0] for row in rows if row[1] == 3]
        self.assertEqual(ties, ["g1.200gb.ai.1month_eft", "g1.2tb"])

    def test_filter_narrows_rows_but_keeps_header(self):
        result = self.run_cli(self.path, "--filter", "2tb")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("responseCount: 7", result.stdout)
        rows = table_rows(result.stdout)
        self.assertEqual([row[0] for row in rows], ["g1.2tb", "g1.2tb.ai.1month_eft"])

    def test_filter_is_case_insensitive(self):
        result = self.run_cli(self.path, "--filter", "AI.1MONTH")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [row[0] for row in table_rows(result.stdout)],
            ["g1.200gb.ai.1month_eft", "g1.2tb.ai.1month_eft"],
        )

    def test_filter_without_match_prints_note(self):
        result = self.run_cli(self.path, "--filter", "no-such-sku")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(table_rows(result.stdout), [])
        self.assertIn("一致する SKU はありません", result.stdout)

    def test_top_limits_rows(self):
        result = self.run_cli(self.path, "--top", "2")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [row[0] for row in table_rows(result.stdout)],
            EXPECTED_ORDER[:2],
        )
        self.assertIn("showing: 2 / 4", result.stdout)

    def test_top_zero_keeps_header_without_claiming_nothing_observed(self):
        result = self.run_cli(self.path, "--top", "0")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("showing: 0 / 4", result.stdout)
        self.assertEqual(table_rows(result.stdout), [])
        self.assertNotIn("SKU はまだ観測されていません", result.stdout)

    def test_top_is_applied_after_filter(self):
        result = self.run_cli(self.path, "--filter", "g1.", "--top", "1")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row[0] for row in table_rows(result.stdout)], ["g1.200gb.ai.1month_eft"])

    def test_json_round_trip(self):
        result = self.run_cli(self.path, "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), FIXTURE)

    def test_empty_skus_still_shows_header(self):
        empty = dict(FIXTURE, skus=[], skuCount=0)
        self.write_json(self.path, empty)

        result = self.run_cli(self.path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skuCount: 0", result.stdout)
        self.assertEqual(table_rows(result.stdout), [])

    def test_missing_file_exits_1_without_traceback(self):
        missing = self.tmpdir / "does-not-exist.json"

        result = self.run_cli(missing)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("見つかりません", result.stderr)
        self.assertIn(str(missing), result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(missing.exists())

    def test_invalid_json_exits_1_without_traceback(self):
        broken = self.tmpdir / "broken.json"
        broken.write_text('{"skus": [', encoding="utf-8")

        result = self.run_cli(broken)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("JSON", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_non_object_json_exits_1(self):
        array_path = self.tmpdir / "array.json"
        array_path.write_text("[]", encoding="utf-8")

        result = self.run_cli(array_path)

        self.assertEqual(result.returncode, 1)
        self.assertIn("JSON オブジェクト", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_does_not_create_or_modify_files(self):
        before = {path.name: path.read_bytes() for path in self.tmpdir.iterdir()}

        result = self.run_cli(self.path, "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        after = {path.name: path.read_bytes() for path in self.tmpdir.iterdir()}
        self.assertEqual(after, before)

    def test_source_imports_no_network_modules(self):
        tree = ast.parse(REPORT_PY.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        forbidden = {"socket", "ssl", "urllib", "http", "ftplib", "smtplib", "requests", "httpx"}
        self.assertEqual(imported & forbidden, set())


if __name__ == "__main__":
    unittest.main()
