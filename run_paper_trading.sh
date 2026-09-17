#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=========================================================="
echo "    🚀 Starting OpenAlgo - Indian Paper Trading Mode     "
echo "=========================================================="

# 1. Ensure Python Virtual Environment
if [ ! -d "$DIR/.venv" ]; then
    echo "⚠️  .venv not found. Creating virtual environment with uv..."
    uv sync
fi

# 2. Ensure Analyze Mode is enabled in SQLite
"$DIR/.venv/bin/python" -c "
import sqlite3, os
db_path = os.path.join('$DIR', 'db', 'openalgo.db')
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute('UPDATE settings SET analyze_mode = 1;')
    conn.commit()
    conn.close()
" 2>/dev/null || true

# 3. Read configured port or default to 5001
PORT=$(grep -E "^FLASK_PORT=" "$DIR/.env" | cut -d'=' -f2 | tr -d " '\"")
PORT=${PORT:-5001}

echo "Starting OpenAlgo on http://127.0.0.1:$PORT ..."
echo "Mode: Analyze Mode / Indian Paper Trading (₹1 Crore Simulated Capital)"
echo "Supported: NSE, NFO, BSE, MCX (Equities, Futures, Options)"
echo ""
echo "Press Ctrl+C to stop the server."
echo "=========================================================="

# Automatically open browser after 2 seconds in background
(sleep 2 && open "http://127.0.0.1:$PORT") &

exec "$DIR/.venv/bin/python" app.py
