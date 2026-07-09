@echo off
chcp 65001 >nul
REM =============================================
REM 成都东软学院官网爬虫 - 每周全量
REM =============================================

set "BACKEND_DIR=D:\实验室204\校园虚拟资源管理\backend"
set "PYTHON_EXE=C:\Users\Aurora\.pyenv\pyenv-win\versions\3.10.11\python.exe"
set "LOG_DIR=%BACKEND_DIR%\logs"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set "DT=%%I"
set "DATE_STAMP=%DT:~0,8%"
set "LOG_FILE=%LOG_DIR%\crawl_full_%DATE_STAMP%.log"

echo [%DATE% %TIME%] 开始全量爬取 >> "%LOG_FILE%"

cd /d "%BACKEND_DIR%"
"%PYTHON_EXE%" manage.py crawl_website --pages 300 --uploader 1 --college 1 >> "%LOG_FILE%" 2>&1

echo [%DATE% %TIME%] 全量爬取完成 >> "%LOG_FILE%"
echo ======================== >> "%LOG_FILE%"
