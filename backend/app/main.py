from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api import admin, auth, chat, devices, knowledge, tickets
from backend.app.core.config import Settings, get_settings
from backend.app.db.seed import seed_database
from backend.app.db.session import create_database
from backend.rag.vector_store import VectorStoreService


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = create_database(app_settings)
        database.create_all()
        seed_database(database.session_factory, app_settings)
        app.state.database = database
        app.state.settings = app_settings
        app.state.vector_store_service = VectorStoreService(app_settings.chroma_path)
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

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
