// "Extrude N mm" preview: the closed profiles as a solid, in a small iso view you can orbit.
// Built from the same profiles the canvas shades; rendered on demand. Where the webview has no
// WebGL (tests), it says so instead.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { meshColors } from "../../viewer/loadPart";
import { fitOrtho, ISO_AZIMUTH_DEG, ISO_ELEVATION_DEG } from "../../viewer/scene";
import type { Profile } from "./profiles";

/** THREE shapes (outer + holes) from the profiles. */
export function profileShapes(profiles: readonly Profile[]): THREE.Shape[] {
  return profiles.map((p) => {
    const shape = new THREE.Shape(p.outer.poly.map(([x, y]) => new THREE.Vector2(x, y)));
    for (const h of p.holes) shape.holes.push(new THREE.Path(h.poly.map(([x, y]) => new THREE.Vector2(x, y))));
    return shape;
  });
}

export function ExtrudePreview({ profiles, depth }: { profiles: readonly Profile[]; depth: number }): JSX.Element {
  const canvas = useRef<HTMLCanvasElement>(null);
  const api = useRef<{ set(p: readonly Profile[], d: number): void; dispose(): void } | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas: el, antialias: true, alpha: true });
    } catch {
      setFailed(true);
      return;
    }
    renderer.setClearColor(0x000000, 0);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    const scene = new THREE.Scene();
    scene.add(new THREE.AmbientLight(0xffffff, 1.1));
    const key = new THREE.DirectionalLight(0xffffff, 0.9);
    key.position.set(-1, -2, 3);
    scene.add(key);
    const camera = new THREE.OrthographicCamera(-50, 50, 50, -50, 0.1, 2000);
    camera.up.set(0, 0, 1);
    const az = THREE.MathUtils.degToRad(ISO_AZIMUTH_DEG);
    const elv = THREE.MathUtils.degToRad(ISO_ELEVATION_DEG);
    camera.position.set(Math.cos(elv) * Math.sin(az) * 200, -Math.cos(elv) * Math.cos(az) * 200, Math.sin(elv) * 200);
    const controls = new OrbitControls(camera, el);
    let frame = 0;
    const draw = () => { frame = 0; renderer.render(scene, camera); };
    const request = () => { if (!frame) frame = requestAnimationFrame(draw); };
    controls.addEventListener("change", request);
    let group: THREE.Group | null = null;
    const size = () => {
      const w = el.clientWidth || 240;
      const h = el.clientHeight || 160;
      renderer.setSize(w, h, false);
      return w / h;
    };
    api.current = {
      set(p, d) {
        if (group) {
          group.traverse((o) => { if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) { o.geometry.dispose(); (o.material as THREE.Material).dispose(); } });
          scene.remove(group);
        }
        group = new THREE.Group();
        const colors = meshColors();
        for (const shape of profileShapes(p)) {
          const geo = new THREE.ExtrudeGeometry(shape, { depth: d, bevelEnabled: false, curveSegments: 24 });
          const mesh = new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ color: colors.face, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
          mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo, 20), new THREE.LineBasicMaterial({ color: colors.edge })));
          group.add(mesh);
        }
        scene.add(group);
        const bbox = new THREE.Box3().setFromObject(group);
        if (!bbox.isEmpty()) {
          fitOrtho(camera, bbox, size());
          controls.target.copy(bbox.getCenter(new THREE.Vector3()));
          controls.update();
        }
        request();
      },
      dispose() {
        cancelAnimationFrame(frame);
        controls.dispose();
        renderer.dispose();
      },
    };
    return () => { api.current?.dispose(); api.current = null; };
  }, []);

  useEffect(() => { api.current?.set(profiles, depth); }, [profiles, depth]);

  if (failed) return <div class="sk-preview-none faint">3D preview needs WebGL.</div>;
  return <canvas ref={canvas} class="sk-preview" aria-label={`Extrude preview, ${depth} mm`} data-extrude-preview />;
}
