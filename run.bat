@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%src"
"%ROOT%.venv\Scripts\python.exe" -m live_telemetry_evo %*
endlocal
