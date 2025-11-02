# MenZ-GeminiCLI

GeminiCLI を使って LLM コメントを生成し、リアルタイム字幕用 WebSocket サーバー（例: zagaroid）に接続してコメントを返すクライアント実装です。

本リポジトリは MenZ-LLM（WS クライアント/字幕→コメント）の設計を参考にしつつ、コメント生成エンジンをローカル LLM ではなく Gemini の CLI 実行に置き換えています。

参考: MenZ-LLM Realtime WS Client（GitHub）: https://github.com/zagan-the-gun/MenZ-LLM


## 要件

- Python 3.10+
- 任意の Gemini CLI 実行環境（既定は npm 版 `@google/gemini-cli`。テンプレートは差し替え可能）


## セットアップ

1) 仮想環境の作成と依存インストール

```bash
python -m venv .venv
source .venv/bin/activate
./setup.sh
```

2) 設定ファイルの編集（`config.ini`）

```ini
[client]
host = localhost
port = 50001
reconnect_initial_ms = 500
reconnect_max_ms = 5000
log_level = DEBUG

[processing]
# 何行まとめて1回の推論に投げるか
lines_per_inference = 3
# 最後の字幕受信からこの秒数経過で不足分でもフラッシュ（0で無効）
idle_flush_seconds = 10

[prompt]
# システムプロンプト（キャラクター設定とルール）
system_prompt =
 あなたはWar Thunderと言うゲーム配信のワイプに映る麻原彰晃です。
 あなたは地獄からこの配信を見ています。
 麻原彰晃っぽいセリフでバラエティ向きの不謹慎なコメントを返してください。
 また、ワンパターンな回答は絶対に避けて下さい。

# 字幕フォーマットテンプレート（zagaroidから送られる字幕用）
# 利用可能プレースホルダ: {text}, {speaker}, {speaker_part}, {lines_num}
template = {speaker}「{text}」

[gemini]
# 例: npm版 gemini-cli（OAuth対応）
cli_command_template = gemini -m {model} -p {prompt}
model_name = gemini-2.5-flash
timeout_seconds = 60
max_output_chars = 120
```

- `[client]` セクション
  - `host/port`: 接続先（デフォルトは zagaroid のポート50001。MCP では常に root path `/` を使用）
  - `log_level`: ログレベル（DEBUG, INFO, WARNING, ERROR）
  - `reconnect_*_ms`: 再接続バックオフ設定

- `[processing]` セクション
  - `lines_per_inference`: 何行まとめて1回の推論に投げるか（バッチ処理サイズ）
  - `idle_flush_seconds`: 最後の字幕受信からこの秒数経過で、不足分でもフラッシュ・実行（0で無効）

- `[prompt]` セクション
  - `system_prompt`: Gemini 初回のシステムプロンプト（キャラクター設定等）
  - `template`: 字幕フォーマットテンプレート（利用可能な変数は `{text}`, `{speaker}`, `{speaker_part}`, `{lines_num}`）

- `[gemini]` セクション
  - `cli_command_template`: 任意の Gemini CLI に合わせて変更。`{model}` と `{prompt}` は必須
  - `model_name`: 使用するモデル名
  - `timeout_seconds`: CLI 実行のタイムアウト秒数
  - `max_output_chars`: コメントの最大文字数（超過分は切り詰め）


## 起動

```bash
source .venv/bin/activate
./run.sh
```

接続先は `config.ini` の `[client]` セクションで指定した `ws://{host}:{port}/` になります。


## WebSocket プロトコル

本クライアントは **MCP（Model Context Protocol）JSON-RPC 2.0 形式**に対応しています。レガシー形式も互換性のため サポートしています。

### MCP 形式（推奨）

- **受信: 字幕（zagaroid などのサーバー → 本クライアント）**

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/subtitle",
  "params": {
    "text": "今日は良い天気",
    "speaker": "viewer",
    "type": "subtitle",
    "language": "ja"
  }
}
```

- **受信: チャットコメント（zagaroid などのサーバー → 本クライアント）**

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/subtitle",
  "params": {
    "text": "すごい！",
    "speaker": "viewer",
    "type": "comment",
    "language": "ja"
  }
}
```

- **送信: コメント（本クライアント → zagaroid などのサーバー）**

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/subtitle",
  "params": {
    "text": "いいね！",
    "speaker": "wipe",
    "type": "comment",
    "language": "ja"
  }
}
```

### 処理フロー

1. `type=subtitle` を受け取る → バッファに蓄積
2. バッファが `lines_per_inference` に達するか、`idle_flush_seconds` 経過で推論実行
3. Gemini CLI でコメント生成
4. `type=comment` として返送

- `type=comment` を受け取った場合は、即座に推論を実行して返送します

### レガシー形式（互換性維持）

```json
{"type":"subtitle","text":"今日は良い天気","speaker":"viewer"}
```

本クライアントは古い形式も受け取りますが、送信は常に MCP 形式です。


## CLI テンプレート例（任意の CLI に対応）

`cli_command_template` はローカルに用意した任意の CLI に合わせて自由に設定できます。最低限 `{model}` と `{prompt}` を含めてください。

- 例: プレースホルダをダブルクォートで囲む場合
  - `gemini generate --model {model} --text "{prompt}" --no-stream`

テンプレートは `shell=True` で実行されます。プロンプトは内部で `shlex.quote()` によりエスケープされますが、CLI 側の仕様に合わせて適宜調整してください。

### npm版 gemini-cli の導入（既定、OAuth対応）

```bash
# Node.js が未導入の場合（macOS例）: brew install node
npm install -g @google/gemini-cli@latest
gemini  # 初回起動でブラウザOAuth
```

PATH が通らない場合は `which gemini` で絶対パスを確認し、`config.ini` の `cli_command_template` を置き換えてください。


## 実装のポイント

- `app/geminicli_runner.py`: Gemini CLI 実行ラッパー。`{model}` と `{prompt}` を埋め込み、標準出力の先頭の非空行をコメントとして抽出します。
- `app/client.py`: WebSocket クライアント本体。以下の機能を実装：
  - **MCP JSON-RPC 2.0 形式のサポート**: `params` 構造を正しく解析
  - **バッファリング機能**: 複数行の字幕をまとめてバッチ処理（`lines_per_inference`）
  - **アイドルフラッシュ**: 最後の字幕受信から一定時間経過後に自動実行（`idle_flush_seconds`）
  - **チャットコメント即時処理**: `type=comment` は即座に推論実行
  - **話者ごとのバッファ管理**: 複数の話者から並行で字幕を受け取った場合に対応
  - **再接続のバックオフ制御**: エクスポーネンシャルバックオフで安全な再接続
- `config.ini`: 接続先、処理設定、プロンプト、CLI 設定。
- `run.sh` / `setup.sh`: 実行・セットアップ用スクリプト。


## トラブルシューティング

- CLI が見つからない / 権限エラー
  - `which gemini` などでパスを確認。必要に応じて `cli_command_template` をフルパス指定に変更。

- 認証エラー
  - 使用する CLI の手順に従って API キーや認証を設定してください（環境変数や設定ファイル等）。

- 出力が長すぎる / フォーマットが崩れる
  - `max_output_chars` を調整。CLI 出力フォーマットに応じて `app/geminicli_runner.py` の抽出処理を拡張してください。

- タイムアウトが発生する
  - `timeout_seconds` を延ばすか、モデル/プロンプトを見直してください。

- WebSocket に接続できない
  - `config.ini` の `[client]` セクションで `host/port/path` を確認。サーバー（例: zagaroid）が起動しているか確認。

- 推論が実行されない / コメントが返ってこない
  - `log_level` を `DEBUG` に設定して詳細ログを確認してください。
  - `lines_per_inference` が大きすぎないか確認（小さい値をお勧め、例：3）。
  - `idle_flush_seconds` を 0 に設定している場合、字幕受信時に自動フラッシュされません。

- バッファリングの挙動が予期しない
  - `lines_per_inference`: 何行で推論を実行するか（例：3 なら3行で実行）
  - `idle_flush_seconds`: 最後の字幕から何秒待つか（0 で無効）
  - 両者の組み合わせにより、早期実行または遅延実行が制御されます

- 複数の話者から同時に字幕が来ると反応が遅い
  - 話者ごとにバッファが独立しているため、複数話者の場合は個別の推論が並行実行されません
  - 逐次処理のため、CPU や CLI リソースに注意してください

- メモリ使用量が増加し続ける
  - 推論完了後もバッファが解放されていない可能性があります
  - ログで `flush_buffer` が呼ばれているか確認してください


## ライセンス

本リポジトリは参考実装です。上位システム（例: zagaroid）や利用する Gemini CLI のライセンス・利用規約に従ってください。


## 参考

- MenZ-LLM Realtime WS Client: https://github.com/zagan-the-gun/MenZ-LLM
