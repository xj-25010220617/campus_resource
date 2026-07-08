@echo off
set PATH=C:\wwwroot\ffmpeg;%PATH%
cd /d C:\wwwroot\campus_resource\backend
.\venv\Scripts\python.exe manage.py runserver 0.0.0.0:3000
