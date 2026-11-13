"""Django settings for campus resource management system."""
import os
import sys
from pathlib import Path

# 阶段 6.4：Celery 队列声明。kombu.Queue 在 broker 包里，settings 早于 celery app 加载，
# 所以这里直接 import；celery 未装时回退为 None，CELERY_QUEUES 整体注释掉走默认队列。
try:
    from kombu import Queue  # noqa: F401
except ImportError:
    Queue = None  # type: ignore[assignment]
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
REDIS_CACHE_URL = config('REDIS_CACHE_URL', default='')
if REDIS_CACHE_URL:
    # 生产：多进程共享缓存（embedding/rerank/判定缓存才真正生效）
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_CACHE_URL,
            'TIMEOUT': 300,
            'KEY_PREFIX': 'campus',
        }
    }
else:
    # 开发/单进程：文件缓存（写操作见 utils/cache_safe.py，Redis 故障不影响问答）
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
            'LOCATION': BASE_DIR / 'cache',
            'TIMEOUT': 120,
        }
    }

# 检索级语义缓存（阶段 5.2）：只缓存"证据"不缓存"答案"，
# 命中后仍走正常生成，引用天然一致；问答类禁用词见 utils/semantic_cache.py
SEMANTIC_CACHE_ENABLED = config('SEMANTIC_CACHE_ENABLED', default=False, cast=bool)
SEMANTIC_CACHE_THRESHOLD = config('SEMANTIC_CACHE_THRESHOLD', default=0.95, cast=float)
SEMANTIC_CACHE_TTL = config('SEMANTIC_CACHE_TTL', default=600, cast=int)
SEMANTIC_CACHE_MAX_ENTRIES = config('SEMANTIC_CACHE_MAX_ENTRIES', default=50, cast=int)

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

# Text-to-SQL 增强：AST 校验（sqlglot）+ 执行自愈 + 结果自检
TABULAR_SQL_VALIDATOR = config('TABULAR_SQL_VALIDATOR', default='auto')  # auto|sqlglot|regex
TABULAR_SQL_SELF_HEAL = config('TABULAR_SQL_SELF_HEAL', default=True, cast=bool)
TABULAR_SQL_MAX_HEAL = config('TABULAR_SQL_MAX_HEAL', default=1, cast=int)
TABULAR_SQL_SELF_CHECK = config('TABULAR_SQL_SELF_CHECK', default=True, cast=bool)

# Schema Linking：列过多时只注入相关列；多 sheet 时按问句选 sheet
TABULAR_SCHEMA_MAX_COLUMNS = config('TABULAR_SCHEMA_MAX_COLUMNS', default=20, cast=int)
TABULAR_SCHEMA_SAMPLE_VALUES = config('TABULAR_SCHEMA_SAMPLE_VALUES', default=5, cast=int)

# Few-shot 示例库：成功问答自动登记（待人工 verified），仅已验证示例注入 Prompt
TABULAR_FEWSHOT_ENABLED = config('TABULAR_FEWSHOT_ENABLED', default=False, cast=bool)
TABULAR_FEWSHOT_TOP_N = config('TABULAR_FEWSHOT_TOP_N', default=3, cast=int)

# 父子块分块（阶段 1.2）：子块进向量库负责"检得准"，父块只存 MySQL 供回填负责"答得全"
CHUNK_CHILD_SIZE = config('CHUNK_CHILD_SIZE', default=350, cast=int)
CHUNK_CHILD_OVERLAP = config('CHUNK_CHILD_OVERLAP', default=80, cast=int)
CHUNK_PARENT_SIZE = config('CHUNK_PARENT_SIZE', default=1200, cast=int)

# Embedding 后端切换（aliyun / local / mock）
EMBED_BACKEND = config('EMBED_BACKEND', default='local')
ALIYUN_EMBED_API_KEY = config('ALIYUN_EMBED_API_KEY', default='')
ALIYUN_EMBED_MODEL = config('ALIYUN_EMBED_MODEL', default='text-embedding-v4')
# 单批条数：百炼同步 Embedding 接口每次最多 10 条，调大可能被服务端拒绝
ALIYUN_EMBED_BATCH_SIZE = config('ALIYUN_EMBED_BATCH_SIZE', default=10, cast=int)
# 单条文本截断字符数：一条超长 chunk 会导致整批失败，截断比丢批划算
ALIYUN_EMBED_MAX_CHARS = config('ALIYUN_EMBED_MAX_CHARS', default=2000, cast=int)
# 可选维度参数：v4 支持 512/768/1024/1536/2048；0 表示不传，用模型默认（1024）
# 显式指定后必须同步把 EMBED_VECTOR_DIM 调成相同值，否则写 Milvus 会维度不匹配
ALIYUN_EMBED_DIMENSIONS = config('ALIYUN_EMBED_DIMENSIONS', default=0, cast=int)
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
# 集合名版本化：新模型/新分块先写入新集合（如 campus_knowledge_v2），
# 评测通过后再切本变量，出问题改回一条 env 即可回滚（需重启进程）。
MILVUS_COLLECTION = config('MILVUS_COLLECTION', default='campus_knowledge')
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

# --- 阶段 6.4：队列拆分 ---
# parse 队列：解析 + 分块 + Embedding + Milvus（重活，需 rate limit 防打百炼配额）
# crawl 队列：官网爬虫（独立流量尖峰，避免阻塞索引队列）
# faq   队列：FAQ 自动生成（低频、不阻塞主链路）
# sys   队列：系统心跳 + 清理 + 版本识别（后台运维类）
if Queue is not None:
    CELERY_QUEUES = (
        Queue('parse',  routing_key='parse.#'),
        Queue('crawl',  routing_key='crawl.#'),
        Queue('faq',    routing_key='faq.#'),
        Queue('sys',    routing_key='sys.#'),
        Queue('default', routing_key='default.#'),  # 保留兜底
    )

# 任务 → 队列映射；shared_task 装饰器上 queue= 与这里二选一，
# 两者都写是防御性配置——装饰器覆盖优先级更高，celery.py 的 beat_schedule 里
# 动态取 task.queue 字段注入，不会被路由表覆盖。
CELERY_TASK_ROUTES = {
    'apps.knowledge.tasks.index_document_task':           {'queue': 'parse'},
    'apps.knowledge.tasks.extract_freshness_task':       {'queue': 'parse'},
    'apps.knowledge.tasks.detect_version_task':          {'queue': 'sys'},
    'apps.knowledge.tasks.auto_link_relations_task':    {'queue': 'sys'},
    'apps.knowledge.tasks.crawl_neusoft_website_task':  {'queue': 'crawl'},
    'apps.knowledge.tasks.refresh_daily_context_task':  {'queue': 'sys'},
    'apps.knowledge.tasks.cleanup_expired_upload_sessions_task': {'queue': 'sys'},
    'apps.faq.tasks.generate_faq_weekly':               {'queue': 'faq'},
    'apps.faq.tasks.generate_faq_from_website':         {'queue': 'faq'},
}

# Embedding rate limit：百炼免费额度每天 200w token，索引一条文档 ~50 次调用，
# 高峰期（爬虫 + 上传）可能并发 10+ 条。80/m 限制能让 10 并发下单条任务串行化
# Embedding 批次调用，而不是 10 条任务同时打 API。
EMBED_RATE_LIMIT = config('EMBED_RATE_LIMIT', default='80/m')

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

# 精排后端：off（关闭）| local（本地 Cross-Encoder，需 CROSS_ENCODER_ENABLED=True）。
# api / onnx 后端尚未接入，配置成这两个值时会自动降级为 off 并打 warning。
RERANK_BACKEND = config('RERANK_BACKEND', default='off')
# 各后端独立阈值：精排分数已归一化到 [0,1]（见 reranker._normalize_rerank_scores），
# 不再复用加权融合的 RAG_SCORE_THRESHOLD，避免换后端后阈值整体失效。
RERANK_MIN_SCORE = config('RERANK_MIN_SCORE', default=0.30, cast=float)
RERANK_TOP_N = config('RERANK_TOP_N', default=20, cast=int)

# ------------------------------------------------------------------
# 检索与解析开关（阶段化改造的回滚开关，默认全部保持旧行为）
# ------------------------------------------------------------------
# 融合算法：weighted（现有手写加权）| rrf（倒数排名融合）
RETRIEVAL_FUSION = config('RETRIEVAL_FUSION', default='weighted')
# RRF 分数与加权重排完全不重量纲，必须独立标定阈值，禁止复用 RAG_SCORE_THRESHOLD。
RRF_K = config('RRF_K', default=60, cast=int)
RRF_SCORE_THRESHOLD = config('RRF_SCORE_THRESHOLD', default=0.006, cast=float)
RRF_RETRY_TRIGGER_SCORE = config('RRF_RETRY_TRIGGER_SCORE', default=0.015, cast=float)
RRF_RETRY_MIN_SCORE = config('RRF_RETRY_MIN_SCORE', default=0.004, cast=float)
# 引用展示阈值：RRF 下一次榜首命中约 1/(K+1)=0.016，0.02 约等于"多通道命中或高位命中"
RRF_REFERENCE_THRESHOLD = config('RRF_REFERENCE_THRESHOLD', default=0.02, cast=float)
# 管理员拒答 / 联网兜底的置信阈值：加权口径 0.5，RRF 下折算为约"两票高位命中"
RRF_ADMIN_MIN_SCORE = config('RRF_ADMIN_MIN_SCORE', default=0.03, cast=float)
RRF_WEB_SEARCH_MIN_SCORE = config('RRF_WEB_SEARCH_MIN_SCORE', default=0.03, cast=float)
# 解析后端：
#   legacy     —— 现有 PyMuPDF / python-docx / openpyxl 直读（默认，可回滚）
#   markitdown —— DOCX/PPTX 走 MarkItDown 转 Markdown（保留标题层级）；
#                 XLSX/CSV 仍走 legacy，因为表格结构化记录要喂 Text-to-SQL
#   service    —— PDF 走外部解析服务（docling-serve / MinerU sidecar），失败回落本地
PARSER_BACKEND = config('PARSER_BACKEND', default='legacy')
# 复杂版式 PDF 解析服务（PARSER_BACKEND=service 时生效），留空则直接用本地解析
DOC_PARSE_SERVICE_URL = config('DOC_PARSE_SERVICE_URL', default='')
DOC_PARSE_SERVICE_TIMEOUT = config('DOC_PARSE_SERVICE_TIMEOUT', default=120, cast=int)
# OCR 后端：auto（优先 RapidOCR，缺失回退 EasyOCR）| rapidocr | easyocr
OCR_BACKEND = config('OCR_BACKEND', default='auto')
# 低成本 Contextual Retrieval（阶段 1.2 可选增强）：Embedding 前拼接
# 「文档标题 > 标题路径」，无需调 LLM。开启后必须整库重灌才生效。
CONTEXTUAL_EMBED_ENABLED = config('CONTEXTUAL_EMBED_ENABLED', default=False, cast=bool)
# 向量通道质量门：最高分过低/分数无差异时把向量权重从 0.7 降到 0.2。
# 默认关闭：当前 bge 后端实测开启后 Recall@5 由 90.0% 降到 86.7%（阈值
# 0.25/0.02 是按更高相似度分布的模型标的，与本地余弦分布不匹配）。
# 换 Embedding 后端后，用 scripts/eval_retrieval.py 标定阈值再打开。
RAG_VECTOR_GATE_ENABLED = config('RAG_VECTOR_GATE_ENABLED', default=False, cast=bool)
RAG_VECTOR_GATE_LOW_TOP = config('RAG_VECTOR_GATE_LOW_TOP', default=0.25, cast=float)
RAG_VECTOR_GATE_MIN_SPREAD = config('RAG_VECTOR_GATE_MIN_SPREAD', default=0.02, cast=float)
# 正文分块通道走 MySQL ngram FULLTEXT（迁移 0017 建索引）；索引缺失或
# 后端不支持时自动回退 icontains，召回不退化、只影响速度。
RAG_FULLTEXT_ENABLED = config('RAG_FULLTEXT_ENABLED', default=True, cast=bool)

# 检索证据充分性判定（CRAG 最小闭环）：开启后每轮问答多一次轻量 LLM 判定，
# 判定"资料不足"时扩大检索一次，并在 Prompt 中禁止编造。默认关闭。
RAG_GRADER_ENABLED = config('RAG_GRADER_ENABLED', default=False, cast=bool)
RAG_GRADER_RETRY_ENABLED = config('RAG_GRADER_RETRY_ENABLED', default=True, cast=bool)
RAG_GRADER_MAX_HITS = config('RAG_GRADER_MAX_HITS', default=5, cast=int)
RAG_GRADER_SNIPPET_CHARS = config('RAG_GRADER_SNIPPET_CHARS', default=400, cast=int)
RAG_GRADER_TIMEOUT = config('RAG_GRADER_TIMEOUT', default=8, cast=float)
RAG_GRADER_CACHE_TTL = config('RAG_GRADER_CACHE_TTL', default=600, cast=int)

# 多问题查询分解（阶段 3.3）：命中"对比/分别/各学院/历年"等提示词时，
# 先拆成最多 N 个子问题各自召回再合并。默认关闭。
RAG_DECOMPOSE_ENABLED = config('RAG_DECOMPOSE_ENABLED', default=False, cast=bool)
RAG_DECOMPOSE_MAX_SUBQUERIES = config('RAG_DECOMPOSE_MAX_SUBQUERIES', default=3, cast=int)
RAG_DECOMPOSE_TIMEOUT = config('RAG_DECOMPOSE_TIMEOUT', default=8, cast=float)

# 测试进程强制关闭依赖真实模型的在线开关：保证用例确定性、可离线运行。
# 线上仍由 env 控制，不受此处影响。
if 'test' in sys.argv:
    RAG_GRADER_ENABLED = False
    RAG_DECOMPOSE_ENABLED = False

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
