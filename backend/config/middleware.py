import logging
import traceback

from django.http import JsonResponse
from django.conf import settings

logger = logging.getLogger(__name__)


class ErrorReportingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        # 记录完整的异常堆栈到日志
        logger.error(f'未捕获异常: {type(exception).__name__}: {exception}')
        logger.error('异常堆栈:', exc_info=True)
        
        # DEBUG 模式下返回详细错误信息
        if settings.DEBUG:
            error_response = {
                'detail': str(exception),
                'code': 'INTERNAL_ERROR',
                'exception_type': type(exception).__name__,
                'traceback': traceback.format_exc().split('\n')[:20],  # 限制堆栈长度
            }
        else:
            error_response = {
                'detail': '服务器内部错误',
                'code': 'INTERNAL_ERROR',
            }
        
        return JsonResponse(error_response, status=500)