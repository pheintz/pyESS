@echo off
REM ============================================
REM   pyESS - run this file
REM ============================================
cd /d "%~dp0"
python src\pyESS_app.py %*
if errorlevel 1 pause
