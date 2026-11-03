"""清理系统自动分类目录，把其中文档迁回待分类。

从此所有资料目录均由部门用户自行创建，AI 不再生成默认目录。
"""
from django.db import migrations


def purge_system_folders(apps, schema_editor):
    KnowledgeFolder = apps.get_model('knowledge', 'KnowledgeFolder')
    KnowledgeDoc = apps.get_model('knowledge', 'KnowledgeDoc')

    system_folders = KnowledgeFolder.objects.filter(is_system=True)
    folder_ids = list(system_folders.values_list('id', flat=True))
    if not folder_ids:
        return

    moved = KnowledgeDoc.objects.filter(folder_id__in=folder_ids).update(folder=None)
    deleted_count, _ = system_folders.delete()
    print(
        f'[0013] 删除系统目录 {len(folder_ids)} 个（含子目录共 {deleted_count} 条），'
        f'{moved} 个文档已迁回待分类',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge', '0012_knowledgefolder_knowledgedoc_folder'),
    ]

    operations = [
        migrations.RunPython(purge_system_folders, migrations.RunPython.noop),
    ]
