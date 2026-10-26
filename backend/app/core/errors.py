"""统一的 API 错误契约与全局异常处理器。

契约：所有错误响应的 `detail` 都是对象，且至少带一个稳定的 `code`：

    {"detail": {"code": "DEVICE_NOT_FOUND", "message": "设备不存在"}}

`message` 是给人看的中文文案，可以随产品调整；`code` 是给程序看的契约，只增不改。
历史上有些端点直接以中文字符串作为 `detail`，这类写法由这里的
`http_exception_handler` 兜底包成对象形态，保证前端拿到的永远是同一形状。

FastAPI 默认的 `RequestValidationError` 返回 `{"detail": [...]}` 数组，完全绕过
code 契约；最常见的一类错误（422）反而没有稳定码。这里统一改写成
`{"detail": {"code": "VALIDATION_FAILED", "fields": [...]}}`。
"""

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


logger = logging.getLogger(__name__)

VALIDATION_FAILED = "VALIDATION_FAILED"
INTERNAL_ERROR = "INTERNAL_ERROR"


def _validation_fields(exc: RequestValidationError) -> list[dict[str, str]]:
    """把 Pydantic 的 loc/msg 拍平成前端可展示的字段列表。

    只搬运字符串，不搬运 `ctx`：Pydantic v2 会在里面塞原始异常对象，序列化会炸。
    """
    return [
        {"path": ".".join(str(part) for part in error.get("loc", ())), "message": str(error.get("msg", ""))}
        for error in exc.errors()
    ]


async def validation_exception_handler(_request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder({
            "detail": {
                "code": VALIDATION_FAILED,
                "message": "请求参数校验失败",
                "fields": _validation_fields(exc),
            }
        }),
    )


async def unhandled_exception_handler(_request, exc: Exception) -> JSONResponse:
    """兜底 500：只回稳定码，异常细节留在服务端日志，不回给客户端。"""
    logger.exception("未处理的服务端异常: %s", type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={"detail": {"code": INTERNAL_ERROR, "message": "服务内部错误，请稍后重试"}},
    )


_STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "UNPROCESSABLE_ENTITY",
    429: "TOO_MANY_REQUESTS",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


async def http_exception_handler(_request, exc: StarletteHTTPException) -> JSONResponse:
    """纯字符串 detail 的兜底：转成 `{"code": 由状态码推导, "message": 原文}`。

    code 只能从状态码推导（NOT_FOUND 之类），拿不到领域语义，所以正式的端点仍应
    自己给 `detail={"code": "DEVICE_NOT_FOUND", ...}`；这里只保证形状不破。
    """
    detail: Any = exc.detail
    if isinstance(detail, str):
        detail = {"code": _STATUS_CODES.get(exc.status_code, f"HTTP_{exc.status_code}"), "message": detail}
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder({"detail": detail}),
        headers=getattr(exc, "headers", None),
    )


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
