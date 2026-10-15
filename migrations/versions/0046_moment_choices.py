"""As vistas escolhidas à mão para os momentos da câmara passam a ficar presas ao momento (segundo e câmara), não ao
número dele: juntar ou apagar outras posições da câmara já não as desloca para o momento errado.

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-04
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: Union[str, None] = "0045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAX_MOMENTS = 6
from app.moments import CLOSE, GOOD, score  # noqa: E402  (a mesma parecença que a app usa)


def _key(k: dict) -> str:  # igual a app/moments.moment_key
    r = lambda v: ",".join(f"{x:.1f}" for x in v)  # noqa: E731
    return f"{float(k['t']):.1f}|{r(k['pos'])}|{r(k['look_at'])}|{float(k.get('lens') or 28):.0f}"


def upgrade() -> None:
    conn = op.get_bind()
    for sid, spec, mom in conn.execute(sa.text("SELECT id, scene3d, moments FROM shot WHERE moments IS NOT NULL")).fetchall():
        spec = spec if isinstance(spec, dict) else json.loads(spec) if spec else {}
        mom = mom if isinstance(mom, dict) else json.loads(mom) if mom else {}
        views = mom.get("views") or {}
        if not any(str(k).isdigit() for k in views):
            continue
        cams = spec.get("camera") or []
        marked = [k for k in cams if k.get("moment") or k.get("panel")]
        chosen = (marked or cams)[:MAX_MOMENTS]
        new = {k: v for k, v in views.items() if not str(k).isdigit()}
        taken: set[int] = set()
        for n_raw, vid in sorted(((k, v) for k, v in views.items() if str(k).isdigit()), key=lambda kv: int(kv[0])):
            row = conn.execute(sa.text("SELECT cam FROM locationview WHERE id = :id"), {"id": int(vid)}).fetchone()
            if not row or not chosen:
                continue
            vcam = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            # a vista vai para o momento cuja câmara mais se parece com a dela (as escolhas antigas podem ter ficado
            # deslocadas quando se juntaram posições); sem nenhum parecido, fica no número antigo se ainda servir
            scored = sorted((score(vcam, {"pos": k["pos"], "look_at": k["look_at"], "lens": k.get("lens", 28)}), i)
                            for i, k in enumerate(chosen) if i not in taken)
            if not scored:
                continue
            best, i = scored[0]
            n = int(n_raw) - 1
            if best > GOOD and 0 <= n < len(chosen) and n not in taken:
                s_n = score(vcam, {"pos": chosen[n]["pos"], "look_at": chosen[n]["look_at"], "lens": chosen[n].get("lens", 28)})
                if s_n <= CLOSE:
                    best, i = s_n, n
            if best <= CLOSE:
                new[_key(chosen[i])] = vid
                taken.add(i)
        mom["views"] = new
        conn.execute(sa.text("UPDATE shot SET moments = :m WHERE id = :id"), {"m": json.dumps(mom), "id": sid})


def downgrade() -> None:
    pass
