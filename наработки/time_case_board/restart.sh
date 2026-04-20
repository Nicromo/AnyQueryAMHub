#!/usr/bin/env bash
# Убивает старый бэкенд и запускает новый. Запускать из папки time_case_board.
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv.unix"
PORT=8790

echo "⏹  Останавливаю старый процесс на :$PORT..."
lsof -ti :"$PORT" | xargs kill -9 2>/dev/null && echo "  убит" || echo "  не было запущено"

sleep 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] python3 не найден" >&2
  exit 1
fi

venv_python=""
if [[ -x "$VENV/bin/python" ]]; then
  venv_python="$VENV/bin/python"
elif [[ -x "$VENV/bin/python3" ]]; then
  venv_python="$VENV/bin/python3"
fi

if [[ ! -d "$VENV" ]] || [[ -z "$venv_python" ]]; then
  if [[ -d "$VENV" ]]; then
    rm -rf "$VENV"
  fi
  echo "📦 Создаю venv в $VENV ..."
  python3 -m venv "$VENV"
  if [[ -x "$VENV/bin/python" ]]; then
    venv_python="$VENV/bin/python"
  else
    venv_python="$VENV/bin/python3"
  fi
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"
echo "[INFO] Python: обновляю зависимости (pip)..."
"$venv_python" -m pip install -q -r requirements.txt

if ! "$venv_python" -c "import fastapi, uvicorn, httpx, sqlalchemy; import pydantic_settings" 2>/dev/null; then
  "$venv_python" -m pip install -r requirements.txt
  if ! "$venv_python" -c "import fastapi, uvicorn, httpx, sqlalchemy; import pydantic_settings" 2>/dev/null; then
    echo "[ERROR] Проверка импорта Python не прошла." >&2
    exit 1
  fi
fi

# shellcheck disable=SC1091
source "$(dirname "$0")/scripts/ensure-web-dist.sh"
ensure_web_dist

export PYTHONPATH="$(pwd)"
echo "🚀 Запускаю бэкенд на :$PORT..."
exec "$venv_python" -m uvicorn backend.main:app --host 127.0.0.1 --port "$PORT"
