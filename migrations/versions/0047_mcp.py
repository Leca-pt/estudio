"""Conector MCP: clientes registados e autorizações (pedidos, códigos e tokens OAuth, só em hash).

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: Union[str, None] = "0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcpclient",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
    )
    op.create_index("ix_mcpclient_client_id", "mcpclient", ["client_id"], unique=True)
    op.create_table(
        "mcpgrant",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_index("ix_mcpgrant_kind", "mcpgrant", ["kind"])
    op.create_index("ix_mcpgrant_key_hash", "mcpgrant", ["key_hash"], unique=True)
    op.create_index("ix_mcpgrant_client_id", "mcpgrant", ["client_id"])


def downgrade() -> None:
    op.drop_table("mcpgrant")
    op.drop_table("mcpclient")
