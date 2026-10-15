"""Páginas ao vivo: cada gravação na base de dados sobe o contador do projeto a que pertence. A página aberta pergunta
o contador a cada poucos segundos e, se mudou (ex.: o conector MCP gravou um campo), redesenha-se sem perder o que se
está a escrever (morph). As mudanças da própria página não contam: a página nova já traz o contador atualizado.

Não contam: os estados dos pedidos de geração e do escritor (têm o seu próprio aviso), o chat do Assistente, a
configuração geral e as credenciais."""

import threading

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import event
from sqlalchemy.orm import Session as OrmSession

from app.security import require_login

router = APIRouter()
_versions: dict[int, int] = {}
_lock = threading.Lock()
IGNORED = {"ImageJob", "PromptTask", "ChatMessage", "ChatProposal", "GlobalConfig", "Credential", "McpClient",
           "McpGrant", "OptionList", "PromptVersion"}


def version(project_id: int | None) -> int:
    return _versions.get(int(project_id or 0), 0)


def _project_of(session, obj) -> int | None:
    from app.models import Asset, Episode, LocationView, Project, Scene, Season, Shot

    if isinstance(obj, Project):
        return obj.id
    pid = getattr(obj, "project_id", None)
    if pid:
        return pid
    if isinstance(obj, Shot):
        sc = session.get(Scene, obj.scene_id)
        return sc.project_id if sc else None
    if isinstance(obj, LocationView):
        a = session.get(Asset, obj.asset_id)
        return a.project_id if a else None
    if isinstance(obj, Episode):
        se = session.get(Season, obj.season_id)
        return se.project_id if se else None
    scene_id = getattr(obj, "scene_id", None)  # linhas do guião, ligações a cenas
    if scene_id:
        sc = session.get(Scene, scene_id)
        return sc.project_id if sc else None
    shot_id = getattr(obj, "shot_id", None)  # checklist, linhas por plano
    if shot_id:
        sh = session.get(Shot, shot_id)
        sc = session.get(Scene, sh.scene_id) if sh else None
        return sc.project_id if sc else None
    return None


@event.listens_for(OrmSession, "before_flush")
def _collect(session, _ctx, _instances) -> None:
    objs = [o for o in list(session.new) + list(session.dirty) + list(session.deleted)
            if type(o).__name__ not in IGNORED and hasattr(o, "__table__")]
    if not objs:
        return
    found = session.info.setdefault("live_projects", set())
    with session.no_autoflush:
        for o in objs:
            try:
                pid = _project_of(session, o)
            except Exception:  # noqa: BLE001 — só informativo
                pid = None
            if pid:
                found.add(int(pid))


@event.listens_for(OrmSession, "after_commit")
def _bump(session) -> None:
    found = session.info.pop("live_projects", None)
    if found:
        with _lock:
            for pid in found:
                _versions[pid] = _versions.get(pid, 0) + 1


@event.listens_for(OrmSession, "after_rollback")
def _forget(session) -> None:
    session.info.pop("live_projects", None)


@router.get("/interno/versao/{project_id}", include_in_schema=False)
def current(project_id: int, _cred=Depends(require_login)):
    return JSONResponse({"v": version(project_id)}, headers={"Cache-Control": "no-store"})
