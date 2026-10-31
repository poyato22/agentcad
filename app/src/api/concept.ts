// Plan 9 G-A: the concept stage of a build (core concept/: api.py, tools.py). A design-heavy
// build's lead pitches 2–3 directions, sketches the chosen one (versions v1, v2, …), the user
// circles / lassos / pins areas with comments, and approves (or skips) the sketch, which opens
// the concept gate for parts. Images are render ids (GET /renders/{id}.png).
// Every string (notes, callouts, option names, comments) is untrusted text: rendered as text.
import { z } from "zod";

const num = z.number();
const vec3 = z.array(num).length(3);

/** The camera of one sketch image: maps a normalised image point back to 3D. */
export const sketchCameraSchema = z.object({
  view: z.string(),
  forward: vec3, up: vec3,
  projection: z.enum(["perspective", "orthographic"]).catch("orthographic"),
  framing: z.object({ min: vec3, max: vec3 }),
  size: z.array(num).length(2),
  style: z.string(),
});

export const sketchImageSchema = z.object({
  view: z.string(), label: z.string(), render_id: z.string(),
  width: num, height: num, camera: sketchCameraSchema,
});

export const calloutSchema = z.object({
  view: z.string(), x: num, y: num, text: z.string(), to: z.array(num).length(2).nullable().optional(),
});

const markBase = { image: z.number().int().nonnegative(), comment: z.string() };
export const circleMarkSchema = z.object({ kind: z.literal("circle"), cx: num, cy: num, r: num, ...markBase });
export const lassoMarkSchema = z.object({ kind: z.literal("lasso"), points: z.array(z.array(num).length(2)), ...markBase });
export const pinMarkSchema = z.object({ kind: z.literal("pin"), x: num, y: num, ...markBase });
export const markSchema = z.discriminatedUnion("kind", [circleMarkSchema, lassoMarkSchema, pinMarkSchema]);
/** A stored mark: numbered, with its image's view and camera. */
export const storedMarkSchema = z.intersection(markSchema, z.object({ n: z.number().int(), view: z.string(), camera: sketchCameraSchema }));

export const sketchFeedbackSchema = z.object({
  feedback_id: z.string(), sketch_id: z.string(), version: z.number().int().nullable().optional(),
  marks: z.array(storedMarkSchema), comment: z.string(),
  annotated: z.array(z.object({ view: z.string(), image: z.number().int(), render_id: z.string() })),
  created_at: num,
});

export const conceptSketchSchema = z.object({
  sketch_id: z.string(), build_id: z.string(), project_id: z.string(), version: z.number().int(),
  parent_sketch_id: z.string().nullable(), run_id: z.string().nullable(),
  images: z.array(sketchImageSchema), notes: z.string(),
  callouts: z.array(calloutSchema),
  option: z.string().nullable().optional(),
  scale: z.object({ figure: z.boolean(), massing_size_mm: z.array(num) }).partial().nullable().optional(),
  created_at: num,
  feedback: z.array(sketchFeedbackSchema).optional(),
});

export const pitchOptionSchema = z.object({
  name: z.string(), mood: z.array(z.string()), proportion_note: z.string(),
  cmf: z.array(z.object({ name: z.string(), color: z.string(), finish: z.string() })),
  tradeoffs: z.object({
    mass_delta: z.string(), part_count: z.union([z.number(), z.string()]), molding: z.string(),
    cost_tier: z.string(), risk: z.string(), gives_up: z.string().optional().default(""),
  }),
  recommended: z.boolean(), reason: z.string(), baseline: z.boolean(),
  callouts: z.array(calloutSchema).optional().default([]),
  sketch: sketchImageSchema.nullable(),
  silhouettes: z.record(z.string(), z.string()),
  massing_size_mm: z.array(num).optional(),
});

export const pitchChoiceSchema = z.object({
  option: z.string().nullable(),
  mix: z.array(z.object({ option: z.string(), take: z.string(), comment: z.string() })),
  comment: z.string(), label: z.string(),
});

export const conceptPitchSchema = z.object({
  pitch_id: z.string(), project_id: z.string(), run_id: z.string().nullable(), question: z.string(),
  options: z.array(pitchOptionSchema), chosen: pitchChoiceSchema.nullable(), created_at: num,
});

export const CONCEPT_STATUSES = ["none", "pitching", "sketching", "approved", "skipped"] as const;
export const conceptRecordSchema = z.object({
  project_id: z.string(),
  status: z.enum(CONCEPT_STATUSES),
  sketch_ids: z.array(z.string()),
  chosen_option: z.string().nullable(),
  pitch_ids: z.array(z.string()).optional().default([]),
  approved_sketch_id: z.string().nullable().optional().default(null),
  updated_at: num.nullable(),
});

export const designClassSchema = z.object({
  cls: z.enum(["engineering", "mixed", "design"]), score: num.optional(), source: z.string().optional(),
}).nullable();

/** GET /projects/{id}/concept */
export const conceptViewSchema = z.object({
  concept: conceptRecordSchema,
  design_class: designClassSchema,
  sketches: z.array(conceptSketchSchema),
  pitches: z.array(conceptPitchSchema),
});

const sentSchema = z.object({ run_id: z.string(), queued: z.boolean(), position: z.number().nullable().optional(), message_id: z.string().nullable().optional() });

/** POST /concept/sketches/{id}/feedback */
export const feedbackSentSchema = sentSchema.extend({
  feedback_id: z.string(), annotated_ids: z.array(z.string()), text: z.string(), images: z.number().int(),
});

/** POST approve / skip */
export const conceptDecisionSchema = z.object({ concept: conceptRecordSchema, sent: sentSchema.nullable() });

/** POST /concept/pitches/{id}/choose */
export const pitchChosenSchema = z.object({ concept: conceptRecordSchema, chosen: pitchChoiceSchema, sent: sentSchema.nullable() });

export type SketchCamera = z.infer<typeof sketchCameraSchema>;
export type SketchImage = z.infer<typeof sketchImageSchema>;
export type Callout = z.infer<typeof calloutSchema>;
export type Mark = z.infer<typeof markSchema>;
export type StoredMark = z.infer<typeof storedMarkSchema>;
export type SketchFeedback = z.infer<typeof sketchFeedbackSchema>;
export type ConceptSketch = z.infer<typeof conceptSketchSchema>;
export type PitchOption = z.infer<typeof pitchOptionSchema>;
export type ConceptPitch = z.infer<typeof conceptPitchSchema>;
export type ConceptRecord = z.infer<typeof conceptRecordSchema>;
export type ConceptStatus = ConceptRecord["status"];
export type ConceptView = z.infer<typeof conceptViewSchema>;
export type FeedbackSent = z.infer<typeof feedbackSentSchema>;
export type ConceptDecision = z.infer<typeof conceptDecisionSchema>;
export type PitchChosen = z.infer<typeof pitchChosenSchema>;

export interface SketchFeedbackBody { marks: Mark[]; comment: string }
export interface ChooseBody { option?: string; mix?: { option: string; take: string; comment?: string }[]; comment?: string }
