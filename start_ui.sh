#!/bin/bash
# BioModStack UI Service Manager
# Usage: ./start_ui.sh [start|stop|status]

PROJECT_DIR="/home/dalab/ProteinDJ_fork/Protein-De-Novo-Modification-and-Design-Platform"
API_LOG="/tmp/biomodstack_api.log"
FRONTEND_LOG="/tmp/biomodstack_frontend.log"

# Load NVM if available to ensure correct Node version
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

# Load NVM if available to ensure correct Node version
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

# Add uv to PATH (usually in $HOME/.cargo/bin or $HOME/.local/bin)
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

check_port() {
    local port=$1
    if lsof -i :$port > /dev/null; then
        echo "   ⚠️  Port $port is still in use. Killing process..."
        fuser -k -n tcp $port > /dev/null 2>&1
    fi
}

start_services() {
    echo "🚀 Starting BioModStack services..."
    
    # Ensure uv is available
    if ! command -v uv &> /dev/null; then
        echo "❌ 'uv' not found. Please install uv or check your PATH."
        exit 1
    fi

    # Start API
    cd "$PROJECT_DIR/platform/api"
    # Check/Kill port 8000
    check_port 8000
    
    echo "   Starting API with uv..."
    nohup uv run uvicorn main:app --reload --port 8000 --host 0.0.0.0 > "$API_LOG" 2>&1 &
    API_PID=$!
    echo "   API started (PID: $API_PID) → http://localhost:8000"
    
    sleep 3
    
    # Start Frontend
    cd "$PROJECT_DIR/platform/frontend"
    # Check/Kill port 5173
    check_port 5173
    
    echo "   Starting Frontend..."
    nohup npm run dev > "$FRONTEND_LOG" 2>&1 &
    FRONTEND_PID=$!
    echo "   Frontend started (PID: $FRONTEND_PID) → http://localhost:5173"
    
    echo "✅ Services started! Logs at: $API_LOG, $FRONTEND_LOG"
}

stop_services() {
    echo "🛑 Stopping BioModStack services..."
    pkill -f "uvicorn.*main:app" 2>/dev/null && echo "   API stopped" || echo "   API not running"
    pkill -f "vite" 2>/dev/null && echo "   Frontend stopped" || echo "   Frontend not running"
    
    # Force kill ports if still open
    check_port 8000
    check_port 5173
    
    echo "✅ Services stopped"
}

status_services() {
    echo "📊 BioModStack Service Status:"
    if pgrep -f "uvicorn.*main:app" > /dev/null; then
        echo "   API: ✅ Running ($(pgrep -f 'uvicorn.*main:app'))"
    else
        echo "   API: ❌ Stopped"
    fi
    
    if pgrep -f "vite" > /dev/null; then
        echo "   Frontend: ✅ Running ($(pgrep -f 'vite'))"
    else
        echo "   Frontend: ❌ Stopped"
    fi
}

case "${1:-start}" in
    start)
        start_services
        ;;
    stop)
        stop_services
        ;;
    status)
        status_services
        ;;
    restart)
        stop_services
        sleep 2
        start_services
        ;;
    *)
        echo "Usage: $0 {start|stop|status|restart}"
        exit 1
        ;;
esac
