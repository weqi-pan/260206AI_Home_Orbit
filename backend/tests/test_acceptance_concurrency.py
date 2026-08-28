# -*- coding: utf-8 -*-
"""Phase 9 concurrency and idempotency acceptance.

Existing cells (referenced, re-verified by the final full run):
- duplicate approval resume / concurrent graph resume:
  test_agent_platform_fix_round1.py::test_concurrent_graph_resume_claims_side_effect_once
  test_agent_platform_fix_round3.py::test_consumed_public_resume_returns_persisted_result_without_runner
- reminder dispatch race:
  test_household_daily_fix_round1.py::test_concurrent_dispatch_window_produces_exactly_one_notification
- Celery duplicate submit / crash-restart dedup:
  test_celery_tasks.py::test_duplicate_submit_is_idempotent_and_does_not_rerun_side_effects
  test_celery_tasks.py::test_worker_crash_mid_task_does_not_duplicate_on_restart

New cells closed here (TDD):
- quota reservation serialization under concurrent chats (QuotaService.reserve
  must serialize on the household quota row: exactly one admission within the
  monthly limit, no oversubscription);
- market install_count under concurrent installs from two households (the
  counter must be exact, not a lost read-modify-write).
"""
import threading

import pytest
from sqlalchemy import func, select


def _new_household(app, name):
    from backend.app.db.models import Account, Household, HouseholdMember

    with app.state.database.session_factory.begin() as db:
        account = Account(username=name, password_hash="hash")
        db.add(account); db.flush()
        household = Household(name=name + "-home", created_by_account_id=account.id)
        db.add(household); db.flush()
        db.add(HouseholdMember(household_id=household.id, account_id=account.id, role="owner"))
        return account.id, household.id


def _account_of(app, household_id):
    from backend.app.db.models import HouseholdMember

    with app.state.database.session_factory() as db:
        return db.scalar(select(HouseholdMember.account_id).where(HouseholdMember.household_id == household_id))


# ------------------------------------------------------- quota serialization
def test_concurrent_quota_reservations_never_oversubscribe_the_monthly_limit(client, app, monkeypatch):
    """两个并发 reserve 同时进入限额判断：月度额度只允许一次入场（聊天链路
    所用的同一 QuotaService.reserve 串行化在家庭配额行上）。"""
    from backend.app.db.models import LlmEndpoint, LlmModel, UsageQuota
    from backend.app.models.quotas import QuotaExceeded, QuotaService

    _, household_id = _new_household(app, "quota-race")
    with app.state.database.session_factory.begin() as db:
        endpoint = LlmEndpoint(protocol="openai_compatible", display_name="Race", base_url="https://x.example",
                               enabled=True)
        db.add(endpoint); db.flush()
        model = LlmModel(endpoint_id=endpoint.id, model_name="race-chat", model_type="chat", max_tokens=8,
                         requests_per_minute=60, enabled=True)
        db.add(model); db.flush()
        db.add(UsageQuota(household_id=household_id, monthly_limit=100, requests_per_minute=60,
                          agent_token_budget=60))
        model_id = model.id

    factory = app.state.database.session_factory
    real_current_usage = QuotaService.current_usage
    rendezvous = threading.Barrier(2)

    def synced_current_usage(self, hid):
        try:
            rendezvous.wait(timeout=1.0)
        except threading.BrokenBarrierError:
            pass
        return real_current_usage(self, hid)

    monkeypatch.setattr(QuotaService, "current_usage", synced_current_usage)

    admitted, rejected, errors = [], [], []

    def reserver():
        try:
            with factory() as session:
                QuotaService(session).reserve(household_id, model_id, 60)
                session.commit()
            admitted.append(threading.current_thread().name)
        except QuotaExceeded as exc:
            rejected.append(exc.code)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reserver, name=f"chat-{index}") for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == [], errors

    # 核心不变量：60+60>100，只能入场一次
    assert len(admitted) == 1, f"oversubscribed: admitted={admitted}, rejected={rejected}"
    assert rejected == ["HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED"]

    with factory() as db:
        from backend.app.db.models import ModelUsage

        total = db.scalar(select(func.sum(func.coalesce(ModelUsage.tokens, 0))).where(
            ModelUsage.household_id == household_id))
        assert total == 60  # 入场令牌恰为一次


# --------------------------------------------------- market install counter
def test_concurrent_market_installs_keep_install_count_exact(client, app, monkeypatch):
    """两个家庭并发安装同一市场条目：install_count 必须精确等于 2（不是
    读-改-写丢失更新后的 1）。"""
    from backend.app.market.service import MarketInstallService
    from backend.tests.test_celery_tasks import _home
    from backend.tests.test_review_governance import _grant_market_ceiling
    from backend.tests.test_market_install_upgrade import _publish

    _, publisher_home = _home(app)
    _grant_market_ceiling(client, publisher_home)
    entry_id, _ = _publish(client, app, publisher_home)

    _, installer_home_a = _new_household(app, "market-race-a")
    _, installer_home_b = _new_household(app, "market-race-b")
    installer_account_a = _account_of(app, installer_home_a)
    installer_account_b = _account_of(app, installer_home_b)

    factory = app.state.database.session_factory
    barrier = threading.Barrier(2)
    real_copy = MarketInstallService._copy_version

    class SyncedService(MarketInstallService):
        def _copy_version(self, household_id, entry, source_version, reuse_agent_version_id=None):
            try:
                barrier.wait(timeout=5)
            except threading.BrokenBarrierError:
                pass
            return real_copy(self, household_id, entry, source_version,
                             reuse_agent_version_id=reuse_agent_version_id)

    results, errors = [], []

    def installer(household_id, account_id):
        try:
            with factory() as session:
                results.append(SyncedService(session).install(
                    household_id, account_id, entry_id, ["household.shopping.write"]))
                session.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=installer, args=home)
               for home in ((installer_home_a, installer_account_a), (installer_home_b, installer_account_b))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert errors == [], errors
    assert len(results) == 2, [getattr(item, "code", item) for item in errors]

    from backend.app.db.models import MarketEntry, MarketInstall

    with factory() as db:
        entry = db.get(MarketEntry, entry_id)
        installs = db.scalars(select(MarketInstall).where(MarketInstall.entry_id == entry_id)).all()
        assert {row.household_id for row in installs} == {installer_home_a, installer_home_b}
        assert entry.install_count == 2, f"lost increment: {entry.install_count}"
