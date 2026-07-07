"""
统一错误码枚举

所有模块的错误码收敛至此，各 views 通过 ErrorCode.XXX.value 引用。
枚举值即为 API 响应中 JSON body 的 "code" 字段值。
"""

from enum import Enum


class ErrorCode(str, Enum):
    """全局统一错误码"""

    # ── Auth / SSO ─────────────────────────────────────────────
    VALIDATION_ERROR = 'VALIDATION_ERROR'
    """参数校验失败：缺少必需参数（code / authCode 等）"""
    INVALID_SSO_CODE = 'INVALID_SSO_CODE'
    """SSO 测试码无效"""
    DINGTALK_AUTH_FAILED = 'DINGTALK_AUTH_FAILED'
    """钉钉扫码授权失败"""

    # ── Chat / AI 对话 ─────────────────────────────────────────
    SENSITIVE_CONTENT = 'SENSITIVE_CONTENT'
    """提问命中敏感词规则"""
    FILE_TOO_LARGE = 'FILE_TOO_LARGE'
    """语音文件超过 10MB 限制"""
    ASR_FAILED = 'ASR_FAILED'
    """语音识别（ASR）转文字失败"""

    # ── Knowledge / 知识文档 ───────────────────────────────────
    CONFLICT = 'CONFLICT'
    """关键词已存在于当前文档中"""
    NOT_FOUND = 'NOT_FOUND'
    """文档不存在"""
    UPLOAD_FAILED = 'UPLOAD_FAILED'
    """OSS 上传失败"""
    OSS_CONFIG_ERROR = 'OSS_CONFIG_ERROR'
    """OSS 配置异常，需联系管理员"""
    OSS_SERVER_ERROR = 'OSS_SERVER_ERROR'
    """OSS 服务端错误"""
    AI_UNAVAILABLE = 'AI_UNAVAILABLE'
    """AI 服务暂时不可用"""

    # ── SSE / 流式响应 ─────────────────────────────────────────
    SSE_ERROR = 'error'
    """SSE 事件流中的通用错误事件"""
