import json
import sys
import urllib.request

URL = "http://127.0.0.1:8000/api/admin/logs/dashboard/?period=day"

try:
    with urllib.request.urlopen(URL, timeout=10) as resp:
        body = resp.read().decode("utf-8")
        print("HTTP_STATUS", resp.status)
        data = json.loads(body)
        print("period", data.get("period"))
        print("start_at", data.get("start_at"))
        print("end_at", data.get("end_at"))
        print("totals_keys", list(data.get("totals", {}).keys()))
        print("totals", json.dumps(data.get("totals"), ensure_ascii=False))
        print("blocks_keys", list(data.get("blocks", {}).keys()))
        upload = data.get("blocks", {}).get("upload", {})
        print("upload.total", upload.get("total"))
        print("upload.total_size (区间值)", upload.get("total_size"))
except Exception as e:
    print("ERR", type(e).__name__, e)
    sys.exit(1)