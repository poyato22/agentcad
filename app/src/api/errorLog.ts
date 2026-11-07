// The error log (core server/logs_api.py): GET /logs/errors lists it, POST reports an error the
// app rendered, POST /logs/errors/reveal shows the file in Finder.
import { z } from "zod";

export const errorSources = ["app", "core", "agent", "tool", "study"] as const;
export type ErrorSource = (typeof errorSources)[number];

export const errorEntrySchema = z.object({
  ts: z.number(), first_ts: z.number().nullish(), count: z.number(),
  source: z.string(), code: z.string().nullish(), message: z.string(), hint: z.string().nullish(),
  stack: z.string().nullish(), build_id: z.string().nullish(), run_id: z.string().nullish(),
  tool: z.string().nullish(), version: z.string().nullish(), fp: z.string().nullish(),
  extra: z.record(z.string(), z.string().nullable()).nullish(),
}).passthrough();
export type ErrorEntry = z.infer<typeof errorEntrySchema>;

export const errorLogSchema = z.object({ path: z.string(), entries: z.array(errorEntrySchema) });
export type ErrorLogList = z.infer<typeof errorLogSchema>;

/** One error the app shows, as reported to the core (which scrubs and dedupes it). */
export interface ErrorReport {
  source: ErrorSource; message: string; code?: string | null; hint?: string | null;
  stack?: string | null; build_id?: string | null; run_id?: string | null; tool?: string | null;
  where?: string | null;
}
