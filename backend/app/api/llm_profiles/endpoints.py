from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.api.llm_profiles import router
from backend.app.api.llm_profiles.security import _cipher
from backend.app.api.llm_profiles.views import endpoint_view
from backend.app.core.security import get_db, require_admin
from backend.app.db.models import Account, LlmCredential, LlmEndpoint, LlmModel
from backend.app.llm_runtime.endpoint_security import UnsafeEndpointUrl, validate_endpoint_url
from backend.app.llm_runtime.schemas import EndpointCreate, EndpointUpdate


@router.get("/endpoints")
def list_endpoints(db: Session = Depends(get_db)):
    credentials = {item.endpoint_id: item for item in db.scalars(select(LlmCredential)).all()}
    return [endpoint_view(item, credentials.get(item.id)) for item in db.scalars(select(LlmEndpoint)).all()]


@router.post("/endpoints", status_code=201)
def create_endpoint(payload: EndpointCreate, admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    from backend.app.audit.service import audit_admin_action

    try:
        base_url = validate_endpoint_url(str(payload.base_url))
    except UnsafeEndpointUrl as exc:
        raise HTTPException(status_code=422, detail={"code": "ENDPOINT_URL_UNSAFE"}) from exc
    item = LlmEndpoint(protocol=payload.protocol, display_name=payload.display_name, base_url=base_url,
                       timeout_seconds=payload.timeout_seconds, max_retries=payload.max_retries, enabled=payload.enabled)
    db.add(item); db.flush()
    credential = None
    if payload.api_key:
        encrypted = _cipher().encrypt(item.id, item.protocol, 1, payload.api_key)
        credential = LlmCredential(endpoint_id=item.id, ciphertext=encrypted.ciphertext, nonce=encrypted.nonce,
                                   key_version=encrypted.key_version, last_four=encrypted.last_four)
        db.add(credential)
    db.commit(); db.refresh(item)
    audit_admin_action(db, admin, "models.endpoint.create", "llm_endpoint", item.id,
                       {"protocol": item.protocol, "display_name": item.display_name, "base_url": base_url,
                        "has_api_key": credential is not None,
                        "masked_api_key": f"****{credential.last_four}" if credential else None})
    return endpoint_view(item, credential)


@router.patch("/endpoints/{endpoint_id}")
def update_endpoint(endpoint_id: str, payload: EndpointUpdate, admin: Account = Depends(require_admin),
                    db: Session = Depends(get_db)):
    from backend.app.audit.service import audit_admin_action

    item = db.get(LlmEndpoint, endpoint_id)
    if not item:
        raise HTTPException(status_code=404, detail={"code": "ENDPOINT_NOT_FOUND"})
    values = payload.model_dump(exclude_unset=True, exclude={"api_key", "clear_api_key"})
    if "base_url" in values:
        try: values["base_url"] = validate_endpoint_url(str(values["base_url"]))
        except UnsafeEndpointUrl as exc: raise HTTPException(status_code=422, detail={"code": "ENDPOINT_URL_UNSAFE"}) from exc
    protocol = values.get("protocol", item.protocol)
    credential = db.get(LlmCredential, endpoint_id)
    if payload.api_key is not None:
        encrypted = _cipher().encrypt(item.id, protocol, 1, payload.api_key)
        if credential is None:
            credential = LlmCredential(endpoint_id=item.id, ciphertext=encrypted.ciphertext, nonce=encrypted.nonce,
                                       key_version=1, last_four=encrypted.last_four); db.add(credential)
        else:
            credential.ciphertext = encrypted.ciphertext; credential.nonce = encrypted.nonce; credential.last_four = encrypted.last_four
    elif payload.clear_api_key and credential:
        db.delete(credential); credential = None
    elif protocol != item.protocol and credential:
        raise HTTPException(status_code=409, detail={"code": "CREDENTIAL_REENTRY_REQUIRED"})
    for key, value in values.items(): setattr(item, key, value)
    item.revision += 1
    for model in db.scalars(select(LlmModel).where(LlmModel.endpoint_id == item.id)).all():
        model.capability_flags_json = "{}"
    db.commit(); db.refresh(item)
    audit_admin_action(db, admin, "models.endpoint.update", "llm_endpoint", item.id,
                       {**values, "has_api_key": credential is not None,
                        "api_key_rotated": payload.api_key is not None,
                        "api_key_cleared": bool(payload.clear_api_key)})
    return endpoint_view(item, credential)
