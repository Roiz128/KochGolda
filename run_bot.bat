@echo off
rem Запускает бота и перезапускает его, если он упал (например, пока ещё не включился VPN).
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
:loop
python main.py >> bot.log 2>&1
echo [%date% %time%] Бот остановился, перезапуск через 15 секунд >> bot.log
timeout /t 15 /nobreak >nul
goto loop
