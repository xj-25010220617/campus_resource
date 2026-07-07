"""已废弃：资源分类系统已移除，不再需要初始化 Category 数据。

如需清理旧分类数据，请在 Django shell 中执行：
    from apps.classify.models import Category
    Category.objects.all().delete()
"""
print('[fix_categories.py] 已废弃：资源分类系统已移除，不再执行任何操作。')
