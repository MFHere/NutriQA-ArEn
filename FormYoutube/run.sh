#!/usr/bin/env bash
# YouTube CSV Processor - Start both FastAPI backend and Streamlit frontend
# Usage: ./run.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    echo "Activating virtual environment..."
    source venv/bin/activate
fi

# Load .env (key=value lines; avoid values with spaces or #)
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | grep -v '^[[:space:]]*$' | xargs)
    echo "Loaded .env"
fi

echo "Starting YouTube CSV Processor..."
echo "================================"

# Start FastAPI backend in background
echo "Starting FastAPI backend on http://127.0.0.1:8000 ..."
(
  cd backend
  exec python -m uvicorn main:app --host 127.0.0.1 --port 8000
) &
BACKEND_PID=$!

sleep 3

# Start Streamlit frontend in background
echo "Starting Streamlit UI on http://127.0.0.1:8501 ..."
(
  cd "$SCRIPT_DIR"
  exec streamlit run app.py --server.port 8501 --server.address 127.0.0.1
) &
FRONTEND_PID=$!

echo ""
echo "================================"
echo "  Backend:  http://127.0.0.1:8000  (PID $BACKEND_PID)"
echo "  Frontend: http://127.0.0.1:8501  (PID $FRONTEND_PID)"
echo "================================"
echo "Press Ctrl+C to stop both."

cleanup() {
  echo ""
  echo "Stopping servers..."
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
  exit 0
}
trap cleanup SIGINT SIGTERM

wait
