#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Tesla Inventory Tracker — VPS Setup Script
# Run this on a fresh Ubuntu 22.04+ VPS.
#
# Usage:
#   1. scp the tesla-tracker folder to your VPS
#   2. ssh into the VPS
#   3. cd tesla-tracker
#   4. chmod +x setup-vps.sh
#   5. sudo ./setup-vps.sh
#   6. Edit .env with your notification settings
#   7. sudo docker compose up -d
# ─────────────────────────────────────────────────────────────
set -euo pipefail

echo "========================================="
echo " Tesla Inventory Tracker — VPS Setup"
echo "========================================="

# ── 1. Install Docker if not present ────────────────────────
if ! command -v docker &> /dev/null; then
    echo "[1/5] Installing Docker..."
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    systemctl enable --now docker
    echo "    Docker installed."
else
    echo "[1/5] Docker already installed, skipping."
fi

# ── 2. Create .env if it doesn't exist ──────────────────────
if [ ! -f .env ]; then
    echo "[2/5] Creating .env from template..."
    cp .env.example .env
    echo "    Created .env — EDIT THIS FILE with your settings before starting."
else
    echo "[2/5] .env already exists, skipping."
fi

# ── 3. Create data directory for SQLite persistence ─────────
echo "[3/5] Creating data directory..."
mkdir -p ./data

# ── 4. Build the Docker image ───────────────────────────────
echo "[4/5] Building Docker image (this downloads Chromium, ~2 min)..."
docker compose build

# ── 5. Set up UFW firewall rule ─────────────────────────────
if command -v ufw &> /dev/null; then
    echo "[5/5] Adding firewall rule for port 8247..."
    ufw allow 8247/tcp 2>/dev/null || true
else
    echo "[5/5] ufw not found, skipping firewall config."
fi

echo ""
echo "========================================="
echo " Setup complete!"
echo "========================================="
echo ""
echo " Next steps:"
echo ""
echo "   1. Edit your settings:"
echo "      nano .env"
echo ""
echo "   2. Start the tracker:"
echo "      sudo docker compose up -d"
echo ""
echo "   3. View the dashboard:"
echo "      http://<your-vps-ip>:8247"
echo ""
echo "   4. View logs:"
echo "      sudo docker compose logs -f"
echo ""
echo "   5. Stop:"
echo "      sudo docker compose down"
echo ""
echo "   6. Restart after .env changes:"
echo "      sudo docker compose down && sudo docker compose up -d"
echo ""
