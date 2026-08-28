"""Phase 5 asynchronous task foundation.

The executor is broker-independent: Celery workers, beat and the documented
manual trigger boundary all funnel through TaskExecutor so that idempotency,
bounded retries with backoff, dead-lettering and in-app failure notifications
behave identically in SQLite preview/test mode (no broker) and full deployment.
"""
