"""Container CLI entry: materializes the production Celery app for ``celery -A``.

``backend.app.tasks.celery_app`` stays import-safe for test/preview
introspection (guard tests import it directly); this module is imported only by
the Celery CLI inside full deployments, so building the app at import time
keeps the production-mode/MySQL/Redis guards enforcing.
"""
from backend.app.tasks.celery_app import create_celery_app

app = create_celery_app()
