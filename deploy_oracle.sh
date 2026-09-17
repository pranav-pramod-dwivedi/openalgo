#!/bin/bash
# ==============================================================================
# OpenAlgo 1-Click Oracle Cloud Always-Free Deployment Script
# Target OS: Ubuntu 22.04 / 24.04 LTS (x86_64 or ARM64 Ampere)
# ==============================================================================

set -e

echo "===================================================================="
echo "🚀 Starting OpenAlgo & Autonomous Trading Agent Deployment on Oracle Cloud"
echo "===================================================================="

# 1. Update system packages
echo "📦 Updating system packages..."
sudo apt-get update -y && sudo apt-get upgrade -y
sudo apt-get install -y python3-pip python3-venv git curl ufw sqlite3 build-essential

# 2. Configure Firewall (UFW)
echo "🛡️ Configuring host firewall..."
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 5001/tcp
sudo ufw --force enable || true

# 3. Install Node.js (v20 LTS)
if ! command -v node &> /dev/null; then
    echo "🟢 Installing Node.js 20 LTS..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi

# 4. Setup Python Virtual Environment
echo "🐍 Setting up Python virtual environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 5. Build React Frontend Bundle
echo "⚛️ Building React Frontend with Dual INR/USD Tabs..."
cd frontend
npm install
npm run build
cd ..

# 6. Ensure Database Directory Exists
mkdir -p db instance

# 7. Create Systemd Service for 24/7 Persistent Uptime
echo "⚙️ Creating Systemd 24/7 background service..."
APP_DIR=$(pwd)
USER_NAME=$(whoami)

sudo tee /etc/systemd/system/openalgo.service > /dev/null <<SERVICE_EOF
[Unit]
Description=OpenAlgo 24/7 Trading Server & WebSocket Hub
After=network.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/app.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICE_EOF

# 8. Reload and Start Service
echo "🔄 Starting OpenAlgo 24/7 service..."
sudo systemctl daemon-reload
sudo systemctl enable openalgo
sudo systemctl restart openalgo

echo "===================================================================="
echo "✅ OPENALGO DEPLOYMENT COMPLETE!"
echo "===================================================================="
echo "Access your OpenAlgo dashboard at:"
echo "👉 http://$(curl -s ifconfig.me):5001"
echo ""
echo "Manage service:"
echo "  sudo systemctl status openalgo"
echo "  sudo systemctl restart openalgo"
echo "  sudo journalctl -u openalgo -f"
echo "===================================================================="
