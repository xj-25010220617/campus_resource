@echo off
chcp 65001 >nul
rem ===========================================================================
rem 【仅限本地开发】Django 开发服务器
rem
rem P1-2 Phase 1-2 / 审计报告 P-5：
rem   本脚本使用 `manage.py runserver`。那是 Django **开发**服务器——单进程、
rem   无并发保护、官方明确声明不得用于生产。改造前它被当作生产入口使用，
rem   本阶段把它明确降级为"本地开发专用"，生产入口改为：
rem
rem       deploy\start_web.bat      生产 WSGI（状态：BLOCKED，见该文件）
rem       deploy\start_worker.bat   Celery worker
rem       deploy\start_beat.bat     Celery beat
rem
rem 生产请勿直接调用本文件。
rem
rem 端口：本地开发沿用 3000（不改变既有开发习惯）。生产端口以
rem deploy\start_web.bat 的 CAMPUS_PORT 为准；两者差异是刻意保留的，
rem 因为生产实际端口尚未验证（PRODUCTION_PORT_NOT_VERIFIED）。
rem ===========================================================================

set PATH=C:\wwwroot\ffmpeg;%PATH%

rem 优先用脚本所在目录，避免写死某台机器的绝对路径；CAMPUS_BACKEND_DIR 可覆盖
if "%CAMPUS_BACKEND_DIR%"=="" (
    set "BACKEND_DIR=%~dp0"
) else (
    set "BACKEND_DIR=%CAMPUS_BACKEND_DIR%"
)
cd /d "%BACKEND_DIR%" || (
    echo [ERROR] 无法进入后端目录：%BACKEND_DIR%
    exit /b 1
)

set "PY=%BACKEND_DIR%venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

set "PORT=3000"
if not "%CAMPUS_DEV_PORT%"=="" set "PORT=%CAMPUS_DEV_PORT%"

echo [dev] manage.py runserver 0.0.0.0:%PORT%  ^(开发服务器，禁止用于生产^)
"%PY%" manage.py runserver 0.0.0.0:%PORT%
