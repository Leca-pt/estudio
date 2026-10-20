"""Ferramentas MCP de imagens: gerar na app (A) ou receber imagens geradas fora (B), e decidir as candidatas.

Reutiliza a app tal como está: os alvos dos prompts (prompt_targets: folha, retrato, look, vista, capa), o escritor
(prompt_writer), o botão Gerar (prompt_targets.enqueue → jobs), o arquivo dos resultados (jobs.store_result) e a
aprovação (media_service.approve_media / reject_media). Nada é aprovado sozinho: o que se gera ou envia fica «por
decidir» até approve_image. As referências são as que a app anexa (prompt_targets.inputs: só imagens aprovadas).
"""

import io
import re
from typing import Any

from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from sqlmodel import Session, select

from app import prompt_targets as PT
from app.db import engine
from app.mcp_server import PUBLIC, READ, WRITE, _project, log, server
from app.models import Asset, CharacterLook, ImageJob, LocationView, MediaFile, Project, Scene, Shot

KINDS = {"sheet": "folha", "portrait": "retrato", "look": "look", "view": "vista", "previs": "vista", "keyframe": "chave",
         "cover": "capa"}
KIND_HELP = ("kind: sheet (folha de um asset), portrait (retrato de uma personagem), look (folha de um look, com look_id), "
             "view (vista de um local, screen asset:N com view_id), previs (vista do local num momento de um plano, "
             "screen sb_shot:N com view_id), keyframe (frame-chave de um plano: uma imagem final do take com as "
             "personagens no sítio, a partir do previs de um momento da câmara — o do campo «Frame-chave › Momento», "
             "por omissão o 1.º; um aprovado por momento (storyboard final e animatic) e o utilizador escolhe qual segue "
             "como referência no pedido de vídeo — o último passo do plano no Storyboard; screen sb_shot:N), cover (capa, screen bible ou story)")
MAX_BYTES = 20 * 1024 * 1024
PAID = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)


def _target(s: Session, p: Project, screen: str, kind: str, look_id: int | None, view_id: int | None) -> PT.Target:
    """O alvo da app (prompt_targets) para este ecrã e tipo, verificando que pertencem um ao outro."""
    if kind not in KINDS:
        raise ToolError(f"Tipo desconhecido «{kind}». {KIND_HELP}.")
    head, _, raw = screen.partition(":")
    num = int(raw) if raw.isdigit() else None
    try:
        if kind in ("sheet", "portrait"):
            if head != "asset" or num is None:
                raise ToolError(f"Para {kind}, o screen é asset:N.")
            a = s.get(Asset, num)
            if a is not None and a.kind == "location":
                raise ToolError("Os locais não têm folha nem retrato: as imagens deles são as vistas (kind view, com view_id).")
            if a is not None and kind == "portrait" and a.kind != "character":
                raise ToolError("Só as personagens têm retrato.")
            return PT.get(s, p, KINDS[kind], str(num))
        if kind == "look":
            lk = s.get(CharacterLook, look_id) if look_id else None
            if lk is None or (head == "asset" and lk.asset_id != num):
                raise ToolError("Indica look_id de um look desta personagem (vê os looks em open_screen do asset).")
            return PT.get(s, p, "look", str(lk.id))
        if kind in ("view", "previs"):
            v = s.get(LocationView, view_id) if view_id else None
            if v is None:
                raise ToolError("Indica view_id (os ids das vistas aparecem em open_screen do local ou do plano).")
            if kind == "view" and not (head == "asset" and v.asset_id == num):
                raise ToolError(f"A vista {view_id} não é do local {screen}.")
            if kind == "previs":
                shot = s.get(Shot, num) if head == "sb_shot" and num else None
                if shot is None:
                    raise ToolError("Para previs, o screen é sb_shot:N.")
                from app import moments

                if v.id not in {r["view"].id for r in moments.rows(s, shot) if r["view"] is not None}:
                    raise ToolError(f"A vista {view_id} não está nos momentos do plano {num}.")
            return PT.get(s, p, "vista", str(v.id))
        if kind == "keyframe":
            if head not in ("video_shot", "sb_shot") or num is None:
                raise ToolError("Para keyframe, o screen é sb_shot:N (o frame-chave é o último passo do plano no Storyboard).")
            return PT.get(s, p, "chave", str(num))
        if head not in ("bible", "story", "project"):
            raise ToolError("Para cover, o screen é bible ou story.")
        return PT.get(s, p, "capa", str(p.id))
    except PT.TargetError as exc:
        raise ToolError(str(exc)) from exc


def _candidates(s: Session, t: PT.Target) -> list[MediaFile]:
    q = select(MediaFile).where(MediaFile.project_id == t.project.id, MediaFile.role == t.role, MediaFile.status == "pending")
    for k, v in t.target.items():
        q = q.where(getattr(MediaFile, k) == v)
    if t.kind == "capa":
        q = q.where(MediaFile.asset_id.is_(None))
    return list(s.exec(q.order_by(MediaFile.id.desc())).all())


def _job_of(s: Session, m: MediaFile) -> ImageJob | None:
    return s.exec(select(ImageJob).where(ImageJob.result_media_id == m.id)).first()


def _model_label(s: Session, job: ImageJob | None) -> str:
    if job is None:
        return "carregada"
    if job.provider == "mcp":
        return (job.model or "gerada fora") + " (enviada pelo MCP)"
    from app.routes.gpt import job_view

    return job_view(s, job)["provider_label"]


def _thumb(path, size: int = 768) -> bytes:
    from PIL import Image as PILImage

    im = PILImage.open(path)
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=82)
    return buf.getvalue()


# ── A) gerar na app ───────────────────────────────────────────────────────────

@server.tool(annotations=WRITE, title="Escrever prompt de imagem",
             description="Corre o escritor de prompts da app para um ecrã e tipo (como o botão «Escrever»), grava o prompt "
                         "no campo respetivo (com versão) e devolve-o. Demora 1 a 3 minutos. " + KIND_HELP + ".")
async def write_prompt(project_id: int, screen: str, kind: str, look_id: int | None = None, view_id: int | None = None) -> str:
    from app import prompt_writer as W
    from app.models import PromptTask

    with Session(engine) as s:
        p = _project(s, project_id)
        t = _target(s, p, screen, kind, look_id, view_id)
        _render_ok(s, p, kind, view_id)  # o escritor também olha para o render
        busy = W.task_for(s, p.id, t.kind, t.ref)
        if busy is not None and busy.status == "running":
            raise ToolError("O escritor já está a escrever este prompt. Espera que termine.")
        if busy is not None:
            s.delete(busy)
        task = PromptTask(project_id=p.id, kind=t.kind, ref=t.ref, model=W.writer_label(s))
        s.add(task)
        s.commit()
        tid, kind_pt, ref = task.id, t.kind, t.ref
    await W._run(tid)
    with Session(engine) as s:
        task = s.get(PromptTask, tid)
        if task.status != "done":
            raise ToolError(f"O escritor falhou: {task.error or 'sem motivo'}")
        t = PT.get(s, s.get(Project, project_id), kind_pt, ref)
        log.info("mcp: prompt escrito %s:%s no projeto %s", kind_pt, ref, project_id)
        return f"Prompt escrito e gravado ({t.what}):\n\n{t.prompt}"


@server.tool(annotations=PAID, title="Gerar imagem na app",
             description="Gera imagens com o prompt e as referências do ecrã, como o botão «Gerar»; ficam como candidatas "
                         "por aprovar. model: um valor de list_models (por omissão o da Configuração). count: quantas "
                         "candidatas (1 a 4). Corre em segundo plano: devolve logo um generation_id para generation_status. "
                         "confirm_cost=true só depois de o utilizador aceitar o custo que generation_status indicar "
                         "(o Artlist pede essa confirmação). resolution: \"1K\" | \"2K\" | \"4K\" (sem ele, a do "
                         "projeto; list_models diz as que cada modelo aceita). " + KIND_HELP + ".")
def generate_image(project_id: int, screen: str, kind: str, look_id: int | None = None, view_id: int | None = None,
                   model: str | None = None, count: int = 1, confirm_cost: bool = False,
                   resolution: str | None = None) -> str:
    from app import image_models, jobs
    from app.routes.gpt import default_generator, generator_options, parse_generator

    count = max(1, min(int(count or 1), 4))
    with Session(engine) as s:
        p = _project(s, project_id)
        t = _target(s, p, screen, kind, look_id, view_id)
        if model and model not in {v for v, _l in generator_options(s, t.use)}:
            raise ToolError(f"O modelo «{model}» não está disponível para isto. Vê list_models.")
        _render_ok(s, p, kind, view_id)
        if not (t.prompt or "").strip() or PT.needs_writing(s, t):
            raise ToolError("O prompt deste ecrã está por escrever ou desatualizado: chama write_prompt primeiro.")
        if resolution is not None:
            resolution = str(resolution).strip().upper()
            allowed = image_models.resolutions(*parse_generator(model or default_generator(s, use=t.use, project=p)))
            if not allowed:
                raise ToolError("Este modelo não tem escolha de resolução (gera no tamanho dele): tira o resolution "
                                "ou escolhe outro modelo em list_models.")
            if resolution not in allowed:
                raise ToolError(f"O modelo não gera a {resolution}. Resoluções válidas: {', '.join(allowed)}.")
        ok, msg = PT.enqueue(s, t, {"gen": model, "res": resolution})  # o mesmo que o botão Gerar
        if not ok:
            s.rollback()
            raise ToolError(msg)
        first = s.exec(select(ImageJob).where(ImageJob.project_id == p.id, ImageJob.role == t.role)
                       .order_by(ImageJob.id.desc())).first()
        made = [first]
        for _ in range(count - 1):  # as outras candidatas: o mesmo pedido, outra vez
            made.append(jobs.enqueue(s, p, first.role, first.prompt, first.aspect_ratio, list(first.inputs or []),
                                     kind=first.kind, provider=first.provider,
                                     model_group_id=first.model_group_id if first.provider != "fal" else first.model,
                                     params=dict(first.params) if first.params else None, **jobs.target_of(first)))
        for j in made:
            j.confirm_cost = bool(confirm_cost)
            s.add(j)
        s.commit()
        gid = "g" + "-".join(str(j.id) for j in made)
    jobs.wake()
    log.info("mcp: geração %s (%s) no projeto %s", gid, kind, project_id)
    return f"{msg.split('.')[0]}. generation_id: {gid} ({count} candidata(s)). Vê o estado com generation_status."


def _render_ok(s: Session, p: Project, kind: str, view_id: int | None) -> None:
    """A imagem de uma vista parte do render dela: sem render (ou com a maquete mudada desde então) o resultado sai com
    a geometria errada. Na app a página tira-o ao abrir; aqui é o render_maquete. O frame-chave parte do previs do
    primeiro momento do plano (o mesmo render_maquete, com sb_shot:N)."""
    if kind == "keyframe":
        return
    if kind not in ("view", "previs") or not view_id:
        return
    from app.mcp_maquete import stale_views

    v = s.get(LocationView, view_id)
    a = s.get(Asset, v.asset_id) if v else None
    if a is not None and a.layout3d and v.id in {x.id for x in stale_views(s, p, a)[1]}:
        raise ToolError(f"A vista {view_id} ainda não tem render da maquete atual: chama render_maquete(screen=\"asset:{a.id}\") primeiro.")


def _job_ids(generation_id: str) -> list[int]:
    if not re.fullmatch(r"g\d+(-\d+)*", generation_id or ""):
        raise ToolError("generation_id inválido (ex.: g123-124).")
    return [int(x) for x in generation_id[1:].split("-")]


@server.tool(annotations=READ, description="O estado de uma geração (em fila, a gerar, pronta, erro) e, quando pronta, os "
                                           "ids das candidatas (candidate_id) criadas.")
def generation_status(generation_id: str) -> str:
    states = {"queued": "em fila", "running": "a gerar", "done": "pronta", "failed": "erro"}
    with Session(engine) as s:
        rows, ready = [], []
        for jid in _job_ids(generation_id):
            j = s.get(ImageJob, jid)
            if j is None:
                rows.append(f"- pedido {jid}: não existe")
                continue
            line = f"- pedido {jid}: {states.get(j.status, j.status)}" + (f" · {(j.params or {}).get('resolution')}"
                                                                          if (j.params or {}).get("resolution") else "")
            if j.status == "done" and j.result_media_id:
                line += f" → candidate_id {j.result_media_id}"
                ready.append(j.result_media_id)
            if j.status == "failed":
                line += f" — {j.error}"
                if "confirmação" in (j.error or ""):
                    line += " (para avançar, depois de o utilizador aceitar o custo: chama outra vez generate_image ou generate_video com confirm_cost=true)"
            rows.append(line)
        total = len(rows)
        head = ("pronta" if len(ready) == total else "erro" if all("erro" in r for r in rows)
                else "a gerar" if any("a gerar" in r for r in rows) else "em fila" if any("em fila" in r for r in rows) else "em curso")
        return f"Geração {generation_id}: {head} ({len(ready)}/{total} prontas)\n" + "\n".join(rows)


@server.tool(annotations=READ, description="Os modelos de imagem disponíveis na app agora, com o valor a usar em model, o "
                                           "fornecedor, as resoluções que cada um aceita (para resolution em "
                                           "generate_image) e o custo por resolução; indica o de omissão e a "
                                           "resolução padrão do projeto, se indicares project_id.")
async def list_models(project_id: int | None = None) -> str:
    from app import artlist, image_models, video_cost
    from app.routes.gpt import default_generator, generator_options

    with Session(engine) as s:
        proj = _project(s, project_id) if project_id else None
        default = default_generator(s, use="generate", project=proj)
        proj_res = proj.image_resolution if proj else None
        options = generator_options(s, "generate")
    pairs = [(int(v.split(":")[1]), r) for v, _l in options if v.startswith("artlist:")
             for r in image_models.resolutions("artlist", int(v.split(":")[1]))]
    quotes = await video_cost.image_quotes(pairs)  # cotações do Artlist (guardadas; as que faltam pedem-se agora)
    out = []
    for value, label in options:
        provider, _, key = value.partition(":")
        model_key = int(key) if provider == "artlist" and key.isdigit() else key or None
        res = image_models.resolutions(provider, model_key)
        if provider == "gpt":
            cost = "subscrição (sem custo por imagem)"
        elif provider == "artlist":
            cost = ", ".join(f"{r}: {quotes[(model_key, r)]} cr." if (model_key, r) in quotes else f"{r}: a cotar" for r in res) \
                or (f"{artlist.MODELS[model_key]['cost']} cr." if artlist.MODELS.get(model_key, {}).get("cost") else "—")
        else:
            cost = (image_models.FAL.get(key) or {}).get("price") or "—"
        out.append(f"- {value} — {label.split(' · ')[0]} · {provider} · resoluções {', '.join(res) or 'tamanho fixo do modelo'}"
                   f" · custo {cost}" + (" · (omissão)" if value == default else ""))
    head = "Modelos de imagem (model=…)" + (f"; resolução padrão do projeto: {proj_res}" if proj_res else "") + ":\n"
    return head + "\n".join(out)


# ── B) gerar fora e enviar ────────────────────────────────────────────────────

async def _fetch_image(image_url: str | None, image_base64: str | None) -> bytes:
    """Os bytes de uma imagem enviada por endereço https ou em base64 (até 20 MB)."""
    from app.mcp_video import _fetch

    return await _fetch(image_url, image_base64, MAX_BYTES, "image")


@server.tool(annotations=READ, structured_output=False,
             description="As imagens que a app anexaria como referência para este ecrã e tipo (as aprovadas do próprio "
                         "asset e as das referências da folha), com um endereço temporário (1 h) e, se pedido, em base64; "
                         "e o prompt atual. " + KIND_HELP + ".")
def get_reference_images(project_id: int, screen: str, kind: str, look_id: int | None = None, view_id: int | None = None,
                         include_base64: bool = False) -> list[Any]:
    from app import artlist, ref_tokens
    from app.media import media_root

    with Session(engine) as s:
        p = _project(s, project_id)
        t = _target(s, p, screen, kind, look_id, view_id)
        refs = PT.inputs(s, t)
        lines, out = [], []
        for n, m in enumerate(refs, 1):
            path = media_root() / m.path
            if not path.is_file() or m.status == "rejected":
                continue
            token, desc = ref_tokens.token_for(s, m)
            url = f"{PUBLIC}/pub/ref/{artlist.share(path, ttl_s=3600)}.png"
            lines.append(f"{n}. {token} — {desc}\n   {url}")
            if include_base64:
                out += [f"{n}. {token}", Image(data=_thumb(path, 1536), format="jpeg")]
        head = (f"Referências para {t.what} ({len(lines)}):\n" + ("\n".join(lines) or "nenhuma")
                + f"\n\nPROMPT ATUAL:\n{t.prompt or '(por escrever)'}")
        return [head, *out]


@server.tool(annotations=WRITE, title="Enviar imagem para a app",
             description="Recebe uma imagem gerada fora (image_url ou image_base64; png, jpg ou webp, até 20 MB), guarda-a "
                         "como candidata por aprovar no sítio certo, com o prompt e o modelo usados, e devolve o "
                         "candidate_id. " + KIND_HELP + ".")
async def upload_image(project_id: int, screen: str, kind: str, image_url: str | None = None, image_base64: str | None = None,
                       look_id: int | None = None, view_id: int | None = None, prompt: str | None = None,
                       model: str | None = None) -> str:
    from PIL import Image as PILImage

    from app import jobs

    data = await _fetch_image(image_url, image_base64)
    try:
        im = PILImage.open(io.BytesIO(data))
        fmt = (im.format or "").upper()
        if fmt not in ("PNG", "JPEG", "WEBP"):
            raise ToolError(f"Formato {fmt or 'desconhecido'}: só png, jpg ou webp.")
        buf = io.BytesIO()
        im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(buf, "PNG")  # a app guarda as imagens em PNG
        png = buf.getvalue()
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 — não é uma imagem que se abra
        raise ToolError("O ficheiro não é uma imagem válida (png, jpg ou webp).") from exc
    with Session(engine) as s:
        p = _project(s, project_id)
        t = _target(s, p, screen, kind, look_id, view_id)
        from app.routes.gpt import aspect_for

        job = ImageJob(project_id=p.id, role=t.role, prompt=(prompt or "").strip(), aspect_ratio=aspect_for(s, p, t.role),
                       inputs=[], kind="generate", provider="mcp", model=(model or "").strip()[:120] or None,
                       status="done", **t.target)
        s.add(job)
        s.flush()
        media = jobs.store_result(s, p, job, png)  # o mesmo arquivo dos resultados da app (pasta, nome, «por decidir»)
        media.source = "mcp"
        job.result_media_id = media.id
        s.add_all([media, job])
        s.commit()
        log.info("mcp: imagem enviada → candidata %s (%s) no projeto %s", media.id, kind, project_id)
        return f"Imagem guardada como candidata por aprovar: candidate_id {media.id} ({t.what})."


# ── Comum: candidatas ─────────────────────────────────────────────────────────

@server.tool(annotations=READ, description="As candidatas por aprovar de um ecrã (todas, ou só de um tipo), com "
                                           "candidate_id, data, modelo e prompt. " + KIND_HELP + ".")
def list_candidates(project_id: int, screen: str, kind: str | None = None, look_id: int | None = None,
                    view_id: int | None = None) -> str:
    with Session(engine) as s:
        p = _project(s, project_id)
        rows = []
        for k in ([kind] if kind else _kinds_for(s, screen)):
            for lk, vw in _subtargets(s, screen, k, look_id, view_id):
                t = _target(s, p, screen, k, lk, vw)
                for m in _candidates(s, t):
                    job = _job_of(s, m)
                    rows.append(f"- candidate_id {m.id} · {t.what} · {m.created_at:%Y-%m-%d %H:%M} · {_model_label(s, job)}"
                                + (f"\n  prompt: {(job.prompt or '')[:400]}" if job and job.prompt else ""))
        return ("Candidatas por aprovar:\n" + "\n".join(rows)) if rows else "Não há candidatas por aprovar neste ecrã."


def _kinds_for(s: Session, screen: str) -> list[str]:
    head, _, raw = screen.partition(":")
    if head == "asset" and raw.isdigit():
        a = s.get(Asset, int(raw))
        if a is None:
            return []
        return ["view"] if a.kind == "location" else ["sheet", "portrait", "look"] if a.kind == "character" else ["sheet"]
    if head == "sb_shot":
        return ["previs", "keyframe"]
    if head == "video_shot":
        return ["keyframe"]
    if head in ("bible", "story"):
        return ["cover"]
    raise ToolError("Este ecrã não tem imagens geradas (usa asset:N, sb_shot:N, video_shot:N, bible ou story).")


def _subtargets(s: Session, screen: str, kind: str, look_id: int | None, view_id: int | None) -> list[tuple]:
    """Para listar sem look_id/view_id: todos os looks da personagem, todas as vistas do local ou dos momentos do plano."""
    head, _, raw = screen.partition(":")
    if kind == "look":
        if look_id:
            return [(look_id, None)]
        return [(lk.id, None) for lk in s.exec(select(CharacterLook).where(CharacterLook.asset_id == int(raw))).all()]
    if kind in ("view", "previs"):
        if view_id:
            return [(None, view_id)]
        if kind == "view":
            return [(None, v.id) for v in s.exec(select(LocationView).where(LocationView.asset_id == int(raw))).all()]
        from app import moments

        shot = s.get(Shot, int(raw))
        return [(None, r["view"].id) for r in moments.rows(s, shot) if r["view"] is not None] if shot else []
    return [(None, None)]


@server.tool(annotations=READ, structured_output=False,
             description="Mostra as candidatas por aprovar de um ecrã em miniatura (como view_images, mas das candidatas).")
def view_candidates(project_id: int, screen: str, kind: str | None = None, limit: int = 4,
                    look_id: int | None = None, view_id: int | None = None) -> list[Any]:
    from app.media import media_root

    with Session(engine) as s:
        p = _project(s, project_id)
        found = []
        for k in ([kind] if kind else _kinds_for(s, screen)):
            for lk, vw in _subtargets(s, screen, k, look_id, view_id):
                t = _target(s, p, screen, k, lk, vw)
                found += [(m, t.what) for m in _candidates(s, t)]
        out: list[Any] = []
        for m, what in found[: max(1, min(limit, 8))]:
            path = media_root() / m.path
            if path.is_file():
                out += [f"candidate_id {m.id} · {what}", Image(data=_thumb(path), format="jpeg")]
        return out or ["Não há candidatas por aprovar neste ecrã."]


def _candidate(s: Session, candidate_id: int) -> MediaFile:
    m = s.get(MediaFile, candidate_id)
    if m is None or m.kind != "image" or m.role not in ("sheet", "portrait", "look", "locview", "key", "cover"):
        raise ToolError(f"A candidata {candidate_id} não existe (ou não é uma imagem de folha, retrato, look, vista, "
                        "frame-chave ou capa).")
    if m.status == "rejected":
        raise ToolError(f"A candidata {candidate_id} foi rejeitada e já não existe.")
    return m


@server.tool(annotations=WRITE, title="Aprovar imagem",
             description="Aprova uma candidata, com o mesmo efeito do botão Aprovar da app (substitui a aprovada anterior, "
                         "que volta a «por decidir»).")
def approve_image(candidate_id: int) -> str:
    from app.media_service import approve_media

    with Session(engine) as s:
        m = _candidate(s, candidate_id)
        if m.status == "approved":
            return f"A imagem {candidate_id} já está aprovada."
        approve_media(s, m)
        s.commit()
        log.info("mcp: imagem %s aprovada", candidate_id)
        return f"Aprovada: imagem {candidate_id} ({m.role})."


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False),
             title="Rejeitar imagem",
             description="Rejeita uma candidata, como o botão Rejeitar da app: sai dos resultados e deixa de ser usada como "
                         "referência; o ficheiro fica guardado em «Rejeitados», de onde o utilizador a pode repor.")
def reject_image(candidate_id: int) -> str:
    from app.media_service import reject_media

    with Session(engine) as s:
        m = _candidate(s, candidate_id)
        reject_media(s, m)
        s.commit()
        log.info("mcp: imagem %s rejeitada", candidate_id)
        return f"Rejeitada: imagem {candidate_id} (fica em «Rejeitados»; o utilizador pode repô-la)."


# ── Fotos de referência dos assets ────────────────────────────────────────────
# As fotos carregadas na ficha (o sítio, a pessoa ou o objeto real): vão como referência da folha e, num local, das
# zonas que as escolheram. Como na app, não têm aprovar/rejeitar: entram logo como referência; tirar põe-nas em
# «Rejeitados» (o utilizador pode repô-las).

def _asset_of(s: Session, p: Project, asset_id: int) -> Asset:
    a = s.get(Asset, asset_id)
    if a is None or a.project_id != p.id:
        raise ToolError(f"Não existe o asset {asset_id} no projeto {p.id}. Vê os assets em project_map.")
    return a


def _photo_zones(a: Asset, photo_id: int) -> list[str]:
    return [z or "(sem nome)" for z, ids in (a.zone_refs or {}).items() if photo_id in [int(i) for i in ids if str(i).isdigit()]]


@server.tool(annotations=READ, structured_output=False,
             description="As fotos de referência de um asset (as que o utilizador carregou na ficha: o sítio, a pessoa ou o "
                         "objeto real), com photo_id, tamanho, as zonas da maquete que as usam (locais) e um endereço "
                         "temporário (1 h) para descarregar o ficheiro original; include_base64=true junta as imagens.")
def list_reference_photos(project_id: int, asset_id: int, include_base64: bool = False) -> list[Any]:
    from PIL import Image as PILImage

    from app import artlist
    from app.asset_refs import asset_photos
    from app.media import media_root

    with Session(engine) as s:
        p = _project(s, project_id)
        a = _asset_of(s, p, asset_id)
        lines, out = [], []
        for n, m in enumerate(asset_photos(s, a), 1):
            path = media_root() / m.path
            if not path.is_file():
                continue
            try:
                with PILImage.open(path) as im:
                    size = f"{im.width}x{im.height}"
            except OSError:
                size = "?"
            zones = _photo_zones(a, m.id)
            url = f"{PUBLIC}/pub/ref/{artlist.share(path, ttl_s=3600)}.png"  # a rota serve o ficheiro tal como é (jpg/webp incluídos)
            lines.append(f"{n}. photo_id {m.id} — {path.name}, {size}"
                         + (f", referência das zonas: {', '.join(zones)}" if zones else "") + f"\n   {url}")
            if include_base64:
                out += [f"{n}. photo_id {m.id}", Image(data=_thumb(path, 1536), format="jpeg")]
        use = ("Vão como referência das zonas que as escolheram (campo zonerefs do ecrã do local) e o Assistente usa-as "
               "para descrever o local." if a.kind == "location" else
               "Vão como referência da folha, antes dos assets escolhidos em sheet_refs.")
        head = f"Fotos de referência de {a.name} ({len(lines)}):\n" + ("\n".join(lines) or "nenhuma") + "\n\n" + use
        return [head, *out]


@server.tool(annotations=WRITE, title="Enviar foto de referência",
             description="Junta uma foto de referência à ficha de um asset (image_url https ou image_base64; png, jpg ou "
                         "webp, até 20 MB), como o «Carregar fotos» da app: entra logo como referência (não há aprovar). "
                         "Num local, zone=<nome da divisão> também a escolhe como referência dessa zona.")
async def upload_reference_photo(project_id: int, asset_id: int, image_url: str | None = None,
                                 image_base64: str | None = None, zone: str | None = None) -> str:
    from app import location3d
    from app.media import UploadError, image_extension
    from app.routes.files import store_bytes

    data = await _fetch_image(image_url, image_base64)
    try:
        ext = image_extension(data, "imagem")
    except UploadError as exc:
        raise ToolError(str(exc)) from exc
    with Session(engine) as s:
        p = _project(s, project_id)
        a = _asset_of(s, p, asset_id)
        if zone is not None:
            if a.kind != "location":
                raise ToolError("Só os locais têm zonas.")
            names = [z["name"] for z in location3d.zones(a.layout3d) if z.get("name")]
            if zone not in names:
                raise ToolError(f"Não existe a zona «{zone}» na maquete. Zonas: {', '.join(names) or 'nenhuma'}.")
            if len(location3d.zone_refs(a, zone)) >= location3d.MAX_ZONE_REFS:
                raise ToolError(f"A zona «{zone}» já tem {location3d.MAX_ZONE_REFS} referências (o máximo). Muda o campo "
                                "zonerefs do ecrã do local primeiro.")
        media = store_bytes(s, p, f"assets/{a.name}/fotos", "image", "photo", data, ext, status="approved", asset_id=a.id)
        if zone is not None:
            refs = dict(a.zone_refs or {})
            refs[zone] = list(dict.fromkeys([*refs.get(zone, []), media.id]))
            a.zone_refs = refs
            s.add(a)
        s.commit()
        log.info("mcp: foto de referência %s em %s (projeto %s)", media.id, a.name, project_id)
        return (f"Foto de referência guardada: photo_id {media.id} em {a.name}"
                + (f", escolhida como referência da zona «{zone}»." if zone else "."))


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False),
             title="Tirar foto de referência",
             description="Tira uma foto de referência de um asset, como o botão «Tirar» da app: deixa de ir como referência "
                         "(da folha e das zonas) e o ficheiro fica em «Rejeitados», de onde o utilizador a pode repor.")
def remove_reference_photo(photo_id: int) -> str:
    from app.media_service import reject_media

    with Session(engine) as s:
        m = s.get(MediaFile, photo_id)
        if m is None or m.role != "photo":
            raise ToolError(f"A foto {photo_id} não existe (ou não é uma foto de referência). Vê list_reference_photos.")
        if m.status == "rejected":
            return f"A foto {photo_id} já tinha sido tirada (está em «Rejeitados»)."
        reject_media(s, m)
        s.commit()
        log.info("mcp: foto de referência %s tirada", photo_id)
        return f"Foto {photo_id} tirada: deixa de ir como referência (fica em «Rejeitados»; o utilizador pode repô-la)."
