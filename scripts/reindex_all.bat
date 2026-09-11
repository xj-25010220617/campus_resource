@echo off
chcp 65001 >nul
rem ===========================================================================
rem 知识库重索引（P1-2 Phase 1-2 / 审计报告 P-1）
rem
rem 本脚本现在只是 `manage.py reindex_docs` 的薄封装。所有安全判定都收敛到
rem apps/knowledge/reindex_tool.py，避免"批处理里另写一份逻辑"再次漂移。
rem
rem 改造前的版本做的是：删光所有 DocChunk、把所有状态重置为 uploaded、
rem rmdir /s /q chroma_db，再逐个索引。三件破坏性操作**先破坏后重建**且完全
rem 不经过审核门，中途失败就会把知识库留在半毁状态。现已全部移除。
rem
rem 用法：
rem   reindex_all.bat            预检 + 只重建通过审核门的文档（有不可索引文档则整体退出）
rem   reindex_all.bat --dry-run  只输出统计，不产生任何写入
rem   reindex_all.bat --force    连已 indexed 的也重建（仍逐条过审核门）
rem
rem 端口/路径：BACKEND_DIR 默认取本脚本所在目录的上一级 backend\，
rem 可用环境变量 CAMPUS_BACKEND_DIR 覆盖（勿再写死某台机器的绝对路径）。
rem ===========================================================================

setlocal

if "%CAMPUS_BACKEND_DIR%"=="" (
    set "BACKEND_DIR=%~dp0..\backend"
) else (
    set "BACKEND_DIR=%CAMPUS_BACKEND_DIR%"
)

cd /d "%BACKEND_DIR%" || (
    echo [ERROR] 无法进入后端目录：%BACKEND_DIR%
    exit /b 1
)

rem 优先使用虚拟环境里的解释器，保持与部署一致
set "PY=%BACKEND_DIR%\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo [reindex] 后端目录: %CD%
echo [reindex] 解释器  : %PY%
echo.

"%PY%" manage.py reindex_docs %*

set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [reindex] 未执行写入（退出码 %RC%）。请先看上面的统计与不可索引原因分布，
    echo [reindex] 再用 manage.py confirm_moderation 处理需要人工确认的文档。
)

endlocal & exit /b %RC%
