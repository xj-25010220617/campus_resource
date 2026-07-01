from django.utils import timezone
from rest_framework import serializers

from .models import FeedbackQuestion


class FeedbackQuestionListSerializer(serializers.ModelSerializer):
    """待回答列表序列化器"""
    college_name = serializers.CharField(source='college.name', read_only=True)

    class Meta:
        model = FeedbackQuestion
        fields = [
            'id', 'question', 'college', 'college_name',
            'status', 'created_at',
        ]
        read_only_fields = ['id', 'status', 'created_at']


class FeedbackQuestionAnswerSerializer(serializers.ModelSerializer):
    """提交人工回答序列化器"""

    answer_text = serializers.CharField(
        required=True, write_only=True,
        label='回答内容',
    )

    class Meta:
        model = FeedbackQuestion
        fields = ['id', 'question', 'answer_text', 'status', 'answered_at']
        read_only_fields = ['id', 'question', 'status', 'answered_at']

    def update(self, instance, validated_data):
        request = self.context['request']
        instance.answer_text = validated_data['answer_text']
        instance.status = 'answered'
        instance.answered_by = request.user
        instance.answered_at = timezone.now()
        instance.save()
        return instance
