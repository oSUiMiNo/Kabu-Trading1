@echo off
cd /d "%~dp0src"
start "" "http://localhost:8080"
uv run python main.py
