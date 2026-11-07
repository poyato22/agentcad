import { cleanup, render, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ErrorReport } from "../api/errorLog";
import type { AgentRun } from "../api/types";
import { ErrorCard } from "../ui/console/MessageActions";
import { setErrorSink, watchErrors } from "./errorLog";

describe("error log hook", () => {
  let reports: ErrorReport[];
  let stop: () => void;
  beforeEach(() => {
    reports = [];
    setErrorSink(async (e) => { reports.push(e); });
    stop = watchErrors(document.body);
  });
  afterEach(() => { stop(); cleanup(); setErrorSink(null); });

  it("a rendered error card produces one log entry, once", async () => {
    const run = { id: "run-7" } as AgentRun;
    const item = { code: "tool_failed", message: "the sketch is not closed", hint: "close the loop" };
    const { rerender } = render(<ErrorCard item={item} stopped={false} run={run} />);
    await waitFor(() => expect(reports).toHaveLength(1));
    const [r] = reports;
    expect(r.source).toBe("agent");
    expect(r.message).toContain("the sketch is not closed");
    expect(r.code).toBeTruthy();
    expect(r.hint).toContain("close the loop");
    expect(r.run_id).toBe("run-7");
    rerender(<ErrorCard item={{ ...item }} stopped={false} run={run} />);
    await new Promise((res) => setTimeout(res, 20));
    expect(reports).toHaveLength(1);       // re-rendering the same error does not log it again
  });

  it("red text logs; text inside the error log view and aria-hidden glyphs do not", async () => {
    render(<div>
      <p role="alert">could not save the printer</p>
      <div data-no-error-log><p role="alert">old entry</p></div>
      <span class="cv-fail" aria-hidden="true">✕</span>
    </div>);
    await waitFor(() => expect(reports).toHaveLength(1));
    expect(reports[0]).toMatchObject({ source: "app", message: "could not save the printer" });
  });

  it("reports uncaught exceptions with their stack", async () => {
    const err = new Error("kaboom");
    window.dispatchEvent(new ErrorEvent("error", { error: err, message: "kaboom" }));
    await waitFor(() => expect(reports).toHaveLength(1));
    expect(reports[0]).toMatchObject({ source: "app", code: "uncaught", message: "kaboom" });
    expect(reports[0].stack).toContain("kaboom");
  });

  it("never throws when the core is unreachable", async () => {
    const sink = vi.fn(async () => { throw new Error("offline"); });
    setErrorSink(sink);
    render(<p role="alert">network down</p>);
    await waitFor(() => expect(sink).toHaveBeenCalledTimes(1));
  });
});
