@echo off
cd /d "%~dp0"
echo Starting AI Recruitment System...
echo Activating virtual environment...
call venv\Scripts\activate.bat
echo Starting FastAPI server...
python -m app.main
echo.
echo Server has stopped.
pause
