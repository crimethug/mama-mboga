@echo off
setlocal
cd /d "%~dp0"

curl.exe --silent --fail --max-time 2 http://127.0.0.1:5000/ >nul 2>nul
if errorlevel 1 (
  start "Mwangis Shop Inventory Server" /min cmd /k python app.py
  for /l %%i in (1,1,15) do (
    curl.exe --silent --fail --max-time 1 http://127.0.0.1:5000/ >nul 2>nul
    if not errorlevel 1 goto openapp
    ping -n 2 127.0.0.1 >nul
  )
  echo Mwangis Shop Inventory System could not start. Check the server window for errors.
  pause
  exit /b 1
)

:openapp
start "" "http://127.0.0.1:5000/"
exit /b 0
