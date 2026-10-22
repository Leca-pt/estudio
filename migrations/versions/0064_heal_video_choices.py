"""Escolhas de imagens dos planos (frame inicial/final, imagens ligadas) que apontavam para imagens entretanto
substituídas ou rejeitadas passam para as aprovadas no lugar delas (video_inputs.heal). As que não têm substituta
ficam: o plano mostra-as em «Antes de gerar».

Revision ID: 0064
Revises: 0063
Create Date: 2026-10-22
"""
from typing import Sequence, Union

from alembic import op
from sqlmodel import Session, select

revision: str = "0064"
down_revision: Union[str, None] = "0063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app import video_inputs
    from app.models import Project

    with Session(bind=op.get_bind()) as session:
        for p in session.exec(select(Project)).all():
            video_inputs.heal_project(session, p.id)
        session.commit()


def downgrade() -> None:
    pass  # não há como saber que imagem estava escolhida antes
