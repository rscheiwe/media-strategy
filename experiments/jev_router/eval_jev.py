"""Evaluate Jev's route classification against the existing workflow eval set."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from jev_client import decide


ROOT = Path(__file__).resolve().parents[2]
EVALSET = ROOT / "experiments/workflow_trigger/evals/evalset.yaml"
RESULTS = Path(__file__).resolve().parent / "results"

ROUTES = {
    "chat": "Conversational answer only: greeting, capability question, definition, general fact, or product discussion. Do not run or ask for workflow details.",
    "workflow_candidate": "The user is requesting, rerunning, or explicitly proposing a media recommendation/workflow, or is changing a planning detail and wants the plan recalculated. This is only a candidate; code must validate slots and authorization before running.",
    "missing_details": "The user is answering a previously requested campaign detail, or is clearly trying to start a recommendation but required planning information is missing. Do not run until code validates all required slots.",
    "discuss_result": "The user is asking about an already displayed recommendation without requesting a recalculation.",
    "decline": "The user says not to run, to wait, cancel, or otherwise withdraw authorization for a workflow.",
    "status": "A workflow is already running and the user is asking about its status or trying to start another one. Do not start a second run.",
}


def questions() -> dict[str, Any]:
    return {
        "route": {"type": "choice", "instructions": "Which route best describes the latest user message given the conversation state?", "criteria": ROUTES},
        "explicit_run_authorization": {"type": "noul", "instructions": "Does the latest user message explicitly authorize running or rerunning the recommendation workflow now?"},
        "proposes_recalculation": {"type": "noul", "instructions": "Does the latest user message propose changing a planning input and recalculating the recommendation, rather than merely discussing the current result?"},
    }


def expected_route(expect: dict[str, Any], state: dict[str, Any], user: str) -> str:
    action = expect.get("action")
    if action == "call":
        return "workflow_candidate"
    if action == "ask":
        return "workflow_candidate"
    if state.get("workflow_running"):
        return "status"
    lowered = user.lower()
    if any(word in lowered for word in ("don't run", "do not run", "never mind", "cancel", "wait")):
        return "decline"
    if state.get("last_result") and any(word in lowered for word in ("why", "what does", "difference", "is ", "great", "thanks")):
        return "discuss_result"
    return "chat"


def state_for(case: dict[str, Any], previous: list[dict[str, Any]]) -> dict[str, Any]:
    setup = case.get("setup") or {}
    result = setup.get("after_result")
    running = setup.get("running")
    return {
        "known_campaign_details": result or running or {},
        "pending_request": any(row.get("expected_action") == "ask" for row in previous),
        "last_result_available": bool(result),
        "workflow_running": bool(running),
        "recent_turns": [{"user": row["user"], "expected_action": row["expected_action"]} for row in previous[-4:]],
    }


def load_cases() -> list[dict[str, Any]]:
    raw = yaml.safe_load(EVALSET.read_text())
    cases: list[dict[str, Any]] = []
    for case in raw:
        previous: list[dict[str, Any]] = []
        for turn_index, turn in enumerate(case.get("turns", [])):
            expect = turn.get("expect") or {}
            action = expect.get("action")
            if action == "no_call":
                action = "no_call"
            cases.append({"case": case["id"], "category": case.get("category"), "turn": turn_index, "user": turn["user"], "expect": expect, "state": state_for(case, previous)})
            previous.append({"user": turn["user"], "expected_action": action})
    return cases


def run(k: int) -> Path:
    cases = load_cases()
    rows: list[dict[str, Any]] = []
    for trial in range(k):
        for item in cases:
            expected = expected_route(item["expect"], item["state"], item["user"])
            expected_action = item["expect"].get("action")
            expected_workflow_relevant = expected_action in {"call", "ask"}
            record: dict[str, Any] = {"case": item["case"], "category": item["category"], "turn": item["turn"], "trial": trial, "user": item["user"], "expected_route": expected, "expected_action": expected_action, "expected_workflow_relevant": expected_workflow_relevant}
            try:
                result = decide({**item["state"], "latest_user_message": item["user"]}, questions())
                answer = result.answers.get("route") or {}
                record.update({"route": answer.get("choice"), "probabilities": answer.get("probabilities"), "confidence": answer.get("confidence"), "explicit_run_authorization": (result.answers.get("explicit_run_authorization") or {}).get("noul"), "proposes_recalculation": (result.answers.get("proposes_recalculation") or {}).get("noul"), "usage": result.usage, "model": result.model, "request_id": result.request_id, "latency_s": result.latency_s})
                record["correct"] = record["route"] == expected
                record["workflow_candidate"] = record["route"] == "workflow_candidate"
                record["workflow_relevant_correct"] = record["workflow_candidate"] or record["route"] == "missing_details" if expected_workflow_relevant else not record["workflow_candidate"]
            except Exception as exc:
                record.update({"error": f"{type(exc).__name__}: {exc}", "correct": False})
            rows.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output = RESULTS / timestamp
    output.mkdir(parents=True, exist_ok=True)
    (output / "turns.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n")
    scored = [r for r in rows if "error" not in r]
    workflow_scored = [r for r in scored if r.get("category") != "ambiguous"]
    expected_positive = [r for r in workflow_scored if r.get("expected_workflow_relevant")]
    no_call = [r for r in workflow_scored if r.get("expected_action") == "no_call"]
    summary = {"trials": k, "turns": len(rows), "exact_route_correct": sum(bool(r.get("correct")) for r in scored), "exact_route_accuracy": (sum(bool(r.get("correct")) for r in scored) / len(scored) if scored else 0), "workflow_relevance_recall": (sum(bool(r.get("workflow_relevant_correct")) for r in expected_positive) / len(expected_positive) if expected_positive else 0), "no_call_candidate_rate": (sum(bool(r.get("workflow_candidate")) for r in no_call) / len(no_call) if no_call else 0), "no_call_candidate_count": sum(bool(r.get("workflow_candidate")) for r in no_call), "calls_routed_candidate": sum(r.get("route") == "workflow_candidate" for r in workflow_scored if r.get("expected_action") == "call"), "calls_routed_missing_details": sum(r.get("route") == "missing_details" for r in workflow_scored if r.get("expected_action") == "call"), "errors": len(rows) - len(scored), "latency_p50_s": percentile([r["latency_s"] for r in scored], 0.50), "latency_p95_s": percentile([r["latency_s"] for r in scored], 0.95), "model": __import__("jev_client").MODEL}
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return output


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = min(len(values) - 1, max(0, round((len(values) - 1) * p)))
    return round(values[index], 4)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=1)
    args = parser.parse_args()
    if args.k < 1:
        raise SystemExit("--k must be >= 1")
    run(args.k)
