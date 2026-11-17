// 3D preview: one reused three.js renderer in the detail panel. Splats through Spark,
// meshes at a light LOD with the asset's PBR maps, surfaces on a sphere, HDRIs as a backdrop.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";
import { OBJLoader } from "three/addons/loaders/OBJLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

const BG = 0x1f1f21;
const EXT_RANK = { jpg: 0, jpeg: 0, png: 1, webp: 1, tif: 2, tiff: 2, tga: 3, bmp: 3, exr: 4, hdr: 4 };
const resNum = (r) => parseInt(r || "0", 10) || 0;
const COLOR_MAPS = new Set(["albedo", "translucency", "specular", "emission"]);
const CONVERT = ["usdz", "usd", "usdc", "usda", "abc"];  // previewed through a server-side GLB conversion

// Some files' clips carry a broken track (an FBX rotation curve that loads with the wrong number of values).
// Playing one gives the model a NaN transform and it vanishes, so only well-formed tracks are kept.
const TRACK_SIZE = { quaternion: 4, position: 3, scale: 3 };  // getValueSize() is derived from the data, so it can't tell
function saneClips(clips) {
  const ok = (t) => {
    const n = t.times.length, want = TRACK_SIZE[t.name.split(".").pop()];
    return n > 0 && (want ? t.values.length === n * want : t.values.length % n === 0) && !t.values.some(Number.isNaN);
  };
  return (clips || []).map((c) => { c.tracks = c.tracks.filter(ok); return c; })
    .filter((c) => c.tracks.length && c.duration > 0);
}
let sparkMod = null;
// Releasing the viewer mid-sort makes Spark reject that sort with "Worker terminate": expected, not an error
window.addEventListener("unhandledrejection", (e) => { if (e.reason && e.reason.message === "Worker terminate") e.preventDefault(); });

export class Viewer {
  constructor(host) {
    this.host = host;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, preserveDrawingBuffer: false });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.0;
    host.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(BG);
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.roomEnv = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.environment = this.roomEnv;
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.autoRotate = true;
    this.controls.autoRotateSpeed = 1.2;
    this.controls.addEventListener("start", () => { this.controls.autoRotate = false; });
    this.root = new THREE.Group();
    this.scene.add(this.root);
    this.textures = {};
    this.channel = "lit";
    this.token = 0;
    this.mixer = null;
    this.clips = [];
    this.playing = false;
    this.timer = new THREE.Timer();
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(host);
    if (window.APP_DEBUG) window.viewer = this;  // ?debug=1: inspect the live viewer from the console or a test
    this.resize();
    this.renderer.setAnimationLoop((t) => {
      this.timer.update(t);
      if (!this.host.isConnected || this.host.offsetParent === null) return;
      if (this.mixer && this.playing) this.mixer.update(Math.min(this.timer.getDelta(), 0.1));
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
    });
  }

  resize() {
    const w = this.host.clientWidth || 1, h = this.host.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  clear() {
    this.token++;
    if (this.mixer) { this.mixer.stopAllAction(); this.mixer.uncacheRoot(this.mixer.getRoot()); }
    this.mixer = null; this.clips = []; this.playing = false;
    this.root.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      const mats = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
      mats.forEach((m) => m.dispose && m.dispose());
      if (o.dispose && o !== this.root) try { o.dispose(); } catch (e) { /* splats */ }
    });
    this.root.clear();
    Object.values(this.textures).forEach((t) => t.dispose());
    this.textures = {};
    this.originals = new Map();
    this.scene.background = new THREE.Color(BG);
    this.scene.environment = this.roomEnv;
    this.channel = "lit";
    this.controls.autoRotate = true;
    this.wire = null;
  }

  // ---------------------------------------------------------------- entry
  async show(asset, listing, onStatus = () => {}) {
    this.clear();
    const tok = this.token;
    const files = listing.files || [];
    this.asset = asset;
    try {
      if (asset.type === "splat") await this.showSplat(files, tok, onStatus);
      else if (asset.type === "hdri") await this.showHdri(asset, tok);
      else if (["3d", "plant"].includes(asset.type)) await this.showMesh(asset, files, tok, onStatus);
      else await this.showSurface(asset, files, tok);
      if (tok === this.token) onStatus("");
    } catch (e) {
      console.error(e);
      if (tok === this.token) onStatus(String(e.message || e) + (window.APP_DEBUG ? "\n" + String(e.stack || "").split("\n").slice(0, 8).join("\n") : ""), true);
    }
    return this.channels();
  }

  channels() {
    return ["lit", ...["albedo", "normal", "roughness", "gloss", "metalness", "ao", "displacement", "opacity", "translucency"]
      .filter((k) => this.textures[k])];
  }

  // ---------------------------------------------------------------- textures
  pickMap(files, type, lod) {
    const c = files.filter((f) => f.role === "map" && f.kind === type && f.variant !== "DX");
    if (!c.length) return null;
    const pool = (lod && c.some((f) => f.lod === lod)) ? c.filter((f) => f.lod === lod) : (c.filter((f) => !f.lod).length ? c.filter((f) => !f.lod) : c);
    // smallest resolution at or above 1K decodes fastest; the server resizes to 1024 anyway
    return pool.slice().sort((a, b) => (EXT_RANK[a.name.split(".").pop().toLowerCase()] ?? 9) - (EXT_RANK[b.name.split(".").pop().toLowerCase()] ?? 9)
      || Math.abs(resNum(a.res) - 2) - Math.abs(resNum(b.res) - 2))[0];
  }

  async loadTex(file, kind) {
    const tex = await new THREE.TextureLoader().loadAsync(file.url + "?s=1024");
    tex.colorSpace = COLOR_MAPS.has(kind) ? THREE.SRGBColorSpace : THREE.NoColorSpace;
    tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
    tex.anisotropy = 8;
    return tex;
  }

  async loadMaps(files, lod, tok) {
    const kinds = ["albedo", "normal", "roughness", "gloss", "metalness", "ao", "displacement", "opacity", "translucency", "bump"];
    await Promise.all(kinds.map(async (k) => {
      const f = this.pickMap(files, k, lod);
      if (!f) return;
      try {
        const t = await this.loadTex(f, k);
        if (tok === this.token) this.textures[k] = t; else t.dispose();
      } catch (e) { console.warn("map", k, e); }
    }));
  }

  pbrMaterial(opts = {}) {
    const T = this.textures;
    const m = new THREE.MeshStandardMaterial({
      map: T.albedo || null, normalMap: T.normal || null, roughnessMap: T.roughness || null,
      metalnessMap: T.metalness || null, aoMap: T.ao || null, alphaMap: T.opacity || null,
      roughness: T.roughness || T.gloss ? 1 : 0.6, metalness: T.metalness ? 1 : 0,
      transparent: false, alphaTest: T.opacity ? 0.45 : 0, side: T.opacity || opts.doubleSide ? THREE.DoubleSide : THREE.FrontSide,
    });
    if (T.gloss && !T.roughness) {  // gloss = inverted roughness
      m.roughnessMap = T.gloss;
      m.onBeforeCompile = (sh) => { sh.fragmentShader = sh.fragmentShader.replace("roughnessFactor *= texelRoughness.g;", "roughnessFactor *= 1.0 - texelRoughness.g;"); };
    }
    if (opts.displacement && T.displacement) {
      m.displacementMap = T.displacement;
      m.displacementScale = opts.displacement;
      m.displacementBias = -opts.displacement / 2;
    }
    return m;
  }

  setChannel(ch) {
    this.channel = ch;
    this.root.traverse((o) => {
      if (!o.isMesh || !this.originals.has(o)) return;
      const lit = this.originals.get(o);
      if (ch === "lit") { o.material = lit; return; }
      const t = this.textures[ch];
      if (!t) return;
      o.material = new THREE.MeshBasicMaterial({ map: t, side: THREE.DoubleSide });
    });
    if (this.wire) this.wire.visible = ch === "proxy" ? true : this.wire.userData.keep;
  }

  // ---------------------------------------------------------------- framing
  // Bounds of a splat without a proxy from its visible splat centres, 3rd-97th percentile per axis: a few far
  // floaters (sparse dust, stray background splats) would otherwise shrink the subject to a dot.
  splatBox(mesh) {
    const n = (mesh.packedSplats && mesh.packedSplats.numSplats) || mesh.numSplats || 0;
    const step = Math.max(1, Math.floor(n / 80000));
    const xs = [], ys = [], zs = [];
    mesh.forEachSplat((i, c, s, q, o) => { if (i % step === 0 && o > 0.15) { xs.push(c.x); ys.push(c.y); zs.push(c.z); } });
    if (xs.length < 50) return null;
    const pct = (a, t) => a[Math.floor(t * (a.length - 1))];
    for (const a of [xs, ys, zs]) a.sort((x, y) => x - y);
    const box = new THREE.Box3(new THREE.Vector3(pct(xs, 0.03), pct(ys, 0.03), pct(zs, 0.03)),
                               new THREE.Vector3(pct(xs, 0.97), pct(ys, 0.97), pct(zs, 0.97)));
    mesh.updateMatrixWorld(true);
    return box.applyMatrix4(mesh.matrixWorld);
  }

  frame(obj, { elevation = 0.18, fill = 1.15, box = null } = {}) {
    box = box || new THREE.Box3().setFromObject(obj);
    if (box.isEmpty()) return;
    const c = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3());
    const r = Math.max(size.length() / 2, 1e-3);
    const d = (r * fill) / Math.sin(THREE.MathUtils.degToRad(this.camera.fov / 2));
    this.camera.near = d / 100; this.camera.far = d * 100; this.camera.updateProjectionMatrix();
    this.camera.position.set(c.x + d * 0.25, c.y + d * elevation, c.z + d * 0.97);
    this.controls.target.copy(c);
    this.controls.update();
  }

  // ---------------------------------------------------------------- splats
  async showSplat(files, tok, onStatus) {
    // Spark 2.1 reads gzip SPZ (v2/v3) but not SPZ v4, so v4 previews come from the PLY
    const rank = { spz: 0, splat: 1, ksplat: 2, ply: 3 };
    const ok = (f) => f.role === "splat" && f.kind in rank && !(f.kind === "spz" && (f.version || 0) >= 4);
    // one splat per layer (a layered pack's splats load together), the best format of each
    const byLayer = new Map();
    for (const f of files.filter(ok).sort((a, b) => rank[a.kind] - rank[b.kind])) if (!byLayer.has(f.layer || "")) byLayer.set(f.layer || "", f);
    const picks = [...byLayer.values()];
    if (!picks.length) throw new Error("No previewable splat format (needs PLY, SPZ, SPLAT or KSPLAT)");
    if (!sparkMod) sparkMod = await import("/web/vendor/spark/spark.module.js");
    if (!this.spark) { this.spark = new sparkMod.SparkRenderer({ renderer: this.renderer }); this.scene.add(this.spark); }
    let mesh = null;
    for (const sp of picks) {
      const what = sp.layer ? sp.layer + " " : "";
      // Spark's url loader misreads the server's streamed files ("Invalid gzip header"); bytes + explicit type work
      const bytes = await this.fetchBytes(sp.url, (mb, tot) => onStatus(`Loading ${what}splat… ${mb} / ${tot} MB`));
      if (tok !== this.token) return;
      onStatus("Decoding " + what + (sp.points ? (sp.points / 1e6).toFixed(1) + "M splats" : "splats") + "…");
      const m = new sparkMod.SplatMesh({ fileBytes: bytes, fileType: sp.kind });
      // File frames (ASSET_FORMAT.md): a 3DGS PLY is Y-down; turning it 180 about Z gives the glTF/SuperSplat
      // frame the proxies use (upright AND facing the same way; an X turn is upright but faces backwards).
      // The library's SPZ already is that Z-turned PLY, so it needs no turn.
      if (sp.kind === "ply" || sp.kind === "splat" || sp.kind === "ksplat") m.quaternion.setFromEuler(new THREE.Euler(0, 0, Math.PI));
      this.root.add(m);
      await m.initialized;
      if (tok !== this.token) return;
      mesh = mesh || m;  // frame on the first layer; a sparse layer (e.g. a star field) can reach far wider
    }
    const proxy = files.find((f) => f.role === "mesh" && ["glb", "gltf"].includes(f.kind));
    let frameObj = mesh;
    if (proxy) {
      const g = await new GLTFLoader().loadAsync(proxy.url);
      if (tok !== this.token) return;
      g.scene.traverse((o) => { if (o.isMesh) o.material = new THREE.MeshBasicMaterial({ color: 0xd6aa4f, wireframe: true, transparent: true, opacity: 0.35 }); });
      g.scene.visible = false;
      g.scene.userData.keep = false;
      this.wire = g.scene;
      this.root.add(g.scene);
      frameObj = g.scene;  // proxy bounds frame tighter than splat floaters
      g.scene.visible = true; this.frame(g.scene, { elevation: 0.12, fill: 0.9 }); g.scene.visible = false;
      return;
    }
    let box = null;
    try { box = this.splatBox(mesh); } catch (e) { box = null; }  // falls back to the full bounds
    this.frame(frameObj, { elevation: 0.12, fill: box ? 1.25 : 0.9, box });
  }

  async fetchBytes(url, progress) {
    const r = await fetch(url);
    if (!r.ok) throw new Error("Couldn't load " + url.split("/").pop() + " (" + r.status + ")");
    const total = +r.headers.get("content-length") || 0;
    if (!total || !r.body) return new Uint8Array(await r.arrayBuffer());
    const out = new Uint8Array(total);
    const reader = r.body.getReader();
    let got = 0, last = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      out.set(value, got); got += value.length;
      if (got - last > 16e6) { last = got; progress((got / 1e6) | 0, (total / 1e6) | 0); }
    }
    return out;
  }

  toggleProxy(on) {
    if (this.wire) { this.wire.visible = on; this.wire.userData.keep = on; }
  }

  // ---------------------------------------------------------------- meshes
  // The browser reads GLB/glTF, FBX and OBJ itself. USD and Alembic go through the server, which converts them
  // once to a GLB (Blender, in the background) and caches it.
  pickMesh(files) {
    const order = ["glb", "gltf", "fbx", "obj", ...CONVERT];
    const meshes = files.filter((f) => f.role === "mesh" && order.includes(f.kind) && !f.proxy);
    if (!meshes.length) return null;
    const lodN = (f) => { const m = /LOD(\d+)/i.exec(f.lod || ""); return m ? +m[1] : (f.lod === "High" ? -1 : 0); };
    const conv = (f) => (CONVERT.includes(f.kind) ? 1 : 0);
    // a mid LOD previews fast and still looks like the asset; per-res variants (Poly Haven): smallest
    return meshes.slice().sort((a, b) => {
      const la = lodN(a), lb = lodN(b);
      const pa = la >= 0 ? Math.abs(la - 2) : 9, pb = lb >= 0 ? Math.abs(lb - 2) : 9;
      return conv(a) - conv(b) || pa - pb || resNum(a.res) - resNum(b.res) || order.indexOf(a.kind) - order.indexOf(b.kind);
    })[0];
  }

  async converted(asset, f, onStatus) {
    onStatus("Converting " + f.kind.toUpperCase() + " for the preview (Blender, first time only)…");
    const r = await fetch("/api/preview-mesh", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: asset.id, idx: f.idx }) }).then((x) => x.json());
    if (!r.ok) throw new Error(r.error || "Couldn't convert " + f.name + " for the preview");
    return { ...f, kind: "glb", url: r.url };
  }

  async loadModel(f) {
    const mgr = new THREE.LoadingManager();
    mgr.setURLModifier((u) => (/\.(jpe?g|png|tga|tiff?|bmp|exr|dds|psd)(\?|$)/i.test(u) && !u.includes("/asset-file/")) ? "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=" : u);
    if (f.kind === "fbx") { const o = await new FBXLoader(mgr).loadAsync(f.url); return { obj: o, clips: saneClips(o.animations) }; }
    if (f.kind === "obj") return { obj: await new OBJLoader(mgr).loadAsync(f.url), clips: [] };
    const g = await new GLTFLoader(mgr).loadAsync(f.url);
    return { obj: g.scene, clips: saneClips(g.animations) };
  }

  async showMesh(asset, files, tok, onStatus) {
    let f = this.pickMesh(files);
    if (!f) throw new Error(asset.native ? "This asset ships only a .c4d scene, so there's no mesh to preview" : "No previewable mesh (GLB, FBX, OBJ, USD or Alembic)");
    if (CONVERT.includes(f.kind)) {
      f = await this.converted(asset, f, onStatus);
      if (tok !== this.token) return;
    }
    onStatus("Loading " + f.kind.toUpperCase() + (f.lod ? " " + f.lod : "") + "…");
    const [{ obj, clips }] = await Promise.all([this.loadModel(f), this.loadMaps(files, f.lod, tok)]);
    if (tok !== this.token) return;
    const parts = files.filter((x) => x.role === "part_map");
    const partTex = {};
    if (parts.length) {
      for (const p of parts.filter((x) => x.kind === "albedo" || x.kind === "normal" || x.kind === "opacity" || x.kind === "roughness")) {
        const k = p.part.toLowerCase().replace(/[^a-z0-9]/g, "");
        partTex[k] = partTex[k] || {};
        if (!partTex[k][p.kind]) partTex[k][p.kind] = p;
      }
    }
    const hasOwn = Object.keys(this.textures).length > 0;
    const shared = hasOwn ? this.pbrMaterial({ doubleSide: asset.type === "plant" }) : null;
    const partMats = {};
    const jobs = [];
    obj.traverse((o) => {
      if (!o.isMesh) return;
      o.castShadow = o.receiveShadow = true;
      if (shared) { o.material = shared; }
      else if (Object.keys(partTex).length) {
        const mats = (Array.isArray(o.material) ? o.material : [o.material]).map((m) => {
          const nm = (m.name || "").toLowerCase().replace(/[^a-z0-9]/g, "");
          const key = Object.keys(partTex).filter((k) => nm.includes(k) || (nm.length >= 3 && k.includes(nm))).sort((a, b) => b.length - a.length)[0];
          if (!key) return m;
          if (!partMats[key]) {
            partMats[key] = new THREE.MeshStandardMaterial({ roughness: 0.7, side: THREE.DoubleSide });
            const pm = partMats[key], pt = partTex[key];
            jobs.push(...Object.entries(pt).map(async ([kind, file]) => {
              const t = await this.loadTex(file, kind);
              if (kind === "albedo") pm.map = t; else if (kind === "normal") pm.normalMap = t;
              else if (kind === "roughness") pm.roughnessMap = t; else if (kind === "opacity") { pm.alphaMap = t; pm.alphaTest = 0.45; }
              pm.needsUpdate = true;
            }));
          }
          return partMats[key];
        });
        o.material = Array.isArray(o.material) ? mats : mats[0];
      }
      this.originals.set(o, o.material);
    });
    await Promise.all(jobs);
    if (tok !== this.token) return;
    this.root.add(obj);
    this.frame(obj);
    if (clips.length) {
      this.mixer = new THREE.AnimationMixer(obj);
      this.clips = clips;
      this.playClip(0);
    }
  }

  // ---------------------------------------------------------------- animation (glTF / FBX clips)
  playClip(i) {
    if (!this.mixer || !this.clips[i]) return;
    this.mixer.stopAllAction();
    this.mixer.clipAction(this.clips[i]).reset().play();
    this.clip = i;
    this.playing = true;
  }

  setPlaying(on) { this.playing = !!on && !!this.mixer; }

  // ---------------------------------------------------------------- surfaces / atlases / decals
  async showSurface(asset, files, tok) {
    await this.loadMaps(files, null, tok);
    if (tok !== this.token) return;
    const flat = asset.type === "atlas" || asset.type === "decal";
    const geo = flat ? new THREE.PlaneGeometry(2, 2, 1, 1) : new THREE.SphereGeometry(1, 256, 128);
    const mesh = new THREE.Mesh(geo, this.pbrMaterial({ displacement: flat ? 0 : 0.06, doubleSide: flat }));
    if (!flat && this.textures.albedo) mesh.material.map.repeat.set(2, 1);
    ["map", "normalMap", "roughnessMap", "metalnessMap", "aoMap", "displacementMap"].forEach((k) => {
      const t = mesh.material[k];
      if (t && !flat) t.repeat.set(2, 1);
    });
    this.originals.set(mesh, mesh.material);
    this.root.add(mesh);
    if (flat) this.controls.autoRotate = false;
    this.frame(mesh, { elevation: flat ? 0 : 0.15, fill: flat ? 0.75 : 1.05 });
  }

  // ---------------------------------------------------------------- HDRI
  async showHdri(asset, tok) {
    const tex = await new THREE.TextureLoader().loadAsync("/preview/" + encodeURIComponent(asset.id) + "?s=2048");
    if (tok !== this.token) { tex.dispose(); return; }
    tex.mapping = THREE.EquirectangularReflectionMapping;
    tex.colorSpace = THREE.SRGBColorSpace;
    this.textures.hdri = tex;
    this.scene.background = tex;
    this.scene.environment = tex;
    const chrome = new THREE.Mesh(new THREE.SphereGeometry(0.42, 96, 48), new THREE.MeshStandardMaterial({ metalness: 1, roughness: 0.04 }));
    const matte = new THREE.Mesh(new THREE.SphereGeometry(0.42, 96, 48), new THREE.MeshStandardMaterial({ color: 0xbdbdbd, metalness: 0, roughness: 0.85 }));
    chrome.position.x = -0.5; matte.position.x = 0.5;
    this.root.add(chrome, matte);
    this.controls.autoRotateSpeed = 0.6;
    this.camera.near = 0.01; this.camera.far = 100; this.camera.updateProjectionMatrix();
    this.camera.position.set(0, 0.25, 3.0);
    this.controls.target.set(0, 0, 0);
    this.controls.update();
  }

  // Hand everything back: scene, textures, the splat sorter and the WebGL context itself. The app calls this when
  // the 3D preview closes and builds a new viewer the next time one opens.
  dispose() {
    this.clear();
    this.renderer.setAnimationLoop(null);
    this.ro.disconnect();
    this.controls.dispose();
    if (this.spark) {
      this.scene.remove(this.spark);
      try { if (this.spark.dispose) this.spark.dispose(); } catch (e) { /* nothing more to free */ }
      this.spark = null;
    }
    this.roomEnv.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.renderer.domElement.remove();
  }
}
