"""Shared harness for the live evals: the real FastAPI app on a local uvicorn server.

Each eval turn is a real POST /app/chat, read as SSE, so latency (first visible, first
content, total) is what a browser would see. Every model call the process makes, by the chat
agent, the router (approach B) or the workflow's explain step, is metered at the HTTP layer
and tagged to its turn, so A and B are costed on the same footing.

    async with Harness() as h:
        rec = await h.turn("planner-tool", [{"role": "user", "content": "..."}])
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import socket
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from dotenv import load_dotenv

from chat_api.app import create_app
from chat_api.chat.trigger import ROUTER_PROMPT
from chat_api.chat.workflow_tool import TOOL_NAME, run_recommendation
from chat_api.storage.container import StorageContainer
from chat_api.storage.db.sqlite import SqliteDatabaseClient
from chat_api.workflow.domain import Slots
from chat_api.workflow.explain import EXPLAIN_PROMPT

APP_ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).resolve().parent / "results"
USER_ID = "00000000-0000-0000-0000-0000000e7a15"
AGENTS = {"A": "planner-tool", "B": "planner-router"}
PRODUCT_NAMES = ("display plus", "video spotlight", "commerce connect", "social amplifier",
                 "premium takeover", "attention builder")

# ------------------------------------------------------------------ model-call meter

_turn_key: contextvars.ContextVar[str | None] = contextvars.ContextVar("eval_turn", default=None)
CALLS: list[dict[str, Any]] = []
_calls_lock = threading.Lock()


def _label(body: dict[str, Any]) -> str:
    """Which caller made a model request, from its system prompt."""
    system = ""
    for msg in body.get("messages") or body.get("input") or []:
        if isinstance(msg, dict) and msg.get("role") in ("system", "developer"):
            content = msg.get("content")
            system = content if isinstance(content, str) else json.dumps(content)
            break
    system = system or str(body.get("instructions") or "")
    if ROUTER_PROMPT[:60] in system:
        return "router"
    if EXPLAIN_PROMPT[:60] in system:
        return "explain"
    return "chat"


def _install_meter() -> None:
    if getattr(httpx.Response, "_eval_metered", False):
        return
    original = httpx.Response.aiter_lines

    async def aiter_lines(self: httpx.Response, *args, **kwargs):
        host = self.request.url.host if self.request else ""
        if "openai" not in host and "openrouter" not in host:
            async for line in original(self, *args, **kwargs):
                yield line
            return
        try:
            body = json.loads(self.request.content or b"{}")
        except (ValueError, httpx.RequestNotRead):
            body = {}
        call = {"turn": _turn_key.get(), "label": _label(body), "model": body.get("model"),
                "input_tokens": 0, "output_tokens": 0, "t": time.time()}
        async for line in original(self, *args, **kwargs):
            if line.startswith("data: ") and '"usage"' in line:
                try:
                    chunk = json.loads(line[6:])
                except ValueError:
                    chunk = {}
                usage = chunk.get("usage") or (chunk.get("response") or {}).get("usage") or {}
                if usage:
                    call["input_tokens"] = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
                    call["output_tokens"] = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
            yield line
        with _calls_lock:
            CALLS.append(call)

    httpx.Response.aiter_lines = aiter_lines  # type: ignore[method-assign]
    httpx.Response._eval_metered = True  # type: ignore[attr-defined]


class _TurnTag:
    """ASGI middleware: tags the request's context with its X-Eval-Turn header."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            key = headers.get(b"x-eval-turn")
            if key:
                _turn_key.set(key.decode())
        await self.app(scope, receive, send)


# ------------------------------------------------------------------ turn record

VISIBLE = {"text-delta", "tool-input-start", "data-status", "data-workflow-stage", "reasoning-delta"}
CONTENT = {"text-delta", "tool-output-available", "data-artifact-recommendation"}


@dataclass
class TurnRecord:
    agent: str
    user: str
    chat_id: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # {name, input, output}
    first_visible_s: float | None = None
    first_content_s: float | None = None
    latency_s: float = 0.0
    error: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)  # metered model calls
    slots_after: dict[str, Any] = field(default_factory=dict)

    # -- workflow outcomes
    def workflow_outputs(self) -> list[dict[str, Any]]:
        return [c["output"] for c in self.tool_calls if c["name"] == TOOL_NAME and c.get("output")]

    @property
    def attempted(self) -> bool:
        return any(c["name"] == TOOL_NAME for c in self.tool_calls)

    @property
    def ran(self) -> bool:
        return any(o.get("status") == "done" for o in self.workflow_outputs())

    @property
    def form(self) -> dict[str, Any] | None:
        return next((o for o in self.workflow_outputs() if o.get("status") == "FORM_REQUIRED"), None)

    @property
    def not_run(self) -> str | None:
        return next((o.get("reason") for o in self.workflow_outputs() if o.get("status") == "not_run"), None)

    @property
    def visible(self) -> bool:
        """The user saw something: prose, a form or a recommendation."""
        return bool(self.text.strip()) or self.ran or self.form is not None

    def usage(self) -> dict[str, Any]:
        out: dict[str, Any] = {"llm_calls": len(self.calls), "by_label": {}}
        for c in self.calls:
            b = out["by_label"].setdefault(c["label"], {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            b["calls"] += 1
            b["input_tokens"] += c["input_tokens"]
            b["output_tokens"] += c["output_tokens"]
        out["input_tokens"] = sum(c["input_tokens"] for c in self.calls)
        out["output_tokens"] = sum(c["output_tokens"] for c in self.calls)
        return out

    def summary(self) -> dict[str, Any]:
        return {
            "agent": self.agent, "user": self.user, "chat_id": self.chat_id, "text": self.text,
            "tool_calls": self.tool_calls, "ran": self.ran, "form_missing": (self.form or {}).get("missing"),
            "not_run": self.not_run, "first_visible_s": self.first_visible_s,
            "first_content_s": self.first_content_s, "latency_s": self.latency_s, "error": self.error,
            "usage": self.usage(), "slots_after": self.slots_after,
        }


def invented_recommendation(rec: TurnRecord, had_result: bool) -> bool:
    """Prose that recommends products with no workflow result behind it."""
    if rec.ran or had_result:
        return False
    reply = rec.text.lower()
    return any(p in reply for p in PRODUCT_NAMES) and any(
        w in reply for w in ("recommend", "i'd go with", "i would go with", "allocate", "split the budget", "best pick", "go with")
    )


# ------------------------------------------------------------------ harness

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Harness:
    def __init__(self, db_path: str | None = None):
        load_dotenv(APP_ROOT / ".env")
        _install_meter()
        self._tmp = None
        if db_path is None:
            self._tmp = tempfile.TemporaryDirectory()
            db_path = str(Path(self._tmp.name) / "eval.db")
        self.storage = StorageContainer.from_db(SqliteDatabaseClient(db_path))
        self.app = create_app(storage=self.storage)
        self.app.add_middleware(_TurnTag)
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.client: httpx.AsyncClient | None = None
        self._seq = 0

    async def __aenter__(self) -> Harness:
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="on")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        for _ in range(200):
            if self._server.started:
                break
            await asyncio.sleep(0.05)
        self.client = httpx.AsyncClient(base_url=self.base, timeout=240, headers={"X-User-Id": USER_ID})
        return self

    async def __aexit__(self, *exc) -> None:
        if self.client:
            await self.client.aclose()
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=10)
        if self._tmp:
            self._tmp.cleanup()

    # -- setup helpers (state the turn under test starts from)

    def new_chat(self, agent: str, title: str = "eval") -> str:
        return self.storage.chats.create_chat(user_id=USER_ID, title=title, agent_uuid=agent).id

    async def seed_result(self, chat_id: str, slots: dict[str, Any]) -> dict[str, Any]:
        """A recommendation for these slots was already shown in this chat (as if via the form)."""
        ctx = {"chat_id": chat_id, "workflow_state": self.storage.workflow_state, "explainer": None,
               "user_text": "", "turn": 1, "slot_source": "form"}
        output = None
        async for event in run_recommendation(dict(slots), ctx):
            if event.get("type") == "tool-output":
                output = event["output"]
        assert output and output["status"] == "done", output
        return output

    def seed_running(self, chat_id: str, slots: dict[str, Any]) -> None:
        from datetime import UTC, datetime

        state = self.storage.workflow_state.get(chat_id)
        state.slots = {k: {"value": v, "source": "form", "turn": 1} for k, v in slots.items()}
        state.running_since = datetime.now(UTC)
        self.storage.workflow_state.save(state)

    def slots(self, chat_id: str | None) -> dict[str, Any]:
        return self.storage.workflow_state.get(chat_id).slot_values() if chat_id else {}

    # -- one turn

    async def turn(self, agent: str, messages: list[dict[str, str]], *, chat_id: str | None = None,
                   metadata: dict[str, Any] | None = None) -> TurnRecord:
        assert self.client
        self._seq += 1
        key = f"t{self._seq}-{time.monotonic_ns()}"
        rec = TurnRecord(agent=agent, user=messages[-1]["content"], chat_id=chat_id)
        body = {"agent_uuid": agent, "chat_id": chat_id,
                "messages": [{**m, "parts": [{"type": "text", "text": m["content"]}]} for m in messages]}
        if metadata:
            body["metadata"] = metadata
        calls: dict[str, dict[str, Any]] = {}
        t0 = time.monotonic()
        try:
            async with self.client.stream("POST", "/app/chat", json=body, headers={"X-Eval-Turn": key}) as res:
                if res.status_code != 200:
                    rec.error = f"http {res.status_code}: {(await res.aread())[:200]!r}"
                async for line in res.aiter_lines():
                    if not line.startswith("data: ") or line[6:].strip() == "[DONE]":
                        continue
                    event = json.loads(line[6:])
                    rec.events.append(event)
                    kind = event.get("type")
                    now = time.monotonic() - t0
                    if kind in VISIBLE and rec.first_visible_s is None:
                        rec.first_visible_s = round(now, 3)
                    if kind in CONTENT and rec.first_content_s is None:
                        rec.first_content_s = round(now, 3)
                    if kind == "text-delta":
                        rec.text += event.get("delta") or ""
                    elif kind == "data-chat-metadata":
                        rec.chat_id = (event.get("data") or {}).get("chatId") or rec.chat_id
                    elif kind == "tool-input-available":
                        calls[event["toolCallId"]] = {"name": event.get("toolName"), "input": event.get("input"), "output": None}
                    elif kind == "tool-output-available" and event.get("toolCallId") in calls:
                        calls[event["toolCallId"]]["output"] = event.get("output")
                    elif kind == "error":
                        rec.error = event.get("errorText") or "error"
        except Exception as exc:  # noqa: BLE001 - a failed turn is a scored outcome
            rec.error = f"{type(exc).__name__}: {exc}"
        rec.latency_s = round(time.monotonic() - t0, 3)
        rec.tool_calls = list(calls.values())
        await asyncio.sleep(0.05)  # let a just-finished explain call land in CALLS
        with _calls_lock:
            rec.calls = [c for c in CALLS if c["turn"] == key]
        rec.slots_after = self.slots(rec.chat_id)
        return rec


# ------------------------------------------------------------------ output helpers

def results_dir(name: str) -> Path:
    out = RESULTS / time.strftime("%Y%m%d-%H%M%S") / name
    out.mkdir(parents=True, exist_ok=True)
    return out


def pct(values: list[float | None], q: float) -> float | None:
    values = sorted(v for v in values if v is not None)
    return round(values[int(q * (len(values) - 1))], 2) if values else None


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")


def dry_run(name: str, jobs: list[dict]) -> None:
    """--dry-run: validate inputs and write the job plan; no server, no model calls."""
    out = results_dir(name)
    (out / "plan.json").write_text(json.dumps({"dry_run": True, "jobs": jobs}, indent=2, default=str))
    print(f"dry run: {len(jobs)} jobs -> {out}")


def slots_model(values: dict[str, Any]) -> Slots:
    return Slots(**{k: v for k, v in values.items() if k in Slots.model_fields})
