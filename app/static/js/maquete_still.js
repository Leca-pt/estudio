// Imagens fixas de uma maquete 3D para o conector MCP (view_layout): a mesma cena que o visualizador da app desenha
// (buildLayer, actorMesh), vista de cima com grelha e números dos objetos, pelas vistas do local, à altura dos olhos,
// de três quartos, por um piso cortado ou por uma câmara livre. Corre num Chromium sem ecrã dentro do contentor.
import * as THREE from '../vendor/three/three.module.min.js';
import { SKY, actorMesh, buildLayer, facingAt, fovFor, groundFn, groundPlane, linear, modelsReady, onGround, v3 } from './scene3d.js?v=49';
import { applyPose } from './scene3d_parts.js?v=5';

const rad = (d) => (d || 0) * Math.PI / 180;

function textSprite(text, { size = 1, color = '#111', bg = 'rgba(255,255,255,0.82)', weight = 600, screen = false } = {}) {
  const cv = document.createElement('canvas');
  const g = cv.getContext('2d');
  const font = `${weight} 44px system-ui, sans-serif`;
  g.font = font;
  const w = Math.ceil(g.measureText(text).width) + 24;
  cv.width = w; cv.height = 64;
  g.font = font;
  if (bg) { g.fillStyle = bg; g.fillRect(0, 4, w, 56); }
  g.fillStyle = color; g.textAlign = 'center'; g.textBaseline = 'middle';
  g.fillText(text, w / 2, 34);
  // screen: tamanho fixo no ecrã e escondido pelo que está à frente (vistas em perspetiva)
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(cv), depthTest: screen, depthWrite: false,
                                                         sizeAttenuation: !screen }));
  sp.scale.set(size * w / 64, size, 1);
  sp.renderOrder = 10;
  return sp;
}

function anchor(o) { // onde pôr o #n de um objeto (coordenadas da cena)
  if (o.center) return [o.center[0], o.center[1], (o.center[2] || 0) + (o.size ? o.size[2] : o.height || 1)];
  if (o.from && o.to) return [(o.from[0] + o.to[0]) / 2, (o.from[1] + o.to[1]) / 2, (o.z || o.from[2] || 0) + 1];
  if (o.points) {
    const xs = o.points.map((p) => p[0]), ys = o.points.map((p) => p[1]);
    return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2, (o.z || 0) + (o.height || 1)];
  }
  return null;
}

function bounds(objects) {
  const xs = [], ys = [];
  const add = (p) => { if (p) { xs.push(p[0]); ys.push(p[1]); } };
  const zones = objects.filter((o) => o.type === 'zone');
  for (const o of zones.length ? zones : objects) {
    add(o.from); add(o.to);
    if (o.center) {
      const s = o.size || [o.radius * 2 || 1, o.radius * 2 || 1];
      add([o.center[0] - s[0] / 2, o.center[1] - s[1] / 2]); add([o.center[0] + s[0] / 2, o.center[1] + s[1] / 2]);
    }
    for (const p of o.points || []) add(p);
  }
  if (!xs.length) return { x0: -10, x1: 10, y0: -10, y1: 10 };
  return { x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys) };
}

export async function renderStills(spec, opts) {
  const size = Math.max(256, Math.min(opts.size || 1024, 1600));
  const canvas = document.createElement('canvas');
  document.body.appendChild(canvas);
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(1);
  renderer.autoClear = false;

  // A cena, como no visualizador
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(SKY);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x5a5a60, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.5);
  sun.position.set(-300, 600, 250);
  scene.add(sun);
  let far = 400;
  const groundAt = groundFn(spec.layers);
  spec.layers.forEach((layer, i) => {
    scene.add(buildLayer(layer, spec.palette, i, groundAt));
    for (const o of layer.objects || []) for (const k of ['center', 'from', 'to']) if (o[k]) far = Math.max(far, Math.abs(o[k][0]) * 2, Math.abs(o[k][1]) * 2);
  });
  const gsize = spec.layers.find((l) => l.ground)?.ground || [far * 3, far * 3];
  scene.add(groundPlane(spec.layers, gsize, spec.palette.ground, spec.palette));
  const t = opts.t || 0;
  for (const a of spec.actors || []) { // o elenco e os figurantes nas posições do segundo t
    const m = actorMesh(a);
    const { pos: rel } = linear(a.keys, t);
    const pos = onGround(groundAt, rel);
    m.position.copy(v3(pos)); m.rotation.y = rad(facingAt(a.keys, t));
    if (m.userData.man) {
      const k = [...a.keys].reverse().find((x) => x.t <= t && x.pose);
      applyPose(m.userData.man, k ? k.pose : 'stand', 0);
    }
    scene.add(m);
  }
  const fog = new THREE.Fog(SKY, far * 0.6, far * 3);

  const base = spec.layers[0] || { objects: [] };
  const objs = base.objects || [];
  let b = bounds(objs);
  if (opts.focus) { // aproximar a planta a uma divisão (pelo nome) ou a um retângulo [x0, y0, x1, y1]
    const z = typeof opts.focus === 'string' && objs.filter((o) => o.type === 'zone' && o.name === opts.focus);
    if (z && z.length) b = bounds(z);
    else if (Array.isArray(opts.focus) && opts.focus.length === 4) {
      const [x0, y0, x1, y1] = opts.focus; b = { x0: Math.min(x0, x1), x1: Math.max(x0, x1), y0: Math.min(y0, y1), y1: Math.max(y0, y1) };
    }
  }
  const span = Math.max(b.x1 - b.x0, b.y1 - b.y0, 4);
  const cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2;
  const lviews = opts.lviews || [];

  function overlays(view, floorZ, cam) { // legendas por cima (sem cortes nem nevoeiro)
    const ov = new THREE.Scene();
    const plan = view === 'top' || view.startsWith('floor:');
    const lineMat = (c, o) => new THREE.LineBasicMaterial({ color: c, transparent: true, opacity: o, depthTest: !plan, depthWrite: false });
    if (plan) { // grelha de 1 m (mais forte a cada 5 m), eixos e números das coordenadas
      const h = (floorZ ?? 0) + 0.05, pad = 2;
      const x0 = Math.floor(b.x0 - pad), x1 = Math.ceil(b.x1 + pad), y0 = Math.floor(b.y0 - pad), y1 = Math.ceil(b.y1 + pad);
      const thin = [], bold = [];
      for (let x = x0; x <= x1; x++) (x % 5 === 0 ? bold : thin).push(v3([x, y0, h]), v3([x, y1, h]));
      for (let y = y0; y <= y1; y++) (y % 5 === 0 ? bold : thin).push(v3([x0, y, h]), v3([x1, y, h]));
      ov.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(thin), lineMat(0x2a2f36, 0.18)));
      ov.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(bold), lineMat(0x2a2f36, 0.55)));
      const axes = [v3([0, y0, h]), v3([0, y1, h]), v3([x0, 0, h]), v3([x1, 0, h])];
      ov.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(axes), lineMat(0xd2283c, 0.9)));
      const ts = span / 55;
      for (let x = Math.ceil(x0 / 5) * 5; x <= x1; x += 5) { const s = textSprite(`x ${x}`, { size: ts, color: '#7a1f2b' }); s.position.copy(v3([x, y0 - 0.6, h])); ov.add(s); }
      for (let y = Math.ceil(y0 / 5) * 5; y <= y1; y += 5) { const s = textSprite(`y ${y}`, { size: ts, color: '#7a1f2b' }); s.position.copy(v3([x0 - 1.2, y, h])); ov.add(s); }
      const ax = textSprite('X →', { size: ts * 1.3, color: '#d2283c' }); ax.position.copy(v3([x1 + 0.8, 0, h])); ov.add(ax);
      const ay = textSprite('Y ↑', { size: ts * 1.3, color: '#d2283c' }); ay.position.copy(v3([0, y1 + 0.8, h])); ov.add(ay);
      if (lviews.length) { // seta da vista A: de onde olha e para onde
        const c = lviews[0].cam, from = v3([c.pos[0], c.pos[1], h]), to = v3([c.look_at[0], c.look_at[1], h]);
        const dir = to.clone().sub(from); const len = Math.max(1.5, Math.min(dir.length(), span / 5));
        const arrow = new THREE.ArrowHelper(dir.normalize(), from, len, 0xff2d8a, len * 0.3, len * 0.18);
        arrow.traverse((o) => { if (o.material) { o.material.depthTest = false; o.material.transparent = true; } o.renderOrder = 9; });
        ov.add(arrow);
        const tag = textSprite(`vista ${lviews[0].name}`, { size: ts * 1.1, color: '#ff2d8a' }); tag.position.copy(from.clone().add(new THREE.Vector3(0, 0.1, 0.8))); ov.add(tag);
      }
    }
    if (opts.labels) {
      const ts = plan ? span / 70 : 0.032;
      const near = (p) => plan || (cam.position.distanceTo(v3(p)) < 30
        && v3(p).sub(cam.position).dot(cam.getWorldDirection(new THREE.Vector3())) > 0);
      objs.forEach((o, n) => {
        if (o.type === 'zone') {
          if (floorZ !== null && Math.abs((o.z || 0) - floorZ) > 0.5) return;
          if (!near([(o.from[0] + o.to[0]) / 2, (o.from[1] + o.to[1]) / 2, (o.z || 0) + 2.4])) return;
          const z = textSprite(o.name || 'zona', { size: (plan ? span / 42 : 0.04), color: '#14365e', bg: 'rgba(214,230,255,0.85)', weight: 700, screen: !plan });
          z.position.copy(v3([(o.from[0] + o.to[0]) / 2, (o.from[1] + o.to[1]) / 2 + (plan ? span / 30 : 0), (o.z || 0) + (plan ? 0.1 : 2.4)]));
          ov.add(z);
        }
        const p = anchor(o);
        if (!p) return;
        if (floorZ !== null && (p[2] < floorZ - 0.3 || p[2] > floorZ + 3.2)) return;
        if (!near(p)) return;
        if (plan && opts.focus && (p[0] < b.x0 - 1 || p[0] > b.x1 + 1 || p[1] < b.y0 - 1 || p[1] > b.y1 + 1)) return;
        const s = textSprite(`#${o.n ?? n}`, { size: ts, color: '#000', bg: 'rgba(255,214,0,0.85)', screen: !plan });
        s.position.copy(v3(plan ? [p[0], p[1], (floorZ ?? 0) + 0.1] : [p[0], p[1], p[2] + 0.15]));
        ov.add(s);
      });
    }
    return ov;
  }

  function camFor(view) {
    const aspect = 16 / 9;
    if (view === 'top' || view.startsWith('floor:')) {
      const half = span / 2 + (opts.focus ? 1 : 3);
      const cam = new THREE.OrthographicCamera(-half, half, half, -half, 0.1, 5000);
      cam.position.set(cx, 1500, -cy); cam.up.set(0, 0, -1); cam.lookAt(cx, 0, -cy);
      return { cam, w: size, h: size };
    }
    const cam = new THREE.PerspectiveCamera(50, aspect, 0.05, far * 6);
    let pos, look, fov;
    if (view === 'camera' && opts.camera) { pos = opts.camera.pos; look = opts.camera.look_at; fov = opts.camera.fov || 50; }
    else if (view === 'eye') { pos = [0, 0, 1.6]; look = [0, 10, 1.6]; fov = 50; }
    else if (view === 'orbit') {
      const d = span * 0.9;
      pos = [cx - d * 0.5, cy - d * 0.5, d * 0.72]; look = [cx, cy, 0]; fov = 45;
    } else {
      const lv = lviews.find((x) => x.name === view);
      if (!lv) return null;
      pos = lv.cam.pos; look = lv.cam.look_at; cam.fov = fovFor(lv.cam.lens || 28, aspect);
    }
    if (fov) cam.fov = fov;
    cam.position.copy(v3(pos)); cam.lookAt(v3(look)); cam.updateProjectionMatrix();
    return { cam, w: size, h: Math.round(size / aspect) };
  }

  const out = [];
  await modelsReady(); // os modelos 3D importados carregam antes de se fotografar
  for (const view of opts.views) {
    const c = camFor(view);
    if (!c) { out.push({ view, error: 'vista desconhecida' }); continue; }
    const floorZ = view.startsWith('floor:') ? Number(view.split(':')[1]) || 0 : null;
    renderer.setSize(c.w, c.h, false);
    if (c.cam.isPerspectiveCamera) { c.cam.aspect = c.w / c.h; c.cam.updateProjectionMatrix(); }
    scene.fog = c.cam.isOrthographicCamera ? null : fog;
    renderer.clippingPlanes = floorZ === null ? []
      : [new THREE.Plane(new THREE.Vector3(0, -1, 0), floorZ + 2.2), new THREE.Plane(new THREE.Vector3(0, 1, 0), -(floorZ - 0.3))];
    renderer.clear();
    renderer.render(scene, c.cam);
    renderer.clippingPlanes = [];
    if (floorZ !== null || c.cam.isOrthographicCamera) renderer.clearDepth(); // na planta, as legendas ficam sempre por cima
    renderer.render(overlays(view, floorZ, c.cam), c.cam);
    out.push({ view, png: canvas.toDataURL('image/png') });
  }
  return out;
}
