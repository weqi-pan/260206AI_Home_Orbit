import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from backend.app.db.models import Base


WARNING = "BACK UP THE DATABASE BEFORE CONTINUING. This reset permanently deletes all application data."
CONFIRMATION = "RESET HOME ORBIT DATABASE"


def main() -> int:
    parser = argparse.ArgumentParser(description="Operator-invoked clean database reset")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--confirm", help=f"Required exact value: {CONFIRMATION}")
    args = parser.parse_args()
    print(WARNING, file=sys.stderr)
    if args.confirm != CONFIRMATION:
        print(f'Re-run with --confirm "{CONFIRMATION}" after verifying the backup.', file=sys.stderr)
        return 2
    url = make_url(args.database_url)
    if url.drivername == "sqlite" and url.database and url.database != ":memory:":
        path = Path(url.database)
        if path.exists():
            path.unlink()
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    engine.dispose()
    print("Clean Home Orbit schema created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
