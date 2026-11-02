@echo off
cd /d "%~dp0"
echo Running CLI test (1 video only): Arabic_Youtube_URLs.xlsx, page 2...
python cli_main.py "..\..\Arabic_Youtube_URLs.xlsx" --limit 1
pause
