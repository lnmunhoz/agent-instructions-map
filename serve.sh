#!/bin/sh
# Scan your Claude Code setup and open the map: ./serve.sh  (PORT=8765 by default)
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8765}"
python3 build.py
ln -sf ../index.html out/index.html
(cd out && python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 &)
sleep 1
URL="http://127.0.0.1:$PORT/"
if command -v open >/dev/null 2>&1; then open "$URL"
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
else echo "Open $URL"; fi
