@echo off
rem Runs the bot in the background (no window). Logs go to bot.log.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0bot.py"
