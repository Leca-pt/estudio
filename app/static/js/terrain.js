// Relevo das maquetes: a mesma conta de app/terrain.py, linha a linha (o servidor e o navegador têm de dar a mesma
// altura em cada ponto), mais o desenho do terreno, das rochas e do que se espalha por cima (rochas, árvores, arbustos).
import * as THREE from '../vendor/three/three.module.min.js';

function hash(ix, iy, seed) {
  let h = (Math.imul(ix, 374761393) + Math.imul(iy, 668265263) + Math.imul(seed, 2246822519 | 0)) >>> 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177) >>> 0;
  h = (h ^ (h >>> 16)) >>> 0;
  return h / 4294967296;
}
const smooth = (t) => t * t * (3 - 2 * t);
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

function noise(x, y, seed) {
  const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy;
  const a = hash(ix, iy, seed), b = hash(ix + 1, iy, seed), c = hash(ix, iy + 1, seed), d = hash(ix + 1, iy + 1, seed);
  const u = smooth(fx), v = smooth(fy);
  const top = a + (b - a) * u, bottom = c + (d - c) * u;
  return (top + (bottom - top) * v) * 2 - 1;
}

export function fbm(x, y, seed) {
  let total = 0, amp = 1, freq = 1, norm = 0;
  for (let o = 0; o < 4; o++) {
    total += noise(x * freq, y * freq, seed + o * 31) * amp;
    norm += amp; amp *= 0.5; freq *= 2;
  }
  return total / norm;
}

function segDist(px, py, path) {
  if (path.length === 1) return Math.sqrt((px - path[0][0]) * (px - path[0][0]) + (py - path[0][1]) * (py - path[0][1]));
  let best = 1e18;
  for (let i = 0; i < path.length - 1; i++) {
    const [ax, ay] = path[i], [bx, by] = path[i + 1];
    const dx = bx - ax, dy = by - ay, L = dx * dx + dy * dy;
    const s = L <= 1e-12 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / L));
    const qx = ax + dx * s, qy = ay + dy * s;
    const d = Math.sqrt((px - qx) * (px - qx) + (py - qy) * (py - qy));
    if (d < best) best = d;
  }
  return best;
}

export function height(t, x, y) {
  const seed = t.seed ?? 1, scale = t.scale || 40;
  let h = (t.z || 0) + (t.relief || 0) * fbm(x / scale, y / scale, seed);
  const flats = [];
  (t.features || []).forEach((f, n) => {
    if (f.kind === 'canyon') {
      const halfFloor = f.floor / 2, halfW = Math.max(f.width / 2, f.floor / 2 + 0.5);
      const d = segDist(x, y, f.path) + (f.jagged ?? 0.4) * (halfW - halfFloor) * 0.35 * fbm(x / 9, y / 9, seed + 101 + n);
      const s = clamp01((d - halfFloor) / (halfW - halfFloor));
      let drop = 1 - smooth(s);
      const terr = f.terraces || 0;
      if (terr > 0) drop = drop + (Math.floor(drop * 5 + 0.5) / 5 - drop) * terr;
      h -= f.depth * drop;
    } else if (f.kind === 'ridge') {
      const d = segDist(x, y, f.path);
      h += f.height * (1 - smooth(clamp01(d / (f.width / 2))));
    } else if (f.kind === 'hill') {
      const cx = x - f.center[0], cy = y - f.center[1];
      const d = Math.sqrt(cx * cx + cy * cy);
      h += f.height * (1 - smooth(clamp01(d / f.radius)));
    } else if (f.kind === 'mesa') {
      const cx = x - f.center[0], cy = y - f.center[1];
      let d = Math.sqrt(cx * cx + cy * cy);
      d += f.radius * 0.12 * fbm(x / 7, y / 7, seed + 201 + n);
      const edge = f.radius * 0.8;
      const s = clamp01((d - edge) / (f.radius - edge));
      h += f.height * (1 - smooth(s));
    } else if (f.kind === 'flat') flats.push(f);
  });
  for (const f of flats) {
    const [x0, y0] = f.from, [x1, y1] = f.to, blend = Math.max(f.blend ?? 4, 0.01);
    const dx = Math.max(x0 - x, 0, x - x1), dy = Math.max(y0 - y, 0, y - y1);
    const w = 1 - smooth(clamp01(Math.sqrt(dx * dx + dy * dy) / blend));
    h += (f.z - h) * w;
  }
  return h;
}

// O chão em (x, y): o terreno mais alto que cobre o ponto, ou 0. null se a maquete não tem terreno.
export function groundFn(layers) {
  const ts = [];
  for (const l of layers || []) for (const o of l.objects || []) if (o.type === 'terrain') ts.push(o);
  if (!ts.length) return null;
  return (x, y) => {
    let best = null;
    for (const t of ts) {
      const [x0, y0] = t.from, [x1, y1] = t.to;
      if (x >= x0 && x <= x1 && y >= y0 && y <= y1) { const h = height(t, x, y); best = best === null ? h : Math.max(best, h); }
    }
    return best === null ? 0 : best;
  };
}

// Malha do terreno: uma grelha até ~200 × 200 quadrados, com as normais de cada triângulo (aspeto facetado de rocha).
export function terrainMesh(t, material) {
  const [x0, y0] = t.from, [x1, y1] = t.to, w = x1 - x0, d = y1 - y0;
  const cell = Math.max(0.5, Math.max(w, d) / 200);
  const nx = Math.max(2, Math.round(w / cell)), ny = Math.max(2, Math.round(d / cell));
  const geo = new THREE.PlaneGeometry(w, d, nx, ny);
  geo.rotateX(-Math.PI / 2); // o plano passa a ser o chão (three: x, altura, -y)
  const p = geo.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const sx = p.getX(i) + x0 + w / 2, sy = -p.getZ(i) + y0 + d / 2; // three z = -y da cena
    p.setXYZ(i, sx, height(t, sx, sy), -sy);
  }
  const flat = geo.toNonIndexed();
  flat.computeVertexNormals();
  geo.dispose();
  const group = new THREE.Group();
  group.add(new THREE.Mesh(flat, material));
  // «Saia» à volta: liga a borda do terreno ao chão plano de fora (à altura média da borda, como em scene3d.groundPlane),
  // para onde um canyon chega à borda não se ver o céu por baixo nem pelo corte
  let sum = 0, cnt = 0;
  for (let i = 0; i <= 40; i++) {
    const fx = x0 + w * i / 40, fy = y0 + d * i / 40;
    for (const [x, y] of [[fx, y0], [fx, y1], [x0, fy], [x1, fy]]) { sum += height(t, x, y); cnt++; }
  }
  const rim = sum / cnt - 0.05, pos = [];
  const edge = (pts) => {
    for (let i = 0; i < pts.length - 1; i++) {
      const [ax, ay] = pts[i], [bx, by] = pts[i + 1], ha = height(t, ax, ay), hb = height(t, bx, by);
      pos.push(ax, ha, -ay, ax, rim, -ay, bx, hb, -by, bx, hb, -by, ax, rim, -ay, bx, rim, -by);
    }
  };
  const along = (n, f) => Array.from({ length: n + 1 }, (_, i) => f(i / n));
  edge(along(nx, (u) => [x0 + w * u, y0])); edge(along(nx, (u) => [x1 - w * u, y1]));
  edge(along(ny, (u) => [x1, y0 + d * u])); edge(along(ny, (u) => [x0, y1 - d * u]));
  const skirt = new THREE.BufferGeometry();
  skirt.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  skirt.computeVertexNormals();
  const sm = material.clone(); sm.side = THREE.DoubleSide;
  group.add(new THREE.Mesh(skirt, sm));
  return group;
}

// Rochas: uma esfera irregular (deformada por ruído, pela semente) esticada para o tamanho e a forma pedidos.
const rockGeos = new Map();
export function rockGeo(seed, shape) {
  const key = `${seed}|${shape}`;
  if (rockGeos.has(key)) return rockGeos.get(key);
  const g = new THREE.IcosahedronGeometry(1, 2);
  const p = g.attributes.position, sharp = shape === 'sharp' || shape === 'spire';
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i), y = p.getY(i), z = p.getZ(i);
    const n = fbm(x * 1.7 + seed * 3.1, z * 1.7 + y * 1.3, seed) * (sharp ? 0.42 : 0.28)
      + fbm(x * 4 + 9, y * 4 + z * 2, seed + 7) * (sharp ? 0.16 : 0.08);
    const r = 1 + n;
    p.setXYZ(i, x * r, Math.max(-0.25, y) * r, z * r); // a base fica achatada (pousa no chão)
  }
  const flat = g.toNonIndexed();
  flat.computeVertexNormals();
  g.dispose();
  rockGeos.set(key, flat);
  return flat;
}
// Forma da rocha → proporções da esfera base (largura, altura, profundidade) quando o tamanho não é dado
export const ROCK_SHAPES = { round: [1, 0.7, 1], sharp: [1, 0.9, 0.8], slab: [1.4, 0.35, 1], spire: [0.5, 2.2, 0.5] };

// Vegetação e afins para espalhar: geometrias de 1 m de altura (o tamanho dá a escala)
const PLANT = {};
function plantGeos() {
  if (PLANT.tree) return PLANT;
  const merge = (parts) => {
    const geos = parts.map(([geo, y]) => { const g = geo.toNonIndexed(); g.translate(0, y, 0); return g; });
    const pos = [];
    for (const g of geos) pos.push(...g.attributes.position.array);
    const out = new THREE.BufferGeometry();
    out.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    out.computeVertexNormals();
    return out;
  };
  PLANT.tree = merge([[new THREE.CylinderGeometry(0.03, 0.04, 0.5, 6), 0.25], [new THREE.IcosahedronGeometry(0.32, 0), 0.68]]);
  PLANT.pine = merge([[new THREE.CylinderGeometry(0.03, 0.04, 0.25, 6), 0.12], [new THREE.ConeGeometry(0.22, 0.8, 7), 0.6]]);
  PLANT.bush = merge([[new THREE.IcosahedronGeometry(0.5, 0).scale(1, 0.75, 1), 0.35]]);
  PLANT.grass = merge([[new THREE.ConeGeometry(0.12, 1, 4), 0.5]]);
  PLANT.cactus = merge([[new THREE.CylinderGeometry(0.09, 0.1, 1, 8), 0.5], [new THREE.CylinderGeometry(0.05, 0.05, 0.35, 6).rotateZ(Math.PI / 2.4), 0.55]]);
  return PLANT;
}

export function rng(seed) { // mulberry32 (o mesmo dos quarteirões)
  let a = seed >>> 0;
  return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
}

// Espalhar N coisas iguais num retângulo, assentes no chão: um só objeto nos dados, milhares no ecrã (instâncias).
export function scatterMesh(o, ground, material, v3) {
  const rand = rng((o.seed ?? 5) * 7919 + 13);
  const [x0, y0] = o.from, [x1, y1] = o.to;
  const [smin, smax] = o.size;
  const rock = ['rock', 'boulder', 'pebble'].includes(o.item);
  const variants = rock ? [0, 1, 2, 3].map((v) => rockGeo((o.seed ?? 5) * 10 + v, o.item === 'boulder' ? 'round' : 'sharp')) : [plantGeos()[o.item]];
  const placed = variants.map(() => []);
  const dummy = new THREE.Object3D();
  const maxSlope = o.max_slope ?? 90, tanMax = Math.tan(Math.min(89.9, maxSlope) * Math.PI / 180);
  let tries = 0, n = 0;
  while (n < o.count && tries < o.count * 6) {
    tries++;
    const x = x0 + rand() * (x1 - x0), y = y0 + rand() * (y1 - y0), r1 = rand(), r2 = rand(), r3 = rand();
    if ((o.avoid || []).some(([ax0, ay0, ax1, ay1]) => x >= ax0 && x <= ax1 && y >= ay0 && y <= ay1)) continue;
    const g = ground ? ground(x, y) : 0;
    if (ground && maxSlope < 90) {
      const sx = ground(x + 0.5, y) - ground(x - 0.5, y), sy = ground(x, y + 0.5) - ground(x, y - 0.5);
      if (Math.hypot(sx, sy) > tanMax) continue;
    }
    const s = smin + (smax - smin) * r1;
    dummy.position.copy(v3([x, y, g - (rock ? s * 0.15 : 0)]));
    dummy.rotation.set(0, r2 * Math.PI * 2, 0);
    if (rock) dummy.scale.set(s * (0.8 + r3 * 0.5) / 2, s * (o.item === 'pebble' ? 0.5 : 0.75) / 2, s * (1.1 - r3 * 0.4) / 2);
    else dummy.scale.set(s * (0.85 + r3 * 0.3), s, s * (0.85 + r3 * 0.3));
    dummy.updateMatrix();
    placed[n % variants.length].push(dummy.matrix.clone());
    n++;
  }
  const group = new THREE.Group();
  variants.forEach((geo, i) => {
    if (!placed[i].length) return;
    const mesh = new THREE.InstancedMesh(geo, material, placed[i].length);
    placed[i].forEach((m, k) => mesh.setMatrixAt(k, m));
    group.add(mesh);
  });
  return group;
}
