"""Escritor de prompts: o estado de um trabalho (o bloco do prompt pergunta enquanto escreve e a página atualiza-se
quando termina) e o mini chat de rever um prompt."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlmodel import Session

from app import prompt_targets as PT, prompt_writer
from app.db import get_session
from app.security import csrf_protect, require_login
from app.stages import get_project
from app.templating import templates

router = APIRouter(prefix="/projetos/{project_id}", dependencies=[Depends(require_login), Depends(csrf_protect)])


@router.get("/escritor/{kind}/{ref}")
def state(request: Request, project_id: int, kind: str, ref: str, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    task = prompt_writer.task_for(session, project.id, kind, ref)
    if task is None:  # terminou: a página atualiza-se (morph) e mostra o prompt novo e o pedido de geração
        return Response(status_code=204, headers={"HX-Trigger": "estudio-refresh"})
    return templates.TemplateResponse(request, "writer/_status.html", {"pid": project.id, "kind": kind, "ref": ref,
                                                                       "task": task})


@router.post("/escritor/{kind}/{ref}/rever")
async def revise(request: Request, project_id: int, kind: str, ref: str, session: Session = Depends(get_session)):
    """Mini chat no bloco do prompt: o pedido corre em segundo plano; a resposta consulta-se em /escritor-rever/<id>."""
    project = get_project(session, project_id)
    data = await request.json()
    prompt, instruction = str(data.get("prompt") or "").strip(), str(data.get("instruction") or "").strip()
    if not prompt or not instruction:
        return JSONResponse({"ok": False, "error": "Escreve o que queres mudar."}, status_code=400)
    try:
        task = prompt_writer.revise_start(session, project, kind, ref, prompt[:8000], instruction[:2000])
    except PT.TargetError:
        raise HTTPException(status_code=404)
    return JSONResponse({"ok": True, "id": task.id, "model": task.model})


@router.get("/escritor-rever/{task_id}")
def revise_state(project_id: int, task_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    return JSONResponse(prompt_writer.revision(session, project.id, task_id))
