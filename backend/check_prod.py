import os, sys
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
sys.path.insert(0, "/www/wwwroot/campus_resource/backend")
import django
django.setup()

from apps.accounts.models import College, SchoolProfile

print("=== 学院 dean 字段检查 ===")
for c in College.objects.all().order_by("id"):
    d = c.dean.strip() if c.dean else ""
    print(f"  [{c.id:2d}] {c.name} | dean={d!r}")

print("\n=== SchoolProfile 关键字段 ===")
sp = SchoolProfile.objects.first()
print(f"  name_full  = {sp.name_full!r}")
print(f"  motto      = {sp.motto!r}")
print(f"  president  = {sp.president!r}")

print("\n=== 测试 build_school_knowledge_from_db ===")
from utils.rag import build_school_knowledge_from_db
kb = build_school_knowledge_from_db()
print(f"  生成字符数: {len(kb)}")
print(f"  前 300 字: {kb[:300]!r}")
print(f"  含 '信息与商务管理': {'信息与商务管理' in kb}")
print(f"  含 '院长': {'院长' in kb}")
print(f"  含张应辉: {'张应辉' in kb}")
