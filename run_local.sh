#!/usr/bin/env bash
# Festival CRM — run on your own machine (Mac / Linux).
# Double-click in Finder, or run:  bash run_local.sh
# Then open http://localhost:8000  (data is saved in festival_crm.db right here).
set -e
cd "$(dirname "$0")"

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "Python 3 isn't installed. Get it from https://www.python.org/downloads/ then run this again."
  read -n 1 -s -r -p "Press any key to close..."; exit 1
fi

if [ ! -d ".venv" ]; then
  echo "First run: setting up (about a minute)…"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

# Open the browser once the server is up.
( sleep 2; (command -v open >/dev/null && open http://localhost:8000) \
    || (command -v xdg-open >/dev/null && xdg-open http://localhost:8000) || true ) &

echo ""
echo "Festival CRM is running →  http://localhost:8000"
echo "Leave this window open. Press Ctrl+C to stop."
echo ""
exec uvicorn main:app --host 127.0.0.1 --port 8000
