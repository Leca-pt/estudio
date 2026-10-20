"""Contexto do Assistente por ecrã: o que ele vê (resumo) e os campos que pode propor."""

import json
import re
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.assistant.fields import EN, FieldSpec, spec
from app.models import (
    ASSET_KINDS, CHECK_CATEGORIES, Act, Asset, AssetSceneLink, Episode, Project, Scene, SceneLine, Season, Shot,
    ShotCheck,
)
from app.options import get_options, project_type_labels
from app.stages import STAGE_NAMES


@dataclass
class Context:
    key: str
    label: str
    stage: int
    summary: str
    fields: dict[str, FieldSpec] = field(default_factory=dict)
    scene_ids: list[int] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)  # pedidos rápidos
    images: list = field(default_factory=list)  # [(caminho, rótulo)] imagens aprovadas que o Assistente vê

    def add(self, fs: FieldSpec) -> None:
        self.fields[fs.key] = fs

    def use_effective_prompts(self, session: Session, project: Project) -> None:
        """Os prompts são guardados tal como estão (escritos pelo escritor de prompts ou editados): nada a montar."""


def mentioned(session: Session, project: Project, message: str) -> list[Asset]:
    """Assets do projeto mencionados com @CÓDIGO numa mensagem."""
    import re

    codes = dict.fromkeys(c.upper() for c in re.findall(r"@([A-Za-z0-9_]+)", message or ""))
    if not codes:
        return []
    rows = session.exec(select(Asset).where(Asset.project_id == project.id, Asset.name.in_(list(codes)))).all()
    by_code = {a.name: a for a in rows}
    return [by_code[c] for c in codes if c in by_code]


def add_mentions(session: Session, ctx: "Context", assets: list[Asset], max_images: int = 10) -> None:
    """Junta ao contexto as fichas e as imagens aprovadas dos assets mencionados com @."""
    if not assets:
        return
    lines = []
    for a in assets:
        lines.append(f"- {a.name} ({ASSET_KINDS[a.kind]}, “{a.label}”): {_v(a.canonical_description)}"
                     + (f" · features-chave: {a.key_features}" if a.key_features else "")
                     + (f" · {'perfil' if a.kind == 'character' else 'notas'}: {_short(a.profile)}" if a.profile else ""))
    ctx.summary += "\n\nASSETS MENCIONADOS PELO UTILIZADOR (@) — usa-os como referência de consistência:\n" + "\n".join(lines)
    seen = {str(p) for p, _l in ctx.images}
    for a in assets:
        for path, label in asset_images(session, a, approved_only=True):
            if str(path) not in seen and len(ctx.images) < max_images:
                ctx.images.append((path, f"{label} (mencionado)"))
                seen.add(str(path))


def asset_images(session: Session, asset: Asset, limit: int = 8, approved_only: bool = False) -> list:
    """Imagens aprovadas do asset (folha e retrato; num local, as referências aprovadas das zonas) e das referências
    da folha."""
    from app.media import media_root
    from app.models import LocationView
    from app.routes.production import approved_media

    wanted = [(asset.approved_image_id, "imagem geral antiga" if asset.kind == "location" else "folha aprovada")]
    if asset.kind == "character":
        wanted.append((asset.portrait_image_id, "retrato aprovado"))
    if asset.kind == "location":
        refs = session.exec(select(LocationView).where(LocationView.asset_id == asset.id, LocationView.kind == "ref",
                                                       LocationView.image_id.is_not(None))).all()
        wanted += [(v.image_id, f"referência aprovada da zona «{v.zone or 'espaço'}»") for v in refs[:4]]
    for ref_id in asset.sheet_refs or []:
        ref = session.get(Asset, ref_id)
        if ref:
            wanted.append((ref.approved_image_id, f"{ref.name} (referência da folha)"))
    from app.models import MediaFile

    photos = session.exec(select(MediaFile).where(MediaFile.asset_id == asset.id, MediaFile.role == "photo",
                                                  MediaFile.status == "approved").order_by(MediaFile.id)).all()
    wanted += [(m.id, f"foto de referência {n} (sítio/objeto real carregado pelo utilizador)") for n, m in enumerate(photos, 1)]
    found = approved_media(session, [i for i, _ in wanted])
    out = [(media_root() / found[i].path, f"{asset.name if 'referência da folha' not in label else ''} {label}".strip())
           for i, label in wanted if i in found]
    if approved_only:
        return out[:limit]
    # Resultados por decidir mais recentes (para o Assistente poder dizer o que está mal numa geração).
    pending = session.exec(select(MediaFile).where(MediaFile.asset_id == asset.id, MediaFile.status == "pending",
                                                   MediaFile.kind == "image").order_by(MediaFile.id.desc())).all()
    seen = set()
    names = {"sheet": "folha", "portrait": "retrato"}
    for m in pending:
        if m.role in seen or m.role not in names:
            continue
        seen.add(m.role)
        out.append((media_root() / m.path, f"{asset.name} {names[m.role]} — resultado por decidir ({m.path.rsplit('/', 1)[-1]})"))
    return out[:limit]


def _v(value) -> str:
    return "—" if value in (None, "") else str(value)


def project_summary(session: Session, project: Project) -> str:
    types = project_type_labels(session)
    rows = [
        f"Projeto: {project.name} (prefixo {project.prefix}, tipo {types.get(project.type, project.type)})",
        f"Género: {_v(project.genre)} · Tom: {_v(project.tone)} · Público: {_v(project.audience)} · "
        f"Língua dos diálogos: {_v(project.dialogue_language)}",
        f"Estilo de render: {_v(project.render_style)} · Proporções: {_v(project.proportions)} · "
        f"Paleta e luz: {_v(project.palette_light)} · Aspect ratio: {_v(project.aspect_ratio)}",
        f"Bloco de estilo (EN): {_v(project.style_block)}",
        f"Exclusões (EN): {_v(project.exclusions)}",
        f"Regras dos prompts: {_v(project.project_rules)}",
        f"O MUNDO (o que é verdade neste mundo; PT, não entra nos prompts):\n{_v(project.world)}",
        f"O mundo nos prompts (EN; o escritor de prompts usa-o nos prompts de ambiente: vistas dos locais, vídeo, capa; "
        f"edita-se na etapa «Mundo», por baixo de «O mundo»): {_v(project.world_prompt)}",
        f"Premissa (a ideia de partida, PT; não entra nos prompts): {_v(project.premise)}",
        f"Logline: {_v(project.logline)}",
        f"Sinopse: {_v(project.synopsis)}",
    ]
    parent = session.get(Project, project.parent_id) if project.parent_id else None
    if parent is not None:  # ex.: um spot da série: a história e as personagens da série servem de base
        rows.append(f"FAZ PARTE DE: {parent.name} (prefixo {parent.prefix}, {types.get(parent.type, parent.type)}). "
                    f"Logline: {_v(parent.logline)} · Sinopse: {_v(parent.synopsis)} · Premissa: {_v(parent.premise)}. "
                    "Os assets dela importam-se na etapa dos assets («Importar de outro projeto»); os importados são "
                    "cópias com o prefixo deste projeto.")
    return "\n".join(rows)


def assets_summary(session: Session, project: Project) -> str:
    assets = session.exec(select(Asset).where(Asset.project_id == project.id).order_by(Asset.kind, Asset.name)).all()
    if not assets:
        return "Assets: nenhum."
    return "Assets do projeto:\n" + "\n".join(
        f"- {a.name} ({ASSET_KINDS[a.kind]}, “{a.label}”{', grupo ' + a.group_name if a.group_name else ''}): {_v(a.canonical_description)}"
        + (f" · features-chave: {a.key_features}" if a.key_features else "")
        + (f"\n    {'Perfil' if a.kind == 'character' else 'Notas'} (PT, não entra nos prompts): {_short(a.profile)}" if a.profile else "")
        for a in assets
    )


def _short(text: str, limit: int = 700) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def scene_summary(session: Session, scene: Scene, with_lines: bool = True) -> str:
    location = session.get(Asset, scene.location_id) if scene.location_id else None
    linked = session.exec(
        select(Asset).join(AssetSceneLink, AssetSceneLink.asset_id == Asset.id).where(AssetSceneLink.scene_id == scene.id)
    ).all()
    rows = [f"Cena C{scene.number:02d} “{_v(scene.title)}” · local {location.name if location else '—'} · "
            f"hora do dia {_v(scene.time_of_day)}",
            f"Descrição da cena: {_v(scene.description)}",
            "Quem e o que aparece na cena (assets marcados): " + (", ".join(a.name for a in linked) or "nenhum")]
    if scene.blocking:
        rows.append(f"Notas de blocking: {scene.blocking}")
    if with_lines:
        from app.shot_script import script_coverage

        coverage = script_coverage(session, scene)
        if coverage:
            rows.append("Guião (com os planos que cobrem cada linha):")
            for r in coverage:
                ln, who = r["line"], (r["who"].name if r["who"] else None)
                where = ", ".join(f"P{s.number:02d}" for s in r["shots"]) or "SEM PLANO"
                rows.append(f"  Linha {r['n']} [line:{ln.id}] " + (f"{who or 'Diálogo'}: “{ln.text}”" if ln.kind == "dialogue"
                                                                    else f"Ação: {ln.text}") + f" — {where}")
            from app import line_audio

            clips = line_audio.clips(session, scene)
            if clips:
                rows.append("Áudio das falas (ficheiros carregados, cada um cobre linhas seguidas):")
                for c in clips:
                    rng = f"linha {c['first']}" if c["first"] == c["last"] else f"linhas {c['first']}–{c['last']}"
                    dur = f", {c['media'].duration_ms / 1000:.1f} s" if c["media"].duration_ms else ""
                    rows.append(f"  [audio:{c['media'].id}] {rng}{dur}" + (f" — DESATUALIZADO: {c['problem']}" if c["problem"] else ""))
        else:
            rows.append("Guião: vazio.")
    return "\n".join(rows)


def _scene_fields(ctx: Context, session: Session, project: Project, scene: Scene) -> None:
    path = f"C{scene.number:02d}"
    for attr in ("title", "time_of_day", "description"):
        ctx.add(spec(session, project, f"scene:{scene.id}.{attr}", path))
    locations = session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == "location").order_by(Asset.name)).all()
    main = session.get(Asset, scene.location_id) if scene.location_id else None
    ctx.add(FieldSpec(f"scene_location:{scene.id}", f"{path} › Local (cabeçalho)", "scene_location",
                      current=main.name if main else "", options=[a.name for a in locations], help=(
                          'value = o código do local principal da cena (o do cabeçalho), um de: '
                          + (", ".join(f"{a.name} ({a.label or a.name})" for a in locations) or "nenhum local criado")
                          + '; "" deixa a cena sem local.')))
    siblings = session.exec(select(Scene.id).where(Scene.project_id == project.id, Scene.episode_id == scene.episode_id,
                                                   Scene.act_id == scene.act_id)).all()
    ctx.add(FieldSpec(f"scene_order:{scene.id}", f"{path} › Posição da cena", "scene_order", current=str(scene.number),
                      options=[str(n) for n in range(1, len(siblings) + 1)], help=(
                          f"value = a nova posição da cena (1 a {len(siblings)}) no "
                          + ("episódio" if scene.episode_id else "ato" if scene.act_id else "projeto")
                          + "; as outras cenas ajustam a numeração. Ex.: \"7\" para a pôr em 7.º lugar.")))
    cur_lines = []
    for ln in session.exec(select(SceneLine).where(SceneLine.scene_id == scene.id).order_by(SceneLine.order)).all():
        who = session.get(Asset, ln.character_id) if ln.character_id else None
        cur_lines.append({"id": ln.id, "kind": ln.kind, "text": ln.text} | ({"character": who.name} if ln.kind == "dialogue" and who else {}))
    ctx.add(FieldSpec(f"script:{scene.id}", f"{path} › Guião completo (inserir, apagar, mudar a ordem)", "script",
                      current=json.dumps({"lines": cur_lines}, ensure_ascii=False), help=(
                          'value = JSON {"lines": [...]} com o guião COMPLETO da cena pela ordem final. Cada linha: '
                          '{"id": <id de uma linha atual, para a manter>, "kind": "action"|"dialogue", "character": '
                          '"<código, só nos diálogos>", "text": "..."}. Sem "id" = linha nova; uma linha atual que não '
                          "venha na lista é apagada. As linhas mantidas levam consigo os planos que as cobrem e o tempo na "
                          "encenação. Usa-o para inserir no meio, apagar ou reordenar.")))
    ctx.add(FieldSpec(f"new_lines:{scene.id}", f"{path} › Guião › Novas linhas", "new_lines", help=(
        'value = JSON {"lines": [{"kind": "action"|"dialogue", "character": "<código da personagem, só nos '
        'diálogos>", "text": "..."}]}. Acrescenta linhas ao fim do guião da cena. Ações em português europeu, curtas, '
        "no presente, com os códigos dos assets (ex.: TST_VERA, TST_RUA_DO_BAR); diálogos na língua dos diálogos do "
        "projeto. Usa-o para partir a descrição da cena em ações e diálogos.")))
    for n, ln in enumerate(session.exec(select(SceneLine).where(SceneLine.scene_id == scene.id).order_by(SceneLine.order)).all(), 1):
        ctx.add(spec(session, project, f"line:{ln.id}.text", f"{path} › Guião › Linha {n}"))
        who = session.get(Asset, ln.character_id) if ln.character_id else None
        cur = {"kind": ln.kind, "text": ln.text} | ({"character": who.name} if ln.kind == "dialogue" and who else {})
        ctx.add(FieldSpec(f"line_edit:{ln.id}", f"{path} › Guião › Linha {n} (tipo, quem fala e texto)", "line_edit",
                          current=json.dumps(cur, ensure_ascii=False), help=(
                              'value = JSON {"kind": "action"|"dialogue", "character": "<código da personagem, só nos '
                              'diálogos>", "text": "..."}: muda o tipo da linha (ex.: de fala para ação), quem fala e o '
                              "texto. A linha mantém o número.")))
    # Quem e o que aparece na cena (o local principal fica sempre marcado)
    linked = set(session.exec(select(AssetSceneLink.asset_id).where(AssetSceneLink.scene_id == scene.id)).all())
    for a in session.exec(select(Asset).where(Asset.project_id == project.id).order_by(Asset.kind, Asset.name)).all():
        if a.id == scene.location_id:
            continue
        ctx.add(FieldSpec(f"link:{a.id}:{scene.id}", f"{path} › Quem e o que aparece › {a.name}", "link",
                          "1" if a.id in linked else "0", help='value "1" para marcar que aparece na cena, "0" para desmarcar'))
    _new_asset_field(ctx)


def _scene_look_field(ctx: "Context", session: Session, scene: Scene, asset: Asset) -> None:
    from app import looks as L

    options = L.looks_of(session, asset)
    if not options:
        return
    ctx.add(FieldSpec(f"scene_look:{scene.id}:{asset.id}", f"C{scene.number:02d} › Guarda-roupa › {asset.name}",
                      "scene_look", L.label(L.scene_look(session, scene, asset)), [L.BASE] + [lk.name for lk in options],
                      help=f'value = o nome de um look de {asset.name} ou "Base" (a folha aprovada). Vale para todos os '
                           "planos da cena."))


def _wardrobe_fields(ctx: "Context", session: Session, project: Project, asset: Asset) -> None:
    """Guarda-roupa: os looks (nome, roupa, prompt da folha), um look novo e o look em cada cena onde a personagem está."""
    from app import looks as L

    looks = L.looks_of(session, asset)
    scenes = session.exec(select(Scene).join(AssetSceneLink, AssetSceneLink.scene_id == Scene.id)
                          .where(AssetSceneLink.asset_id == asset.id).order_by(Scene.number)).all()
    rows = [f"- «{lk.name}»: {_v(lk.description)} · folha {'aprovada' if lk.image_id else 'por gerar'}" for lk in looks]
    used = [f"C{s.number:02d}: {L.label(L.scene_look(session, s, asset))}" for s in scenes]
    ctx.summary += ("\n\nGUARDA-ROUPA (cada look é a mesma pessoa com outra roupa e acessórios; escolhe-se por cena):\n"
                    + ("\n".join(rows) or "- só a Base (a folha aprovada)")
                    + ("\nLook em cada cena: " + "; ".join(used) if used else ""))
    for lk in looks:
        for attr in ("name", "description", "prompt"):
            ctx.add(spec(session, project, f"look:{lk.id}.{attr}", f"{asset.name} › Look «{lk.name}»"))
    ctx.add(FieldSpec(f"new_look:{asset.id}", f"{asset.name} › Guarda-roupa › Novo look", "new_look", help=(
        'value = JSON {"name": "<nome curto em PT, ex.: Rua>", "description": "<a roupa, o calçado e os acessórios, em '
        'inglês, concretos: peças, cores, materiais, desgaste, onde fica cada acessório>", "scenes": ["<códigos das '
        'cenas onde a personagem usa este look, ex.: C03>"]}. Propõe a partir do guião (ex.: a personagem está à chuva, '
        "de pijama, de fato). A folha do look gera-se depois na ficha, a partir da folha aprovada.")))
    for s in scenes:
        _scene_look_field(ctx, session, s, asset)
    ctx.scene_ids = list(dict.fromkeys([*ctx.scene_ids, *[s.id for s in scenes]]))


REWRITE_ASKED = re.compile(r"\b(de raiz|do zero|desde o in[ií]cio|refa[zç]\w*|reescrev\w*|recome[cç]\w*|"
                           r"nova maquete|maquete nova|apaga tudo|tudo de novo|toda de novo|outra maquete)", re.I)


def limit_rewrites(ctx: "Context", message: str) -> None:
    """Uma maquete que já existe muda-se por partes («Alterar por partes»): só o que muda, e o resto fica igual. A
    reescrita completa só fica disponível quando o utilizador a pede (ex.: «refaz a maquete de raiz»)."""
    if REWRITE_ASKED.search(message or ""):
        return
    for key in [k for k, fs in ctx.fields.items() if k.startswith("layout3d:") and fs.label.endswith("(reescrever toda)")]:
        del ctx.fields[key]


def _new_asset_field(ctx: Context) -> None:
    ctx.add(FieldSpec("new_asset", "Novo asset", "new_asset", help=(
        'value = JSON {"label": "<nome em PT>", "kind": "character|location|prop|element", '
        '"scenes": ["C01", ...], "description": "<descrição canónica em inglês, opcional>", '
        '"refs": ["<código de um asset existente cujas imagens servem de referência à folha>", ...] (opcional)}. '
        "Só para personagens, locais, adereços ou elementos do mundo que ainda não existem. Elemento do mundo "
        "(element) = uma família de coisas que se repetem no fundo e têm de ter sempre o mesmo aspeto (ex.: táxis "
        "autónomos, ecrãs de publicidade, drones de entrega, robots de limpeza); a folha mostra várias variantes. Quando o utilizador menciona "
        "assets com @, usa-os em refs se o novo asset tiver de bater certo com eles.")))


def _location_field(ctx: "Context", session: Session, project: Project, scene: Scene) -> None:
    """O local (com maquete) onde a encenação da cena se passa: um dos locais da cena com maquete."""
    from app import scene3d
    from app.routes.storyboard import scene_locations

    with_layout = [a for a in scene_locations(session, scene) if a.layout3d]
    ctx.add(FieldSpec(f"set3d_from:{scene.id}", f"C{scene.number:02d} › Local da encenação", "set3d_from",
                      current=f"asset:{scene.set3d_asset_id}" if scene.set3d_asset_id else "",
                      options=[f"asset:{a.id}" for a in with_layout], help=(
                          'value = "asset:<id>" de um dos LOCAIS DA CENA COM MAQUETE: a encenação (e todos os planos) '
                          "passa-se dentro dessa maquete.")))
    ctx.summary += ("\nLOCAIS DA CENA COM MAQUETE: " + ("; ".join(f"asset:{a.id} = {a.name} — {scene3d.set_summary(a.layout3d)}"
                                                       for a in with_layout) or "nenhum (monta primeiro a maquete do local)"))


def _stage_summary(session: Session, project: Project, scene: Scene) -> str:
    """A encenação da cena para o Assistente: o local, o elenco com as cores, o JSON e os troços dos planos."""
    import json as _json

    from app import scene3d

    labels = {a.name: a.label or a.name for a in session.exec(select(Asset).where(Asset.project_id == project.id)).all()}
    cast = scene3d.scene_cast(session, scene)
    base, src = scene3d.scene_set(session, scene)
    shots = session.exec(select(Shot).where(Shot.scene_id == scene.id).order_by(Shot.number)).all()
    wins = "; ".join(f"P{sh.number:02d} {scene3d.shot_window(sh)[0]:g}–{sum(scene3d.shot_window(sh)):g} s"
                     + ("" if sh.scene3d else " (sem câmara)") for sh in shots)
    return (
        "ENCENAÇÃO 3D DA CENA: quem está onde e o que se mexe ao longo de toda a cena, dentro da maquete do local. Os "
        "planos são câmaras sobre ela, cada um num troço de tempo — todos veem as mesmas posições (campo/contracampo).\n"
        f"Maquete em uso ({scene3d.source_label(src) if src else 'nenhuma'}): {scene3d.set_summary(base)}\n"
        "ELENCO DA ENCENAÇÃO (cores escolhidas pelo utilizador; usa exatamente estes, com \"code\"): "
        + ("; ".join(f"{labels.get(c, c)} ({c}) = {scene3d.CAST_NAMES.get(col, col)}" for c, col in cast.items()) or "ninguém ainda")
        + "\nPLANTA DA MAQUETE (coordenadas em metros; usa-a para pôr as pessoas nos sítios certos — sentadas nos bancos e "
        "cadeiras que existem, encostadas ao balcão, a passar pelas portas — e nunca inventes mobília que já existe):\n"
        + scene3d.staging_plan(base)
        + "\nEncenação atual: " + (_json.dumps(scene.stage3d, ensure_ascii=False) if scene.stage3d else "nenhuma")
        + "\nTroços dos planos: " + (wins or "ainda sem planos"))


def _scene3d_fields(ctx: "Context", session: Session, project: Project, scene: Scene, shot: Shot) -> None:
    """A câmara do plano sobre a encenação da cena: o troço, as posições das pessoas nele, os momentos e o campo."""
    import json as _json

    from app import moments, scene3d
    from app.media import media_root

    base, _src = scene3d.scene_set(session, scene)
    start, dur = scene3d.shot_window(shot)
    stage = scene.stage3d or {}
    sliced = scene3d.slice_actors(stage.get("actors", []), start, dur)
    ctx.summary += (
        "\n\n" + _stage_summary(session, project, scene)
        + f"\n\nCÂMARA DO PLANO P{shot.number:02d}: mostra a encenação de {start:g} s a {start + dur:g} s "
        f"(DURAÇÃO DO PLANO: {dur:g} s = a do vídeo). Em cada momento da câmara (\"moment\": true) a app escolhe a vista "
        "do local mais parecida, que dá o aspeto do sítio ao vídeo; o vídeo segue o vídeo-guia gravado.\n"
        "POSIÇÕES NO TROÇO DO PLANO (tempo a contar do início do plano; NÃO as mudes aqui — mudam-se na encenação da "
        "cena): " + _json.dumps(sliced, ensure_ascii=False)
        + "\nCâmara atual do plano: " + (_json.dumps(dict(shot.scene3d, start=start), ensure_ascii=False) if shot.scene3d else "nenhuma"))
    rows = moments.rows(session, shot)
    if rows:
        ctx.summary += ("\nVISTAS DO LOCAL NOS MOMENTOS DA CÂMARA (o que vai para o vídeo em cada momento; a parecença compara "
                        "o que a câmara do plano vê com o que a vista mostra: parecida / aproximada / diferente). Para "
                        "mudar, usa o campo «Momento N › Vista do local»: o id de uma vista, \"auto\" (a mais parecida) ou "
                        "\"nova\" (cria uma vista do local a partir da câmara desse momento, para quando nenhuma serve):")
        for r in rows:
            cands = "; ".join(f"{v.id} = {v.zone or 'espaço'} · {v.label}{'' if v.image_id else ' (sem imagem)'}"
                              for v in r["candidates"])
            ctx.summary += (f"\n- Momento {r['n']} ({r['t']:g} s, câmara na zona «{r['zone'] or '—'}»): "
                            f"{'escolhida à mão' if not r['auto'] else 'automática'} → "
                            + (f"{r['view'].id} = {r['view'].label} ({r['fit']})" if r["view"] else "nenhuma")
                            + f". Mais parecidas: {cands or '—'}")
            ctx.add(FieldSpec(f"moment_view:{shot.id}:{r['n']}", f"{_shot_path(scene, shot)} › Momento {r['n']} › Vista do local",
                              "moment_view", current="auto" if r["auto"] else str(r["view"].id if r["view"] else ""),
                              options=["auto", "nova"] + [str(v.id) for v in _loc_views(session, shot)], help=(
                                  'value = "<id da vista>", "auto" ou "nova". Olha para o previs do momento e para as '
                                  "vistas: escolhe a que mostra o mesmo sítio, do mesmo lado; se nenhuma serve, \"nova\".")))
    if base is not None and stage:
        ctx.add(FieldSpec(f"scene3d:{shot.id}", f"{_shot_path(scene, shot)} › Câmara do plano", "scene3d",
                          current=_json.dumps(dict(shot.scene3d, start=start), ensure_ascii=False) if shot.scene3d else None,
                          help=scene3d.CAMERA_HELP))
    elif base is None:
        ctx.summary += "\nA CENA AINDA NÃO TEM LOCAL COM MAQUETE: diz ao utilizador para montar a maquete do local primeiro."
    else:
        ctx.summary += ("\nA CENA AINDA NÃO TEM ENCENAÇÃO: a câmara do plano faz-se depois. Diz ao utilizador para montar a "
                        "encenação na página da cena (Storyboard).")
    seen = scene3d.previews(session, scene3d.preview_hash(scene3d.compose(session, project.id, shot=shot)), shot_id=shot.id)
    if seen:  # o Assistente vê o que construiu: vista de cima da versão atual
        ctx.summary += ("\nVÊS A CENA ATUAL nas imagens anexadas (vista de cima, com o caminho da câmara a rosa). Antes "
                        "de propores, confere: a câmara não atravessa paredes e vê o que o plano descreve.")
    from app.models import MediaFile

    view_imgs, shown = [], set()
    for r in rows:  # as vistas candidatas de cada momento (as aprovadas), para comparar com o previs
        for v in r["candidates"][:3]:
            m = session.get(MediaFile, v.image_id) if v.image_id else None
            if m is not None and m.status == "approved" and v.id not in shown and (media_root() / m.path).is_file():
                shown.add(v.id)
                view_imgs.append((media_root() / m.path, f"vista {v.id} do local: {v.zone or 'espaço'} · {v.label}"))
    ctx.images = seen + [(media_root() / r["render"].path, f"previs do momento {r['n']} (câmara do plano)")
                         for r in rows if r["render"] is not None] + view_imgs[:8] + ctx.images
    ctx.suggestions = (["Ajusta a câmara deste plano", "Revê a câmara em cada momento"] if shot.scene3d
                       else ["Põe a câmara deste plano"]) + ctx.suggestions


def _json_dumps(v) -> str | None:
    import json as _json

    return _json.dumps(v, ensure_ascii=False) if v else None


def _in_frame(session: Session, shot: Shot) -> list[str]:
    from app.scene3d import in_frame

    return in_frame(session, shot)


def _loc_views(session: Session, shot: Shot) -> list:
    from app import location3d, moments

    loc = moments.location(session, shot)
    return location3d.location_views(session, loc) if loc else []


def _shot_path(scene: Scene, shot: Shot) -> str:
    return f"C{scene.number:02d} › P{shot.number:02d}"


def build(session: Session, project: Project, key: str, scene_hint: int | None = None) -> Context:
    kind, _, raw = key.partition(":")
    obj_id = int(raw) if raw.isdigit() else None
    base = project_summary(session, project)

    if kind in ("room", "room_file"):  # Sala de argumentistas (app/assistant/room_ctx.py)
        from app.assistant import room_ctx

        return room_ctx.build(session, project, key, base)

    if kind == "bible":
        # A capa usa as personagens e o local do projeto: o Assistente precisa das fichas deles.
        ctx = Context(key, "Projeto · Bíblia", 1, "\n\n".join([base, assets_summary(session, project)]))
        for attr in ("premise", "logline", "synopsis"):
            ctx.add(spec(session, project, f"project.{attr}", "A ideia"))
        for attr in ("genre", "tone", "audience", "dialogue_language", "references", "render_style", "proportions",
                     "palette_light", "style_block", "exclusions", "project_rules"):
            group = "Identidade" if attr in ("genre", "tone", "audience", "dialogue_language", "references") else \
                "Estilo visual" if attr in ("render_style", "proportions", "palette_light", "style_block") else "Regras fixas"
            ctx.add(spec(session, project, f"project.{attr}", group))
        ctx.add(spec(session, project, "project.cover_prompt", "Capa"))
        _project_video_fields(ctx, session, project)
        ctx.suggestions = (["Ajuda-me a escrever a premissa"] if not (project.premise or "").strip() else
                           ["Propõe a logline e a sinopse a partir da premissa", "Propõe género, tom e público a partir da premissa"]
                           ) + ["Revê o bloco de estilo", "Revê o prompt da capa"]
        ctx.use_effective_prompts(session, project)
        return ctx

    if kind in ("story", "episode", "act"):
        summary = [base]
        ctx = Context(key, "História", 2, "")
        scenes: list[Scene] = []
        if kind == "story":
            if project.type == "series":
                for s in session.exec(select(Season).where(Season.project_id == project.id).order_by(Season.number)).all():
                    summary.append(f"Temporada {s.number} “{_v(s.title)}”: arco {_v(s.arc)} · resumo {_v(s.summary)}")
                    for attr in ("title", "arc", "summary"):
                        ctx.add(spec(session, project, f"season:{s.id}.{attr}", f"T{s.number}"))
            elif project.type == "film":
                for a in session.exec(select(Act).where(Act.project_id == project.id).order_by(Act.number)).all():
                    summary.append(f"Ato {a.number} “{_v(a.title)}”: arco {_v(a.arc)} · resumo {_v(a.summary)}")
                    for attr in ("title", "arc", "summary"):
                        ctx.add(spec(session, project, f"act:{a.id}.{attr}", f"A{a.number}"))
            else:
                scenes = list(session.exec(select(Scene).where(Scene.project_id == project.id).order_by(Scene.number)).all())
        elif kind == "episode":
            episode = session.get(Episode, obj_id)
            ctx.label = f"Episódio {episode.number}"
            summary.append(f"Episódio {episode.number} “{_v(episode.title)}”: logline {_v(episode.logline)} · sinopse {_v(episode.synopsis)}")
            for attr in ("title", "logline", "synopsis"):
                ctx.add(spec(session, project, f"episode:{episode.id}.{attr}", f"E{episode.number:02d}"))
            scenes = list(session.exec(select(Scene).where(Scene.episode_id == episode.id).order_by(Scene.number)).all())
        else:
            act = session.get(Act, obj_id)
            ctx.label = f"Ato {act.number}"
            summary.append(f"Ato {act.number} “{_v(act.title)}”: arco {_v(act.arc)} · resumo {_v(act.summary)}")
            for attr in ("title", "arc", "summary"):
                ctx.add(spec(session, project, f"act:{act.id}.{attr}", f"A{act.number}"))
            scenes = list(session.exec(select(Scene).where(Scene.act_id == act.id).order_by(Scene.number)).all())
        groups = []  # onde se podem criar cenas novas
        if kind == "episode":
            groups = [(f"episode-{obj_id}", f"E{episode.number:02d}")]
        elif kind == "act":
            groups = [(f"act-{obj_id}", f"A{act.number}")]
        elif project.type == "short":
            groups = [("project", "Curta")]
        elif project.type == "film":
            groups = [(f"act-{a.id}", f"A{a.number}") for a in
                      session.exec(select(Act).where(Act.project_id == project.id).order_by(Act.number)).all()]
        if kind == "story" and project.type == "series":  # criar temporadas e episódios (como os botões da app)
            ctx.add(FieldSpec("new_season", "Nova temporada", "new_season", help=(
                'value = JSON {"title": "<opcional>", "arc": "<opcional>", "summary": "<opcional>"}. Fica no fim.')))
            for s in session.exec(select(Season).where(Season.project_id == project.id).order_by(Season.number)).all():
                ctx.add(FieldSpec(f"new_episode:{s.id}", f"T{s.number} › Novo episódio", "new_episode", help=(
                    'value = JSON {"title": "<título>", "logline": "<opcional>", "synopsis": "<opcional>"}. Fica no fim '
                    "da temporada; as cenas criam-se depois no ecrã episode:<id> (new_scene).")))
        elif kind == "story" and project.type == "film":
            ctx.add(FieldSpec("new_act", "Novo ato", "new_act", help=(
                'value = JSON {"title": "<opcional>", "arc": "<opcional>", "summary": "<opcional>"}. Fica no fim.')))
        o = get_options(session)
        for gkey, glabel in groups:
            ctx.add(FieldSpec(f"new_scene:{gkey}", f"{glabel} › Nova cena", "new_scene", help=(
                'value = JSON {"title": "<título curto em PT>", "description": "<o que acontece, em PT>", '
                '"location": "<código de um local do projeto, opcional>", "time_of_day": "<da lista, opcional>"}. '
                f"Horas do dia: {o.get('time_of_day')}. Uma cena por proposta, pela ordem da história; fica no fim.")))
        focus = next((s for s in scenes if s.id == scene_hint), None)
        for s in scenes:
            summary.append(scene_summary(session, s, with_lines=(focus is None or s.id == focus.id)))
        if focus:
            ctx.label += f" · Cena {focus.number}"
            _scene_fields(ctx, session, project, focus)
        ctx.summary = "\n\n".join(summary + [assets_summary(session, project)])
        ctx.scene_ids = [s.id for s in scenes]
        ctx.suggestions = (["Divide a descrição em ações e diálogos", "Marca quem e o que aparece nesta cena",
                            "Revê o guião da cena aberta"] if focus
                           else (["Propõe os arcos das temporadas a partir da sinopse"] if kind == "story" and project.type == "series"
                                 else ["Propõe as cenas a partir da sinopse"]))
        return ctx

    if kind == "breakdown":
        scenes = list(session.exec(select(Scene).where(Scene.project_id == project.id).order_by(Scene.number)).all())
        links = session.exec(select(AssetSceneLink).join(Scene, Scene.id == AssetSceneLink.scene_id).where(Scene.project_id == project.id)).all()
        by_asset: dict[int, list[str]] = {}
        for link in links:
            sc = next((s for s in scenes if s.id == link.scene_id), None)
            if sc:
                by_asset.setdefault(link.asset_id, []).append(f"C{sc.number:02d}")
        assets = session.exec(select(Asset).where(Asset.project_id == project.id)).all()
        matrix = "\n".join(f"- {a.name}: {', '.join(sorted(by_asset.get(a.id, []))) or 'nenhuma cena'}" for a in assets)
        ctx = Context(key, "Assets por cena", 4, "\n\n".join(
            [base] + [scene_summary(session, s) for s in scenes] + [assets_summary(session, project), "Matriz atual:\n" + (matrix or "vazia")]))
        _new_asset_field(ctx)
        for a in assets:
            for s in scenes:
                on = s.id in {link.scene_id for link in links if link.asset_id == a.id}
                ctx.add(FieldSpec(f"link:{a.id}:{s.id}", f"{a.name} em C{s.number:02d}", "link", "1" if on else "0",
                                  help='value "1" para marcar, "0" para desmarcar'))
        ctx.scene_ids = [s.id for s in scenes]
        ctx.suggestions = ["Extrai os assets do guião", "Revê o quadro contra o guião"]
        return ctx

    if kind == "assets":
        ctx = Context(key, "Design de assets", 4, base + "\n\n" + assets_summary(session, project),
                      suggestions=(["Ajuda-me a descrever o mundo"] if not (project.world or "").strip() else
                                   ["Revê os assets contra o mundo"]) + ["Organiza os assets em grupos", "Que assets faltam descrever?"])
        ctx.add(spec(session, project, "project.world", "Mundo"))
        ctx.add(spec(session, project, "project.world_prompt", "Mundo"))
        if (project.world or "").strip() and not (project.world_prompt or "").strip():
            ctx.suggestions.insert(0, "Resume o mundo para os prompts")
        _new_asset_field(ctx)  # criar assets daqui, sobretudo elementos do mundo (táxis, ecrãs, drones…)
        if (project.world or "").strip() and not session.exec(select(Asset.id).where(
                Asset.project_id == project.id, Asset.kind == "element")).first():
            ctx.suggestions.insert(1, "Propõe os elementos do mundo")
        for a in session.exec(select(Asset).where(Asset.project_id == project.id).order_by(Asset.name)).all():
            ctx.add(spec(session, project, f"asset:{a.id}.group_name", a.name))
        return ctx

    if kind == "asset":
        asset = session.get(Asset, obj_id)
        ctx = Context(key, f"Asset · {asset.name}", 4, "\n\n".join([base, assets_summary(session, project),
                      f"Asset em edição: {asset.name} ({ASSET_KINDS[asset.kind]}, “{asset.label}”)\n" +
                      f"{'Perfil' if asset.kind == 'character' else 'Notas'} completo(as):\n{_v(asset.profile)}\n" +
                      f"Voz: tipo {_v(asset.voice_type)} · timbre {_v(asset.voice_timbre)} · descrição {_v(asset.voice)}\n" +
                      (f"Voz de referência: {'aprovada' if asset.voice_ref_id else 'ainda sem voz aprovada'} · o que a "
                       f"voz diz (amostra): {_v(asset.voice_sample)}\n" if asset.kind == "character" else "") +
                      "Referências para gerar a folha (as imagens aprovadas destes assets vão anexadas; a descrição "
                      "deste asset deve bater certo com elas): "
                      + (", ".join(a.name for a in (session.get(Asset, i) for i in asset.sheet_refs or []) if a) or "nenhuma")
                      + ((f"\nModelo 3D importado: {'sim — nas maquetes e na encenação usa-se com {"type": "model", "asset": "' + asset.name + '"}, e um ator com este código aparece com esta forma' if asset.model3d_id else 'nenhum (o utilizador pode carregar um .glb na secção Modelo 3D)'}")
                         if asset.kind in ("prop", "element") else "")]))
        attrs = ["label", "group_name", "canonical_description", "profile", "sheet_prompt"]
        if asset.kind == "character":
            attrs += ["portrait_prompt", "key_features", "voice_type", "voice_timbre", "voice", "voice_sample", "voice_note"]
        loc_seen: list = []
        if asset.kind == "location":
            from app import scene3d

            if asset.layout3d:  # mudar por partes: só o que muda (rápido, e o resto fica intacto)
                ctx.add(FieldSpec(f"layout3dedit:{asset.id}", f"{asset.name} › Maquete 3D › Alterar por partes",
                                  "layout3d_edit", help=scene3d.LAYOUT_EDIT_HELP + " " + scene3d.OBJECTS_HELP
                                  + " " + scene3d.models_line(session, project.id)))
            ctx.add(FieldSpec(f"layout3d:{asset.id}", f"{asset.name} › Maquete 3D" + (" (reescrever toda)" if asset.layout3d else ""),
                              "layout3d", help=(scene3d.LAYOUT_REWRITE_NOTE if asset.layout3d else "")
                              + scene3d.LAYOUT_HELP_PREFIX + scene3d.SET_HELP + " " + scene3d.models_line(session, project.id)))
            if asset.layout3d:
                from app import location3d

                for z in location3d.zones(asset.layout3d):
                    if not z["name"]:
                        continue
                    ctx.add(FieldSpec(f"zonenote:{asset.id}:{z['name']}", f"{asset.name} › {z['name']} › Como é", EN,
                                      current=(asset.zone_notes or {}).get(z["name"]) or None, help=(
                                          "Descrição (EN, até ~50 palavras, 1–3 frases) só desta " + ("zona ao ar livre: fachada do edifício "
                                          "(materiais, porta, janelas, letreiro), a rua ou o pátio (pavimento, passeios, o que "
                                          "há em frente), luz" if z["outdoor"] else "divisão: paredes, chão, mobília principal, "
                                          "luz") + ". O escritor de prompts usa-o em todas as vistas desta zona; a descrição "
                                          "canónica é do local todo (normalmente o interior principal). Sem pessoas.")))
                ref_opts = location3d.ref_options(session, asset)
                if ref_opts:
                    menu = "; ".join(f"{o['id']} = {o['group']} · {o['label']}" for o in ref_opts)
                    for z in location3d.zones(asset.layout3d):
                        if z["name"]:
                            ctx.add(FieldSpec(f"zonerefs:{asset.id}:{z['name']}", f"{asset.name} › {z['name']} › Referências",
                                              "zone_refs", options=[str(o["id"]) for o in ref_opts],
                                              current=json.dumps(location3d.zone_refs(asset, z["name"])), help=(
                                                  f"value = JSON lista de ids (até {location3d.MAX_ZONE_REFS}, por ordem de "
                                                  "importância; [] tira todas) das imagens que mostram como é esta zona; "
                                                  "as fotos reais vão para o escritor de prompts, as outras também para o "
                                                  f"gerador. Escolhe entre: {menu}.")))
                for lv in location3d.location_views(session, asset):
                    ctx.add(FieldSpec(f"lvprompt:{lv.id}", f"{asset.name} › {lv.zone or 'espaço'} · {lv.label} › Prompt",
                                      "prompt", current=lv.prompt, help=(
                                          "Prompt (EN) da imagem desta vista da maquete, escrito pelo escritor de prompts. "
                                          "Para melhorar o aspeto de uma zona, muda o «Como é» dessa zona (vale para todas "
                                          "as vistas dela; ao gerar, o escritor reescreve-as); muda o prompt de uma vista só "
                                          "para algo que é só dela, e mantém os marcadores @CÓDIGO das imagens.")))
            ctx.summary += ("\n\nLOCAL: tudo pela maquete 3D — dá a geometria (zonas, portas, mobília) e as vistas são "
                            "posições de câmara nela. Monta-a a partir da descrição, das notas e das fotos de referência.")
            if asset.layout3d:
                from app import location3d as _l3d

                loose = _l3d.outside_zones(asset.layout3d)
                if loose:
                    ctx.summary += (f"\n\nATENÇÃO — {len(loose)} coisa(s) da maquete fora de todas as zonas (ex.: "
                                    + ", ".join(loose[:6]) + "). Uma vista guardada aí não sabe onde está: alarga a zona "
                                    "ao ar livre (ex.: a «rua da frente» até ao fim da rua modelada) ou cria zonas novas "
                                    "(ex.: «cruzamento», «rua transversal») com \"ceiling\": 0.")
            ctx.summary += ("\n\nMaquete 3D do local, NUMERADA — os #n são "
                            "os números a usar em «Alterar por partes»:\n" + scene3d.layout_listing(asset.layout3d))
            seen = scene3d.previews(session, scene3d.preview_hash(scene3d.compose(session, project.id, location=asset)),
                                    asset_id=asset.id) if asset.layout3d else []
            if seen:
                ctx.summary += ("\nVÊS A MAQUETE ATUAL nas imagens anexadas (de cima e as 4 vistas a partir do centro). Antes "
                                "de propores, confere-a contra a descrição, as vistas e as fotos do local: paredes, portas, "
                                "mobília virada para o lado certo, nada a atravessar paredes, escala.")
                loc_seen = seen
            attrs.remove("sheet_prompt")  # os locais não têm folha: as imagens são as vistas da maquete
        for attr in attrs:
            ctx.add(spec(session, project, f"asset:{asset.id}.{attr}", asset.name))
        if asset.kind == "character":
            _wardrobe_fields(ctx, session, project, asset)
        refs = [session.get(Asset, i) for i in (asset.sheet_refs or [])]
        ctx.add(FieldSpec(f"sheet_refs:{asset.id}", f"{asset.name} › Referências da folha (lista completa)", "sheet_refs",
                          current=json.dumps([r.name for r in refs if r], ensure_ascii=False), help=(
                              'value = JSON com a lista COMPLETA dos códigos cujas imagens aprovadas vão como referência '
                              'ao gerar a folha (ex.: ["PRJ_RUA", "PRJ_BAR"]); tira-se uma retirando-a da lista; [] = nenhuma.')))
        ctx.add(FieldSpec(f"asset_code:{asset.id}", f"{asset.name} › Código", "asset_code", current=asset.name, help=(
            f"value = o código novo (começa por {project.prefix}_, só maiúsculas, números e _). A app muda a pasta e os "
            "ficheiros e substitui o código em todos os textos do projeto (guião, descrições, prompts, encenação).")))
        ctx.add(FieldSpec(f"asset_kind:{asset.id}", f"{asset.name} › Tipo", "asset_kind", current=asset.kind,
                          options=list(ASSET_KINDS), help=(
                              "value = character | location | prop | element. Ao deixar de ser personagem, as falas "
                              "deixam de ter esta personagem; ao deixar de ser local, as cenas deixam de o ter no cabeçalho.")))
        ctx.add(FieldSpec(f"sheet_ref:{asset.id}", f"{asset.name} › Adicionar às referências da folha", "sheet_ref", help=(
            "value = código de um asset do projeto (ex.: TST_RUA_DO_BAR) cujas imagens aprovadas passam a ir como "
            "referência ao gerar a folha deste asset. Propõe quando o utilizador mencionar um asset com @ e este "
            "tiver de bater certo com ele (ex.: vista aérea da mesma rua, interior de um veículo).")))
        ctx.images = loc_seen + asset_images(session, asset)
        ctx.suggestions = ["Escreve a descrição canónica", "Revê o prompt da folha"]
        if asset.kind == "location":
            ctx.suggestions = ["Revê a maquete 3D" if asset.layout3d else "Monta a maquete 3D deste local",
                               "Escreve a descrição canónica"]
        if asset.kind == "character":
            ctx.suggestions = (["Ajuda-me a escrever o perfil"] if not (asset.profile or "").strip()
                               else ["Propõe a descrição canónica a partir do perfil", "Propõe a voz a partir do perfil"]
                               ) + ctx.suggestions + ["Revê o prompt do retrato"]
        has_photos = any("foto de referência" in label for _p, label in ctx.images)
        if asset.kind != "character" and has_photos:
            ctx.suggestions = ["Escreve a descrição canónica a partir das fotos"] + [
                x for x in ctx.suggestions if x != "Escreve a descrição canónica"]
        if asset.kind == "location":
            from app import location3d

            missing = [z["name"] for z in location3d.zones(asset.layout3d) if z["name"]
                       and not ((asset.zone_notes or {}).get(z["name"]) or "").strip()] if asset.layout3d else []
            ctx.suggestions += ((["Escreve o «Como é» das zonas que não têm"] if missing else [])
                                + ["Revê o «Como é» de cada zona"])
        ctx.use_effective_prompts(session, project)
        return ctx

    if kind == "sb_scene":
        scene = session.get(Scene, obj_id)
        shots = session.exec(select(Shot).where(Shot.scene_id == scene.id).order_by(Shot.number)).all()
        from app import scene3d as _s3

        shot_rows = "\n".join(f"- P{s.number:02d}: {_v(s.shot_type)} · {_v(s.camera_angle)} · {_v(s.title)} · "
                              f"{_v(s.duration_s)} s, troço {_s3.shot_window(s)[0]:g}–{sum(_s3.shot_window(s)):g} s" for s in shots)
        ctx = Context(key, f"Cena {scene.number} · {scene.title or 'Storyboard'}", 5, "\n\n".join(
            [base, scene_summary(session, scene), assets_summary(session, project), "Planos atuais:\n" + (shot_rows or "nenhum"),
             ]))
        ctx.add(spec(session, project, f"scene:{scene.id}.blocking", f"C{scene.number:02d}"))
        if scene.stage3d:
            _ends = [_s3.shot_window(s)[0] + _s3.shot_window(s)[1] for s in shots]
            _d = float(scene.stage3d.get("duration") or 0)
            ctx.add(FieldSpec(f"scene_duration:{scene.id}", f"C{scene.number:02d} › Duração da encenação", "scene_duration",
                              current=f"{_d:g}", options=[f"{max(_ends or [0]):g}"], help=(
                                  f"Segundos (até {_s3.STAGE_MAX_S}). Alonga (ou encurta) a encenação: as posições e os "
                                  "tempos das personagens ficam como estão, só o fim muda. Não pode ficar abaixo do fim "
                                  f"do último plano ({max(_ends or [0]):g} s).")))
        from app import scene3d as _s3
        from app.shot_script import script_coverage as _cov

        ctx.summary += "\n\n" + _stage_summary(session, project, scene)
        ctx.summary += ("\nGuião numerado (os números a usar em \"lines\"): "
                        + "; ".join(f"{r['n']}. {r['line'].text[:80]}" for r in _cov(session, scene)))
        _location_field(ctx, session, project, scene)
        if scene.set3d_asset_id:
            ctx.add(FieldSpec(f"stage3d:{scene.id}", f"C{scene.number:02d} › Encenação 3D", "stage3d",
                              current=_json_dumps(scene.stage3d), help=_s3.STAGE_HELP + " " + _s3.models_line(session, project.id)))
        seen = _s3.previews(session, _s3.preview_hash(_s3.compose(session, project.id, scene=scene)), scene_id=scene.id)
        if seen:
            ctx.summary += ("\nVÊS A ENCENAÇÃO ATUAL nas imagens anexadas (de cima). Confere que ninguém atravessa paredes "
                            "nem mobília e que as posições batem com as notas de blocking.")
            ctx.images = seen + ctx.images
        from app import looks as _looks
        from app.routes.production import scene_assets as _scene_assets

        for a in _scene_assets(session, scene):
            if a.kind == "character":
                _scene_look_field(ctx, session, scene, a)
                line = _looks.context_line(session, scene, a)
                ctx.summary += f"\n{a.name}: " + (line[2:] if line else "look Base (a folha aprovada)")
        o = get_options(session)
        ctx.add(FieldSpec(f"new_shot:{scene.id}", f"C{scene.number:02d} › Novo plano", "new_shot", help=(
            'value = JSON {"title": "<nome curto em PT>", "description": "<o que acontece no plano, em PT>", '
            '"lines": [<números das linhas do guião que o plano cobre>], "shot_type": "<da lista>", "camera_angle": "<da lista>", '
            '"camera_movement": "<da lista>", "duration": <segundos>, "camera": {"start": <segundo da encenação onde o '
            'plano começa>, "camera": [posições da câmara, ver abaixo]}}. '
            f"Listas — tipo de plano: {o.get('shot_type')}; ângulo: {o.get('camera_angle')}; movimento: {o.get('camera_movement')}. "
            "COBERTURA: os planos são câmaras sobre a ENCENAÇÃO da cena, cada um num troço de tempo (\"start\" e "
            "\"duration\", pelos segundos das linhas em ENCENAÇÃO › lines). Numa conversa: um plano geral que situa toda a "
            "gente, e planos por cima do ombro (campo/contracampo) de quem fala, todos do mesmo lado do eixo (regra dos "
            "180°); dois planos podem mostrar o mesmo troço de pontos de vista diferentes (a montagem escolhe). Um plano "
            "por proposta, pela ordem do guião; cada linha do guião em pelo menos um plano. Duração de cada plano entre 4 e "
            "12 s. Formato da câmara: " + _s3.CAMERA_HELP if scene.set3d_asset_id else
            'value = JSON {"title", "description" (PT), "lines": [números], "shot_type", "camera_angle", "camera_movement"}. '
            f"Listas — tipo de plano: {o.get('shot_type')}; ângulo: {o.get('camera_angle')}; movimento: {o.get('camera_movement')}. "
            "Um plano por proposta, pela ordem do guião; cada linha do guião em pelo menos um plano.")))
        ctx.scene_ids = [scene.id]
        ctx.suggestions = (["Monta a encenação 3D da cena a partir do guião e do blocking"] if not scene.stage3d
                           else ["Propõe a cobertura: os planos e as câmaras de cada um", "Ajusta a encenação"]) \
            + ["Escreve as notas de blocking"]
        ctx.use_effective_prompts(session, project)
        return ctx

    if kind in ("sb_shot", "video_shot"):
        from app import moments, ref_tokens
        from app.media import media_root
        from app.routes.video import shot_model, inputs_for, shot_mode

        shot = session.get(Shot, obj_id)
        scene = session.get(Scene, shot.scene_id)
        checks = session.exec(select(ShotCheck).where(ShotCheck.shot_id == shot.id)).all()
        prev = session.exec(select(Shot).where(Shot.scene_id == scene.id, Shot.number == shot.number - 1)).first()
        rows = [base, scene_summary(session, scene), assets_summary(session, project),
                f"Plano em edição P{shot.number:02d} «{_v(shot.title)}»: tipo {_v(shot.shot_type)} · ângulo "
                f"{_v(shot.camera_angle)} · movimento {_v(shot.camera_movement)} · duração {_v(shot.duration_s)} s",
                f"O QUE ACONTECE NO PLANO (do utilizador, em português): {_v(shot.description)}",
                "Quem a câmara do plano vê: " + (", ".join(_in_frame(session, shot)) or "ninguém (ou ainda sem câmara)")]
        if prev:
            rows.append(f"Plano anterior P{prev.number:02d}: {_v(prev.shot_type)} · {_v(prev.description)}")
        if checks:
            rows.append("Checklist de continuidade: " + "; ".join(f"[{CHECK_CATEGORIES[c.category]}] {c.text}" for c in checks))
        model = shot_model(session, shot)
        pairs = []
        if kind == "video_shot":
            mode = shot_mode(session, shot, model)
            sent = inputs_for(session, shot, model)
            pairs = [(m, *ref_tokens.token_for(session, m)) for m in sent]
            rows.append(_inputs_block(session, shot, model))
            rows.append(f"Modelo {_v((model or {}).get('name'))} · " + ("recebe as imagens como referências" if mode == "references"
                                                         else "só recebe a imagem inicial (e final)"))
            rows.append(_video_line(session, project, shot, model))
            from app import model_profiles

            rows.append(model_profiles.video_format((model or {}).get("name")))
            rows.append("O PROMPT DE VÍDEO é escrito pelo escritor de prompts (botões Gerar e Escrever, com a guia da "
                        "Configuração). Propõe alterações ao prompt só quando o utilizador pedir; o som e a fala podes "
                        "propor nos campos próprios.")
            from app.prompt_context import shot_talk

            talk = " / ".join(f"{(session.get(Asset, c).name if c and session.get(Asset, c) else '?')}: \"{txt}\""
                              for c, txt in shot_talk(session, shot))
            # o mesmo que o escritor de prompts vê: o que o vídeo-guia mostra em cada momento e as regras do prompt
            # (sem isto, um «revê o prompt» trocava «de frente» por «de costas» e voltava a contar figurantes)
            from app.options import get_config
            from app.prompt_targets import _frame_block
            from app.seeds import DEFAULT_PROMPT_GUIDES

            if shot.scene3d:
                rows.append(_frame_block(session, shot))
            rule = (get_config(session, "prompt_guides", {}) or {}).get("video") or DEFAULT_PROMPT_GUIDES["video"]
            rows.append("REGRAS DO PROMPT DE VÍDEO (as do escritor; uma revisão cumpre-as, não as desfaz):\n" + rule
                        + "\nAo rever, confirma a ação contra «O QUE O VÍDEO-GUIA MOSTRA EM CADA MOMENTO»: nunca troques "
                        "de frente por de costas (nem o lado do ecrã) — o modelo segue o texto e não o vídeo-guia.")
            rows.append(f"Som: falas {_v(talk)} · efeitos {_v(shot.sound_effects)} · ambiente {_v(shot.ambience)} "
                        f"· música {_v(shot.music)}")
            from app import keyframe as _KF

            _key = _KF.approved(session, shot)
            rows.append("FRAME-CHAVE (imagem do início do take com as personagens no sítio; aprovada, segue como "
                        "referência no pedido de vídeo): " + (
                            f"aprovado ({ref_tokens.token_for(session, _key)[0]})"
                            + (" — DESATUALIZADO: a câmara ou a encenação mudou depois" if _KF.stale(session, shot) else "")
                            if _key is not None else "nenhum aprovado")
                        + ". Gera-se no Storyboard do plano (último passo), não aqui.")
            rows.append(_timing_line(session, scene, shot))
            ctx = Context(key, f"Cena {scene.number} · Plano {shot.number} · vídeo", 6, "\n\n".join(rows))
            _duration_field(ctx, session, scene, shot)
            _video_fields(ctx, session, project, shot, model)
            from app import video_inputs as _VI

            _acc = _VI.accepts(model)
            ctx.add(FieldSpec(f"shot:{shot.id}.video_inputs", f"{_shot_path(scene, shot)} › O que segue no pedido",
                              "video_inputs", current=_VI.as_json(session, shot, model), help=(
                                  'value = JSON {"guide": true|false, "items": [{"ref": "<marcador>", "on": true|false, '
                                  '"mode": "ref"|"start_frame"|"end_frame"}]} — os itens da lista «IMAGENS QUE SEGUEM NO '
                                  'PEDIDO DE VÍDEO» (imagens e vozes; as vozes só com "on"); os que faltarem ficam como '
                                  f'estão. Este modelo aceita: até {_acc["max_images"]} imagens de referência'
                                  + (", vídeo-guia" if _acc["video"] else ", sem vídeo")
                                  + (f", até {_acc['max_audios']} vozes" if _acc["audio"] else ", sem vozes")
                                  + ", frame inicial" + (" e final" if _acc["end_frame"] else "")
                                  + ". Com uma frame inicial o pedido é imagem-para-vídeo: seguem só as frames e o prompt.")))
            for attr in ("sound_effects", "ambience", "music", "video_prompt"):
                ctx.add(spec(session, project, f"shot:{shot.id}.{attr}", _shot_path(scene, shot)))
            ctx.suggestions = ["Propõe o som deste plano", "Revê o prompt de vídeo"]
            ms = moments.rows(session, shot)
            ctx.images = [(media_root() / r["render"].path, f"previs do momento {r['n']}") for r in ms if r["render"]]
            from app import prompt_targets as _PT

            guide_imgs = [(media_root() / m.path, label) for m, label
                          in _PT.seen_only(session, _PT.get(session, project, "video", str(shot.id))) if m.role == "guide"]
            ctx.images = guide_imgs + ctx.images  # o próprio vídeo-guia em imagens, antes dos previs
        else:
            rows.append(_timing_line(session, scene, shot))
            from app import keyframe as _KF

            _key = _KF.approved(session, shot)
            _mo = _KF.moment(session, shot)
            _all = _KF.all_approved(session, shot)
            rows.append("FRAMES-CHAVE (último passo do plano: imagens finais com as personagens no sítio, uma por momento da "
                        "câmara, a partir do previs desse momento; são o storyboard final e o animatic; UMA segue como "
                        "referência no pedido de vídeo — de preferência uma em que já estão todos nas suas posições): "
                        + ("; ".join(f"momento {a['n'] or '?'} ({a['t']:g} s) {ref_tokens.token_for(session, a['media'])[0]}"
                                     + (" — SEGUE NO VÍDEO" if a["video"] else "") + (" — DESATUALIZADO" if a["stale"] else "")
                                     for a in _all) or "nenhum aprovado")
                        + (f". O próximo gera-se do momento {_mo['n']} ({_mo['t']:g} s)" if _mo else "")
                        + (f"; nesse instante: {'; '.join(_KF.absent(session, shot))}" if _mo and _KF.absent(session, shot) else "")
                        + f". Prompt: {_v(shot.key_prompt)}. O prompt é do escritor de prompts (botões Gerar e Escrever do "
                        "bloco «Frames-chave»); propõe alterações só quando o utilizador pedir. Aprovar e escolher o que "
                        "segue no vídeo é do utilizador.")
            ctx = Context(key, f"Cena {scene.number} · Plano {shot.number}", 5, "\n\n".join(rows))
            _duration_field(ctx, session, scene, shot)
            ctx.add(spec(session, project, f"shot:{shot.id}.key_prompt", _shot_path(scene, shot)))
            if _mo:
                ctx.add(FieldSpec(f"key_moment:{shot.id}", f"{_shot_path(scene, shot)} › Frame-chave › Momento", "key_moment",
                                  current=str(_mo["n"]), options=[str(k["n"]) for k in moments.keys(shot)], help=(
                                      'value = "<número do momento da câmara>": de que momento se gera o próximo '
                                      "frame-chave (o previs, a vista do local e as folhas de quem está em plano nesse "
                                      "instante). Escreve depois o prompt para esse momento.")))
            for attr in ("title", "description", "shot_type", "camera_angle", "camera_movement"):
                ctx.add(spec(session, project, f"shot:{shot.id}.{attr}", _shot_path(scene, shot)))
            from app.shot_script import script_coverage, shot_line_ids

            mine = set(shot_line_ids(session, shot))
            ctx.add(FieldSpec(f"shot_lines:{shot.id}", f"{_shot_path(scene, shot)} › Linhas do guião", "shot_lines",
                              current=", ".join(str(r["n"]) for r in script_coverage(session, scene) if r["line"].id in mine),
                              help='value = JSON {"lines": [<números das linhas do guião que este plano cobre>]} — substitui a escolha.'))
            ctx.add(FieldSpec(f"new_check:{shot.id}", f"{_shot_path(scene, shot)} › Nova verificação", "new_check", help=(
                'value = JSON {"category": "props|geography|gazes", "text": "<o que confirmar, em PT>", "note": "<opcional>"}')))
            ctx.suggestions = ["Que continuidade devo verificar?"]
            _scene3d_fields(ctx, session, project, scene, shot)
        ctx.scene_ids = [scene.id]
        images = [(media_root() / m.path, f"{t} — {d}") for m, t, d in pairs if (media_root() / m.path).is_file()]
        if images:
            ctx.images = images + [x for x in ctx.images if x[0] not in {p for p, _l in images}]
        return ctx

    if kind == "storyboard":  # vista geral do storyboard: propor planos em qualquer cena
        o = get_options(session)
        scenes = list(session.exec(select(Scene).where(Scene.project_id == project.id).order_by(Scene.number)).all())
        rows = [base]
        for sc in scenes:
            shots = session.exec(select(Shot).where(Shot.scene_id == sc.id).order_by(Shot.number)).all()
            rows.append(scene_summary(session, sc, with_lines=False) + "\nPlanos: "
                        + ("; ".join(f"P{s.number:02d} {_v(s.title)}" for s in shots) or "nenhum"))
        ctx = Context(key, "Storyboard", 5, "\n\n".join(rows))
        for sc in scenes:
            ctx.add(FieldSpec(f"new_shot:{sc.id}", f"C{sc.number:02d} › Novo plano", "new_shot", help=(
                'value = JSON {"title", "description" (PT), "shot_type", "camera_angle", "camera_movement", "visible": '
                f'[códigos]}}. Listas — tipo: {o.get("shot_type")}; ângulo: {o.get("camera_angle")}; movimento: '
                f'{o.get("camera_movement")}.')))
        ctx.scene_ids = [s.id for s in scenes]
        ctx.suggestions = ["Que cenas ainda não têm planos?"]
        return ctx

    stage = obj_id if kind == "stage" and obj_id in STAGE_NAMES else 1
    return Context(key, STAGE_NAMES[stage], stage, base + "\n\n" + assets_summary(session, project)
                   + "\n\nNESTA PÁGINA NÃO HÁ CAMPOS PARA PROPOR: responde só em texto e, se o utilizador quiser mudar "
                     "alguma coisa, diz-lhe em que página se faz (a do plano, da cena ou do asset).")


def is_en(fs: FieldSpec) -> bool:
    return fs.kind in (EN, "prompt")


# ── Modelo e resolução de vídeo (plano e projeto) ────────────

def _video_line(session: Session, project: Project, shot: Shot, model: dict | None) -> str:
    """«Modelo: X (padrão / escolhido no plano) · Resolução: Y · Custo estimado: Z»."""
    from app import video_cost
    from app.options import video_settings
    from app.routes.video import shot_resolution

    res = shot_resolution(session, shot, model)
    args = video_cost.shot_args(session, shot)  # o mesmo pedido (e a mesma cotação) que o ecrã de vídeo do plano
    q = video_cost.shot_cached(args)
    if q is None:
        video_cost.shot_prefetch(args)
    return (f"Modelo: {(model or {}).get('name') or '—'} ({'escolhido no plano' if shot.model else 'padrão do projeto'}) · "
            f"Resolução: {res} ({'escolhida no plano' if shot.video_resolution else 'padrão do projeto'}) · "
            f"Duração: {_v(shot.duration_s)} s · Custo estimado: "
            + (q["text"] if q else "por cotar (a página de vídeo do plano pede-o ao fornecedor; ou volta a abrir este ecrã "
                                   "daqui a ~30 s)"))


def _video_options(session: Session) -> list[str]:
    from app.routes.video import video_models

    return ["default"] + [m["name"] for m in video_models(session)]


def _video_fields(ctx, session: Session, project: Project, shot: Shot, model: dict | None) -> None:
    from app.routes.video import RESOLUTIONS, project_model, project_resolution, resolutions_of

    pm = project_model(session, project)
    ctx.add(FieldSpec(f"shot:{shot.id}.video_model", f"C{session.get(Scene, shot.scene_id).number:02d} › P{shot.number:02d} › Modelo de vídeo",
                      "video_model", current=shot.model or "default", options=_video_options(session), help=(
                          f"«default» = o padrão do projeto ({(pm or {}).get('name') or '—'}), ou o nome exato de um "
                          "modelo da lista. A duração do plano ajusta-se às que o modelo aceita.")))
    ok = resolutions_of(model)
    ctx.add(FieldSpec(f"shot:{shot.id}.video_resolution", f"C{session.get(Scene, shot.scene_id).number:02d} › P{shot.number:02d} › Resolução",
                      "video_resolution", current=shot.video_resolution or "default", options=["default", *ok], help=(
                          f"«default» = a do projeto ({project_resolution(session, project)}), ou uma das que o modelo "
                          f"do plano aceita: {', '.join(ok)}.")))


def _project_video_fields(ctx, session: Session, project: Project) -> None:
    from app.routes.video import RESOLUTIONS, project_model, project_resolution

    pm = project_model(session, project)
    ctx.add(FieldSpec("project.video_model", "Vídeo › Modelo padrão do projeto", "video_model",
                      current=(pm or {}).get("name"), options=_video_options(session)[1:], help=(
                          "O modelo de vídeo dos planos que não escolheram outro (nome exato da lista).")))
    ctx.add(FieldSpec("project.video_resolution", "Vídeo › Resolução padrão do projeto", "video_resolution",
                      current=project_resolution(session, project), options=list(RESOLUTIONS), help=(
                          "480p, 720p ou 1080p; cada plano usa a mais próxima que o seu modelo aceite.")))
    ctx.add(FieldSpec("project.image_resolution", "Imagens › Resolução padrão do projeto", "video_resolution",
                      current=project.image_resolution or "2K", options=["1K", "2K", "4K"], help=(
                          "1K, 2K ou 4K: a resolução das imagens geradas (folhas, retratos, looks, vistas, previs, capa). "
                          "Cada modelo usa a mais próxima que aceita; generate_image aceita outra em resolution.")))


def _inputs_block(session: Session, shot: Shot, model: dict | None) -> str:
    """Tudo o que a app pode anexar ao pedido de vídeo, com o estado (ligado/desligado) e o modo de cada item."""
    from app import video_inputs as VI

    st = VI.items(session, shot, model)
    lines = [f"IMAGENS QUE SEGUEM NO PEDIDO DE VÍDEO (pedido {'imagem-para-vídeo: só as frames e o prompt' if st['mode'] == 'frames' else 'por referências'}; "
             "as ligadas vão anexadas; refere cada uma pelo marcador, a app troca-o pela sintaxe do modelo; muda-se com o "
             "campo video_inputs):"]
    g = st["guide"]
    lines.append(f"- @Video1 vídeo-guia da cena 3D: {'LIGADO' if g['on'] else 'DESLIGADO'}"
                 + (f" ({g['why']})" if not g["on"] and g.get("why") else ""))
    for it in st["images"]:
        lines.append(f"- {it['token']} = {it['desc']} [{it['group']}]: {'LIGADO' if it['on'] else 'DESLIGADO'}"
                     + (f" · {VI.MODE_LABELS[it['mode']].lower()}" if it["on"] else "")
                     + (f" ({it['why']})" if it.get("why") else ""))
    for v in st["voices"]:
        lines.append(f"- {v['token']} = voz de referência de {v['asset'].label or v['asset'].name}: "
                     f"{'LIGADA' if v['on'] else 'DESLIGADA'}" + (f" ({v['why']})" if v.get("why") else ""))
    return "\n".join(lines)


def _timing_line(session: Session, scene: Scene, shot: Shot) -> str:
    """A duração do plano, o troço da encenação, as durações que o modelo aceita e o estado do guia e dos previs."""
    from app import moments, scene3d
    from app.models import MediaFile
    from app.routes.storyboard import fmt_durations, shot_durations

    start, dur = scene3d.shot_window(shot)
    stage_d = float((scene.stage3d or {}).get("duration") or 0)
    from app.media_service import media_list

    guide = session.get(MediaFile, shot.guide_video_id) if shot.guide_video_id else None
    pending = None if guide else next((m for m in reversed(media_list(session, shot_id=shot.id, role="guide"))
                                       if m.status == "pending"), None)
    which = guide or pending
    label = "" if guide else f"por aprovar (guide_id {pending.id}) " if pending else ""
    if which is None:
        g = "sem vídeo-guia"
    elif which.duration_ms and shot.duration_s and abs(which.duration_ms - shot.duration_s * 1000) > 50:
        g = (f"{label}DESATUALIZADO (gravado com {which.duration_ms / 1000:g} s; o plano tem {shot.duration_s} s): grava-o "
             "de novo")
    elif not guide:
        g = f"{label}em dia"
    elif moments.guide_hash(shot, which.id) not in (None, scene3d.preview_hash(scene3d.compose(session, scene.project_id, shot=shot))):
        g = "DESATUALIZADO (a encenação, a câmara ou o troço mudaram depois de o gravares): grava-o de novo"
    else:
        g = "em dia"
    rows = moments.rows(session, shot) if shot.scene3d else []
    prev = ("sem momentos" if not rows else "em dia" if all(r["render_fresh"] for r in rows)
            else "DESATUALIZADOS (render_maquete sb_shot:%d)" % shot.id)
    return (f"DURAÇÃO DO PLANO: {_v(shot.duration_s)} s · troço {start:g}–{start + dur:g} s da encenação"
            + (f" ({stage_d:g} s)" if stage_d else "") + f" · o modelo aceita {fmt_durations(shot_durations(session, shot))}"
            f" (campo shot:{shot.id}.duration) · vídeo-guia: {g} · previs: {prev}")


def _duration_field(ctx, session: Session, scene: Scene, shot: Shot) -> None:
    from app.routes.storyboard import fmt_durations, shot_durations

    allowed = shot_durations(session, shot)
    ctx.add(FieldSpec(f"shot:{shot.id}.duration", f"C{scene.number:02d} › P{shot.number:02d} › Duração", "shot_duration",
                      current=str(shot.duration_s or ""), options=[str(d) for d in allowed], help=(
                          f"Segundos inteiros, uma das que o modelo de vídeo do plano aceita: {fmt_durations(allowed)}. "
                          "Os planos seguintes da cena andam com a diferença (o troço de cada um na encenação); se o fim "
                          "passar a encenação, dá erro — alonga-a com scene_duration no ecrã sb_scene. O vídeo-guia e os "
                          "previs do plano (e dos seguintes) ficam por refazer.")))
