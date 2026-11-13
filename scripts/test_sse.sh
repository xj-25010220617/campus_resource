#!/usr/bin/env bash
# 冒烟测试：SSE 问答接口
set +e
echo "=== SSE Ask Smoke Test ==="
curl -s -N -X POST http://127.0.0.1:3000/api/chat/ask/ \
  -H "Content-Type: application/json" \
  -d '{"question": "图书馆开放时间是几点？"}' \
  --max-time 120 2>&1 | head -c 8000
echo ""
echo "=== DONE ==="
