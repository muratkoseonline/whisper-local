@echo off
setlocal
set "ROOT=C:\Users\Murat\whisper-local"
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
set "SERVER=%ROOT%\server.py"
set "URL=http://127.0.0.1:8765"

rem Start the local server only if this port is not already in use.
netstat -ano | findstr /R /C:":8765 .*LISTENING" >nul
if errorlevel 1 (
  start "Whisper Local Server" /min "%PYTHON%" "%SERVER%"
  timeout /t 2 /nobreak >nul
)
start "" "%URL%"
endlocal
