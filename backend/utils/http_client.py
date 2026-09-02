"""
统一外部 HTTP 客户端
- 根据 service 名自动注入超时配置
- 统一日志格式
- 熔断检测（连续失败后快速失败）
- 兼容 requests 和 oss2 SDK
"""
import logging
import time
from collections import namedtuple
from typing import Optional

import requests

logger = logging.getLogger(__name__)


try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


class ServiceTimeout(namedtuple('ServiceTimeout', ['connect', 'read'])):
    pass


TIMEOUT_PROFILES = {
    'llm_chat': ServiceTimeout(10, 60),
    'llm_stream': ServiceTimeout(10, 120),
    # 视觉/多模态读图（qwen-vl），图片内容大、响应慢，read 放宽到 120s
    'llm_vision': ServiceTimeout(10, 120),
    'aliyun_embed': ServiceTimeout(10, 30),
    'dingtalk': ServiceTimeout(5, 10),
    'oss_sdk': ServiceTimeout(5, 30),
    'oss_download': ServiceTimeout(5, 120),
}


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = 'closed'

    def record_success(self):
        self.failure_count = 0
        self.state = 'closed'

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = 'open'
            logger.warning(f'熔断触发: failure_count={self.failure_count}')

    def is_available(self) -> bool:
        if self.state == 'closed':
            return True
        if time.time() - self.last_failure_time >= self.recovery_timeout:
            self.state = 'half-open'
            return True
        return False


_circuit_breakers = {}


def _get_circuit_breaker(service: str) -> CircuitBreaker:
    if service not in _circuit_breakers:
        _circuit_breakers[service] = CircuitBreaker()
    return _circuit_breakers[service]


class ExternalClient:
    @staticmethod
    def request(service: str, method: str, url: str, **kwargs) -> requests.Response:
        cb = _get_circuit_breaker(service)
        if not cb.is_available():
            logger.error(f'服务熔断: {service} 不可用，快速失败')
            raise requests.RequestException(f'服务熔断: {service} 当前不可用')

        # 钉钉 API 是国内服务，不走系统代理（否则代理挂了登录直接 500）
        if service == 'dingtalk':
            kwargs.setdefault('proxies', {'http': None, 'https': None})

        timeout_profile = TIMEOUT_PROFILES.get(service)
        if timeout_profile:
            kwargs.setdefault('timeout', (timeout_profile.connect, timeout_profile.read))

        start_time = time.time()
        try:
            resp = requests.request(method, url, **kwargs)
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info(f'[HTTP] {service} {method} {url} {resp.status_code} {elapsed_ms}ms')
            cb.record_success()
            return resp
        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.error(f'[HTTP] {service} {method} {url} FAILED {elapsed_ms}ms: {e}')
            cb.record_failure()
            raise

    @staticmethod
    def get(service: str, url: str, **kwargs) -> requests.Response:
        return ExternalClient.request(service, 'GET', url, **kwargs)

    @staticmethod
    def post(service: str, url: str, **kwargs) -> requests.Response:
        return ExternalClient.request(service, 'POST', url, **kwargs)

    @staticmethod
    def put(service: str, url: str, **kwargs) -> requests.Response:
        return ExternalClient.request(service, 'PUT', url, **kwargs)

    @staticmethod
    def delete(service: str, url: str, **kwargs) -> requests.Response:
        return ExternalClient.request(service, 'DELETE', url, **kwargs)

    @staticmethod
    def get_timeout(service: str) -> Optional[ServiceTimeout]:
        return TIMEOUT_PROFILES.get(service)

    @staticmethod
    async def async_request(service: str, method: str, url: str, **kwargs) -> "httpx.Response":
        if not HAS_HTTPX:
            raise ImportError('httpx 未安装，请安装: pip install httpx')

        cb = _get_circuit_breaker(service)
        if not cb.is_available():
            logger.error(f'服务熔断: {service} 不可用，快速失败')
            raise httpx.HTTPError(f'服务熔断: {service} 当前不可用')

        timeout_profile = TIMEOUT_PROFILES.get(service)
        if timeout_profile:
            kwargs.setdefault('timeout', timeout_profile.read)

        start_time = time.time()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.request(method, url, **kwargs)
                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.info(f'[HTTP] {service} {method} {url} {resp.status_code} {elapsed_ms}ms')
                cb.record_success()
                return resp
        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.error(f'[HTTP] {service} {method} {url} FAILED {elapsed_ms}ms: {e}')
            cb.record_failure()
            raise

    @staticmethod
    async def async_post(service: str, url: str, **kwargs) -> "httpx.Response":
        return await ExternalClient.async_request(service, 'POST', url, **kwargs)

    @staticmethod
    async def async_stream(service: str, method: str, url: str, **kwargs):
        if not HAS_HTTPX:
            raise ImportError('httpx 未安装，请安装: pip install httpx')

        cb = _get_circuit_breaker(service)
        if not cb.is_available():
            logger.error(f'服务熔断: {service} 不可用，快速失败')
            raise httpx.HTTPError(f'服务熔断: {service} 当前不可用')

        timeout_profile = TIMEOUT_PROFILES.get(service)
        if timeout_profile:
            kwargs.setdefault('timeout', timeout_profile.read)

        start_time = time.time()
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(method, url, **kwargs) as resp:
                    resp.raise_for_status()
                    elapsed_ms = int((time.time() - start_time) * 1000)
                    logger.info(f'[HTTP] {service} {method} {url} {resp.status_code} {elapsed_ms}ms')
                    cb.record_success()
                    async for line in resp.aiter_lines():
                        yield line
        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.error(f'[HTTP] {service} {method} {url} FAILED {elapsed_ms}ms: {e}')
            cb.record_failure()
            raise