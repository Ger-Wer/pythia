#!/bin/bash
set -euo pipefail

# === Pythia Setup Script ===
# Запуск: curl -sSL https://raw.githubusercontent.com/YOUR_USER/pythia/main/scripts/setup.sh | bash
# Или после git clone: bash scripts/setup.sh

echo "=== Pythia Setup ==="

echo "[1/8] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq git curl python3 python3-pip python3-venv sqlite3 jq

echo "[2/8] Installing Bun..."
if ! command -v bun &> /dev/null; then
    curl -fsSL https://bun.sh/install | bash
    export BUN_INSTALL="$HOME/.bun"
    export PATH="$BUN_INSTALL/bin:$PATH"
    echo 'export BUN_INSTALL="$HOME/.bun"' >> ~/.bashrc
    echo 'export PATH="$BUN_INSTALL/bin:$PATH"' >> ~/.bashrc
fi
echo "Bun version: $(bun --version)"

echo "[3/8] Setting up project..."
PROJECT_DIR="$HOME/pythia"
if [ ! -d "$PROJECT_DIR" ]; then
    echo "Clone the repo first: git clone git@github.com:YOUR_USER/pythia.git"
    exit 1
fi
cd "$PROJECT_DIR"

echo "[4/8] Cloning Dexter..."
if [ ! -d "dexter" ]; then
    git clone https://github.com/virattt/dexter.git dexter
    cd dexter && bun install && cd ..
fi

echo "[5/8] Installing Bun dependencies..."
bun install

echo "[6/8] Setting up Python environment..."
cd python
python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt
deactivate
cd ..

echo "[7/8] Creating directories..."
mkdir -p data logs

echo "[8/8] Setting up environment..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Edit .env with your API keys: nano $PROJECT_DIR/.env"
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env:           nano $PROJECT_DIR/.env"
echo "  2. Day 0 check:         bash scripts/day0_check.sh"
echo "  3. Test single run:     bun run src/main.ts"
echo "  4. Install crontab:     crontab scripts/crontab.txt"
echo ""
