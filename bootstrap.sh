#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Somnia Bootstrap
#
# First-run setup for a fresh Somnia deployment.
# Generates secrets, writes .env, creates data directories,
# and brings up the stack.
#
# Usage:
#   ./bootstrap.sh              # Interactive (recommended)
#   ./bootstrap.sh --backend env   # Non-interactive, env backend
#   ./bootstrap.sh --backend file  # Non-interactive, file backend (default)
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}→${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
err()   { echo -e "${RED}✗${NC} $*" >&2; }

# ── Pre-flight checks ────────────────────────────────────────────────

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║         🌙  Somnia Bootstrap  🌙         ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

# Check Docker
if ! command -v docker &>/dev/null; then
    err "Docker not found. Install Docker first: https://docs.docker.com/get-docker/"
    exit 1
fi
ok "Docker found: $(docker --version | head -1)"

# Check Docker Compose
if docker compose version &>/dev/null; then
    COMPOSE="docker compose"
    ok "Docker Compose found: $(docker compose version --short)"
elif command -v docker-compose &>/dev/null; then
    COMPOSE="docker-compose"
    ok "Docker Compose found: $(docker-compose --version)"
else
    err "Docker Compose not found. Install it: https://docs.docker.com/compose/install/"
    exit 1
fi

# Check Python 3
if ! command -v python3 &>/dev/null; then
    err "Python 3 not found. Required for secret generation."
    exit 1
fi
ok "Python 3 found: $(python3 --version)"

# Check for existing .env
if [ -f .env ]; then
    warn "Existing .env found — this looks like a re-run."
    echo ""
    read -p "  Overwrite .env? Existing will be backed up. [y/N] " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        info "Aborting. Your .env is unchanged."
        exit 0
    fi
fi

# ── Configuration ─────────────────────────────────────────────────────

BACKEND="${1:-}"

# Strip --backend= prefix if present
if [[ "$BACKEND" == --backend=* ]]; then
    BACKEND="${BACKEND#--backend=}"
elif [[ "$BACKEND" == "--backend" ]]; then
    BACKEND="${2:-}"
fi

if [ -z "$BACKEND" ]; then
    echo ""
    info "Choose a secrets backend:"
    echo ""
    echo "  1) file      Encrypted vault file (recommended)"
    echo "               Secrets stored in data/config/secrets.enc"
    echo "               Encrypted with a master key in .env"
    echo ""
    echo "  2) env       Environment variables only"
    echo "               All secrets in .env (simplest)"
    echo ""
    echo "  3) 1password  1Password integration"
    echo "               Requires 1Password CLI or service account"
    echo ""
    read -p "  Choice [1/2/3, default=1]: " -n 1 -r
    echo ""
    case "$REPLY" in
        2) BACKEND="env" ;;
        3) BACKEND="1password" ;;
        *) BACKEND="file" ;;
    esac
fi

ok "Backend: ${BACKEND}"

# ── Create data directories ──────────────────────────────────────────

info "Creating data directories..."
mkdir -p data/config data/workspaces data/outputs data/runtime data/domains data/documents
ok "Data directories ready"

# ── Install cryptography package if needed ────────────────────────────

if [ "$BACKEND" = "file" ]; then
    if ! python3 -c "import cryptography" &>/dev/null; then
        info "Installing cryptography package..."
        pip3 install --quiet cryptography 2>/dev/null || pip install --quiet cryptography
        ok "cryptography installed"
    fi
fi

# ── Generate secrets ─────────────────────────────────────────────────

echo ""
info "Generating secrets..."

VAULT_PATH="data/config/secrets.enc"

PYTHONPATH="$SCRIPT_DIR" python3 -m shared.secrets.bootstrap \
    --backend "$BACKEND" \
    --env-path .env \
    --vault-path "$VAULT_PATH"

# ── Create Docker network if needed ──────────────────────────────────

if ! docker network inspect mcp-net &>/dev/null; then
    info "Creating Docker network: mcp-net"
    docker network create mcp-net
    ok "Network created"
else
    ok "Docker network mcp-net exists"
fi

# ── Prompt for Claude API key ────────────────────────────────────────

echo ""
info "Somnia needs a Claude API key for the dream cycle."
echo "  Get one at: https://console.anthropic.com"
echo ""
read -p "  Anthropic API key (or press Enter to skip): " API_KEY

if [ -n "$API_KEY" ]; then
    echo "" >> .env
    echo "# Claude API key" >> .env
    echo "ANTHROPIC_API_KEY=${API_KEY}" >> .env
    ok "API key saved to .env"
else
    warn "No API key provided. Add ANTHROPIC_API_KEY to .env before starting."
    warn "The dream cycle won't run without it."
fi

# ── Start the stack ──────────────────────────────────────────────────

echo ""
read -p "$(echo -e ${CYAN}'→'${NC}) Start Somnia now? [Y/n] " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    info "Starting Somnia..."
    $COMPOSE up -d

    echo ""
    info "Waiting for health checks..."
    sleep 10

    # Check health
    HEALTHY=true
    for svc in somnia-postgres quies vigil portal; do
        STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$svc" 2>/dev/null || echo "not found")
        if [ "$STATUS" = "healthy" ]; then
            ok "$svc: healthy"
        elif [ "$STATUS" = "starting" ]; then
            warn "$svc: still starting (give it a minute)"
        else
            err "$svc: $STATUS"
            HEALTHY=false
        fi
    done

    echo ""
    if [ "$HEALTHY" = true ]; then
        ok "All services healthy!"
    else
        warn "Some services still starting. Check: $COMPOSE ps"
    fi
fi

# ── Print connection info ────────────────────────────────────────────

echo ""
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo -e "${GREEN}  Somnia is ready!${NC}"
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo ""
echo "  MCP endpoints (add these in Claude.ai → Settings → MCP):"
echo ""
echo "    Somnia:  http://localhost:8011/somnia"
echo "    Vigil:   http://localhost:8000/vigil"
echo "    Fabrica: http://localhost:8001/fabrica"
echo ""
echo "  Portal:    http://localhost:8002"
echo "  Dashboard: http://localhost:8010/dashboard"
echo ""
echo "  Then open Claude and say hi. Claude will help"
echo "  you set up workspaces, hooks, and everything else."
echo ""
