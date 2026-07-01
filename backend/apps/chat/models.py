"""Chat app — AI 问答与对话历史模型"""
from django.db import models
from django.conf import settings


class Conversation(models.Model):
    """会话（一个会话可包含多轮问答）"""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='conversations', verbose_name='用户'
    )
    title = models.CharField(max_length=256, blank=True, default='', verbose_name='会话标题')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'chat_conversation'
        verbose_name = '会话'
        verbose_name_plural = verbose_name
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.user.username} 的会话 #{self.id}'


class ChatMessage(models.Model):
    """聊天消息（用户提问 + AI 回答）"""
    ROLE_CHOICES = [
        ('user', '用户'),
        ('assistant', 'AI助手'),
    ]
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE,
        related_name='messages', verbose_name='所属会话'
    )
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, verbose_name='角色')
    content = models.TextField(verbose_name='消息内容')
    references = models.JSONField(default=list, blank=True, verbose_name='引用的资源列表')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')

    class Meta:
        db_table = 'chat_message'
        verbose_name = '聊天消息'
        verbose_name_plural = verbose_name
        ordering = ['created_at']

    def __str__(self):
        prefix = '用户' if self.role == 'user' else 'AI'
        return f'{prefix}：{self.content[:30]}...'


class QueryLog(models.Model):
    """RAG 查询日志（记录提问/命中/反馈，用于检索质量分析）"""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='query_logs', verbose_name='用户'
    )
    conversation = models.ForeignKey(
        Conversation, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='query_logs',
        verbose_name='关联会话'
    )
    question = models.TextField(verbose_name='用户问题')
    parsed_filters = models.JSONField(null=True, blank=True, verbose_name='LLM解析出的硬过滤条件')
    hit_count = models.IntegerField(default=0, verbose_name='召回命中数')
    top_doc_ids = models.JSONField(null=True, blank=True, verbose_name='召回Top-K的doc_id列表')
    response_ms = models.IntegerField(default=0, verbose_name='响应耗时（毫秒）')
    user_feedback = models.SmallIntegerField(
        default=-1, verbose_name='用户反馈（1=赞/0=踩/-1=无反馈）'
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')

    class Meta:
        db_table = 'query_log'
        verbose_name = '查询日志'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at'], name='idx_qlog_user_created'),
            models.Index(fields=['user_feedback', 'created_at'], name='idx_qlog_feedback'),
        ]

    def __str__(self):
        return f'查询 #{self.id}：{self.question[:30]}'
