import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.security import get_db, require_admin
from backend.app.db.models import FaqPair, Household, KnowledgeFile, LlmCredential, LlmEndpoint, LlmModel, ModelGrant, ModelPurposeBinding
from backend.app.models.collections import DatabaseCollectionSwitchService
from backend.app.models.credentials import CredentialCipher, InvalidMasterKey
from backend.app.models.endpoint_security import UnsafeEndpointUrl, validate_endpoint_url
from backend.app.models.quotas import QuotaService
from backend.app.models.registry import BindingRejected, ModelRegistry, RegistryValidationError
from backend.app.models.runtime_service import ModelRuntimeError, ModelRuntimeService
from backend.app.models.source_snapshot import (
    EmbeddingSourceIncomplete, EmbeddingSourceSnapshotStale,
    capture_embedding_sources, cleanup_embedding_sources,
)
from backend.app.models.schemas import EndpointCreate, EndpointUpdate, ModelCreate, ModelUpdate, PurposeBindingRequest, QuotaRequest


router = APIRouter(prefix="/models", tags=["models"], dependencies=[Depends(require_admin)])


def _cipher() -> CredentialCipher:
    try:
        return CredentialCipher(os.getenv("LLM_CREDENTIAL_MASTER_KEY"))
    except InvalidMasterKey as exc:
        raise HTTPException(status_code=503, detail={"code": "MODEL_MASTER_KEY_INVALID"}) from exc


def endpoint_view(item: LlmEndpoint, credential: LlmCredential | None):
    return {
        "id": item.id, "protocol": item.protocol, "display_name": item.display_name, "base_url": item.base_url,
        "timeout_seconds": item.timeout_seconds, "max_retries": item.max_retries, "enabled": item.enabled,
        "revision": item.revision, "disabled_reason": item.disabled_reason,
        "has_api_key": credential is not None, "masked_api_key": f"****{credential.last_four}" if credential else None,
    }


def model_view(item: LlmModel):
    return {
        "id": item.id, "endpoint_id": item.endpoint_id, "model_name": item.model_name, "model_type": item.model_type,
        "temperature": item.temperature, "max_tokens": item.max_tokens, "embedding_dimensions": item.embedding_dimensions,
        "use_responses_api": item.use_responses_api, "capability_flags": json.loads(item.capability_flags_json or "{}"),
        "requests_per_minute": item.requests_per_minute, "agent_token_budget": item.agent_token_budget, "enabled": item.enabled,
    }


@router.get("/endpoints")
def list_endpoints(db: Session = Depends(get_db)):
    credentials = {item.endpoint_id: item for item in db.scalars(select(LlmCredential)).all()}
    return [endpoint_view(item, credentials.get(item.id)) for item in db.scalars(select(LlmEndpoint)).all()]


@router.post("/endpoints", status_code=201)
def create_endpoint(payload: EndpointCreate, db: Session = Depends(get_db)):
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
    return endpoint_view(item, credential)


@router.patch("/endpoints/{endpoint_id}")
def update_endpoint(endpoint_id: str, payload: EndpointUpdate, db: Session = Depends(get_db)):
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
    return endpoint_view(item, credential)


@router.get("/profiles")
def list_models(db: Session = Depends(get_db)):
    return [model_view(item) for item in db.scalars(select(LlmModel)).all()]


@router.post("/profiles", status_code=201)
def create_model(payload: ModelCreate, db: Session = Depends(get_db)):
    try:
        item = ModelRegistry(db).create_model(**payload.model_dump())
    except RegistryValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code}) from exc
    return model_view(item)


@router.patch("/profiles/{model_id}")
def update_model(model_id: str, payload: ModelUpdate, db: Session = Depends(get_db)):
    item = db.get(LlmModel, model_id)
    if not item:
        raise HTTPException(status_code=404, detail={"code": "MODEL_NOT_FOUND"})
    values = payload.model_dump(exclude_unset=True)
    effective_max_tokens = values.get("max_tokens", item.max_tokens)
    effective_agent_budget = values.get("agent_token_budget", item.agent_token_budget)
    if item.model_type == "chat" and effective_max_tokens is None and effective_agent_budget is None:
        raise HTTPException(status_code=422, detail={"code": "MODEL_REQUEST_BUDGET_REQUIRED"})
    for key, value in values.items():
        setattr(item, key, value)
    item.revision += 1
    item.capability_flags_json = "{}"
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail={"code": "MODEL_PROFILE_INVALID"}) from exc
    db.refresh(item)
    return model_view(item)


@router.post("/profiles/{model_id}/capability-tests")
def run_capability_test(model_id: str, request: Request, db: Session = Depends(get_db)):
    model = db.get(LlmModel, model_id)
    if not model: raise HTTPException(status_code=404, detail={"code": "MODEL_NOT_FOUND"})
    tester_factory = getattr(request.app.state, "capability_tester_factory", None)
    try:
        tester = tester_factory(model) if tester_factory else ModelRuntimeService(db).capability_tester(model)
    except ModelRuntimeError as exc:
        raise HTTPException(status_code=503, detail={"code": exc.code}) from exc
    result = tester.run_embedding(model.embedding_dimensions) if model.model_type == "embedding" else tester.run_chat()
    ModelRegistry(db).record_capability_result(model.id, result.capabilities, result.error_code,
                                               {"event_types": [event.type for event in result.events], "dimensions": result.dimensions})
    return {"passed": result.passed, "capabilities": result.capabilities, "error_code": result.error_code, "dimensions": result.dimensions}


@router.get("/bindings")
def list_bindings(db: Session = Depends(get_db)):
    return [{"purpose": item.purpose, "model_id": item.model_id, "config_revision": item.config_revision}
            for item in db.scalars(select(ModelPurposeBinding)).all()]


@router.put("/bindings/{purpose}")
def bind_purpose(purpose: str, payload: PurposeBindingRequest, request: Request, db: Session = Depends(get_db)):
    if purpose != payload.purpose: raise HTTPException(status_code=422, detail={"code": "PURPOSE_MISMATCH"})
    registry = ModelRegistry(db)
    snapshot = None
    try:
        model, _ = registry.validate_binding(purpose, payload.model_id)
        if purpose == "embedding":
            from backend.app.api.knowledge import _knowledge_file_path
            from backend.rag.vector_store import VectorStoreService
            switch = DatabaseCollectionSwitchService(db, getattr(request.app.state, "embedding_store_factory", None))
            try:
                snapshot = capture_embedding_sources(
                    db, request.app.state.settings, _knowledge_file_path, lock=True,
                    staging_root=request.app.state.settings.chroma_path,
                )
            except EmbeddingSourceIncomplete as exc:
                switch.fail(model.id, "EMBEDDING_SOURCE_INCOMPLETE", exc.evidence)
                raise HTTPException(status_code=409, detail={"code": "EMBEDDING_SOURCE_INCOMPLETE"})
            db.commit()
            factory = getattr(request.app.state, "embedding_store_factory", None)
            if factory is None:
                embeddings = ModelRuntimeService(db).build_embeddings_for_model(model)
                from langchain_chroma import Chroma
                factory = lambda collection_name, model_id: Chroma(
                    collection_name=collection_name, embedding_function=embeddings,
                    persist_directory=str(request.app.state.settings.chroma_path),
                )
            switch = DatabaseCollectionSwitchService(db, factory)
            build = switch.prepare(model.id, snapshot.evidence)

            def compare_before_swap(current_build):
                db.expire_all()
                current = capture_embedding_sources(db, request.app.state.settings, _knowledge_file_path, lock=True)
                evidence = json.loads(current_build.evidence_json)
                evidence["final_source_snapshot_token"] = current.token
                if current.token != snapshot.token:
                    raise EmbeddingSourceSnapshotStale(evidence)
                current_build.evidence_json = json.dumps(evidence, sort_keys=True)

            def rebuild_scopes(store):
                from backend.app.api.knowledge import official_knowledge_skill_version
                from backend.app.db.models import FaqPair, KnowledgeFile

                service = VectorStoreService(vector_store=store)
                file_homes = dict(db.execute(select(KnowledgeFile.id, KnowledgeFile.household_id).where(
                    KnowledgeFile.id.in_([item[0] for item in snapshot.files]))).all()) if snapshot.files else {}
                faq_homes = dict(db.execute(select(FaqPair.id, FaqPair.household_id).where(
                    FaqPair.id.in_([item[0] for item in snapshot.faqs]))).all()) if snapshot.faqs else {}
                households = sorted(set(file_homes.values()) | set(faq_homes.values()))
                for household_id in households:
                    service.rebuild(
                        files=[item for item in snapshot.files if file_homes.get(item[0]) == household_id],
                        faqs=[item for item in snapshot.faqs if faq_homes.get(item[0]) == household_id],
                        household_id=household_id,
                        skill_version_id=official_knowledge_skill_version(db, household_id),
                    )

            switch.build(build.id, rebuild_scopes, commit=False, before_swap=compare_before_swap)
        item = registry.bind(purpose, payload.model_id)
    except HTTPException:
        raise
    except EmbeddingSourceSnapshotStale as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code}) from exc
    except BindingRejected as exc: raise HTTPException(status_code=409, detail={"code": exc.code}) from exc
    except ModelRuntimeError as exc: raise HTTPException(status_code=503, detail={"code": exc.code}) from exc
    except Exception as exc: raise HTTPException(status_code=503, detail={"code": "EMBEDDING_REBUILD_FAILED"}) from exc
    finally:
        cleanup_embedding_sources(snapshot)
    return {"purpose": item.purpose, "model_id": item.model_id, "config_revision": item.config_revision}


@router.put("/households/{household_id}/quota")
def configure_quota(household_id: str, payload: QuotaRequest, db: Session = Depends(get_db)):
    item = QuotaService(db).configure(household_id, **payload.model_dump())
    return {"household_id": item.household_id, "monthly_limit": item.monthly_limit,
            "requests_per_minute": item.requests_per_minute, "agent_token_budget": item.agent_token_budget}


@router.get("/households/{household_id}/grants")
def list_grants(household_id: str, db: Session = Depends(get_db)):
    if not db.get(Household, household_id):
        raise HTTPException(status_code=404, detail={"code": "HOUSEHOLD_NOT_FOUND"})
    return [{"household_id": item.household_id, "model_id": item.model_id}
            for item in db.scalars(select(ModelGrant).where(ModelGrant.household_id == household_id)).all()]


@router.put("/households/{household_id}/grants/{model_id}")
def grant_model(household_id: str, model_id: str, db: Session = Depends(get_db)):
    if not db.get(Household, household_id):
        raise HTTPException(status_code=404, detail={"code": "HOUSEHOLD_NOT_FOUND"})
    if not db.get(LlmModel, model_id):
        raise HTTPException(status_code=404, detail={"code": "MODEL_NOT_FOUND"})
    item = db.scalar(select(ModelGrant).where(ModelGrant.household_id == household_id, ModelGrant.model_id == model_id))
    if not item:
        item = ModelGrant(household_id=household_id, model_id=model_id)
        db.add(item)
        db.commit()
    return {"household_id": item.household_id, "model_id": item.model_id}


@router.delete("/households/{household_id}/grants/{model_id}", status_code=204)
def revoke_model(household_id: str, model_id: str, db: Session = Depends(get_db)):
    db.execute(delete(ModelGrant).where(ModelGrant.household_id == household_id, ModelGrant.model_id == model_id))
    db.commit()
