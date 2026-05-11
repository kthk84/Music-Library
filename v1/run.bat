@echo off
REM MP3 Tag Cleaner - Startup Script for Windows

echo 🎵 MP3 Tag Cleaner
echo ==================
echo.

REM Check if Python is installed
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo ❌ Python is not installed!
    echo Please install Python from https://www.python.org/downloads/
    pause
    exit /b 1
)

python --version
echo.

REM Check if dependencies are installed
python -c "import flask" >nul 2>nul
if %errorlevel% neq 0 (
    echo 📦 Installing dependencies...
    pip install -r requirements.txt
    echo.
)

echo 🚀 Starting application...
echo Open your browser to: http://localhost:5000
echo.
echo Press Ctrl+C to stop the server
echo.

python app.py
pause

