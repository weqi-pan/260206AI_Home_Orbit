"""认证与会话加固的回归闸门：限流、轮换、重放断族、登出、令牌版本号。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def login(client, settings, username="demo", password=None):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password or settings.demo_initial_password})


def register(client, username="rotation-user", password="Rotation@123"):
    return client.post("/api/auth/register", json={"username": username, "password": password, "city": "杭州"})


def test_login_is_rate_limited_per_username_with_retry_after(client, settings):
    for _ in range(settings.login_rate_limit_username_attempts):
        assert login(client, settings, password="definitely-wrong").status_code == 401

    blocked = login(client, settings, password="definitely-wrong")

    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "LOGIN_RATE_LIMITED"
    assert int(blocked.headers["retry-after"]) > 0
    # 限流挡在密码校验之前：正确密码此时也进不来，否则闸门等于可绕
    assert login(client, settings).status_code == 429


def test_successful_login_clears_username_failures(client, settings):
    for _ in range(settings.login_rate_limit_username_attempts - 1):
        assert login(client, settings, password="definitely-wrong").status_code == 401

    assert login(client, settings).status_code == 200
    # 成功即清零：正常用户偶尔打错几次密码，不该在窗口内被逐步逼近上限
    for _ in range(settings.login_rate_limit_username_attempts - 1):
        assert login(client, settings, password="definitely-wrong").status_code == 401


def test_rate_limiter_stores_no_plaintext_identifier(client, app, settings):
    from backend.app.db.models import LoginAttempt

    login(client, settings, username="enumeration-probe", password="wrong")

    with app.state.database.session_factory() as session:
        keys = [row.key_hash for row in session.scalars(select(LoginAttempt))]

    assert keys, "失败尝试必须落库，否则限流重启即失效"
    assert all(len(key) == 64 for key in keys), "key 是 sha256 十六进制"
    assert not any("enumeration-probe" in key for key in keys)


def test_refresh_rotates_and_returns_a_new_refresh_token(client, settings):
    first = login(client, settings).json()

    rotated = client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})

    assert rotated.status_code == 200
    body = rotated.json()
    assert body["access_token"] != first["access_token"]
    assert body["refresh_token"] != first["refresh_token"], "refresh 必须轮换，否则泄露的令牌可永久续期"
    assert body["account"]["username"] == "demo"


def test_refresh_replay_revokes_the_whole_family(client, settings):
    first = login(client, settings).json()
    second = client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()

    # 重放已用过的 refresh：模拟令牌被窃取后两边各自使用
    replay = client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})

    assert replay.status_code == 401
    assert replay.json()["detail"]["code"] == "REFRESH_REUSED"
    # 断族：连刚换出来的那条也一起作废，攻击者与用户都被请回登录页
    assert client.post("/api/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 401
    # 提升 token_version 后，重放发生前的访问令牌也立即失效
    assert client.get("/api/auth/me", headers=bearer(second["access_token"])).status_code == 401


def test_logout_revokes_only_that_refresh_token(client, settings):
    first = login(client, settings).json()
    second = login(client, settings).json()

    assert client.post("/api/auth/logout", json={"refresh_token": first["refresh_token"]}).status_code == 204

    assert client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 200


def test_logout_is_idempotent_for_garbage_tokens(client):
    assert client.post("/api/auth/logout", json={"refresh_token": "not-a-jwt"}).status_code == 204


def test_logout_all_revokes_sessions_and_invalidates_access_tokens(client, settings):
    first = login(client, settings).json()
    second = login(client, settings).json()

    assert client.post("/api/auth/logout-all", headers=bearer(first["access_token"])).status_code == 204

    assert client.post("/api/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 401
    assert client.get("/api/auth/me", headers=bearer(first["access_token"])).status_code == 401


def test_password_change_invalidates_existing_access_tokens(client, settings):
    tokens = login(client, settings).json()

    changed = client.put("/api/auth/password", headers=bearer(tokens["access_token"]),
                         json={"current_password": settings.demo_initial_password,
                               "new_password": "NewPassword@456"})

    assert changed.status_code == 204
    # 改密前签发的访问令牌必须立刻失效，不能继续用到过期
    assert client.get("/api/auth/me", headers=bearer(tokens["access_token"])).status_code == 401
    assert client.post("/api/auth/refresh",
                       json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_admin_reset_password_invalidates_sessions(client, app, settings):
    victim = login(client, settings).json()
    admin_login = client.post("/api/auth/login",
                              json={"username": "admin", "password": settings.admin_initial_password}).json()
    with app.state.database.session_factory() as session:
        from backend.app.db.models import Account

        victim_id = session.scalar(select(Account.id).where(Account.username == "demo"))

    reset = client.post(f"/api/admin/accounts/{victim_id}/reset-password",
                        headers=bearer(admin_login["access_token"]), json={"password": "ResetByAdmin@789"})

    assert reset.status_code == 204
    assert client.get("/api/auth/me", headers=bearer(victim["access_token"])).status_code == 401
    assert client.post("/api/auth/refresh",
                       json={"refresh_token": victim["refresh_token"]}).status_code == 401


def test_disabling_an_account_revokes_its_refresh_sessions(client, app, settings):
    victim = login(client, settings).json()
    admin_login = client.post("/api/auth/login",
                              json={"username": "admin", "password": settings.admin_initial_password}).json()
    with app.state.database.session_factory() as session:
        from backend.app.db.models import Account

        victim_id = session.scalar(select(Account.id).where(Account.username == "demo"))

    disabled = client.put(f"/api/admin/accounts/{victim_id}", headers=bearer(admin_login["access_token"]),
                          json={"status": "disabled"})
    assert disabled.status_code == 200

    # 重新启用后，禁用前那对令牌也不该复活
    client.put(f"/api/admin/accounts/{victim_id}", headers=bearer(admin_login["access_token"]),
               json={"status": "active"})
    assert client.post("/api/auth/refresh",
                       json={"refresh_token": victim["refresh_token"]}).status_code == 401


def test_tokens_issued_before_token_version_still_work(client, app, settings):
    """升级兼容：没有 ver 的在途令牌按版本 0 放行，避免发版即全员掉线。"""
    import jwt

    from backend.app.db.models import Account

    with app.state.database.session_factory() as session:
        account = session.scalar(select(Account).where(Account.username == "demo"))
        legacy = jwt.encode(
            {"sub": account.id, "role": account.role, "type": "access", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            settings.jwt_secret, algorithm="HS256",
        )

    assert client.get("/api/auth/me", headers=bearer(legacy)).status_code == 200


def test_duplicate_registration_message_does_not_confirm_the_username(client):
    assert register(client, "taken-name").status_code == 201

    duplicate = register(client, "taken-name")

    assert duplicate.status_code == 409
    detail = duplicate.json()["detail"]
    assert detail["code"] == "USERNAME_UNAVAILABLE"
    # 不能出现「用户名已存在」这类确认性措辞，否则等于提供账号枚举接口
    assert "已存在" not in detail["message"]
