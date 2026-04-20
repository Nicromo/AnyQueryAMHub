@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set PYTHONPATH=%CD%

set "PY=py -3"
%PY% -c "pass" 1>nul 2>nul
if errorlevel 1 set "PY=python"
%PY% -c "pass" 1>nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python 3 not found ^(install from python.org and tick "Add to PATH", or use `py -3`^).
  pause
  exit /b 1
)

REM Окружение только в .venv.win — не пересекается с .venv.unix на Mac/Linux ^(общая папка / синк^).
set "VENV_PY=%ROOT%\.venv.win\Scripts\python.exe"

if not exist "%VENV_PY%" (
  if exist ".venv.win" (
    echo.
    echo [INFO] Папка .venv.win повреждена или неполная — пересоздаю...
    rmdir /s /q ".venv.win" 2>nul
    if exist ".venv.win" (
      echo [ERROR] Не удалось удалить .venv.win — закройте IDE/терминал и удалите папку вручную.
      pause
      exit /b 1
    )
  )
  echo Creating Windows venv in .venv.win ...
  %PY% -m venv .venv.win
  if errorlevel 1 (
    echo [ERROR] Failed to create venv
    pause
    exit /b 1
  )
)

if not exist "%VENV_PY%" (
  echo [ERROR] После создания venv не найден: %VENV_PY%
  pause
  exit /b 1
)

echo [INFO] Python: updating dependencies ^(pip^)...
"%VENV_PY%" -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo [ERROR] pip install failed ^(проверь интернет / VPN / proxy^)
  pause
  exit /b 1
)

"%VENV_PY%" -c "import fastapi, uvicorn, httpx, sqlalchemy; import pydantic_settings" 1>nul 2>nul
if errorlevel 1 (
  echo [WARN] Import check failed — retrying pip with output...
  "%VENV_PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] pip retry failed
    pause
    exit /b 1
  )
  "%VENV_PY%" -c "import fastapi, uvicorn, httpx, sqlalchemy; import pydantic_settings" 1>nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Python packages still not importable after pip.
    pause
    exit /b 1
  )
)

call "%~dp0scripts\ensure-web-dist.bat"
if errorlevel 1 (
  pause
  exit /b 1
)

echo.
echo ============================================================
echo   Case Board — сервер работает, пока открыто ЭТО окно.
echo   Не закрывайте его: иначе в браузере будет ERR_CONNECTION_REFUSED.
echo   Адрес: http://127.0.0.1:8790
echo   Python venv: .venv.win ^(на Mac/Linux — .venv.unix, не конфликтуют^)
echo ============================================================
echo.
echo See SECURITY.md for VPN / compliance notes.
REM Браузер через 2 с — чтобы uvicorn успел занять порт ^(иначе «отказ в соединении»^)
start /min cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8790/"
"%VENV_PY%" -m uvicorn backend.main:app --host 127.0.0.1 --port 8790
pause
