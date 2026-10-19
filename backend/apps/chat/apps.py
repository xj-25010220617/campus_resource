import logging
import threading

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


class ChatConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.chat'
    verbose_name = 'AI 问答'

    def ready(self):
        """启动时检查 Embedding API 是否可用（后台线程，不阻塞启动）。"""
        if getattr(settings, 'SKIP_STARTUP_HEALTH_CHECK', False):
            return

        def _check_embed_api():
            try:
                from utils.llm import llm_client
                test_text = '健康检查'
                result = llm_client.embed_one(test_text)
                if result and len(result) > 0:
                    embed_backend = getattr(settings, 'EMBED_BACKEND', 'unknown')
                    logger.info(
                        'Embedding API 健康检查通过: dim=%d backend=%s',
                        len(result), embed_backend,
                    )
                else:
                    logger.warning(
                        'Embedding API 健康检查失败: 返回空向量。'
                        '请检查 ALIYUN_EMBED_API_KEY 配置。'
                        'RAG 检索将降级为关键词搜索。'
                    )
            except Exception as e:
                logger.warning(
                        'Embedding API 健康检查异常: %s。RAG 检索可能降级为关键词搜索。', e
                    )

                # 顺带预热 Cross-Encoder 重排模型（懒加载冷启动实测 ~170s，
                # 不预热会拖慢后端重启后的第一个 RAG 查询）
                try:
                    from utils.reranker import _get_cross_encoder
                    _get_cross_encoder()
                except Exception as e:
                    logger.warning('Cross-Encoder 预热异常: %s', e)

        threading.Thread(target=_check_embed_api, daemon=True).start()
