// "Build part": store the sketch in the core (POST /sketches), then start a run with the profile's
// default agent and model through the same POST /runs the console's new-run dialog uses, with the
// sketch attached by id. The core puts the sketch in the run's context block as data and adds the
// photo/render as images for vision-capable models.
import { CoreError, type CoreClient } from "../../../api/client";
import type { AgentInfo, ModelsInfo, Profile, SketchSummary } from "../../../api/types";
import { defaultModelRef, splitModelRef } from "../../dialogs/modelRef";
import { toUpload, type SketchDoc } from "./model";
import type { PickedPhoto } from "./photo";

/** Same set as core sketch.VISION_PROVIDERS. */
export const VISION_VENDORS: ReadonlySet<string> = new Set(["anthropic", "claude-code", "openai", "gemini"]);

/** The user's request. The staged procedure (strokes → dimensions → constraints → ≤ 3 questions)
 *  is added by the core, next to the sketch data. */
export const BUILD_PROMPT = "Build the part shown in my sketch.";

export interface BuildTarget { agent: string; modelRef: string; vendor: string; vision: boolean; keyMissing: boolean }

/** What a sketch run will use: the profile's default agent, and the model the core will pick for it. */
export function buildTarget(profile: Profile, agents: readonly AgentInfo[], models: ModelsInfo): BuildTarget {
  const agent = profile.default_agent;
  const info = agents.find((a) => a.name === agent) ?? { name: agent, description: "", model: "inherit" };
  const modelRef = defaultModelRef(info, models, profile.default_model);
  const { vendor } = splitModelRef(modelRef);
  const v = models.vendors.find((x) => x.vendor === vendor);
  const keyMissing = !!v && (v.key === "none" || v.key === "signed_out" || v.key === "not_installed");
  return { agent, modelRef, vendor, vision: VISION_VENDORS.has(vendor), keyMissing };
}

export interface BuildInput { doc: SketchDoc; notes: string; photo: PickedPhoto | null; render: string | null; agent: string; modelRef: string }

export async function buildPart(client: CoreClient, input: BuildInput, onStep?: (step: "saving" | "starting") => void): Promise<{ run_id: string; sketch: SketchSummary }> {
  onStep?.("saving");
  const sketch = await client.postSketch({
    sketch: toUpload({ ...input.doc, notes: input.notes }),
    photo: input.photo ? { data_b64: input.photo.data_b64, name: input.photo.name } : null,
    render_png_b64: input.doc.strokes.length ? input.render : null,
  });
  onStep?.("starting");
  // pass the model shown in the build target so the run uses exactly it, not a re-resolved default
  const { run_id } = await client.createRun({ agent: input.agent, prompt: BUILD_PROMPT, sketch_id: sketch.sketch_id, model_ref: input.modelRef });
  return { run_id, sketch };
}

export function describeError(e: unknown): string {
  if (e instanceof CoreError) return e.hint ? `${e.message} — ${e.hint}` : e.message;
  return e instanceof Error ? e.message : String(e);
}
