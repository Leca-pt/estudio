// Modelos 3D importados (GLB) nas maquetes: carregados uma vez por endereço, desenhados em cinzento (só a forma, como
// o resto da maquete) e ajustados ao tamanho pedido. Enquanto carregam fica uma caixa no lugar; os renders e o
// vídeo-guia esperam por eles (modelsReady).
import * as THREE from '../vendor/three/three.module.min.js';
import { GLTFLoader } from '../vendor/three/GLTFLoader.js';

const cache = new Map(); // url → Promise<Object3D | null> (as normais em falta calculam-se ao usar)
const pending = new Set();
const loader = new GLTFLoader();

function load(url) {
  if (!cache.has(url)) {
    const p = new Promise((resolve) => {
      loader.load(url, (gltf) => resolve(gltf.scene), undefined, (err) => { console.warn('modelo 3D não carregou', url, err); resolve(null); });
    });
    cache.set(url, p);
    pending.add(p);
    p.finally(() => pending.delete(p));
  }
  return cache.get(url);
}

// Todos os modelos pedidos até agora já chegaram (com ou sem erro)
export const modelsReady = () => Promise.all([...pending]);

// Grupo com o modelo (cinzento, na cor dada), com a base em 0 e centrado em planta, esticado para size [x, y, z] da
// cena ou, só com a altura, à escala. Sem size nem height fica no tamanho do ficheiro (metros).
export function greyModel(url, material, size, heightOnly, onReady) {
  const outer = new THREE.Group();
  const ph = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), material); // caixa no lugar, enquanto carrega
  const s0 = size || [1, 1, heightOnly || 1];
  ph.scale.set(s0[0], s0[2], s0[1]); ph.position.y = s0[2] / 2;
  outer.add(ph);
  load(url).then((src) => {
    if (!src) { outer.userData.failed = true; return; }
    const obj = src.clone(true);
    obj.rotation.y = Math.PI; // glTF: a frente é +Z; na maquete a frente é +Y da cena (−Z do three)
    obj.traverse((o) => { if (o.isMesh) { if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals(); o.material = material; o.castShadow = false; } });
    obj.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(obj), dim = new THREE.Vector3(), mid = new THREE.Vector3();
    box.getSize(dim); box.getCenter(mid);
    const holder = new THREE.Group();
    obj.position.set(-mid.x, -box.min.y, -mid.z);
    holder.add(obj);
    if (size) holder.scale.set(size[0] / (dim.x || 1), size[2] / (dim.y || 1), size[1] / (dim.z || 1)); // cena: x, y (fundo), z (altura)
    else if (heightOnly) holder.scale.setScalar(heightOnly / (dim.y || 1));
    outer.remove(ph);
    outer.add(holder);
    if (onReady) onReady();
  });
  return outer;
}

// Pré-visualização na ficha do asset: o modelo a rodar devagar, em cinzento, com as medidas
export function preview(el) {
  if (el.dataset.mounted) return;
  el.dataset.mounted = '1';
  const canvas = document.createElement('canvas');
  el.prepend(canvas);
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xcfd6de);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x5a5a60, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.4); sun.position.set(-3, 6, 4); scene.add(sun);
  const cam = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
  const mat = new THREE.MeshLambertMaterial({ color: 0x9c9da3 });
  const info = el.querySelector('[data-model-info]');
  let model = null, angle = 0, radius = 3, ty = 0.5;
  const group = greyModel(el.dataset.modelUrl, mat, null, null, () => {
    const box = new THREE.Box3().setFromObject(group), dim = new THREE.Vector3();
    box.getSize(dim);
    radius = Math.max(dim.x, dim.y, dim.z) * 1.6 || 3; ty = dim.y / 2;
    if (info) info.textContent = `${dim.x.toFixed(2)} × ${dim.z.toFixed(2)} × ${dim.y.toFixed(2)} m (largura × fundo × altura, no ficheiro)`;
    model = group;
  });
  scene.add(group);
  setTimeout(() => { if (!model && info && group.userData.failed) info.textContent = 'Não foi possível ler este modelo (comprimido com Draco ou Meshopt?). Exporta-o sem compressão.'; }, 8000);
  const fit = () => { const w = el.clientWidth || 300, h = Math.round(w * 0.62); renderer.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix(); };
  fit();
  new ResizeObserver(fit).observe(el);
  const tick = () => {
    if (!el.isConnected) { renderer.dispose(); return; }
    angle += 0.006;
    cam.position.set(Math.sin(angle) * radius, ty + radius * 0.35, Math.cos(angle) * radius);
    cam.lookAt(0, ty, 0);
    renderer.render(scene, cam);
    requestAnimationFrame(tick);
  };
  tick();
}
