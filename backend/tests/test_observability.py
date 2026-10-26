"""可观测性回归闸门：请求上下文、指标、健康探针。"""

import json
import logging


def test_health_reports_checks_and_keeps_status_key(client):
    body = client.get("/health").json()

    # status 键必须保留：docker-compose 的健康检查与既有测试都读它
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    # 测试模式走 create_all()，没有 alembic_version 表，revision 刻意报 None
    assert body["checks"]["revision"] is None
    # 测试模式没有 Redis，不探也不该因它判 degraded
    assert body["checks"]["redis"] == "skipped"


def test_health_degrades_instead_of_failing_when_database_is_unreachable(client, app):
    class BrokenEngine:
        def connect(self):
            raise RuntimeError("database is gone")

    original = app.state.database.engine
    app.state.database.engine = BrokenEngine()
    try:
        response = client.get("/health")
    finally:
        app.state.database.engine = original

    # 探针自身不能变成新的故障点：降级上报，而不是 500
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["database"] == "unavailable"


def test_metrics_endpoint_exposes_prometheus_text(client):
    client.get("/api/auth/me")  # 先制造一次请求，确保计数器有值
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "http_requests_total" in response.text
    assert "http_request_duration_seconds" in response.text


def test_metrics_uses_route_template_not_raw_path(client, settings):
    """高频变动的路径参数不能进标签，否则指标基数会随数据量爆炸。"""
    login = client.post("/api/auth/login", json={"username": "demo", "password": settings.demo_initial_password})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    client.get("/api/devices/model-aaaa", headers=headers)
    client.get("/api/devices/model-bbbb", headers=headers)

    text = client.get("/metrics").text

    assert 'path="/api/devices/{model_id}"' in text
    assert "model-aaaa" not in text
    assert "model-bbbb" not in text


def test_unmatched_route_does_not_leak_raw_path_into_metrics(client):
    client.get("/api/definitely-not-a-route-9f3a")

    text = client.get("/metrics").text

    assert 'path="__unmatched__"' in text
    assert "definitely-not-a-route-9f3a" not in text


def test_request_id_is_generated_and_echoed(client):
    response = client.get("/health")

    assert len(response.headers["x-request-id"]) == 32


def test_incoming_request_id_is_propagated(client):
    response = client.get("/health", headers={"X-Request-ID": "trace-from-gateway"})

    assert response.headers["x-request-id"] == "trace-from-gateway"


def test_access_log_carries_request_id_and_status(client, caplog):
    with caplog.at_level("INFO", logger="backend.app.access"):
        response = client.get("/health", headers={"X-Request-ID": "log-correlation"})

    records = [record for record in caplog.records if record.name == "backend.app.access"]
    assert records, "每个请求都应产生一条 access 日志"
    last = records[-1]
    assert last.request_id == "log-correlation"
    assert last.status == response.status_code
    assert last.path == "/health"


def test_error_response_carries_request_id_header(client):
    """错误响应也要能和日志对上：异常处理器的响应同样要经过中间件加头。

    只放在响应头、不进 body —— body 已经是错误契约（code/message/fields），
    再塞一个字段会让前端和既有断言都得跟着改，而 HTTP 客户端本来就能读头。
    """
    response = client.get("/api/auth/me", headers={"X-Request-ID": "err-correlation"})

    assert response.status_code == 401
    assert response.headers["x-request-id"] == "err-correlation"


def test_json_formatter_serializes_extra_fields():
    from backend.app.core.logging import JsonFormatter

    record = logging.LogRecord("demo", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.request_id = "abc123"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["msg"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "abc123"
    assert "ts" in payload


def test_configure_logging_is_a_noop_in_test_mode(settings):
    from backend.app.core.logging import configure_logging

    root_handlers = list(logging.getLogger().handlers)

    configure_logging(settings)

    # 测试模式不能抢 pytest 的 root handler，否则 caplog 断言会全部失效
    assert logging.getLogger().handlers == root_handlers
