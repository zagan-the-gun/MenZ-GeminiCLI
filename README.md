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
path = /wipe_subtitle
reconnect_initial_ms = 500
reconnect_max_ms = 5000

[gemini]
# {model} と {prompt} のプレースホルダを含むコマンドテンプレート（既定は npm版 gemini-cli）
cli_command_template = gemini -m {model} -p {prompt}
model_name = gemini-1.5-flash
timeout_seconds = 60
max_output_chars = 120
```

- `cli_command_template`: 任意の Gemini CLI に合わせて変更してください。`{model}` と `{prompt}` は必須です。
- `model_name`: 使用するモデル名。
- `timeout_seconds`: CLI 実行のタイムアウト秒数。
- `max_output_chars`: コメントの最大文字数（超過分は切り詰め）。


## 起動

```bash
source .venv/bin/activate
./run.sh
```

接続先は `config.ini` の `[client]` セクションで指定した `ws://{host}:{port}{path}` になります。


## WebSocket プロトコル

- 受信: 字幕（サーバー→本クライアント）

```json
{"type":"subtitle","text":"今日は良い天気","speaker":"viewer"}
```

- 送信: コメント（本クライアント→サーバー）

```json
{"type":"comment","comment":"いいね！"}
```

本クライアントは `type=subtitle` を受け取ると、Gemini CLI へプロンプトを組み立てて実行し、1 行の短い日本語コメントを生成して `type=comment` として返します。


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
- `app/client.py`: WebSocket クライアント本体。字幕受信→Gemini 実行→コメント返却、再接続のバックオフ制御などを行います。
- `config.ini`: 接続先と CLI 設定。
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
  - `host/port/path` を確認。サーバー（例: zagaroid）が起動しているか確認。


## ライセンス

本リポジトリは参考実装です。上位システム（例: zagaroid）や利用する Gemini CLI のライセンス・利用規約に従ってください。


## 参考

- MenZ-LLM Realtime WS Client: https://github.com/zagan-the-gun/MenZ-LLM
