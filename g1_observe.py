"""g1-observe の純粋ロジック（mitmproxy に依存しない）。

観測専用。レスポンスを書き換える関数はここに置かない。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

TARGET_HOST = "one.google.com"

# g1-acq と同じ考え方: 前後がトークン境界でない場合だけ拾う。
#   ng: `xg1.foo` / `g1.foo2x` / `person@g1.example`
SKU_PATTERN = (
    r"(?<![A-Za-z0-9._@%-])"
    r"((?:g1\.|com\.google\.one\.)[A-Za-z0-9._-]+)"
    r"(?![A-Za-z0-9._-])"
)
SKU_RE = re.compile(SKU_PATTERN)

# 本文中で点がエスケープされていることがある。発見用にだけ復号する。
ESCAPED_DOT_RE = re.compile(
    r"\\u002e|\\x2e|&#0*46;|&#x0*2e;|&period;",
    re.IGNORECASE,
)

MAX_SKUS_PER_RESPONSE = 500
MAX_RECENT = 30
MAX_BODY_BYTES = 8 * 1024 * 1024


def decode_escaped_dots(text: str) -> str:
    """`\\u002e` などの点エスケープを `.` に戻す（発見用のコピーにだけ使う）。"""
    return ESCAPED_DOT_RE.sub(".", text)


def extract_skus(text: str) -> list:
    """本文から SKU 候補を取り出す（重複なし・昇順）。"""
    if not text:
        return []
    decoded = decode_escaped_dots(text)
    found = {match.group(1) for match in SKU_RE.finditer(decoded)}
    return sorted(found)[:MAX_SKUS_PER_RESPONSE]


def host_of(url: str) -> str:
    """URL からホスト名を取り出す（失敗したら空文字）。"""
    if not isinstance(url, str):
        return ""
    match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/?#]+)", url)
    if not match:
        return ""
    return match.group(1).split(":")[0].lower()


def host_allowed(host: str, allowlist: str) -> bool:
    """観測対象のホストか。allowlist が "*" なら全ホスト。"""
    if allowlist is None:
        return True
    wanted = [item.strip().lower() for item in str(allowlist).split(",") if item.strip()]
    if not wanted or "*" in wanted:
        return True
    host = (host or "").lower()
    for item in wanted:
        if item.startswith("*."):
            if host.endswith(item[1:]):
                return True
        elif host == item:
            return True
    return False


def is_target_url(url: str) -> bool:
    """`https://one.google.com/...` だけを対象にする。"""
    if not isinstance(url, str) or not url:
        return False
    prefix = f"https://{TARGET_HOST}/"
    if url == f"https://{TARGET_HOST}":
        return True
    return url.startswith(prefix)


def is_textual(content_type: Optional[str]) -> bool:
    """本文がテキストとして読めそうか。"""
    if not content_type:
        return True
    lowered = content_type.lower()
    if "json" in lowered or "text" in lowered:
        return True
    if "javascript" in lowered or "xml" in lowered:
        return True
    return False


def is_readable_body(content_type: Optional[str], has_body: bool) -> bool:
    """本文を SKU 抽出の対象にすべきか。

    本文が無いレスポンスは content-type に関わらず対象にする。one.google.com の
    ログイン誘導 302 は `content-type: application/binary` かつ `content-length: 0`
    を返すため、content-type だけで弾くと観測が丸ごと落ちる（本文が無いので
    SKU も無いが、その事実自体を記録する）。
    """
    if not has_body:
        return True
    return is_textual(content_type)


@dataclass
class Observation:
    at: str
    path: str
    status: int
    body_bytes: int
    skus: list = field(default_factory=list)
    host: str = ""

    def as_dict(self) -> dict:
        return {
            "at": self.at,
            "host": self.host,
            "path": self.path,
            "status": self.status,
            "bodyBytes": self.body_bytes,
            "candidateCount": len(self.skus),
            "skus": list(self.skus),
        }


class Report:
    """観測結果を貯めて JSON に落とす。秘密情報は保存しない。"""

    SCHEMA = "g1-observe.v1"

    def __init__(self, limit: Optional[int] = None) -> None:
        self.limit = MAX_SKUS_PER_RESPONSE if limit is None else int(limit)
        self.responses_seen = 0
        self.responses_matched = 0
        self.response_nontext = 0
        self.counts: dict = {}
        self.last_seen: dict = {}
        self.recent: list = []
        self.host_counts: dict = {}

    def note_response(self) -> None:
        self.responses_seen += 1

    def note_nontext(self) -> None:
        self.response_nontext += 1

    def note_observation(self, observation: Observation) -> None:
        self.responses_matched += 1
        if observation.host:
            self.host_counts[observation.host] = self.host_counts.get(observation.host, 0) + 1
        for sku in observation.skus:
            self.counts[sku] = self.counts.get(sku, 0) + 1
            self.last_seen[sku] = observation.at
        self.recent.append(observation)
        if len(self.recent) > MAX_RECENT:
            self.recent = self.recent[-MAX_RECENT:]

    def sku_rows(self) -> list:
        rows = [
            {"id": sku, "count": self.counts[sku], "lastSeen": self.last_seen.get(sku, "")}
            for sku in self.counts
        ]
        rows.sort(key=lambda row: (-row["count"], row["id"]))
        return rows

    def host_rows(self) -> list:
        return [
            {"host": host, "count": count}
            for host, count in sorted(self.host_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    def as_dict(self) -> dict:
        return {
            "schema": self.SCHEMA,
            "updatedAt": _now(),
            "responseCount": self.responses_seen,
            "matchedResponses": self.responses_matched,
            "nonTextResponses": self.response_nontext,
            "skuCount": len(self.counts),
            "hosts": self.host_rows(),
            "skus": self.sku_rows(),
            "recent": [item.as_dict() for item in self.recent],
        }

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.as_dict(), handle, ensure_ascii=False, indent=2)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def format_line(observation: Observation, new_skus: Sequence[str]) -> str:
    """ターミナルに出す 1 行を作る。"""
    added = ""
    if new_skus:
        added = "  NEW: " + ", ".join(new_skus)
    return (
        f"[{observation.at}] {observation.path} "
        f"{observation.status} {observation.body_bytes}B "
        f"skus={len(observation.skus)}{added}"
    )


# ---------------------------------------------------------------------------
# 書き換え（g1-acq 互換）。既定では無効で、観測専用のまま動く。
# ---------------------------------------------------------------------------

def rewrite_skus(text: str, exact_map: Optional[dict] = None,
                 prefix_map: Optional[dict] = None) -> tuple:
    """本文中の SKU 文字列を置換する。戻り値は (新本文, 置換回数, 内訳)。

    g1-acq と同じく、トークン境界を守って置換する。価格などの数値には触れない。
    置換先がカタログに無い ID でも、ここでは何も検証しない（結果は購入画面で確認する）。
    """
    if not text:
        return text, 0, []

    exact_map = {k: v for k, v in (exact_map or {}).items() if k and v}
    prefix_map = {k: v for k, v in (prefix_map or {}).items() if k and v}
    if not exact_map and not prefix_map:
        return text, 0, []

    hits = []

    def _replace(match: "re.Match") -> str:
        # SKU_RE は後読み/先読みで境界を作るので、トークンは group(1) だけ。
        token = match.group(1)
        if token in exact_map:
            target = exact_map[token]
        else:
            target = ""
            for prefix, value in prefix_map.items():
                if token.startswith(prefix):
                    target = value
                    break
            if not target:
                return match.group(0)
        hits.append({"from": token, "to": target})
        return target

    out = SKU_RE.sub(_replace, text)
    return out, len(hits), hits
