import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from backend.app.api import (admin, agents, approvals, auth, chat, creator, devices, home, households, installations,
                             knowledge, models, reviews, setup, skills, tickets, tools)
from backend.app.core.config import Settings, get_settings
from backend.app.db.seed import seed_database
from backend.app.db.session import create_database, require_database_revision
from backend.app.services.ticket_summary import TicketSummaryService
from backend.rag.vector_store import VectorStoreUnavailable, configured_vector_store


logger = logging.getLogger(__name__)


def sync_knowledge_vectors(database, vector_service, settings):
    """以 knowledge_files(active) 为准补偿向量库：缺失的入向量库，文件缺失的跳过；任何失败不阻塞启动。"""
    from backend.app.api.knowledge import _knowledge_file_path
    from backend.app.api.knowledge import official_knowledge_skill_version
    from backend.app.db.models import KnowledgeFile

    try:
        with database.session_factory() as session:
            items = list(session.scalars(select(KnowledgeFile).where(KnowledgeFile.status == "active")).all())
    except Exception:
        logger.warning("知识库向量同步跳过：读取 knowledge_files 失败", exc_info=True)
        return
    for item in items:
        try:
            path = _knowledge_file_path(settings, item)
            if path is None:
                logger.warning("知识库向量同步跳过：源文件缺失 filename=%s", item.filename)
                continue
            if not vector_service.has_file(item.id):
                skill_version_id = official_knowledge_skill_version(session, item.household_id)
                vector_service.add_file(item.id, path, item.filename, household_id=item.household_id,
                                        skill_version_id=skill_version_id)
                logger.info("知识库向量同步：已索引 filename=%s", item.filename)
        except VectorStoreUnavailable as exc:
            logger.warning("知识库向量同步跳过：%s", exc)
            return
        except Exception:
            logger.warning("知识库向量同步单文件失败 filename=%s", item.filename, exc_info=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = create_database(app_settings)
        if app_settings.app_mode in {"test", "preview"}:
            database.create_all()
        else:
            require_database_revision(database.engine)
        seed_database(database.session_factory, app_settings)
        from backend.app.api.tools import sync_builtin_catalog
        with database.session_factory() as catalog_db:
            sync_builtin_catalog(catalog_db)
        app.state.database = database
        app.state.settings = app_settings
        app.state.vector_store_service = configured_vector_store(database.session_factory, app_settings.chroma_path)
        from backend.agent.platform.runner import DeploymentAgentRunner
        app.state.deployment_agent_runner = DeploymentAgentRunner(database.session_factory)
        sync_knowledge_vectors(database, app.state.vector_store_service, app_settings)
        yield
        database.engine.dispose()

    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(devices.router, prefix="/api")
    app.include_router(tickets.router, prefix="/api")
    app.include_router(knowledge.router, prefix="/api")
    app.include_router(households.router, prefix="/api")
    app.include_router(setup.router, prefix="/api")
    app.include_router(home.router, prefix="/api")
    app.include_router(models.router, prefix="/api")
    app.include_router(tools.router, prefix="/api")
    app.include_router(agents.router, prefix="/api")
    app.include_router(approvals.router, prefix="/api")
    app.include_router(skills.router, prefix="/api")
    app.include_router(installations.router, prefix="/api")
    app.include_router(creator.router, prefix="/api")
    app.include_router(reviews.router, prefix="/api")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
