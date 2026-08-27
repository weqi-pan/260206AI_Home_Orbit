from collections.abc import Mapping


def resolve_migration_url(configured_url: str, environment: Mapping[str, str]) -> str:
    return environment.get("ALEMBIC_DATABASE_URL") or environment.get("DATABASE_URL") or configured_url
