from enum import IntEnum
from typing import Any


class ErrorCode(IntEnum):
    SUCCESS = 0
    REQUEST_INVALID = 40000
    KNOWLEDGE_BASE_NOT_FOUND = 40001
    PERMISSION_DENIED = 40002
    MESSAGE_EMPTY = 40003
    UNSUPPORTED_FILE_TYPE = 40004
    UNAUTHENTICATED = 40100
    INVALID_API_KEY = 40101
    SIGNATURE_FAILED = 40102
    INTERNAL_ERROR = 50000
    QDRANT_FAILED = 50001
    EMBEDDING_FAILED = 50002
    LLM_FAILED = 50003
    DOCUMENT_PARSE_FAILED = 50004
    INTENT_FAILED = 41000
    INTENT_LOW_CONFIDENCE = 41001
    INTENT_SCHEMA_INVALID = 41002
    TEMPLATE_NOT_FOUND = 42000
    TEMPLATE_RENDER_FAILED = 42001
    TEMPLATE_INDEX_FAILED = 42002
    POLICY_DENIED = 43000
    POLICY_ROUTE_INVALID = 43001
    STATE_FAILED = 44000
    REPLY_BUILD_FAILED = 45000
    WECHAT_CALLBACK_FAILED = 60000
    WECHAT_SIGNATURE_FAILED = 60001
    WECHAT_MESSAGE_PARSE_FAILED = 60002
    WECHAT_REPLY_FAILED = 60003


ERROR_MESSAGES = {
    ErrorCode.SUCCESS: "success",
    ErrorCode.REQUEST_INVALID: "请求参数错误",
    ErrorCode.KNOWLEDGE_BASE_NOT_FOUND: "知识库不存在",
    ErrorCode.PERMISSION_DENIED: "用户无权限",
    ErrorCode.MESSAGE_EMPTY: "消息为空",
    ErrorCode.UNSUPPORTED_FILE_TYPE: "文件格式不支持",
    ErrorCode.UNAUTHENTICATED: "未认证",
    ErrorCode.INVALID_API_KEY: "API Key 无效",
    ErrorCode.SIGNATURE_FAILED: "签名验证失败",
    ErrorCode.INTERNAL_ERROR: "服务内部错误",
    ErrorCode.QDRANT_FAILED: "Qdrant 检索失败",
    ErrorCode.EMBEDDING_FAILED: "Embedding 失败",
    ErrorCode.LLM_FAILED: "大模型调用失败",
    ErrorCode.DOCUMENT_PARSE_FAILED: "文档解析失败",
    ErrorCode.INTENT_FAILED: "意图识别失败",
    ErrorCode.INTENT_LOW_CONFIDENCE: "意图置信度过低",
    ErrorCode.INTENT_SCHEMA_INVALID: "意图识别返回结构错误",
    ErrorCode.TEMPLATE_NOT_FOUND: "未找到合适模板",
    ErrorCode.TEMPLATE_RENDER_FAILED: "模板渲染失败",
    ErrorCode.TEMPLATE_INDEX_FAILED: "模板入库失败",
    ErrorCode.POLICY_DENIED: "策略拒绝执行",
    ErrorCode.POLICY_ROUTE_INVALID: "路由结果非法",
    ErrorCode.STATE_FAILED: "用户状态读写失败",
    ErrorCode.REPLY_BUILD_FAILED: "回复组装失败",
    ErrorCode.WECHAT_CALLBACK_FAILED: "微信回调错误",
    ErrorCode.WECHAT_SIGNATURE_FAILED: "微信签名验证失败",
    ErrorCode.WECHAT_MESSAGE_PARSE_FAILED: "微信消息解析失败",
    ErrorCode.WECHAT_REPLY_FAILED: "微信回复失败",
}


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        status_code: int = 400,
        data: Any = None,
    ) -> None:
        self.code = code
        self.message = message or ERROR_MESSAGES[code]
        self.status_code = status_code
        self.data = data
        super().__init__(self.message)
