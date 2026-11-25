"""addon.py の配線テスト（mitmproxy のテストユーティリティを使う。実通信なし）。

観測専用であること（レスポンスを書き換えないこと）もここで固定する。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mitmproxy.test import tflow

import addon


def _flow(url="https://one.google.com/ai?hl=ja", body='{"x":"g1.2tb.ai.1month_eft"}',
          content_type="application/json", status=200):
    flow = tflow.tflow(resp=True)
    flow.request.url = url
    parts = urlsplit(url)
    flow.request.path = parts.path + (("?" + parts.query) if parts.query else "")
    flow.response.status_code = status
    flow.response.headers["content-type"] = content_type
    flow.response.set_text(body)
    return flow


def _live_302_flow():
    """ライブの one.google.com が返す形を再現する（HTTP/2, 302, application/binary, 0B）。"""
    flow = tflow.tflow(resp=True)
    flow.request.http_version = "HTTP/2.0"
    flow.request.url = "https://one.google.com/ai?hl=ja"
    flow.request.path = "/ai?hl=ja"
    flow.response.http_version = "HTTP/2.0"
    flow.response.status_code = 302
    flow.response.headers["content-type"] = "application/binary"
    flow.response.headers["content-length"] = "0"
    flow.response.headers["location"] = "https://accounts.google.com/ServiceLogin"
    flow.response.set_content(b"")
    return flow


class AddonWiringTests(unittest.TestCase):
    def setUp(self):
        # TemporaryDirectory はテスト終了まで生かす（即時削除すると保存に失敗する）。
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.report_path = os.path.join(self._tmp.name, "report.json")
        self.observer = addon.G1Observer()
        self.observer.report_path = self.report_path

    def test_captures_skus_from_target_response_and_keeps_body_unchanged(self):
        flow = _flow()
        before = flow.response.get_text()

        self.observer.response(flow)

        # 観測専用: 本文は改変しない
        self.assertEqual(flow.response.get_text(), before)
        self.assertEqual(self.observer.report.responses_matched, 1)
        self.assertIn("g1.2tb.ai.1month_eft", self.observer.report.counts)

    def test_hosts_allowlist_limits_observation(self):
        # 既定は全ホスト観測。allowlist を絞ると対象外は無視する。
        self.observer.hosts = "one.google.com"
        self.observer.response(_flow(url="https://www.google.com/ai"))
        self.assertEqual(self.observer.report.responses_seen, 0)
        self.observer.response(_flow())
        self.assertEqual(self.observer.report.responses_seen, 1)

    def test_observes_other_googleapis_hosts_by_default(self):
        # iOS の Google アプリは subscriptionsmobile-pa.googleapis.com などを使う。
        self.observer.hosts = "*"
        self.observer.response(
            _flow(url="https://subscriptionsmobile-pa.googleapis.com/v1/x")
        )
        self.assertEqual(self.observer.report.responses_seen, 1)
        self.assertIn("subscriptionsmobile-pa.googleapis.com", self.observer.report.host_counts)

    def test_ignores_non_text_bodies(self):
        self.observer.response(_flow(content_type="image/png", body="binary"))
        self.assertEqual(self.observer.report.responses_seen, 1)
        self.assertEqual(self.observer.report.response_nontext, 1)
        self.assertEqual(self.observer.report.responses_matched, 0)

    def test_new_skus_are_reported_only_once(self):
        printed = []
        with mock.patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(map(str, a)))):
            self.observer.response(_flow())
            self.observer.response(_flow())
        self.assertEqual(len(printed), 2)
        self.assertIn("NEW", printed[0])
        self.assertNotIn("NEW", printed[1])

    def test_report_is_written(self):
        with mock.patch("builtins.print"):
            self.observer.response(_flow())
            self.observer._flush()
        self.assertTrue(Path(self.report_path).exists())

    def test_live_302_with_binary_content_type_and_empty_body_is_observed(self):
        # 回帰テスト: ライブの 302 は content-type: application/binary / 0B。
        # content-type だけで弾くと、hook は入るのに何も出ずレポートも書かれない。
        flow = _live_302_flow()
        with mock.patch("builtins.print") as fake_print:
            self.observer.response(flow)

        line = " ".join(str(arg) for arg in fake_print.call_args.args)
        self.assertRegex(
            line, r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}\] /ai 302 0B skus=0\s+\[one\.google\.com\]$"
        )
        self.assertEqual(self.observer.report.responses_matched, 1)
        self.assertEqual(self.observer.report.response_nontext, 0)
        # 観測専用: 本文は空のまま
        self.assertEqual(flow.response.raw_content, b"")
        # hook の中でレポートが書かれている
        self.assertTrue(Path(self.report_path).exists())

    def test_hook_error_is_logged_and_never_raised(self):
        with mock.patch("builtins.print"), mock.patch.object(
            addon.obs, "format_line", side_effect=RuntimeError("boom")
        ), mock.patch.object(addon, "_warn") as warn:
            self.observer.response(_flow())  # 例外を外に出さない

        warn.assert_called_once()
        message = warn.call_args.args[0]
        self.assertIn("[g1-observe] hook error:", message)
        self.assertIn("boom", message)

    def test_warn_falls_back_to_stderr_without_a_running_proxy(self):
        captured = []
        with mock.patch(
            "builtins.print", side_effect=lambda *a, **k: captured.append((a, k))
        ):
            addon._warn("[g1-observe] hook error: RuntimeError('x')")

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][0][0], "[g1-observe] hook error: RuntimeError('x')")
        self.assertEqual(captured[0][1].get("file"), sys.stderr)


if __name__ == "__main__":
    unittest.main()


class RewriteWiringTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.observer = addon.G1Observer()
        self.observer.report_path = os.path.join(self._tmp.name, "r.json")
        self.observer.hosts = "*"
        self.observer.exact_map = {"g1.ios.20tb.m.v2": "g1.20tb.1month_eft.acq"}

    def test_rewrite_disabled_by_default(self):
        flow = _flow(url="https://one.google.com/ai", body='{"sku":"g1.ios.20tb.m.v2"}')
        before = flow.response.get_text()
        with mock.patch("builtins.print"):
            self.observer.response(flow)
        self.assertEqual(flow.response.get_text(), before)

    def test_rewrite_applies_when_enabled(self):
        self.observer.rewrite = True
        flow = _flow(url="https://one.google.com/ai", body='{"sku":"g1.ios.20tb.m.v2"}')
        with mock.patch("builtins.print"):
            self.observer.response(flow)
        self.assertIn("g1.20tb.1month_eft.acq", flow.response.get_text())
        self.assertEqual(self.observer.rewrite_count, 1)

    def test_rewrite_only_touches_the_ai_page(self):
        self.observer.rewrite = True
        flow = _flow(url="https://one.google.com/_/Other", body='{"sku":"g1.ios.20tb.m.v2"}')
        before = flow.response.get_text()
        with mock.patch("builtins.print"):
            self.observer.response(flow)
        self.assertEqual(flow.response.get_text(), before)
