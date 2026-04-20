@echo off
setlocal EnableDelayedExpansion
REM Запуск из корня time_case_board: call scripts\ensure-web-dist.bat
cd /d "%~dp0\.."

if exist "web\dist\index.html" exit /b 0

if exist "web\dist.zip" (
  echo [INFO] Нет web\dist\ — распаковка web\dist.zip ...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%CD%\web\dist.zip' -DestinationPath '%CD%\web' -Force"
  if errorlevel 1 (
    echo [WARN] Expand-Archive завершился с ошибкой — пробую npm...
  )
  if exist "web\__MACOSX" rmdir /s /q "web\__MACOSX" 2>nul
)

if exist "web\dist\index.html" (
  echo [INFO] Фронт готов ^(из dist.zip^).
  exit /b 0
)

where npm 1>nul 2>nul
if errorlevel 1 (
  echo [WARN] Нет web\dist\index.html и npm не в PATH — будет заглушка.
  echo       Установите Node.js или распакуйте web\dist.zip в web\dist\
  exit /b 0
)

if not exist "web\package.json" (
  echo [WARN] Нет web\package.json
  exit /b 0
)

echo [INFO] npm install ^&^& npm run build ...
pushd web
call npm install
if errorlevel 1 (
  popd
  echo [ERROR] npm install failed
  exit /b 1
)
call npm run build
if errorlevel 1 (
  popd
  echo [ERROR] npm run build failed
  exit /b 1
)
popd
exit /b 0
