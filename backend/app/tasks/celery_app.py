"""Celery application for full deployment (production + MySQL + Redis).

SQLite preview/test mode must never build or start this app; import-time side
effects are avoided so the module is safe to import for introspection while
``create_celery_app`` refuses non-production settings.
"""
from sqlalchemy.engine import make_url

from backend.app.core.config import get_settings


def assert_full_deployment_mode(settings=None) -> None:
    settings = settings or get_settings()
    if settings.app_mode != "production":
        raise RuntimeError("CELERY_REQUIRES_PRODUCTION_MODE")
    if not make_url(settings.database_url).drivername.startswith("mysql"):
        raise RuntimeError("CELERY_REQUIRES_MYSQL")
    if not settings.redis_url.startswith("redis://"):
        raise RuntimeError("CELERY_REQUIRES_REDIS")


def create_celery_app(settings=None):
    assert_full_deployment_mode(settings)
    settings = settings or get_settings()
    from celery import Celery
    from celery.schedules import crontab

    app = Celery("home_orbit", broker=settings.redis_url, backend=settings.redis_url)
    app.conf.update(
        task_default_queue="home-orbit",
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_time_limit=600,
        task_soft_time_limit=540,
        timezone="UTC",
        beat_schedule={
            "reminders-dispatch": {"task": "tasks.reminders-dispatch", "schedule": 30.0},
            "process-retries": {"task": "tasks.process-retries", "schedule": 30.0},
            "models-health-check": {"task": "tasks.models-health-check",
                                    "schedule": crontab(minute=0)},
        },
    )
    from backend.app.tasks.worker_tasks import register_tasks

    return register_tasks(app)


def worker_app():
    """Entrypoint used by ``celery -A backend.app.tasks.celery_app:worker_app worker``."""
    return create_celery_app()
