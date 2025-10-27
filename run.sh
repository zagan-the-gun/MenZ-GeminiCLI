#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR"

export PYTHONUNBUFFERED=1

# .env があれば読み込む（GOOGLE_API_KEY/GEMINI_API_KEY を設定可能）
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

# 片方だけ設定されている場合は相互補完
if [[ -z "${GEMINI_API_KEY:-}" && -n "${GOOGLE_API_KEY:-}" ]]; then
  export GEMINI_API_KEY="$GOOGLE_API_KEY"
fi
if [[ -z "${GOOGLE_API_KEY:-}" && -n "${GEMINI_API_KEY:-}" ]]; then
  export GOOGLE_API_KEY="$GEMINI_API_KEY"
fi

# 前提: すでに venv が有効化済み（`source .venv/bin/activate` 等）
python -m app.client

