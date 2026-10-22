from fastapi import APIRouter, Depends
import json
import os

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security import get_current_account, get_db
from backend.app.db.models import Account, LlmEndpoint, LlmModel, ModelPurposeBinding
from backend.app.llm_runtime.credentials import master_key_is_valid
from backend.app.llm_runtime.runtime_service import ModelRuntimeError, ModelRuntimeService


router = APIRouter(prefix="/setup", tags=["setup"])
REQUIRED_PURPOSES = ["primary_chat"]
OPTIONAL_PURPOSES = ["embedding"]
REQUIRED_CAPABILITIES = {
    "primary_chat": {"generation", "streaming", "cancellation", "tool_calling", "structured_output"},
    "embedding": {"embedding"},
}


class SetupStatus(BaseModel):
    ready: bool
    ai_ready: bool
    embedding_ready: bool
    error_code: str | None
    required_purposes: list[str]
    optional_purposes: list[str]
    configured_purposes: list[str]
    missing_purposes: list[str]
    missing_optional_purposes: list[str]
    degraded_features: list[str]
    can_configure: bool
    master_key_valid: bool


def setup_status(session: Session, account: Account) -> SetupStatus:
    master_key_valid = master_key_is_valid(os.getenv("LLM_CREDENTIAL_MASTER_KEY"))
    configured = []
    purposes = [*REQUIRED_PURPOSES, *OPTIONAL_PURPOSES]
    for purpose in purposes if master_key_valid else []:
        try:
            resolved = ModelRuntimeService(session).resolve(purpose, require_grant=False)
        except ModelRuntimeError:
            continue
        flags = resolved.snapshot.profile["capabilities"]
        if all(flags.get(name) == "passed" for name in REQUIRED_CAPABILITIES[purpose]):
            configured.append(purpose)
    missing = [purpose for purpose in REQUIRED_PURPOSES if purpose not in configured]
    missing_optional = [purpose for purpose in OPTIONAL_PURPOSES if purpose not in configured]
    ai_ready = master_key_valid and not missing
    embedding_ready = master_key_valid and "embedding" in configured
    return SetupStatus(
        ready=ai_ready,
        ai_ready=ai_ready,
        embedding_ready=embedding_ready,
        error_code=None if ai_ready else "MODEL_NOT_CONFIGURED",
        required_purposes=REQUIRED_PURPOSES,
        optional_purposes=OPTIONAL_PURPOSES,
        configured_purposes=configured,
        missing_purposes=missing,
        missing_optional_purposes=missing_optional,
        degraded_features=[] if embedding_ready else ["knowledge_retrieval", "vectorization"],
        can_configure=account.role == "admin",
        master_key_valid=master_key_valid,
    )


def purpose_ready(session: Session, purpose: str) -> bool:
    if not master_key_is_valid(os.getenv("LLM_CREDENTIAL_MASTER_KEY")):
        return False
    try:
        resolved = ModelRuntimeService(session).resolve(purpose, require_grant=False)
    except ModelRuntimeError:
        return False
    flags = resolved.snapshot.profile["capabilities"]
    return all(flags.get(name) == "passed" for name in REQUIRED_CAPABILITIES[purpose])


@router.get("/status", response_model=SetupStatus)
def get_setup_status(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    return setup_status(session, account)
