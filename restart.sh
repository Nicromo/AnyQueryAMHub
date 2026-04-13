#!/bin/bash
# Перезапуск сервиса Roadmap bulk tasks (порт 5051)
cd "$(dirname "$0")"
lsof -ti :5051 | xargs kill -9 2>/dev/null
sleep 1
python3 app.py
