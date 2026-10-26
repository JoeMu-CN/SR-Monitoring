import contextlib
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.agent import models as agent_models  # noqa: F401
from app.ai import models as ai_models  # noqa: F401
from app.auth import models as auth_models  # noqa: F401
from app.config import DATABASE_URL
from app.database import Base
from app.notification import models as notification_models  # noqa: F401
from app.notification import user_models as notification_user_models  # noqa: F401
from app.research import models as research_models  # noqa: F401
from app.scheduler import runtime_models as scheduler_runtime_models  # noqa: F401
from app.signals import models as signal_models  # noqa: F401
from app.suppliers import models as supplier_models  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# app / scheduler / research-worker 共用同一镜像，启动命令都会执行 upgrade head。
# 会话级 advisory lock 让并发迁移串行化：后到者等待前者提交，再按已更新的版本空跑，
# 避免同一迁移被两个进程同时执行而报 DuplicateTable。
MIGRATION_LOCK_KEY = 0x616C656D


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        is_postgres = connection.dialect.name == "postgresql"
        if is_postgres:
            connection.exec_driver_sql(
                "SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,)
            )
            # 结束锁查询的隐式事务；advisory lock 是会话级，提交后仍然持有。
            connection.commit()
        try:
            context.configure(connection=connection, target_metadata=target_metadata)

            with context.begin_transaction():
                context.run_migrations()
        finally:
            if is_postgres:
                with contextlib.suppress(Exception):
                    connection.exec_driver_sql(
                        "SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,)
                    )
                    connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
