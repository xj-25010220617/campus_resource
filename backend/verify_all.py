import os, sys
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
sys.path.insert(0, "/www/wwwroot/campus_resource/backend")
import django
django.setup()

from apps.accounts.models import College, SchoolProfile

sp = SchoolProfile.objects.first()
print("=== 校领导 ===")
print(f"  党委书记: {sp.party_secretary!r}")
print(f"  校长: {sp.president!r}")
print(f"  leaders_text 字符数: {len(sp.leaders_text or '')}")
if sp.leaders_text:
    for line in sp.leaders_text.strip().splitlines():
        print(f"    {line}")

print("\n=== 学院 dean + secretary ===")
for c in College.objects.filter(secretary__gt='').order_by('id'):
    print(f"  {c.name:20s} | dean={c.dean!r:20s} | secretary={c.secretary!r}")

print("\n=== RAG 上下文 ===")
from utils.rag import build_school_knowledge_from_db
kb = build_school_knowledge_from_db()
print(f"  总字符: {len(kb)}")
print(f"  含赖廷谦: {'赖廷谦' in kb}")
print(f"  含沈洪科: {'沈洪科' in kb}")
print(f"  含罗玺:   {'罗玺' in kb}")
print(f"  含赵壁:   {'赵壁' in kb}")
print(f"  含校领导班子: {'校领导班子' in kb}")
# 打印校领导班子块
import re
m = re.search(r'## 学校概况.*?校领导班子[\s\S]*?(?=\n## )', kb)
if m:
    print("\n  --- 校领导班子块 ---")
    print(m.group().strip()[:500])
