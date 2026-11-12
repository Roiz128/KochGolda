@echo off
rem Останавливает бота и его цикл перезапуска.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and ($_.CommandLine -match 'run_bot\.bat' -or $_.CommandLine -match 'main\.py') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Бот остановлен.
pause
