"""Django settings for campus resource management system."""
import os
from pathlib import Path
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

_DEV_SECRET_KEY = 'django-insecure-dev-key-change-in-production-2026'

SECRET_KEY = config('SECRET_KEY', default=_DEV_SECRET_KEY)

# DEBUG 默认关闭（fail-closed）；本地开发在 .env 显式开启
DEBUG = config('DEBUG', default=False, cast=bool)

# 环境标识：production / test / development。
# 默认跟随 DEBUG（True→development，False→production），测试服在 .env 显式设置 ENVIRONMENT=test。
# SSO Mock 等仅限非生产环境的功能以此为准（见 apps/accounts/views.py 的 _sso_mock_enabled）。
ENVIRONMENT = config('ENVIRONMENT', default='development' if DEBUG else 'production')

# 部署安全：生产环境（DEBUG=False）禁止使用已知的开发密钥
if not DEBUG and SECRET_KEY == _DEV_SECRET_KEY:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured(
        '生产环境必须显式设置 SECRET_KEY，禁止使用默认开发密钥'
    )

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='*').split(',')

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'django_filters',
    'drf_spectacular',
    # Local apps
    'apps.accounts',
    'apps.classify',
    'apps.resources',
    'apps.admin_panel',
    'apps.chat',
    'apps.knowledge',
    'apps.feedback',
    'apps.faq',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'config.middleware.ErrorReportingMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database — MySQL 8.0
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': config('DB_NAME', default='campus_resource'),
        'USER': config('DB_USER', default='root'),
        'PASSWORD': config('DB_PASSWORD', default=''),
        'HOST': config('DB_HOST', default='127.0.0.1'),
        'PORT': config('DB_PORT', default='3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
        'TEST': {
            # 测试库必须与开发库不同名，避免跑测试时误删开发库 campus_resource
            'NAME': config('DB_TEST_NAME', default='test_campus_resource'),
            'DEPENDENCIES': [],
        },
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Custom User model
AUTH_USER_MODEL = 'accounts.User'

# 文件上传大小限制
DATA_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024  # 100MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024  # 100MB

# Internationalization
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# CORS
CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOWED_ORIGINS = config('CORS_ORIGINS', default='http://localhost:5173').split(',')

# DRF configuration
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# JWT settings
from datetime import timedelta
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=2),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# 缓存 — 文件缓存，跨进程共享，无需 Redis
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': BASE_DIR / 'cache',
        'TIMEOUT': 120,
    }
}

# DRF Spectacular (API docs)
SPECTACULAR_SETTINGS = {
    'TITLE': '校园虚拟资源管理系统 API',
    'DESCRIPTION': '成都东软学院校园资源管理平台接口文档',
    'VERSION': '1.0.0',
    'SCHEMA_PATH_PREFIX': '/api/',
    'COMPONENT_SPLIT_REQUEST': True,
    'SERVE_INCLUDE_SCHEMA': False,
    'OPERATION_ID_PREFIX': '',
}

# OSS configuration (placeholder — 实际使用时在 .env 中配置)
OSS_ACCESS_KEY_ID = config('OSS_ACCESS_KEY_ID', default='')
OSS_ACCESS_KEY_SECRET = config('OSS_ACCESS_KEY_SECRET', default='')
OSS_ENDPOINT = config('OSS_ENDPOINT', default='oss-cn-chengdu.aliyuncs.com')
OSS_BUCKET_NAME = config('OSS_BUCKET_NAME', default='')

# ------------------------------------------------------------------
# Qwen（阿里云百炼）/ LLM — 仅 Chat；Embedding 同平台，见 ALIYUN_EMBED_*
# ------------------------------------------------------------------
# 统一命名为 QWEN_*；DEEPSEEK_* 仅作迁移期回退（升级后可在 .env 删除）。
QWEN_API_KEY = config('QWEN_API_KEY', default=config('DEEPSEEK_API_KEY', default=''))
QWEN_BASE_URL = config('QWEN_BASE_URL', default=config('DEEPSEEK_BASE_URL', default='https://dashscope.aliyuncs.com/compatible-mode'))
QWEN_CHAT_MODEL = config('QWEN_CHAT_MODEL', default=config('DEEPSEEK_CHAT_MODEL', default='qwen-flash'))
# 视觉（多模态）模型 — 图片内容识别/智能分类用，独立于文本对话模型
QWEN_VL_MODEL = config('QWEN_VL_MODEL', default='qwen-vl-plus')

# LLM Provider 切换（qwen=云端 OpenAI 兼容 / local=本地模型占位）
# 后期本地部署问答模型时改 .env 的 LLM_PROVIDER=local 即可，不改代码。
LLM_PROVIDER = config('LLM_PROVIDER', default='qwen')

# 意图路由 LLM 兜底：仅当正则/规则无法判断时调用一次短超时分类。
# 关闭后路由完全走原有确定性规则（测试环境默认关闭，见 settings_test.py）。
QUERY_ROUTER_LLM_ENABLED = config('QUERY_ROUTER_LLM_ENABLED', default=True, cast=bool)
QUERY_ROUTER_LLM_TIMEOUT = config('QUERY_ROUTER_LLM_TIMEOUT', default=3.0, cast=float)
QUERY_ROUTER_LLM_CACHE_TTL = config('QUERY_ROUTER_LLM_CACHE_TTL', default=600, cast=int)

# 表格问答引擎（utils/tabular_query.py，sqlite3 内存表执行）
TABULAR_SQL_LLM_TIMEOUT = config('TABULAR_SQL_LLM_TIMEOUT', default=8.0, cast=float)
TABULAR_MAX_ROWS = config('TABULAR_MAX_ROWS', default=100000, cast=int)
TABULAR_RESULT_MAX_ROWS = config('TABULAR_RESULT_MAX_ROWS', default=100, cast=int)

# Embedding 后端切换（aliyun / local / mock）
EMBED_BACKEND = config('EMBED_BACKEND', default='local')
ALIYUN_EMBED_API_KEY = config('ALIYUN_EMBED_API_KEY', default='')
ALIYUN_EMBED_MODEL = config('ALIYUN_EMBED_MODEL', default='text-embedding-v3')
ALIYUN_EMBED_BASE_URL = config(
    'ALIYUN_EMBED_BASE_URL',
    default='https://dashscope.aliyuncs.com/compatible-mode/v1',
)
LOCAL_EMBED_MODEL = config('LOCAL_EMBED_MODEL', default='BAAI/bge-base-zh-v1.5')

# 向量维度：必须与 Embedding 后端实际输出一致，否则 Milvus 写入报维度不匹配
# （MilvusException: expected dim X, got Y）导致文档永远到不了 indexed 状态。
# 默认由 EMBED_BACKEND 派生：aliyun text-embedding-v3=1024；local bge-base-zh-v1.5 / mock=768。
# 更换其他维度的模型时在 .env 用 EMBED_VECTOR_DIM 显式覆盖，并运行 reindex_all 重建向量库。
_EMBED_DIM_BY_BACKEND = {'aliyun': 1024, 'local': 768, 'mock': 768}
EMBED_VECTOR_DIM = int(config(
    'EMBED_VECTOR_DIM',
    default=_EMBED_DIM_BY_BACKEND.get(EMBED_BACKEND, 768),
))
# HuggingFace 下载镜像（国内直连 huggingface.co 不通时走 hf-mirror.com）
# 需在 import huggingface_hub / sentence_transformers 之前写入环境变量，故在此导出
_hf_endpoint = config('HF_ENDPOINT', default='')
if _hf_endpoint:
    os.environ['HF_ENDPOINT'] = _hf_endpoint

# ------------------------------------------------------------------
# Milvus / ChromaDB
# ------------------------------------------------------------------
# Milvus Lite 嵌入式向量库（本地文件，无需 Docker / 独立服务）。
# 注意：向量/索引库的 C++ 层在 Windows 下无法打开含中文路径（同 chroma-hnswlib
# 的 "Cannot open header file" 问题），项目目录含中文，故库默认放用户主目录（纯 ASCII）。
from pathlib import Path as _Path
MILVUS_URI = config('MILVUS_URI', default=str(_Path.home() / '.campus_milvus.db'))
CHROMA_DB_PATH = _Path(config('CHROMA_DB_PATH', default=str(_Path.home() / '.campus_chroma_db')))

# ------------------------------------------------------------------
# Celery
# ------------------------------------------------------------------
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default='redis://127.0.0.1:6379/0')
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default='redis://127.0.0.1:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Asia/Shanghai'
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_TASK_TRACK_STARTED = True

# ------------------------------------------------------------------
# SSO 单点登录
# ------------------------------------------------------------------
# Mock SSO 仅用于开发/测试环境（ENVIRONMENT=development/test）。生产环境必须保持默认 False；
# 且即便误置为 True，ENVIRONMENT=production 时也不会生效（见 accounts.views._sso_mock_enabled）。
# SSO_MOCK_ALLOW_PRIVILEGED 控制 Mock 是否暴露高权限账号（mock_super_admin/mock_admin）：
# 公网测试服保持默认 False（登录列表隐藏、callback 拒绝），需要测试超管时临时打开。
SSO_MOCK_ENABLED = config('SSO_MOCK_ENABLED', default=False, cast=bool)
SSO_MOCK_ALLOW_PRIVILEGED = config('SSO_MOCK_ALLOW_PRIVILEGED', default=False, cast=bool)
SSO_LOGIN_URL = config('SSO_LOGIN_URL', default='')

# ------------------------------------------------------------------
# 钉钉扫码登录
# ------------------------------------------------------------------
DINGTALK_APP_KEY = config('DINGTALK_APP_KEY', default='')
DINGTALK_APP_SECRET = config('DINGTALK_APP_SECRET', default='')

# ------------------------------------------------------------------
# ASR 语音识别（faster-whisper）
# ------------------------------------------------------------------
ASR_MODEL_NAME = 'small'
ASR_TIMEOUT = 30
ASR_DEBUG = True   # 开启后控制台打印 faster-whisper 识别信息，排查完可关掉

# ------------------------------------------------------------------
# 扫描件 OCR（文字层抽取为空时的兜底）
# ------------------------------------------------------------------
# 单文档 OCR 页数上限（超过截断，避免扫描件页数过多导致索引超时/费用失控）。
# 注意：超限不再静默成功——解析结果会带 truncated 标记并写入 doc.index_error。
OCR_MAX_PAGES = config('OCR_MAX_PAGES', default=20, cast=int)
# PDF 逐页判定阈值：单页文字层少于该字数即认为该页没有有效文字，转图片走 OCR。
# 逐页判定（而非按全文判定）是混合型 PDF 扫描页不被跳过的前提。
OCR_PAGE_MIN_TEXT_CHARS = config('OCR_PAGE_MIN_TEXT_CHARS', default=20, cast=int)

# FAQ 自动生成配置
# ------------------------------------------------------------------
# 最小占比阈值（百分比）— 超过此比例的问题才提取为 FAQ
FAQ_MIN_RATIO = 0.5
# 每次最多保留的高频问题簇数
FAQ_MAX_CLUSTERS = 20
# 向量聚类相似度阈值
FAQ_CLUSTER_THRESHOLD = 0.85
# 与已有 FAQ 去重阈值
FAQ_DEDUP_THRESHOLD = 0.90
# 日志采集天数
FAQ_ANALYSIS_DAYS = 7
# 官网/知识库 FAQ 草稿生成配置
FAQ_WEBSITE_MAX_PAGES = config('FAQ_WEBSITE_MAX_PAGES', default=20, cast=int)
FAQ_WEBSITE_MAX_DOCS = config('FAQ_WEBSITE_MAX_DOCS', default=20, cast=int)

# ------------------------------------------------------------------
# 官网爬虫配置
# ------------------------------------------------------------------
NEUSOFT_WEBSITE_URL = config('NEUSOFT_WEBSITE_URL', default='https://www.nsu.edu.cn')
CRAWLER_MAX_PAGES = config('CRAWLER_MAX_PAGES', default=200, cast=int)
CRAWLER_CRAWL_DELAY = config('CRAWLER_CRAWL_DELAY', default=0.5, cast=float)
# 附件下载（爬虫扩展）：仅下载可解析的文档类附件，图片/音视频/压缩包不下载
CRAWLER_DOWNLOAD_FILES = config('CRAWLER_DOWNLOAD_FILES', default=True, cast=bool)
CRAWLER_FILE_TYPES = config('CRAWLER_FILE_TYPES', default='pdf,doc,docx,xls,xlsx,ppt,pptx,csv,txt,md')
CRAWLER_MAX_FILE_SIZE_MB = config('CRAWLER_MAX_FILE_SIZE_MB', default=50, cast=int)
CRAWLER_MAX_FILES = config('CRAWLER_MAX_FILES', default=100, cast=int)
# 路径含这些关键词且无扩展名的 URL 视为候选附件（如 /system/_content/download.jsp），
# 下载后按 Content-Type 二次确认；置空禁用
CRAWLER_FILE_URL_HINTS = config('CRAWLER_FILE_URL_HINTS', default='download')
CRAWLER_FILE_TIMEOUT = config('CRAWLER_FILE_TIMEOUT', default=60, cast=int)

# ------------------------------------------------------------------
# 启动健康检查
# ------------------------------------------------------------------
SKIP_STARTUP_HEALTH_CHECK = config('SKIP_STARTUP_HEALTH_CHECK', default=False, cast=bool)

# ------------------------------------------------------------------
# 同义词词典
# ------------------------------------------------------------------
SYNONYM_DICT_CUSTOM_PATH = config('SYNONYM_DICT_CUSTOM_PATH', default='')

# ------------------------------------------------------------------
# Cross-Encoder 重排序
# ------------------------------------------------------------------
# 默认关闭：当前服务器为纯 CPU，bge-reranker-v2-m3 对 20 个候选精排单次需 40~60s，
# 叠加低置信二次检索后问答首字要 80~100s，超过前端 30s 超时导致"AI回答超时"。
# 关闭后走 rag._merge_and_rerank 的加权融合（原有兜底逻辑）；部署到 GPU 机器可在 .env 置 True 开启。
CROSS_ENCODER_ENABLED = config('CROSS_ENCODER_ENABLED', default=False, cast=bool)
CROSS_ENCODER_MODEL = config('CROSS_ENCODER_MODEL', default='BAAI/bge-reranker-v2-m3')

# ------------------------------------------------------------------
# 多轮对话摘要
# ------------------------------------------------------------------
MAX_HISTORY_ROUNDS_BEFORE_SUMMARY = config('MAX_HISTORY_ROUNDS_BEFORE_SUMMARY', default=3, cast=int)

# ------------------------------------------------------------------
# JS 渲染页面抓取（Playwright）
# ------------------------------------------------------------------
PLAYWRIGHT_ENABLED = config('PLAYWRIGHT_ENABLED', default=False, cast=bool)
PLAYWRIGHT_TIMEOUT = config('PLAYWRIGHT_TIMEOUT', default=15000, cast=int)
PLAYWRIGHT_HEADLESS = config('PLAYWRIGHT_HEADLESS', default=True, cast=bool)
