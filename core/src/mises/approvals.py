"""Human approval requests: first decision wins, hash-bound, single consume."""
from __future__ import annotations

import asyncio
import time
import uuid

from .events import Event, EventBus
from .store import Approval, Store
from .types import ErrorCode, MisesError, canonical_json, sha256_hex


def hash_payload(payload: dict) -> str:
    return sha256_hex(canonical_json(payload))


class ApprovalService:
    def __init__(self, store: Store, events: EventBus):
        self.store = store
        self.events = events
        self._waiters: dict[str, asyncio.Event] = {}

    def _event(self, approval_id: str) -> asyncio.Event:
        return self._waiters.setdefault(approval_id, asyncio.Event())

    def _wake(self, approval_id: str) -> None:
        self._event(approval_id).set()

    def _expire_if_due(self, a: Approval) -> Approval:
        if a.status == "pending" and time.time() >= a.expires_at:
            self.store.cas_approval(a.id, from_status="pending", to_status="expired",
                                    decided_by="system:expired", decided_at=time.time())
            self._wake(a.id)
            return self.store.get_approval(a.id)
        return a

    async def request(self, run_id: str, action: str, payload: dict, *, microversion=None,
                      deltas=None, ttl_s: float = 1800) -> Approval:
        a = self.store.insert_approval(Approval(
            id=uuid.uuid4().hex[:12], run_id=run_id, action=action, payload=payload,
            payload_hash=hash_payload(payload), microversion=microversion,
            deltas=deltas or {}, expires_at=time.time() + ttl_s))
        self._event(a.id)
        self.events.publish(Event(type="approval_requested", run_id=run_id,
                                  data=a.model_dump(mode="json")))
        return a

    async def wait(self, approval_id: str, timeout_s: float | None = None) -> Approval:
        ev = self._event(approval_id)
        deadline = None if timeout_s is None else time.time() + timeout_s
        while True:
            a = self._expire_if_due(self.store.get_approval(approval_id))
            if a.status != "pending":
                return a
            limit = a.expires_at - time.time()
            if deadline is not None:
                limit = min(limit, deadline - time.time())
            if limit <= 0:
                if deadline is not None and time.time() >= deadline:
                    return a
                continue
            try:
                await asyncio.wait_for(ev.wait(), limit)
            except asyncio.TimeoutError:
                pass

    def decide(self, approval_id: str, payload_hash: str, approve: bool, decided_by: str,
               edited_payload: dict | None = None) -> Approval:
        a = self._expire_if_due(self.store.get_approval(approval_id))
        if a.payload_hash != payload_hash:
            raise MisesError(ErrorCode.STALE_VERSION,
                             "approval payload changed since the card was shown",
                             hint="refresh the approval card")
        if a.status != "pending":
            return a
        fields: dict = {"decided_by": decided_by, "decided_at": time.time()}
        if approve and edited_payload is not None:
            fields["edited_payload"] = edited_payload
        self.store.cas_approval(approval_id, from_status="pending",
                                to_status="approved" if approve else "rejected", **fields)
        self._wake(approval_id)
        return self.store.get_approval(approval_id)

    def consume(self, approval_id: str, payload_hash: str) -> dict:
        try:
            a = self._expire_if_due(self.store.get_approval(approval_id))
        except KeyError:
            raise MisesError(ErrorCode.NEEDS_APPROVAL, "unknown approval") from None
        if a.status != "approved":
            raise MisesError(ErrorCode.NEEDS_APPROVAL, f"approval is {a.status}, not approved")
        if time.time() >= a.expires_at:
            self.store.cas_approval(approval_id, from_status="approved", to_status="expired")
            raise MisesError(ErrorCode.NEEDS_APPROVAL, "approval expired",
                             hint="request a new approval")
        effective = a.edited_payload if a.edited_payload is not None else a.payload
        if payload_hash != hash_payload(effective):
            raise MisesError(ErrorCode.NEEDS_APPROVAL,
                             "payload differs from the approved one", hint="request a new approval")
        if not self.store.cas_approval(approval_id, from_status="approved", to_status="executed"):
            raise MisesError(ErrorCode.NEEDS_APPROVAL, "approval already used")
        return effective

    def cancel_run(self, run_id: str) -> int:
        n = 0
        for a in self.store.pending_approvals(run_id):
            if self.store.cas_approval(a.id, from_status="pending", to_status="rejected",
                                       decided_by="system:cancel", decided_at=time.time()):
                n += 1
            self._wake(a.id)
        return n
