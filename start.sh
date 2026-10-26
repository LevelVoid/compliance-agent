#!/usr/bin/env bash
# ==============================================================================
# RegionGuard: Synchronized Client & Backend Runner
# Starts FastAPI backend (port 8000) & Next.js frontend (port 3000)
# Cleanly terminates both on Ctrl+C (SIGINT/SIGTERM)
# ==============================================================================

set -eo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
CLIENT_PORT="${CLIENT_PORT:-3000}"
HOST="${HOST:-127.0.0.1}"
RESTART_FLAG=0

for arg in "$@"; do
    if [ "$arg" == "--restart" ] || [ "$arg" == "-r" ]; then
        RESTART_FLAG=1
    fi
done

# ANSI Colors
BOLD="\033[1m"
GREEN="\033[0;32m"
CYAN="\033[0;36m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
RESET="\033[0m"

echo -e "${BOLD}${CYAN}"
echo "=============================================================="
echo "          🛡️  RegionGuard: Compliance Agent Runner            "
echo "=============================================================="
echo -e "${RESET}"

# 1. Environment & Pre-flight Checks
cd "$ROOT_DIR"

# Python check
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}[!] Python virtual environment not found (.venv). Creating...${RESET}"
    python3 -m venv .venv
    echo -e "${GREEN}[✓] Created .venv. Installing backend dependencies...${RESET}"
    .venv/bin/pip install --upgrade pip -q
    .venv/bin/pip install -r requirements.txt -q
fi

PY_BIN="$ROOT_DIR/.venv/bin/python"
UVICORN_BIN="$ROOT_DIR/.venv/bin/uvicorn"

if [ ! -x "$UVICORN_BIN" ]; then
    echo -e "${YELLOW}[!] uvicorn not found in .venv. Installing requirements...${RESET}"
    "$PY_BIN" -m pip install -r requirements.txt -q
fi

# Node & Client check
if ! command -v node >/dev/null 2>&1; then
    echo -e "${RED}[ERROR] Node.js is required but not found in PATH.${RESET}"
    exit 1
fi

if [ ! -d "$ROOT_DIR/client" ]; then
    echo -e "${RED}[ERROR] client/ directory not found in root: $ROOT_DIR${RESET}"
    exit 1
fi

if [ ! -d "$ROOT_DIR/client/node_modules" ]; then
    echo -e "${YELLOW}[!] client/node_modules not found. Installing npm dependencies...${RESET}"
    (cd "$ROOT_DIR/client" && npm install)
fi

# Ensure client .env.local matches backend port
CLIENT_ENV="$ROOT_DIR/client/.env.local"
EXPECTED_API_URL="http://localhost:${BACKEND_PORT}"
if [ ! -f "$CLIENT_ENV" ] || ! grep -q "NEXT_PUBLIC_API_URL" "$CLIENT_ENV"; then
    echo -e "${CYAN}[i] Generating client/.env.local (NEXT_PUBLIC_API_URL=${EXPECTED_API_URL})${RESET}"
    echo "NEXT_PUBLIC_API_URL=${EXPECTED_API_URL}" > "$CLIENT_ENV"
fi

# 2. Port conflict detection & handling
kill_port_owner() {
    local port=$1
    local name=$2
    local pids
    pids=$(lsof -ti :"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo -e "${YELLOW}[!] Port $port is in use by PID(s): $pids. Stopping existing $name...${RESET}"
        kill -15 $pids 2>/dev/null || true
        sleep 1
        kill -9 $pids 2>/dev/null || true
    fi
}

BACKEND_ALREADY_RUNNING=0
if [ "$RESTART_FLAG" -eq 1 ]; then
    echo -e "${YELLOW}[!] Force restart specified. Stopping existing backend if running...${RESET}"
    kill_port_owner "$BACKEND_PORT" "backend"
elif curl -s "http://${HOST}:${BACKEND_PORT}/api/health" >/dev/null 2>&1; then
    echo -e "${GREEN}[✓] Active backend detected and healthy on http://${HOST}:${BACKEND_PORT}${RESET}"
    BACKEND_ALREADY_RUNNING=1
else
    kill_port_owner "$BACKEND_PORT" "backend"
fi

kill_port_owner "$CLIENT_PORT" "client"

# 3. Clean exit handling
BACKEND_PID=""
CLIENT_PID=""

cleanup() {
    echo -e "\n${YELLOW}[!] Shutting down RegionGuard services...${RESET}"
    if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo -e "${CYAN}[*] Stopping backend (PID: $BACKEND_PID)...${RESET}"
        kill -15 "$BACKEND_PID" 2>/dev/null || true
    fi
    if [ -n "$CLIENT_PID" ] && kill -0 "$CLIENT_PID" 2>/dev/null; then
        echo -e "${CYAN}[*] Stopping client (PID: $CLIENT_PID)...${RESET}"
        kill -15 "$CLIENT_PID" 2>/dev/null || true
    fi
    sleep 0.5
    echo -e "${GREEN}[✓] Services stopped cleanly.${RESET}"
    exit 0
}

trap cleanup INT TERM EXIT

# 4. Start Backend (FastAPI via Uvicorn) if not already active
if [ "$BACKEND_ALREADY_RUNNING" -eq 0 ]; then
    echo -e "${CYAN}[1/2] Starting FastAPI backend on http://${HOST}:${BACKEND_PORT} ...${RESET}"
    "$UVICORN_BIN" api:app --reload --host "$HOST" --port "$BACKEND_PORT" &
    BACKEND_PID=$!

    # Wait for backend healthcheck
    echo -n "      Waiting for backend to be ready"
    BACKEND_READY=0
    for i in {1..20}; do
        if curl -s "http://${HOST}:${BACKEND_PORT}/api/health" >/dev/null 2>&1; then
            BACKEND_READY=1
            break
        fi
        echo -n "."
        sleep 0.5
    done
    echo ""

    if [ "$BACKEND_READY" -eq 1 ]; then
        echo -e "${GREEN}[✓] Backend is healthy and ready!${RESET}"
    else
        echo -e "${YELLOW}[!] Backend did not respond to /api/health in time, proceeding anyway...${RESET}"
    fi
else
    echo -e "${GREEN}[1/2] Reusing running backend on port ${BACKEND_PORT}.${RESET}"
fi

# 5. Start Frontend (Next.js)
echo -e "${CYAN}[2/2] Starting Next.js client on http://localhost:${CLIENT_PORT} ...${RESET}"
(cd "$ROOT_DIR/client" && npm run dev -- -p "$CLIENT_PORT") &
CLIENT_PID=$!

echo -e "\n${BOLD}${GREEN}==============================================================${RESET}"
echo -e "${BOLD}${GREEN}  🚀 Both Client and Backend are running in sync!            ${RESET}"
echo -e "${BOLD}${GREEN}==============================================================${RESET}"
echo -e "  • ${BOLD}Web Dashboard:${RESET}      http://localhost:${CLIENT_PORT}"
echo -e "  • ${BOLD}FastAPI Backend:${RESET}    http://${HOST}:${BACKEND_PORT}"
echo -e "  • ${BOLD}API Docs (Swagger):${RESET} http://${HOST}:${BACKEND_PORT}/docs"
echo -e "  • ${BOLD}API Health Check:${RESET}   http://${HOST}:${BACKEND_PORT}/api/health"
echo -e "${YELLOW}Press Ctrl+C to stop services.${RESET}\n"

# Wait for process(es) to terminate
if [ -n "$BACKEND_PID" ]; then
    wait -n "$BACKEND_PID" "$CLIENT_PID" 2>/dev/null || wait
else
    wait "$CLIENT_PID" 2>/dev/null || wait
fi
