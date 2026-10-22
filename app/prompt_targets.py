"""Alvos de prompt: tudo o que se gera na app com um prompt escrito pelo escritor de prompts (docs/DECISOES.md).

kind / ref:
  folha / <asset>     folha de personagem, adereço ou elemento do mundo
  retrato / <asset>   retrato de uma personagem (a folha aprovada como referência)
  look / <look>       folha de um look do guarda-roupa (a folha e o retrato aprovados como referência; muda a roupa)
  vista / <vista>     imagem de uma vista da maquete de um local (render + referência da zona + folhas da zona)
  video / <plano>     take de um plano (vistas do local nos momentos, folhas do elenco, vídeo-guia)
  chave / <plano>     frame-chave de um plano (previs do 1.º momento + vista do local + folhas do elenco; app/keyframe.py)
  capa / <projeto>    capa do projeto
Cada alvo diz: onde fica o prompt, que imagens seguem para o gerador (por ordem), que imagens o escritor vê só para
si (fotos reais, previs), o contexto, a guia (Configuração → Prompts) e como se pede a geração.
"""

import hashlib
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app import prompt_context as C
from app.models import Asset, CharacterLook, LocationView, MediaFile, Project, PromptVersion, Scene, Shot
from app.options import get_config

KINDS = ("folha", "retrato", "look", "vista", "video", "chave", "capa")
KIND_GUIDE = {"character": "character", "prop": "prop", "element": "element"}


@dataclass
class Target:
    kind: str
    ref: str
    project: Project
    obj: object
    attr: str
    field_key: str
    role: str  # papel do pedido de geração (ImageJob.role)
    what: str  # o que se gera (PT)
    guide: str  # chave da guia em Configuração → Prompts
    use: str = "generate"  # uso para escolher modelos: generate | maquete | video
    target: dict = field(default_factory=dict)  # alvo do pedido (asset_id, locview_id, shot_id)

    @property
    def prompt(self) -> str | None:
        return getattr(self.obj, self.attr)


class TargetError(ValueError):
    pass


def get(session: Session, project: Project, kind: str, ref: str) -> Target:
    """O alvo kind/ref deste projeto (TargetError se não existe ou é de outro projeto)."""
    if kind not in KINDS or not str(ref).isdigit():
        raise TargetError("Alvo desconhecido.")
    rid = int(ref)
    if kind in ("folha", "retrato"):
        a = session.get(Asset, rid)
        if a is None or a.project_id != project.id or a.kind == "location" or (kind == "retrato" and a.kind != "character"):
            raise TargetError("Asset não encontrado.")
        if kind == "retrato":
            return Target(kind, ref, project, a, "portrait_prompt", f"asset:{a.id}.portrait_prompt", "portrait",
                          f"retrato de {a.label or a.name}", "portrait", target={"asset_id": a.id})
        return Target(kind, ref, project, a, "sheet_prompt", f"asset:{a.id}.sheet_prompt", "sheet",
                      f"folha de {a.label or a.name}", KIND_GUIDE.get(a.kind, "character"), target={"asset_id": a.id})
    if kind == "look":
        lk = session.get(CharacterLook, rid)
        a = session.get(Asset, lk.asset_id) if lk else None
        if a is None or a.project_id != project.id:
            raise TargetError("Look não encontrado.")
        return Target(kind, ref, project, lk, "prompt", f"look:{lk.id}.prompt", "look",
                      f"folha do look «{lk.name}» de {a.label or a.name}", "look",
                      target={"asset_id": a.id, "look_id": lk.id})
    if kind == "vista":
        v = session.get(LocationView, rid)
        a = session.get(Asset, v.asset_id) if v else None
        if a is None or a.project_id != project.id:
            raise TargetError("Vista não encontrada.")
        return Target(kind, ref, project, v, "prompt", f"lvprompt:{v.id}", "locview",
                      f"vista «{v.label}» de {a.name}" + (f" · {v.zone}" if v.zone else ""), "view", "maquete",
                      target={"asset_id": a.id, "locview_id": v.id})
    if kind in ("video", "chave"):
        sh = session.get(Shot, rid)
        sc = session.get(Scene, sh.scene_id) if sh else None
        if sc is None or sc.project_id != project.id:
            raise TargetError("Plano não encontrado.")
        if kind == "chave":  # com a cena 3D parte do previs (como as vistas da maquete); sem ela, gera-se do texto
            return Target(kind, ref, project, sh, "key_prompt", f"shot:{sh.id}.key_prompt", "key",
                          f"frame-chave do P{sh.number:02d}", "key", "maquete" if sh.scene3d else "generate",
                          target={"shot_id": sh.id})
        return Target(kind, ref, project, sh, "video_prompt", f"shot:{sh.id}.video_prompt", "clip",
                      f"vídeo do P{sh.number:02d}", "video", "video", target={"shot_id": sh.id})
    if rid != project.id:
        raise TargetError("Projeto errado.")
    return Target(kind, ref, project, project, "cover_prompt", f"project:{project.id}.cover_prompt", "cover",
                  "capa do projeto", "cover")


def zone_targets(session: Session, project: Project, asset: Asset, zone: str | None) -> list[Target]:
    """As vistas de uma zona com render (o botão «Escrever prompts desta zona»)."""
    from app import location3d

    return [get(session, project, "vista", str(v.id)) for v in location3d.location_views(session, asset)
            if (v.zone or "") == (zone or "") and v.render_id]


# ── Imagens ───────────────────────────────────────────────────

def _approved(session: Session, ids: list[int | None]) -> list[MediaFile]:
    from app.routes.production import approved_media

    found = approved_media(session, [i for i in ids if i])
    return [found[i] for i in dict.fromkeys(i for i in ids if i) if i in found]


def inputs(session: Session, t: Target) -> list[MediaFile]:
    """As imagens que seguem para o gerador, pela ordem de envio (o vídeo-guia vai à parte, ver video_guide)."""
    from app import ref_tokens

    if t.kind == "folha":
        from app.asset_refs import sheet_ref_inputs

        return ref_tokens.medias(session, sheet_ref_inputs(session, t.obj))
    if t.kind == "retrato":
        return _approved(session, [t.obj.approved_image_id])
    if t.kind == "look":
        a = session.get(Asset, t.obj.asset_id)
        return _approved(session, [a.approved_image_id, a.portrait_image_id])
    if t.kind == "vista":
        from app import location3d

        asset = session.get(Asset, t.obj.asset_id)
        return ref_tokens.medias(session, location3d.view_inputs(session, asset, t.obj))
    if t.kind == "video":
        from app.routes.video import shot_model, inputs_for

        return inputs_for(session, t.obj, shot_model(session, t.obj))
    if t.kind == "chave":
        from app import keyframe

        return keyframe.inputs(session, t.obj)
    return cover_inputs(session, t.project)


def _look_line(session: Session, asset: Asset, v) -> str | None:
    """Para o escritor: que imagem do sítio segue com o render, e para quê."""
    from app import location3d as L, ref_tokens

    if v.kind != "view":
        return None
    near = L.look_reference(session, asset, v)
    if near:
        tok = ref_tokens.token_for(session, near["media"])[0]
        return (f"{tok}, a vista «{near['view'].label}» desta zona, tirada de uma câmara {near['fit']} com esta: mostra "
                "as mesmas paredes e os mesmos objetos. Usa-a para os materiais, as cores, a luz e o que está em cada "
                "sítio; o enquadramento, a altura da câmara e a lente vêm do render.")
    ref = L.zone_reference(session, asset, v.zone)
    if ref is not None:
        return (f"{ref_tokens.token_for(session, ref)[0]}, a referência da zona (pode estar tirada de outro lado): só os "
                "materiais, as cores e a luz; o que esta vista vê vem do render e da descrição da zona.")
    return None


def cover_inputs(session: Session, project: Project) -> list[MediaFile]:
    """Capa: as folhas das 4 primeiras personagens e a imagem do primeiro local com maquete."""
    from app.asset_refs import location_image

    chars = session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == "character")
                         .order_by(Asset.id)).all()[:4]
    out = _approved(session, [c.approved_image_id for c in chars])
    for loc in session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == "location")
                            .order_by(Asset.id)).all():
        key = location_image(session, loc)
        if key is not None:
            return out + [key]
    return out


def seen_only(session: Session, t: Target) -> list[tuple[MediaFile, str]]:
    """Imagens que só o escritor vê (não vão para o gerador): as fotografias reais escolhidas para a zona e o previs
    dos momentos do plano."""
    if t.kind == "vista":
        from app import location3d

        asset = session.get(Asset, t.obj.asset_id)
        photos = [session.get(MediaFile, i) for i in location3d.zone_refs(asset, t.obj.zone)]
        return [(m, "FOTOGRAFIA REAL escolhida para esta zona (não vai para o gerador: descreve por palavras o aspeto "
                    "que mostra — fachadas, materiais, cores, letreiros — sem copiar a composição)")
                for m in photos if m is not None and m.role == "photo" and m.status == "approved"][:6]
    if t.kind == "chave":
        from app import keyframe

        prev = keyframe.previous(session, t.obj)
        return [(prev, "o frame-chave aprovado do PLANO ANTERIOR desta cena (não vai para o gerador): mantém a mesma luz, "
                       "a mesma roupa e o mesmo sítio, descritos por palavras; o enquadramento e as posições são os do "
                       "previs deste plano")] if prev is not None else []
    if t.kind == "video":
        from app import moments
        from app.media import guide_sheet

        out = []
        guide = video_guide(session, t)
        sheet = guide_sheet(guide.path) if guide is not None else None
        if sheet:  # o próprio vídeo-guia, para o texto descrever o que o modelo vai ver
            out.append((MediaFile(id=-guide.id, project_id=guide.project_id, path=sheet[0], kind="image", role="guide"),
                        "o VÍDEO-GUIA @Video1 em imagens, da esquerda para a direita e de cima para baixo, aos "
                        + ", ".join(f"{x:g}" for x in sheet[1]) + " s. Cada pessoa é uma cápsula lisa com uma viseira escura no "
                        "rosto: viseira visível = de frente para a câmara; sem viseira = de costas. O texto descreve a "
                        "ação assim e nunca ao contrário"))
        return out + [(r["render"], f"previs (render cinzento da cena 3D do plano) no momento {r['n']}, {r['t']:g} s: "
                                    "quem está onde e o enquadramento; cada pessoa é uma cápsula com viseira, o elenco "
                                    "num tom da sua cor (não vai para o modelo)")
                      for r in moments.rows(session, t.obj) if r["render"] is not None]
    return []


def voices(session: Session, t: Target) -> list[MediaFile]:
    """Vídeo: as vozes de referência de quem fala no plano que seguem (nos modelos que aceitam áudio)."""
    if t.kind != "video":
        return []
    from app.routes.video import shot_model, voice_inputs

    return voice_inputs(session, t.obj, shot_model(session, t.obj))


def video_guide(session: Session, t: Target) -> MediaFile | None:
    if t.kind != "video":
        return None
    from app.routes.video import shot_model, guide_for_video

    return guide_for_video(session, t.obj, shot_model(session, t.obj)).get("media")


# ── Contexto e guia ───────────────────────────────────────────

def guide_text(session: Session, t: Target) -> str:
    from app.seeds import DEFAULT_PROMPT_GUIDES

    guides = get_config(session, "prompt_guides", {}) or {}
    return (guides.get(t.guide) or DEFAULT_PROMPT_GUIDES.get(t.guide) or "").strip()


def context(session: Session, t: Target) -> str:
    """O contexto próprio do alvo (a bíblia e as regras vão uma vez por pedido, ver shared_context)."""
    if t.kind in ("folha", "retrato"):
        a = t.obj
        from app.asset_refs import asset_photos, sheet_ref_assets

        refs = sheet_ref_assets(session, a)
        extra = []
        if asset_photos(session, a):
            extra.append(f"Há {len(asset_photos(session, a))} foto(s) de referência carregadas pelo utilizador (vão "
                         "para o gerador): diz no prompt para que servem.")
        if refs:
            extra.append("Outros assets escolhidos como referência (as imagens deles vão para o gerador): "
                         + "; ".join(f"{r['asset'].name} ({r['asset'].label})" for r in refs))
        return C.join(C.asset_block(a), "\n".join(extra))
    if t.kind == "look":
        a = session.get(Asset, t.obj.asset_id)
        others = [f"«{lk.name}»: {lk.description or '—'}" for lk in session.exec(
            select(CharacterLook).where(CharacterLook.asset_id == a.id, CharacterLook.id != t.obj.id)).all()]
        return C.join(C.asset_block(a), C._lines(f"LOOK «{t.obj.name}»", [
            ("Roupa e acessórios deste look (EN; é isto que muda)", t.obj.description),
            ("Outros looks da personagem (não misturar)", "; ".join(others) or None)]))
    if t.kind == "vista":
        from app import location3d as L

        v, asset = t.obj, session.get(Asset, t.obj.asset_id)
        info = L.zone_info(asset.layout3d, v.zone) or {}
        elements = []
        for i in L.design_refs(session, asset, v.zone):
            m = session.get(MediaFile, i)
            el = session.get(Asset, m.asset_id) if m and m.asset_id else None
            if el is not None:
                elements.append(f"{el.name} ({el.label}): {(el.canonical_description or '').strip()[:240]}")
        rows = [("Local", f"{asset.name} ({asset.label})"), ("Descrição canónica do local", asset.canonical_description),
                ("Zona", (v.zone or "o espaço todo") + (" — ao ar livre (sem teto)" if info.get("outdoor") else "")),
                ("Tipo de imagem", "referência larga da zona (a «folha» da zona)" if v.kind == "ref"
                 else "vista à altura dos olhos"),
                ("Nome da vista", v.label),
                ("Como é esta zona (do utilizador, EN)", (asset.zone_notes or {}).get(v.zone or "")),
                ("O que a maquete mostra desta posição (pista)", L.seen_from(asset.layout3d, v)),
                ("Imagem do sítio que segue (Image 2)", _look_line(session, asset, v)),
                ("Elementos do mundo escolhidos para esta zona (só estes; com o design das folhas)",
                 "; ".join(elements) or "nenhum — não acrescentes elementos do mundo que não estejam no «Como é»")]
        return C._lines("VISTA DA MAQUETE", rows)
    if t.kind == "video":
        shot = t.obj
        scene = session.get(Scene, shot.scene_id)
        from app import looks, moments
        from app import scene3d
        from app.routes.video import shot_model
        from app.scene3d import in_frame

        cast = [session.exec(select(Asset).where(Asset.project_id == t.project.id, Asset.name == code)).first()
                for code in in_frame(session, shot)]
        model = shot_model(session, shot) or {}
        moms = "\n".join(f"  - momento {r['n']} ({r['t']:g} s): câmara a {r['cam']['lens']:g} mm"
                         + (f", na zona «{r['zone']}»" if r["zone"] else "")
                         + (f"; vista do local: {r['view'].label} ({r['fit']})" if r["view"] else "; sem vista do local")
                         for r in moments.rows(session, shot))
        guide = video_guide(session, t)
        from app import keyframe, ref_tokens

        key = keyframe.approved(session, shot)
        key = key if key is not None and key in inputs(session, t) else None
        key_tok = ref_tokens.token_for(session, key)[0] if key is not None else ""
        key_t = keyframe.made_at(session, key) if key is not None else 0.0
        if key is None:
            key_text = ""
        elif key_t < 0.05:
            key_text = (f"FRAME-CHAVE: segue {key_tok}, a imagem do início deste plano já com o sítio, a luz, a roupa de "
                        "cada personagem e onde cada uma está e para onde olha. Nas References, numa frase: \""
                        f"{key_tok} is the opening frame of this shot: start from exactly this composition — the same "
                        "place, light, clothes and the same positions of everyone\". As posições iniciais vêm dela: não "
                        "as descrevas em pormenor; descreve o que acontece a partir daí.")
        else:
            key_text = (f"FRAME-CHAVE: segue {key_tok}, a imagem deste plano aos {key_t:g} s (NÃO é o início): o sítio, a "
                        "luz, a roupa de cada personagem e onde cada uma está e para onde olha nesse instante. Nas "
                        f"References, numa frase: \"{key_tok} shows this shot at {key_t:g}s: by then everyone is exactly "
                        "in these positions, in this place and light, with these clothes\". Descreve o que acontece "
                        "até aí (quem entra, por onde, para onde vai) e depois; não digas que o plano começa nesta imagem.")
        return C.join(C.scene_block(session, scene), C.shot_block(session, shot),
                      "MOMENTOS DA CÂMARA (cena 3D)\n" + moms if moms else "",
                      _frame_block(session, shot),
                      scene3d.variation_note(session, scene),
                      key_text,
                      "VÍDEO-GUIA: segue como @Video1 (previs cinzento com a câmara e as posições exatas)." if guide
                      else (f"SEM VÍDEO-GUIA: descreve por palavras o movimento de câmara (de onde parte, para onde vai, "
                            f"a lente) e o que cada pessoa faz a partir das posições do frame-chave {key_tok}; não refiras "
                            "@Video1.") if key is not None
                      else ("SEM VÍDEO-GUIA (este modelo não o aceita ou ainda não está aprovado): descreve por palavras "
                            "o movimento de câmara (de onde parte, para onde vai, a lente) e onde está cada pessoa em relação "
                            "à câmara e ao cenário, pela ENCENAÇÃO acima; não refiras @Video1."),
                      *[C.join(C.asset_block(a, f"ELENCO {a.name}"), looks.context_line(session, scene, a))
                        for a in cast if a],
                      f"MODELO DE VÍDEO: {model.get('name') or '—'}; durações {model.get('durations_s') or '—'}; "
                      f"vídeo-guia: {'aceita' if model.get('video_refs') else 'não aceita'}; vozes de referência: "
                      f"{'até %d' % (model.get('max_audios') or 3) if model.get('audio_refs') else 'não aceita'}; imagens: "
                      + (f"até {model.get('max_images') or 9} como referências" if model.get('refs') else "só a imagem inicial (e final)"))
    if t.kind == "chave":
        return _key_context(session, t)
    chars = session.exec(select(Asset).where(Asset.project_id == t.project.id, Asset.kind == "character")
                         .order_by(Asset.id)).all()[:4]
    return C.join(*[C.asset_block(a) for a in chars])


def _key_context(session: Session, t: Target) -> str:
    """Frame-chave: o plano, o primeiro momento (quem está onde, de frente ou de costas), a cor da cápsula de cada
    personagem no previs e o aspeto de cada uma (folha e look da cena)."""
    from app import keyframe, looks, moments, scene3d
    from app.routes.production import scene_assets

    shot = t.obj
    scene = session.get(Scene, shot.scene_id)
    if shot.scene3d:
        codes = keyframe.in_shot(session, shot)  # só quem a câmara vê nesse instante
        cast = [a for a in (session.exec(select(Asset).where(Asset.project_id == t.project.id, Asset.name == c)).first()
                            for c in codes) if a is not None and a.kind in ("character", "prop", "element")]
        n_extras, _extras = scene3d.extras_note(session, shot)
        r = keyframe.moment(session, shot)
        notes = scene3d.moment_notes(session, shot)
        first = notes[r["n"] - 1] if r and len(notes) >= r["n"] else None
        gone = keyframe.absent(session, shot)
        start = (f"PREVIS: o frame-chave é o instante do momento {r['n']} ({r['t']:g} s do plano) visto pela câmara do "
                 f"plano ({r['cam']['lens']:g} mm). O render desse instante é a primeira imagem que segue: dá o "
                 "enquadramento, a altura e o ângulo da câmara, onde está cada pessoa e para onde olha (viseira escura "
                 "= o rosto) e onde está cada objeto." if r else "")
        far = ""
        if keyframe.view_far(session, shot):
            loc = moments.location(session, shot)
            note = ((loc.zone_notes or {}).get(r.get("zone") or "") if loc else "") or (loc.canonical_description if loc else "")
            far = (f"SEM IMAGEM DO LOCAL: a vista do local deste momento foi tirada de outro ângulo (parecença {r['fit']}) "
                   "e não segue (os modelos copiavam-lhe o enquadramento). Descreve no prompt, em poucas palavras, os "
                   "materiais, as cores e a luz do sítio a partir de COMO É ESTA ZONA; o enquadramento, a altura da "
                   "câmara, a lente e a profundidade do espaço vêm só do previs.\nCOMO É ESTA ZONA: " + (note or "—"))
        rows = [start, far,
                "NO PREVIS, QUEM É QUEM: " + "; ".join(keyframe.cast_colours(session, shot)) if cast else "",
                f"NESSE INSTANTE (calculado da encenação e da câmara): {first}" if first else "",
                ("FORA DESTA IMAGEM (aparecem noutra altura do plano; NÃO os ponhas na imagem nem os nomeies no prompt): "
                 + "; ".join(gone)) if gone else "",
                "" if cast else ("NINGUÉM DO ELENCO NESTE INSTANTE: a imagem mostra só o sítio (e figurantes, se houver)."),
                (f"FIGURANTES: {n_extras} — as figuras cinzento-claro do previs são pessoas comuns, com roupa do sítio e "
                 "da época, variada e com cor (nunca manequins nem estátuas)." if n_extras else ""),
                scene3d.variation_note(session, scene)]
    else:
        cast = [a for a in scene_assets(session, scene) if a.kind == "character"]
        rows = ["SEM CENA 3D: compõe a imagem pela descrição do plano (tipo de plano, ângulo, quem está onde, de frente ou "
                "de costas) e pela ação da cena no início do plano."]
    return C.join(C.scene_block(session, scene), C.shot_block(session, shot), "\n".join(x for x in rows if x),
                  *[C.join(C.asset_block(a, f"ELENCO {a.name}"), looks.context_line(session, scene, a)) for a in cast])


def t_project(session: Session, shot: Shot) -> int:
    return session.get(Scene, shot.scene_id).project_id


def _frame_block(session: Session, shot: Shot) -> str:
    """O troço da encenação da cena que o plano mostra e onde está cada pessoa em relação à câmara."""
    from app import scene3d

    notes = scene3d.frame_notes(session, shot)
    start, dur = scene3d.shot_window(shot)
    n_extras, extras = scene3d.extras_note(session, shot)
    n_cast = sum(1 for c in scene3d.in_frame(session, shot)
                 if (a := session.exec(select(Asset).where(Asset.project_id == t_project(session, shot),
                                                           Asset.name == c)).first()) and a.kind == "character")
    count = (f"CONTAGEM: {n_cast} personagem(ns) do elenco em plano"
             + (f" + {n_extras} figurante(s) (pessoas genéricas, sem folha)" if n_extras else "")
             + ". Nas Constraints: " + (f"\"exactly {n_cast} main character(s), each appearing once\"" if n_cast else
                                       "\"none of the main characters appears\"")
             + (", e que os figurantes são pessoas genéricas que seguem as posições e os movimentos do vídeo-guia (não "
                "os contes como personagens nem lhes dês caras do elenco)" if n_extras else ""))
    per_moment = scene3d.moment_notes(session, shot)
    return (f"ENCENAÇÃO: o plano mostra o troço de {start:g} s a {start + dur:g} s da encenação da cena (as mesmas posições "
            "de todos os planos da cena)." + ("\nQUEM A CÂMARA VÊ (a meio do plano): " + "; ".join(notes) if notes else "")
            + ("\nO QUE O VÍDEO-GUIA MOSTRA EM CADA MOMENTO (calculado da encenação e da câmara; descreve a ação assim — "
               "de frente ou de costas, o lado do ecrã e para onde anda — e NUNCA ao contrário: se o texto contradiz o "
               "vídeo-guia, o modelo segue o texto):\n" + "\n".join("- " + x for x in per_moment) if per_moment else "")
            + ("\nFIGURANTES, ELEMENTOS DO MUNDO E VEÍCULOS EM PLANO: " + extras + ". No prompt NÃO os descrevas um a "
               "um: uma frase por tipo chega (ex.: \"The light-grey figures in @Video1 are background extras: "
               "ordinary, random passers-by dressed for the place and era in varied, colourful everyday clothes, "
               "following exactly the positions and movements of @Video1; the grey boxes are ordinary cars.\"; nunca lhes chames mannequins: o modelo "
               "desenha manequins); os elementos do mundo pela cor e pelo marcador, numa frase cada (\"The lime figure "
               "in @Video1 is a police robot exactly as in @X\"; a folha deles segue)." if extras else "")
            + "\n" + count)


def shared_context(session: Session, project: Project) -> str:
    return C.join(C.project_block(session, project), C.rules_block(session))


def _video_format(session: Session, t: Target) -> str:
    """O formato do perfil do modelo do plano (secções, tempos, falas): se muda, o prompt fica desatualizado."""
    if t.kind != "video":
        return ""
    from app import model_profiles
    from app.routes.video import shot_model

    return model_profiles.video_format((shot_model(session, t.obj) or {}).get("name"))


BASIS_LABELS = ("o guia de prompts (Configuração)", "a bíblia ou as regras do projeto",
                "o que o prompt descreve (o plano, a cena e os planos à volta, o asset…)", "o modelo de vídeo do plano",
                "as imagens que seguem no pedido", "as imagens que o escritor vê", "as vozes")


def _basis_items(session: Session, t: Target) -> list[str]:
    return [guide_text(session, t), shared_context(session, t.project), context(session, t),
            _video_format(session, t),
            ",".join(str(m.id) for m in inputs(session, t)),
            ",".join(str(m.id) for m, _l in seen_only(session, t)),
            ",".join(str(m.id) for m in voices(session, t))]


def basis(session: Session, t: Target) -> str:
    """Impressão digital do que o escritor usa: quando muda, o prompt escrito fica desatualizado."""
    return hashlib.sha1("\n".join(_basis_items(session, t)).encode()).hexdigest()[:12]


def basis_parts(session: Session, t: Target) -> dict[str, str]:
    """A mesma impressão, parte a parte (guardada com cada versão escrita, para dizer o que mudou)."""
    return {label: hashlib.sha1(x.encode()).hexdigest()[:10] for label, x in zip(BASIS_LABELS, _basis_items(session, t))}


def stale_reasons(session: Session, t: Target) -> list[str]:
    """O que mudou desde que o escritor escreveu o prompt (vazio se não está desatualizado)."""
    if state(session, t) != "stale":
        return []
    last = session.exec(select(PromptVersion).where(PromptVersion.project_id == t.project.id,
                                                    PromptVersion.field_key == t.field_key)
                        .order_by(PromptVersion.id.desc())).first()
    if last is None or not last.parts:
        return ["os dados de que o prompt depende (versão escrita antes de a app guardar o pormenor)"]
    now = basis_parts(session, t)
    return [label for label, h in now.items() if last.parts.get(label) != h] or ["os dados de que o prompt depende"]


def keep_written(session: Session, t: Target) -> None:
    """«Gerar com este texto»: o prompt do escritor, tal como está, passa a valer para os dados atuais."""
    from app.prompt_versions import record

    record(session, t.project.id, t.field_key, t.prompt or "", "assistente", basis=basis(session, t),
           parts=basis_parts(session, t))


def state(session: Session, t: Target) -> str:
    """"missing" (sem prompt), "manual" (editado à mão), "assistant" (escrito e atual) ou "stale" (escrito, mas os
    dados mudaram depois)."""
    current = t.prompt
    if not (current or "").strip():
        return "missing"
    last = session.exec(select(PromptVersion).where(PromptVersion.project_id == t.project.id,
                                                    PromptVersion.field_key == t.field_key)
                        .order_by(PromptVersion.id.desc())).first()
    if last is None or last.source != "assistente" or last.text != current or last.basis is None:
        return "manual"  # editado à mão, ou alterado no chat do Assistente: vai tal como está
    return "assistant" if last.basis == basis(session, t) else "stale"


def needs_writing(session: Session, t: Target) -> bool:
    return state(session, t) in ("missing", "stale")


# ── Gerar ─────────────────────────────────────────────────────

def enqueue(session: Session, t: Target, gen: dict | None) -> tuple[bool, str]:
    """Pede a geração com o prompt atual. gen: {"gen": "<modelo de imagem>"} ou, no vídeo, {"audio": bool}.
    Devolve (pedido feito, mensagem para o utilizador)."""
    gen = gen or {}
    if t.kind == "video":
        from app.routes.video import queue_video

        return queue_video(session, t.project, t.obj, t.prompt or "", bool(gen.get("audio", True)))
    from app.routes.gpt import default_generator, parse_generator, queue_image

    if t.kind == "vista" and not t.obj.render_id:
        return False, "Ainda não há render desta vista: abre a maquete do local para o tirar."
    params = None
    if t.kind == "chave":
        from app import keyframe, moments, outdated

        problem = keyframe.missing(session, t.obj) or outdated.check(session, t.obj, "chave")
        if problem:
            return False, problem
        if t.obj.scene3d:  # parte do previs (a 1.ª imagem é a geometria); a versão da cena e o momento ficam guardados
            r = keyframe.moment(session, t.obj)
            params = {"base": True, "hash": moments.render_hash(session, t.obj), "moment": moments.moment_key(r),
                      "n": r["n"], "t": r["t"]}
    if t.kind == "retrato" and not t.obj.approved_image_id:
        return False, "Aprova primeiro a folha: o retrato parte dela."
    if t.kind == "look" and not getattr(session.get(Asset, t.obj.asset_id), "approved_image_id", None):
        return False, "Aprova primeiro a folha da personagem: o look parte dela."
    provider, model = parse_generator(gen.get("gen") or default_generator(session, use=t.use, project=t.project))
    return queue_image(session, t.project, t.role, t.prompt or "", [m.id for m in inputs(session, t)], provider,
                       model, use=t.use, resolution=gen.get("res") or None, params=params, **t.target)


def writer_status(session: Session, t: Target):
    from app import prompt_writer

    return prompt_writer.task_for(session, t.project.id, t.kind, t.ref)
