from sqlalchemy import select


def register(client, username="new-user", password="Password@123"):
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "city": "杭州", "phone": "13800000000"},
    )


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_creates_linked_profile_and_returns_tokens(client):
    response = register(client)

    assert response.status_code == 201
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["account"]["username"] == "new-user"
    assert body["account"]["role"] == "user"
    assert body["account"]["user_id"].isdigit()


def test_duplicate_registration_is_rejected(client):
    assert register(client).status_code == 201
    response = register(client)

    assert response.status_code == 409


def test_registration_rejects_password_over_72_utf8_bytes(client):
    response = register(client, username="long-password", password="中" * 25)

    assert response.status_code == 422


def test_password_change_rejects_new_password_over_72_utf8_bytes(client, settings):
    login = client.post(
        "/api/auth/login",
        json={"username": "demo", "password": settings.demo_initial_password},
    ).json()

    response = client.put(
        "/api/auth/password",
        headers=bearer(login["access_token"]),
        json={"current_password": settings.demo_initial_password, "new_password": "中" * 25},
    )

    assert response.status_code == 422


def test_login_rejects_wrong_password(client):
    response = client.post("/api/auth/login", json={"username": "demo", "password": "wrong"})

    assert response.status_code == 401


def test_disabled_account_cannot_login_or_use_existing_token(client, app, settings):
    from backend.app.db.models import Account

    login = client.post(
        "/api/auth/login",
        json={"username": "demo", "password": settings.demo_initial_password},
    ).json()
    with app.state.database.session_factory.begin() as session:
        account = session.scalar(select(Account).where(Account.username == "demo"))
        account.status = "disabled"

    assert client.post(
        "/api/auth/login",
        json={"username": "demo", "password": settings.demo_initial_password},
    ).status_code == 401
    assert client.get("/api/auth/me", headers=bearer(login["access_token"])).status_code == 401


def test_refresh_and_me(client, settings):
    login = client.post(
        "/api/auth/login",
        json={"username": "demo", "password": settings.demo_initial_password},
    ).json()

    refreshed = client.post("/api/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert refreshed.status_code == 200
    access_token = refreshed.json()["access_token"]

    me = client.get("/api/auth/me", headers=bearer(access_token))
    assert me.status_code == 200
    assert me.json()["username"] == "demo"
    assert me.json()["user_id"] == "1001"


def test_access_and_refresh_tokens_cannot_be_interchanged(client, settings):
    login = client.post(
        "/api/auth/login",
        json={"username": "demo", "password": settings.demo_initial_password},
    ).json()

    assert client.post(
        "/api/auth/refresh", json={"refresh_token": login["access_token"]}
    ).status_code == 401
    assert client.get("/api/auth/me", headers=bearer(login["refresh_token"])).status_code == 401


def test_password_change_invalidates_old_password(client, settings):
    login = client.post(
        "/api/auth/login",
        json={"username": "demo", "password": settings.demo_initial_password},
    ).json()
    changed = client.put(
        "/api/auth/password",
        headers=bearer(login["access_token"]),
        json={"current_password": settings.demo_initial_password, "new_password": "NewPassword@456"},
    )

    assert changed.status_code == 204
    assert client.post(
        "/api/auth/login",
        json={"username": "demo", "password": settings.demo_initial_password},
    ).status_code == 401
    assert client.post(
        "/api/auth/login", json={"username": "demo", "password": "NewPassword@456"}
    ).status_code == 200


def test_password_change_rejects_wrong_current_password(client, settings):
    login = client.post(
        "/api/auth/login",
        json={"username": "demo", "password": settings.demo_initial_password},
    ).json()

    response = client.put(
        "/api/auth/password",
        headers=bearer(login["access_token"]),
        json={"current_password": "wrong-password", "new_password": "NewPassword@456"},
    )

    assert response.status_code == 400


def test_admin_must_change_password_flag_clears_after_change(client, settings):
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": settings.admin_initial_password},
    ).json()
    assert login["account"]["must_change_password"] is True

    changed = client.put(
        "/api/auth/password",
        headers=bearer(login["access_token"]),
        json={"current_password": settings.admin_initial_password, "new_password": "NewAdminPassword@456"},
    )

    assert changed.status_code == 204
    relogin = client.post(
        "/api/auth/login", json={"username": "admin", "password": "NewAdminPassword@456"}
    ).json()
    assert relogin["account"]["must_change_password"] is False


def test_registration_retries_user_id_integrity_conflict(client, monkeypatch):
    from backend.app.api import auth

    allocated_ids = iter(["1001", "999999"])
    monkeypatch.setattr(auth, "_next_user_id", lambda session: next(allocated_ids))

    response = register(client, username="retry-user")

    assert response.status_code == 201
    assert response.json()["account"]["user_id"] == "999999"


def test_admin_dashboard_forbids_user_and_allows_admin(client, settings):
    user_token = register(client).json()["access_token"]
    assert client.get("/api/admin/dashboard", headers=bearer(user_token)).status_code == 403

    admin_login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": settings.admin_initial_password},
    ).json()
    response = client.get("/api/admin/dashboard", headers=bearer(admin_login["access_token"]))

    assert response.status_code == 200
    assert {"accounts", "users", "devices", "open_tickets"} <= response.json().keys()


def test_health_endpoint(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
