#!/usr/bin/env python3
"""g1-observe.json を集計して表示する読み取り専用 CLI。

ネットワークへは接続しない。ファイルは読むだけで、書き換えも新規作成もしない。
標準ライブラリ以外に依存しない。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_TOP = 50
PROG = "report.py"


class ReportError(Exception):
    """利用者にそのまま見せるエラー。main() が受け止めて終了コード 1 にする。"""


def _as_count(value: Any) -> int:
    """件数として読める値を int にする。読めなければ 0。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return 0
    return 0


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def load_report(path: str) -> Tuple[Dict[str, Any], str]:
    """レポートを読む。失敗は ReportError（日本語メッセージ付き）にする。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except FileNotFoundError:
        raise ReportError(
            f"レポートが見つかりません: {path}\n"
            "  mitmdump を `--set g1_report=g1-observe.json` 付きで起動し、"
            "iPhone で Google One のプラン画面を開いてから再実行してください。"
        )
    except IsADirectoryError:
        raise ReportError(f"ディレクトリが指定されています（ファイルを指定してください）: {path}")
    except PermissionError:
        raise ReportError(f"レポートを読む権限がありません: {path}")
    except UnicodeDecodeError:
        raise ReportError(f"レポートが UTF-8 のテキストではありません: {path}")
    except OSError as exc:
        raise ReportError(f"レポートを読めません: {path}: {exc.strerror or exc}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReportError(
            f"JSON として解析できません: {path}: {exc.msg}（{exc.lineno} 行 {exc.colno} 列）"
        )
    if not isinstance(data, dict):
        raise ReportError(f"レポートの形式が不正です（JSON オブジェクトではありません）: {path}")
    return data, raw


def sku_rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """`skus` を count 降順・SKU 昇順の行にする。壊れた行は読み飛ばす。"""
    entries = data.get("skus")
    rows: List[Dict[str, Any]] = []
    if not isinstance(entries, list):
        return rows
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        sku = entry.get("id")
        if not isinstance(sku, str) or not sku:
            continue
        rows.append(
            {
                "id": sku,
                "count": _as_count(entry.get("count")),
                "lastSeen": _as_text(entry.get("lastSeen")),
            }
        )
    rows.sort(key=lambda row: (-row["count"], row["id"]))
    return rows


def render_lines(
    path: str,
    data: Dict[str, Any],
    rows: Sequence[Dict[str, Any]],
    total_rows: int,
    matched: int,
    top: int,
    filter_text: Optional[str],
) -> List[str]:
    """表示する行を組み立てる（純粋関数）。rows は --top 適用後、matched は適用前。"""
    width = max([len("SKU")] + [len(row["id"]) for row in rows])
    lines = [f"report: {path}"]

    schema = _as_text(data.get("schema"))
    if schema:
        lines.append(f"schema: {schema}")
    updated_at = _as_text(data.get("updatedAt"))
    if updated_at:
        lines.append(f"updatedAt: {updated_at}")

    lines.extend(
        [
            f"responseCount: {_as_count(data.get('responseCount'))}",
            f"matchedResponses: {_as_count(data.get('matchedResponses'))}",
            f"nonTextResponses: {_as_count(data.get('nonTextResponses'))}",
            f"skuCount: {total_rows}",
        ]
    )
    if filter_text is not None:
        lines.append(f"filter: {filter_text} -> {matched} / {total_rows}")
    lines.append(f"showing: {len(rows)} / {total_rows} (top {top})")
    lines.append("")
    lines.append(f"{'SKU':<{width}}  {'count':>5}  lastSeen")

    if not rows:
        lines.append(f"({_empty_note(total_rows, matched, filter_text)})")
    for row in rows:
        lines.append(f"{row['id']:<{width}}  {row['count']:>5}  {row['lastSeen']}")
    return lines


def _empty_note(total_rows: int, matched: int, filter_text: Optional[str]) -> str:
    """表に 1 行も出ない理由を書く（--top 0 を「観測なし」と誤読させない）。"""
    if total_rows == 0:
        return "SKU はまだ観測されていません"
    if matched == 0 and filter_text is not None:
        return "一致する SKU はありません"
    return "表示する行がありません: --top 0"


def _top_type(value: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"整数を指定してください: {value}")
    if number < 0:
        raise argparse.ArgumentTypeError(f"0 以上を指定してください: {value}")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="g1-observe.json を集計して表示する（読み取り専用・ネットワーク不要）",
        epilog=(
            "例:\n"
            f"  python3 {PROG} g1-observe.json\n"
            f"  python3 {PROG} g1-observe.json --filter g1.2tb --top 10\n"
            f"  python3 {PROG} g1-observe.json --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", help="g1-observe.json のパス")
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON をそのまま出力する（--filter / --top は無視する）",
    )
    parser.add_argument(
        "--filter",
        dest="filter_text",
        metavar="TEXT",
        default=None,
        help="SKU を部分一致で絞り込む（大文字小文字は区別しない）",
    )
    parser.add_argument(
        "--top",
        type=_top_type,
        default=DEFAULT_TOP,
        metavar="N",
        help=f"表示する行数（既定 {DEFAULT_TOP}）",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        data, raw = load_report(args.path)
    except ReportError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1

    if args.json:
        sys.stdout.write(raw if raw.endswith("\n") else raw + "\n")
        return 0

    all_rows = sku_rows(data)
    rows = all_rows
    if args.filter_text is not None:
        needle = args.filter_text.lower()
        rows = [row for row in rows if needle in row["id"].lower()]
    shown = rows[: args.top]

    for line in render_lines(
        args.path, data, shown, len(all_rows), len(rows), args.top, args.filter_text
    ):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
