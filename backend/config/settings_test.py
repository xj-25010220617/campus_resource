from .settings import *

DATABASES = {
    "default": {
        "ENGINE": "config.sqlite_test_backend",
        "NAME": ":memory:",
    }
}

# 测试默认不调用真实模型；需要覆盖 LLM 兜底的用例自行 override_settings。
QUERY_ROUTER_LLM_ENABLED = False
