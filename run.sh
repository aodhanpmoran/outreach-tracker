#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "Setting up virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -q flask
else
    source venv/bin/activate
fi

echo ""
echo "Starting Outreach Tracker..."
python app.py
