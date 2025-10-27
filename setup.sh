#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR"

# 前提: すでに venv が有効化済み（`source .venv/bin/activate` 等）
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "[python] Dependencies installed."

# npm 版 gemini-cli をグローバルに導入（存在しなければ）
if command -v npm >/dev/null 2>&1; then
  if ! command -v gemini >/dev/null 2>&1; then
    echo "[npm] Installing @google/gemini-cli globally..."
    npm install -g @google/gemini-cli@latest --silent
  else
    echo "[npm] gemini CLI already installed. Skipping."
  fi
else
  echo "[npm] npm が見つかりません。npm版 gemini-cli を使う場合は Node.js/npm をインストールしてください。"
fi

echo "Setup completed."

