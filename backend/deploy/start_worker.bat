@echo off
chcp 65001 >nul
rem ===========================================================================
rem Celery Worker（P1-2 Phase 1-2 / 审计报告 P-5）
rem
rem 背景：改造前仓库里**没有任何 worker / beat 启动脚本**。上传链路的
rem moderation → promotion → index 全靠 Celery 异步执行，但没有 worker
rem 就意味着任务只入队、永远不被消费——上传的文档会永久停在"已上传未审核"。
rem 更糟的是本地开发环境用 `shared_task_compat` 把 `task.delay()` 降级成
rem 就地同步执行，掩盖了这个问题，直到上生产才暴露。
rem
rem `-A config` 加载 config/celery.py，它会 autodiscover_tasks()，
rem 因此以下任务都会被注册并可见（可用 `celery -A config inspect registered` 核对）：
rem     apps.knowledge.tasks.moderate_and_promote_task     审核 → 提升 → 索引
rem     apps.knowledge.tasks.retry_failed_moderations_task 审核失败重试 + 未入队补扫
rem     apps.knowledge.tasks.index_document_task           文档索引
rem     apps.knowledge.tasks.extract_freshness_task        日期提取
rem     apps.knowledge.tasks.crawl_neusoft_website_task    官网爬取
rem     apps.knowledge.tasks.cleanup_expired_upload_sessions_task
rem     apps.faq.tasks.generate_faq_weekly
rem
rem 注意：worker 与 beat **必须分开两个进程**（本脚本只管 worker）。
rem 同进程跑 `-B` 在多 worker 场景下会产生重复调度，故不采用。
rem ===========================================================================

setlocal

set "BACKEND_DIR=%~dp0.."
cd /d "%BACKEND_DIR%" || (
    echo [ERROR] 无法进入后端目录：%BACKEND_DIR%
    exit /b 1
)

set "PY=%BACKEND_DIR%\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -c "import celery" >nul 2>&1 || (
    echo [ERROR] 未安装 celery（requirements.txt 已声明 celery^>=5.3.0，请先 pip install）
    exit /b 1
)

rem Windows 上 Celery 的默认 prefork 池不可用（不支持 fork），必须显式指定
rem solo / threads，否则 worker 起不来或行为异常。Linux 部署可用 CELERY_POOL=prefork。
if "%CELERY_POOL%"=="" set "CELERY_POOL=solo"
if "%CELERY_CONCURRENCY%"=="" set "CELERY_CONCURRENCY=4"

echo [worker] 启动 Celery worker（app=config，pool=%CELERY_POOL%，并发=%CELERY_CONCURRENCY%）

"%PY%" -m celery -A config worker ^
    --loglevel=info ^
    --pool=%CELERY_POOL% ^
    --concurrency=%CELERY_CONCURRENCY% ^
    --without-gossip --without-mingle

endlocal
