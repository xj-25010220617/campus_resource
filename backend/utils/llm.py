"""
大模型 API 调用封装
- Chat（对话/视觉/流式）：经 LLMProvider 抽象分发（见 utils/llm_provider.py）
- Embedding（文本向量化，支持多后端：阿里云百炼 / 本地 / Mock）
- 失败重试（指数退避）
"""
import logging
from typing import List, Optional

from django.conf import settings

from .http_client import ExternalClient
from .llm_provider import (
    LLMTimeoutError,
    MAX_RETRIES,
    _check_ascii,
    _retry_sleep,
    build_provider,
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Embedding 后端配置
# ------------------------------------------------------------------
# EMBED_BACKEND 可选值：
#   aliyun: 阿里云百炼（OpenAI 兼容模式）
#   local:  本地 sentence-transformers（默认，bge-base-zh-v1.5）
#   mock:   SHA256 伪向量（无语义，仅开发调试）
EMBED_BACKEND = getattr(settings, 'EMBED_BACKEND', 'local')

# 阿里云百炼 Embedding
ALIYUN_EMBED_API_KEY = getattr(settings, 'ALIYUN_EMBED_API_KEY', '')
ALIYUN_EMBED_MODEL = getattr(settings, 'ALIYUN_EMBED_MODEL', 'text-embedding-v3')
ALIYUN_EMBED_BASE_URL = getattr(
    settings, 'ALIYUN_EMBED_BASE_URL',
    'https://dashscope.aliyuncs.com/compatible-mode/v1',
)

# 本地模型（仅 EMBED_BACKEND=local 时生效）
LOCAL_EMBED_MODEL = getattr(settings, 'LOCAL_EMBED_MODEL', 'BAAI/bge-base-zh-v1.5')

# 本地 Embedding 模型单例：避免每次调用重新加载数百 MB 权重（实测每次 ~10s）
_local_embed_model = None


def _get_local_embed_model():
    global _local_embed_model
    if _local_embed_model is None:
        from sentence_transformers import SentenceTransformer
        _local_embed_model = SentenceTransformer(LOCAL_EMBED_MODEL)
    return _local_embed_model

# bge-v1.5 系列要求 query 侧加检索前缀，passage 侧不加
BGE_QUERY_PREFIX = '为这个句子生成表示以用于检索相关文章：'


def image_bytes_to_data_url(image_bytes: bytes, max_size: int = 1280, quality: int = 85) -> Optional[str]:
    """将图片二进制压缩为 base64 data URL（供视觉模型读取）。

    用 Pillow 解码 → 长边缩到 ≤ max_size → 转 RGB（透明铺白底）→ 存 JPEG → base64。
    返回形如 'data:image/jpeg;base64,...' 的字符串；解码失败或像素超限返回 None（调用方回退文本分类）。
    """
    import base64
    import io

    try:
        from PIL import Image
    except ImportError:
        logger.warning('Pillow 未安装，无法将图片传给视觉模型，回退文本分类')
        return None

    try:
        img = Image.open(io.BytesIO(image_bytes))
        width, height = img.size
        # 像素数硬顶 8000 万：低于 Pillow 默认告警线，覆盖所有手机照片，
        # 超大 PNG/扫描图直接放弃，避免整图解码瞬时占用数百 MB 内存。
        if width * height > 80_000_000:
            logger.warning(f'图片像素过大 {width}x{height}，放弃视觉识别，回退文本分类')
            return None
        # JPEG 走 draft 惰性重采样：超大 JPEG 不解全图就缩到目标尺寸
        try:
            img.draft('RGB', (max_size, max_size))
        except Exception:
            pass
        img.thumbnail((max_size, max_size), Image.LANCZOS)
        img.load()
        if img.mode in ('RGBA', 'LA', 'P'):
            # 带透明通道/调色板图先铺白底再转 RGB，避免直接 convert 出现黑底
            rgba = img.convert('RGBA')
            background = Image.new('RGB', rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        else:
            img = img.convert('RGB')
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=quality)
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        return f'data:image/jpeg;base64,{b64}'
    except Exception as e:
        logger.warning(f'图片处理失败，回退文本分类: {e}')
        return None


class LLMClient:
    """Embedding + Chat 门面：Embedding 自持多后端，Chat 委托给 LLMProvider。"""

    VECTOR_DIM = getattr(settings, 'EMBED_VECTOR_DIM', 768)  # 唯一事实源见 settings.EMBED_VECTOR_DIM

    def __init__(self):
        self.embed_api_key = ALIYUN_EMBED_API_KEY
        self.embed_base_url = ALIYUN_EMBED_BASE_URL
        self._provider = build_provider()

    @property
    def embed_headers(self):
        return {
            'Authorization': f'Bearer {self.embed_api_key}',
            'Content-Type': 'application/json',
        }

    # ------------------------------------------------------------------
    # Embedding（文本向量化）
    # ------------------------------------------------------------------
    def embed(self, texts: List[str], is_query: bool = False) -> List[List[float]]:
        """
        文本向量化（多后端自动降级）。

        优先顺序：
        1. 阿里云百炼（已配置 API Key 时自动启用）
        2. 本地 sentence-transformers（默认）
        3. Mock Embedding（hash 向量）

        Args:
            texts: 文本列表，每个元素一个 chunk
            is_query: 是否为查询侧（bge 需要加检索前缀）

        Returns:
            对应的向量列表
        """
        if not texts:
            return []

        # 后端 1: 阿里云百炼（已配置 API Key 时自动启用）
        if EMBED_BACKEND == 'aliyun' and ALIYUN_EMBED_API_KEY:
            if not _check_ascii('ALIYUN_EMBED_API_KEY', ALIYUN_EMBED_API_KEY):
                logger.error('EMBED_BACKEND=aliyun 但 API Key 含非 ASCII 字符，跳过阿里云（修复 .env 后重启）')
            elif not _check_ascii('ALIYUN_EMBED_BASE_URL', self.embed_base_url):
                logger.error('EMBED_BACKEND=aliyun 但 Base URL 含非 ASCII 字符，跳过阿里云（修复 .env 后重启）')
            else:
                result = self._embed_via_aliyun(texts)
                if result:
                    return result

        # 后端 2: 本地 sentence-transformers
        if EMBED_BACKEND == 'local':
            result = self._embed_via_local(texts, is_query=is_query)
            if result:
                return result

        # 后端 3: 始终可用的 Mock 降级
        logger.warning('所有 Embedding 后端不可用，降级为 Mock 向量')
        return self._mock_embed(texts)

    def _embed_via_local(self, texts: List[str], is_query: bool = False) -> Optional[List[List[float]]]:
        """通过本地 sentence-transformers 模型获取向量。"""
        try:
            model = _get_local_embed_model()
            if is_query:
                texts = [BGE_QUERY_PREFIX + t for t in texts]
            embeddings = model.encode(texts, show_progress_bar=False)
            logger.info(f'本地 Embedding OK: {len(embeddings)}条 dim={embeddings.shape[1]}')
            return embeddings.tolist()
        except ImportError:
            logger.warning('sentence-transformers 未安装，本地 Embedding 不可用')
            return None
        except Exception as e:
            logger.error(f'本地 Embedding 失败: {e}')
            return None

    def _embed_via_aliyun(self, texts: List[str]) -> Optional[List[List[float]]]:
        """通过阿里云百炼（OpenAI 兼容模式）获取向量。
        自动分批次（阿里云限制每批 ≤ 10 条）。

        API 格式 (OpenAI-compatible):
            POST {base_url}/embeddings
            {"model": "text-embedding-v3", "input": ["text1", "text2"]}
            → {"data": [{"embedding": [...], "index": 0}, ...]}
        """
        url = f'{self.embed_base_url.rstrip("/")}/embeddings'
        BATCH_SIZE = 10
        all_embeddings = []

        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start:start + BATCH_SIZE]
            for attempt in range(MAX_RETRIES):
                try:
                    resp = ExternalClient.post(
                        'aliyun_embed',
                        url,
                        headers=self.embed_headers,
                        json={
                            'model': ALIYUN_EMBED_MODEL,
                            'input': batch,
                        },
                        timeout=30,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        data['data'].sort(key=lambda x: x['index'])
                        all_embeddings.extend([item['embedding'] for item in data['data']])
                        break  # batch done, next
                    else:
                        logger.warning(
                            f'阿里云 Embedding 失败 (attempt {attempt+1}): '
                            f'status={resp.status_code} body={resp.text[:200]}'
                        )
                except Exception as e:
                    logger.warning(f'阿里云 Embedding 异常 (attempt {attempt+1}): {e}')

                if attempt < MAX_RETRIES - 1:
                    _retry_sleep(attempt + 1)
                else:
                    return None  # this batch failed entirely

        if all_embeddings:
            logger.info(f'阿里云 Embedding OK: {len(all_embeddings)}条 dim={len(all_embeddings[0])}')
            return all_embeddings
        return None

    def embed_one(self, text: str, is_query: bool = False) -> List[float]:
        """单条文本向量化"""
        results = self.embed([text], is_query=is_query)
        return results[0] if results else []

    def _mock_embed(self, texts: List[str]) -> List[List[float]]:
        """开发阶段 Mock：生成 768 维伪随机向量（维度与 bge-base-zh-v1.5 一致）"""
        import hashlib
        results = []
        for text in texts:
            h = hashlib.sha256(text.encode()).digest()
            vec = []
            for i in range(LLMClient.VECTOR_DIM):
                b = h[i % len(h)]
                vec.append(((b + i) % 256) / 255.0)
            results.append(vec)
        return results

    # ------------------------------------------------------------------
    # Chat（对话/视觉/流式）— 委托给 LLMProvider
    # ------------------------------------------------------------------
    def chat(self, messages, model=None, temperature=0.7, max_tokens=2048,
             timeout=None, max_retries=None):
        return self._provider.chat(
            messages, model=model, temperature=temperature, max_tokens=max_tokens,
            timeout=timeout, max_retries=max_retries,
        )

    def chat_vision(self, text, image_data_urls, model=None, temperature=0.0, max_tokens=800):
        return self._provider.chat_vision(text, image_data_urls, model=model, temperature=temperature, max_tokens=max_tokens)

    def chat_stream(self, messages, model=None, temperature=0.3, max_tokens=2048):
        yield from self._provider.chat_stream(messages, model=model, temperature=temperature, max_tokens=max_tokens)

    async def chat_stream_async(self, messages, model=None, temperature=0.3, max_tokens=2048):
        async for token in self._provider.chat_stream_async(messages, model=model, temperature=temperature, max_tokens=max_tokens):
            yield token


# 全局单例
llm_client = LLMClient()
