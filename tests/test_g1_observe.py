"""g1-observe の振る舞いテスト（mitmproxy・端末・ネットワーク不要）。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import g1_observe as obs


class ExtractSkuTests(unittest.TestCase):
    def test_extracts_g1_and_com_google_one_ids(self):
        text = (
            'foo g1.2tb.ai.12months_ip_90p bar '
            '"g1.20tb.1month_eft.acq" com.google.one.something'
        )
        self.assertEqual(
            obs.extract_skus(text),
            ["com.google.one.something", "g1.20tb.1month_eft.acq", "g1.2tb.ai.12months_ip_90p"],
        )

    def test_respects_token_boundaries(self):
        # `xg1.` は直前が x、`person@g1.` は直前が @ なので拾わない。
        # `g1.ios.2tbx` は前後が境界なので、そのまま 1 トークンとして拾う。
        text = "xg1.ios.2tb g1.ios.2tbx person@g1.example g1.ios.2tb.m.v2"
        self.assertEqual(obs.extract_skus(text), ["g1.ios.2tb.m.v2", "g1.ios.2tbx"])

    def test_decodes_escaped_dots(self):
        text = r'{"id":"g1\u002e2tb\u002eai\u002e1month_eft"}'
        self.assertEqual(obs.extract_skus(text), ["g1.2tb.ai.1month_eft"])

    def test_deduplicates_and_sorts(self):
        text = "g1.b g1.a g1.b g1.a"
        self.assertEqual(obs.extract_skus(text), ["g1.a", "g1.b"])

    def test_empty_and_non_string_inputs(self):
        self.assertEqual(obs.extract_skus(""), [])
        self.assertEqual(obs.extract_skus(None), [])

    def test_caps_number_of_candidates(self):
        text = " ".join(f"g1.tier{i}" for i in range(600))
        self.assertLessEqual(len(obs.extract_skus(text)), obs.MAX_SKUS_PER_RESPONSE)


class TargetUrlTests(unittest.TestCase):
    def test_accepts_only_https_one_google_com(self):
        self.assertTrue(obs.is_target_url("https://one.google.com/ai?x=1"))
        self.assertTrue(obs.is_target_url("https://one.google.com/"))
        self.assertTrue(obs.is_target_url("https://one.google.com"))

    def test_rejects_lookalikes_and_other_schemes(self):
        for url in (
            "http://one.google.com/ai",
            "https://one.google.com.evil/ai",
            "https://evil.one.google.com/ai",
            "https://www.google.com/",
            "",
            None,
        ):
            with self.subTest(url=url):
                self.assertFalse(obs.is_target_url(url))


class ContentTypeTests(unittest.TestCase):
    def test_textual_detection(self):
        self.assertTrue(obs.is_textual("application/json"))
        self.assertTrue(obs.is_textual("text/html; charset=utf-8"))
        self.assertTrue(obs.is_textual("application/javascript"))
        self.assertTrue(obs.is_textual(None))
        self.assertFalse(obs.is_textual("image/png"))
        self.assertFalse(obs.is_textual("application/octet-stream"))


class ReadableBodyTests(unittest.TestCase):
    def test_empty_body_is_readable_even_with_binary_content_type(self):
        # one.google.com のログイン誘導 302 は content-type: application/binary で
        # content-length: 0 を返す。中身が無いので content-type だけで弾かない。
        self.assertTrue(obs.is_readable_body("application/binary", has_body=False))

    def test_present_binary_body_is_not_readable(self):
        self.assertFalse(obs.is_readable_body("image/png", has_body=True))
        self.assertFalse(obs.is_readable_body("application/octet-stream", has_body=True))

    def test_present_textual_body_is_readable(self):
        self.assertTrue(obs.is_readable_body("application/json", has_body=True))
        self.assertTrue(obs.is_readable_body("text/html; charset=utf-8", has_body=True))
        self.assertTrue(obs.is_readable_body(None, has_body=True))


class ReportTests(unittest.TestCase):
    def _observation(self, skus, path="/ai", status=200):
        return obs.Observation(
            at="2026-11-25T10:00:00+0900", path=path, status=status, body_bytes=123, skus=skus
        )

    def test_aggregates_counts_and_recent(self):
        report = obs.Report()
        report.note_response()
        report.note_observation(self._observation(["g1.a", "g1.b"]))
        report.note_response()
        report.note_nontext()
        report.note_observation(self._observation(["g1.a"]))

        data = report.as_dict()
        self.assertEqual(data["responseCount"], 2)
        self.assertEqual(data["matchedResponses"], 2)
        self.assertEqual(data["nonTextResponses"], 1)
        self.assertEqual(data["skuCount"], 2)
        self.assertEqual(data["skus"][0], {"id": "g1.a", "count": 2, "lastSeen": "2026-11-25T10:00:00+0900"})
        self.assertEqual(len(data["recent"]), 2)

    def test_recent_is_capped(self):
        report = obs.Report()
        for _ in range(obs.MAX_RECENT + 5):
            report.note_observation(self._observation(["g1.a"]))
        self.assertEqual(len(report.as_dict()["recent"]), obs.MAX_RECENT)

    def test_save_writes_valid_json(self):
        report = obs.Report()
        report.note_observation(self._observation(["g1.a"]))
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "report.json")
            report.save(path)
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        self.assertEqual(data["schema"], "g1-observe.v1")
        self.assertEqual(data["matchedResponses"], 1)

    def test_format_line_mentions_new_skus(self):
        line = obs.format_line(self._observation(["g1.a"]), ["g1.a"])
        self.assertIn("/ai", line)
        self.assertIn("NEW", line)
        self.assertIn("g1.a", line)


if __name__ == "__main__":
    unittest.main()


class RewriteTests(unittest.TestCase):
    MAP = {"g1.ios.20tb.m.v2": "g1.20tb.1month_eft.acq"}
    PREFIX = {"g1.ios.2tb.": "g1.2tb.ai.12months_ip_90p"}

    def test_exact_and_prefix_replacements(self):
        text = "a g1.ios.20tb.m.v2 b g1.ios.2tb.m.3month_ip_75p c g1.2tb.ai.1month_eft"
        out, count, hits = obs.rewrite_skus(text, self.MAP, self.PREFIX)
        self.assertEqual(count, 2)
        self.assertIn("g1.20tb.1month_eft.acq", out)
        self.assertIn("g1.2tb.ai.12months_ip_90p", out)
        self.assertIn("g1.2tb.ai.1month_eft", out)  # 対象外は温存
        self.assertEqual(hits[0], {"from": "g1.ios.20tb.m.v2", "to": "g1.20tb.1month_eft.acq"})

    def test_no_rules_leaves_text_untouched(self):
        text = "g1.ios.20tb.m.v2"
        out, count, hits = obs.rewrite_skus(text)
        self.assertEqual(out, text)
        self.assertEqual(count, 0)
        self.assertEqual(hits, [])

    def test_respects_token_boundaries(self):
        text = "xg1.ios.20tb.m.v2 person@g1.ios.20tb.m.v2"
        out, count, _ = obs.rewrite_skus(text, self.MAP)
        self.assertEqual(count, 0)
        self.assertEqual(out, text)

    def test_does_not_touch_numbers_or_prices(self):
        text = '{"price":"$99.99","sku":"g1.ios.20tb.m.v2"}'
        out, _, _ = obs.rewrite_skus(text, self.MAP)
        self.assertIn("$99.99", out)
        self.assertIn("g1.20tb.1month_eft.acq", out)
