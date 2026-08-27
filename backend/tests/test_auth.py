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


def test_login_rejects_wrong_password(client):
    response = client.post("/api/auth/login", json={"username": "demo", "password": "wrong"})

    assert response.status_code == 401


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
