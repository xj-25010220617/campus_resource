@echo off
chcp 65001 >nul
REM =============================================
REM 一键重置索引 + 重新索引所有文档
REM 用法: 双击运行
REM =============================================

set "BACKEND_DIR=D:\实验室204\校园虚拟资源管理\backend"

echo [1/4] 清空旧索引数据...
cd /d "%BACKEND_DIR%"
python manage.py shell --command="from apps.knowledge.models import KnowledgeDoc,DocChunk;DocChunk.objects.all().delete();KnowledgeDoc.objects.all().update(status='uploaded')"
echo OK

echo [2/4] 删除旧 ChromaDB 向量库...
if exist "%BACKEND_DIR%\chroma_db" (
    rmdir /s /q "%BACKEND_DIR%\chroma_db"
    echo OK
) else (echo 不存在，跳过)

echo [3/4] 重新索引所有文档（阿里云百炼 Embedding）...
python manage.py shell --command="from apps.knowledge.models import KnowledgeDoc;from apps.knowledge.tasks import index_document_task;from utils.milvus import milvus_client;milvus_client.connect();milvus_client.ensure_collection();docs=KnowledgeDoc.objects.filter(status='uploaded').order_by('id');print('待索引 %d 篇' %% docs.count());[print('  [%d/%d] ID=%%d %%s' %% (i+1,docs.count(),d.id,d.title[:20])) or index_document_task(d.id) for i,d in enumerate(docs)]"
echo OK

echo [4/4] 验证 RAG 搜索...
python manage.py shell --command="from utils.rag import retrieve;from django.contrib.auth import get_user_model;User=get_user_model();admin=User.objects.filter(role='super_admin').first();uc={'user_id':admin.id,'role':'super_admin','college_id':admin.college_id};[print('  \"'+q+'\" '+str(len(retrieve(q,user_context=uc)))+' 条') for q in ['校园新闻','通知公告','mysql']]"

echo.
echo ===== 全部完成！=====
pause
