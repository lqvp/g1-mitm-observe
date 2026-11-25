# g1-mitm-observe

`one.google.com` のレスポンスを mitmproxy で観測し、Google アプリ（iOS）が受け取る Google One の SKU 文字列（`g1.*` と `com.google.one.*`）を JSON にまとめる小さなツールです。レスポンスは書き換えません。

## これは何か

iOS の Google アプリが `https://one.google.com` から受け取る SKU 文字列を観測するだけで、書き換えも課金もしない。対象は `https://one.google.com` へのレスポンスだけで、ほかのホストは見ない。

- レスポンス本文から `g1.` と `com.google.one.` で始まるトークンを取り出す。
- 保存するのは SKU 文字列、リクエストパス、HTTP ステータス、時刻、本文サイズ。
- 課金、購入、解約、アカウント操作はしない。

## なぜ書き換えないのか

課金の権威は App Store 側の商品メタデータ（SKU と価格）にあり、アプリが受け取る表示用の JSON にはない。表示データを書き換えても請求金額は変わらないため、このツールは書き換えを目的にしない。

同種の試みである g1-acq でも [issue #2](https://github.com/crhywun/g1-acq/issues/2) が未解決のままです。

## セットアップ

必要なものは 2 つです。

- mitmproxy（`mitmdump` コマンド）。`python3 -m pip install mitmproxy` で入ります。
- Python 3.9 以降。`g1_observe.py` と `report.py` は標準ライブラリだけで動きます。

リポジトリのルートで `mitmdump` を起動します。

```bash
mitmdump --listen-host 0.0.0.0 --listen-port 8080 \
         -s addon.py \
         --set g1_report=./g1-observe.json \
         --set block_global=false
```

`--listen-host 0.0.0.0` は iPhone から届くようにするためです。`--set block_global=false` は必須で、既定の `true` のままだとプライベートアドレス（Tailscale の `100.x` を含む）からの接続が拒否されます。

## iPhone 側の設定

1. Wi-Fi の設定を開き、いま繋いでいるネットワークの「HTTP プロキシ」を「手動」にします。サーバに**このホストの Tailscale IP**（`tailscale ip -4` で確認できます。例 `100.x.y.z`）、ポートに `8080` を入れます。認証はオフのままにします。
2. Safari で `http://mitm.it` を開き、iOS 用の証明書をダウンロードします。ダウンロードしたプロファイルは 設定 > 一般 > VPN とデバイス管理 からインストールします。
3. 設定 > 一般 > 情報 > 証明書信頼設定 を開き、mitmproxy の証明書を有効にします。

インストールと有効化の両方が必要です。インストールだけでは TLS の検証を通せません。

## 確認手順

1. ターミナルで `mitmdump` を起動したままにします。
2. iPhone で Google アプリを開き、Google One のプラン画面（アップグレードやダウングレードの選択画面）を開きます。
3. ターミナルに次のような行が出れば観測できています。

   ```
   [2026-11-25T10:27:28+0900] /ai 200 28B skus=1  NEW: g1.2tb.ai.1month_eft
   ```

   `skus=0` の行だけが続くときは、対象の URL がまだ出ていないか、本文をテキストとして読めていません。
4. 保存された JSON を集計して確認します。

   ```bash
   python3 report.py g1-observe.json
   ```

   ```
   report: g1-observe.json
   schema: g1-observe.v1
   updatedAt: 2026-11-25T10:27:28+0900
   responseCount: 12
   matchedResponses: 9
   nonTextResponses: 3
   skuCount: 4
   showing: 4 / 4 (top 50)

   SKU                       count  lastSeen
   com.google.one.plan.free     11  2026-11-25T10:26:02+0900
   g1.2tb.ai.1month_eft          4  2026-11-25T10:27:28+0900
   g1.2tb                        2  2026-11-25T10:25:41+0900
   g1.200gb.ai.1month_eft        1  2026-11-25T10:24:57+0900
   ```

## つまずきどころ

### 証明書ピニング

Google アプリが証明書ピニングをしていると、mitmproxy を挟めません。症状は TLS ハンドシェイクの失敗で、ターミナルに `tls handshake failed` などが出ます。アプリ側の実装次第なので、このツールからは回避できません。Safari で `https://one.google.com` を開いたときに観測できるかどうかで、ピニングかどうかを切り分けられます。

### QUIC / HTTP/3

mitmproxy は UDP 443 を観測できません。Google のサービスは QUIC を使うことがあり、HTTP/3 で通信している間はレスポンスが取れません。ルータやファイアウォールで UDP 443 を塞ぐと、しばらくして TCP にフォールバックします。iOS 側に QUIC を切る設定はありません。

### iOS の証明書信頼は 2 段階

プロファイルのインストール（Safari から）と「証明書信頼設定」での有効化は別の操作です。片方だけだと TLS エラーになり、`http://mitm.it` は開けても `https://` が通りません。iOS 16 以降ではプロファイルが 設定 > 一般 > VPN とデバイス管理 に並びます。

### `block_global`

mitmproxy は既定でプライベートアドレスからの接続を拒否します。Tailscale の IP はプライベート扱いなので、`--set block_global=false` を付け忘れると iPhone から繋がりません。このときブラウザには何も表示されないことが多いです。

## レポートの読み方

`report.py` は保存済み JSON を読むだけで、ネットワークにも出ませんし、ファイルも書きません。

- `python3 report.py g1-observe.json` は見出しと表を出します。表は count の降順、同数なら SKU の昇順です。
- `--json` は JSON をそのまま出します。`--filter` と `--top` は無視します。
- `--filter TEXT` は SKU を部分一致で絞ります。大文字小文字は区別しません。
- `--top N` は表の行数を絞ります。既定は 50 です。
- ファイルが無い、JSON として壊れている、といったときは日本語のメッセージを標準エラーに出し、終了コード 1 で止まります。トレースバックは出しません。

## 注意

観測専用のツールです。保存するのは SKU 文字列、パス、HTTP ステータス、時刻、本文サイズだけです。クエリ文字列は落としてパスだけを残します。リクエストヘッダやレスポンスヘッダ、Cookie、トークン、アカウント情報は保存しません。

- 使い終わったら、iPhone の Wi-Fi の HTTP プロキシを「オフ」に戻します。
- 証明書は検証に使った端末だけに入れます。信頼を外すときは 証明書信頼設定 から無効にします。
- `--listen-host 0.0.0.0` で待ち受けるので、同じネットワークから誰でもプロキシとして使えます。必要な間だけ起動します。

## テスト

```bash
python3 -m unittest discover -s tests
```

`tests/test_addon.py` は mitmproxy のテストユーティリティを使います。`tests/test_report.py` は子プロセスで `report.py` を起動し、出力と終了コードを確認します。どちらも実通信はしません。

## ファイル構成

| ファイル | 役割 |
| --- | --- |
| `addon.py` | mitmproxy のアドオン。`one.google.com` のレスポンスを観測する |
| `g1_observe.py` | mitmproxy に依存しない純粋ロジック（SKU 抽出、集計、保存） |
| `report.py` | 保存済み JSON を読む CLI |
| `tests/` | 標準ライブラリの unittest |

## ライセンス

このリポジトリのコードと文書は CC0 1.0 Universal（パブリックドメイン）として
提供します。権利を主張しません。

<https://creativecommons.org/publicdomain/zero/1.0/legalcode>
