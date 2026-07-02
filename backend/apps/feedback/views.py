import logging

from rest_framework import generics, permissions, status
from rest_framework.response import Response

from config.permissions import IsAdmin
from .models import FeedbackQuestion
from .serializers import FeedbackQuestionListSerializer, FeedbackQuestionAnswerSerializer


logger = logging.getLogger(__name__)


class FeedbackPendingListView(generics.ListAPIView):
    """
    待人工回答的问题列表

    GET /api/feedback/pending/
    功能：返回待回答的问题列表，支持按学院筛选
    权限：admin 和 super_admin 可访问
        - admin 只能看到本学院的待回答列表
        - super_admin 可看到全部
    参数：
        - college_id (int, optional): 学院 ID 筛选
        - page (int, default=1): 页码
        - page_size (int, default=20): 每页数量
    返回：分页的待回答列表，每条含 id/question/college/status/created_at
    """
    serializer_class = FeedbackQuestionListSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        user = self.request.user
        queryset = FeedbackQuestion.objects.filter(status='pending')

        # admin 只能看本学院的待回答
        if user.role == 'admin':
            queryset = queryset.filter(college_id=user.college_id)

        # 支持额外学院筛选（super_admin 使用）
        college_id = self.request.query_params.get('college_id')
        if college_id and user.role == 'super_admin':
            queryset = queryset.filter(college_id=college_id)

        return queryset.select_related('college')


class FeedbackAnswerView(generics.UpdateAPIView):
    """
    提交人工回答

    POST /api/feedback/{id}/answer/
    功能：管理员提交人工回答，状态变为 answered
    权限：admin 和 super_admin 可访问
        - admin 只能回答本学院的待回答
        - super_admin 可回答全部
    参数：
        - answer_text (string, required): 人工回答内容
    返回：{"id": 1, "status": "answered", "answered_at": "..."}
    """
    queryset = FeedbackQuestion.objects.filter(status='pending')
    serializer_class = FeedbackQuestionAnswerSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    http_method_names = ['post']  # 仅允许 POST

    def check_object_permissions(self, request, obj):
        # admin 只能回答本学院的问题
        if request.user.role == 'admin' and obj.college_id != request.user.college_id:
            self.permission_denied(request, message='只能回答本学院的待回答')
        return super().check_object_permissions(request, obj)

    def post(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # TODO: 人工回答索引入知识库（依赖 apps/knowledge 的 KnowledgeDoc 模型）
        # 后续 W2-W3 丙完成 knowledge app 后，在此调用 Celery 任务将 answer_text
        # 作为 knowledge_doc (file_type='qa', source_type='manual') 索引入库
        logger.info(f'用户 {request.user.username} 回答了问题 #{instance.id}')

        return Response(serializer.data, status=status.HTTP_200_OK)
