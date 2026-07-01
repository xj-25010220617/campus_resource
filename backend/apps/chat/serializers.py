from rest_framework import serializers
from .models import Conversation, ChatMessage, QueryLog


class ChatMessageSerializer(serializers.ModelSerializer):
    """聊天消息序列化器（只读）"""

    class Meta:
        model = ChatMessage
        fields = ['id', 'role', 'content', 'references', 'created_at']
        read_only_fields = fields


class ConversationSerializer(serializers.ModelSerializer):
    """会话序列化器（只读）"""
    messages = ChatMessageSerializer(many=True, read_only=True)

    class Meta:
        model = Conversation
        fields = ['id', 'title', 'messages', 'created_at', 'updated_at']
        read_only_fields = fields


class ConversationListSerializer(serializers.ModelSerializer):
    """会话列表序列化器（不含 messages）"""

    class Meta:
        model = Conversation
        fields = ['id', 'title', 'created_at', 'updated_at']
        read_only_fields = fields


class AskSerializer(serializers.Serializer):
    """AI 问答请求参数"""
    conversation_id = serializers.IntegerField(required=False, allow_null=True, help_text='会话ID（不传则新建）')
    question = serializers.CharField(max_length=2000, help_text='用户提问')


class FeedbackSerializer(serializers.Serializer):
    """问答反馈请求参数"""
    message_id = serializers.IntegerField(help_text='消息ID')
    feedback = serializers.IntegerField(min_value=0, max_value=1, help_text='1=赞 / 0=踩')


class MessageRateSerializer(serializers.Serializer):
    """消息反馈请求参数"""
    rating = serializers.ChoiceField(choices=['like', 'dislike'], help_text='like=点赞 / dislike=点踩')
