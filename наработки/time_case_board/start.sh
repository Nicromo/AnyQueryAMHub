#!/usr/bin/env bash
# Окружение Python только в .venv.unix — не конфликтует с .venv.win на Windows (iCloud/Dropbox/общая папка).
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$(pwd)"

VENV=".venv.unix"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] Не найден python3. Установите: https://www.python.org/downloads/mac-osx/ или brew install python" >&2
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
    echo "[INFO] Папка $VENV без bin/python — пересоздаю..."
    rm -rf "$VENV"
  fi
  echo "Creating Python venv in $VENV ..."
  python3 -m venv "$VENV"
  if [[ -x "$VENV/bin/python" ]]; then
    venv_python="$VENV/bin/python"
  elif [[ -x "$VENV/bin/python3" ]]; then
    venv_python="$VENV/bin/python3"
  else
    echo "[ERROR] После python3 -m venv нет $VENV/bin/python. Проверьте: xcode-select --install" >&2
    exit 1
  fi
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"
echo "[INFO] Python: обновляю зависимости (pip)..."
"$venv_python" -m pip install -q -r requirements.txt

if ! "$venv_python" -c "import fastapi, uvicorn, httpx, sqlalchemy; import pydantic_settings" 2>/dev/null; then
  echo "[WARN] Импорт пакетов не прошёл — повторяю pip install с выводом..."
  "$venv_python" -m pip install -r requirements.txt
  if ! "$venv_python" -c "import fastapi, uvicorn, httpx, sqlalchemy; import pydantic_settings" 2>/dev/null; then
    echo "[ERROR] После pip install не импортируются fastapi/uvicorn/httpx/sqlalchemy/pydantic_settings." >&2
    exit 1
  fi
fi

# shellcheck disable=SC1091
source "$(dirname "$0")/scripts/ensure-web-dist.sh"
ensure_web_dist

echo ""
echo "============================================================"
echo "  Case Board — сервер работает, пока работает этот процесс."
echo "  Адрес: http://127.0.0.1:8790"
echo "  Закройте терминал = сервер остановится (ERR_CONNECTION_REFUSED)."
echo "  Python venv: $VENV (отдельно от Windows: .venv.win)"
echo "============================================================"
echo ""

exec "$venv_python" -m uvicorn backend.main:app --host 127.0.0.1 --port 8790
