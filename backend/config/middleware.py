import logging

from django.http import JsonResponse

logger = logging.getLogger(__name__)


class ErrorReportingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        logger.error(f'未捕获异常: {type(exception).__name__}: {exception}')
        return JsonResponse(
            {'detail': '服务器内部错误', 'code': 'INTERNAL_ERROR'},
            status=500,
        )