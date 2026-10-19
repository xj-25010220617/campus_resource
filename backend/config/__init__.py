"""Django config package"""
import pymysql
pymysql.install_as_MySQLdb()

# 确保 Django 进程内 shared_task 绑定到本项目的 Celery app（redis broker），
# 否则 @shared_task 会绑定到 default_app（默认 amqp://localhost），导致
# task.delay() 连接失败并走同步降级，上传请求被索引任务阻塞。
from .celery import app as celery_app  # noqa: E402,F401
