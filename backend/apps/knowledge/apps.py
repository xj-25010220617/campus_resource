from django.apps import AppConfig


class KnowledgeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.knowledge'

    def ready(self):
        # 注册版本链恢复信号（删除文档时把被替代的旧版重新指向后继）
        from . import versioning  # noqa: F401
