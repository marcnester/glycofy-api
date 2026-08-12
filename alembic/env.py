from logging.config import fileConfig

# IMPORTANT: import models so they register with Base.metadata for autogenerate
import app.models  # noqa: F401
from alembic import context

# Import Base + engine from your project (single source of truth)
from app.db import Base, engine

# ------------------------------------------------------------------------------
# Alembic Config object, provides access to .ini values in alembic.ini
# ------------------------------------------------------------------------------
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use your model's MetaData object for 'autogenerate' support
target_metadata = Base.metadata


# ------------------------------------------------------------------------------
# Offline mode: generates SQL scripts without DB connection
# ------------------------------------------------------------------------------
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    # ``str(URL)`` intentionally redacts passwords as ``***``. Alembic needs
    # the actual connection URL when configuring an offline migration.
    url = engine.url.render_as_string(hide_password=False)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=True,  # good practice for SQLite
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# ------------------------------------------------------------------------------
# Online mode: runs migrations directly against the DB
# ------------------------------------------------------------------------------
def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # Reuse the application engine instead of rebuilding it from ``str(URL)``.
    # SQLAlchemy redacts passwords in that string representation, which makes
    # a production migration authenticate with the literal value ``***``.
    # The engine retains the real password internally.
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            render_as_batch=True,  # good practice for SQLite
        )

        with context.begin_transaction():
            context.run_migrations()


# ------------------------------------------------------------------------------
# Choose mode automatically
# ------------------------------------------------------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
