import os, sys
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
sys.path.insert(0, "/www/wwwroot/campus_resource/backend")
import django
django.setup()

from apps.accounts.models import College

print("=== 院长字段 ===")
for c in College.objects.all().order_by("id"):
    print(f"  {c.name} : dean={c.dean!r}")

print("\n=== RAG 上下文统计 ===")
from utils.rag import build_school_knowledge_from_db
kb = build_school_knowledge_from_db()
print(f"  总字符数: {len(kb)}")
print(f"  院长出现次数: {kb.count('院长')}")
print(f"  含赵媛媛: {'赵媛媛' in kb}")
print(f"  含宁多彪: {'宁多彪' in kb}")
print(f"  含罗频捷: {'罗频捷' in kb}")
print(f"  含王雷: {'王雷' in kb}")
print(f"  含张应辉校长: {'张应辉' in kb}")

# 找信息与商务管理学院块
import re
m = re.search(r'### 3\.[^#]+?(?=###|$)', kb, re.DOTALL)
if m:
    print("\n  --- 信息与商务管理学院块 ---")
    print(m.group().strip()[:700])
