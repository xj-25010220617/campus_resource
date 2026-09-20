"""
LLM Provider 抽象 — 聊天/视觉模型可插拔。

设计目标：问答智能体模型后期很可能切换为本地部署（vLLM / Ollama 等），
把「对话/视觉」这一层抽成 Provider，业务代码只依赖 `llm_client.chat*` 接口，
切换后端只需改 `LLM_PROVIDER` 环境变量，不改任何调用方。

当前实现：
- QwenProvider：OpenAI 兼容接口（现状实际走 DeepSeek，经 QWEN_* 变量回退）
- LocalProvider：本地模型占位，后期接入
"""
import json as _json
import logging
import time
from typing import AsyncGenerator, Generator, List, Optional

import requests
from django.conf import settings

from .http_client import ExternalClient

logger = logging.getLogger(__name__)

# 重试配置
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # 指数退避基数（秒）


class LLMTimeoutError(Exception):
    """LLM 请求超时或最终失败（已重试 MAX_RETRIES 次）。"""


def _retry_sleep(attempt: int):
    """指数退避等待"""
    delay = RETRY_BACKOFF_BASE ** attempt
    time.sleep(delay)


def _check_ascii(name: str, value: str) -> bool:
    """检查 API Key / Base URL 是否为纯 ASCII。

    urllib3 用 latin-1 编码 HTTP 请求头，值里一旦混入非 ASCII 字符（中文、
    emoji、不可见字符，通常是复制粘贴带入），请求会在发送前直接抛
    `UnicodeEncodeError: 'latin-1' codec can't encode characters ...`。
    这里提前校验，把"3 次重试后静默降级 Mock"变成一行可定位的错误日志。
    """
    if value and any(ord(ch) > 127 for ch in value):
        logger.error(
            f'{name} 含非 ASCII 字符: {value[:24]!r}。'
            '通常是复制粘贴带入的中文/emoji/隐藏字符，请重新从控制台复制。'
        )
        return False
    return True


class LLMProvider:
    """聊天/视觉模型 Provider 抽象基类。"""

    def chat(self, messages: List[dict], model: str = None, temperature: float = 0.7, max_tokens: int = 2048) -> Optional[str]:
        raise NotImplementedError

    def chat_stream(self, messages: List[dict], model: str = None, temperature: float = 0.3, max_tokens: int = 2048) -> Generator[str, None, None]:
        raise NotImplementedError

    async def chat_stream_async(self, messages: List[dict], model: str = None, temperature: float = 0.3, max_tokens: int = 2048) -> AsyncGenerator[str, None]:
        raise NotImplementedError

    def chat_vision(self, text: str, image_data_urls: List[str], model: str = None, temperature: float = 0.0, max_tokens: int = 800) -> Optional[str]:
        raise NotImplementedError


class QwenProvider(LLMProvider):
    """OpenAI 兼容接口的聊天实现（当前实际走 DeepSeek，经 QWEN_* 变量回退）。"""

    def __init__(self):
        self.api_key = getattr(settings, 'QWEN_API_KEY', '')
        self.base_url = getattr(settings, 'QWEN_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode')
        self.chat_model = getattr(settings, 'QWEN_CHAT_MODEL', 'qwen-flash')
        self.vl_model = getattr(settings, 'QWEN_VL_MODEL', 'qwen-vl-plus')

    @property
    def headers(self):
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

    def chat(self, messages, model=None, temperature=0.7, max_tokens=2048):
        if not self.api_key:
            return self._mock_chat(messages)

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = ExternalClient.post(
                    'llm_chat',
                    f'{self.base_url}/v1/chat/completions',
                    headers=self.headers,
                    json={
                        'model': model or self.chat_model,
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

    def chat_vision(self, text, image_data_urls, model=None, temperature=0.0, max_tokens=800):
        if not image_data_urls:
            return None
        if not self.api_key:
            return self._mock_chat([{'role': 'user', 'content': text}])

        content = [{'type': 'text', 'text': text}]
        for data_url in image_data_urls:
            content.append({'type': 'image_url', 'image_url': {'url': data_url}})

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = ExternalClient.post(
                    'llm_vision',
                    f'{self.base_url}/v1/chat/completions',
                    headers=self.headers,
                    json={
                        'model': model or self.vl_model,
                        'messages': [{'role': 'user', 'content': content}],
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
                logger.warning(f'Vision 超时 (attempt {attempt+1}/{MAX_RETRIES})')
            except requests.RequestException as e:
                last_error = e
                logger.warning(f'Vision 请求失败 (attempt {attempt+1}/{MAX_RETRIES}): {e}')

            if attempt < MAX_RETRIES - 1:
                _retry_sleep(attempt + 1)

        logger.error(f'Vision 最终失败: {last_error}')
        return None

    def chat_stream(self, messages, model=None, temperature=0.3, max_tokens=2048):
        if not self.api_key:
            yield from self._mock_stream(messages)
            return

        if not _check_ascii('QWEN_API_KEY', self.api_key):
            raise LLMTimeoutError('QWEN_API_KEY 含非 ASCII 字符，请检查配置后重试')

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = ExternalClient.post(
                    'llm_stream',
                    f'{self.base_url}/v1/chat/completions',
                    headers=self.headers,
                    json={
                        'model': model or self.chat_model,
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
        raise LLMTimeoutError(f'AI 回答超时（已重试 {MAX_RETRIES} 次）：{last_error}')

    async def chat_stream_async(self, messages, model=None, temperature=0.3, max_tokens=2048):
        if not self.api_key:
            for char in self._mock_chat(messages):
                yield char
            return

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
                        f'{self.base_url}/v1/chat/completions',
                        headers=self.headers,
                        json={
                            'model': model or self.chat_model,
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
    def _mock_chat(messages):
        """开发阶段 Mock 对话"""
        last_msg = messages[-1]['content'] if messages else ''
        return f'[Mock LLM Response] 您的问题是：「{last_msg[:50]}...」。当前为开发模式，请配置 QWEN_API_KEY 后获取真实回答。'

    @staticmethod
    def _mock_stream(messages):
        """开发阶段 Mock 流式对话"""
        text = QwenProvider._mock_chat(messages)
        for char in text:
            yield char


class LocalProvider(LLMProvider):
    """本地模型占位 — 后期接入 vLLM / Ollama 等，当前未实现。"""

    def _not_ready(self):
        raise NotImplementedError('本地 LLM 尚未接入，请在 .env 设置 LLM_PROVIDER=qwen 使用云端模型')

    def chat(self, messages, model=None, temperature=0.7, max_tokens=2048):
        self._not_ready()

    def chat_stream(self, messages, model=None, temperature=0.3, max_tokens=2048):
        self._not_ready()

    async def chat_stream_async(self, messages, model=None, temperature=0.3, max_tokens=2048):
        self._not_ready()

    def chat_vision(self, text, image_data_urls, model=None, temperature=0.0, max_tokens=800):
        self._not_ready()


def build_provider() -> LLMProvider:
    """按 LLM_PROVIDER 配置实例化 provider。"""
    provider_name = getattr(settings, 'LLM_PROVIDER', 'qwen')
    if provider_name == 'local':
        return LocalProvider()
    return QwenProvider()
