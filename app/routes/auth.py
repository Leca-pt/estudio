from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.db import get_session
from app.models import Credential
from app.security import (
    MIN_PASSWORD_LENGTH,
    csrf_protect,
    get_credential,
    hash_password,
    login_limiter,
    require_login_allow_pending,
    start_session,
    verify_password,
)
from app.templating import templates

router = APIRouter(dependencies=[Depends(csrf_protect)])


def _safe_next(next_url: str | None) -> str:
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/projetos"


@router.get("/login")
def login_form(request: Request, next: str | None = None):
    return templates.TemplateResponse(request, "login.html", {"next": next or "", "error": None})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    session: Session = Depends(get_session),
):
    ip = request.client.host if request.client else "unknown"
    ctx = {"next": next, "email": email}

    if login_limiter.is_blocked(ip):
        ctx["error"] = "Demasiadas tentativas. Tenta novamente daqui a 15 minutos."
        return templates.TemplateResponse(request, "login.html", ctx, status_code=429)

    cred = get_credential(session)
    ok = (
        cred is not None
        and email.strip().lower() == cred.email
        and verify_password(cred.password_hash, password)
    )
    if not ok:
        login_limiter.record_failure(ip)
        ctx["error"] = "Email ou password incorretos."
        return templates.TemplateResponse(request, "login.html", ctx, status_code=401)

    login_limiter.reset(ip)
    start_session(request, cred)
    if cred.must_change_password:
        return RedirectResponse("/alterar-password", status_code=303)
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/alterar-password")
def change_password_form(request: Request, cred: Credential = Depends(require_login_allow_pending)):
    return templates.TemplateResponse(
        request, "change_password.html", {"forced": cred.must_change_password, "error": None}
    )


@router.post("/alterar-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    cred: Credential = Depends(require_login_allow_pending),
    session: Session = Depends(get_session),
):
    error = None
    if not verify_password(cred.password_hash, current_password):
        error = "A password atual está incorreta."
    elif len(new_password) < MIN_PASSWORD_LENGTH:
        error = f"A nova password tem de ter pelo menos {MIN_PASSWORD_LENGTH} caracteres."
    elif new_password != confirm_password:
        error = "As passwords novas não coincidem."
    elif new_password == current_password:
        error = "A nova password tem de ser diferente da atual."
    from_account = (await request.form()).get("origin") == "conta" and not cred.must_change_password
    if error:
        if from_account:
            return templates.TemplateResponse(
                request, "config/account.html",
                {"in_config": True, "section": "conta", "email": cred.email, "error": error, "saved": None},
                status_code=400,
            )
        return templates.TemplateResponse(
            request,
            "change_password.html",
            {"forced": cred.must_change_password, "error": error},
            status_code=400,
        )

    cred.password_hash = hash_password(new_password)
    cred.must_change_password = False
    cred.session_version += 1  # termina outras sessões
    session.add(cred)
    session.commit()
    session.refresh(cred)
    start_session(request, cred)
    return RedirectResponse("/configuracao/conta?guardado=1" if from_account else "/projetos", status_code=303)
