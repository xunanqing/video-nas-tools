@echo off
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
python -m app.dedup.cli %*
exit /b %errorlevel%
