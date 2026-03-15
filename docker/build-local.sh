#!/bin/bash
# Build BabelCast all-in-one Docker image locally (Mac CPU version)
#
# Usage:
#   ./build-local.sh
#   docker run -p 8080:8080 \
#     -e CONF_SOURCE_LANG=pt \
#     -e CONF_TARGET_LANG=en \
#     babelcast-local

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BABELCAST_REPO="${BABELCAST_REPO:-$HOME/babelcast}"

echo "=== BabelCast Local Build ==="
echo "Source repo: $BABELCAST_REPO"
echo ""

# Check source repo exists
if [ ! -d "$BABELCAST_REPO/teams-bot" ]; then
    echo "ERROR: babelcast repo not found at $BABELCAST_REPO"
    echo "Set BABELCAST_REPO env var to point to your local babelcast repo"
    exit 1
fi

# Copy source files into build context
echo "[1/3] Copying teams-bot..."
rm -rf "$SCRIPT_DIR/teams-bot"
cp -r "$BABELCAST_REPO/teams-bot" "$SCRIPT_DIR/teams-bot"

echo "[2/3] Copying pipeline API..."
rm -rf "$SCRIPT_DIR/api"
cp -r "$BABELCAST_REPO/docker/api" "$SCRIPT_DIR/api"

echo "[3/3] Building Docker image..."
docker build \
    -f "$SCRIPT_DIR/Dockerfile.all-in-one-local" \
    -t babelcast-local \
    "$SCRIPT_DIR"

echo ""
echo "=== Build complete ==="
echo ""
echo "Run with:"
echo "  docker run -p 8080:8080 \\"
echo "    -e CONF_SOURCE_LANG=pt \\"
echo "    -e CONF_TARGET_LANG=en \\"
echo "    babelcast-local"
