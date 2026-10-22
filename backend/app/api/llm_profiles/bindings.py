from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.llm_profiles import router
from backend.app.core.security import get_db
from backend.app.db.models import FaqPair, Household, KnowledgeFile, ModelPurposeBinding
from backend.app.llm_runtime.collections import DatabaseCollectionSwitchService
from backend.app.llm_runtime.registry import BindingRejected, ModelRegistry
from backend.app.llm_runtime.runtime_service import ModelRuntimeError
from backend.app.llm_runtime.schemas import PurposeBindingRequest
from backend.app.llm_runtime.source_snapshot import (
    EmbeddingSourceIncomplete,
    EmbeddingSourceSnapshotStale,
    capture_embedding_sources,
    cleanup_embedding_sources,
)
from backend.rag.vector_store import VectorStoreService


@router.get("/bindings")
def list_bindings(db: Session = Depends(get_db)):
    return [{"purpose": item.purpose, "model_id": item.model_id, "config_revision": item.config_revision}
            for item in db.scalars(select(ModelPurposeBinding)).all()]


@router.put("/bindings/{purpose}")
def bind_purpose(purpose: str, payload: PurposeBindingRequest, request: Request,
                 admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
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
    from backend.app.audit.service import audit_admin_action

    audit_admin_action(db, admin, "models.binding.bind", "model_purpose_binding", item.purpose,
                       {"purpose": item.purpose, "model_id": item.model_id, "config_revision": item.config_revision})
    return {"purpose": item.purpose, "model_id": item.model_id, "config_revision": item.config_revision}
