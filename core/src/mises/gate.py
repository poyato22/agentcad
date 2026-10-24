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
            self.store.append_step(run_id, StepKind.ERROR,
                                   {"call_id": call_id, "name": tool_name, **e.to_dict()})
            self.events.publish(Event(type="tool_result", run_id=run_id,
                                      data={"call_id": call_id, "name": tool_name, "ok": False,
                                            **e.to_dict()}))
            raise
        return out

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
        if t.safety == SafetyClass.IRREVERSIBLE:                                 # 6
            appr = await self.approvals.request(run_id, name, payload, deltas={})
            appr = await self.approvals.wait(appr.id)
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
        key = sha256_hex(canonical_json([run_id, name, payload]))
        if cacheable:
            hit = self.store.cache_get(key)
            if hit is not None:
                out = ToolOutput.model_validate(hit)
                self._finish(run_id, name, call_id, out)
                return out

        ctx = ToolContext(run_id=run_id, user=self.user, store=self.store, events=self.events,
                          workdir=workdir, policy=policy, call_id=call_id, services=self.services)
        try:                                                                     # 8
            out = await asyncio.wait_for(t.handler(ctx, args), t.timeout_s)
        except MisesError:
            raise
        except asyncio.TimeoutError:
            raise MisesError(ErrorCode.TRANSIENT, f"{name} timed out after {t.timeout_s}s",
                             hint="retry the call") from None
        except Exception as e:
            raise MisesError(ErrorCode.TOOL_FAILED, f"{type(e).__name__}: {e}") from None

        out = self._spill(out, workdir, call_id)                                 # 9
        if cacheable:                                                            # 10
            self.store.cache_put(key, out.model_dump(mode="json"))
        self._finish(run_id, name, call_id, out)
        return out

    def _check_scope(self, t: ToolDef, args: Any, policy) -> None:
        if not t.scope_arg:
            return
        value = getattr(args, t.scope_arg, None)
        allowed = set(policy.edit_scope)
        if t.safety == SafetyClass.READ:
            allowed |= set(policy.read_scope)
        if "*" in allowed or value in allowed:
            return
        raise MisesError(ErrorCode.OUT_OF_SCOPE,
                         f"{t.scope_arg}={value!r} is outside this run's scope",
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
