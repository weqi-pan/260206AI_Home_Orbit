from fastapi import APIRouter, Depends
import json
import os

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security import get_current_account, get_db
from backend.app.db.models import Account, LlmEndpoint, LlmModel, ModelPurposeBinding
from backend.app.models.credentials import master_key_is_valid


router = APIRouter(prefix="/setup", tags=["setup"])
REQUIRED_PURPOSES = ["primary_chat", "embedding"]
REQUIRED_CAPABILITIES = {
    "primary_chat": {"generation", "streaming", "cancellation", "tool_calling", "structured_output"},
    "embedding": {"embedding"},
}


class SetupStatus(BaseModel):
    ready: bool
    error_code: str | None
    required_purposes: list[str]
    configured_purposes: list[str]
    missing_purposes: list[str]
    can_configure: bool
    master_key_valid: bool


def setup_status(session: Session, account: Account) -> SetupStatus:
    master_key_valid = master_key_is_valid(os.getenv("LLM_CREDENTIAL_MASTER_KEY"))
    rows = session.execute(select(ModelPurposeBinding.purpose, LlmModel.capability_flags_json).join(
        LlmModel, LlmModel.id == ModelPurposeBinding.model_id
    ).join(LlmEndpoint, LlmEndpoint.id == LlmModel.endpoint_id).where(
        ModelPurposeBinding.purpose.in_(REQUIRED_PURPOSES), LlmModel.enabled.is_(True), LlmEndpoint.enabled.is_(True)
    )).all() if master_key_valid else []
    configured = []
    for purpose, raw_flags in rows:
        flags = json.loads(raw_flags or "{}")
        if all(flags.get(name) == "passed" for name in REQUIRED_CAPABILITIES[purpose]):
            configured.append(purpose)
    missing = [purpose for purpose in REQUIRED_PURPOSES if purpose not in configured]
    return SetupStatus(
        ready=master_key_valid and not missing,
        error_code=None if master_key_valid and not missing else "MODEL_NOT_CONFIGURED",
        required_purposes=REQUIRED_PURPOSES,
        configured_purposes=configured,
        missing_purposes=missing,
        can_configure=account.role == "admin",
        master_key_valid=master_key_valid,
    )


@router.get("/status", response_model=SetupStatus)
def get_setup_status(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    return setup_status(session, account)
