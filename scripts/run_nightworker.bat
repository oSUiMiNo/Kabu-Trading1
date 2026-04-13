@echo off
cd /d C:\Users\osuim\Dev\Investment_2026_0413\NightWorker\src
C:\Users\osuim\.local\bin\uv.exe run python main.py --max-reviews 20
if errorlevel 1 exit /b %errorlevel%
C:\Users\osuim\.local\bin\uv.exe run python words_consolidator.py
if errorlevel 1 exit /b %errorlevel%
