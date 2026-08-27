import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.tests.test_agent_platform_runtime import db_session, platform_seed  # noqa: F401


def test_chat_session_creation_requires_exactly_one_household():
    from backend.app.api.chat import exactly_one_household
    assert exactly_one_household(["household-1"]) == "household-1"
    with pytest.raises(Exception) as missing: exactly_one_household([])
    assert missing.value.detail["code"] == "HOUSEHOLD_MEMBERSHIP_MISSING"
    with pytest.raises(Exception) as ambiguous: exactly_one_household(["one", "two"])
    assert ambiguous.value.detail["code"] == "HOUSEHOLD_MEMBERSHIP_AMBIGUOUS"


def test_chat_session_household_is_non_null_in_model():
    from backend.app.db.models import ChatSession
    assert ChatSession.__table__.c.household_id.nullable is False


def test_duplicate_authoritative_installation_is_db_rejected(db_session, platform_seed):
    from backend.app.db.models import AgentInstallation
    duplicate = AgentInstallation(household_id=platform_seed["household_id"],
        deployment_id=platform_seed["deployment_id"], agent_version_id=platform_seed["context"].agent_version_id,
        status="installed")
    db_session.add(duplicate)
    with pytest.raises(IntegrityError): db_session.commit()


def test_authority_installation_query_is_deterministic(db_session, platform_seed):
    from backend.agent.platform.authority import ExecutionAuthority
    first = ExecutionAuthority(db_session).installation(platform_seed["deployment_id"], platform_seed["household_id"],
        platform_seed["context"].agent_version_id)[1]
    second = ExecutionAuthority(db_session).installation(platform_seed["deployment_id"], platform_seed["household_id"],
        platform_seed["context"].agent_version_id)[1]
    assert first.id == second.id == platform_seed["installation_id"]
