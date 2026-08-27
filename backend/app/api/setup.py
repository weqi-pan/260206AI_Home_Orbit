from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security import get_current_account, get_db
from backend.app.db.models import Account, ModelPurposeBinding


router = APIRouter(prefix="/setup", tags=["setup"])
REQUIRED_PURPOSES = ["primary_chat", "embedding"]


class SetupStatus(BaseModel):
    ready: bool
    error_code: str | None
    required_purposes: list[str]
    configured_purposes: list[str]
    missing_purposes: list[str]
    can_configure: bool


def setup_status(session: Session, account: Account) -> SetupStatus:
    configured = list(session.scalars(select(ModelPurposeBinding.purpose).where(
        ModelPurposeBinding.purpose.in_(REQUIRED_PURPOSES)
    )).all())
    missing = [purpose for purpose in REQUIRED_PURPOSES if purpose not in configured]
    return SetupStatus(
        ready=not missing,
        error_code=None if not missing else "MODEL_NOT_CONFIGURED",
        required_purposes=REQUIRED_PURPOSES,
        configured_purposes=configured,
        missing_purposes=missing,
        can_configure=account.role == "admin",
    )


@router.get("/status", response_model=SetupStatus)
def get_setup_status(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    return setup_status(session, account)
