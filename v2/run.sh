#!/bin/bash

# MP3 Tag Cleaner - Startup Script

echo "🎵 MP3 Tag Cleaner"
echo "=================="
echo ""

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed!"
    echo "Please install Python from https://www.python.org/downloads/"
    exit 1
fi

echo "✅ Python found: $(python3 --version)"
echo ""

# Check if dependencies are installed
if ! python3 -c "import flask" &> /dev/null; then
    echo "📦 Installing dependencies..."
    pip3 install -r requirements.txt
    echo ""
fi

echo "🚀 Starting application..."
echo "Open your browser to: http://localhost:5002"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

python3 app.py

