# iOS 側の SKU 命名規則（2026-11-25 実測）

`one.google.com/ai` の応答（1.2 MB の HTML）から実際に観測した値にもとづく。
推測ではなく、mitmproxy で捕捉した文字列そのまま。

## 観測された SKU 一覧

| SKU | 期間 | 割引 |
|---|---|---|
| `g1.ios.ai.2tb.m.ip_1mo_90p.acq` | 月払い | 初月 90% オフ |
| `g1.ios.ai.2tb.a.ip_1yr_10p.acq` | 年払い | 1 年 10% オフ |
| `g1.ios.helium.m.1month_ip_90p.acq` | 月払い | 初月 90% オフ |
| `g1.ios.helium.a.ip_1yr_10p.acq` | 年払い | 1 年 10% オフ |
| `g1.ios.2tb.m.3month_ip_75p` | 月払い | 3 ヶ月 75% オフ |
| `g1.ios.100gb.m.3month_ip_75p` | 月払い | 3 ヶ月 75% オフ |
| `g1.ios.2tb.a.12month_ip_15p` | 年払い | 12 ヶ月 15% オフ |
| `g1.ios.100gb.a.12month_ip_15p` | 年払い | 12 ヶ月 15% オフ |
| `g1.ios.20tb.m.v2` | 月払い | Ultra 20TB の入口 |
| `g1.ios.30tb.ai.pro.m` | 月払い | Ultra 30TB の入口 |
| `com.google.one.generic` | — | 汎用 |

**重要: `_eft`（無料トライアル）は 1 つも含まれていない。** すべて `_ip_`（割引）。
g1-acq v7.6.0 が書き換え先に指定する `g1.20tb.1month_eft.acq` は、
この実測カタログには存在しない ID である。

## 命名の構造

```
g1.ios.<tier>.<請求周期>.<期間>_ip_<割引率>p[.acq]
```

| 要素 | 値 | 意味 |
|---|---|---|
| `tier` | `2tb` / `100gb` / `ai.2tb` / `helium` / `20tb` / `30tb.ai.pro` | 容量・系統。`helium` と `ai.2tb` は Android 側のスキャン対象に無い |
| 請求周期 | `m` / `a` | 月払い / 年払い |
| 期間 | `1month` / `3month` / `12month` / `1mo` / `1yr` | 表記が 2 系統ある（`month` 綴りと短縮形） |
| 割引 | `ip_90p` / `ip_75p` / `ip_15p` / `ip_10p` | `90p` = 90% オフ |
| 接尾 | `.acq` | 獲得向けプロモの目印 |

### Android 側との違い

| | iOS | Android |
|---|---|---|
| 接頭 | `g1.ios.` | `g1.` |
| tier の語順 | `ai.2tb`（容量が後） | `2tb.ai`（容量が先） |
| 期間 | `1month` / `1mo` が混在 | `1month` / `1months` |
| 無料トライアル | 観測されず | `_eft` が存在（`g1.2tb.ai.18months_eft` など） |

**iOS の ID は Android の ID とそのまま対応しない。** 名前が似ていても別の商品。

## g1-acq の書き換えマッピング（v7.6.0）

| 元 | 既定の書き換え先 | 予備 |
|---|---|---|
| `g1.ios.2tb.` で始まる ID | `g1.2tb.ai.12months_ip_90p` | 無し |
| `g1.ios.20tb.m.v2` | `g1.20tb.1month_eft.acq` | `g1.20tb.3months_ip_90p.acq` |
| `g1.ios.30tb.ai.pro.m` | `g1.ai.ultra.1month_eft.acq` | `g1.ai.ultra.3months_ip_90p.acq` |

## 未解決の点

- `_ip_` の `ip` の語源、`.acq` の語源は一次情報が無い
- `helium` / `ai.2tb` が Android 側にも存在するかは未検証
- 書き換え後に Apple の收銀台を通せるかは未検証（g1-acq の issue #2 は未解決のまま）
