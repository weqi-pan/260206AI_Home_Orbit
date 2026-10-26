"""错误契约的回归闸门：所有错误响应的 detail 都是对象，且至少带稳定的 code。"""


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def demo_headers(client, settings):
    login = client.post("/api/auth/login", json={"username": "demo", "password": settings.demo_initial_password})
    return bearer(login.json()["access_token"])


def test_validation_error_uses_stable_code_and_field_list(client):
    response = client.post("/api/auth/register", json={"username": "only-username"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "VALIDATION_FAILED"
    assert detail["fields"], "校验失败必须逐字段给出原因"
    assert {"path", "message"} <= set(detail["fields"][0])


def test_validation_error_does_not_leak_raw_exception_objects(client):
    # Pydantic v2 的 ctx 里会塞原始异常对象，直接序列化会炸；这里确认响应可序列化且不含 repr
    response = client.post("/api/auth/register", json={"username": "x", "password": 12345})

    assert response.status_code == 422
    assert "Traceback" not in response.text
    assert all(isinstance(field["message"], str) for field in response.json()["detail"]["fields"])


def test_unauthenticated_request_has_code(client):
    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "ACCESS_TOKEN_INVALID"
    assert response.json()["detail"]["message"] == "无效或已过期的访问令牌"


def test_admin_route_reports_admin_required(client, settings):
    headers = demo_headers(client, settings)
    response = client.get("/api/admin/accounts", headers=headers)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ADMIN_REQUIRED"
    assert response.json()["detail"]["message"] == "需要管理员权限"


def test_unknown_route_is_wrapped_into_the_same_shape(client):
    """Starlette 自带的 404 是纯字符串 detail，兜底处理必须把它包成对象。"""
    response = client.get("/api/no-such-endpoint")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "NOT_FOUND"


def test_domain_not_found_keeps_business_code(client, settings):
    headers = demo_headers(client, settings)
    response = client.get("/api/devices/no-such-model", headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"] == {"code": "DEVICE_NOT_FOUND", "message": "设备不存在"}
