@echo off
chcp 65001 >nul
title DungeonHub
cd /d "%~dp0"

echo ==== DungeonHub ====
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python не найден. Установите его с https://www.python.org/downloads/
  echo     и при установке отметьте галочку "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Сейчас откроется Блокнот с настройками.
  echo Вставьте ключ Gemini после GEMINI_API_KEY= , сохраните файл и закройте Блокнот.
  echo.
  pause
  start /wait notepad ".env"
)

if not exist ".venv\Scripts\python.exe" (
  echo Первый запуск: готовлю окружение, это займёт минуту...
  python -m venv .venv
  if errorlevel 1 ( echo [!] Не удалось создать окружение Python & pause & exit /b 1 )
)

echo Проверяю зависимости...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 ( echo [!] Не удалось установить зависимости & pause & exit /b 1 )

for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"
if not exist "data" mkdir "data"
set "DB_PATH=%~dp0data\dungeonhub.db"

echo Запускаю сайт...
start "DungeonHub - сервер (не закрывайте во время игры)" ".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

where cloudflared >nul 2>nul
if errorlevel 1 (
  echo.
  echo [!] cloudflared не найден, поэтому ссылки для друзей не будет.
  echo     Установите его командой в PowerShell:  winget install --id Cloudflare.cloudflared
  echo     и запустите start.bat ещё раз. Сайт пока доступен только вам.
) else (
  echo Открываю туннель для друзей...
  start "DungeonHub - туннель (ссылка для друзей здесь)" cloudflared tunnel --no-autoupdate --protocol http2 --url http://localhost:8000
  echo.
  echo Ссылка для друзей появится через несколько секунд в окне "туннель":
  echo строка вида https://....trycloudflare.com
)

timeout /t 3 >nul
start "" http://localhost:8000
echo.
echo Готово. Чтобы остановить игру, закройте окна "сервер" и "туннель".
echo.
pause
