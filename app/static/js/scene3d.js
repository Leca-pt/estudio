// Maquete 3D: desenha no navegador a cena escrita pelo Assistente (app/scene3d.py), anima-a, tira as imagens dos
// painéis e grava o vídeo-guia. Coordenadas da cena: metros, Z para cima, Y para a frente; no three.js, Y para cima.
import * as THREE from '../vendor/three/three.module.min.js';
import { OrbitControls } from '../vendor/three/OrbitControls.js';
import { applyPose, hasWindows, label, mannequin, mat, piece, windowMat } from './scene3d_parts.js?v=4';
import { groundFn, height, rockGeo, scatterMesh, terrainMesh } from './terrain.js?v=3';
import { greyModel, modelsReady } from './model3d.js?v=2';
export { groundFn, modelsReady };

const live = new Set();
const CAPTURE = [1280, 720];
export const SKY = 0xcfd6de;

// (x, y, z) da cena → three.js
export const v3 = (p) => new THREE.Vector3(p[0], p[2], -p[1]);
const rad = (d) => (d || 0) * Math.PI / 180;

function rng(seed) { // mulberry32: quarteirões iguais sempre que a cena é desenhada
  let a = seed >>> 0;
  return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
}

// Interpolação suave (Hermite com tangentes Catmull-Rom; paragem suave nas pontas) para a câmara
function hermite(keys, t, get) {
  if (!keys.length) return null;
  if (t <= keys[0].t) return get(keys[0]).slice();
  const last = keys[keys.length - 1];
  if (t >= last.t) return get(last).slice();
  let i = 0;
  while (i < keys.length - 2 && t > keys[i + 1].t) i++;
  const k0 = keys[i], k1 = keys[i + 1], dt = (k1.t - k0.t) || 1e-6, s = (t - k0.t) / dt;
  const tan = (j) => {
    if (j <= 0 || j >= keys.length - 1) return get(keys[j]).map(() => 0);
    const a = keys[j - 1], b = keys[j + 1];
    return get(b).map((x, n) => (x - get(a)[n]) / ((b.t - a.t) || 1e-6));
  };
  const m0 = tan(i), m1 = tan(i + 1), p0 = get(k0), p1 = get(k1);
  const h00 = 2 * s ** 3 - 3 * s ** 2 + 1, h10 = s ** 3 - 2 * s ** 2 + s, h01 = -2 * s ** 3 + 3 * s ** 2, h11 = s ** 3 - s ** 2;
  return p0.map((x, n) => h00 * x + h10 * dt * m0[n] + h01 * p1[n] + h11 * dt * m1[n]);
}

export function linear(keys, t) { // posição e direção de um ator
  if (t <= keys[0].t || keys.length === 1) return { pos: keys[0].pos, i: 0 };
  const last = keys[keys.length - 1];
  if (t >= last.t) return { pos: last.pos, i: keys.length - 2 };
  let i = 0;
  while (i < keys.length - 2 && t > keys[i + 1].t) i++;
  const a = keys[i], b = keys[i + 1], s = (t - a.t) / ((b.t - a.t) || 1e-6);
  return { pos: a.pos.map((x, n) => x + (b.pos[n] - x) * s), i, s };
}

function speedAt(keys, t) {
  if (keys.length < 2) return 0;
  const { i } = linear(keys, t);
  const a = keys[i], b = keys[i + 1] || a;
  if (t < keys[0].t || t > keys[keys.length - 1].t) return 0;
  const dt = (b.t - a.t) || 1e-6;
  return Math.hypot(b.pos[0] - a.pos[0], b.pos[1] - a.pos[1]) / dt;
}

function distAt(keys, t) {
  let d = 0;
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (t <= a.t) break;
    const s = Math.min(1, (t - a.t) / ((b.t - a.t) || 1e-6));
    d += Math.hypot(b.pos[0] - a.pos[0], b.pos[1] - a.pos[1]) * s;
  }
  return d;
}

export function facingAt(keys, t) {
  const withF = keys.filter((k) => k.facing !== undefined);
  if (withF.length) {
    if (withF.length === 1 || t <= withF[0].t) return withF[0].facing;
    const lastF = withF[withF.length - 1];
    if (t >= lastF.t) return lastF.facing;
    let i = 0;
    while (i < withF.length - 2 && t > withF[i + 1].t) i++;
    const a = withF[i], b = withF[i + 1], s = (t - a.t) / ((b.t - a.t) || 1e-6);
    let d = ((b.facing - a.facing + 540) % 360) - 180;
    return a.facing + d * s;
  }
  if (keys.length > 1) { // sem "facing": vira-se para onde anda
    const { i } = linear(keys, t);
    const a = keys[i], b = keys[i + 1] || keys[i];
    const dx = b.pos[0] - a.pos[0], dy = b.pos[1] - a.pos[1];
    if (Math.abs(dx) + Math.abs(dy) > 1e-3) return Math.atan2(-dx, dy) * 180 / Math.PI;
  }
  return 0;
}

// Prisma triangular de 1 × 1 × 1 centrado: a secção (x, altura) é o triângulo dado, ao comprido da profundidade.
function prismGeo(tri) {
  const shape = new THREE.Shape(tri.map(([x, y]) => new THREE.Vector2(x, y)));
  const g = new THREE.ExtrudeGeometry(shape, { depth: 1, bevelEnabled: false });
  g.translate(0, 0, -0.5);
  return g;
}

// Polígono em planta (pontos [x, y] dos dados) levantado de z até z + altura.
function extrudeMesh(o, material) {
  const shape = new THREE.Shape(o.points.map(([x, y]) => new THREE.Vector2(x, y)));
  const g = new THREE.ExtrudeGeometry(shape, { depth: o.height, bevelEnabled: false });
  g.rotateX(-Math.PI / 2); // o plano da forma passa a ser o chão (x, -y) e a altura sobe
  const m = new THREE.Mesh(g, material);
  m.position.y = o.z || 0;
  return m;
}

class Batch { // caixas e cilindros por cor, desenhados em lote (milhares de prédios sem custo)
  constructor() { this.items = new Map(); this.dummy = new THREE.Object3D(); }
  add(kind, color, fn, windows = false) {
    const key = kind + color + (windows ? 'w' : '');
    if (!this.items.has(key)) this.items.set(key, { kind, color, windows, mats: [] });
    fn(this.dummy);
    this.dummy.updateMatrix();
    this.items.get(key).mats.push(this.dummy.matrix.clone());
    this.dummy.position.set(0, 0, 0); this.dummy.rotation.set(0, 0, 0); this.dummy.scale.set(1, 1, 1);
  }
  build(parent) {
    const geos = { box: new THREE.BoxGeometry(1, 1, 1), cyl: new THREE.CylinderGeometry(1, 1, 1, 16),
      gable: prismGeo([[-0.5, -0.5], [0.5, -0.5], [0, 0.5]]), ramp: prismGeo([[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5]]) };
    for (const { kind, color, windows, mats } of this.items.values()) {
      const mesh = new THREE.InstancedMesh(geos[kind], windows ? windowMat(color) : mat(color), mats.length);
      mats.forEach((m, n) => mesh.setMatrixAt(n, m));
      parent.add(mesh);
    }
  }
}

export function buildLayer(layer, palette, idx, ground = null) {
  const group = new THREE.Group();
  const off = layer.offset || [0, 0, 0];
  group.position.copy(v3(off));
  group.rotation.y = rad(layer.rot_z);
  const batch = new Batch();
  const col = (c) => new THREE.Color(palette[c] || c).getHex();
  const rand = rng((layer.seed || 7) + idx * 101);
  const box = (center, size, color, rotZ = 0, windows = false) => batch.add('box', col(color), (d) => {
    d.position.copy(v3(center)); d.rotation.y = rotZ; d.scale.set(size[0], size[2], size[1]);
  }, windows);
  let zoneN = 0;
  for (const o of layer.objects || []) {
    if (o.type === 'box') box(o.center, o.size, o.color, rad(o.rot_z), hasWindows(o.color) && !o.plain && o.size[2] > 3.5);
    else if (o.type === 'prism') batch.add(o.shape === 'ramp' ? 'ramp' : 'gable', col(o.color), (d) => {
      d.position.copy(v3(o.center)); d.rotation.y = rad(o.rot_z); d.scale.set(o.size[0], o.size[2], o.size[1]);
    });
    else if (o.type === 'terrain') group.add(terrainMesh(o, mat(col(o.color))));
    else if (o.type === 'rock') { // rocha irregular, assente no chão (z = acima do chão)
      const [w, dd, h] = o.size, g0 = ground ? ground(o.center[0], o.center[1]) : 0;
      const m = new THREE.Mesh(rockGeo(o.seed, o.shape), mat(col(o.color)));
      m.position.copy(v3([o.center[0], o.center[1], g0 + (o.center[2] || 0) + h * 0.12]));
      m.rotation.y = rad(o.rot_z); m.scale.set(w / 2, h / 1.3, dd / 2);
      if (o.label) m.userData.info = `rocha «${o.label}»`;
      group.add(m);
    } else if (o.type === 'scatter') group.add(scatterMesh(o, ground, mat(col(o.color)), v3));
    else if (o.type === 'model') { // modelo 3D importado (GLB) de um asset, em cinzento; sem ficheiro, uma caixa com o nome
      const base = o.center.length === 2 ? [o.center[0], o.center[1], ground ? ground(o.center[0], o.center[1]) : 0] : o.center;
      let m;
      if (o.url) m = greyModel(o.url, mat(col(o.color)), o.size, o.height);
      else {
        const sz = o.size || [1, 1, o.height || 1];
        m = new THREE.Group(); const b = new THREE.Mesh(new THREE.BoxGeometry(sz[0], sz[2], sz[1]), mat(col(o.color)));
        b.position.y = sz[2] / 2; m.add(b);
        const tag = label(o.asset + ' · sem modelo 3D'); tag.position.y = sz[2] + 0.6; tag.layers.set(1); m.add(tag);
      }
      m.position.copy(v3(base)); m.rotation.y = rad(o.rot_z);
      m.userData.info = `${o.asset}${o.label ? ' «' + o.label + '»' : ''} (modelo 3D) em [${o.center.map((x) => +x.toFixed(2)).join(', ')}]`;
      group.add(m);
    }
    else if (o.type === 'extrude') {
      const c = col(o.color);
      group.add(extrudeMesh(o, hasWindows(o.color) && !o.plain && o.height > 3.5 ? windowMat(c) : mat(c)));
    }
    else if (o.type === 'piece') {
      const pc = piece(o.model, col(o.color), o.size);
      pc.position.copy(v3(o.center)); pc.rotation.y = rad(o.rot_z);
      pc.userData.info = `${o.model}${o.label ? ' «' + o.label + '»' : ''} em [${o.center.map((x) => +x.toFixed(2)).join(', ')}]`;
      group.add(pc);
    } else if (o.type === 'zone') { // divisão: chão ligeiramente diferente + nome (só na vista livre)
      const [x0, y0] = o.from, [x1, y1] = o.to;
      const floor = new THREE.Mesh(new THREE.PlaneGeometry(x1 - x0, y1 - y0), mat(zoneN++ % 2 ? '#86807a' : '#7a756f'));
      floor.rotation.x = -Math.PI / 2; floor.position.copy(v3([(x0 + x1) / 2, (y0 + y1) / 2, (o.z || 0) + 0.005]));
      group.add(floor);
      const area = (x1 - x0) * (y1 - y0), ceil = o.ceiling ?? (area < 400 ? 2.9 : 0);
      if (ceil > 0) { // teto: só se vê de dentro (de cima é transparente), para os renders dos interiores não terem céu
        const top = new THREE.Mesh(new THREE.PlaneGeometry(x1 - x0, y1 - y0), mat('#c9c5bd'));
        top.rotation.x = Math.PI / 2; top.position.copy(v3([(x0 + x1) / 2, (y0 + y1) / 2, (o.z || 0) + ceil]));
        group.add(top);
      }
      const tag = label(o.name);
      tag.position.copy(v3([(x0 + x1) / 2, (y0 + y1) / 2, (o.z || 0) + 2.4])); tag.layers.set(1);
      tag.userData.floorZ = o.z || 0;
      group.add(tag);
    }
    else if (o.type === 'cylinder') batch.add('cyl', col(o.color), (d) => { d.position.copy(v3(o.center)); d.scale.set(o.radius, o.height, o.radius); });
    else if (o.type === 'blocks') {
      const [x0, y0] = o.from, [x1, y1] = o.to, [lw, ld] = o.lot, gap = o.gap ?? 2;
      for (let x = x0; x < x1; x += lw) {
        const w = Math.min(lw, x1 - x);
        for (let y = y0; y < y1; y += ld) {
          const d = Math.min(ld, y1 - y), h = o.height[0] + rand() * (o.height[1] - o.height[0]);
          const bw = w - gap, bd = d - gap, built = bw > 0.2 && bd > 0.2;
          if (built) box([x + w / 2, y + d / 2, h / 2], [bw, bd, h], o.color, 0, hasWindows(o.color) && !o.plain);
          // Equipamento de telhado (2 × 2 × 3 m): só em cima de um prédio onde caiba, sempre dentro do telhado. Os
          // números aleatórios gastam-se sempre, para as alturas dos prédios seguintes não mudarem.
          for (let r = 0; r < (o.roof_bits || 0); r++) {
            const rx = rand(), ry = rand();
            if (!built || bw < 3 || bd < 3) continue;
            box([x + gap / 2 + 1 + rx * (bw - 2), y + gap / 2 + 1 + ry * (bd - 2), h + 1.5], [2, 2, 3], o.roof_color || 'roof');
          }
        }
      }
    } else if (o.type === 'pillars') {
      for (let n = 0; n < o.count; n++) {
        const s = o.count > 1 ? n / (o.count - 1) : 0;
        const p = [o.from[0] + (o.to[0] - o.from[0]) * s, o.from[1] + (o.to[1] - o.from[1]) * s, o.height / 2];
        batch.add('cyl', col(o.color), (d) => { d.position.copy(v3(p)); d.scale.set(o.radius, o.height, o.radius); });
      }
    } else if (o.type === 'beam') {
      const a = v3(o.from), b = v3(o.to);
      batch.add('box', col(o.color), (d) => { d.position.copy(a.clone().add(b).multiplyScalar(0.5)); d.lookAt(b); d.scale.set(o.width, o.thickness, a.distanceTo(b)); });
    }
  }
  batch.build(group);
  return group;
}

// Com relevo, a altura das pessoas e dos veículos conta a partir do chão por baixo delas.
export const onGround = (ground, pos) => (ground ? [pos[0], pos[1], (pos[2] || 0) + ground(pos[0], pos[1])] : pos);
// O chão plano à volta: com relevo, tem um buraco à medida dos terrenos e fica à altura média da borda deles (a
// paisagem continua sem degrau); sem relevo, é o plano de sempre a 0.
export function groundPlane(layers, size, color, palette = null) {
  const ts = [];
  for (const l of layers || []) for (const t of l.objects || []) if (t.type === 'terrain') ts.push(t);
  const m = new THREE.MeshLambertMaterial({ color: ts.length && palette ? (palette[ts[0].color] || ts[0].color) : color }); // com relevo, a cor do terreno
  if (!ts.length) {
    const g = new THREE.Mesh(new THREE.PlaneGeometry(size[0], size[1]), m);
    g.rotation.x = -Math.PI / 2; g.position.y = -0.02;
    return g;
  }
  const x0 = Math.min(...ts.map((t) => t.from[0])), y0 = Math.min(...ts.map((t) => t.from[1]));
  const x1 = Math.max(...ts.map((t) => t.to[0])), y1 = Math.max(...ts.map((t) => t.to[1]));
  const ground = groundFn(layers);
  let sum = 0, n = 0;
  for (let i = 0; i <= 40; i++) {
    const fx = x0 + (x1 - x0) * i / 40, fy = y0 + (y1 - y0) * i / 40;
    for (const [x, y] of [[fx, y0], [fx, y1], [x0, fy], [x1, fy]]) { sum += ground(x, y); n++; }
  }
  const W = Math.max(size[0], (x1 - x0) * 4), D = Math.max(size[1], (y1 - y0) * 4), cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  const shape = new THREE.Shape([[cx - W / 2, cy - D / 2], [cx + W / 2, cy - D / 2], [cx + W / 2, cy + D / 2], [cx - W / 2, cy + D / 2]].map(([x, y]) => new THREE.Vector2(x, y)));
  shape.holes.push(new THREE.Path([[x0, y0], [x0, y1], [x1, y1], [x1, y0]].map(([x, y]) => new THREE.Vector2(x, y))));
  const g = new THREE.Mesh(new THREE.ShapeGeometry(shape), m);
  g.rotation.x = -Math.PI / 2; // (x, y da cena) → chão do three (x, -z)
  g.position.y = sum / n - 0.05;
  return g;
}

export function actorMesh(a) { // o elenco: cilindro de 1,75 m com faixa na frente; carros e comboio: caixas
  const g = new THREE.Group(), mat = new THREE.MeshLambertMaterial({ color: a.hex });
  const add = (geo, m, pos) => { const mesh = new THREE.Mesh(geo, m); mesh.position.copy(v3(pos)); g.add(mesh); return mesh; };
  if (a.model_url && a.kind !== 'character' && a.kind !== 'person') { // adereço ou veículo com modelo 3D importado
    g.add(greyModel(a.model_url, mat, a.size || null, a.size ? null : null));
  } else if (a.kind === 'character' || a.kind === 'person') {
    const man = mannequin(a.hex, a.size ? a.size[2] : 1.75, a.kind === 'person' && !a.code); // figurante sem código: cinzento
    g.add(man.root); g.userData.man = man;
  } else if (a.kind === 'car') {
    g.add(piece('car', a.hex, a.size));
  } else if (a.kind === 'train') {
    const size = a.size || [3, 12, 3.2];
    for (let c = 0; c < (a.cars || 4); c++) add(new THREE.BoxGeometry(size[0], size[2], size[1]), mat, [0, -c * (size[1] + 0.6), size[2] / 2]);
  } else {
    const size = a.size || (a.kind === 'vehicle' ? [2.5, 6, 2.5] : [0.5, 0.5, 0.5]);
    add(new THREE.BoxGeometry(size[0], size[2], size[1]), mat, [0, 0, size[2] / 2]);
  }
  return g;
}

export function fovFor(lens, aspect) { // lente em mm (sensor de 36 mm na largura) → campo de visão vertical do three.js
  const h = 2 * Math.atan(18 / lens);
  return 2 * Math.atan(Math.tan(h / 2) / aspect) * 180 / Math.PI;
}

const fmt = (s) => s.toFixed(1).replace('.', ',') + ' s';
// A câmara de cada maquete (vista livre, câmara à mão): quando a página se atualiza (renders prontos, vista guardada,
// mudança do Assistente) a maquete é desenhada de novo e continua onde estava, em vez de voltar ao início.
const camMemory = new Map();
const editMemory = new Map(); // a posição da câmara do plano em edição (sobrevive a uma atualização da página)
const listOpen = new Map(); // a lista das posições da câmara aberta ou fechada (idem)

export function mount(el) {
  for (const v of live) if (!v.el.isConnected) { v.dispose(); live.delete(v); }
  if (el.dataset.mounted) return;
  el.dataset.mounted = '1';
  const spec = JSON.parse(el.querySelector('.m3d-data').textContent || 'null');
  if (!spec) return;
  const moments = JSON.parse(el.querySelector('.m3d-moments')?.textContent || '[]');  // [{n, t}] da câmara do plano
  const locPending = JSON.parse(el.querySelector('.m3d-locpending')?.textContent || '[]');  // vistas novas do local sem render
  const stageMode = !!spec.stage; // encenação da cena: a ação toda, sem câmara própria (cada plano tem a sua)
  const layoutOnly = !stageMode && (!spec.camera || !spec.camera.length);
  let camKeys = spec.camera || [], camOffset = 0;
  const camEdit = !!el.dataset.cameraUrl && !layoutOnly && !stageMode; // editar a câmara do plano à mão
  const walkOn = (layoutOnly && !!el.dataset.viewsBase) || camEdit; // a câmara que se segue: a do plano (ou, na encenação, a do plano escolhido)
  const [aw, ah] = (el.dataset.aspect || '16:9').split(':').map(Number);
  const aspect = aw / ah;

  const stage = el.querySelector('.m3d-stage');
  const canvas = document.createElement('canvas');
  canvas.style.aspectRatio = `${aw} / ${ah}`;
  stage.appendChild(canvas);
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(1);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(SKY);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x5a5a60, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.5);
  sun.position.set(-300, 600, 250);
  scene.add(sun);

  let far = 400;
  const layerGroups = [];
  const groundAt = groundFn(spec.layers); // relevo (null = chão plano a 0)
  spec.layers.forEach((layer, i) => {
    const g = buildLayer(layer, spec.palette, i, groundAt);
    layerGroups.push(g);
    scene.add(g);
    for (const o of layer.objects || []) for (const k of ['center', 'from', 'to']) if (o[k]) far = Math.max(far, Math.abs(o[k][0]) * 2, Math.abs(o[k][1]) * 2);
  });
  const gsize = spec.layers.find((l) => l.ground)?.ground || [far * 3, far * 3];
  const groundMesh = groundPlane(spec.layers, gsize, spec.palette.ground, spec.palette);
  scene.add(groundMesh);
  scene.fog = new THREE.Fog(SKY, far * 0.6, far * 3);

  const actors = spec.actors.map((a) => {
    const m = actorMesh(a); scene.add(m);
    m.userData.info = a.code ? `${a.name || a.code} (${a.code})` : (a.label || a.kind);
    return { a, m };
  });

  // Câmara do plano e vista livre (com o caminho da câmara desenhado)
  const shotCam = new THREE.PerspectiveCamera(40, aspect, 0.1, far * 6);
  const freeCam = new THREE.PerspectiveCamera(45, aspect, 0.1, far * 8);
  freeCam.layers.enable(1);
  const controls = new OrbitControls(freeCam, canvas);
  controls.enableDamping = true;
  const helpers = new THREE.Group();
  helpers.traverse((o) => o.layers.set(1));
  scene.add(helpers);
  let camHelper = null, helperCam = null;
  const camPaths = stageMode ? (spec.shots || []).map((sh) => ({ keys: sh.camera, d: sh.duration, n: sh.n }))
    : (spec.camera && spec.camera.length ? [{ keys: spec.camera, d: spec.duration, n: 0 }] : []);
  if (!layoutOnly) {
    for (const [ci, cp] of camPaths.entries()) { // o caminho da câmara (na encenação, um por plano, com o número)
      const color = stageMode ? [0xff3b8d, 0x1e6bff, 0x18c24a, 0xff7a00, 0x8a2be2, 0x00a88f][ci % 6] : 0xff3b8d;
      const pts = [];
      for (let i = 0; i <= 120; i++) pts.push(v3(hermite(cp.keys, cp.d * i / 120, (k) => k.pos)));
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color }));
      line.layers.set(1); helpers.add(line);
      for (const k of cp.keys) {
        const dot = new THREE.Mesh(new THREE.SphereGeometry(Math.max(0.4, far / 250), 12, 8), new THREE.MeshBasicMaterial({ color: (k.moment || k.panel) ? color : 0x999999 }));
        dot.position.copy(v3(k.pos)); dot.layers.set(1); helpers.add(dot);
      }
      if (stageMode && cp.keys.length) {
        const tag = label(`P${String(cp.n).padStart(2, '0')}`);
        tag.scale.set(1.2, 0.4, 1); tag.position.copy(v3([cp.keys[0].pos[0], cp.keys[0].pos[1], cp.keys[0].pos[2] + 0.6])); tag.layers.set(1);
        helpers.add(tag);
      }
    }
    helperCam = new THREE.PerspectiveCamera(40, aspect, 0.5, Math.max(8, far / 40)); // só para desenhar a câmara do plano
    camHelper = new THREE.CameraHelper(helperCam);
    camHelper.layers.set(1);
    scene.add(camHelper);
  }
  const box = new THREE.Box3().setFromObject(scene.children.find((c) => c.isGroup) || groundMesh);
  const center = box.isEmpty() ? new THREE.Vector3() : box.getCenter(new THREE.Vector3());
  const radius = box.isEmpty() ? 50 : box.getSize(new THREE.Vector3()).length() / 2;
  freeCam.position.copy(center.clone().add(new THREE.Vector3(radius * 0.6, radius * 0.8, radius * 0.9)));
  controls.target.copy(center);

  // Vistas de um local com maquete: cada uma é uma posição de câmara (desenhada na vista livre); dá para ver por ela,
  // guardar novas a partir da vista livre e o navegador tira o render de cada uma.
  const lviews = JSON.parse(el.querySelector('.m3d-lviews')?.textContent || '[]');
  const lvCam = new THREE.PerspectiveCamera(50, aspect, 0.05, far * 6);
  const setLvCam = (cam) => {
    dirty = true;
    lvCam.position.copy(v3(cam.pos)); lvCam.lookAt(v3(cam.look_at)); lvCam.fov = fovFor(cam.lens, aspect); lvCam.updateProjectionMatrix();
    lvCam.userData.cam = cam;
  };
  for (const lv of lviews) { // pirâmide pequena: de onde a vista olha e para onde (ref a magenta, vistas a laranja)
    const c = new THREE.PerspectiveCamera(fovFor(lv.cam.lens, aspect), aspect, 0.2, 1.4);
    c.position.copy(v3(lv.cam.pos)); c.lookAt(v3(lv.cam.look_at)); c.updateMatrixWorld();
    const hy = Math.tan(c.fov * Math.PI / 360) * 0.9, hx = hy * aspect;
    const corners = [[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]].map(([x, y]) => new THREE.Vector3(x, y, -0.9));
    const pts = [];
    for (let k = 0; k < 4; k++) pts.push(new THREE.Vector3(), corners[k], corners[k], corners[(k + 1) % 4]);
    const h = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({ color: lv.kind === 'ref' ? 0xff3b8d : 0xff9a1f }));
    h.position.copy(c.position); h.quaternion.copy(c.quaternion); h.layers.set(1); scene.add(h);
    const tag = label((lv.zone ? lv.zone + ' · ' : '') + lv.label);
    tag.scale.set(1.8, 0.45, 1); tag.position.copy(v3([lv.cam.pos[0], lv.cam.pos[1], lv.cam.pos[2] + 0.5])); tag.layers.set(1); scene.add(tag);
  }

  let t = 0, playing = false, view = layoutOnly || stageMode ? 'free' : 'shot', last = 0, raf = 0;
  let showCams = true;
  if (stageMode) try { showCams = localStorage.getItem('m3d-cams') !== '0'; } catch (e) { /* por omissão, à vista */ }

  // "Câmara à mão" (só nos locais): compor uma vista como quem segura a câmara — arrastar para olhar à volta, andar,
  // subir/descer, mudar a lente. O ecrã mostra exatamente o enquadramento que fica guardado.
  const walkCam = new THREE.PerspectiveCamera(50, aspect, 0.05, far * 6);
  const walk = { pos: new THREE.Vector3(0, 1.6, 0), yaw: 0, pitch: 0, lens: 24 };
  const walkDir = () => new THREE.Vector3(-Math.sin(walk.yaw) * Math.cos(walk.pitch), Math.sin(walk.pitch),
    -Math.cos(walk.yaw) * Math.cos(walk.pitch));
  function applyWalk() {
    if (typeof remember === 'function') remember();
    walkCam.position.copy(walk.pos);
    walkCam.lookAt(walk.pos.clone().add(walkDir()));
    walkCam.fov = fovFor(walk.lens, aspect); walkCam.updateProjectionMatrix();
    const info = el.querySelector('.m3d-walk-info');
    if (info) info.textContent = `${walk.lens.toFixed(0)} mm · câmara a ${walk.pos.y.toFixed(1).replace('.', ',')} m`;
    const lr = el.querySelector('.m3d-lens');
    if (lr && Number(lr.value) !== Math.round(walk.lens)) lr.value = Math.round(walk.lens);
    dirty = true;
  }
  function walkFrom(pos, target, lens) { // pos/target em coordenadas do three.js
    const d = target.clone().sub(pos).normalize();
    walk.pos.copy(pos); walk.yaw = Math.atan2(-d.x, -d.z); walk.pitch = Math.asin(Math.max(-1, Math.min(1, d.y)));
    if (lens) walk.lens = lens;
    applyWalk();
  }
  function floorBelow(x, z, maxY) { // o chão (superfície virada para cima) mais alto por baixo de maxY
    const ray = new THREE.Raycaster(new THREE.Vector3(x, maxY, z), new THREE.Vector3(0, -1, 0));
    const hit = ray.intersectObjects(scene.children, true).find((h) => h.face && h.object.visible
      && h.face.normal.clone().transformDirection(h.object.matrixWorld).y > 0.7);
    return hit ? hit.point.y : 0;
  }
  const cutTop = () => (cut ? cutPlanes[0].constant : Infinity);
  const memKey = el.dataset.viewsBase || el.dataset.preview || '';
  function remember() {
    if (!memKey) return;
    camMemory.set(memKey, { view, t, walk: { pos: walk.pos.toArray(), yaw: walk.yaw, pitch: walk.pitch, lens: walk.lens },
      free: { pos: freeCam.position.toArray(), target: controls.target.toArray() } });
  }
  function startWalk() {
    if (view === 'lv' && lvCam.userData.cam) { // a partir da vista que se está a ver
      const c = lvCam.userData.cam;
      walkFrom(v3(c.pos), v3(c.look_at), c.lens);
    } else if (view === 'shot' && camKeys.length) { // a partir da câmara do plano, no segundo em que se está
      const ct = Math.max(0, t - camOffset);
      walkFrom(v3(hermite(camKeys, ct, (k) => k.pos)), v3(hermite(camKeys, ct, (k) => k.look_at)), hermite(camKeys, ct, (k) => [k.lens])[0]);
    } else if (view !== 'walk') { // a partir da vista livre: à altura dos olhos, no chão por baixo do ponto para onde ela olha
      const p = controls.target.clone();
      const ground = floorBelow(p.x, p.z, Math.min(cutTop() - 0.05, 5000));
      const d = controls.target.clone().sub(freeCam.position); d.y = 0;
      if (d.lengthSq() < 1e-6) d.set(0, 0, -1);
      walk.pos.set(p.x, ground + 1.6, p.z);
      walkFrom(walk.pos.clone(), walk.pos.clone().add(d.normalize()), walk.lens);
    }
    setView('walk');
    say('Câmara à mão: arrasta para olhar à volta · W A S D ou setas para andar · Q / E para descer / subir · roda do '
      + 'rato muda a lente · duplo clique no chão põe lá a câmara (à altura dos olhos). Depois, '
      + (camEdit ? '«Guardar esta posição» ou «Juntar posição aqui».' : '«Guardar vista aqui».'));
  }
  function moveWalk(fwd, side, up) {
    const f = new THREE.Vector3(-Math.sin(walk.yaw), 0, -Math.cos(walk.yaw));
    const r = new THREE.Vector3(Math.cos(walk.yaw), 0, -Math.sin(walk.yaw));
    walk.pos.addScaledVector(f, fwd).addScaledVector(r, side); walk.pos.y = Math.max(0.2, walk.pos.y + up);
    applyWalk();
  }
  const turnWalk = (deg, pitchDeg = 0) => {
    walk.yaw += deg * Math.PI / 180;
    walk.pitch = Math.max(-1.4, Math.min(1.4, walk.pitch + pitchDeg * Math.PI / 180));
    applyWalk();
  };
  const duration = spec.duration || 0;

  function pose(time) {
    for (const { a, m } of actors) {
      const { pos } = linear(a.keys, time);
      m.position.copy(v3(onGround(groundAt, pos))); // com relevo, a altura conta a partir do chão
      m.rotation.y = rad(facingAt(a.keys, time));
      const man = m.userData.man;
      if (man) { // pose da última posição com "pose"; quem se desloca anda (ou corre) sozinho
        const k = [...a.keys].reverse().find((x) => x.t <= time && x.pose);
        const speed = speedAt(a.keys, time);
        let p = k ? k.pose : 'stand';
        if (p === 'stand' && speed > 0.3) p = speed > 3.5 ? 'run' : 'walk';
        const dist = distAt(a.keys, time);
        applyPose(man, p, (p === 'run' ? dist / 2.6 : p === 'walk' ? dist / 1.4 : time) * Math.PI * 2 + (p === 'walk' && speed <= 0.3 ? time * Math.PI * 3.6 : 0));
      }
    }
    if (!layoutOnly && camKeys.length) {
      const ct = Math.max(0, time - camOffset);
      shotCam.position.copy(v3(hermite(camKeys, ct, (k) => k.pos)));
      shotCam.lookAt(v3(hermite(camKeys, ct, (k) => k.look_at)));
      shotCam.fov = fovFor(hermite(camKeys, ct, (k) => [k.lens])[0], aspect);
      shotCam.updateProjectionMatrix();
      if (helperCam) {
        helperCam.position.copy(shotCam.position); helperCam.quaternion.copy(shotCam.quaternion);
        helperCam.fov = shotCam.fov; helperCam.updateProjectionMatrix(); helperCam.updateMatrixWorld(); camHelper.update();
      }
    }
  }

  // Desenhar só quando alguma coisa muda (câmara, tempo, vista, piso, tamanho) e com a maquete à vista: desenhar
  // 60 vezes por segundo sem parar ocupava o browser e deixava a página lenta a responder aos cliques.
  let dirty = true, onScreen = true;
  const invalidate = () => { dirty = true; };
  controls.addEventListener('change', invalidate);
  new IntersectionObserver(([e]) => { onScreen = e.isIntersecting; if (onScreen) invalidate(); }).observe(stage);
  document.addEventListener('visibilitychange', invalidate);
  function size(w, h) { renderer.setSize(w, h, false); invalidate(); }
  function fit() { const w = Math.max(320, Math.round(stage.clientWidth)); size(w, Math.round(w / aspect)); }

  // "Ver por dentro": na vista livre, corta tudo o que fica 2,6 m acima do chão de um piso (tetos, telhados, os andares
  // de cima), como uma planta. Os pisos são as alturas ("z") das divisões com teto. Só no ecrã: os renders das vistas,
  // dos painéis e o vídeo-guia veem sempre a maquete inteira.
  // Pisos: as alturas de todas as divisões cobertas e as divisões ao ar livre acima do chão (terraços, telhados).
  const zonesAll = spec.layers.flatMap((l) => (l.objects || []).filter((o) => o.type === 'zone'));
  const covered = (o) => (o.ceiling ?? ((o.to[0] - o.from[0]) * (o.to[1] - o.from[1]) < 400 ? 2.9 : 0)) > 0;
  const zr = (o) => Math.round((o.z || 0) * 10) / 10;
  const levels = [...new Set(zonesAll.filter((o) => covered(o) || zr(o) > 0.5).map(zr))].sort((a, b) => a - b);
  const roofed = levels.some((z) => zonesAll.some((o) => covered(o) && zr(o) === z));
  const ground0 = levels.reduce((best, z, i) => (Math.abs(z) < Math.abs(levels[best]) ? i : best), 0);
  const levelName = (i) => { // pela altura (3 m por andar) e com as divisões desse piso, para se saber o que é
    const z = levels[i], n = Math.abs(z) < 1 ? 0 : Math.round(z / 3);
    const name = n === 0 ? 'R/C' : n < 0 ? `Cave${n < -1 ? ' ' + n : ''}` : `${n}.º andar`;
    const here = zonesAll.filter((o) => zr(o) === z).map((o) => o.name).filter(Boolean);
    return `${name} (${z.toLocaleString('pt-PT')} m)` + (here.length ? ` · ${here.slice(0, 4).join(', ')}${here.length > 4 ? '…' : ''}` : '');
  };
  const cutPlanes = [new THREE.Plane(new THREE.Vector3(0, -1, 0), 2.6)];
  const floorTags = [];
  scene.traverse((o) => { if (o.userData.floorZ !== undefined) floorTags.push(o); });
  const tagsFor = (z) => floorTags.forEach((g) => { g.visible = z === null || Math.abs(g.userData.floorZ - z) < 0.5; });
  const cutAt = (z) => { cutPlanes[0].constant = z + 2.6; dirty = true; };
  let cut = false;
  try {
    const saved = localStorage.getItem('m3d-cut');
    cut = roofed && saved !== '0';
    const lv = levels.indexOf(Number(saved));
    cutAt(levels[lv >= 0 ? lv : ground0] ?? 0);
  } catch (e) { cut = roofed; cutAt(levels[ground0] ?? 0); }
  function draw() {
    pose(t);
    if (camHelper) camHelper.visible = view === 'free' && camKeys.length > 0 && showCams;
    helpers.visible = showCams;
    renderer.clippingPlanes = cut && view === 'free' ? cutPlanes : [];
    tagsFor(cut && view === 'free' && levels.length > 1 ? cutPlanes[0].constant - 2.6 : null);
    renderer.render(scene, view === 'shot' ? shotCam : view === 'lv' ? lvCam : view === 'walk' ? walkCam : freeCam);
  }

  // Barra: play, tempo, vistas, painéis, legenda
  const bar = el.querySelector('.m3d-bar');
  const q = (s) => el.querySelector(s);
  bar.innerHTML = (layoutOnly ? '' : `<button class="btn sm" type="button" data-m3d="play">▶</button>
    <input class="m3d-range" type="range" min="0" max="${duration}" step="0.04" value="0" aria-label="Tempo">
    <span class="mono m3d-time">${fmt(0)} / ${fmt(duration)}</span>`) +
    `<span class="sp"></span>${layoutOnly ? '' : `<div class="seg m3d-views" role="group"><button type="button" data-m3d="shot" class="on">Câmara do plano</button><button type="button" data-m3d="free">Vista livre</button>${camEdit ? '<button type="button" data-m3d="walk">Câmara à mão</button>' : ''}</div>`}`
    + (layoutOnly && el.dataset.viewsBase ? `<div class="seg m3d-views" role="group"><button type="button" data-m3d="free" class="on">Vista livre</button><button type="button" data-m3d="walk">Câmara à mão</button></div><button class="btn sm pri" type="button" data-m3d="savelv">Guardar vista aqui</button>` : '')
    + (stageMode && camPaths.length ? `<button class="btn sm m3d-cams" type="button" data-m3d="cams" aria-pressed="true" title="Mostrar ou esconder as câmaras dos planos na vista livre">Câmaras</button>` : '')
    + (levels.length === 1 ? `<button class="btn sm m3d-cut" type="button" data-m3d="cut" aria-pressed="${cut}" title="Na vista livre: tira tetos, telhados e andares de cima para ver lá dentro">Ver por dentro</button>` : '')
    + (levels.length > 1 ? `<select class="in m3d-floor" aria-label="Ver por dentro" title="Na vista livre: corta acima do piso escolhido para ver lá dentro">
        <option value="off">Ver tudo (por fora)</option>${levels.map((z, i) => `<option value="${z}"${cut && cutPlanes[0].constant === z + 2.6 ? ' selected' : ''}>${levelName(i)}</option>`).join('')}</select>` : '');
  if (walkOn) { // controlos da câmara à mão (também para quem não usa o teclado)
    const pad = document.createElement('div');
    pad.className = 'm3d-walk row'; pad.hidden = true;
    pad.innerHTML = `<button class="btn sm" type="button" data-walk="turn:15" title="Rodar para a esquerda (seta ←)">⟲ 15°</button>
      <button class="btn sm" type="button" data-walk="turn:-15" title="Rodar para a direita (seta →)">15° ⟳</button>
      <button class="btn sm" type="button" data-walk="turn:180" title="Virar para o lado oposto">Virar 180°</button>
      <span class="m3d-sep"></span>
      <button class="btn sm" type="button" data-walk="move:0.5:0" title="Andar para a frente (W)">Frente</button>
      <button class="btn sm" type="button" data-walk="move:-0.5:0" title="Andar para trás (S)">Trás</button>
      <button class="btn sm" type="button" data-walk="move:0:-0.5" title="Passo à esquerda (A)">Esq.</button>
      <button class="btn sm" type="button" data-walk="move:0:0.5" title="Passo à direita (D)">Dir.</button>
      <button class="btn sm" type="button" data-walk="up:0.2" title="Subir (E)">Subir</button>
      <button class="btn sm" type="button" data-walk="up:-0.2" title="Descer (Q)">Descer</button>
      <button class="btn sm" type="button" data-walk="tilt:0" title="Pôr a câmara direita (sem inclinação)">Nivelar</button>
      <span class="m3d-sep"></span>
      <label class="row m3d-lens-l" style="gap: 6px">Lente<input class="m3d-lens" type="range" min="12" max="120" step="1" value="24" aria-label="Lente em mm"></label>
      <span class="mono m3d-walk-info"></span>`;
    bar.after(pad);
  }
  q('.m3d-cams')?.setAttribute('aria-pressed', String(showCams));
  const cuts = stageMode ? (spec.shots || []) : [];
  const cutTotal = cuts.reduce((sum, sh) => sum + sh.duration, 0);
  // Montado pelo tempo da ação: a ação corre uma vez e a câmara é a do plano que começou mais recentemente; planos que
  // começam juntos (cobertura do mesmo troço) dividem esse troço em partes iguais, pela ordem dos números.
  const actionCuts = (() => {
    const ends = cuts.flatMap((sh) => [sh.start, sh.start + sh.duration]);
    const bounds = [...new Set([0, duration, ...ends].map((b) => Math.max(0, Math.min(duration, b))))].sort((a, b) => a - b);
    const runs = [];
    for (let k = 0; k + 1 < bounds.length; k++) {
      const a = bounds[k], b = bounds[k + 1], mid = (a + b) / 2;
      const cov = cuts.map((sh, i) => ({ sh, i })).filter(({ sh }) => sh.start <= mid && mid < sh.start + sh.duration);
      const top = Math.max(...cov.map((c) => c.sh.start));
      const group = cov.filter((c) => c.sh.start === top).map((c) => c.i);
      const last = runs[runs.length - 1];
      if (last && last.group.join() === group.join()) last.to = b; else runs.push({ from: a, to: b, group });
    }
    const out = [];
    for (const r of runs) {
      const parts = r.group.length ? r.group : [-1], step = (r.to - r.from) / parts.length;
      parts.forEach((i, j) => {
        const last = out[out.length - 1];
        if (last && last.i === i) last.to = r.from + (j + 1) * step;
        else out.push({ from: r.from + j * step, to: r.from + (j + 1) * step, i });
      });
    }
    return out;
  })();
  // As falas do guião, nos segundos que a encenação lhes dá (legendas do montado)
  const subs = JSON.parse(el.querySelector('.m3d-subs')?.textContent || '[]');
  const chips = q('.m3d-chips');
  if (chips && layoutOnly && lviews.length) { // vistas do local: escolher uma põe a câmara à mão lá
    const esc = (x) => String(x).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const zones = [...new Set(lviews.map((lv) => lv.zone || ''))];
    chips.innerHTML = `<label class="row m3d-lv-l" style="gap: 6px">Vistas<select class="in m3d-lv-pick" aria-label="Ver por uma vista do local">
      <option value="">Escolhe uma vista… (${lviews.length})</option>
      ${zones.map((z) => `<optgroup label="${esc(z || 'Espaço todo')}">${lviews.filter((lv) => (lv.zone || '') === z)
        .map((lv) => `<option value="${lv.id}">${esc(lv.label)}</option>`).join('')}</optgroup>`).join('')}</select></label>`;
  }
  if (chips && stageMode) { // a encenação pela câmara de cada plano, no troço dele
    const whole = Math.abs(cutTotal - duration) > 0.05; // há planos sobrepostos ou buracos: os dois modos diferem
    chips.innerHTML = (cuts.length > 1 ? `<button class="chip m3d-edit" type="button" data-m3d="edit" data-edit="action" title="A ação corre uma vez, do princípio ao fim, e a câmara corta para cada plano quando ele começa; planos que filmam o mesmo troço repartem-no entre si">▶ Montado · ${fmt(duration)}</button>`
      + (whole ? `<button class="chip m3d-edit" type="button" data-m3d="edit" data-edit="shots" title="Cada plano inteiro, um a seguir ao outro, pela ordem dos números: os que filmam o mesmo troço repetem a ação (como montar os takes tal como estão)">▶ Planos inteiros · ${cuts.length} planos · ${fmt(cutTotal)}</button>` : '') : '')
      + (spec.shots || []).map((sh, i) => `<button class="chip" type="button" data-m3d-shot="${i}">P${String(sh.n).padStart(2, '0')} · ${fmt(sh.start)}–${fmt(sh.start + sh.duration)}</button>`).join('')
      || '<span class="hint">Ainda nenhum plano tem câmara.</span>';
  } else if (chips && !layoutOnly) { // os momentos da câmara: um botão por momento, no segundo dele
    chips.innerHTML = moments.map((mo) => `<button class="chip" type="button" data-m3d-t="${Math.min(mo.t, duration)}">Momento ${mo.n} · ${fmt(mo.t)}</button>`).join('');
  }
  // "Montado" (encenação): os planos com câmara seguidos, pela ordem dos números, cada um na sua parte da encenação —
  // a câmara corta de um para o outro, como no filme. A barra de tempo passa a ser a do montado.
  let cutMode = null, mt = 0, cutIdx = -1; // cutMode: null, 'action' (tempo da ação) ou 'shots' (planos inteiros)
  const cutTag = document.createElement('span');
  cutTag.className = 'm3d-cut-tag mono'; cutTag.hidden = true; stage.appendChild(cutTag);
  const subEl = document.createElement('div');
  subEl.className = 'm3d-sub'; subEl.hidden = true; stage.appendChild(subEl);
  const editTotal = () => (cutMode === 'shots' ? cutTotal : duration);
  const pn = (n) => `P${String(n).padStart(2, '0')}`;
  function editAt(v) { // segundo do montado → plano e segundo da encenação
    mt = Math.max(0, Math.min(editTotal(), v));
    let i = -1, label = '';
    if (cutMode === 'shots') {
      let acc = 0; i = 0;
      while (i < cuts.length - 1 && mt >= acc + cuts[i].duration) { acc += cuts[i].duration; i++; }
      t = Math.min(duration, cuts[i].start + (mt - acc));
      label = `${pn(cuts[i].n)} · ${fmt(mt - acc)}`;
    } else {
      t = mt;
      const seg = actionCuts.find((x) => mt < x.to) || actionCuts[actionCuts.length - 1];
      i = seg ? seg.i : -1;
      label = i >= 0 ? `${pn(cuts[i].n)} · ${fmt(t - cuts[i].start)}` : 'sem plano';
    }
    if (i >= 0 && i !== cutIdx) { cutIdx = i; useShot(i); }
    cutTag.textContent = label;
    const sub = subs.find((x) => t >= x.from && (t < x.to || (t >= duration && x.to >= duration))) || null; // a última fala fica até ao fim
    if (sub !== subEl.sub) { // só mexe no texto quando a fala muda
      subEl.sub = sub; subEl.hidden = !sub;
      if (sub) {
        const who = document.createElement('b'), said = document.createElement('span');
        who.textContent = sub.who ? `${sub.who}: ` : ''; said.textContent = sub.text;
        subEl.replaceChildren(who, said);
      }
    }
    invalidate();
    if (range) range.value = mt;
    if (timeEl) timeEl.textContent = `${fmt(mt)} / ${fmt(editTotal())}`;
  }
  function editMode(kind) {
    if (kind === cutMode) return;
    cutMode = kind; cutTag.hidden = !kind; cutIdx = -1;
    if (!kind) { subEl.hidden = true; subEl.sub = null; }
    el.querySelectorAll('.m3d-edit').forEach((b) => b.classList.toggle('on', !!kind && b.dataset.edit === kind));
    if (range) range.max = kind ? editTotal() : duration;
    if (kind) { setView('shot'); editAt(0); } else setT(t);
  }
  function useShot(i) { // encenação: seguir a câmara do plano i, do início do troço dele
    const sh = (spec.shots || [])[i];
    if (!sh) return false;
    camKeys = sh.camera; camOffset = sh.start;
    el.querySelectorAll('[data-m3d-shot]').forEach((b) => b.classList.toggle('on', b.dataset.m3dShot === String(i)));
    return true;
  }
  const legend = q('.m3d-legend');
  if (legend) legend.innerHTML = (spec.legend || []).map((l) => `<span class="m3d-key"><i style="background:${l.hex}"></i>${l.text}</span>`).join('')
    + (layoutOnly ? '' : '<span class="m3d-key"><i style="background:#c4c7c6"></i>figurantes</span><span class="m3d-key dim">viseira = frente</span>');
  const range = q('.m3d-range'), timeEl = q('.m3d-time'), playBtn = q('[data-m3d="play"]');
  if (layoutOnly) { // só o cenário: ainda não há câmara para tirar imagens nem gravar
    el.querySelectorAll('[data-m3d="record"]').forEach((b) => { b.hidden = true; });
    if (el.dataset.base) { const st = q('.m3d-status'); if (st) st.textContent = 'Só o cenário: falta a câmara e quem está onde (pede a cena ao Assistente).'; }
  }

  function setT(v) { invalidate(); t = Math.max(0, Math.min(duration, v)); if (!playing) remember(); if (range) range.value = t; if (timeEl) timeEl.textContent = `${fmt(t)} / ${fmt(duration)}`; }
  function setView(v) {
    invalidate();
    view = v;
    remember();
    controls.enabled = v === 'free';
    const pad = el.querySelector('.m3d-walk');
    if (pad) pad.hidden = v !== 'walk';
    const pick = el.querySelector('.m3d-lv-pick');
    if (pick && v !== 'walk') pick.value = '';
    if (v === 'walk') { // o teclado vai para a maquete, mas sem tirar o foco a quem está a escrever (ex.: no chat)
      canvas.tabIndex = 0;
      const a = document.activeElement;
      if (!a || a === document.body || el.contains(a)) canvas.focus({ preventScroll: true });
    }
    el.querySelectorAll('.m3d-views button').forEach((b) => b.classList.toggle('on', b.dataset.m3d === v));
  }
  function loop(now) {
    if (!canvas.isConnected) { handle.dispose(); live.delete(handle); return; }  // a página trocou a maquete
    raf = requestAnimationFrame(loop);
    if (playing && cutMode) {
      editAt(mt + (now - last) / 1000);
      if (mt >= editTotal()) { playing = false; if (playBtn) playBtn.textContent = '▶'; }
    } else if (playing) {
      setT(t + (now - last) / 1000);
      if (t >= duration) { playing = false; if (playBtn) playBtn.textContent = '▶'; }
    }
    last = now;
    if (!onScreen || document.hidden) return;
    if (view === 'free') controls.update();  // com inércia, continua a avisar "change" até parar
    if (!dirty) return;
    dirty = false;
    draw();
  }
  el.addEventListener('click', (e) => {
    const wb = e.target.closest('[data-walk]');
    if (wb) {
      const [k, a, b] = wb.dataset.walk.split(':');
      if (k === 'turn') turnWalk(Number(a));
      else if (k === 'move') moveWalk(Number(a), Number(b), 0);
      else if (k === 'up') moveWalk(0, 0, Number(a));
      else if (k === 'tilt') { walk.pitch = 0; applyWalk(); }
      canvas.focus({ preventScroll: true });
      return;
    }
    const lvb = e.target.closest('[data-lv]');
    if (lvb) { const lv = lviews.find((x) => String(x.id) === lvb.dataset.lv); if (lv) { setLvCam(lv.cam); setView('lv'); } return; }
    const sb = e.target.closest('[data-m3d-shot]');
    if (sb) {
      editMode(null);
      if (useShot(Number(sb.dataset.m3dShot))) { setT(camOffset); setView('shot'); playing = false; if (playBtn) playBtn.textContent = '▶'; }
      return;
    }
    const b = e.target.closest('[data-m3d], [data-m3d-t]');
    if (!b) return;
    if (b.dataset.m3dT !== undefined) { setT(Number(b.dataset.m3dT)); setView('shot'); playing = false; if (playBtn) playBtn.textContent = '▶'; return; }
    const what = b.dataset.m3d;
    if (what === 'play') {
      if (cutMode) { if (mt >= editTotal()) editAt(0); } else if (t >= duration) setT(0);
      playing = !playing; b.textContent = playing ? '⏸' : '▶';
    }
    else if (what === 'edit') {
      if (cutMode === b.dataset.edit) { if (mt >= editTotal()) editAt(0); } else editMode(b.dataset.edit);
      playing = true; if (playBtn) playBtn.textContent = '⏸';
    }
    else if (what === 'shot' && stageMode && !camKeys.length) { if (useShot(0)) { setT(camOffset); setView('shot'); } }
    else if (what === 'shot' || what === 'free') { if (what === 'free') editMode(null); setView(what); }
    else if (what === 'walk') { editMode(null); startWalk(); }
    else if (what === 'record') record(b);
    else if (what === 'cams') { // mostrar/esconder as câmaras dos planos (caminhos e números) na encenação
      showCams = !showCams; b.setAttribute('aria-pressed', String(showCams)); invalidate();
      try { localStorage.setItem('m3d-cams', showCams ? '1' : '0'); } catch (err) { /* só não fica lembrado */ }
    }
    else if (what === 'savelv') saveView(b);
    else if (what === 'cut') {
      cut = !cut; b.setAttribute('aria-pressed', String(cut)); setView('free');
      try { localStorage.setItem('m3d-cut', cut ? '1' : '0'); } catch (e) { /* só não fica lembrado */ }
    }
  });
  q('.m3d-lv-pick')?.addEventListener('change', (e) => {
    const lv = lviews.find((x) => String(x.id) === e.target.value);
    if (!lv) return;
    setLvCam(lv.cam);
    walkFrom(v3(lv.cam.pos), v3(lv.cam.look_at), lv.cam.lens);
    setView('walk');
    say(`Estás na vista «${lv.label}». Mexe a câmara (arrasta, W A S D, Virar 180°…) e «Guardar vista aqui» cria uma `
      + 'vista nova; a original não muda.');
  });
  q('.m3d-floor')?.addEventListener('change', (e) => {
    cut = e.target.value !== 'off';
    if (cut) cutAt(Number(e.target.value));
    setView('free');
    try { localStorage.setItem('m3d-cut', cut ? e.target.value : '0'); } catch (err) { /* só não fica lembrado */ }
  });
  range?.addEventListener('input', () => { if (cutMode) editAt(Number(range.value)); else setT(Number(range.value)); playing = false; if (playBtn) playBtn.textContent = '▶'; });

  const status = q('.m3d-status');
  const say = (msg) => {  // também no cartão da página, quando a maquete está num modal fechado
    if (status) status.textContent = msg;
    document.querySelectorAll('.m3d-status-out').forEach((o) => { o.textContent = msg; });
  };
  const csrf = () => document.querySelector('input[name="csrf_token"]')?.value || '';
  async function post(url, blob, name) {
    const fd = new FormData();
    fd.append('csrf_token', csrf());
    fd.append('file', blob, name);
    const r = await fetch(url, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf() } });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) throw new Error(j.error || `Erro ${r.status}`);
    return j;
  }

  // Erros dos renders em linguagem de utilizador (o detalhe técnico fica na consola).
  const human = (err) => {
    console.warn(err);
    const m = String(err && err.message || '');
    if (/Erro 5\d\d/.test(m)) return 'o servidor deu um erro; volta a abrir a página para tentar outra vez';
    if (/Erro 4\d\d/.test(m)) return 'a página está desatualizada; recarrega-a e tenta outra vez';
    if (/fetch|network|Failed/i.test(m)) return 'sem ligação ao servidor; verifica a internet e recarrega a página';
    return 'não foi possível; recarrega a página e tenta outra vez';
  };

  async function captureMoments() { // o previs de cada momento: a câmara do plano no segundo do momento, com o elenco
    if (!moments.length || !el.dataset.previewHash) return;
    try { // num ecrã à parte (como os renders das vistas): a maquete à vista não salta enquanto se mexe na câmara
      for (const mo of moments) {
        say(`A tirar o previs do momento ${mo.n} de ${moments.length}…`);
        const blob = await momentShot(mo.t);
        const fd = new FormData();
        fd.append('csrf_token', csrf()); fd.append('hash', el.dataset.previewHash); fd.append('file', blob, `previs_m${mo.n}.png`);
        const resp = await fetch(`${el.dataset.base}/maquete/render/${mo.n}`, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf() } });
        if (!resp.ok) throw new Error(`Erro ${resp.status}`);
      }
      say('');
    } catch (err) { say(`O previs não ficou guardado: ${human(err)}.`); }
  }

  async function momentShot(at) { // o previs de um instante: a câmara do plano nesse segundo, com o elenco
    const { r } = cap();
    r.setSize(...CAPTURE, false); r.clippingPlanes = [];
    const mt = Math.min(at, duration);
    const cc = new THREE.PerspectiveCamera(fovFor(hermite(camKeys, mt, (k) => [k.lens])[0], aspect), CAPTURE[0] / CAPTURE[1], 0.1, far * 6);
    cc.position.copy(v3(hermite(camKeys, mt, (k) => k.pos))); cc.lookAt(v3(hermite(camKeys, mt, (k) => k.look_at)));
    cc.updateProjectionMatrix();
    pose(mt); r.render(scene, cc); pose(t); dirty = true;
    return capBlob();
  }

  async function locRenders() { // vistas novas do local (criadas a partir da câmara do plano): render e profundidade só da maquete
    if (!locPending.length || !el.dataset.locBase || !el.dataset.locHash) return;
    const hidden = [...actors.map(({ m }) => m), ...layerGroups.slice(1), helpers];
    const keep = hidden.map((o) => o.visible);
    hidden.forEach((o) => { o.visible = false; });
    if (camHelper) camHelper.visible = false;
    try {
      for (const [n, lv] of locPending.entries()) {
        say(`A tirar o render da vista nova do local (${n + 1} de ${locPending.length})…`);
        setLvCam(lv.cam); size(...CAPTURE); lvCam.aspect = CAPTURE[0] / CAPTURE[1]; lvCam.updateProjectionMatrix();
        renderer.clippingPlanes = [];
        renderer.render(scene, lvCam);
        const blob = await new Promise((res) => canvas.toBlob(res, 'image/png'));
        depthShot(lvCam, lv.outdoor ? 60 : 15);
        const depth = await new Promise((res) => canvas.toBlob(res, 'image/png'));
        const fd = new FormData();
        fd.append('csrf_token', csrf()); fd.append('hash', el.dataset.locHash); fd.append('file', blob, `vista${lv.id}.png`);
        fd.append('depth', depth, `vista${lv.id}_depth.png`);
        await fetch(`${el.dataset.locBase}/${lv.id}/render`, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf() } });
      }
      say('');
    } catch (err) { say(`O render da vista não ficou guardado: ${human(err)}.`); } finally {
      hidden.forEach((o, i) => { o.visible = keep[i]; });
      fit(); invalidate();
    }
  }

  // As tarefas que usam a câmara e o tamanho da imagem (gravar, previs, renders) correm uma de cada vez: duas ao mesmo
  // tempo mudavam o tamanho a meio de uma gravação, e o Chrome entregava o vídeo vazio.
  let lane = Promise.resolve();
  const exclusive = (fn) => { const run = lane.then(fn, fn); lane = run.catch(() => {}); return run; };

  function record(btn) {
    btn.disabled = true;
    say('A preparar a gravação…');
    return exclusive(() => recordNow(btn));
  }

  async function recordNow(btn) { // grava a animação pela câmara do plano, em tempo real
    await modelsReady();
    const types = ['video/mp4;codecs=avc1', 'video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'];
    const mime = types.find((m) => window.MediaRecorder && MediaRecorder.isTypeSupported(m));
    if (!mime) { btn.disabled = false; return say('Este navegador não consegue gravar vídeo (experimenta o Chrome ou o Edge).'); }
    playing = false; setView('shot'); size(...CAPTURE); setT(0); draw();
    const stream = canvas.captureStream(0);  // 0 = cada imagem entra quando a pedimos (requestFrame), sem depender do ecrã
    const track = stream.getVideoTracks()[0];
    const rec = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 8_000_000 });
    const chunks = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    const done = new Promise((res) => { rec.onstop = res; });
    rec.start(250);
    say(`A gravar… ${fmt(0)} / ${fmt(duration)}`);
    const t0 = performance.now();
    await new Promise((res) => {
      const tick = () => {
        const s = (performance.now() - t0) / 1000;
        setT(Math.min(s, duration)); draw();
        track.requestFrame?.();
        say(`A gravar… ${fmt(t)} / ${fmt(duration)}`);
        if (s >= duration + 0.15) return res();
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    rec.stop(); await done;
    track.stop();
    fit();
    const blob = new Blob(chunks, { type: mime.split(';')[0] });
    if (!blob.size) {
      btn.disabled = false;
      return say('A gravação saiu vazia (o separador ficou escondido ou o browser parou o desenho). Mantém esta página à vista e grava outra vez.');
    }
    try {
      say('A enviar o vídeo-guia…');
      await post(`${el.dataset.base}/maquete/guia`, blob, mime.includes('mp4') ? 'guia.mp4' : 'guia.webm');
      (window.estudioRefresh ? window.estudioRefresh() : location.reload());
    } catch (err) { say(`Não foi possível: ${human(err)}.`); btn.disabled = false; }
  }

  if (walkOn) {
    let drag = null;
    canvas.addEventListener('pointerdown', (e) => { if (view === 'walk') { drag = { x: e.clientX, y: e.clientY }; canvas.setPointerCapture(e.pointerId); } });
    canvas.addEventListener('pointermove', (e) => {
      if (view !== 'walk' || !drag) return;
      turnWalk(-(e.clientX - drag.x) * 0.25, -(e.clientY - drag.y) * 0.25);
      drag = { x: e.clientX, y: e.clientY };
    });
    const stop = () => { drag = null; };
    canvas.addEventListener('pointerup', stop); canvas.addEventListener('pointercancel', stop);
    canvas.addEventListener('wheel', (e) => {
      if (view !== 'walk') return;
      e.preventDefault();
      walk.lens = Math.max(12, Math.min(120, walk.lens * (e.deltaY > 0 ? 0.93 : 1.07))); applyWalk();
    }, { passive: false });
    canvas.addEventListener('keydown', (e) => {
      if (view !== 'walk') return;
      const step = e.shiftKey ? 1.5 : 0.3;
      const k = e.key.toLowerCase();
      const moves = { w: [step, 0, 0], arrowup: [step, 0, 0], s: [-step, 0, 0], arrowdown: [-step, 0, 0],
        a: [0, -step, 0], d: [0, step, 0], e: [0, 0, 0.1], q: [0, 0, -0.1] };
      if (k === 'arrowleft') turnWalk(e.shiftKey ? 15 : 5);
      else if (k === 'arrowright') turnWalk(e.shiftKey ? -15 : -5);
      else if (moves[k]) moveWalk(...moves[k]);
      else return;
      e.preventDefault();
    });
    canvas.addEventListener('dblclick', (e) => { // pôr a câmara no sítio clicado, à altura dos olhos
      if (view !== 'walk' && view !== 'free') return;
      const rect = canvas.getBoundingClientRect();
      const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
      const ray = new THREE.Raycaster(); ray.setFromCamera(ndc, view === 'walk' ? walkCam : freeCam);
      const top = view === 'free' ? cutTop() : Infinity; // com «Ver por dentro», o que está cortado não conta
      const hit = ray.intersectObjects(scene.children, true).find((h) => h.face && h.object.visible && h.point.y < top - 0.05
        && h.face.normal.clone().transformDirection(h.object.matrixWorld).y > 0.7);
      if (!hit) return say('Duplo clique num chão (ou numa laje) para pôr lá a câmara.');
      const keepYaw = view === 'walk';
      if (!keepYaw) { // vindo da vista livre: olha na direção em que a vista livre estava a olhar
        const d = controls.target.clone().sub(freeCam.position); walk.yaw = Math.atan2(-d.x, -d.z); walk.pitch = 0;
      }
      walk.pos.set(hit.point.x, hit.point.y + 1.6, hit.point.z);
      applyWalk(); setView('walk');
    });
    el.querySelector('.m3d-lens')?.addEventListener('input', (e) => { walk.lens = Number(e.target.value); applyWalk(); });
  }
  { // clicar num objeto (sem arrastar): o que é e as coordenadas, para dizer ao Assistente «põe-no aqui»
    let down = null;
    canvas.addEventListener('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY }; });
    canvas.addEventListener('pointerup', (e) => {
      if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) return;
      down = null;
      const cam = view === 'shot' ? shotCam : view === 'lv' ? lvCam : view === 'walk' ? walkCam : freeCam;
      const rect = canvas.getBoundingClientRect();
      const ray = new THREE.Raycaster();
      ray.setFromCamera(new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1), cam);
      const top = view === 'free' && cut ? cutPlanes[0].constant : Infinity;
      const hit = ray.intersectObjects(scene.children, true).find((h) => h.object.visible && h.point.y < top - 0.02);
      if (!hit) return;
      let o = hit.object, info = '';
      while (o && !info) { info = o.userData.info || ''; o = o.parent; }
      const p = hit.point, at = `[${p.x.toFixed(2)}, ${(-p.z).toFixed(2)}, ${p.y.toFixed(2)}]`;
      say(info ? `${info} · clicaste no ponto ${at}` : `Ponto ${at} (x, y, altura)`);
    });
  }
  const afterRestore = [];
  if (camEdit) { // editor da câmara do plano: as posições-chave, editadas com a câmara à mão
    const keysW = JSON.parse(JSON.stringify(spec.camera || []));
    let editing = null; // índice da posição em edição, 'new' (posição nova) ou null
    const keepEditing = () => { if (memKey) editMemory.set(memKey, editing); };
    const ed = document.createElement('div');
    ed.className = 'm3d-camedit';
    (q('.m3d-chips') || bar).after(ed);
    const sec = (x) => fmt(x);
    function renderEd() {
      keepEditing();
      const rows = keysW.map((k, i) => `<div class="ce-row${editing === i ? ' on' : ''}">
        <span class="mono ce-n">${i + 1}</span>
        <label class="row" style="gap: 4px">aos <input class="in ce-t" type="number" step="0.1" min="0" max="${duration}" value="${k.t}" data-ce-t="${i}" aria-label="Segundo da posição ${i + 1}"> s</label>
        <span class="mono dim">${Math.round(k.lens)} mm</span>
        <label class="row" style="gap: 4px" title="Os momentos escolhem a vista do local que vai para o vídeo"><input type="checkbox" data-ce-m="${i}" ${k.moment ? 'checked' : ''}> momento</label>
        <span class="sp"></span>
        <button class="btn sm" type="button" data-ce="go:${i}">Ver</button>
        <button class="btn sm${editing === i ? ' pri' : ''}" type="button" data-ce="edit:${i}">Editar</button>
        <button class="btn sm no" type="button" data-ce="del:${i}" ${keysW.length < 2 ? 'disabled' : ''}>Apagar</button></div>`).join('');
      const editBar = editing === null ? '' : `<div class="ce-edit">${editing === 'new'
        ? `Posição nova aos <b>${sec(t)}</b>: compõe o enquadramento com a câmara à mão.`
        : `A editar a posição <b>${editing + 1}</b> (aos ${sec(keysW[editing].t)}): ajusta com a câmara à mão.`}
        <span class="sp"></span><button class="btn sm pri" type="button" data-ce="save">${editing === 'new' ? 'Guardar como posição nova' : 'Guardar esta posição'}</button>
        <button class="btn sm" type="button" data-ce="cancel">Cancelar</button></div>`;
      ed.innerHTML = `<div class="ce-h"><b>Câmara do plano</b><span class="hint">posições-chave: a câmara passa por elas, pela ordem do tempo</span><span class="sp"></span>
        <button class="btn sm" type="button" data-ce="add">Juntar posição aqui</button>
        ${el.dataset.cameraUndo ? '<button class="btn sm" type="button" data-ce="undo" title="Volta à câmara antes da última gravação">Desfazer</button>' : ''}</div>
        ${editBar}<details class="ce-list"${ceListOpen() ? ' open' : ''}><summary class="hint">Posições da câmara (${keysW.length})</summary><div class="ce-rows">${rows}</div></details>`;
    }
    // com muitas posições a lista fica fechada (abre-se para editar); o que o utilizador escolhe fica
    function ceListOpen() {
      if (editing !== null) return true;
      const k = memKey || 'cam';
      return listOpen.has(k) ? listOpen.get(k) : keysW.length <= 4;
    }
    ed.addEventListener('toggle', (e) => {
      if (e.target.matches && e.target.matches('details.ce-list')) listOpen.set(memKey || 'cam', e.target.open);
    }, true);
    async function saveCam(body, msg) {
      try {
        const r = await fetch(el.dataset.cameraUrl, { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() }, body: JSON.stringify(body) });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || !j.ok) throw new Error(j.error || `Erro ${r.status}`);
        say(msg);
        (window.estudioRefresh ? window.estudioRefresh() : location.reload());
      } catch (err) { say(`Não foi possível guardar a câmara: ${err.message}`); }
    }
    const saveKeys = (msg) => { keysW.sort((a, b) => a.t - b.t); saveCam({ camera: keysW }, msg); };
    function keyFromWalk(old) {
      const dist = old ? Math.max(0.5, v3(old.pos).distanceTo(v3(old.look_at))) : 5;
      return { pos: toSpec(walk.pos), look_at: toSpec(walk.pos.clone().addScaledVector(walkDir(), dist)), lens: Math.round(walk.lens * 10) / 10 };
    }
    ed.addEventListener('click', (e) => {
      const b = e.target.closest('[data-ce]');
      if (!b) return;
      const [act, arg] = b.dataset.ce.split(':');
      const i = Number(arg);
      playing = false; if (playBtn) playBtn.textContent = '▶';
      if (act === 'go') { editing = null; setT(keysW[i].t); setView('shot'); renderEd(); }
      else if (act === 'edit') {
        editing = i; setT(keysW[i].t);
        walkFrom(v3(keysW[i].pos), v3(keysW[i].look_at), keysW[i].lens); setView('walk'); renderEd();
      } else if (act === 'add') {
        if (view !== 'walk') startWalk();
        editing = 'new'; renderEd();
      } else if (act === 'cancel') { editing = null; setView('shot'); renderEd(); }
      else if (act === 'save') {
        if (view !== 'walk') return say('Põe a câmara com a câmara à mão e grava.');
        if (editing === 'new') {
          const at = Math.round(t * 10) / 10;
          const same = keysW.findIndex((k) => Math.abs(k.t - at) < 0.05);
          const key = { t: at, ...keyFromWalk(same >= 0 ? keysW[same] : null), moment: true };
          if (same >= 0) keysW[same] = { ...key, moment: keysW[same].moment }; else keysW.push(key);
          saveKeys(`Posição aos ${sec(at)} guardada.`);
        } else if (editing !== null) {
          keysW[editing] = { ...keysW[editing], ...keyFromWalk(keysW[editing]) };
          saveKeys(`Posição ${editing + 1} guardada.`);
        }
        editing = null; renderEd();
      } else if (act === 'del') { keysW.splice(i, 1); editing = null; renderEd(); saveKeys('Posição apagada.'); }
      else if (act === 'undo') saveCam({ undo: true }, 'Voltou a câmara anterior.');
    });
    ed.addEventListener('change', (e) => {
      const tIn = e.target.closest('[data-ce-t]'), mIn = e.target.closest('[data-ce-m]');
      if (tIn) {
        const v = Math.max(0, Math.min(duration, Number(String(tIn.value).replace(',', '.')) || 0));
        keysW[Number(tIn.dataset.ceT)].t = Math.round(v * 10) / 10; saveKeys('Segundo da posição mudado.');
      } else if (mIn) {
        keysW[Number(mIn.dataset.ceM)].moment = mIn.checked; saveKeys(mIn.checked ? 'Marcado como momento.' : 'Já não é momento.');
      }
    });
    const was = memKey ? editMemory.get(memKey) : null; // a página atualizou-se a meio da edição: continua
    if (was !== null && was !== undefined && (was === 'new' || keysW[was])) editing = was;
    renderEd();
    afterRestore.push(() => { if (view !== 'walk' && editing !== null) { editing = null; renderEd(); } });
  }
  const ro = new ResizeObserver(() => { if (!btnBusy()) fit(); });
  const btnBusy = () => [...el.querySelectorAll('[data-m3d="record"]')].some((b) => b.disabled);
  ro.observe(stage);
  controls.addEventListener('end', remember);
  const saved = memKey && camMemory.get(memKey);
  if (saved) { // a mesma maquete desenhada de novo: a câmara fica onde estava
    freeCam.position.fromArray(saved.free.pos); controls.target.fromArray(saved.free.target); controls.update();
    walk.pos.fromArray(saved.walk.pos); walk.yaw = saved.walk.yaw; walk.pitch = saved.walk.pitch; walk.lens = saved.walk.lens;
    applyWalk();
    if (saved.view === 'walk' || saved.view === 'free') view = saved.view;
  }
  fit(); setT(saved && saved.t ? Math.min(saved.t, duration) : 0); setView(view);
  afterRestore.forEach((f) => f());
  raf = requestAnimationFrame(loop);
  const toSpec = (p) => [Math.round(p.x * 100) / 100, Math.round(-p.z * 100) / 100, Math.round(p.y * 100) / 100];
  async function saveView(btn) { // a posição da câmara à mão (ou da vista livre) passa a ser uma vista do local
    if (view !== 'free' && view !== 'walk') { startWalk(); return say('Compõe a vista com a câmara à mão e clica outra vez em «Guardar vista aqui».'); }
    const name = window.prompt('Nome da vista (ex.: balcão visto da porta):', '');
    if (name === null) return;
    let cam;
    if (view === 'walk') {
      cam = { pos: toSpec(walk.pos), look_at: toSpec(walk.pos.clone().addScaledVector(walkDir(), 5)), lens: Math.round(walk.lens * 10) / 10 };
    } else {
      const hfov = 2 * Math.atan(Math.tan(freeCam.fov * Math.PI / 360) * aspect);
      cam = { pos: toSpec(freeCam.position), look_at: toSpec(controls.target), lens: Math.round(18 / Math.tan(hfov / 2) * 10) / 10 };
    }
    btn.disabled = true;
    try {
      const r = await fetch(`${el.dataset.viewsBase}/nova`, { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() }, body: JSON.stringify({ cam, label: name }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.ok) throw new Error(j.error || `Erro ${r.status}`);
      (window.estudioRefresh ? window.estudioRefresh() : location.reload());
    } catch (err) { say(`Não foi possível: ${human(err)}.`); btn.disabled = false; }
  }
  // Mapa de profundidade de uma vista (para os modelos de imagem que seguem a estrutura): distância real de cada
  // píxel à câmara, perto = branco, longe (até depthFar m) = preto, céu preto. Linear, sem nevoeiro nem nomes.
  const depthMat = new THREE.MeshDepthMaterial();
  depthMat.onBeforeCompile = (shader) => {
    shader.uniforms.uNear = { value: 0.3 };
    shader.uniforms.uFar = { value: 40 };
    depthMat.userData.shader = shader;
    shader.fragmentShader = 'uniform float uNear;\nuniform float uFar;\n' + shader.fragmentShader.replace(
      'vec3( 1.0 - fragCoordZ )',
      'vec3( 1.0 - clamp( ( - perspectiveDepthToViewZ( fragCoordZ, uNear, uFar ) - uNear ) / ( uFar - uNear ), 0.0, 1.0 ) )');
  };
  // Renders das vistas e imagens para o Assistente: num ecrã escondido à parte, para a maquete à vista não saltar
  // enquanto o utilizador mexe na câmara.
  let capPair = null;
  const cap = () => {
    if (!capPair) {
      const c = document.createElement('canvas');
      const r = new THREE.WebGLRenderer({ canvas: c, antialias: true, preserveDrawingBuffer: true });
      r.setPixelRatio(1);
      capPair = { c, r };
    }
    return capPair;
  };
  const capBlob = () => new Promise((res) => cap().c.toBlob(res, 'image/png'));
  function capCam(spec) { // a câmara de uma vista, como setLvCam, mas só para a captura
    const c = new THREE.PerspectiveCamera(fovFor(spec.lens, aspect), CAPTURE[0] / CAPTURE[1], 0.05, far * 6);
    c.position.copy(v3(spec.pos)); c.lookAt(v3(spec.look_at)); c.updateProjectionMatrix();
    return c;
  }
  function depthShot(cam, depthFar, rnd = renderer) {
    const renderer = rnd;
    const keep = { bg: scene.background, fog: scene.fog, near: cam.near, far: cam.far };
    renderer.clippingPlanes = [];  // o corte do "Ver por dentro" nunca entra nos renders
    scene.background = new THREE.Color(0x000000); scene.fog = null; scene.overrideMaterial = depthMat;
    cam.near = 0.3; cam.far = depthFar; cam.updateProjectionMatrix();
    const sh = depthMat.userData.shader;
    if (sh) { sh.uniforms.uNear.value = 0.3; sh.uniforms.uFar.value = depthFar; }
    renderer.render(scene, cam);
    if (!sh && depthMat.userData.shader) {  // 1.ª vez: o shader acabou de compilar com os valores por omissão
      depthMat.userData.shader.uniforms.uFar.value = depthFar; renderer.render(scene, cam);
    }
    scene.background = keep.bg; scene.fog = keep.fog; scene.overrideMaterial = null;
    cam.near = keep.near; cam.far = keep.far; cam.updateProjectionMatrix();
  }

  async function viewShot(lv) { // render de uma vista do local e o mapa de profundidade dela
    const { r } = cap(), cc = capCam(lv.cam);
    r.setSize(...CAPTURE, false); r.clippingPlanes = [];
    r.render(scene, cc);
    const blob = await capBlob();
    depthShot(cc, lv.outdoor ? 60 : 15, r);
    return { blob, depth: await capBlob() };
  }

  async function viewRenders() { // render de cada vista sem render ou com a maquete mudada desde então
    const todo = lviews.filter((lv) => !lv.fresh);
    if (!todo.length || !el.dataset.previewHash) return;
    try {
      for (const [n, lv] of todo.entries()) {
        say(`A tirar o render das vistas (${n + 1} de ${todo.length})…`);
        const { blob, depth } = await viewShot(lv);
        const fd = new FormData();
        fd.append('csrf_token', csrf()); fd.append('hash', el.dataset.previewHash); fd.append('file', blob, `vista${lv.id}.png`);
        fd.append('depth', depth, `vista${lv.id}_depth.png`);
        await fetch(`${el.dataset.viewsBase}/${lv.id}/render`, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf() } });
      }
      (window.estudioRefresh ? window.estudioRefresh() : location.reload());
    } catch (err) { say(`Os renders das vistas não ficaram guardados: ${human(err)}.`); } finally { fit(); }
  }

  // Vistas para o Assistente ver o que construiu: de cima (com o caminho da câmara e as divisões) e, nas maquetes dos
  // locais, as 4 vistas a partir do centro à altura dos olhos. Só quando a maquete mudou desde a última vez.
  async function previews() {
    const shots = [];
    const grab = async (cam, w, h, name) => {
      const { r } = cap();
      r.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix(); pose(0);
      r.clippingPlanes = name.startsWith('inside') ? cutPlanes.map((pl) => pl.clone()) : [];
      tagsFor(name.startsWith('inside') && levels.length > 1 ? r.clippingPlanes[0].constant - 2.6 : null);
      r.render(scene, cam);
      dirty = true; // as etiquetas dos pisos voltam ao que a vista à mostra pede
      shots.push([name, await capBlob()]);
    };
    try {
      const topCam = new THREE.PerspectiveCamera(45, 1, 0.1, far * 10);
      topCam.layers.enable(1);
      topCam.position.copy(center.clone().add(new THREE.Vector3(0, Math.max(6, radius * 2.3), 0.01)));
      topCam.lookAt(center);
      if (camHelper) camHelper.visible = true;
      await grab(topCam, 1024, 1024, 'top');
      const keepCut = cutPlanes[0].constant;
      try {
        for (const [i, z] of levels.slice(0, 6).entries()) { cutAt(z); await grab(topCam, 1024, 1024, `inside${i}`); }
      } finally { cutPlanes[0].constant = keepCut; }
      if (el.dataset.previewViews) {
        const eye = new THREE.PerspectiveCamera(fovFor(16, aspect), aspect, 0.05, far * 6);
        for (const [n, dir] of [['a', [0, 1]], ['b', [1, 0]], ['c', [0, -1]], ['d', [-1, 0]]]) {
          eye.position.copy(v3([0, 0, 1.6])); eye.lookAt(v3([dir[0], dir[1], 1.6]));
          await grab(eye, ...CAPTURE, n);
        }
      }
      const fd = new FormData();
      fd.append('csrf_token', csrf()); fd.append('hash', el.dataset.previewHash);
      for (const [n, b] of shots) fd.append(n, b, `${n}.png`);
      await fetch(el.dataset.preview, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf() } });
      el.dataset.previewHave = el.dataset.previewHash;
    } catch (err) { /* sem vistas: o Assistente continua a funcionar, só não as vê */ } finally { fit(); }
  }
  const needPreviews = el.dataset.preview && el.dataset.previewHash && el.dataset.previewHash !== el.dataset.previewHave;
  const needRenders = lviews.some((lv) => !lv.fresh);
  const needMoments = !layoutOnly && !!el.dataset.momentsStale;
  const needLoc = !layoutOnly && locPending.length > 0;
  if (needPreviews || needRenders || needMoments || needLoc) setTimeout(() => exclusive(async () => {
    await modelsReady(); invalidate(); // os modelos 3D importados têm de estar na cena antes das fotografias
    if (needPreviews) await previews();
    if (needRenders) await viewRenders();
    if (needMoments) await captureMoments();
    if (needLoc) await locRenders();
    if ((needMoments || needLoc) && !btnBusy()) (window.estudioRefresh ? window.estudioRefresh() : location.reload());
  }), 900);

  // Para o conector MCP (num navegador sem ecrã, app/maquete_render.py): os mesmos renders que esta página tira e envia,
  // devolvidos a quem os pede (o servidor guarda-os como se viessem daqui).
  const dataUrl = (b) => new Promise((res) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.readAsDataURL(b); });
  el.m3dCapture = {
    async view(lv) { const { blob, depth } = await viewShot(lv); return { png: await dataUrl(blob), depth: await dataUrl(depth) }; },
    async moment(at) { return dataUrl(await momentShot(at)); },
  };
  // Gravação quadro a quadro (a do conector MCP, num navegador sem ecrã): os mesmos quadros do botão «Gravar
  // vídeo-guia» (câmara do plano, 1280×720), mas cada um pedido a um instante, sem depender do tempo real.
  if (!layoutOnly && !stageMode) el.m3dFrames = {
    duration,
    start() { playing = false; setView('shot'); size(...CAPTURE); },
    frame(s) { setT(s); draw(); return canvas.toDataURL('image/jpeg', 0.92); },
    end() { fit(); },
  };

  const handle = { el, dispose() {
    cancelAnimationFrame(raf); ro.disconnect(); controls.dispose(); renderer.dispose(); renderer.forceContextLoss?.();
    if (capPair) { capPair.r.dispose(); capPair.r.forceContextLoss?.(); }
  } };
  live.add(handle);
}
