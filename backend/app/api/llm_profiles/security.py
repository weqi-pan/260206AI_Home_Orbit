import os

from fastapi import HTTPException

from backend.app.llm_runtime.credentials import CredentialCipher, InvalidMasterKey


def _cipher() -> CredentialCipher:
    try:
        return CredentialCipher(os.getenv("LLM_CREDENTIAL_MASTER_KEY"))
    except InvalidMasterKey as exc:
        raise HTTPException(status_code=503, detail={"code": "MODEL_MASTER_KEY_INVALID"}) from exc
