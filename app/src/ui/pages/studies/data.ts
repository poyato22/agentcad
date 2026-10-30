// Study data shared by the cards in the thread, the Studies page and the immersive view: records,
// chart data and result GLBs, fetched once per study and kept while it is finished (a running or
// just-finished study is read again). The client is resolved per call (it changes on reconnect).
import type { StudyCharts, StudyRecord } from "../../../api/studies";
import { actions } from "../../../state/store";

export interface StudyData {
  record(simId: string): Promise<StudyRecord>;
  charts(simId: string): Promise<StudyCharts>;
  glb(simId: string): Promise<ArrayBuffer | null>;
  /** Forget a study (it changed: finished, cancelled, its part was rebuilt). */
  forget(simId: string): void;
}

const MAX = 64;

function lru<T>(): { get(k: string): T | undefined; set(k: string, v: T): void; del(k: string): void; clear(): void } {
  const m = new Map<string, T>();
  return {
    get: (k) => { const v = m.get(k); if (v !== undefined) { m.delete(k); m.set(k, v); } return v; },
    set: (k, v) => { m.delete(k); m.set(k, v); while (m.size > MAX) m.delete(m.keys().next().value as string); },
    del: (k) => { m.delete(k); },
    clear: () => m.clear(),
  };
}

export function createStudyData(client = () => actions.client()): StudyData & { clear(): void } {
  const recs = lru<Promise<StudyRecord>>();
  const charts = lru<Promise<StudyCharts>>();
  const glbs = lru<Promise<ArrayBuffer | null>>();
  const settled = (st: string | null | undefined) => st === "done" || st === "failed" || st === "cancelled" || st === "interrupted";
  return {
    record(simId) {
      const hit = recs.get(simId);
      if (hit) return hit;
      const p = client().getStudy(simId);
      recs.set(simId, p);
      // only finished studies stay cached; a failed fetch is retried next time
      p.then((r) => { if (!settled(r.status)) recs.del(simId); }, () => recs.del(simId));
      return p;
    },
    charts(simId) {
      const hit = charts.get(simId);
      if (hit) return hit;
      const p = client().studyCharts(simId);
      charts.set(simId, p);
      p.then((c) => { if (!settled(c.status)) charts.del(simId); }, () => charts.del(simId));
      return p;
    },
    glb(simId) {
      const hit = glbs.get(simId);
      if (hit) return hit;
      const p = client().simGlb(simId).then((r) => (r === "not-modified" ? null : r.bytes));
      glbs.set(simId, p);
      p.then((b) => { if (!b) glbs.del(simId); }, () => glbs.del(simId));
      return p;
    },
    forget(simId) { recs.del(simId); charts.del(simId); glbs.del(simId); },
    clear() { recs.clear(); charts.clear(); glbs.clear(); },
  };
}

/** The app's one study data cache. */
export const studyData = createStudyData();
