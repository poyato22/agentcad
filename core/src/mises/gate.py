"""The gate: every tool call from any agent passes through this pipeline."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from . import announce, budget
from .approvals import ApprovalService, hash_payload
from .events import Event, EventBus
from .store import Store
from .tools.names import resolve_tool_name, unknown_tool_hint
from .tools.registry import ToolContext, ToolDef, ToolRegistry
from .types import (ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput,
                    canonical_json, sha256_hex)

MAX_OUTPUT_BYTES = 16 * 1024
# Images ride beside the JSON result (ToolOutput.images), so the 16 KB text cap never drops
# them; they have their own caps instead: at most MAX_IMAGES per result and MAX_IMAGE_B64_BYTES
# of base64 in total. Beyond that, images are dropped with a warning (the PNG handles remain).
MAX_IMAGES = 6
MAX_IMAGE_B64_BYTES = 4 * 1024 * 1024
# P11 F9: the same call (tool + arguments) failing this many times is a stuck agent: its error
# then carries a strategy-switch note; edits that undo each other (A, B, A, B) are oscillation
STUCK_REPEATS = 3
STRATEGY_SWITCH = ("Switch strategy: do not repeat this call. Try a different approach or tool, "
                   "simplify the change, or ask the user one specific question.")


def _year_start() -> float:
    return datetime(datetime.now().year, 1, 1).timestamp()


class Gate:
    def __init__(self, registry: ToolRegistry, store: Store, approvals: ApprovalService,
                 events: EventBus, *, user: str = "local", services: dict | None = None):
        self.registry = registry
        self.store = store
        self.approvals = approvals
        self.events = events
        self.user = user
        self.services = services if services is not None else {}
        # P11 F9: per run, failures of each identical call, and the last changing calls
        self._fails: dict[str, dict[str, int]] = {}
        self._edits: dict[str, list[str]] = {}

    async def call(self, run_id: str, tool_name: str, raw_input: dict, *, call_id: str,
                   workdir: Path, agent: bool = False) -> ToolOutput:
        """`agent`: the call came straight from a model (ToolContext.agent_call). A decorated
        or bare form of a real tool's name (`mcp__mises__x`, `Mises.X`) is that tool (P11 F3)."""
        tool_name = resolve_tool_name(tool_name, self._names()) or tool_name
        self.store.append_step(run_id, StepKind.TOOL_CALL,
                               {"call_id": call_id, "name": tool_name, "input": raw_input})
        self.events.publish(Event(type="tool_call", run_id=run_id,
                                  data={"call_id": call_id, "name": tool_name, "input": raw_input}))
        try:
            out = await self._run(run_id, tool_name, raw_input, call_id, Path(workdir), agent)
        except MisesError as e:
            e = self._stuck(run_id, tool_name, raw_input, self._redact_error(e, run_id))
            self._error(run_id, tool_name, call_id, e)
            raise e from None
        except asyncio.CancelledError:
            # every tool_call Step gets an outcome Step, also when the run is cancelled
            self._error(run_id, tool_name, call_id,
                        MisesError(ErrorCode.CANCELLED, "call cancelled before it finished"))
            raise
        except Exception as e:
            self._error(run_id, tool_name, call_id, self._redact_error(
                MisesError(ErrorCode.TOOL_FAILED, f"{type(e).__name__}: {e}"), run_id))
            raise
        return self._progress(run_id, tool_name, raw_input, out)

    def _key(self, name: str, raw_input: dict) -> str:
        return sha256_hex(canonical_json([name, raw_input]))

    def _stuck(self, run_id: str, name: str, raw_input: dict, e: MisesError) -> MisesError:
        """P11 F9: the same call failing STUCK_REPEATS times: its error carries the last
        errors and a strategy-switch note (and a step says so). Never ends the turn."""
        if e.code == ErrorCode.CANCELLED:
            return e
        fails = self._fails.setdefault(run_id, {})
        k = self._key(name, raw_input)
        fails[k] = n = fails.get(k, 0) + 1
        if n < STUCK_REPEATS:
            return e
        note = (f"This exact {name} call has now failed {n} times (last: {e.message}). "
                + STRATEGY_SWITCH)
        if n == STUCK_REPEATS:
            self.store.append_step(run_id, StepKind.DECISION, {"stuck": {
                "kind": "repeated_failure", "tool": name, "times": n, "error": e.message,
                "note": note}})
        e.hint = f"{e.hint}. {note}" if e.hint else note    # keeps its type and extras
        return e

    def _progress(self, run_id: str, name: str, raw_input: dict, out: ToolOutput) -> ToolOutput:
        """P11 F9: a call that worked clears its failures; changing calls that undo each other
        (the same two edits alternating: A, B, A, B) get an oscillation warning."""
        k = self._key(name, raw_input)
        self._fails.get(run_id, {}).pop(k, None)
        t = self.registry._tools.get(name)
        if t is None or t.safety == SafetyClass.READ or not out.ok:
            return out
        edits = self._edits.setdefault(run_id, [])
        edits.append(k)
        del edits[:-4]
        if len(edits) == 4 and edits[0] == edits[2] and edits[1] == edits[3] \
                and edits[0] != edits[1]:
            note = ("These edits are going back and forth (the same two changes alternating). "
                    + STRATEGY_SWITCH)
            self.store.append_step(run_id, StepKind.DECISION, {"stuck": {
                "kind": "oscillation", "tool": name, "note": note}})
            edits.clear()
            return out.model_copy(update={"warnings": [*out.warnings, note]})
        return out

    def _names(self) -> list[str]:
        return [t.name for t in self.registry.list()]

    def _error(self, run_id: str, name: str, call_id: str, e: MisesError) -> None:
        self.store.append_step(run_id, StepKind.ERROR, {"call_id": call_id, "name": name,
                                                        **e.to_dict()})
        self.events.publish(Event(type="tool_result", run_id=run_id,
                                  data={"call_id": call_id, "name": name, "ok": False,
                                        **e.to_dict()}))

    async def _run(self, run_id: str, name: str, raw_input: dict, call_id: str,
                   workdir: Path, agent: bool = False) -> ToolOutput:
        try:                                                                     # 1
            t = self.registry.get(name)
        except MisesError as e:
            # recoverable: the model gets the closest real names and calls one of them
            raise MisesError(ErrorCode.NOT_FOUND, e.message,
                             hint=unknown_tool_hint(name, self._names())) from None
        try:                                                                     # 2
            args = t.input_model.model_validate(raw_input)
        except Exception as e:
            raise MisesError(ErrorCode.INVALID_INPUT, str(e)) from None
        policy = self.store.current_policy(run_id)
        if t.safety not in policy.allowed_classes:                               # 3
            raise MisesError(ErrorCode.DENIED,
                             f"tool class {t.safety} is not allowed for this run")
        self._check_scope(t, args, policy)                                       # 4
        self._check_budget(t, run_id, policy)                                    # 5

        payload = args.model_dump()
        ctx = ToolContext(run_id=run_id, user=self.user, store=self.store, events=self.events,
                          workdir=workdir, policy=policy, call_id=call_id, services=self.services,
                          agent_call=agent)
        for check in self.services.get("call_checks") or ():                     # 5b
            check(ctx, name, args)
        # irreversible tools always ask; any other tool may ask for one call (needs_approval)
        reason = t.needs_approval(ctx, args) if t.needs_approval else None
        if t.safety == SafetyClass.IRREVERSIBLE or reason:                       # 6
            deltas = await self._preview(t, ctx, args) if t.preview else {}
            if reason:
                deltas = {**deltas, "approval_reason": reason}
            appr = await self.approvals.request(run_id, name, payload, deltas=deltas)
            tracker = self._tracker(run_id)
            prev = (tracker.state, tracker.detail) if tracker is not None else None
            if tracker is not None:
                tracker.set("waiting_approval", f"waiting for approval: {name}")
            try:
                appr = await self.approvals.wait(appr.id)
            finally:
                if tracker is not None and prev is not None and prev[0] is not None:
                    tracker.set(prev[0], prev[1])
            if appr.status != "approved":
                raise MisesError(ErrorCode.DENIED, f"approval {appr.status} for {name}")
            payload = self.approvals.consume(appr.id, hash_payload(
                appr.edited_payload if appr.edited_payload is not None else appr.payload))
            try:
                args = t.input_model.model_validate(payload)
            except Exception as e:
                raise MisesError(ErrorCode.INVALID_INPUT, f"edited payload invalid: {e}") from None
            payload = args.model_dump()

        cacheable = t.safety != SafetyClass.READ                                 # 7
        # The cache only dedupes a retry of the SAME call (same call_id). A new call with
        # identical input really runs again: state may have changed in between (e.g. set 20,
        # set 10, set 20), and a re-run verifier must give a fresh verdict.
        key = sha256_hex(canonical_json([run_id, name, payload, call_id]))
        if cacheable:
            hit = self.store.cache_get(key)
            if hit is not None:
                out = ToolOutput.model_validate(hit)
                self._finish(run_id, name, call_id, out)
                return out

        try:                                                                     # 8
            out = await asyncio.wait_for(t.handler(ctx, args), t.timeout_s)
        except MisesError:
            raise
        except asyncio.TimeoutError:
            raise MisesError(ErrorCode.TRANSIENT, f"{name} timed out after {t.timeout_s}s",
                             hint="retry the call") from None
        except Exception as e:
            raise MisesError(ErrorCode.TOOL_FAILED, f"{type(e).__name__}: {e}") from None

        out = self._with_assembly_render(run_id, out)
        out = self._redact(out, run_id)                                          # 9
        out = self._cap_images(out)
        out = self._spill(out, workdir, call_id)
        if cacheable and out.ok:                                                 # 10
            # ok=False is never cached: a retry of a failed call must really run again
            self.store.cache_put(key, out.model_dump(mode="json"))
        self._finish(run_id, name, call_id, out)
        # Plan 7 R16: a card for the user when this result created a part, placed it, …;
        # made from the result itself, never from what the model says (a cache hit above is a
        # retry of a call that was already announced)
        announce.on_tool_result(self, run_id, name, payload, out)
        return out

    @staticmethod
    async def _preview(t: ToolDef, ctx: ToolContext, args: Any) -> dict:
        try:
            deltas = await asyncio.wait_for(t.preview(ctx, args), t.timeout_s)
        except MisesError:
            raise
        except asyncio.TimeoutError:
            raise MisesError(ErrorCode.TRANSIENT, f"{t.name} preview timed out after {t.timeout_s}s",
                             hint="retry the call") from None
        except Exception as e:
            raise MisesError(ErrorCode.TOOL_FAILED, f"preview {type(e).__name__}: {e}") from None
        if not isinstance(deltas, dict):
            raise MisesError(ErrorCode.TOOL_FAILED, f"{t.name} preview must return a dict")
        return deltas

    @staticmethod
    def _norm_scope(s: Any) -> Any:
        # Onshape ids are hex and case-insensitive; parse_url lowercases them
        return s.lower() if isinstance(s, str) and s.lower().startswith("onshape:") else s

    def _check_scope(self, t: ToolDef, args: Any, policy) -> None:
        if t.scope_keys is not None:
            keys, label = list(t.scope_keys(args)), "scope"
        elif t.scope_arg:
            keys, label = [getattr(args, t.scope_arg, None)], t.scope_arg
        else:
            return
        allowed = set(policy.edit_scope)
        if t.safety == SafetyClass.READ:
            allowed |= set(policy.read_scope)
        allowed = {self._norm_scope(a) for a in allowed}
        if "*" in allowed or any(self._norm_scope(k) in allowed for k in keys):
            return
        shown = keys[0] if t.scope_keys is None else ", ".join(map(str, keys))
        raise MisesError(ErrorCode.OUT_OF_SCOPE,
                         f"{label}={shown!r} is outside this run's scope",
                         hint="ask the user to widen the scope")

    def _check_budget(self, t: ToolDef, run_id: str, policy) -> None:
        budget.check_usd(self.store, run_id)     # the run's / its family's dollar cap (I-8)
        if t.onshape_cost <= 0:
            return
        used_year = self.store.ledger_used(self.user, since=_year_start())
        used_run = budget.onshape_used(self.store, self.user, run_id)   # a lead: this turn
        if (used_year + t.onshape_cost > policy.caps.onshape_calls_year
                or used_run + t.onshape_cost > policy.caps.onshape_calls_run):
            raise MisesError(ErrorCode.BUDGET_EXHAUSTED,
                             f"{t.name} needs {t.onshape_cost} Onshape calls; allowance exhausted",
                             hint="ask the user to raise the cap")

    def _tracker(self, run_id: str):
        """The run's ActivityTracker, via the runtime service; None in bare unit tests."""
        rt = self.services.get("runtime")
        getter = getattr(rt, "activity_tracker", None)
        if getter is None:
            return None
        try:
            return getter(run_id)
        except Exception:  # noqa: BLE001
            return None

    def _scrub_terms(self, run_id: str | None) -> list[tuple[str, str]]:
        """(text, replacement) pairs scrubbed from a result: the admin and MCP tokens, and for
        any caller but the app's own ui runs, the live ui run ids (I5: an agent never learns
        the id the user's edits go through, so it cannot present it as its identity)."""
        terms = [(s, "[redacted]") for s in (self.services.get("secrets") or ()) if s]
        ui_ids = self.services.get("ui_run_ids") or ()
        if ui_ids and run_id not in ui_ids:
            terms += [(r, "user") for r in ui_ids if r]
        return terms

    def _redact(self, out: ToolOutput, run_id: str | None = None) -> ToolOutput:
        """Scrub the admin and MCP tokens from a result (summary, data, warnings). The sandbox
        already blocks a build from reading server.json; this is the second line, so a token
        that reached a result some other way never reaches the model or the step log."""
        terms = self._scrub_terms(run_id)
        if not terms:
            return out
        # images are base64 pixels, not text: scrub the text part only, then reattach them
        blob = out.model_dump_json(exclude={"images"})
        red = blob
        for s, rep in terms:
            red = red.replace(s, rep)
        if red == blob:
            return out
        return ToolOutput.model_validate_json(red).model_copy(update={"images": out.images})

    def _with_assembly_render(self, run_id: str, out: ToolOutput) -> ToolOutput:
        """E33: the whole-assembly render from this run's last turn rides along with its next
        tool result (image blocks the model looks at), once."""
        take = getattr(self.services.get("runtime"), "take_assembly_images", None)
        imgs = take(run_id) if take is not None else []
        if not imgs:
            return out
        note = ("whole-assembly render from your last turn attached (problem parts tinted: red "
                "overlap, orange stray, amber no joint) — look at it")
        return out.model_copy(update={"images": [*out.images, *imgs],
                                      "warnings": [*out.warnings, note]})

    @staticmethod
    def _cap_images(out: ToolOutput) -> ToolOutput:
        if not out.images:
            return out
        kept, total = [], 0
        for img in out.images:
            if len(kept) >= MAX_IMAGES or total + len(img.data_b64) > MAX_IMAGE_B64_BYTES:
                break
            kept.append(img)
            total += len(img.data_b64)
        if len(kept) == len(out.images):
            return out
        note = (f"{len(out.images) - len(kept)} image(s) dropped by the image cap "
                f"({MAX_IMAGES} images / {MAX_IMAGE_B64_BYTES // (1024 * 1024)} MB); the PNG "
                "handles still have them")
        return out.model_copy(update={"images": kept, "warnings": [*out.warnings, note]})

    def _redact_error(self, e: MisesError, run_id: str | None = None) -> MisesError:
        terms = self._scrub_terms(run_id)
        if not terms:
            return e
        def scrub(v):
            for s, rep in terms:
                if isinstance(v, str):
                    v = v.replace(s, rep)
            return v
        e.message = scrub(e.message)
        if e.hint is not None:
            e.hint = scrub(e.hint)
        return e

    @staticmethod
    def _spill(out: ToolOutput, workdir: Path, call_id: str) -> ToolOutput:
        """The 16 KB cap applies to the JSON text only; images ride through untouched."""
        blob = out.model_dump_json(exclude={"images"})
        if len(blob.encode("utf-8")) <= MAX_OUTPUT_BYTES:
            return out
        path = workdir / "outputs" / f"{call_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(blob)
        return ToolOutput(ok=out.ok, summary=out.summary, handles=[str(path)], data={},
                          warnings=out.warnings, step_kind=out.step_kind, images=out.images)

    def _finish(self, run_id: str, name: str, call_id: str, out: ToolOutput) -> None:
        # the step log keeps what was shown, never the pixels (they are in the PNG handles)
        body = {"call_id": call_id, "name": name,
                "output": out.model_dump(mode="json", exclude={"images"})}
        if out.images:
            body["images"] = [{"media_type": i.media_type, "label": i.label,
                               "b64_bytes": len(i.data_b64)} for i in out.images]
        self.store.append_step(run_id, out.step_kind, body)
        self.events.publish(Event(type="tool_result", run_id=run_id,
                                  data={"call_id": call_id, "name": name, "ok": out.ok,
                                        "summary": out.summary}))
