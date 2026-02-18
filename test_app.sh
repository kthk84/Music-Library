#!/bin/bash
# Quick smoke test for MP3 Cleaner app
cd "$(dirname "$0")"
echo "=== MP3 Cleaner Smoke Test ==="
echo ""
echo "1. Testing bootstrap API..."
start=$(python3 -c "import time; print(int(time.time()*1000))")
code=$(curl -s -o /tmp/bootstrap.json -w "%{http_code}" --max-time 30 http://127.0.0.1:5002/api/shazam-sync/bootstrap 2>/dev/null || echo "000")
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
code2=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:5002/api/settings 2>/dev/null || echo "000")
echo "   HTTP $code2"
echo ""
echo "3. Testing page load..."
code3=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:5002/ 2>/dev/null || echo "000")
echo "   HTTP $code3"
echo ""
if [ "$code" = "200" ] && [ "$code2" = "200" ] && [ "$code3" = "200" ]; then
  echo "=== All tests PASSED ==="
else
  echo "=== Some tests FAILED ==="
  exit 1
fi
