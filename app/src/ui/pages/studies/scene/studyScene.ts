// The study 3D view (Studies page and the immersive view): one Three.js scene that shows either
// the part being set up (with load / support markers and face picking) or a result GLB with its
// channels (recolor, adjustable range, deformed shape × scale, mode-shape animation, a section
// plane with the core's section values, a probe marker) and, for wind, particles moving along
// the velocity slice (or static streamlines with reduced motion).
//
// Frames: rendered on demand; a requestAnimationFrame loop runs only while something moves
// (mode shapes, particles, the results fading in) and the page is visible. Everything the scene
// made is disposed on dispose(): geometries, materials, textures, controls, the renderer.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import type { StudySection } from "../../../../api/studies";
import type { PartFace } from "../../../../api/types";
import { EDGE_THRESHOLD_DEG, MM_PER_GLTF_UNIT, type PartScene } from "../../../viewer/loadPart";
import { fitOrtho, ISO_AZIMUTH_DEG, ISO_ELEVATION_DEG, type RendererLike } from "../../../viewer/scene";
import { colormap, type Marker } from "../model";
import {
  advect, channelValues, colorize, dirVec, faceAnchor, finiteRange, flowField, matchFace, planePoint, readChannels, seedParticles,
  streamlines, threeAttribute, type Bounds, type Channel, type FlowField, type Particles, type V3,
} from "./fields";

export interface SceneColors { ink: string; dim: string; selected: string; edge: string; paper: string }

export function themeColors(): SceneColors {
  const css = typeof getComputedStyle === "function" && typeof document !== "undefined" ? getComputedStyle(document.documentElement) : null;
  const v = (name: string, d: string) => css?.getPropertyValue(name).trim() || d;
  return { ink: v("--ink", "#ececec"), dim: v("--dim", "#9a9a9a"), selected: v("--blue", "#3d8bff"), edge: v("--mesh-edge", "#e6e6e6"), paper: v("--paper", "#111111") };
}

export interface ResultInfo {
  channels: Channel[];
  /** The type-level field the GLB was colored by (von_mises, mode_1, temperature, cp). */
  field: string;
  bounds: Bounds;
  /** Mode frequencies (modal) or factors (buckling), by channel name. */
  modes: { name: string; freq_hz?: number; factor?: number }[];
}

type SceneRenderer = RendererLike & { localClippingEnabled?: boolean; forceContextLoss?: () => void };

export interface StudySceneDeps {
  createRenderer?: (canvas: HTMLCanvasElement) => SceneRenderer;
  reducedMotion?: () => boolean;
  colors?: () => SceneColors;
  now?: () => number;
}

export interface StudySceneHandle {
  /** The design model (setup): markers and face picks go on it. Takes ownership. */
  setPart(p: PartScene | null): void;
  /** A result GLB (replaces any shown result). */
  loadResult(bytes: ArrayBuffer): Promise<ResultInfo>;
  clearResult(): void;
  /** Recolor by a channel (vector channels by magnitude) over [lo, hi] (null = the channel's range). */
  setChannel(name: string, range?: [number, number] | null): [number, number] | null;
  /** Deformed shape: displacement × scale (static / drop), or the mode shape's amplitude. */
  setDeform(scale: number): void;
  /** Animate the shown mode shape (or stop). Static frame with reduced motion. */
  setAnimate(on: boolean): void;
  setMarkers(markers: readonly Marker[], faces: readonly PartFace[], selectedCase: number | null): void;
  /** Wind: particles on this velocity section (null: none). */
  setFlow(section: StudySection | null, frame?: readonly (readonly number[])[] | null): void;
  /** A section plane: clips the result and shows the core's section values on it. */
  setSection(s: { axis: "x" | "y" | "z"; offset: number; values: StudySection | null; range: [number, number] } | null): void;
  setProbe(point: V3 | null): void;
  /** What's under the pointer (client px): the part point and normal, and the matching face id. */
  pick(clientX: number, clientY: number, faces?: readonly PartFace[]): { point: V3; normal: V3; face: string | null } | null;
  bounds(): Bounds | null;
  fit(): void;
  resize(w: number, h: number): void;
  /** Screen position (px in the canvas) of a part point, for HTML labels. */
  project(p: V3): [number, number] | null;
  /** Test / e2e state. */
  state(): { channel: string | null; animating: boolean; particles: number; streamlines: number; markers: number; section: boolean; frames: number };
  dispose(): void;
}

function defaultRenderer(canvas: HTMLCanvasElement): SceneRenderer {
  return new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
}

function parseGlb(bytes: ArrayBuffer): Promise<{ scene: THREE.Group; extras: unknown }> {
  return new Promise((ok, fail) => {
    new GLTFLoader().parse(bytes, "", (g) => ok({ scene: g.scene, extras: (g.asset as { extras?: unknown }).extras }),
      (err) => fail(err instanceof Error ? err : new Error(String(err instanceof ErrorEvent ? err.message : err))));
  });
}

function disposeTree(o: THREE.Object3D): void {
  o.traverse((x) => {
    const m = x as THREE.Mesh;
    if (m.geometry) m.geometry.dispose();
    const mats = m.material ? (Array.isArray(m.material) ? m.material : [m.material]) : [];
    for (const mat of mats) {
      for (const v of Object.values(mat)) if (v instanceof THREE.Texture) v.dispose();
      mat.dispose();
    }
  });
  o.removeFromParent();
}

interface ResultMesh {
  mesh: THREE.Mesh; base: Float32Array; attrs: Map<string, THREE.BufferAttribute>;
  colorBuf: Float32Array; values: Float32Array | null; range: [number, number];
}

export function createStudyScene(canvas: HTMLCanvasElement, deps: StudySceneDeps = {}): StudySceneHandle {
  const renderer = (deps.createRenderer ?? defaultRenderer)(canvas);
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(typeof window === "undefined" ? 1 : Math.min(window.devicePixelRatio || 1, 2));
  if ("localClippingEnabled" in renderer || renderer instanceof THREE.WebGLRenderer) renderer.localClippingEnabled = true;
  const reduced = deps.reducedMotion ?? (() => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches);
  const colors = deps.colors ?? themeColors;
  const now = deps.now ?? (() => performance.now());

  const scene = new THREE.Scene();
  scene.add(new THREE.AmbientLight(0xffffff, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 0.9);
  key.position.set(-1, -2, 3);
  scene.add(key);
  const camera = new THREE.OrthographicCamera(-50, 50, 50, -50, 0.1, 1000);
  camera.up.set(0, 0, 1);
  const az = THREE.MathUtils.degToRad(ISO_AZIMUTH_DEG); const el = THREE.MathUtils.degToRad(ISO_ELEVATION_DEG);
  camera.position.set(Math.cos(el) * Math.sin(az), -Math.cos(el) * Math.cos(az), Math.sin(el)).multiplyScalar(200);
  camera.lookAt(0, 0, 0);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = false;
  // the browser may drop the GL context (too many contexts, GPU reset): keep the canvas and
  // redraw when it comes back instead of leaving a blank view
  const onLost = (e: Event) => { e.preventDefault(); };
  const onRestored = () => { requestRender(); };
  canvas.addEventListener("webglcontextlost", onLost);
  canvas.addEventListener("webglcontextrestored", onRestored);

  let width = 1; let height = 1;
  let frame = 0; let loop = 0; let frames = 0;
  let disposed = false;
  let part: PartScene | null = null;
  let result: { root: THREE.Group; meshes: ResultMesh[]; info: ResultInfo } | null = null;
  let channel: string | null = null;
  let deform = 0;
  let animate = false;
  let revealUntil = 0;
  const markers = new THREE.Group(); scene.add(markers);
  const flowGroup = new THREE.Group(); scene.add(flowGroup);
  const sectionGroup = new THREE.Group(); scene.add(sectionGroup);
  const probeGroup = new THREE.Group(); scene.add(probeGroup);
  let flow: { field: FlowField; particles: Particles | null; lines: THREE.LineSegments; scale: number; rot: THREE.Matrix3 | null; staticLines: number } | null = null;
  let clip: THREE.Plane | null = null;
  let lastT = now();
  const t0 = now();

  const draw = () => {
    frame = 0;
    if (disposed) return;
    frames++;
    try { renderer.render(scene, camera); } catch { /* never throws into the UI */ }
  };
  const requestRender = () => { if (!frame && !disposed) frame = requestAnimationFrame(draw); };
  controls.addEventListener("change", requestRender);

  const moving = () => !disposed && !reduced() && ((animate && result !== null) || (flow?.particles != null) || now() < revealUntil);
  const tick = () => {
    loop = 0;
    if (!moving()) { requestRender(); return; }
    const t = now();
    const dt = Math.min(0.05, (t - lastT) / 1000);
    lastT = t;
    if (typeof document === "undefined" || !document.hidden) {
      if (now() < revealUntil + 20 && result && channel) recolor(Math.min(1, 1 - (revealUntil - t) / 700));
      if (animate && result) applyDeform(Math.sin(((t - t0) / 1000) * Math.PI * 1.25));
      if (flow?.particles) { advect(flow.field, flow.particles, dt, flow.scale); writeParticles(); }
      draw();
    }
    loop = requestAnimationFrame(tick);
  };
  const kick = () => { if (!loop && moving()) { lastT = now(); loop = requestAnimationFrame(tick); } else requestRender(); };

  // ---- result coloring and deformation

  function recolor(reveal = 1): void {
    if (!result) return;
    for (const rm of result.meshes) {
      if (!rm.values) continue;
      colorize(rm.values, rm.range[0], rm.range[1], rm.colorBuf, reveal);
      const c = rm.mesh.geometry.getAttribute("color") as THREE.BufferAttribute;
      c.needsUpdate = true;
    }
  }

  function deformAttr(rm: ResultMesh): THREE.BufferAttribute | null {
    if (!result) return null;
    const ch = result.info.channels;
    const shown = ch.find((c) => c.name === channel);
    // a mode shape deforms by itself; otherwise the displacement field
    const name = shown && /^mode_/.test(shown.name) ? shown : ch.find((c) => c.name === "displacement");
    return name ? rm.attrs.get(threeAttribute(name.attribute)) ?? null : null;
  }

  function applyDeform(phase = 1): void {
    if (!result) return;
    const diag = Math.hypot(...result.info.bounds.max.map((x, i) => x - result!.info.bounds.min[i])) / MM_PER_GLTF_UNIT;
    for (const rm of result.meshes) {
      const pos = rm.mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
      const arr = pos.array as Float32Array;
      const d = deformAttr(rm);
      const isMode = d !== null && /^_mode_/.test(d.name || "");
      // displacement is in mm (buffer in m: /1000); a mode shape is normalized: 8% of the size × scale
      const k = d === null ? 0 : isMode ? 0.08 * diag * deform * phase : (deform * phase) / MM_PER_GLTF_UNIT;
      const src = d?.array as Float32Array | undefined;
      for (let i = 0; i < arr.length; i++) arr[i] = rm.base[i] + (src && d?.itemSize === 3 ? src[i] * k : 0);
      pos.needsUpdate = true;
      rm.mesh.geometry.computeBoundingSphere();
    }
  }

  // ---- markers

  function arrow(origin: THREE.Vector3, dir: THREE.Vector3, len: number, color: string): THREE.Object3D {
    const a = new THREE.ArrowHelper(dir, origin, len, new THREE.Color(color).getHex(), len * 0.28, len * 0.16);
    return a;
  }

  // ---- particles

  function writeParticles(): void {
    if (!flow?.particles) return;
    const { field, particles: p, lines, scale, rot } = flow;
    const pos = lines.geometry.getAttribute("position") as THREE.BufferAttribute;
    const arr = pos.array as Float32Array;
    const tail = 0.06;
    const v = new THREE.Vector3();
    for (let i = 0; i < p.pu.length; i++) {
      const a = planePoint(field, p.pu[i], p.pv[i]);
      const vel = [0, 0];
      // the tail points back along the local flow (a short streak, no history kept)
      const s = sampleTail(field, p.pu[i], p.pv[i]);
      vel[0] = s[0]; vel[1] = s[1];
      const b = planePoint(field, p.pu[i] - vel[0] * scale * tail, p.pv[i] - vel[1] * scale * tail);
      for (const [k, q] of [[0, a], [1, b]] as const) {
        v.set(q[0], q[1], q[2]);
        if (rot) v.applyMatrix3(rot);
        arr[6 * i + 3 * k] = v.x; arr[6 * i + 3 * k + 1] = v.y; arr[6 * i + 3 * k + 2] = v.z;
      }
    }
    pos.needsUpdate = true;
  }

  function sampleTail(f: FlowField, u: number, w: number): [number, number] {
    const nu = f.us.length; const nv = f.vs.length;
    const i = Math.max(0, Math.min(nu - 1, Math.round(((u - f.us[0]) / (f.us[nu - 1] - f.us[0])) * (nu - 1))));
    const j = Math.max(0, Math.min(nv - 1, Math.round(((w - f.vs[0]) / (f.vs[nv - 1] - f.vs[0])) * (nv - 1))));
    const a = f.vu[i * nv + j]; const b = f.vv[i * nv + j];
    return Number.isFinite(a) && Number.isFinite(b) ? [a, b] : [0, 0];
  }

  function clearGroup(g: THREE.Group): void { for (const c of [...g.children]) disposeTree(c); }

  function bounds(): Bounds | null {
    const b = result ? new THREE.Box3().setFromObject(result.root) : part ? part.bbox : null;
    if (!b || b.isEmpty()) return null;
    return { min: [b.min.x, b.min.y, b.min.z], max: [b.max.x, b.max.y, b.max.z] };
  }

  function fit(): void {
    const b = result ? new THREE.Box3().setFromObject(result.root) : part?.bbox ?? null;
    if (b && !b.isEmpty()) {
      if (flow) b.expandByObject(flowGroup);
      fitOrtho(camera, b, width / Math.max(height, 1));
      controls.target.copy(b.getCenter(new THREE.Vector3()));
      controls.update();
    }
    requestRender();
  }

  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();

  return {
    setPart(p) {
      if (part && part !== p) part.dispose();
      part = p;
      if (p) { scene.add(p.object); if (!result) fit(); }
      requestRender();
    },

    async loadResult(bytes) {
      const { scene: src, extras } = await parseGlb(bytes);
      if (disposed) { disposeTree(src); throw new Error("disposed"); }
      const root = new THREE.Group();
      root.add(src);
      root.scale.setScalar(MM_PER_GLTF_UNIT);
      root.rotation.x = Math.PI / 2;
      const meshes: ResultMesh[] = [];
      const edge = colors().edge;
      src.traverse((o) => {
        if (!(o instanceof THREE.Mesh)) return;
        const g = o.geometry as THREE.BufferGeometry;
        for (const m of Array.isArray(o.material) ? o.material : [o.material]) m.dispose();
        o.material = new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
        let colorAttr = g.getAttribute("color") as THREE.BufferAttribute | undefined;
        const n = g.getAttribute("position").count;
        if (!colorAttr || colorAttr.itemSize !== 3) { colorAttr = new THREE.BufferAttribute(new Float32Array(n * 3).fill(0.3), 3); g.setAttribute("color", colorAttr); }
        const attrs = new Map<string, THREE.BufferAttribute>();
        for (const [name, a] of Object.entries(g.attributes)) if (name.startsWith("_")) { (a as THREE.BufferAttribute).name = name; attrs.set(name, a as THREE.BufferAttribute); }
        const edges = new THREE.LineSegments(new THREE.EdgesGeometry(g, EDGE_THRESHOLD_DEG), new THREE.LineBasicMaterial({ color: edge, transparent: true, opacity: 0.35 }));
        edges.userData.edges = true;
        o.add(edges);
        meshes.push({ mesh: o, base: Float32Array.from(g.getAttribute("position").array as ArrayLike<number>), attrs, colorBuf: colorAttr.array as Float32Array, values: null, range: [0, 1] });
      });
      if (meshes.length === 0) { disposeTree(root); throw new Error("result model has no geometry"); }
      root.updateMatrixWorld(true);
      if (result) disposeTree(result.root);
      scene.add(root);
      const box = new THREE.Box3().setFromObject(root);
      const ex = (extras as { mises_sim?: Record<string, unknown> } | undefined)?.mises_sim ?? {};
      const channels = readChannels(extras);
      const info: ResultInfo = {
        channels, field: typeof ex.field === "string" ? ex.field : channels[0]?.name ?? "",
        bounds: { min: [box.min.x, box.min.y, box.min.z], max: [box.max.x, box.max.y, box.max.z] },
        modes: channels.filter((c) => /^mode_/.test(c.name)).map((c) => ({ name: c.name, ...(c.freq_hz !== undefined ? { freq_hz: c.freq_hz } : {}), ...(c.factor !== undefined ? { factor: c.factor } : {}) })),
      };
      result = { root, meshes, info };
      if (part) part.object.visible = false;
      clearGroup(markers);
      fit();
      if (!reduced()) revealUntil = now() + 700;
      kick();
      return info;
    },

    clearResult() {
      if (result) disposeTree(result.root);
      result = null;
      channel = null;
      if (part) part.object.visible = true;
      requestRender();
    },

    setChannel(name, range = null) {
      if (!result) return null;
      const ch = result.info.channels.find((c) => c.name === name);
      if (!ch) return null;
      channel = name;
      let full: [number, number] = [ch.min, ch.max];
      for (const rm of result.meshes) {
        const a = rm.attrs.get(threeAttribute(ch.attribute));
        rm.values = a ? channelValues(a.array as ArrayLike<number>, ch.kind === "vector" && a.itemSize === 3 ? "vector" : "scalar") : null;
        // the core's range for the channel (the field's, which can exceed the surface vertices'),
        // else what the vertices hold
        if (rm.values && !(Number.isFinite(ch.min) && Number.isFinite(ch.max) && ch.max > ch.min)) full = finiteRange(rm.values);
      }
      // a normalized mode shape colors by its own magnitude 0..1
      const r: [number, number] = range ?? full;
      for (const rm of result.meshes) rm.range = r;
      recolor(now() < revealUntil ? 0 : 1);
      applyDeform();
      kick();
      return full;
    },

    setDeform(scale) {
      deform = Math.max(0, Number.isFinite(scale) ? scale : 0);
      applyDeform();
      requestRender();
    },

    setAnimate(on) {
      animate = on;
      if (!on || reduced()) applyDeform(1);
      kick();
    },

    setMarkers(list, faces, selectedCase) {
      clearGroup(markers);
      const b = bounds();
      if (!b) { requestRender(); return; }
      const size = Math.max(...b.max.map((x, i) => x - b.min[i]), 1);
      const c = colors();
      const mags = list.filter((m) => m.role === "load" && typeof m.magnitude === "number").map((m) => Math.abs(m.magnitude as number));
      const maxMag = Math.max(1e-9, ...mags);
      for (const m of list) {
        const at = m.face === "body"
          ? { center: b.min.map((x, i) => (x + b.max[i]) / 2) as V3, normal: [0, 0, 1] as V3 }
          : faceAnchor(m.face, b, faces);
        if (!at) continue;
        const sel = selectedCase !== null && m.caseIndex === selectedCase;
        const color = sel ? c.selected : m.role === "load" ? c.ink : c.dim;
        const center = new THREE.Vector3(...at.center);
        const normal = new THREE.Vector3(...at.normal).normalize();
        if (m.role === "load") {
          const d = dirVec(m.direction) ?? (m.kind === "pressure" ? (at.normal.map((x) => -x) as V3) : null);
          if (!d) continue;
          // length grows with the magnitude (log), between 18% and 45% of the part
          const rel = typeof m.magnitude === "number" ? Math.log1p(Math.abs(m.magnitude)) / Math.log1p(maxMag) : 1;
          const len = size * (0.18 + 0.27 * Math.max(0, Math.min(1, rel)));
          const dir = new THREE.Vector3(...d);
          const obj = m.kind === "moment" ? momentGlyph(center, dir, size * 0.14, color) : arrow(center.clone().addScaledVector(dir, -len), dir, len, color);
          obj.userData.marker = m.role;
          markers.add(obj);
        } else {
          const glyph = supportGlyph(m.role, center, normal, size * 0.06, color);
          glyph.userData.marker = m.role;
          markers.add(glyph);
        }
      }
      requestRender();
    },

    setFlow(section, frame = null) {
      clearGroup(flowGroup);
      flow = null;
      const f = section ? flowField(section) : null;
      if (f) {
        const span = Math.max(f.us[f.us.length - 1] - f.us[0], 1);
        // a particle crosses the tunnel in ~4 s at the fastest speed
        const scale = span / (4 * f.maxSpeed);
        const rot = frame && frame.length === 3 ? new THREE.Matrix3().set(
          frame[0][0], frame[1][0], frame[2][0], frame[0][1], frame[1][1], frame[2][1], frame[0][2], frame[1][2], frame[2][2]) : null;
        const c = colors();
        if (reduced()) {
          const ls = streamlines(f, 28, 160, scale);
          const pts: number[] = [];
          const v = new THREE.Vector3();
          for (const line of ls) for (let k = 1; k < line.length; k++) for (const q of [line[k - 1], line[k]]) {
            const p = planePoint(f, q[0], q[1]); v.set(p[0], p[1], p[2]); if (rot) v.applyMatrix3(rot); pts.push(v.x, v.y, v.z);
          }
          const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
          const lines = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: c.ink, transparent: true, opacity: 0.55 }));
          flowGroup.add(lines);
          flow = { field: f, particles: null, lines, scale, rot, staticLines: ls.length };
        } else {
          const n = 900;
          const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 6), 3));
          const lines = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: c.ink, transparent: true, opacity: 0.75 }));
          lines.frustumCulled = false;
          flowGroup.add(lines);
          flow = { field: f, particles: seedParticles(f, n), lines, scale, rot, staticLines: 0 };
          writeParticles();
        }
      }
      kick();
    },

    setSection(s) {
      clearGroup(sectionGroup);
      clip = null;
      if (result) for (const rm of result.meshes) (rm.mesh.material as THREE.Material).clippingPlanes = [];
      if (s && result) {
        const axis = "xyz".indexOf(s.axis);
        const n = new THREE.Vector3(); n.setComponent(axis, -1);
        clip = new THREE.Plane(n, s.offset);
        for (const rm of result.meshes) (rm.mesh.material as THREE.Material).clippingPlanes = [clip];
        const b = result.info.bounds;
        const sec = s.values;
        const us = sec ? sec.u_values.filter((x): x is number => typeof x === "number") : [];
        const vs = sec ? sec.v_values.filter((x): x is number => typeof x === "number") : [];
        const [u, v] = [0, 1, 2].filter((a) => a !== axis);
        const u0 = us[0] ?? b.min[u]; const u1 = us[us.length - 1] ?? b.max[u];
        const v0 = vs[0] ?? b.min[v]; const v1 = vs[vs.length - 1] ?? b.max[v];
        let tex: THREE.DataTexture | null = null;
        if (sec && us.length > 1 && vs.length > 1 && Array.isArray(sec.values) && sec.values.length === us.length && !Array.isArray((sec.values[0] as unknown[])?.[0])) {
          const data = new Uint8Array(us.length * vs.length * 4);
          const [lo, hi] = s.range;
          for (let i = 0; i < us.length; i++) for (let j = 0; j < vs.length; j++) {
            const val = (sec.values[i] as (number | null)[])[j];
            const k = 4 * (j * us.length + i);
            if (typeof val !== "number") { data[k + 3] = 0; continue; }
            const [r, g, bb] = colormap(hi > lo ? (val - lo) / (hi - lo) : 0);
            data[k] = r * 255; data[k + 1] = g * 255; data[k + 2] = bb * 255; data[k + 3] = 235;
          }
          tex = new THREE.DataTexture(data, us.length, vs.length, THREE.RGBAFormat);
          tex.colorSpace = THREE.SRGBColorSpace;
          tex.magFilter = THREE.LinearFilter;
          tex.needsUpdate = true;
        }
        const geo = new THREE.PlaneGeometry(1, 1);
        const mat = new THREE.MeshBasicMaterial({ map: tex, color: tex ? 0xffffff : colors().dim, transparent: true, opacity: tex ? 1 : 0.18, side: THREE.DoubleSide, depthWrite: false });
        const quad = new THREE.Mesh(geo, mat);
        // PlaneGeometry spans x/y: scale to (u, v) and rotate so its normal is the section axis
        const basis = new THREE.Matrix4();
        const eu = new THREE.Vector3(); eu.setComponent(u, 1);
        const ev = new THREE.Vector3(); ev.setComponent(v, 1);
        const ea = new THREE.Vector3(); ea.setComponent(axis, 1);
        basis.makeBasis(eu, ev, ea);
        quad.applyMatrix4(new THREE.Matrix4().makeScale(u1 - u0, v1 - v0, 1));
        quad.applyMatrix4(basis);
        const center = new THREE.Vector3(); center.setComponent(u, (u0 + u1) / 2); center.setComponent(v, (v0 + v1) / 2); center.setComponent(axis, s.offset);
        quad.position.copy(center);
        quad.userData.section = true;
        sectionGroup.add(quad);
      }
      requestRender();
    },

    setProbe(point) {
      clearGroup(probeGroup);
      if (point) {
        const b = bounds();
        const r = b ? Math.max(...b.max.map((x, i) => x - b.min[i])) * 0.012 : 0.5;
        const ring = new THREE.Mesh(new THREE.SphereGeometry(Math.max(r, 0.2), 16, 12), new THREE.MeshBasicMaterial({ color: colors().selected, depthTest: false }));
        ring.renderOrder = 10;
        ring.position.set(...point);
        probeGroup.add(ring);
      }
      requestRender();
    },

    pick(clientX, clientY, faces = []) {
      const rect = canvas.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return null;
      ndc.set(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
      raycaster.setFromCamera(ndc, camera);
      const target = result?.root ?? part?.object;
      if (!target) return null;
      const hits = raycaster.intersectObject(target, true).filter((h) => h.object instanceof THREE.Mesh && h.face
        && (!clip || clip.distanceToPoint(h.point) >= -1e-6));
      const h = hits[0];
      if (!h || !h.face) return null;
      const nrm = h.face.normal.clone().transformDirection(h.object.matrixWorld).normalize();
      if (nrm.dot(raycaster.ray.direction) > 0) nrm.negate();
      const point: V3 = [h.point.x, h.point.y, h.point.z];
      const normal: V3 = [nrm.x, nrm.y, nrm.z];
      return { point, normal, face: faces.length ? matchFace(point, normal, faces) : null };
    },

    bounds,
    fit,

    resize(w, h) {
      width = Math.max(1, w); height = Math.max(1, h);
      renderer.setSize(width, height, false);
      fit();
    },

    project(p) {
      const v = new THREE.Vector3(...p).project(camera);
      if (v.z < -1 || v.z > 1) return null;
      return [((v.x + 1) / 2) * width, ((1 - v.y) / 2) * height];
    },

    state() {
      return {
        channel, animating: moving() && (animate || flow?.particles != null), particles: flow?.particles?.pu.length ?? 0,
        streamlines: flow?.staticLines ?? 0, markers: markers.children.length, section: sectionGroup.children.length > 0, frames,
      };
    },

    dispose() {
      if (disposed) return;
      disposed = true;
      if (frame) cancelAnimationFrame(frame);
      if (loop) cancelAnimationFrame(loop);
      controls.dispose();
      for (const g of [markers, flowGroup, sectionGroup, probeGroup]) clearGroup(g);
      if (result) disposeTree(result.root);
      result = null;
      part?.dispose();
      part = null;
      canvas.removeEventListener("webglcontextlost", onLost);
      canvas.removeEventListener("webglcontextrestored", onRestored);
      renderer.dispose();
      // release the GL context now, not at GC: browsers cap live contexts (~16 in Chrome) and drop
      // the oldest (the thumbnail renderer, the page's own view) when a new one is made
      renderer.forceContextLoss?.();
    },
  };
}

/** A support / thermal face marker: a small flat square on the face with a stub along the normal. */
function supportGlyph(role: Marker["role"], center: THREE.Vector3, normal: THREE.Vector3, s: number, color: string): THREE.Object3D {
  const g = new THREE.Group();
  const mat = new THREE.LineBasicMaterial({ color });
  const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), normal);
  const pts: number[] = [];
  const sq = role === "support" ? [[-1, -1], [1, -1], [1, 1], [-1, 1]] : [[0, -1.2], [1.2, 0], [0, 1.2], [-1.2, 0]];
  for (let i = 0; i < sq.length; i++) { const a = sq[i]; const b = sq[(i + 1) % sq.length]; pts.push(a[0] * s, a[1] * s, 0, b[0] * s, b[1] * s, 0); }
  if (role === "support") for (const x of [-1, 0, 1]) pts.push(x * s, -s, 0, x * s - s * 0.6, -s, -s * 0.6); // hatch: held
  pts.push(0, 0, 0, 0, 0, s * (role === "impact" ? 3 : 1.6));
  const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  const lines = new THREE.LineSegments(geo, mat);
  lines.quaternion.copy(q);
  g.add(lines);
  g.position.copy(center).addScaledVector(normal, s * 0.15);
  return g;
}

/** A moment: a circular arc around the axis with an arrow head. */
function momentGlyph(center: THREE.Vector3, axis: THREE.Vector3, r: number, color: string): THREE.Object3D {
  const g = new THREE.Group();
  const curve = new THREE.EllipseCurve(0, 0, r, r, 0, Math.PI * 1.6);
  const pts = curve.getPoints(32).map((p) => new THREE.Vector3(p.x, p.y, 0));
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color }));
  const end = pts[pts.length - 1]; const prev = pts[pts.length - 2];
  const head = new THREE.ArrowHelper(end.clone().sub(prev).normalize(), prev, r * 0.5, new THREE.Color(color).getHex(), r * 0.4, r * 0.25);
  g.add(line, head);
  g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), axis.clone().normalize());
  g.position.copy(center);
  return g;
}
