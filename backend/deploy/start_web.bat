@echo off
chcp 65001 >nul
rem ===========================================================================
rem 生产 Web 服务入口（P1-2 Phase 1-2 / 审计报告 P-5）
rem
rem 背景：改造前生产靠 `start.bat` 执行 `manage.py runserver`。runserver 是
rem Django 的开发服务器——单进程、无并发保护、明确声明"不要用于生产"。
rem 它在这里承担生产 WSGI 角色，一旦并发上来就会成为瓶颈，且异常处理、
rem 静态文件、超时行为都与生产预期不符。
rem
rem 本脚本**不会**回退到 runserver。它会依次探测仓库 requirements.txt 里
rem 已声明的 WSGI 服务器；都不可用时**直接退出并报 BLOCKED**，把问题暴露
rem 出来，而不是让它继续被 runserver 掩盖。
rem
rem 状态：BLOCKED: production WSGI dependency/plan required
rem   requirements.txt 目前**没有**任何生产 WSGI 服务器（gunicorn / waitress /
rem   uwsgi / uvicorn 均未声明）。按本阶段"不新增依赖"的约束，这里不擅自
rem   引入，只提供探测 + 明确报错。补齐依赖后本脚本无需修改即可生效。
rem
rem 端口：CAMPUS_PORT 未设置时用 %DEFAULT_PORT%。
rem   注意：仓库内现状不一致——deploy\nginx.conf 的 upstream 指向 8000，
rem   而旧 start.bat 用 3000。本阶段统一为 8000（nginx 是唯一入口，
rem   以它为准），但**无法从开发机确认生产实际端口**，故在交付报告中标记
rem   PRODUCTION_PORT_NOT_VERIFIED，请部署时确认后设置 CAMPUS_PORT。
rem ===========================================================================

setlocal

set "DEFAULT_PORT=8000"
if "%CAMPUS_PORT%"=="" (
    set "PORT=%DEFAULT_PORT%"
) else (
    set "PORT=%CAMPUS_PORT%"
)

set "BACKEND_DIR=%~dp0.."
cd /d "%BACKEND_DIR%" || (
    echo [ERROR] 无法进入后端目录：%BACKEND_DIR%
    exit /b 1
)

set "PY=%BACKEND_DIR%\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo [web] 后端目录: %CD%
echo [web] 监听端口: %PORT%  (CAMPUS_PORT 可覆盖；生产实际端口未验证)

rem ---- 生产 WSGI 服务器探测（按优先级） --------------------------------
set "WSGI="
"%PY%" -c "import waitress" >nul 2>&1 && set "WSGI=waitress"
if not defined WSGI (
    "%PY%" -c "import gunicorn" >nul 2>&1 && set "WSGI=gunicorn"
)

if not defined WSGI (
    echo.
    echo [BLOCKED] production WSGI dependency/plan required
    echo   当前环境没有安装任何生产 WSGI 服务器（已探测 waitress / gunicorn）。
    echo   按 P1-2 Phase 1-2 的约束，本阶段不新增依赖，因此这里**拒绝启动**，
    echo   而不是回退到 `manage.py runserver`（那是开发服务器，不得用于生产）。
    echo.
    echo   处理方式（任选其一，然后重新运行本脚本）：
    echo     1^) 在 requirements.txt 中声明 waitress，并 pip install waitress
    echo        ^(Windows 推荐；gunicorn 不支持 Windows^)
    echo     2^) 改为 Linux 部署，声明并安装 gunicorn
    exit /b 2
)

echo [web] WSGI 服务器: %WSGI%

if "%WSGI%"=="waitress" (
    "%PY%" -m waitress --listen=0.0.0.0:%PORT% --threads=8 config.wsgi:application
) else (
    "%PY%" -m gunicorn config.wsgi:application --bind 0.0.0.0:%PORT% --workers 4 --timeout 300
)

endlocal
