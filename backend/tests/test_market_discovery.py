# -*- coding: utf-8 -*-
"""Phase 7 marketplace discovery: search (name/description), category filter,
detail pages with version history / risk-labeled permissions / install count /
official badge, favorites and reports; deprecated entries hidden and
unsearchable; install count idempotent per household."""
import json

import pytest
from sqlalchemy import select

from backend.tests.test_celery_tasks import _home
from backend.tests.test_review_governance import (MARKET_CARD, _admin_headers, _grant_market_ceiling,
                                                  _login, _submit, _validated_draft)


def _register(client, username):
    response = client.post("/api/auth/register", json={"username": username, "password": "Market@123",
                                                       "city": "上海"})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _publish(client, app, household_id, card=None, category="生活", reason="通过"):
    draft_id = _validated_draft(client, app, household_id, card=card)
    body = _submit(client, draft_id, category=category).json()
    decision = client.post(f"/api/reviews/submissions/{body['submission_id']}/decision",
                           headers=_admin_headers(client), json={"action": "approve", "reason": reason})
    assert decision.status_code == 200, decision.text
    with app.state.database.session_factory() as db:
        from backend.app.db.models import MarketEntry

        entry = db.scalar(select(MarketEntry).where(MarketEntry.name == (card or MARKET_CARD)["name"]))
        assert entry is not None
        return entry.id


def test_listings_search_category_detail_and_official_section(client, app):
    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    entry_id = _publish(client, app, household_id)

    headers = _login(client)
    listings = client.get("/api/market/listings", headers=headers).json()
    assert any(item["id"] == entry_id for item in listings["entries"])
    official_names = {item["name"] for item in listings["official"]}
    assert {"家庭厨师", "家庭计划员", "生活百科", "设备管家"} & official_names  # 官方内容出现在发现页

    searched = client.get("/api/market/listings", headers=headers,
                          params={"query": "采购"}).json()
    assert [item["id"] for item in searched["entries"]] == [entry_id]
    assert searched["official"] == [] or all("采购" in item["name"] + item["description"]
                                             for item in searched["official"])
    missing = client.get("/api/market/listings", headers=headers, params={"query": "不存在的东西"}).json()
    assert missing["entries"] == []

    filtered = client.get("/api/market/listings", headers=headers, params={"category": "烹饪"}).json()
    assert all(item["category"] == "烹饪" for item in filtered["entries"])

    detail = client.get(f"/api/market/entries/{entry_id}", headers=headers).json()
    assert detail["name"] == MARKET_CARD["name"]
    assert detail["official"] is False
    assert detail["install_count"] == 0
    assert detail["current_version"]["number"] >= 1
    assert detail["versions"][0]["number"] == detail["current_version"]["number"]
    permissions = {item["key"]: item for item in detail["permissions"]}
    assert permissions["household.shopping.write"]["risk_level"] == "reversible"
    assert permissions["household.recipe.scale"]["risk_level"] == "low"
    assert detail["my_installation"] is None


def test_favorites_toggle_and_listing_state(client, app):
    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    entry_id = _publish(client, app, household_id)
    headers = _login(client)

    assert client.post(f"/api/market/entries/{entry_id}/favorite", headers=headers).status_code == 201
    detail = client.get(f"/api/market/entries/{entry_id}", headers=headers).json()
    assert detail["favorited"] is True
    favorites = client.get("/api/market/favorites", headers=headers).json()
    assert [item["id"] for item in favorites] == [entry_id]
    assert client.delete(f"/api/market/entries/{entry_id}/favorite", headers=headers).status_code == 200
    assert client.get(f"/api/market/entries/{entry_id}", headers=headers).json()["favorited"] is False
    assert client.get("/api/market/favorites", headers=headers).json() == []


def test_report_creates_open_report_visible_to_admin(client, app):
    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    entry_id = _publish(client, app, household_id)
    headers = _login(client)

    response = client.post(f"/api/market/entries/{entry_id}/reports", headers=headers,
                           json={"reason": "描述与实际行为不符"})
    assert response.status_code == 201, response.text
    report_id = response.json()["id"]
    assert response.json()["status"] == "open"

    admin = _admin_headers(client)
    queue = client.get("/api/reviews/reports?status=open", headers=admin).json()
    assert any(item["id"] == report_id for item in queue["items"])
    handled = client.post(f"/api/reviews/reports/{report_id}/handle", headers=admin,
                          json={"action": "dismiss", "note": "查证不成立"})
    assert handled.status_code == 200, handled.text
    assert handled.json()["status"] == "dismissed"
    from backend.app.db.models import AdminActionAudit

    with app.state.database.session_factory() as db:
        audits = db.scalars(select(AdminActionAudit).where(AdminActionAudit.action == "report.dismiss")).all()
        assert any(report_id in (row.resource_id or "") or row.resource_id == report_id for row in audits)
    remaining = client.get("/api/reviews/reports?status=open", headers=admin).json()
    assert not any(item["id"] == report_id for item in remaining["items"])


def test_takedown_hides_entry_from_discovery_and_search(client, app):
    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    entry_id = _publish(client, app, household_id)
    admin = _admin_headers(client)

    takedown = client.post(f"/api/reviews/entries/{entry_id}/takedown", headers=admin,
                           json={"reason": "举报成立，下架处理"})
    assert takedown.status_code == 200, takedown.text
    assert takedown.json()["status"] == "deprecated"

    headers = _login(client)
    listings = client.get("/api/market/listings", headers=headers).json()
    assert not any(item["id"] == entry_id for item in listings["entries"])
    searched = client.get("/api/market/listings", headers=headers,
                          params={"query": MARKET_CARD["name"]}).json()
    assert not any(item["id"] == entry_id for item in searched["entries"])  # 不可搜索
    assert client.get(f"/api/market/entries/{entry_id}", headers=headers).status_code == 404

    from backend.app.db.models import AdminActionAudit

    with app.state.database.session_factory() as db:
        assert db.scalars(select(AdminActionAudit).where(AdminActionAudit.action == "review.takedown")).all()
