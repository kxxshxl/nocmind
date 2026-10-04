from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import sys
import os
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))
from app.db.session import Base
from app.db.models import *  # noqa: F401,F403

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline():
    url = os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction(): context.run_migrations()


def run_migrations_online():
    cfg = dict(config.get_section(config.config_ini_section, {}) or {})
    cfg["sqlalchemy.url"] = os.getenv("DATABASE_URL", cfg.get("sqlalchemy.url"))
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction(): context.run_migrations()

if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
