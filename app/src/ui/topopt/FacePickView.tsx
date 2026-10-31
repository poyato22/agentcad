// The part in the Generative design setup: hover highlights the face under the cursor (its id
// and size in a caption), a click while a picker is armed assigns that face, and the faces the
// setup already uses stay tinted. Faces come from the part's metrics face_list (viewer/pick.ts
// matchFace); the list selects stay as the fallback (and without WebGL).
import * as THREE from "three";
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { PartFace, Vec3 } from "../../api/types";
import { loadPart, type PartScene } from "../viewer/loadPart";
import { describeFace, matchFace } from "../viewer/pick";
import { fitOrtho, ISO_AZIMUTH_DEG, ISO_ELEVATION_DEG } from "../viewer/scene";

export type PartGlbLoader = () => Promise<{ bytes: ArrayBuffer; etag: string } | "not-modified">;

const HOVER = 0x4b93ff;
const USED = 0x8dbaff;

/** The mesh triangles that belong to a face (planar: on its plane with its normal; curved: near
 *  its centre), as one geometry for the highlight overlay. */
export function faceTriangles(pos: Float32Array, face: PartFace, size: number): Float32Array {
  const out: number[] = [];
  const tol = Math.max(1e-3, size * 0.004);
  const reach = Math.sqrt(face.area) * 0.75 + tol;
  const c = face.center;
  for (let i = 0; i < pos.length; i += 9) {
    const a = [pos[i], pos[i + 1], pos[i + 2]];
    const b = [pos[i + 3], pos[i + 4], pos[i + 5]];
    const d = [pos[i + 6], pos[i + 7], pos[i + 8]];
    const m = [(a[0] + b[0] + d[0]) / 3, (a[1] + b[1] + d[1]) / 3, (a[2] + b[2] + d[2]) / 3];
    if (face.type === "plane" && face.normal) {
      const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
      const v = [d[0] - a[0], d[1] - a[1], d[2] - a[2]];
      const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
      const ln = Math.hypot(n[0], n[1], n[2]) || 1;
      const cos = (n[0] * face.normal[0] + n[1] * face.normal[1] + n[2] * face.normal[2]) / ln;
      const off = (m[0] - c[0]) * face.normal[0] + (m[1] - c[1]) * face.normal[1] + (m[2] - c[2]) * face.normal[2];
      if (cos < 0.97 || Math.abs(off) > tol) continue;
    } else if (Math.hypot(m[0] - c[0], m[1] - c[1], m[2] - c[2]) > reach) continue;
    for (let k = 0; k < 9; k++) out.push(pos[i + k]);
  }
  return new Float32Array(out);
}

interface View {
  renderer: THREE.WebGLRenderer; scene: THREE.Scene; camera: THREE.OrthographicCamera;
  part: PartScene | null; tris: Float32Array; size: number; overlays: THREE.Group; az: number; el: number;
}

function soup(part: PartScene): Float32Array {
  const out: number[] = [];
  part.object.updateMatrixWorld(true);
  part.object.traverse((o) => {
    if (!(o instanceof THREE.Mesh) || o.userData["edges"]) return;
    const g = (o.geometry as THREE.BufferGeometry).toNonIndexed();
    const p = g.getAttribute("position");
    const v = new THREE.Vector3();
    for (let i = 0; i < p.count; i++) { v.fromBufferAttribute(p, i).applyMatrix4(o.matrixWorld); out.push(v.x, v.y, v.z); }
    g.dispose();
  });
  return new Float32Array(out);
}

export function FacePickView({ load, faces, used, armedLabel, onPick }: {
  load: PartGlbLoader; faces: readonly PartFace[]; used: readonly string[];
  /** The picker waiting for a click ("Held face 1"), or null. */
  armedLabel: string | null; onPick: (faceId: string) => void;
}): JSX.Element {
  const canvas = useRef<HTMLCanvasElement>(null);
  const view = useRef<View | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "nogl" | "error">("loading");
  const [hover, setHover] = useState<PartFace | null>(null);

  const draw = (): void => {
    const v = view.current;
    if (!v) return;
    const az = THREE.MathUtils.degToRad(v.az); const el = THREE.MathUtils.degToRad(v.el);
    v.camera.position.set(Math.cos(el) * Math.sin(az), -Math.cos(el) * Math.cos(az), Math.sin(el)).multiplyScalar(100);
    if (v.part) fitOrtho(v.camera, v.part.bbox, (canvas.current?.clientWidth || 1) / (canvas.current?.clientHeight || 1), 1.1);
    v.renderer.render(v.scene, v.camera);
  };

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    try {
      const renderer = new THREE.WebGLRenderer({ canvas: c, antialias: true, alpha: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setSize(c.clientWidth || 320, c.clientHeight || 240, false);
      const scene = new THREE.Scene();
      scene.add(new THREE.AmbientLight(0xffffff, 1.1));
      const key = new THREE.DirectionalLight(0xffffff, 1.4);
      key.position.set(-0.4, -0.6, 1);
      scene.add(key);
      const overlays = new THREE.Group();
      scene.add(overlays);
      const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 1000);
      camera.up.set(0, 0, 1);
      view.current = { renderer, scene, camera, part: null, tris: new Float32Array(), size: 1, overlays, az: ISO_AZIMUTH_DEG, el: ISO_ELEVATION_DEG };
    } catch { setState("nogl"); return; }
    let live = true;
    void load().then(async (r) => {
      if (!live || r === "not-modified") return;
      const part = await loadPart(r.bytes);
      const v = view.current;
      if (!live || !v) { part.dispose(); return; }
      v.part = part;
      v.scene.add(part.object);
      v.tris = soup(part);
      v.size = part.bbox.getSize(new THREE.Vector3()).length();
      draw();
      setState("ok");
    }).catch(() => { if (live) setState("error"); });
    return () => {
      live = false;
      view.current?.part?.dispose();
      view.current?.renderer.dispose();
      view.current = null;
    };
  }, [load]);

  // the overlays: faces the setup uses, and the hovered one
  useEffect(() => {
    const v = view.current;
    if (!v || state !== "ok") return;
    for (const o of [...v.overlays.children]) { (o as THREE.Mesh).geometry.dispose(); ((o as THREE.Mesh).material as THREE.Material).dispose(); v.overlays.remove(o); }
    const add = (f: PartFace, color: number, opacity: number) => {
      const t = faceTriangles(v.tris, f, v.size);
      if (!t.length) return;
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(t, 3));
      const m = new THREE.MeshBasicMaterial({ color, transparent: true, opacity, side: THREE.DoubleSide, depthTest: false });
      const mesh = new THREE.Mesh(g, m);
      mesh.renderOrder = 2;
      v.overlays.add(mesh);
    };
    for (const id of used) { const f = faces.find((x) => x.id === id); if (f) add(f, USED, 0.35); }
    if (hover) add(hover, HOVER, 0.55);
    draw();
  }, [hover, used, faces, state]);

  const hit = (e: PointerEvent): PartFace | null => {
    const v = view.current;
    const c = canvas.current;
    if (!v || !v.part || !c) return null;
    const r = c.getBoundingClientRect();
    const ndc = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(ndc, v.camera);
    const meshes: THREE.Object3D[] = [];
    v.part.object.traverse((o) => { if (o instanceof THREE.Mesh) meshes.push(o); });
    const h = ray.intersectObjects(meshes, false)[0];
    if (!h || !h.face) return null;
    const n = h.face.normal.clone().transformDirection(h.object.matrixWorld);
    return matchFace(faces, [h.point.x, h.point.y, h.point.z] as Vec3, [n.x, n.y, n.z] as Vec3, Math.max(0.05, v.size * 0.01));
  };

  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const onDown = (e: PointerEvent) => { drag.current = { x: e.clientX, y: e.clientY, moved: false }; };
  const onMove = (e: PointerEvent) => {
    const v = view.current;
    if (drag.current && v && (e.buttons & 1)) {
      const dx = e.clientX - drag.current.x; const dy = e.clientY - drag.current.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) drag.current.moved = true;
      v.az -= dx * 0.5; v.el = Math.max(-85, Math.min(85, v.el + dy * 0.5));
      drag.current = { ...drag.current, x: e.clientX, y: e.clientY };
      draw();
      return;
    }
    const f = hit(e);
    if ((f?.id ?? null) !== (hover?.id ?? null)) setHover(f);
  };
  const onUp = (e: PointerEvent) => {
    const wasDrag = drag.current?.moved ?? false;
    drag.current = null;
    if (wasDrag || !armedLabel) return;
    const f = hit(e);
    if (f) onPick(f.id);
  };

  return (
    <div class="gd-preview gd-preview-pick" data-testid="gd-pick-view" data-state={state} data-picking={armedLabel ? "" : undefined}>
      <canvas ref={canvas} role="img" aria-label="The part: hover a face to see it, click to pick it"
        onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerLeave={() => setHover(null)} />
      {state === "loading" && <span class="gd-preview-note faint">Loading the part…</span>}
      {state === "nogl" && <span class="gd-preview-note faint">Picking in 3D needs WebGL: use the lists.</span>}
      {state === "error" && <span class="gd-preview-note faint">The part's model is not available: use the lists.</span>}
      <span class="gd-pick-hint dim" data-testid="gd-pick-caption" style={{ position: "absolute", left: 8, bottom: 6 }}>
        {armedLabel ? `Click a face for ${armedLabel.toLowerCase()} (Esc stops)` : hover ? describeFace(hover) : "Hover a face to see it"}
        {armedLabel && hover ? ` · ${describeFace(hover)}` : ""}
      </span>
    </div>
  );
}
