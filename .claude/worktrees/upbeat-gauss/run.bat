@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set ROOT=%CD%

echo === Roadmap — Массовое создание задач ===
echo.

REM Python: py launcher или python в PATH
set PYTHON=
where py >nul 2>&1 && set PYTHON=py -3
if not defined PYTHON where python >nul 2>&1 && set PYTHON=python
if not defined PYTHON where python3 >nul 2>&1 && set PYTHON=python3
if not defined PYTHON (
  echo Python не найден. Установите с https://www.python.org/downloads/
  echo При установке включите "Add Python to PATH".
  pause
  exit /b 1
)

REM venv
if not exist "%ROOT%\.venv\Scripts\python.exe" (
  echo Создаём окружение и ставим зависимости (один раз)...
  %PYTHON% -m venv "%ROOT%\.venv"
  if errorlevel 1 ( echo Ошибка venv. & pause & exit /b 1 )
)
set VENV_PYTHON=%ROOT%\.venv\Scripts\python.exe
set VENV_PIP=%ROOT%\.venv\Scripts\pip.exe
echo Зависимости Python...
"%VENV_PIP%" install -r requirements.txt -q 2>nul || "%VENV_PIP%" install -r requirements.txt
echo.

REM Ollama (опционально)
where ollama >nul 2>&1 && (
  curl -sf http://localhost:11434/api/tags >nul 2>&1 || (
    echo Запуск Ollama...
    start /B ollama serve
    timeout /t 3 /nobreak >nul
  )
  if not defined OLLAMA_MODEL set OLLAMA_MODEL=qwen2.5:3b
  ollama list 2>nul | findstr /C:"%OLLAMA_MODEL%" >nul 2>&1 || (
    echo Скачиваю модель %OLLAMA_MODEL%...
    ollama pull %OLLAMA_MODEL%
  )
) || (
  echo Ollama не найден (опционально). Для транскрипций: https://ollama.com
)
echo.

echo Запуск сервера Roadmap на http://127.0.0.1:5051
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:5051"
"%VENV_PYTHON%" app.py
echo Сервер остановлен.
pause
