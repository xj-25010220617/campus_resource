from django.contrib import admin
from .models import Conversation, ChatMessage, QueryLog


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'title', 'created_at', 'updated_at']
    list_filter = ['created_at']
    search_fields = ['user__username', 'title']


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'conversation', 'role', 'content', 'created_at']
    list_filter = ['role', 'created_at']
    search_fields = ['content']


@admin.register(QueryLog)
class QueryLogAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'question', 'hit_count', 'response_ms', 'user_feedback', 'created_at']
    list_filter = ['user_feedback', 'created_at']
    search_fields = ['question']
