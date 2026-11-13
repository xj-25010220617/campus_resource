#!/usr/bin/env bash
# SSE 冒烟：只输出事件类型统计 + 关键事件（start/references/done）
set +e
OUT=$(curl -s -N -X POST http://127.0.0.1:3000/api/chat/ask/ \
  -H "Content-Type: application/json" \
  -d '{"question": "图书馆开放时间是几点？"}' \
  --max-time 180 2>&1)

echo "===== event counts ====="
echo "$OUT" | grep -c "^event: token" | sed 's/^/token events: /'
echo "$OUT" | grep -c "^event: start" | sed 's/^/start events: /'
echo "$OUT" | grep -c "^event: references" | sed 's/^/references events: /'
echo "$OUT" | grep -c "^event: done" | sed 's/^/done events: /'

echo ""
echo "===== start / references / done ====="
echo "$OUT" | grep -E "^event: (start|references|done)$" -A 1

echo ""
echo "===== answer head (first 300 chars) ====="
echo "$OUT" | grep '^data: {"content"' | head -40 | sed 's/^data: //' | python3 -c "
import sys, json
s = ''
for line in sys.stdin:
    try:
        s += json.loads(line)
    except Exception:
        pass
print(s[:300])
"
