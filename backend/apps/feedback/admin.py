from django.contrib import admin

from .models import FeedbackQuestion


@admin.register(FeedbackQuestion)
class FeedbackQuestionAdmin(admin.ModelAdmin):
    list_display = ['id', 'question_preview', 'college', 'status', 'created_at']
    list_filter = ['status', 'college']
    search_fields = ['question']
    readonly_fields = ['created_at', 'answered_at']

    def question_preview(self, obj):
        return obj.question[:60]

    question_preview.short_description = '问题摘要'
