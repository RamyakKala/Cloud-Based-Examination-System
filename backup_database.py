"""Create a timestamped backup of the configured SQLite or PostgreSQL database."""

import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parent


def backup_database():
    database_url = make_url(os.environ.get("DATABASE_URL", "sqlite:///exam_system.db"))
    backup_dir = Path(os.environ.get("BACKUP_DIR", ROOT / "backups")).expanduser().resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if database_url.get_backend_name() == "sqlite":
        source = Path(database_url.database or "")
        if not source.is_absolute():
            source = ROOT / "instance" / source
        if not source.is_file():
            raise FileNotFoundError(f"SQLite database not found: {source}")
        target = backup_dir / f"exam_system_{timestamp}.db"
        if target.exists():
            raise FileExistsError(f"Backup already exists: {target}")
        with closing(sqlite3.connect(source)) as source_connection:
            with closing(sqlite3.connect(target)) as target_connection:
                source_connection.backup(target_connection)
        return target

    if database_url.get_backend_name() != "postgresql":
        raise ValueError(f"Unsupported database backend: {database_url.get_backend_name()}")

    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        raise RuntimeError("pg_dump is required to back up PostgreSQL. Install PostgreSQL client tools and retry.")

    target = backup_dir / f"exam_system_{timestamp}.dump"
    if target.exists():
        raise FileExistsError(f"Backup already exists: {target}")
    command = [pg_dump, "--format=custom", "--no-owner", "--file", str(target)]
    if database_url.host:
        command.extend(["--host", database_url.host])
    if database_url.port:
        command.extend(["--port", str(database_url.port)])
    if database_url.username:
        command.extend(["--username", database_url.username])
    if database_url.database:
        command.extend(["--dbname", database_url.database])

    environment = os.environ.copy()
    if database_url.password:
        environment["PGPASSWORD"] = database_url.password
    sslmode = database_url.query.get("sslmode")
    if sslmode:
        environment["PGSSLMODE"] = str(sslmode)

    try:
        subprocess.run(command, check=True, env=environment)
    except subprocess.CalledProcessError:
        target.unlink(missing_ok=True)
        raise
    return target


if __name__ == "__main__":
    print(f"Database backup created: {backup_database()}")
