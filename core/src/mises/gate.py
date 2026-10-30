"""The gate: every tool call from any agent passes through this pipeline."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from .approvals import ApprovalService, hash_payload
from .events import Event, EventBus
from .store import Store
from .tools.registry import ToolContext, ToolDef, ToolRegistry
from .types import (ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput,
                    canonical_json, sha256_hex)

MAX_OUTPUT_BYTES = 16 * 1024


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

    async def call(self, run_id: str, tool_name: str, raw_input: dict, *, call_id: str,
                   workdir: Path) -> ToolOutput:
        self.store.append_step(run_id, StepKind.TOOL_CALL,
                               {"call_id": call_id, "name": tool_name, "input": raw_input})
        self.events.publish(Event(type="tool_call", run_id=run_id,
                                  data={"call_id": call_id, "name": tool_name, "input": raw_input}))
        try:
            out = await self._run(run_id, tool_name, raw_input, call_id, Path(workdir))
        except MisesError as e:
            e = self._redact_error(e, run_id)
            self._error(run_id, tool_name, call_id, e)
            raise
        except asyncio.CancelledError:
            # every tool_call Step gets an outcome Step, also when the run is cancelled
            self._error(run_id, tool_name, call_id,
                        MisesError(ErrorCode.CANCELLED, "call cancelled before it finished"))
            raise
        except Exception as e:
            self._error(run_id, tool_name, call_id, self._redact_error(
                MisesError(ErrorCode.TOOL_FAILED, f"{type(e).__name__}: {e}"), run_id))
            raise
        return out

    def _error(self, run_id: str, name: str, call_id: str, e: MisesError) -> None:
        self.store.append_step(run_id, StepKind.ERROR, {"call_id": call_id, "name": name,
                                                        **e.to_dict()})
        self.events.publish(Event(type="tool_result", run_id=run_id,
                                  data={"call_id": call_id, "name": name, "ok": False,
                                        **e.to_dict()}))

    async def _run(self, run_id: str, name: str, raw_input: dict, call_id: str,
                   workdir: Path) -> ToolOutput:
        t = self.registry.get(name)                                              # 1
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
                          workdir=workdir, policy=policy, call_id=call_id, services=self.services)
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

        out = self._redact(out, run_id)                                          # 9
        out = self._spill(out, workdir, call_id)
        if cacheable and out.ok:                                                 # 10
            # ok=False is never cached: a retry of a failed call must really run again
            self.store.cache_put(key, out.model_dump(mode="json"))
        self._finish(run_id, name, call_id, out)
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
        if t.onshape_cost <= 0:
            return
        used_year = self.store.ledger_used(self.user, since=_year_start())
        used_run = self.store.get_run(run_id).onshape_calls
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
        blob = out.model_dump_json()
        red = blob
        for s, rep in terms:
            red = red.replace(s, rep)
        return out if red == blob else ToolOutput.model_validate_json(red)

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
        blob = out.model_dump_json()
        if len(blob.encode("utf-8")) <= MAX_OUTPUT_BYTES:
            return out
        path = workdir / "outputs" / f"{call_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(blob)
        return ToolOutput(ok=out.ok, summary=out.summary, handles=[str(path)], data={},
                          warnings=out.warnings, step_kind=out.step_kind)

    def _finish(self, run_id: str, name: str, call_id: str, out: ToolOutput) -> None:
        body = {"call_id": call_id, "name": name, "output": out.model_dump(mode="json")}
        self.store.append_step(run_id, out.step_kind, body)
        self.events.publish(Event(type="tool_result", run_id=run_id,
                                  data={"call_id": call_id, "name": name, "ok": out.ok,
                                        "summary": out.summary}))
