#!/usr/bin/env bash
# Вызывается из start.sh / restart.sh при cwd = корень time_case_board.
# 1) если есть web/dist/index.html — ок
# 2) иначе распаковать web/dist.zip → web/dist/
# 3) иначе npm install && npm run build (если есть npm)
ensure_web_dist() {
  if [[ -f web/dist/index.html ]]; then
    return 0
  fi

  if [[ -f web/dist.zip ]]; then
    echo "[INFO] Нет web/dist/ — распаковываю web/dist.zip ..."
    rm -rf web/dist
    if command -v unzip >/dev/null 2>&1; then
      unzip -o -q web/dist.zip -d web || echo "[WARN] unzip завершился с ошибкой — пробую npm..."
    else
      echo "[WARN] unzip не найден — пробую npm..."
    fi
    rm -rf web/__MACOSX 2>/dev/null || true
  fi

  if [[ -f web/dist/index.html ]]; then
    echo "[INFO] Фронт готов (из dist.zip)."
    return 0
  fi

  if [[ -f web/package.json ]] && command -v npm >/dev/null 2>&1; then
    echo "[INFO] Собираю фронт: cd web && npm install && npm run build ..."
    (cd web && npm install && npm run build) || {
      echo "[ERROR] Сборка фронта не удалась." >&2
      return 1
    }
  else
    echo "[WARN] Нет web/dist/index.html, нет npm или web/package.json — в браузере будет заглушка." >&2
    echo "      Установите Node.js или распакуйте web/dist.zip вручную в web/dist/" >&2
  fi
  return 0
}
