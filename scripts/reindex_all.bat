@echo off
chcp 65001 >nul

set BACKEND_DIR=D:\实验室204\校园虚拟资源管理\backend
cd /d "%BACKEND_DIR%"

echo [1/4] Clearing old index data...
python manage.py shell --command="from apps.knowledge.models import KnowledgeDoc,DocChunk;DocChunk.objects.all().delete();KnowledgeDoc.objects.all().update(status='uploaded')"

echo [2/4] Removing old ChromaDB...
rmdir /s /q chroma_db 2>nul

echo [3/4] Re-indexing all documents...
python manage.py shell --command="from apps.knowledge.models import KnowledgeDoc;from apps.knowledge.tasks import index_document_task;from utils.milvus import milvus_client;milvus_client.connect();milvus_client.ensure_collection();docs=KnowledgeDoc.objects.filter(status='uploaded').order_by('id');print('Total: '+str(docs.count()));[index_document_task(d.id) for d in docs]"

echo [4/4] Verifying RAG search...
python manage.py shell --command="from utils.rag import retrieve;from django.contrib.auth import get_user_model;User=get_user_model();admin=User.objects.filter(role='super_admin').first();uc={'user_id':admin.id,'role':'super_admin','college_id':admin.college_id};[print(q+': '+str(len(retrieve(q,user_context=uc)))+' hits') for q in ['notice','campus','mysql']]"

echo Done!
pause
