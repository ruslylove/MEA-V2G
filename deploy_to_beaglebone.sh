#!/bin/bash

# Configuration
REMOTE_USER="debian"
REMOTE_HOST="beaglebone.local"
REMOTE_DIR="~/MEA-V2G"
LOCAL_DIR="$(pwd)"

# Ensure we are in the project root
if [ ! -f "$LOCAL_DIR/SpiAdapter.py" ]; then
    echo "Error: Please run this script from the project root (/Users/rus/Development/MEA-V2G)"
    exit 1
fi

echo "🚀 Syncing files to $REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR..."

# Sync files using rsync
# -a: archive mode (preserves permissions, symlinks, etc.)
# -v: verbose
# -z: compress during transfer
# --delete: delete files on remote that are no longer on local
# --exclude: skip unnecessary files
rsync -avz --delete \
    --exclude='.git/' \
    --exclude='.venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.agent/' \
    --exclude='.gemini/' \
    --exclude='pru/gen/' \
    --exclude='pru/*.out' \
    "$LOCAL_DIR/" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/"

if [ $? -eq 0 ]; then
    echo "✅ Sync complete!"
    echo "To run your code, SSH into the BeagleBone:"
    echo "ssh $REMOTE_USER@$REMOTE_HOST"
else
    echo "❌ Sync failed. Check your connection or SSH keys."
fi
