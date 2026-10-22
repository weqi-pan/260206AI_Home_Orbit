from fastapi import APIRouter, Depends

from backend.app.core.security import require_admin


router = APIRouter(prefix="/models", tags=["models"], dependencies=[Depends(require_admin)])

from . import bindings, endpoints, grants, profiles, quotas  # noqa: E402,F401
