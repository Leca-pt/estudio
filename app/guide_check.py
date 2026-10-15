"""O prompt de vídeo contra o vídeo-guia: o modelo segue o TEXTO quando os dois se contradizem, por isso cada prompt
escrito (botão Reescrever, mini chat, Assistente) passa por aqui antes de ficar.

Verifica só o que tem de bater certo (ver «fixo e livre» na guia do vídeo): de frente / de perfil / de costas de cada
personagem do elenco pela ordem do vídeo-guia, os marcadores das personagens e das imagens que seguem, o @Video1, e
que os figurantes não voltam a ser contados nem chamados manequins. O resto é livre."""

import re

from sqlmodel import Session, select

from app.models import Asset, Scene, Shot

# O que o texto diz da orientação de uma personagem em relação à câmara.
FACE_WORDS = {
    "front": r"facing (?:the )?camera|faces? (?:the )?camera|towards? (?:the )?camera|from the front|front view"
             r"|comes? (?:straight )?at (?:the )?camera",
    "back": r"back (?:is )?(?:turned )?to(?:ward)? (?:the )?camera|from behind|from the back|away from (?:the )?camera"
            r"|rear view|(?:his|her|their) back\b|back view|walks? away\b|moves? away\b",
    "profile": r"\bprofile\b|side view|side-on|from the side",
}
FACE_PT = {"front": "de frente", "back": "de costas", "profile": "de perfil"}
NUM = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
WORDS_N = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve".split())}
SECTION = re.compile(r"^\s*(References|Summary|Scene and look|Scene|Action|Dialogue|Sound|Constraints)\s*:", re.I | re.M)


def _sections(text: str) -> dict[str, str]:
    """{"action": …, "constraints": …, "all_but_refs": …}; sem etiquetas, a ação é o texto todo."""
    marks = [(m.start(), m.group(1).lower()) for m in SECTION.finditer(text)]
    if not marks:
        return {"action": text, "constraints": "", "body": text}
    parts = {}
    for i, (pos, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        parts[name] = text[pos:end].split(":", 1)[1]
    body = "\n".join(v for k, v in parts.items() if k not in ("references", "constraints", "sound"))
    return {"action": parts.get("action") or body, "constraints": parts.get("constraints", ""), "body": body}


def _collapse(seq: list[str]) -> list[str]:
    out = []
    for x in seq:
        if x and (not out or out[-1] != x):
            out.append(x)
    return out


def _is_subsequence(small: list[str], big: list[str]) -> bool:
    it = iter(big)
    return all(x in it for x in small)


GENERIC = (r"passers-by|pedestrians?|extras?|people|crowd|figures?|officers?|police|robots?|cars?|man|woman|men|women|"
           r"someone|customers?|drivers?|passer-by")


def _attributions(action: str, names: dict[str, list[str]]) -> dict[str, list[str]]:
    """{código: orientações pela ordem}: cada «de frente / de costas / de perfil» conta para quem foi nomeado logo antes
    (um figurante, um polícia… não conta para ninguém do elenco). As falas entre aspas não contam."""
    text = re.sub(r'"[^"]*"|“[^”]*”', " ", action.lower())
    marks = []
    for code, ns in names.items():
        for n in ns:
            marks += [(m.start(), code) for m in re.finditer(rf"(?<![\w@]){re.escape(n.lower())}(?![\w])", text)]
    marks += [(m.start(), None) for m in re.finditer(rf"\b(?:{GENERIC})\b", text)]
    marks.sort()
    hits = sorted((m.start(), face) for face, pattern in FACE_WORDS.items() for m in re.finditer(pattern, text))
    ends = [m.end() for m in re.finditer(r"[.;!?]|,\s*then\b", text)]
    out: dict[str, list[str]] = {c: [] for c in names}
    for pos, face in hits:
        begin = max([e for e in ends if e <= pos], default=0)  # início da frase
        stop = min([e for e in ends if e > pos], default=len(text))
        before = [(p, c) for p, c in marks if begin <= p < pos]
        after = [(p, c) for p, c in marks if pos < p < stop]
        if before:  # «Ana stands…, facing the camera»
            who = before[-1][1]
        elif after:  # «Seen from behind, Ana walks…»
            who = after[0][1]
        else:  # «He turns, his back to the camera»: quem vinha da frase anterior
            who = next((c for p, c in reversed(marks) if p < pos), None)
        if who is not None:
            out[who].append(face)
    return out


def check(session: Session, shot: Shot, text: str) -> list[dict]:
    """[{"level": "error" | "warn", "text": em português}]: error = o modelo vai fazer outra coisa que não o vídeo-guia."""
    from app import scene3d
    from app.routes.video import shot_model, guide_for_video

    text = text or ""
    if not text.strip():
        return []
    issues: list[dict] = []
    scene = session.get(Scene, shot.scene_id)
    assets = {a.name: a for a in session.exec(select(Asset).where(Asset.project_id == scene.project_id)).all()}
    guide = guide_for_video(session, shot, shot_model(session, shot)).get("media")
    parts = _sections(text)

    if guide is not None and not re.search(r"@video1\b", text, re.I):
        issues.append({"level": "error", "text": "O prompt não refere o vídeo-guia (@Video1)."})
    if guide is None and re.search(r"@video1\b", text, re.I):
        issues.append({"level": "error", "text": "O prompt cita o vídeo-guia (@Video1), que não segue neste pedido "
                                                 "(desligado, ou o pedido é com frame inicial)."})
    from app import ref_tokens as _rt, video_inputs as _vi
    from app.routes.video import request_plan as _rp

    _sent = {m.id for m in _rp(session, shot, shot_model(session, shot))["images"]}
    for _mid, (_g, _m) in _vi.pool(session, shot).items():  # marcadores de imagens que não seguem
        _tok = _rt.token_for(session, _m)[0]
        if _mid not in _sent and _tok and re.search(re.escape(_tok) + r"(?!\w|\.\w)", text):
            issues.append({"level": "error", "text": f"O prompt cita {_tok}, que não segue no pedido (está desligado)."})

    from app import ref_tokens
    from app.routes.video import request_plan, shot_model

    for m in request_plan(session, shot, shot_model(session, shot))["images"]:  # cada imagem que segue tem um papel
        token = ref_tokens.token_for(session, m)[0]
        if token and not re.search(re.escape(token) + r"(?!\w|\.\w)", text):
            issues.append({"level": "error", "text": f"Falta o marcador {token}: essa imagem segue para o modelo sem "
                                                     "ele saber o que é."})

    if guide is not None and shot.scene3d:  # cada elemento do mundo pela cor que tem no vídeo-guia
        cast_colors = scene3d.scene_cast(session, scene)
        shown = {a.get("code") for a, _d in scene3d.visible_actors(session, shot)}
        for code in sorted(c for c in shown if c and assets.get(c) is not None
                           and assets[c].kind not in ("character", "prop")):
            color = scene3d.CAST_EN.get(cast_colors.get(code, ""), "")
            if not color:
                continue
            cor = scene3d.CAST_NAMES.get(cast_colors.get(code, ""), color)
            near = [text[max(0, m.start() - 160):m.end() + 40].lower() for m in re.finditer(rf"@{re.escape(code)}\b", text)]
            if not near:
                issues.append({"level": "error", "text": f"Falta o marcador @{code} (a figura {cor} no vídeo-guia)."})
            elif not any(color.lower() in n for n in near):
                issues.append({"level": "error", "text": f"@{code} sem a cor do vídeo-guia: o modelo não sabe que a "
                                                         f"figura {cor} é {assets[code].label or code}."})

    from app.prompt_context import shot_talk

    plan = request_plan(session, shot, shot_model(session, shot))
    with_voice = {session.get(Asset, v.asset_id).name for v in plan["voices"] if v.asset_id and session.get(Asset, v.asset_id)}
    for code in sorted(set(re.findall(r"@([A-Z][A-Z0-9_]*)\.voice\b", text)) - with_voice):
        issues.append({"level": "error", "text": f"O prompt usa @{code}.voice, mas essa voz não segue com o pedido: "
                                                 "descreve a voz por palavras."})
    by_id = {a.id: a for a in assets.values()}
    for cid in dict.fromkeys(c for c, _line in shot_talk(session, shot)):
        a = by_id.get(cid)
        if a is not None and a.name in with_voice and f"@{a.name}.voice" not in text:
            issues.append({"level": "warn", "text": f"A fala de {a.label or a.name} não usa @{a.name}.voice (a app junta "
                                                    "a voz no envio)."})

    facts = scene3d.moment_facts(session, shot) if shot.scene3d else []
    seen: dict[str, list[str]] = {}
    for m in facts:
        for p in m["people"]:
            if assets.get(p["code"]) is not None and assets[p["code"]].kind == "character":
                seen.setdefault(p["code"], []).append(p["face"])
    main = [c for c in scene3d.in_frame(session, shot) if assets.get(c) is not None and assets[c].kind == "character"]
    sent_codes = {assets_by_id.name for assets_by_id in [session.get(Asset, m.asset_id) for m in
                  _rp(session, shot, shot_model(session, shot))["images"] if m.asset_id] if assets_by_id}
    for code in main:
        if code not in sent_codes:  # a folha não segue (ex.: pedido com frame inicial): o marcador não pode ir
            continue
        if not re.search(rf"@{re.escape(code)}\b", text):
            issues.append({"level": "error", "text": f"Falta o marcador @{code}: o modelo não sabe quem é "
                                                     f"{assets[code].label or code}."})
    for code, a in assets.items():
        if a.kind == "character" and code not in main and re.search(rf"@{re.escape(code)}\b(?!\.voice)", text):
            issues.append({"level": "warn", "text": f"O prompt fala de {a.label or code} (@{code}), que não aparece "
                                                    "neste plano."})

    names = {c: sorted({f"@{c}", assets[c].label or c, (assets[c].label or c).split()[0]}, key=len, reverse=True)
             for c in seen}
    said_by = _attributions(parts["action"], names)
    for code, faces in seen.items():
        expected = _collapse([f for f in faces if f])
        if not expected:
            continue
        a = assets[code]
        said = _collapse(said_by.get(code, []))
        who = a.label or code
        exp_pt = " → ".join(FACE_PT[x] for x in expected)
        wrong = [x for x in said if x not in expected]
        if wrong:
            issues.append({"level": "error", "text": f"{who}: o prompt diz «{FACE_PT[wrong[0]]}», mas no vídeo-guia está "
                                                     f"{exp_pt}."})
        elif said and said[0] != expected[0]:
            issues.append({"level": "error", "text": f"{who}: o prompt começa «{FACE_PT[said[0]]}», mas o vídeo-guia "
                                                     f"começa {FACE_PT[expected[0]]} ({exp_pt})."})
        elif said and not _is_subsequence(said, expected):
            issues.append({"level": "error", "text": f"{who}: a ordem no prompt ({' → '.join(FACE_PT[x] for x in said)}) "
                                                     f"não é a do vídeo-guia ({exp_pt})."})
        elif not said and len(expected) > 1:
            issues.append({"level": "warn", "text": f"{who}: o prompt não diz como está virado; no vídeo-guia está "
                                                    f"{exp_pt}."})

    low = text.lower()
    for m in re.finditer(r"mannequins?|dummies|dummy", low):
        before = low[max(0, m.start() - 12):m.start()]
        if not re.search(r"\bno\b|\bnot\b|\bnever\b|,\s*$", before):
            issues.append({"level": "error", "text": "O prompt chama manequins aos figurantes: o modelo desenha "
                                                     "manequins."})
            break
    for m in re.finditer(rf"\bexactly ({NUM}) (people|persons|passers-by|pedestrians|extras|figures|police\w*|robots?|cars?)\b", low):
        n = WORDS_N.get(m.group(1), m.group(1))
        if m.group(2) in ("people", "persons") and str(n) == str(len(main)):
            continue  # «exactly 1 people» = as personagens principais: certo
        issues.append({"level": "error", "text": f"O prompt conta pessoas ou elementos do mundo («{m.group(0)}»): só se "
                                                 f"contam as personagens principais ({len(main)}); figurantes e elementos "
                                                 "do mundo ficam livres."})
        break
    return issues


def errors(issues: list[dict]) -> list[str]:
    return [i["text"] for i in issues if i["level"] == "error"]


def feedback(issues: list[dict]) -> str:
    """Para devolver ao escritor quando o prompt não passa."""
    return ("O PROMPT ANTERIOR NÃO SEGUE O VÍDEO-GUIA. Corrige isto e devolve o prompt completo:\n"
            + "\n".join(f"- {i['text']}" for i in issues))
