from alembic import context
from sqlmodel import SQLModel

import app.models  # noqa: F401  (regista as tabelas)
from app.db import engine

target_metadata = SQLModel.metadata


def run_migrations_online() -> None:
    with engine.connect() as connection:
        # O modo batch do SQLite recria tabelas (DROP + RENAME). Com foreign_keys=ON,
        # o DROP dispara os ON DELETE CASCADE e apagaria dados das tabelas filhas.
        # O PRAGMA tem de ser mudado fora de uma transação.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite não suporta ALTER completo
        )
        with context.begin_transaction():
            context.run_migrations()
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Migração deixou chaves estrangeiras inválidas: {violations[:5]}")
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


run_migrations_online()
