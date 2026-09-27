"""In-process human-approval broker.

Connects three parties that never call each other directly:

1. Agent code (via `agentguard.request_approval`, decorator.py) that
   needs to *wait* for a human decision, with a deterministic timeout so
   it never hangs forever (Rule: "The agent must never hang forever
   waiting for a human").
2. The FastAPI WebSocket endpoint (`server/api.py`,
   `/ws/runs/{run_id}/approval`) that pushes new requests to a connected
   dashboard and relays that dashboard's response back.
3. The REST endpoint (`POST /runs/{run_id}/human-decision`) that
   resolves a request from a plain HTTP call (e.g. a non-WebSocket
   client, or the test suite).

This is intentionally in-memory/per-process: Phase 2's dashboard talks
to a single running API process. A multi-process/multi-tenant broker
(backed by e.g. Redis pub/sub) is exactly the kind of thing later
phases can swap in behind this same interface without touching
decorator.py or server/api.py.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from ..models import HumanDecision, HumanOutcome


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalBroker:
    def __init__(self) -> None:
        self._pending: dict[str, HumanDecision] = {}
        self._futures: dict[str, "asyncio.Future[HumanDecision]"] = {}
        self._loops: dict[str, asyncio.AbstractEventLoop] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    # -- agent side: block (with timeout) until a human responds -------

    async def request(self, request: HumanDecision) -> HumanDecision:
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[HumanDecision]" = loop.create_future()
        self._pending[request.id] = request
        self._futures[request.id] = fut
        self._loops[request.id] = loop

        await self._publish(request.run_id, {"type": "approval_request", "request": _dump(request)})

        try:
            resolved = await asyncio.wait_for(fut, timeout=request.timeout_s)
        except asyncio.TimeoutError:
            request.status = "timeout"
            request.resolved_at = _now()
            # Distinct from the original "why approval was requested" text
            # (still sitting in request.reason) -- a timeout is nobody's
            # decision, so it must never read like a human's rejection.
            request.reason = f"timed out waiting for human approval after {request.timeout_s}s"
            resolved = request
            await self._publish(request.run_id, {"type": "approval_resolved", "request": _dump(resolved)})
        finally:
            self._pending.pop(request.id, None)
            self._futures.pop(request.id, None)
            self._loops.pop(request.id, None)

        return resolved

    # -- resolving side: REST endpoint or WebSocket-relayed dashboard reply

    def resolve(
        self,
        request_id: str,
        status: HumanOutcome,
        resolved_by: Optional[str] = None,
        modified_evidence: Optional[dict] = None,
        reason: Optional[str] = None,
    ) -> Optional[HumanDecision]:
        """Thread-safe: the caller (a REST handler or a WebSocket
        connection) may be running on a different OS thread — and
        therefore a different event loop — than the one the agent's
        `request()` coroutine is suspended on (e.g. the agent process and
        the dashboard API are driven by separate loops). Resolving the
        future via `call_soon_threadsafe` on ITS loop is what makes that
        safe; a bare `fut.set_result(...)` here is only well-defined when
        caller and awaiter happen to share a loop, which is not
        guaranteed once the API and the agent are different processes.

        `modified_evidence`: a reviewer's corrected proposal params —
        carried on the resolved HumanDecision for perform_action_with_result()
        callers to read back; never mutates `evidence` itself.

        `reason`: the reviewer's own reason for this resolution (e.g. a
        REST caller's `body.reason`, or a terminal prompt's typed
        explanation). ALWAYS overwrites `pending.reason` — which up to
        this point held only "why approval was requested" — with either
        that explicit reason or, absent one, a fallback that still names
        who/what resolved it. Never leaves the stale escalation text in
        place: that made a genuine human rejection indistinguishable from
        a timeout or an approval, since resolve() never touched the field
        before this parameter existed.
        """
        pending = self._pending.get(request_id)
        fut = self._futures.get(request_id)
        loop = self._loops.get(request_id)
        if pending is None or fut is None or fut.done():
            return None

        pending.status = status
        pending.resolved_at = _now()
        pending.resolved_by = resolved_by
        pending.modified_evidence = modified_evidence
        pending.reason = reason or (f"{status} by {resolved_by}" if resolved_by else f"status={status}")

        def _set_result() -> None:
            if not fut.done():
                fut.set_result(pending)

        if loop is not None:
            loop.call_soon_threadsafe(_set_result)
        else:
            _set_result()
        return pending

    def get_pending(self, run_id: str) -> list[HumanDecision]:
        return [r for r in self._pending.values() if r.run_id == run_id]

    async def notify(self, run_id: str, message: dict) -> None:
        """Public fan-out for events that don't originate from a live
        `request()` waiter — e.g. the post-hoc "low confidence + high
        impact" HUMAN decision, which has nothing blocked in `await`."""
        await self._publish(run_id, message)

    # -- WebSocket fan-out -----------------------------------------------

    def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(run_id, set()).add(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(run_id)
        if subscribers is not None:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(run_id, None)

    async def _publish(self, run_id: str, message: dict) -> None:
        for queue in self._subscribers.get(run_id, ()):  # copy not needed: no mutation during iteration
            queue.put_nowait(message)


def _dump(request: HumanDecision) -> dict:
    return request.model_dump(mode="json")


_broker: ApprovalBroker | None = None


def get_broker() -> ApprovalBroker:
    global _broker
    if _broker is None:
        _broker = ApprovalBroker()
    return _broker


def configure_broker(broker: ApprovalBroker) -> None:
    global _broker
    _broker = broker


def reset_broker() -> None:
    global _broker
    _broker = None
