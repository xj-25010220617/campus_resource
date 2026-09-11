"""Django settings for campus resource management system."""
import os
from pathlib import Path
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

_DEV_SECRET_KEY = 'django-insecure-dev-key-change-in-production-2026'


# ---------------------------------------------------------------------------
# 环境变量读取助手（P1-2 Phase 1-2 / 审计报告 P-6）
#
# 生产安全配置此前依赖"部署的人记得去改 .env"：ALLOWED_HOSTS 缺省值是 `*`，
# 一旦生产 .env 漏配或写空，Django 会**静默接受任意 Host 头**，没有任何报错。
# 这里统一改成"缺省即安全 + 配错就拒绝启动"。
#
# 变量名同时接受 `DJANGO_*` 与仓库历史名（如 `ALLOWED_HOSTS` / `CORS_ORIGINS`），
# 前者优先，避免已有 .env 直接失效。
# ---------------------------------------------------------------------------

from django.core.exceptions import ImproperlyConfigured  # noqa: E402


def _cfg_bool(*names, default=False):
    """按顺序取第一个非空环境变量并解析为布尔值。"""
    for name in names:
        raw = config(name, default=None)
        if raw is None or str(raw).strip() == '':
            continue
        return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')
    return default


def _cfg_list(*names, default=''):
    """按顺序取第一个非空环境变量并按逗号切分为去空白的列表。"""
    for name in names:
        raw = config(name, default=None)
        if raw is None or str(raw).strip() == '':
            continue
        return [item.strip() for item in str(raw).split(',') if item.strip()]
    return [item.strip() for item in default.split(',') if item.strip()]


SECRET_KEY = config('SECRET_KEY', default=_DEV_SECRET_KEY)

# DEBUG 默认关闭（fail-closed）；本地开发在 .env 显式开启
DEBUG = _cfg_bool('DJANGO_DEBUG', 'DEBUG', default=False)

# 部署安全：生产环境（DEBUG=False）禁止使用已知的开发密钥
if not DEBUG and SECRET_KEY == _DEV_SECRET_KEY:
    raise ImproperlyConfigured(
        '生产环境必须显式设置 SECRET_KEY，禁止使用默认开发密钥'
    )

# ALLOWED_HOSTS：缺省不再是 `*`。本地开发（DEBUG=True）没有配置时兜底为 `*`
# 以保持既有开发体验；生产环境必须显式配置，配错直接拒绝启动。
ALLOWED_HOSTS = _cfg_list('DJANGO_ALLOWED_HOSTS', 'ALLOWED_HOSTS')
if not ALLOWED_HOSTS:
    if DEBUG:
        ALLOWED_HOSTS = ['*']
    else:
        raise ImproperlyConfigured(
            '生产环境（DEBUG=False）必须显式设置 ALLOWED_HOSTS，'
            '例如 DJANGO_ALLOWED_HOSTS=example.edu.cn,www.example.edu.cn'
        )

if not DEBUG and '*' in ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        '生产环境（DEBUG=False）禁止 ALLOWED_HOSTS 包含 "*"：'
        '这会让任意 Host 头通过校验（审计报告 P-6）。'
        '请改为显式列出真实域名，例如 '
        'DJANGO_ALLOWED_HOSTS=example.edu.cn,www.example.edu.cn'
    )

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
#
# `CORS_ALLOW_ALL_ORIGINS` 只允许在开发环境为 True；生产环境即使显式设了
# CORS_ALLOW_ALL_ORIGINS=true 也直接拒绝启动（审计报告 P-6）。跨域放行是
# 浏览器侧的安全边界，不能靠"部署的人记得关"来保证。
CORS_ALLOW_ALL_ORIGINS = _cfg_bool('CORS_ALLOW_ALL_ORIGINS', default=DEBUG)
if not DEBUG and CORS_ALLOW_ALL_ORIGINS:
    raise ImproperlyConfigured(
        '生产环境（DEBUG=False）禁止 CORS_ALLOW_ALL_ORIGINS=True：'
        '这会让任意站点携带用户凭证调用本服务（审计报告 P-6）。'
        '请改用 CORS_ALLOWED_ORIGINS 列出真实前端源。'
    )

# 生产环境不再兜底到 localhost——那只会让人误以为已配置。
CORS_ALLOWED_ORIGINS = _cfg_list(
    'CORS_ALLOWED_ORIGINS', 'CORS_ORIGINS',
    default='http://localhost:5173' if DEBUG else '',
)

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

# Embedding 后端切换（aliyun / local / mock）
EMBED_BACKEND = config('EMBED_BACKEND', default='aliyun')
ALIYUN_EMBED_API_KEY = config('ALIYUN_EMBED_API_KEY', default='')
ALIYUN_EMBED_MODEL = config('ALIYUN_EMBED_MODEL', default='text-embedding-v3')
ALIYUN_EMBED_BASE_URL = config(
    'ALIYUN_EMBED_BASE_URL',
    default='https://dashscope.aliyuncs.com/compatible-mode/v1',
)
LOCAL_EMBED_MODEL = config('LOCAL_EMBED_MODEL', default='paraphrase-multilingual-MiniLM-L12-v2')

# ------------------------------------------------------------------
# 上传意图（P1-2D）
#
# 服务端签发的隔离区上传凭证有效期。取得短是安全考虑：凭证过期后不能再
# complete/consume，未完成的上传对象留在隔离区等待人工处理（§十四 不自动删除）。
# 分片上传的会话有效期另见 chunked_views.SESSION_EXPIRE_HOURS（24 小时）。
# ------------------------------------------------------------------
UPLOAD_INTENT_TTL_SECONDS = config(
    'UPLOAD_INTENT_TTL_SECONDS', default=3600, cast=int)

# ------------------------------------------------------------------
# 内容安全审核服务（P1-2C）
#
# 全部走配置，不在代码里散落字面量。安全相关默认值一律取**最严格**的一侧：
# 检测器默认全部启用且必需——宁可让文档停在 unavailable，也不放行未审核内容。
# ------------------------------------------------------------------
# 策略版本：写入 ContentModeration.policy_version / ModerationAttempt.policy_version，
# 规则或检测器变化后必须递增，才能区分"同一内容在不同策略下的结论"。
CONTENT_MODERATION_POLICY_VERSION = config(
    'CONTENT_MODERATION_POLICY_VERSION', default='content-moderation-v1',
)

# 必需检测器：其中任何一个没能给出结论，本次审核只能是 unavailable / error。
# 要放宽必须显式修改这里（有记录的评审决定），而不是让代码静默跳过。
CONTENT_MODERATION_REQUIRED_DETECTORS = ('clamav', 'rule', 'llm')
# 可选检测器：缺席不阻断放行；但一旦给出非 clean 结论，依然计入判决。
CONTENT_MODERATION_OPTIONAL_DETECTORS = ('vision',)
# 图片没有任何可抽文本，视觉检测器缺席等于图片完全没被检查 → 默认必须。
CONTENT_MODERATION_VISION_REQUIRED_FOR_IMAGES = True

# 重试与租约
CONTENT_MODERATION_MAX_ATTEMPTS = config('CONTENT_MODERATION_MAX_ATTEMPTS', default=3, cast=int)
CONTENT_MODERATION_RETRY_BACKOFF_SECONDS = config(
    'CONTENT_MODERATION_RETRY_BACKOFF_SECONDS', default=300, cast=int)
CONTENT_MODERATION_LEASE_SECONDS = config(
    'CONTENT_MODERATION_LEASE_SECONDS', default=600, cast=int)

# 上传解耦后的补扫宽限期（P1-2 Phase 1-2 / 审计报告 P-4）。
# `upload_service.schedule_moderation()` 只入队、绝不降级同步执行：队列不可用
# 时文档会停在"已建 KnowledgeDoc、对象仍在 quarantine/、无审核记录"。
# `retry_failed_moderations_task` 会重新派发这类文档，但只处理创建时间早于
# 本宽限期的——避免和"刚提交、正在入队"的请求抢同一份文档。
MODERATION_DISPATCH_RECOVERY_GRACE_SECONDS = config(
    'MODERATION_DISPATCH_RECOVERY_GRACE_SECONDS', default=900, cast=int)

# ------------------------------------------------------------------
# Promotion（P1-2D STEP 4-B）
#
# 隔离区 → 正式区（quarantine/ → knowledge/）的抢占租约。promotion 期间记录
# 被 CAS 置为 promoting；持有者崩溃后该状态不会自己恢复，超过本时长即视为
# 租约过期，允许其他 worker 重新认领（OSS copy 幂等，重做安全）。
# ------------------------------------------------------------------
PROMOTION_CLAIM_TIMEOUT_SECONDS = config(
    'PROMOTION_CLAIM_TIMEOUT_SECONDS', default=900, cast=int)

# ClamAV（clamd INSTREAM）。默认启用且必需：未部署时应表现为
# moderation_state=unavailable，而不是"跳过扫描后放行"。
CLAMAV_ENABLED = config('CLAMAV_ENABLED', default=True, cast=bool)
CLAMAV_HOST = config('CLAMAV_HOST', default='127.0.0.1')
CLAMAV_PORT = config('CLAMAV_PORT', default=3310, cast=int)
CLAMAV_TIMEOUT = config('CLAMAV_TIMEOUT', default=30, cast=int)

# 模型审核
LLM_MODERATION_ENABLED = config('LLM_MODERATION_ENABLED', default=True, cast=bool)
LLM_MODERATION_TIMEOUT = config('LLM_MODERATION_TIMEOUT', default=60, cast=int)
# 送模型的字符上限：绝不允许把文件全文直接发给模型
MODERATION_MAX_LLM_INPUT_CHARS = config(
    'MODERATION_MAX_LLM_INPUT_CHARS', default=12000, cast=int)

# 视觉审核
VISION_MODERATION_ENABLED = config('VISION_MODERATION_ENABLED', default=True, cast=bool)
VISION_MODERATION_TIMEOUT = config('VISION_MODERATION_TIMEOUT', default=120, cast=int)

# 读取 / 抽取上限
MODERATION_MAX_DOWNLOAD_BYTES = config(
    'MODERATION_MAX_DOWNLOAD_BYTES', default=50 * 1024 * 1024, cast=int)
MODERATION_MAX_MEDIA_BYTES = config(
    'MODERATION_MAX_MEDIA_BYTES', default=20 * 1024 * 1024, cast=int)
MODERATION_MAX_EXTRACT_CHARS = config(
    'MODERATION_MAX_EXTRACT_CHARS', default=2000000, cast=int)

# 旧格式 DOC 解析（antiword）。不使用 soffice：它会以完整文档方式在本机
# 打开上传文件，风险面显著更大，且当前部署未验证可用。
MODERATION_DOC_ANTIWORD_ENABLED = config(
    'MODERATION_DOC_ANTIWORD_ENABLED', default=True, cast=bool)
MODERATION_SUBPROCESS_TIMEOUT_SECONDS = config(
    'MODERATION_SUBPROCESS_TIMEOUT_SECONDS', default=20, cast=int)
MODERATION_SUBPROCESS_MAX_OUTPUT_BYTES = config(
    'MODERATION_SUBPROCESS_MAX_OUTPUT_BYTES', default=8 * 1024 * 1024, cast=int)

# ------------------------------------------------------------------
# Milvus / ChromaDB
# ------------------------------------------------------------------
MILVUS_HOST = config('MILVUS_HOST', default='localhost')
MILVUS_PORT = config('MILVUS_PORT', default='19530')
# 注意：chroma-hnswlib 的 C++ 层无法打开含中文的 Windows 路径
# （"Cannot open header file" / "Error loading hnsw index"），
# 项目目录含中文，因此 Chroma 库默认放在用户主目录下（纯 ASCII 路径）。
from pathlib import Path as _Path
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
# Mock SSO 仅用于本地开发调试。生产环境必须保持默认 False；
# 且即便误置为 True，仅当 DEBUG=True 时才会生效（见 accounts.views._sso_mock_enabled）。
SSO_MOCK_ENABLED = config('SSO_MOCK_ENABLED', default=False, cast=bool)
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
NEUSOFT_WEBSITE_URL = config('NEUSOFT_WEBSITE_URL', default='https://www.neusoft.edu.cn')
CRAWLER_MAX_PAGES = config('CRAWLER_MAX_PAGES', default=200, cast=int)
CRAWLER_CRAWL_DELAY = config('CRAWLER_CRAWL_DELAY', default=0.5, cast=float)

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
CROSS_ENCODER_ENABLED = config('CROSS_ENCODER_ENABLED', default=True, cast=bool)
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
