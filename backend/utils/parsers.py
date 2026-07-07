"""自定义解析器"""
import json
import re

from rest_framework.parsers import JSONParser
from rest_framework.exceptions import ParseError


class CleanJSONParser(JSONParser):
    """清理控制字符的 JSON 解析器

    解决用户输入包含未转义控制字符时的 JSON 解析错误问题。
    会自动移除 ASCII 控制字符（0x00-0x1F 和 0x7F），但保留换行符、回车符、制表符。
    """

    def parse(self, stream, media_type=None, parser_context=None):
        """重写 parse 方法，先清理数据再解析"""
        parser_context = parser_context or {}
        encoding = parser_context.get('encoding', 'utf-8')

        try:
            data = stream.read().decode(encoding)
        except UnicodeDecodeError:
            raise ParseError('无效的编码格式')

        cleaned_data = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', data)

        try:
            return json.loads(cleaned_data)
        except json.JSONDecodeError as e:
            raise ParseError(f'JSON parse error - {str(e)}')