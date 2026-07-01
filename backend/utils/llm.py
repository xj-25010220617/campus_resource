"""
大模型 API 调用封装
- DeepSeek Chat（对话生成）
- DeepSeek Embedding（文本向量化）
"""
import logging
from typing import Generator, List, Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 配置
# ------------------------------------------------------------------
LLM_API_KEY = getattr(settings, 'DEEPSEEK_API_KEY', '')
LLM_BASE_URL = getattr(settings, 'DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
LLM_CHAT_MODEL = getattr(settings, 'DEEPSEEK_CHAT_MODEL', 'deepseek-chat')
LLM_EMBED_MODEL = getattr(settings, 'DEEPSEEK_EMBED_MODEL', 'deepseek-embed')


class LLMClient:
    """DeepSeek API 封装"""

    def __init__(self):
        self.api_key = LLM_API_KEY
        self.base_url = LLM_BASE_URL

    @property
    def headers(self):
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

    # ------------------------------------------------------------------
    # Embedding（文本向量化）
    # ------------------------------------------------------------------
    def embed(self, texts: List[str]) -> List[List[float]]:
        """
        文本向量化。
        参数: texts — 文本列表，每个元素一个 chunk
        返回: 对应的向量列表
        """
        if not self.api_key:
            return self._mock_embed(texts)

        resp = requests.post(
            f'{self.base_url}/v1/embeddings',
            headers=self.headers,
            json={
                'model': LLM_EMBED_MODEL,
                'input': texts,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return [item['embedding'] for item in data['data']]

    def embed_one(self, text: str) -> List[float]:
        """单条文本向量化"""
        results = self.embed([text])
        return results[0] if results else []

    @staticmethod
    def _mock_embed(texts: List[str]) -> List[List[float]]:
        """开发阶段无 API Key 时的 Mock"""
        import hashlib
        results = []
        for text in texts:
            # 用文本 hash 生成伪随机向量（仅用于开发调试）
            h = hashlib.sha256(text.encode()).digest()
            vec = [(b / 255.0) for b in h[:1024]]  # 展开到 1024 维
            # 补齐
            while len(vec) < 1024:
                vec.append(0.0)
            results.append(vec[:1024])
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
        """非流式对话，返回完整回复文本"""
        if not self.api_key:
            return self._mock_chat(messages)

        resp = requests.post(
            f'{self.base_url}/v1/chat/completions',
            headers=self.headers,
            json={
                'model': model or LLM_CHAT_MODEL,
                'messages': messages,
                'temperature': temperature,
                'max_tokens': max_tokens,
                'stream': False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data['choices'][0]['message']['content']

    # ------------------------------------------------------------------
    # Chat（流式）— 返回生成器
    # ------------------------------------------------------------------
    def chat_stream(
        self,
        messages: List[dict],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> Generator[str, None, None]:
        """流式对话，逐 token yield 文本"""
        if not self.api_key:
            yield from self._mock_stream(messages)
            return

        resp = requests.post(
            f'{self.base_url}/v1/chat/completions',
            headers=self.headers,
            json={
                'model': model or LLM_CHAT_MODEL,
                'messages': messages,
                'temperature': temperature,
                'max_tokens': max_tokens,
                'stream': True,
            },
            timeout=120,
            stream=True,
        )
        resp.raise_for_status()

        for line in resp.iter_lines(decode_unicode=True):
            if not line or line.startswith(':'):
                continue
            if line.startswith('data: '):
                data_str = line[6:]
                if data_str.strip() == '[DONE]':
                    break
                try:
                    import json
                    chunk = json.loads(data_str)
                    delta = chunk.get('choices', [{}])[0].get('delta', {})
                    content = delta.get('content', '')
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

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
