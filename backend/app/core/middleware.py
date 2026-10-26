"""请求级上下文与 HTTP 指标。

实现成**纯 ASGI 中间件**而不是 `BaseHTTPMiddleware`：后者会额外包一层任务组并缓冲
响应体，对 `EventSourceResponse` 这类长连接有已知的断流风险。纯 ASGI 只是透传
`send`，流式响应完全不受影响，也就不需要「对 SSE 关掉计时」这种特例。
"""

import logging
import time
import uuid
from contextvars import ContextVar

from prometheus_client import Counter, Histogram


logger = logging.getLogger("backend.app.access")

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

REQUEST_ID_HEADER = "X-Request-ID"

http_requests_total = Counter(
    "http_requests_total", "HTTP 请求总数", ["method", "path", "status"],
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds", "HTTP 请求耗时（秒）", ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


def _route_path(scope) -> str:
    """优先用路由模板，避免 /api/devices/{id} 之类的高基数标签炸掉指标。

    未匹配到路由（404/405）时退化成字面量占位，不落具体路径。
    """
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path or "__unmatched__"


class RequestContextMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(REQUEST_ID_HEADER.lower().encode())
        request_id = incoming.decode("latin-1") if incoming else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500  # 下游没发 response.start 就崩了，按 500 记账

        async def send_with_request_id(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = message.get("headers") or []
                # 覆盖而不是追加：调用方传进来的值可能被下游也写了一份
                message["headers"] = [(k, v) for k, v in headers if k.lower() != REQUEST_ID_HEADER.lower().encode()]
                message["headers"].append((REQUEST_ID_HEADER.encode(), request_id.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration = time.perf_counter() - started
            method = scope.get("method", "-")
            path = _route_path(scope)
            http_requests_total.labels(method=method, path=path, status=str(status_code)).inc()
            http_request_duration_seconds.labels(method=method, path=path).observe(duration)
            logger.info(
                "%s %s %s %.0fms",
                method, scope.get("path", "-"), status_code, duration * 1000,
                extra={"request_id": request_id, "status": status_code, "duration_ms": round(duration * 1000, 1),
                       "method": method, "path": path},
            )
            request_id_var.reset(token)
