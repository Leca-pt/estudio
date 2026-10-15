"""O que segue no pedido de vídeo de um plano, escolhido item a item: o vídeo-guia, cada imagem (como referência ou
como frame inicial/final) e cada voz.

Guardado no plano:
- Shot.video_refs: as imagens ligadas no modo referências, pela ordem (None = a escolha automática da app);
- Shot.video_inputs: {"guide": false se o vídeo-guia está desligado, "voices_off": [ids], "modes": {"<id>":
  "start_frame" | "end_frame"}}. Sem nada = tudo como a app escolhe (tudo ligado).

Com uma imagem como frame inicial (e final), o pedido é do tipo imagem-para-vídeo: seguem só as frames e o prompt
(os modelos recebem as referências, o vídeo e as vozes noutro tipo de pedido, que não se mistura com este).
"""

import json

from sqlmodel import Session

from app.models import MediaFile, Shot
from app.memo import memo

MODES = ("ref", "start_frame", "end_frame")
MODE_LABELS = {"ref": "Referência", "start_frame": "Frame inicial", "end_frame": "Frame final"}


def config(shot: Shot) -> dict:
    raw = shot.video_inputs or {}
    modes = {int(k): v for k, v in (raw.get("modes") or {}).items() if str(k).isdigit() and v in ("start_frame", "end_frame")}
    return {"guide": raw.get("guide") is not False, "voices_off": {int(x) for x in raw.get("voices_off") or [] if str(x).isdigit()},
            "modes": modes}


def accepts(model: dict | None) -> dict:
    """O que o modelo aceita: referências (e quantas), vídeo, vozes, frame inicial e final."""
    from app.routes.video import limits

    lim = limits(model)
    return {"refs": bool((model or {}).get("refs")), "max_images": lim["images"], "video": lim["videos"] > 0,
            "audio": lim["audios"] > 0, "max_audios": lim["audios"], "start_frame": model is not None,
            "end_frame": model is not None and model.get("end_frame", True) is not False}


@memo
def pool(session: Session, shot: Shot) -> dict[int, tuple[str, MediaFile]]:
    """Todas as imagens que podem seguir: {id: (grupo, imagem)}, pela ordem dos grupos."""
    from app.routes.video import video_candidates

    out: dict[int, tuple[str, MediaFile]] = {}
    for group, items in video_candidates(session, shot):
        for m in items:
            out.setdefault(m.id, (group, m))
    return out


@memo
def frames(session: Session, shot: Shot, model: dict | None) -> list[MediaFile]:
    """As frames escolhidas à mão ([inicial, final?]), ou [] se o pedido é por referências."""
    c, ok = config(shot), accepts(model)
    p = pool(session, shot)
    start = next((p[i][1] for i, m in c["modes"].items() if m == "start_frame" and i in p), None)
    end = next((p[i][1] for i, m in c["modes"].items() if m == "end_frame" and i in p), None) if ok["end_frame"] else None
    if start is None:
        return []
    return [start] + ([end] if end is not None else [])


@memo
def items(session: Session, shot: Shot, model: dict | None) -> dict:
    """Para a página e o conector: cada item com o estado (ligado/desligado), o modo e o motivo quando não segue."""
    from app import ref_tokens
    from app.routes.video import guide_for_video, limits, request_plan, send_refs, speakers

    c, ok = config(shot), accepts(model)
    plan = request_plan(session, shot, model)
    sent = {m.id for m in plan["images"]}
    frame_mode = plan["mode"] == "frames"
    chosen = shot.video_refs is not None
    # as ligadas como referência (a escolha à mão ou a automática), mesmo quando o pedido é com frame inicial
    selected = {m.id for m in send_refs(session, shot, limits(model)["images"])} | set(c["modes"])
    images = []
    for mid, (group, m) in pool(session, shot).items():
        token, desc = ref_tokens.token_for(session, m)
        mode = c["modes"].get(mid, "ref")
        why = "com frame inicial seguem só as frames" if frame_mode and mid in selected and mid not in sent else None
        images.append({"id": mid, "media": m, "token": token, "desc": desc, "group": group, "mode": mode,
                       "on": mid in selected, "sent": mid in sent, "why": why})
    g = guide_for_video(session, shot, model)
    guide_media = g.get("media")
    from app.models import MediaFile as MF

    guide_file = session.get(MF, shot.guide_video_id) if shot.guide_video_id else None
    guide = {"media": guide_file, "on": plan["guide"] is not None, "wanted": c["guide"],
             "why": None if plan["guide"] is not None else g.get("warn")}
    voices = []
    from app.routes.production import approved_media

    found = approved_media(session, [a.voice_ref_id for a in speakers(session, shot)])
    going = {m.id for m in plan["voices"]}
    dropped = {a.id: why for a, why in plan["dropped"]}
    for a in speakers(session, shot):
        m = found.get(a.voice_ref_id) if a.voice_ref_id else None
        if m is None:
            continue
        why = ("desligada" if m.id in c["voices_off"] else "com frame inicial não seguem vozes" if frame_mode
               else dropped.get(a.id))
        voices.append({"id": m.id, "media": m, "asset": a, "token": f"@{a.name}.voice", "on": m.id in going,
                       "why": None if m.id in going else why})
    groups: dict[str, list] = {}
    for it in images:
        groups.setdefault(it["group"], []).append(it)
    return {"guide": guide, "images": images, "groups": list(groups.items()), "voices": voices, "mode": plan["mode"], "accepts": ok,
            "custom": chosen or bool(shot.video_inputs), "guide_ok": guide_media is not None or plan["guide"] is not None}


def as_json(session: Session, shot: Shot, model: dict | None) -> str:
    """O valor atual do campo shot:N.video_inputs (o mesmo formato que se grava)."""
    st = items(session, shot, model)
    out = [{"ref": i["token"], "on": i["on"], "mode": i["mode"]} for i in st["images"]]
    out += [{"ref": v["token"], "on": v["on"]} for v in st["voices"]]
    return json.dumps({"guide": st["guide"]["on"], "items": out}, ensure_ascii=False)


def parse(session: Session, shot: Shot, model: dict | None, value) -> tuple[list[int] | None, dict | None, list[str]]:
    """Valida o JSON {"guide", "items": [{"ref", "on", "mode"}]} (os itens que faltam ficam como estão). Devolve
    (video_refs, video_inputs, notas). ValueError com o motivo e as opções válidas."""
    from app import ref_tokens
    from app.routes.video import auto_refs, speakers
    from app.routes.production import approved_media

    try:
        data = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError as exc:
        raise ValueError('JSON inválido. Formato: {"guide": true, "items": [{"ref": "@CODIGO", "on": true, "mode": "ref"}]}') from exc
    if not isinstance(data, dict):
        raise ValueError('Formato: {"guide": true, "items": [{"ref": "@CODIGO", "on": true, "mode": "ref"}]}')
    ok, c, p = accepts(model), config(shot), pool(session, shot)
    by_token = {ref_tokens.token_for(session, m)[0]: mid for mid, (_g, m) in p.items()}
    found = approved_media(session, [a.voice_ref_id for a in speakers(session, shot)])
    voice_tokens = {f"@{a.name}.voice": found[a.voice_ref_id].id for a in speakers(session, shot)
                    if a.voice_ref_id and a.voice_ref_id in found}
    current = items(session, shot, model)
    on = {i["id"]: i["on"] for i in current["images"]}
    order = [i["id"] for i in current["images"]]
    modes = dict(c["modes"])
    voices_off = set(c["voices_off"])
    allowed_modes = ["ref"] + (["start_frame"] if ok["start_frame"] else []) + (["end_frame"] if ok["end_frame"] else [])
    rows = data.get("items") or []
    if not isinstance(rows, list):
        raise ValueError('"items" tem de ser uma lista.')
    given = []
    for it in rows:
        if not isinstance(it, dict) or not str(it.get("ref") or "").strip():
            raise ValueError('Cada item: {"ref": "<marcador>", "on": true|false, "mode": "ref"|"start_frame"|"end_frame"}.')
        ref = str(it["ref"]).strip()
        ref = ref if ref.startswith("@") else "@" + ref
        if ref in voice_tokens:
            if it.get("mode") not in (None, "ref"):
                raise ValueError(f"{ref} é uma voz: não tem modo.")
            (voices_off.discard if it.get("on", True) else voices_off.add)(voice_tokens[ref])
            continue
        if ref not in by_token:
            raise ValueError(f"O marcador {ref} não existe neste plano. Marcadores válidos: "
                             + ", ".join(list(by_token) + list(voice_tokens)) + ".")
        mid = by_token[ref]
        mode = it.get("mode") or modes.get(mid, "ref")
        if mode not in allowed_modes:
            raise ValueError(f"O modo «{mode}» não serve para o {(model or {}).get('name') or 'modelo'}. Modos válidos: "
                             + ", ".join(allowed_modes) + ".")
        on[mid] = bool(it.get("on", True))
        if mode == "ref":
            modes.pop(mid, None)
        else:
            if not on[mid]:
                raise ValueError(f"{ref} está como {mode} mas desligado: liga-o ou põe-no como ref.")
            modes[mid] = mode
        given.append(mid)
    starts = [m for m, v in modes.items() if v == "start_frame"]
    ends = [m for m, v in modes.items() if v == "end_frame"]
    if len(starts) > 1 or len(ends) > 1:
        raise ValueError("Só pode haver uma frame inicial e uma frame final.")
    if ends and not starts:
        raise ValueError("A frame final precisa de uma frame inicial.")
    guide = data.get("guide", c["guide"])
    if not isinstance(guide, bool):
        raise ValueError('"guide" é true ou false.')
    notes = []
    if starts:
        if guide:
            notes.append("com frame inicial o vídeo-guia não segue (o pedido é de imagem-para-vídeo)")
        others = [m for m in on if on[m] and m not in starts + ends]
        if others:
            notes.append(f"com frame inicial seguem só as frames: {len(others)} imagem(ns) ligada(s) como referência "
                         "não seguem")
    else:
        refs_on = [m for m in (given + [x for x in order if x not in given]) if on.get(m)]
        if not ok["refs"] and refs_on:
            raise ValueError(f"O {(model or {}).get('name') or 'modelo'} não aceita imagens de referência: usa start_frame "
                             "(e end_frame) para a imagem inicial (e final).")
        if len(refs_on) > ok["max_images"]:
            raise ValueError(f"Demasiadas imagens: {len(refs_on)} ligadas, o {(model or {}).get('name') or 'modelo'} aceita "
                             f"até {ok['max_images']}. Desliga algumas.")
        if guide and not ok["video"]:
            raise ValueError(f"O {(model or {}).get('name') or 'modelo'} não aceita vídeo de referência: \"guide\": false.")
    refs_list = [m for m in (given + [x for x in order if x not in given]) if on.get(m) and m not in starts + ends]
    auto = [m.id for m in auto_refs(session, shot, ok["max_images"])]
    video_refs = None if refs_list == auto else refs_list
    cfg = {}
    if not guide:
        cfg["guide"] = False
    if voices_off:
        cfg["voices_off"] = sorted(voices_off)
    if modes:
        cfg["modes"] = {str(k): v for k, v in modes.items()}
    return video_refs, (cfg or None), notes
