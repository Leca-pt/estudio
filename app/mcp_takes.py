"""Ferramentas MCP para trabalhar plano a plano: os takes de um plano (com o que os gerou), frames tirados de um take
(para referência de outro plano ou imagem de uma vista), o valor completo de um campo, gravar vários campos de uma vez
e o custo final do próximo take."""

import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from sqlmodel import Session, select

from app.db import engine
from app.mcp_server import PUBLIC, READ, WRITE, _context, _project, _write_fields, log, server
from app.models import Asset, ImageJob, LocationView, MediaFile, Scene, Shot

STATUS = {"approved": "aprovado", "rejected": "rejeitado", "pending": "por decidir"}
LINK_TTL = 2 * 3600  # os links dos vídeos e miniaturas valem 2 h


def _shot(s: Session, project_id: int, shot_id: int) -> Shot:
    p = _project(s, project_id)
    sh = s.get(Shot, shot_id)
    sc = s.get(Scene, sh.scene_id) if sh else None
    if sc is None or sc.project_id != p.id:
        raise ToolError(f"O plano {shot_id} não é deste projeto.")
    return sh


def _link(path: Path, ext: str) -> str:
    from app.artlist import share

    return f"{PUBLIC}/pub/ref/{share(path, ttl_s=LINK_TTL)}{ext}"


def _thumb(rel: str) -> Path | None:
    """Miniatura do take (um fotograma a meio segundo), guardada ao lado do vídeo e feita uma só vez."""
    from app.media import video_thumb

    return video_thumb(rel)


@server.tool(annotations=READ, structured_output=True, title="Takes de um plano",
             description="Os takes gerados (e carregados) de um plano, do mais recente para o mais antigo: id, data, estado "
                         "(aprovado / rejeitado / por decidir), modelo, resolução, duração, custo, URL do vídeo e da "
                         "miniatura (válidos 2 h), e o prompt e o que seguiu no pedido (video_inputs) com que foi gerado.")
def list_takes(project_id: int, shot_id: int) -> dict[str, Any]:
    from app.routes.video import take_labels

    with Session(engine) as s:
        sh = _shot(s, project_id, shot_id)
        clips = list(s.exec(select(MediaFile).where(MediaFile.shot_id == sh.id, MediaFile.role == "clip")
                            .order_by(MediaFile.id.desc())).all())
        jobs = {j.result_media_id: j for j in s.exec(select(ImageJob).where(
            ImageJob.result_media_id.in_([c.id for c in clips]))).all()} if clips else {}
        labels = take_labels(s, clips)
        from app.media import media_root

        takes = []
        for c in clips:
            j = jobs.get(c.id)
            params = (j.params or {}) if j else {}
            path = media_root() / c.path
            alive = path.is_file()  # os takes rejeitados ficam guardados
            thumb = _thumb(c.path) if alive else None
            takes.append({
                "take_id": c.id, "label": labels.get(c.id, "").split(" · ")[0] or None,
                "created_at": c.created_at.strftime("%Y-%m-%d %H:%M"), "status": STATUS.get(c.status, c.status),
                "chosen": c.id == sh.approved_clip_id,
                "model": params.get("model_name") or (labels.get(c.id, "").split(" · ")[1:2] or [None])[0]
                         or (None if j else "carregado"),
                "resolution": params.get("resolution"), "duration_s": round(c.duration_ms / 1000, 2) if c.duration_ms else None,
                "asked_duration_s": j.duration_s if j else None, "credits": j.credits if j else None,
                "quoted_credits": params.get("quote"), "video_url": _link(path, ".mp4") if alive else None,
                "thumbnail_url": _link(thumb, ".png") if thumb else None, "prompt": j.prompt if j else None,
                "video_inputs": {"guide": params.get("guide"), "sent": params.get("sent"),
                                 "kind": j.kind if j else None} if j else None,
                "artlist_generation_id": params.get("generation_id"),
            })
        return {"shot_id": sh.id, "shot": f"P{sh.number:02d}", "duration_s": sh.duration_s, "takes": takes}


def _frame(src: Path, t: str) -> bytes:
    """O fotograma do take no segundo t, ou "first" / "last", em PNG (tamanho original)."""
    if t == "first":
        args = ["-i", str(src), "-frames:v", "1"]
    elif t == "last":
        args = ["-sseof", "-0.1", "-i", str(src), "-update", "1", "-frames:v", "1"]
    else:
        try:
            sec = max(0.0, float(str(t).replace(",", ".")))
        except ValueError as exc:
            raise ToolError('t é um número de segundos, "first" ou "last".') from exc
        args = ["-ss", f"{sec:.3f}", "-i", str(src), "-frames:v", "1"]
    run = subprocess.run(["ffmpeg", "-v", "error", *args, "-f", "image2", "-vcodec", "png", "-"], capture_output=True,
                         timeout=120)
    if run.returncode != 0 or not run.stdout.startswith(b"\x89PNG"):
        raise ToolError("Não consegui tirar o fotograma desse segundo (o take é mais curto?).")
    return run.stdout


@server.tool(annotations=WRITE, structured_output=True, title="Fotograma de um take",
             description="Tira o fotograma de um take no segundo t (ou \"first\" / \"last\") e guarda-o já aprovado. "
                         "target: \"shot_ref:<plano>\" — fica como imagem de referência desse plano (aparece nas "
                         "video_inputs dele como @SHOT<plano>.f<id>, desligada; liga-a com o campo video_inputs, como ref, "
                         "start_frame ou end_frame) — ou \"view:<asset_id>:<view_id>\" — passa a ser a imagem aprovada "
                         "dessa vista do local (só para frames sem personagens: as vistas são do local vazio).")
def frame_from_take(project_id: int, shot_id: int, take_id: int, t: str, target: str) -> dict[str, Any]:
    from app.media import media_root, safe_segment, write_file
    from app.media_service import approve_media

    with Session(engine) as s:
        sh = _shot(s, project_id, shot_id)
        p = _project(s, project_id)
        take = s.get(MediaFile, take_id)
        if take is None or take.shot_id != sh.id or take.role != "clip":
            raise ToolError(f"O take {take_id} não é um take do plano {shot_id} (vê list_takes).")
        src = media_root() / take.path
        if not src.is_file():
            raise ToolError("O ficheiro do take já não existe.")
        png = _frame(src, str(t).strip().lower())
        t_ms = 0 if str(t) == "first" else -1 if str(t) == "last" else int(float(str(t).replace(",", ".")) * 1000)
        kind, _, rest = target.partition(":")
        if kind == "shot_ref":
            dest = s.get(Shot, int(rest)) if rest.isdigit() else None
            dsc = s.get(Scene, dest.scene_id) if dest else None
            if dsc is None or dsc.project_id != p.id:
                raise ToolError(f"O plano {rest} não é deste projeto.")
            base = PurePosixPath(safe_segment(p.prefix), "planos", str(dest.id), "referencias")
            n = len(s.exec(select(MediaFile).where(MediaFile.shot_id == dest.id, MediaFile.role == "shotref")).all()) + 1
            rel = base / f"v{n}_de{sh.id}_t{max(t_ms, 0)}{'_fim' if t_ms < 0 else ''}.png"
            while (media_root() / rel).exists():
                n += 1
                rel = base / f"v{n}_de{sh.id}_t{max(t_ms, 0)}{'_fim' if t_ms < 0 else ''}.png"
            write_file(str(rel), png)
            m = MediaFile(project_id=p.id, path=str(rel), kind="image", role="shotref", source="upload",
                          status="approved", shot_id=dest.id)
            s.add(m)
            s.commit()
            from app import ref_tokens

            token = ref_tokens.token_for(s, m)[0]
            log.info("mcp: frame do take %s (plano %s) → referência do plano %s", take_id, sh.id, dest.id)
            return {"media_id": m.id, "token": token, "target": target, "status": "aprovado",
                    "next": f"Liga-o no plano {dest.id} com set_field shot:{dest.id}.video_inputs, ex.: "
                            f'{{"items": [{{"ref": "{token}", "on": true, "mode": "start_frame"}}]}}'}
        if kind == "view":
            a_raw, _, v_raw = rest.partition(":")
            asset = s.get(Asset, int(a_raw)) if a_raw.isdigit() else None
            view = s.get(LocationView, int(v_raw)) if v_raw.isdigit() else None
            if asset is None or asset.project_id != p.id or view is None or view.asset_id != asset.id:
                raise ToolError("target view:<asset_id>:<view_id> — essa vista não é desse local deste projeto.")
            base = PurePosixPath(safe_segment(p.prefix), "assets", safe_segment(asset.name), "maquete", f"vista{view.id}")
            n = len(s.exec(select(MediaFile).where(MediaFile.locview_id == view.id, MediaFile.role == "locview")).all()) + 1
            rel = base / f"v{n}_take{take.id}.png"
            while (media_root() / rel).exists():
                n += 1
                rel = base / f"v{n}_take{take.id}.png"
            write_file(str(rel), png)
            m = MediaFile(project_id=p.id, path=str(rel), kind="image", role="locview", source="upload",
                          status="pending", asset_id=asset.id, locview_id=view.id)
            s.add(m)
            s.flush()
            approve_media(s, m)  # a anterior volta a «por decidir»
            s.commit()
            log.info("mcp: frame do take %s → vista %s do local %s", take_id, view.id, asset.id)
            return {"media_id": m.id, "target": target, "status": "aprovado",
                    "note": f"É agora a imagem aprovada da vista {view.id} ({view.zone or 'espaço'} · {view.label})."}
        raise ToolError('target é "shot_ref:<plano>" ou "view:<asset_id>:<view_id>".')


@server.tool(annotations=READ, structured_output=True, title="Valor completo de um campo",
             description="O valor atual completo de um campo de um ecrã (o open_screen corta os textos longos, como os "
                         "prompts e o JSON da encenação stage3d:N): para confirmar uma gravação ou ler as posições.")
def get_field(project_id: int, screen: str, field: str) -> dict[str, Any]:
    with Session(engine) as s:
        p = _project(s, project_id)
        ctx = _context(s, p, screen)
        if field not in ctx.fields:
            raise ToolError(f"O campo «{field}» não existe no ecrã «{screen}». Campos: {', '.join(ctx.fields)}")
        f = ctx.fields[field]
        s.rollback()
        return {"field": field, "label": f.label, "kind": f.kind, "value": f.current, "options": f.options or None}


@server.tool(annotations=WRITE, title="Gravar vários campos",
             description="Grava vários campos do mesmo ecrã numa só operação: valida todos e, se algum não servir, não "
                         "grava nenhum. values = {campo: valor, …} com as chaves exatas de open_screen. Usa isto em vez "
                         "de vários set_field em paralelo.")
def set_fields(project_id: int, screen: str, values: dict[str, Any]) -> str:
    clean = {k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for k, v in (values or {}).items()}
    return _write_fields(project_id, screen, clean)


@server.tool(annotations=READ, structured_output=True, title="Custo do próximo take",
             description="O custo final do próximo take do plano, cotado pelo fornecedor com os mesmos inputs do pedido "
                         "(modelo, resolução, duração e o que está ligado em video_inputs, incluindo o vídeo-guia). Pode "
                         "demorar ~20 s na primeira vez de cada combinação.")
async def quote_shot(project_id: int, shot_id: int) -> dict[str, Any]:
    from app import video_cost

    with Session(engine) as s:
        sh = _shot(s, project_id, shot_id)
        a = video_cost.shot_args(s, sh)
    q = await video_cost.shot_quote(a)
    m = a["model"] or {}
    out = {"shot_id": shot_id, "model": m.get("name"), "resolution": a["res"], "duration_s": a["seconds"],
           "request": "imagem-para-vídeo" if a["frames"] else "referências", "images": len(a["images"]),
           "guide": bool(a["videos"]), "voices": len(a["audios"])}
    if not q:
        return {**out, "credits": None, "text": "sem cotação (o fornecedor não respondeu; tenta outra vez)"}
    return {**out, "credits": q.get("credits"), "usd": q.get("usd"), "without_guide": q.get("base"),
            "guide_cost": (q["credits"] - q["base"]) if q.get("base") is not None and q.get("credits") is not None else None,
            "text": q["text"], "source": q.get("source")}
