// Peças da maquete 3D: mobília e objetos com a forma certa, prédios com janelas e as figuras das pessoas.
// Coordenadas locais em metros, como na cena: x à direita, y para a frente (+Y = frente da peça), z para cima.
import * as THREE from '../vendor/three/three.module.min.js';

const mats = new Map();
export function mat(hex) {
  const key = String(hex);
  if (!mats.has(key)) mats.set(key, new THREE.MeshLambertMaterial({ color: hex }));
  return mats.get(key);
}
const DARK = '#3d4045', LIGHT = '#e9e6df', GLASS = '#9fb0bf', BOTTLE = '#5c7a6a';
const BOX = new THREE.BoxGeometry(1, 1, 1), CYL = new THREE.CylinderGeometry(1, 1, 1, 18), SPH = new THREE.SphereGeometry(1, 16, 12);

class Kit { // monta uma peça a partir de caixas, cilindros e esferas (em coordenadas locais da cena)
  constructor() { this.g = new THREE.Group(); }
  box(x, y, z, w, d, h, color, rx = 0) {
    const m = new THREE.Mesh(BOX, mat(color)); m.position.set(x, z, -y); m.scale.set(w, h, d); m.rotation.x = rx; this.g.add(m); return m;
  }
  cyl(x, y, z, r, h, color, r2 = null) {
    const geo = r2 === null ? CYL : new THREE.CylinderGeometry(r2 / r, 1, 1, 18);
    const m = new THREE.Mesh(geo, mat(color)); m.position.set(x, z, -y); m.scale.set(r, h, r); this.g.add(m); return m;
  }
  wheel(x, y, z, r, w, color) { // cilindro deitado (eixo em X)
    const m = new THREE.Mesh(CYL, mat(color)); m.position.set(x, z, -y); m.rotation.z = Math.PI / 2; m.scale.set(r, w, r); this.g.add(m); return m;
  }
  sph(x, y, z, sx, sy, sz, color) {
    const m = new THREE.Mesh(SPH, mat(color)); m.position.set(x, z, -y); m.scale.set(sx, sz, sy); this.g.add(m); return m;
  }
}

const legs4 = (k, w, d, h, t, c) => { for (const sx of [-1, 1]) for (const sy of [-1, 1]) k.box(sx * (w / 2 - t), sy * (d / 2 - t), h / 2, t, t, h, c); };

// Cada peça é desenhada no tamanho por omissão (o mesmo de app/scene3d.py PIECES) e depois esticada para "size".
const BUILD = {
  table_rect: (k, c) => { k.box(0, 0, 0.73, 1.4, 0.8, 0.04, c); legs4(k, 1.3, 0.7, 0.71, 0.05, c); },
  table_round: (k, c) => { k.cyl(0, 0, 0.73, 0.45, 0.04, c); k.cyl(0, 0, 0.37, 0.04, 0.7, DARK); k.cyl(0, 0, 0.015, 0.25, 0.03, DARK); },
  chair: (k, c) => { k.box(0, 0, 0.45, 0.45, 0.45, 0.04, c); k.box(0, -0.21, 0.68, 0.45, 0.04, 0.44, c); legs4(k, 0.43, 0.43, 0.44, 0.035, c); },
  bar_stool: (k, c) => { k.cyl(0, 0, 0.74, 0.19, 0.05, c); k.cyl(0, 0, 0.37, 0.025, 0.72, DARK); k.cyl(0, 0, 0.26, 0.14, 0.015, DARK); k.cyl(0, 0, 0.015, 0.2, 0.03, DARK); },
  sofa: (k, c) => { k.box(0, 0, 0.2, 2.0, 0.9, 0.4, c); k.box(0, 0.08, 0.46, 1.6, 0.72, 0.12, c); k.box(0, -0.35, 0.62, 2.0, 0.2, 0.46, c); for (const s of [-1, 1]) k.box(s * 0.9, 0.05, 0.55, 0.2, 0.8, 0.3, c); },
  armchair: (k, c) => { k.box(0, 0, 0.2, 0.9, 0.85, 0.4, c); k.box(0, 0.07, 0.46, 0.5, 0.68, 0.12, c); k.box(0, -0.33, 0.62, 0.9, 0.2, 0.46, c); for (const s of [-1, 1]) k.box(s * 0.35, 0.05, 0.55, 0.2, 0.75, 0.3, c); },
  booth: (k, c) => { k.box(0, 0.05, 0.2, 1.4, 0.45, 0.4, c); k.box(0, 0.05, 0.45, 1.4, 0.5, 0.1, c); k.box(0, -0.25, 0.8, 1.4, 0.1, 0.7, c); },
  bench: (k, c) => { k.box(0, 0.05, 0.45, 1.6, 0.4, 0.05, c); k.box(0, -0.18, 0.65, 1.6, 0.04, 0.3, c); legs4(k, 1.5, 0.4, 0.44, 0.05, DARK); },
  counter: (k, c) => { k.box(0, -0.05, 0.53, 3.0, 0.6, 1.06, c); k.box(0, 0.03, 1.08, 3.1, 0.76, 0.05, c); k.box(0, 0.27, 0.2, 3.0, 0.04, 0.04, DARK); },
  shelf: (k, c) => {
    for (const s of [-1, 1]) k.box(s * 0.78, 0, 1.0, 0.04, 0.4, 2.0, c);
    k.box(0, -0.18, 1.0, 1.6, 0.04, 2.0, c);
    for (const z of [0.05, 0.45, 0.9, 1.35, 1.8]) k.box(0, 0, z, 1.56, 0.38, 0.03, c);
    for (const z of [0.45, 0.9, 1.35]) for (let i = 0; i < 8; i++) k.cyl(-0.63 + i * 0.18, 0.05, z + 0.13, 0.035, 0.24, BOTTLE);
  },
  fridge: (k, c) => { k.box(0, 0, 0.925, 0.7, 0.7, 1.85, c); k.box(0, 0.355, 1.25, 0.66, 0.01, 0.015, DARK); k.box(0.28, 0.37, 1.0, 0.03, 0.03, 0.4, DARK); },
  register: (k, c) => { k.box(0, 0, 0.08, 0.4, 0.4, 0.16, c); k.box(0, -0.06, 0.26, 0.3, 0.03, 0.2, DARK, -0.25); },
  stall: (k, c) => {
    k.box(0, 0.1, 0.45, 2.0, 1.0, 0.9, c);
    for (const sx of [-1, 1]) for (const sy of [-1, 1]) k.cyl(sx * 0.95, 0.1 + sy * 0.5, 1.15, 0.03, 2.3, DARK);
    k.box(0, 0.3, 2.2, 2.2, 1.5, 0.05, '#9a7d6a', 0.18);
  },
  kitchen_counter: (k, c) => { k.box(0, 0, 0.44, 2.4, 0.6, 0.88, c); k.box(0, 0.01, 0.9, 2.42, 0.62, 0.04, '#a7a39b'); for (let i = -1; i <= 1; i++) k.box(i * 0.8, 0.305, 0.45, 0.01, 0.01, 0.8, DARK); },
  stove: (k, c) => { k.box(0, 0, 0.45, 0.6, 0.6, 0.9, c); for (const sx of [-1, 1]) for (const sy of [-1, 1]) k.cyl(sx * 0.14, sy * 0.14, 0.91, 0.08, 0.01, DARK); },
  sink: (k, c) => { k.box(0, 0, 0.44, 0.8, 0.6, 0.88, '#a89582'); k.box(0, 0.01, 0.9, 0.82, 0.62, 0.03, c); k.box(0, 0.02, 0.88, 0.5, 0.36, 0.06, DARK); k.cyl(0, -0.22, 1.02, 0.02, 0.2, c); },
  bed: (k, c) => {
    k.box(0, 0, 0.15, 1.6, 2.0, 0.3, '#8e8176'); k.box(0, 0.02, 0.4, 1.55, 1.95, 0.2, c);
    for (const s of [-1, 1]) k.box(s * 0.4, -0.78, 0.55, 0.6, 0.32, 0.1, LIGHT);
    k.box(0, -1.0, 0.6, 1.6, 0.06, 1.2, '#8e8176');
  },
  wardrobe: (k, c) => { k.box(0, 0, 1.05, 1.2, 0.6, 2.1, c); k.box(0, 0.305, 1.05, 0.01, 0.01, 2.0, DARK); for (const s of [-1, 1]) k.box(s * 0.06, 0.31, 1.05, 0.02, 0.02, 0.25, DARK); },
  desk: (k, c) => { k.box(0, 0, 0.73, 1.4, 0.7, 0.04, c); legs4(k, 1.3, 0.6, 0.71, 0.05, c); k.box(0.45, 0, 0.55, 0.4, 0.6, 0.3, c); },
  tv: (k) => { k.box(0, 0, 0.42, 1.2, 0.05, 0.68, '#222428'); k.box(0, 0, 0.03, 0.4, 0.25, 0.06, DARK); k.box(0, 0, 0.07, 0.06, 0.04, 0.1, DARK); },
  plant: (k, c) => { k.cyl(0, 0, 0.15, 0.2, 0.3, '#8b6f5a', 0.15); k.sph(0, 0, 0.75, 0.35, 0.35, 0.45, c); },
  door: (k, c) => {
    for (const s of [-1, 1]) k.box(s * 0.48, 0, 1.06, 0.06, 0.14, 2.12, DARK);
    k.box(0, 0, 2.13, 1.02, 0.14, 0.06, DARK); k.box(0, 0, 1.05, 0.88, 0.05, 2.08, c); k.box(0.35, 0.05, 1.0, 0.1, 0.03, 0.03, '#c9c9c9');
  },
  window: (k, c) => {
    k.box(0, 0, 0.65, 1.2, 0.06, 1.3, c);
    for (const s of [-1, 1]) k.box(s * 0.58, 0, 0.65, 0.05, 0.12, 1.3, DARK);
    for (const z of [0.02, 1.28]) k.box(0, 0, z, 1.2, 0.12, 0.05, DARK);
    k.box(0, 0, 0.65, 0.04, 0.1, 1.26, DARK);
  },
  stairs: (k, c) => { const n = 14; for (let i = 0; i < n; i++) { const h = (i + 1) * 2.8 / n; k.box(0, -1.5 + (i + 0.5) * 3 / n, h / 2, 1.2, 3 / n, h, c); } },
  column: (k, c) => { k.cyl(0, 0, 1.5, 0.25, 3.0, c); k.box(0, 0, 0.05, 0.6, 0.6, 0.1, c); k.box(0, 0, 2.95, 0.6, 0.6, 0.1, c); },
  ceiling_lamp: (k, c) => { k.cyl(0, 0, 0.55, 0.01, 0.5, DARK); k.cyl(0, 0, 0.15, 0.25, 0.25, c, 0.08); k.sph(0, 0, 0.04, 0.07, 0.07, 0.07, '#fff2c4'); },
  street_lamp: (k, c) => { k.cyl(0, 0, 0.15, 0.15, 0.3, c); k.cyl(0, 0, 2.5, 0.07, 5.0, c); k.box(0, 0.6, 4.95, 0.06, 1.2, 0.06, c); k.box(0, 1.2, 4.85, 0.25, 0.45, 0.12, c); k.box(0, 1.2, 4.78, 0.2, 0.38, 0.02, '#fff2c4'); },
  tree: (k, c) => { k.cyl(0, 0, 1.5, 0.15, 3.0, '#6f5a48'); k.sph(0, 0, 4.2, 1.5, 1.5, 1.8, c); },
  car: (k, c) => {
    k.box(0, 0, 0.55, 1.8, 4.4, 0.6, c); k.box(0, -0.2, 1.15, 1.6, 2.2, 0.6, GLASS);
    k.box(0, -0.2, 1.47, 1.58, 2.0, 0.05, c);
    for (const sx of [-1, 1]) for (const sy of [-1, 1]) k.wheel(sx * 0.85, sy * 1.4, 0.33, 0.33, 0.22, '#232427');
    for (const s of [-1, 1]) k.box(s * 0.6, 2.2, 0.65, 0.3, 0.02, 0.12, LIGHT);
  },
  motorbike: (k, c) => {
    for (const s of [-1, 1]) k.wheel(0, s * 0.7, 0.3, 0.3, 0.12, '#232427');
    k.box(0, 0, 0.65, 0.3, 1.2, 0.35, c); k.box(0, -0.2, 0.86, 0.28, 0.6, 0.1, DARK); k.box(0, 0.55, 1.0, 0.7, 0.05, 0.05, DARK);
  },
  bin: (k, c) => { k.cyl(0, 0, 0.5, 0.3, 1.0, c, 0.27); k.cyl(0, 0, 1.02, 0.31, 0.04, DARK); },
  crate: (k, c) => { k.box(0, 0, 0.4, 0.8, 0.8, 0.8, c); for (const z of [0.2, 0.6]) k.box(0, 0.405, z, 0.82, 0.01, 0.06, DARK); },
};
export const PIECE_DIMS = {
  table_rect: [1.4, 0.8, 0.75], table_round: [0.9, 0.9, 0.75], chair: [0.45, 0.48, 0.9], bar_stool: [0.4, 0.4, 0.75],
  sofa: [2.0, 0.9, 0.85], armchair: [0.9, 0.85, 0.85], booth: [1.4, 0.6, 1.15], bench: [1.6, 0.5, 0.8], counter: [3.0, 0.7, 1.1],
  shelf: [1.6, 0.4, 2.0], fridge: [0.7, 0.7, 1.85], register: [0.4, 0.4, 0.3], stall: [2.0, 1.2, 2.3],
  kitchen_counter: [2.4, 0.6, 0.9], stove: [0.6, 0.6, 0.9], sink: [0.8, 0.6, 0.9], bed: [1.6, 2.0, 0.55], wardrobe: [1.2, 0.6, 2.1],
  desk: [1.4, 0.7, 0.75], tv: [1.2, 0.1, 0.7], plant: [0.5, 0.5, 1.2], door: [0.9, 0.12, 2.1], window: [1.2, 0.12, 1.3],
  stairs: [1.2, 3.0, 2.8], column: [0.5, 0.5, 3.0], ceiling_lamp: [0.5, 0.5, 0.8], street_lamp: [0.4, 1.4, 5.0], tree: [3.0, 3.0, 6.0],
  car: [1.8, 4.4, 1.5], motorbike: [0.8, 2.0, 1.1], bin: [0.6, 0.6, 1.0], crate: [0.8, 0.8, 0.8],
};

export function piece(model, color, size) {
  const k = new Kit();
  (BUILD[model] || ((kk, c) => kk.box(0, 0, 0.5, 1, 1, 1, c)))(k, color);
  const d = PIECE_DIMS[model] || [1, 1, 1], s = size || d;
  k.g.scale.set(s[0] / d[0], s[2] / d[2], s[1] / d[1]); // three: x, altura, profundidade
  const outer = new THREE.Group();
  outer.add(k.g);
  return outer;
}

// Prédios: janelas desenhadas no shader a partir da posição no mundo (um andar a cada 3 m; montras no rés-do-chão)
const WINDOWED = new Set(['building', 'building_dark', 'building_light', 'tall', 'low']);
export const hasWindows = (colorName) => WINDOWED.has(colorName);
const winMats = new Map();
export function windowMat(hex) {
  const key = String(hex);
  if (winMats.has(key)) return winMats.get(key);
  const m = new THREE.MeshLambertMaterial({ color: hex });
  m.onBeforeCompile = (sh) => {
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vWinP;\nvarying vec3 vWinN;')
      .replace('#include <begin_vertex>', `#include <begin_vertex>
        vec4 wp = vec4(transformed, 1.0);
        vec3 wn = objectNormal;
        #ifdef USE_INSTANCING
          wp = instanceMatrix * wp; wn = mat3(instanceMatrix) * wn;
        #endif
        vWinP = (modelMatrix * wp).xyz; vWinN = normalize(mat3(modelMatrix) * wn);`);
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vWinP;\nvarying vec3 vWinN;')
      .replace('#include <color_fragment>', `#include <color_fragment>
        if (abs(vWinN.y) < 0.5 && vWinP.y > 0.2) {
          float along = abs(vWinN.x) > abs(vWinN.z) ? vWinP.z : vWinP.x;
          float fy = fract(vWinP.y / 3.0), fx = fract(along / 2.6);
          bool ground = vWinP.y < 3.0;
          float wy = ground ? step(0.12, fy) * step(fy, 0.8) : step(0.35, fy) * step(fy, 0.82);
          float wx = ground ? step(0.1, fx) * step(fx, 0.9) : step(0.22, fx) * step(fx, 0.72);
          diffuseColor.rgb = mix(diffuseColor.rgb, diffuseColor.rgb * vec3(0.36, 0.4, 0.48), wy * wx);
        }`);
  };
  m.customProgramCacheKey = () => 'win' + key;
  winMats.set(key, m);
  return m;
}

// Figura das pessoas (elenco, figurantes, elementos do mundo com corpo): uma cápsula lisa, sem braços nem pernas, com
// uma viseira escura na cabeça a mostrar para onde está virada. Cinzento-claro com um tom da cor de cada um (os
// figurantes ficam só cinzentos). Testado no Seedance (5 de outubro de 2026): quanto mais o boneco parece um corpo
// real, mais o modelo de vídeo o copia (andar rígido, golpes no ar, cores do previs); a cápsula só lhe dá onde está
// cada um, para onde está virado e quando — o corpo e a roupa vêm das folhas e do texto.
const FIGURE_GREY = '#c4c7c6';
const CAPSULE = new THREE.CapsuleGeometry(0.2, 0.95, 8, 20);
const VISOR = new THREE.SphereGeometry(1, 24, 12, Math.PI * 0.18, Math.PI * 0.64, Math.PI * 0.36, Math.PI * 0.22);
const visorMat = new THREE.MeshLambertMaterial({ color: '#2c3236' });
export function mannequin(color, height, extra) {
  const root = new THREE.Group(), body = new THREE.Group();
  root.add(body);
  const tint = extra ? new THREE.Color(FIGURE_GREY) : new THREE.Color(FIGURE_GREY).lerp(new THREE.Color(color), 0.7);
  const m = mat('#' + tint.getHexString());
  const torso = new THREE.Mesh(CAPSULE, m); torso.position.y = 0.68; torso.scale.set(1, 1, 0.72); body.add(torso);
  const head = new THREE.Group(); head.position.y = 1.49; body.add(head);
  const skull = new THREE.Mesh(SPH, m); skull.scale.set(0.11, 0.13, 0.115); head.add(skull);
  const visor = new THREE.Mesh(VISOR, visorMat); visor.scale.set(0.116, 0.136, 0.121);
  visor.rotation.y = Math.PI; // a frente da figura é -Z local
  head.add(visor);
  root.scale.setScalar((height || 1.75) / 1.75);
  return { root, body, torso, head };
}

const D = Math.PI / 180;
export function applyPose(m, pose, phase) {
  m.body.position.set(0, 0, 0); m.body.rotation.set(0, 0, 0);
  m.torso.position.y = 0.68; m.torso.scale.set(1, 1, 0.72); m.head.position.y = 1.49;
  if (pose === 'sit') { // sentada: só o tronco acima do assento (0,47 m)
    m.torso.scale.set(1, 0.62, 0.72); m.torso.position.y = 0.47 + 0.42; m.head.position.y = 1.4;
  } else if (pose === 'bend') { // dobrada para a frente (golpe no corpo, cansaço)
    m.body.rotation.x = -38 * D;
  } else if (pose === 'kneel') { // de joelhos: mais baixa
    m.torso.scale.set(1, 0.7, 0.72); m.torso.position.y = 0.42 + 0.47; m.head.position.y = 1.31;
  } else if (pose === 'lie') { // deitada de costas no chão (para trás de quem estava de pé)
    m.body.rotation.x = 90 * D; m.body.position.y = 0.2;
  } else if (pose === 'walk' || pose === 'run') { // desliza com um leve balanço e inclina-se para a frente
    const run = pose === 'run';
    m.body.position.y = Math.abs(Math.cos(phase)) * (run ? 0.03 : 0.012);
    m.body.rotation.x = -(run ? 8 : 3) * D;
  }
}

// Etiqueta de texto (nome das divisões, na vista livre)
export function label(text) {
  const cv = document.createElement('canvas'); cv.width = 512; cv.height = 128;
  const g = cv.getContext('2d');
  g.fillStyle = 'rgba(20,22,26,0.72)'; g.fillRect(0, 24, 512, 80);
  g.fillStyle = '#ffffff'; g.font = '600 52px system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
  g.fillText(text, 256, 66);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(cv), depthTest: false }));
  sp.scale.set(4, 1, 1);
  return sp;
}
