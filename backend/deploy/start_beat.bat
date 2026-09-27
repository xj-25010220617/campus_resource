@echo off
chcp 65001 >nul
rem ===========================================================================
rem Celery Beat 调度器（P1-2 Phase 1-2 / 审计报告 P-5、P-26）
rem
rem 背景：改造前没有 beat 启动脚本，而 `retry-failed-moderations` 恰恰是
rem 兜住"审核链断了"的那个人：
rem   - 检测器不可用导致 unavailable / error 的文档，靠它按退避重派；
rem   - 因 broker 不可用而从未入队的上传（P-4 的 fail-closed 路径），
rem     同样靠它补扫重派（见 apps/knowledge/tasks.py 的
rem     `_recover_undispatched_uploads`）。
rem 没有 beat，这两条恢复路径都不存在。
rem
rem `-A config` 加载 config/celery.py 的 beat_schedule，**不会覆盖也不会
rem 删除**任何既有条目。当前调度表（全部保留）：
rem     generate-faq-weekly              每周一 03:00
rem     crawl-neusoft-website-daily      每天 04:00
rem     crawl-neusoft-website-weekly-full 每周日 05:00
rem     refresh-daily-context            每天 00:05
rem     cleanup-expired-upload-sessions  每天 03:30
rem     retry-failed-moderations         每 10 分钟
rem
rem 调度文件持久化在 %BACKEND_DIR%\celerybeat-schedule，重启不丢进度。
rem **只能有一个 beat 实例**，多开会重复调度。
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

echo [beat] 启动 Celery beat（app=config，调度文件=%BACKEND_DIR%\celerybeat-schedule）

"%PY%" -m celery -A config beat ^
    --loglevel=info ^
    --schedule="%BACKEND_DIR%\celerybeat-schedule"

endlocal
