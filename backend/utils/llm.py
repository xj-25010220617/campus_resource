"""
大模型 API 调用封装
- DeepSeek Chat（对话生成，同步 + 异步流式）
- Embedding（文本向量化，支持多后端：阿里云百炼 / 本地 / Mock）
- 失败重试（指数退避）
"""
import logging
import time
from typing import AsyncGenerator, Dict, Generator, List, Optional

import requests
from django.conf import settings

from .http_client import ExternalClient

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 配置
# ------------------------------------------------------------------
LLM_API_KEY = getattr(settings, 'DEEPSEEK_API_KEY', '')
LLM_BASE_URL = getattr(settings, 'DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
LLM_CHAT_MODEL = getattr(settings, 'DEEPSEEK_CHAT_MODEL', 'deepseek-chat')

# ------------------------------------------------------------------
# Embedding 后端配置
# ------------------------------------------------------------------
# EMBED_BACKEND 可选值：
#   aliyun: 阿里云百炼（生产推荐，OpenAI 兼容模式）
#   local: 本地 sentence-transformers
#   mock:  SHA256 伪向量（无语义，仅开发调试）
EMBED_BACKEND = getattr(settings, 'EMBED_BACKEND', 'aliyun')

# 阿里云百炼 Embedding
ALIYUN_EMBED_API_KEY = getattr(settings, 'ALIYUN_EMBED_API_KEY', '')
ALIYUN_EMBED_MODEL = getattr(settings, 'ALIYUN_EMBED_MODEL', 'text-embedding-v3')
ALIYUN_EMBED_BASE_URL = getattr(
    settings, 'ALIYUN_EMBED_BASE_URL',
    'https://dashscope.aliyuncs.com/compatible-mode/v1',
)

# 本地模型（仅 EMBED_BACKEND=local 时生效）
LOCAL_EMBED_MODEL = getattr(settings, 'LOCAL_EMBED_MODEL', 'BAAI/bge-small-zh-v1.5')

# Embedding 后端选择：'deepseek' / 'aliyun'（阿里云百炼）/ 'local'（本地 sentence-transformers）
EMBED_BACKEND = getattr(settings, 'EMBED_BACKEND', 'deepseek')
LOCAL_EMBED_MODEL = getattr(settings, 'LOCAL_EMBED_MODEL', 'paraphrase-multilingual-MiniLM-L12-v2')
ALIYUN_EMBED_API_KEY = getattr(settings, 'ALIYUN_EMBED_API_KEY', '')  # 复用 OSS 的 AccessKey
ALIYUN_EMBED_MODEL = getattr(settings, 'ALIYUN_EMBED_MODEL', 'text-embedding-v2')

# 备选 Embedding 模型名（当前模型 404 时自动尝试）
FALLBACK_EMBED_MODELS = ['deepseek-embed', 'deepseek-embedding', 'deepseek-embedding-v1']

# 重试配置
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # 指数退避基数（秒）


def _retry_sleep(attempt: int):
    """指数退避等待"""
    delay = RETRY_BACKOFF_BASE ** attempt
    time.sleep(delay)


class LLMClient:
    """DeepSeek API + 阿里云百炼 Embedding 封装"""

    VECTOR_DIM = 1024  # text-embedding-v3 维度（1024）

    def __init__(self):
        self.chat_api_key = LLM_API_KEY
        self.chat_base_url = LLM_BASE_URL
        self.embed_api_key = ALIYUN_EMBED_API_KEY
        self.embed_base_url = ALIYUN_EMBED_BASE_URL

    @property
    def chat_headers(self):
        return {
            'Authorization': f'Bearer {self.chat_api_key}',
            'Content-Type': 'application/json',
        }

    @property
    def embed_headers(self):
        return {
            'Authorization': f'Bearer {self.embed_api_key}',
            'Content-Type': 'application/json',
        }

    # ------------------------------------------------------------------
    # Embedding（文本向量化）
    # ------------------------------------------------------------------
    def embed(self, texts: List[str]) -> List[List[float]]:
        """
        文本向量化（多后端自动降级）。

        优先顺序：
        1. DeepSeek API（配置的模型名，404 时尝试备选模型名）
        2. 本地 sentence-transformers（如果已安装且配置使用）
        3. Mock Embedding（hash 向量）

        Args:
            texts: 文本列表，每个元素一个 chunk

        Returns:
            对应的向量列表
        """
        if not texts:
            return []

        # 后端 1: DeepSeek API
        if self.chat_api_key and EMBED_BACKEND in ('deepseek',):
            result = self._embed_via_api(texts)
            if result:
                return result

        # 后端 2: 阿里云百炼（已配置 API Key 时自动启用）
        if EMBED_BACKEND == 'aliyun' and ALIYUN_EMBED_API_KEY:
            result = self._embed_via_aliyun(texts)
            if result:
                return result

        # 后端 3: 本地 sentence-transformers
        if EMBED_BACKEND == 'local':
            result = self._embed_via_local(texts)
            if result:
                return result

        # 后端 4: 始终可用的 Mock 降级
        logger.warning('所有 Embedding 后端不可用，降级为 Mock 向量')
        return self._mock_embed(texts)

    def _embed_via_api(self, texts: List[str]) -> Optional[List[List[float]]]:
        """通过 DeepSeek API 获取向量，404 时自动尝试备选模型名。"""
        model_names = [LLM_EMBED_MODEL]
        if LLM_EMBED_MODEL not in FALLBACK_EMBED_MODELS:
            model_names += FALLBACK_EMBED_MODELS

        NON_RETRYABLE_STATUSES = {400, 401, 403, 404, 405, 406, 415}

        for model in model_names:
            for attempt in range(MAX_RETRIES):
                try:
                    resp = ExternalClient.post(
                        'deepseek_embed',
                        f'{self.base_url}/v1/embeddings',
                        headers=self.headers,
                        json={
                            'model': model,
                            'input': texts,
                        },
                        timeout=10,
                    )

                    if resp.status_code in NON_RETRYABLE_STATUSES:
                        if resp.status_code == 404:
                            logger.warning(
                                f'Embedding 模型 "{model}" 不可用 (404)，尝试下一个...'
                            )
                            break  # 跳过此模型的全部重试
                        logger.error(
                            f'Embedding 永久错误 status={resp.status_code} '
                            f'body={resp.text[:200]}'
                        )
                        return None

                    resp.raise_for_status()
                    data = resp.json()
                    results = [item['embedding'] for item in data['data']]
                    logger.info(f'Embedding API OK: model={model} {len(results)}条')
                    return results

                except requests.Timeout as e:
                    logger.warning(f'Embedding 超时 model={model} ({attempt+1}/{MAX_RETRIES})')
                except requests.RequestException as e:
                    logger.warning(f'Embedding 失败 model={model} ({attempt+1}/{MAX_RETRIES}): {e}')

                if attempt < MAX_RETRIES - 1:
                    _retry_sleep(attempt + 1)

        return None

    def _embed_via_local(self, texts: List[str]) -> Optional[List[List[float]]]:
        """通过本地 sentence-transformers 模型获取向量。"""
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(LOCAL_EMBED_MODEL)
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

    def embed_one(self, text: str) -> List[float]:
        """单条文本向量化"""
        results = self.embed([text])
        return results[0] if results else []

    def _mock_embed(self, texts: List[str]) -> List[List[float]]:
        """开发阶段 Mock：生成 1024 维伪随机向量（与 text-embedding-v3 维度一致）"""
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
    # Chat（对话生成）— 非流式
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: List[dict],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> Optional[str]:
        """
        非流式对话（带重试），返回完整回复文本。

        Args:
            messages: 消息列表 [{'role': 'system'|'user'|'assistant', 'content': str}]
            model: 模型名，默认 deepseek-chat
            temperature: 生成温度
            max_tokens: 最大 token 数

        Returns:
            完整回复文本，失败返回 None
        """
        if not self.chat_api_key:
            return self._mock_chat(messages)

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = ExternalClient.post(
                    'deepseek_chat',
                    f'{self.chat_base_url}/v1/chat/completions',
                    headers=self.chat_headers,
                    json={
                        'model': model or LLM_CHAT_MODEL,
                        'messages': messages,
                        'temperature': temperature,
                        'max_tokens': max_tokens,
                        'stream': False,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data['choices'][0]['message']['content']
            except requests.Timeout as e:
                last_error = e
                logger.warning(f'Chat 超时 (attempt {attempt+1}/{MAX_RETRIES})')
            except requests.RequestException as e:
                last_error = e
                logger.warning(f'Chat 请求失败 (attempt {attempt+1}/{MAX_RETRIES}): {e}')

            if attempt < MAX_RETRIES - 1:
                _retry_sleep(attempt + 1)

        logger.error(f'Chat 最终失败: {last_error}')
        return None

    # ------------------------------------------------------------------
    # Chat（流式）— 同步生成器
    # ------------------------------------------------------------------
    def chat_stream(
        self,
        messages: List[dict],
        model: str = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> Generator[str, None, None]:
        """
        流式对话（同步），逐 token yield 文本。
        适合 Django 同步 StreamingHttpResponse。

        Args:
            messages: 消息列表
            model: 模型名
            temperature: 生成温度（RAG 场景建议 0.3）
            max_tokens: 最大 token 数
        """
        if not self.chat_api_key:
            yield from self._mock_stream(messages)
            return

        import json as _json

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = ExternalClient.post(
                    'deepseek_stream',
                    f'{self.chat_base_url}/v1/chat/completions',
                    headers=self.chat_headers,
                    json={
                        'model': model or LLM_CHAT_MODEL,
                        'messages': messages,
                        'temperature': temperature,
                        'max_tokens': max_tokens,
                        'stream': True,
                    },
                    stream=True,
                )
                resp.raise_for_status()

                for line in resp.iter_lines(decode_unicode=True):
                    if not line or line.startswith(':'):
                        continue
                    if line.startswith('data: '):
                        data_str = line[6:]
                        if data_str.strip() == '[DONE]':
                            return
                        try:
                            chunk = _json.loads(data_str)
                            delta = chunk.get('choices', [{}])[0].get('delta', {})
                            content = delta.get('content', '')
                            if content:
                                yield content
                        except (_json.JSONDecodeError, KeyError, IndexError):
                            continue
                return  # 正常结束
            except requests.Timeout as e:
                last_error = e
                logger.warning(f'Stream 超时 (attempt {attempt+1}/{MAX_RETRIES})')
            except requests.RequestException as e:
                last_error = e
                logger.warning(f'Stream 请求失败 (attempt {attempt+1}/{MAX_RETRIES}): {e}')

            if attempt < MAX_RETRIES - 1:
                _retry_sleep(attempt + 1)

        logger.error(f'Stream 最终失败: {last_error}')
        yield from self._mock_stream(messages)

    # ------------------------------------------------------------------
    # Chat（流式）— 异步生成器（httpx）
    # ------------------------------------------------------------------
    async def chat_stream_async(
        self,
        messages: List[dict],
        model: str = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> AsyncGenerator[str, None]:
        """
        流式对话（异步），逐 token yield 文本。
        适合 Django AsyncStreamingHttpResponse（生产环境推荐）。

        需要: pip install httpx

        Args:
            messages: 消息列表
            model: 模型名
            temperature: 生成温度
            max_tokens: 最大 token 数
        """
        if not self.chat_api_key:
            for char in self._mock_chat(messages):
                yield char
            return

        import json as _json

        try:
            import httpx
        except ImportError:
            logger.error('httpx 未安装，回退到同步 mock')
            for char in self._mock_chat(messages):
                yield char
            return

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream(
                        'POST',
                        f'{self.chat_base_url}/v1/chat/completions',
                        headers=self.chat_headers,
                        json={
                            'model': model or LLM_CHAT_MODEL,
                            'messages': messages,
                            'temperature': temperature,
                            'max_tokens': max_tokens,
                            'stream': True,
                        },
                    ) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line or line.startswith(':'):
                                continue
                            if line.startswith('data: '):
                                data_str = line[6:]
                                if data_str.strip() == '[DONE]':
                                    return
                                try:
                                    chunk = _json.loads(data_str)
                                    delta = chunk.get('choices', [{}])[0].get('delta', {})
                                    content = delta.get('content', '')
                                    if content:
                                        yield content
                                except (_json.JSONDecodeError, KeyError, IndexError):
                                    continue
                return
            except Exception as e:
                last_error = e
                logger.warning(f'Async stream 失败 (attempt {attempt+1}/{MAX_RETRIES}): {e}')

            if attempt < MAX_RETRIES - 1:
                _retry_sleep(attempt + 1)

        logger.error(f'Async stream 最终失败: {last_error}')
        for char in self._mock_chat(messages):
            yield char

    @staticmethod
    def _mock_chat(messages: List[dict]) -> str:
        """开发阶段 Mock 对话"""
        last_msg = messages[-1]['content'] if messages else ''
        return f'[Mock LLM Response] 您的问题是：「{last_msg[:50]}...」。当前为开发模式，请配置 DEEPSEEK_API_KEY 后获取真实回答。'

    @staticmethod
    def _mock_stream(messages: List[dict]) -> Generator[str, None, None]:
        """开发阶段 Mock 流式对话"""
        text = LLMClient._mock_chat(messages)
        for char in text:
            yield char


# 全局单例
llm_client = LLMClient()
