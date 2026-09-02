"""Admin Panel - 首页公开数据概览路由（挂载于 /api/platform/，非管理端语义）

公开接口与超管看板分离：
- PublicStatsView 的计算逻辑集中在 admin_panel（统计数据服务都在这里），
- 但路由独立挂载在 /api/platform/stats/ 下，不让公开接口出现在 /api/admin/ 前缀里。

config/urls.py 中以 include 方式引用本模块。
"""
from django.urls import path

from .views import PublicStatsView

urlpatterns = [
    path('stats/', PublicStatsView.as_view(), name='public-platform-stats'),
]
