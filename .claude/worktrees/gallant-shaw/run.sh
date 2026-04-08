#!/usr/bin/env bash
# Запуск с двойного клика или из терминала (Linux): ./run.sh
# Устанавливает зависимости, при необходимости запускает Ollama, поднимает сервер и открывает браузер.
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
export PATH="/usr/local/bin:$PATH"

echo "=== Roadmap — Массовое создание задач ==="
echo ""

# Python
PYTHON3=""
for candidate in python3 python; do
  if command -v "$candidate" &>/dev/null; then
    PYTHON3="$candidate"
    break
  fi
done
if [ -z "$PYTHON3" ]; then
  echo "Python 3 не найден. Установите: sudo apt install python3 python3-venv python3-pip"
  echo "Или: https://www.python.org/downloads/"
  read -n 1 -p "Нажмите Enter для выхода..."
  exit 1
fi

# venv и зависимости
VENV_DIR="$ROOT/.venv"
if [ ! -d "$VENV_DIR" ] || [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "Создаём окружение и ставим зависимости (один раз)..."
  "$PYTHON3" -m venv "$VENV_DIR" || { echo "Ошибка venv."; read -r; exit 1; }
fi
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
echo "Зависимости Python..."
"$VENV_PIP" install -r requirements.txt -q 2>/dev/null || "$VENV_PIP" install -r requirements.txt
echo ""

# Ollama (опционально)
if command -v ollama &>/dev/null; then
  if ! curl -sf "http://localhost:11434/api/tags" &>/dev/null; then
    echo "Запуск Ollama..."
    nohup ollama serve &>/dev/null &
    sleep 3
    curl -sf "http://localhost:11434/api/tags" &>/dev/null && echo "Ollama запущен." || echo "Ollama не ответил — при необходимости: ollama serve"
  else
    echo "Ollama уже запущен."
  fi
  OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:3b}"
  if ! ollama list 2>/dev/null | grep -q "$OLLAMA_MODEL"; then
    echo "Модель $OLLAMA_MODEL не найдена. Скачиваю..."
    ollama pull "$OLLAMA_MODEL" || true
  fi
else
  echo "Ollama не найден (опционально). Для обработки транскрипций: https://ollama.com"
fi
echo ""

echo "Запуск сервера Roadmap на http://127.0.0.1:5051"
(sleep 2.5; xdg-open "http://127.0.0.1:5051" 2>/dev/null || true) &
"$VENV_PYTHON" app.py
echo "Сервер остановлен."
