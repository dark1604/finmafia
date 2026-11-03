@echo off
chcp 65001 >nul
title Кремнієва Долина: Битва Стартапів - Server Launcher
cd /d "%~dp0"

echo ========================================================================
echo   🌱 КРЕМНІЄВА ДОЛИНА: БИТВА СТАРТАПІВ (Silicon Valley: Startup Battle)
echo      Тиждень фінансової грамотності (5-9 класи)
echo ========================================================================
echo.

:: Визначаємо команду запуску Python (віртуальне середовище, python або py)
set "PY_CMD="
if exist ".venv\Scripts\python.exe" (
    set "PY_CMD=.venv\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if %errorlevel% equ 0 (
        set "PY_CMD=python"
    ) else (
        where py >nul 2>nul
        if %errorlevel% equ 0 (
            set "PY_CMD=py"
        )
    )
)

if "%PY_CMD%"=="" (
    echo [ПОМИЛКА] Python не знайдено у системі!
    echo Будь ласка, встановіть Python з https://www.python.org/downloads/
    echo Під час встановлення обов'язково поставте галочку "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

echo [1/3] Перевірка бібліотек Flask та Flask-SocketIO...
%PY_CMD% -c "import flask, flask_socketio, simple_websocket" >nul 2>nul
if %errorlevel% neq 0 (
    echo Встановлення необхідних пакетів із requirements.txt...
    %PY_CMD% -m pip install -r requirements.txt
)

:: Якщо в системі є ngrok, пропонуємо запустити тунель у сусідньому вікні
where ngrok >nul 2>nul
if %errorlevel% equ 0 (
    echo [2/3] Знайдено ngrok! Запускаємо тунель ngrok http 5000 в окремому вікні...
    start "Ngrok Tunnel (Port 5000)" cmd /k "ngrok http 5000"
) else (
    echo [2/3] Локальний режим (для інтернет-тунелю можна запустити: ngrok http 5000)
)

echo [3/3] Відкриваємо Панель Ведучого (/admin) у браузері та запускаємо сервер...
start "" "http://127.0.0.1:5000/admin"

echo.
echo Сервер активний! Не закривайте це вікно під час гри.
echo ------------------------------------------------------------------------
%PY_CMD% app.py

echo.
echo Роботу сервера завершено.
pause
