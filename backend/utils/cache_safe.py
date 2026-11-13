"""缓存安全访问封装。

Redis 不可用时缓存读写不应把问答打挂：所有异常降级为"未命中/未写入"。
开发默认文件缓存，生产可通过 REDIS_CACHE_URL 切 Redis。
"""
import logging
from typing import Any, Optional

from django.core.cache import cache

logger = logging.getLogger(__name__)


def safe_get(key: str, default: Any = None) -> Any:
    try:
        value = cache.get(key)
    except Exception as exc:  # Redis 宕机/序列化损坏等
        logger.warning('缓存读取失败，按未命中处理: %s', type(exc).__name__)
        return default
    return default if value is None else value


def safe_set(key: str, value: Any, timeout: Optional[int] = None) -> bool:
    try:
        cache.set(key, value, timeout)
        return True
    except Exception as exc:
        logger.warning('缓存写入失败，已忽略: %s', type(exc).__name__)
        return False


def safe_delete(key: str) -> None:
    try:
        cache.delete(key)
    except Exception as exc:
        logger.warning('缓存删除失败，已忽略: %s', type(exc).__name__)
