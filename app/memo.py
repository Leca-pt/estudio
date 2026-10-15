"""Contas caras feitas uma só vez.

- memo: dentro de um pedido (por sessão da base de dados). As páginas chamam as mesmas funções (o pedido de vídeo de um
  plano, os momentos, a cena 3D) dezenas de vezes; o resultado fica guardado na sessão até haver uma gravação (flush,
  commit ou rollback limpam tudo) — e não se usa enquanto houver mudanças por gravar.
- across: entre pedidos, para o que só muda quando o projeto muda: a chave leva o contador do projeto (app/live.py),
  que sobe a cada gravação; com prazo máximo, para o que muda por outras vias.
"""

import functools
import threading
import time

from sqlalchemy import event
from sqlalchemy.orm import Session as OrmSession


def _k(v):
    if hasattr(v, "__table__"):
        return (type(v).__name__, getattr(v, "id", None))
    if isinstance(v, dict):
        return ("dict", v.get("provider"), v.get("name"), v.get("key"), v.get("group"))
    if isinstance(v, (list, tuple)):
        return tuple(_k(x) for x in v)
    return v


def memo(fn):
    @functools.wraps(fn)
    def wrap(session, *args, **kw):
        if session.new or session.dirty or session.deleted:  # há mudanças por gravar: conta a sério
            return fn(session, *args, **kw)
        cache = session.info.setdefault("memo", {})
        key = (fn.__module__, fn.__qualname__, tuple(_k(a) for a in args), tuple(sorted((k, _k(v)) for k, v in kw.items())))
        if key not in cache:
            cache[key] = fn(session, *args, **kw)
        out = cache[key]
        return out.copy() if isinstance(out, (dict, list)) else out  # quem a usa pode mexer na lista de cima
    return wrap


@event.listens_for(OrmSession, "after_flush")
def _clear_flush(session, _ctx) -> None:
    session.info.pop("memo", None)


@event.listens_for(OrmSession, "after_commit")
def _clear_commit(session) -> None:
    session.info.pop("memo", None)


@event.listens_for(OrmSession, "after_rollback")
def _clear_rollback(session) -> None:
    session.info.pop("memo", None)


_across: dict = {}
_lock = threading.Lock()


def across(project_id: int, key: tuple, fn, ttl: float = 120.0):
    """O resultado de fn() guardado entre pedidos enquanto o projeto não mudar (e no máximo ttl segundos)."""
    from app import live

    k = (project_id, live.version(project_id), key)
    now = time.monotonic()
    hit = _across.get(k)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = fn()
    with _lock:
        if len(_across) > 2000:  # as mais antigas saem
            for old in sorted(_across, key=lambda x: _across[x][0])[:1000]:
                _across.pop(old, None)
        _across[k] = (now, value)
    return value
