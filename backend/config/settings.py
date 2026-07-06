"""Django settings for campus resource management system."""
import os
from pathlib import Path
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY', default='django-insecure-dev-key-change-in-production-2026')

DEBUG = config('DEBUG', default=True, cast=bool)

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
            'NAME': 'campus_resource',
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
OSS_ENDPOINT = config('OSS_ENDPOINT', default='oss-cn-hangzhou.aliyuncs.com')
OSS_BUCKET_NAME = config('OSS_BUCKET_NAME', default='')

# ------------------------------------------------------------------
# DeepSeek / LLM
# ------------------------------------------------------------------
DEEPSEEK_API_KEY = config('DEEPSEEK_API_KEY', default='')
DEEPSEEK_BASE_URL = config('DEEPSEEK_BASE_URL', default='https://api.deepseek.com')
DEEPSEEK_CHAT_MODEL = config('DEEPSEEK_CHAT_MODEL', default='deepseek-chat')
DEEPSEEK_EMBED_MODEL = config('DEEPSEEK_EMBED_MODEL', default='deepseek-embed')

# ------------------------------------------------------------------
# Milvus / ChromaDB
# ------------------------------------------------------------------
MILVUS_HOST = config('MILVUS_HOST', default='localhost')
MILVUS_PORT = config('MILVUS_PORT', default='19530')
CHROMA_DB_PATH = BASE_DIR / 'chroma_db'

# ------------------------------------------------------------------
# Celery
# ------------------------------------------------------------------
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default='redis://127.0.0.1:6379/0')
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default='redis://127.0.0.1:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Asia/Shanghai'

# ------------------------------------------------------------------
# 钉钉扫码登录
# ------------------------------------------------------------------
DINGTALK_APP_KEY = config('DINGTALK_APP_KEY', default='')
DINGTALK_APP_SECRET = config('DINGTALK_APP_SECRET', default='')

# ------------------------------------------------------------------
# ASR 语音识别（FunASR）
# ------------------------------------------------------------------
ASR_MODEL_NAME = 'iic/SenseVoiceSmall'
ASR_TIMEOUT = 30
ASR_DEBUG = True   # 开启后控制台打印 FunASR 原始返回值，排查完可关掉
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
