import hmac
import secrets
import threading
import time
from collections import deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session
from app.models import Credential

ph = PasswordHasher()

MIN_PASSWORD_LENGTH = 12
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


# ── Passwords ────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return ph.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def get_credential(session: Session) -> Credential | None:
    return session.exec(select(Credential)).first()


def ensure_credential(session: Session) -> None:
    """Cria a credencial a partir do .env se ainda não existir."""
    if get_credential(session) is None:
        session.add(
            Credential(
                email=settings.admin_email.strip().lower(),
                password_hash=settings.admin_initial_password_hash,
                must_change_password=True,
            )
        )
        session.commit()


# ── Limite de tentativas de login ────────────────────────────

class LoginRateLimiter:
    """Falhas por IP e globais numa janela deslizante (memória do processo)."""

    def __init__(self, per_ip: int = 5, global_max: int = 30, window_s: int = 15 * 60):
        self.per_ip = per_ip
        self.global_max = global_max
        self.window_s = window_s
        self._by_ip: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, q: deque[float], now: float) -> None:
        while q and now - q[0] > self.window_s:
            q.popleft()

    def is_blocked(self, ip: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._prune(self._global, now)
            q = self._by_ip.get(ip)
            if q is not None:
                self._prune(q, now)
            return len(self._global) >= self.global_max or (q is not None and len(q) >= self.per_ip)

    def record_failure(self, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._global.append(now)
            self._by_ip.setdefault(ip, deque()).append(now)

    def reset(self, ip: str) -> None:
        with self._lock:
            self._by_ip.pop(ip, None)


login_limiter = LoginRateLimiter()


# ── Sessão ───────────────────────────────────────────────────

class LoginRequired(Exception):
    pass


class PasswordChangeRequired(Exception):
    pass


def start_session(request: Request, cred: Credential) -> None:
    request.session.clear()
    request.session["cred_id"] = cred.id
    request.session["ver"] = cred.session_version
    request.session["csrf"] = secrets.token_urlsafe(32)


def _session_credential(request: Request, session: Session) -> Credential | None:
    cred_id = request.session.get("cred_id")
    if cred_id is None:
        return None
    cred = session.get(Credential, cred_id)
    if cred is None or request.session.get("ver") != cred.session_version:
        return None
    return cred


def require_login_allow_pending(
    request: Request, session: Session = Depends(get_session)
) -> Credential:
    """Sessão válida, mesmo que a password ainda tenha de ser alterada."""
    cred = _session_credential(request, session)
    if cred is None:
        request.session.clear()
        raise LoginRequired()
    return cred


def require_login(cred: Credential = Depends(require_login_allow_pending)) -> Credential:
    if cred.must_change_password:
        raise PasswordChangeRequired()
    return cred


# ── CSRF ─────────────────────────────────────────────────────

def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def csrf_protect(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    expected = request.session.get("csrf")
    candidates = [request.headers.get("x-csrf-token")]
    if request.headers.get("content-type", "").startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
        candidates.append((await request.form()).get("csrf_token"))
    if not expected or not any(
        isinstance(sent, str) and hmac.compare_digest(expected, sent) for sent in candidates
    ):
        raise HTTPException(status_code=403, detail="Pedido inválido (CSRF).")
