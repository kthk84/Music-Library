#!/bin/bash
# Quick smoke test for MP3 Cleaner app. App must be running (e.g. python app.py).
# Port: default 5002 (same as app.py). Override: MP3CLEANER_PORT=5002 bash test_app.sh
cd "$(dirname "$0")"
PORT="${MP3CLEANER_PORT:-5002}"
BASE="http://127.0.0.1:$PORT"
echo "=== MP3 Cleaner Smoke Test (port $PORT) ==="
echo ""
echo "1. Testing bootstrap API..."
start=$(python3 -c "import time; print(int(time.time()*1000))")
code=$(curl -s -o /tmp/bootstrap.json -w "%{http_code}" --max-time 30 "$BASE/api/shazam-sync/bootstrap" 2>/dev/null || echo "000")
end=$(python3 -c "import time; print(int(time.time()*1000))")
dur=$(( (end - start) / 1000 ))
echo "   HTTP $code in ${dur}s"
if [ "$code" = "200" ]; then
  shazam=$(python3 -c "import json; d=json.load(open('/tmp/bootstrap.json')); print(d.get('status',{}).get('shazam_count',0))" 2>/dev/null || echo "?")
  folders=$(python3 -c "import json; d=json.load(open('/tmp/bootstrap.json')); print(len(d.get('settings',{}).get('destination_folders',[])))" 2>/dev/null || echo "?")
  echo "   Shazam: $shazam, Folders: $folders"
else
  echo "   FAILED"
fi
echo ""
echo "2. Testing settings API..."
code2=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$BASE/api/settings" 2>/dev/null || echo "000")
echo "   HTTP $code2"
echo ""
echo "3. Testing page load..."
code3=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$BASE/" 2>/dev/null || echo "000")
echo "   HTTP $code3"
echo ""
echo "4. Testing status API..."
code4=$(curl -s -o /tmp/status.json -w "%{http_code}" --max-time 10 "$BASE/api/shazam-sync/status" 2>/dev/null || echo "000")
echo "   HTTP $code4"
echo ""
echo "5. Testing mutation-log API..."
code5=$(curl -s -o /tmp/mutation.json -w "%{http_code}" --max-time 5 "$BASE/api/shazam-sync/mutation-log" 2>/dev/null || echo "000")
echo "   HTTP $code5"
echo ""
if [ "$code" = "200" ] && [ "$code2" = "200" ] && [ "$code3" = "200" ] && [ "$code4" = "200" ] && [ "$code5" = "200" ]; then
  echo "=== All tests PASSED ==="
else
  echo "=== Some tests FAILED ==="
  exit 1
fi
