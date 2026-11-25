"""mitmproxy アドオン: one.google.com のレスポンスから g1.* を観測する。

観測専用。flow.response を書き換えない。課金・購入の操作もしない。

使い方:
    mitmdump --listen-host 0.0.0.0 --listen-port 8080 \
             -s addon.py --set g1_report=./g1-observe.json
"""

from __future__ import annotations

import json
import os
import sys
import time

from mitmproxy import ctx, http

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import g1_observe as obs  # noqa: E402


def _warn(message: str) -> None:
    """警告を出す。

    実行中のプロキシでは ctx.log が使えるが、テストのように mitmproxy の
    実行コンテキスト外では ctx.log が存在しない。その場合は標準エラーへ出す。
    """
    try:
        ctx.log.warn(message)
    except Exception:  # noqa: BLE001 - ctx.log の有無しか判定できない
        print(message, file=sys.stderr, flush=True)


class G1Observer:
    def __init__(self) -> None:
        self.report = obs.Report()
        self.seen: set = set()
        self.report_path = "g1-observe.json"
        self.hosts = "*"
        self.rewrite = False
        self.exact_map: dict = {}
        self.prefix_map: dict = {}
        self.rewrite_count = 0
        self.save_every = 1.0
        self._last_save = 0.0

    def load(self, loader) -> None:
        loader.add_option(
            name="g1_report",
            typespec=str,
            default="g1-observe.json",
            help="観測結果の保存先（JSON）",
        )
        loader.add_option(
            name="g1_rewrite",
            typespec=bool,
            default=False,
            help="one.google.com/ai の応答を書き換える（既定は観測のみ）",
        )
        loader.add_option(
            name="g1_map",
            typespec=str,
            default="",
            help='完全一致の置換（JSON）。例 {"g1.ios.20tb.m.v2":"g1.20tb.1month_eft.acq"}',
        )
        loader.add_option(
            name="g1_prefix",
            typespec=str,
            default="",
            help='前方一致の置換（JSON）。例 {"g1.ios.2tb.":"g1.2tb.ai.12months_ip_90p"}',
        )
        loader.add_option(
            name="g1_hosts",
            typespec=str,
            default="*",
            help="観測するホスト（カンマ区切り、*.example.com 可、* で全部）",
        )

    def configure(self, updated) -> None:
        if "g1_report" in updated:
            self.report_path = ctx.options.g1_report
            ctx.log.info(f"[g1-observe] report -> {self.report_path}")
        if "g1_hosts" in updated:
            self.hosts = ctx.options.g1_hosts
            ctx.log.info(f"[g1-observe] hosts -> {self.hosts}")
        if "g1_rewrite" in updated:
            self.rewrite = bool(ctx.options.g1_rewrite)
            ctx.log.info(f"[g1-observe] rewrite -> {self.rewrite}")
        for name, key in (("g1_map", "exact_map"), ("g1_prefix", "prefix_map")):
            if name not in updated:
                continue
            raw = getattr(ctx.options, name) or ""
            mapping = {}
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        mapping = {str(k): str(v) for k, v in parsed.items()}
                    else:
                        ctx.log.warn(f"[g1-observe] {name} は JSON オブジェクトにしてください")
                except ValueError as exc:
                    ctx.log.warn(f"[g1-observe] {name} の JSON を解析できません: {exc}")
            setattr(self, key, mapping)
            ctx.log.info(f"[g1-observe] {name} -> {len(mapping)} rule(s)")

    def running(self) -> None:
        ctx.log.info(
            "[g1-observe] ready: waiting for https://one.google.com traffic "
            "(observation only; responses are not modified)"
        )
        print(f"[g1-observe] report path: {os.path.abspath(self.report_path)}", flush=True)

    def response(self, flow: http.HTTPFlow) -> None:
        # hook 内の例外は mitmproxy のログに出ないまま観測が止まる。必ず表に出す。
        try:
            self._observe(flow)
        except Exception as exc:  # noqa: BLE001 - 観測を止めず、原因を可視化する
            _warn(f"[g1-observe] hook error: {exc!r}")

    def _observe(self, flow: http.HTTPFlow) -> None:
        url = flow.request.pretty_url
        host = obs.host_of(url)
        if not obs.host_allowed(host, self.hosts):
            return

        self.report.note_response()
        response = flow.response
        if response is None:
            self.report.note_nontext()
            return

        content_type = response.headers.get("content-type")
        # raw_content は例外を出さない（None はストリーミング中で本文が手元に無い）。
        has_body = bool(response.raw_content)
        if not obs.is_readable_body(content_type, has_body=has_body):
            self.report.note_nontext()
            return

        raw = response.get_text(strict=False) or ""
        if len(raw.encode("utf-8", errors="ignore")) > obs.MAX_BODY_BYTES:
            raw = raw[: obs.MAX_BODY_BYTES]

        skus = obs.extract_skus(raw)
        observation = obs.Observation(
            at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            path=flow.request.path.split("?")[0],
            status=response.status_code,
            body_bytes=len(raw.encode("utf-8", errors="ignore")),
            skus=skus,
            host=host,
        )
        new_skus = [sku for sku in skus if sku not in self.seen]
        self.seen.update(skus)
        self.report.note_observation(observation)
        print(f"{obs.format_line(observation, new_skus)}  [{host}]", flush=True)

        # 書き換え（明示的に有効化したときだけ）。g1-acq と同じく /ai だけが対象。
        if (
            self.rewrite
            and host == "one.google.com"
            and observation.path.startswith("/ai")
        ):
            new_text, replaced, hits = obs.rewrite_skus(
                raw, self.exact_map, self.prefix_map
            )
            if replaced:
                try:
                    flow.response.set_text(new_text)
                except (ValueError, AttributeError) as exc:
                    ctx.log.warn(f"[g1-observe] 書き換えに失敗: {exc!r}")
                else:
                    self.rewrite_count += replaced
                    for hit in hits:
                        print(
                            f"[g1-observe] REWRITE {hit['from']} -> {hit['to']}",
                            flush=True,
                        )

        now = time.time()
        if now - self._last_save >= self.save_every:
            self._last_save = now
            self._flush()

    def done(self) -> None:
        self._flush()
        ctx.log.info(
            f"[g1-observe] done: {self.report.responses_matched} matched / "
            f"{self.report.responses_seen} seen, {len(self.report.counts)} skus"
        )

    def _flush(self) -> None:
        try:
            self.report.save(self.report_path)
        except OSError as exc:
            _warn(f"[g1-observe] cannot write report: {exc}")


addons = [G1Observer()]
