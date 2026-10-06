"""The clarification form (brief requirements 2 and 7): a FORM_REQUIRED tool output.

A form-tool contract (status FORM_REQUIRED + form_config), with pre-filled values: every field is shown, filled with what's already known, so a strategist
can confirm or correct a misread detail in one step. `missing` lists what blocks the run.
"""

from __future__ import annotations

from .domain import GEOS, KPI_LABELS, KPIS, VERTICALS, Slots
from .steps import slot_valid

FIELD_LABELS = {
    "client_name": "Client",
    "vertical": "Vertical",
    "kpi": "Primary KPI",
    "geo": "Geo",
    "budget": "Budget (USD)",
    "impression_goal": "Impression goal (optional)",
}
BUDGET_PRESETS = [10000, 15000, 25000, 40000]


MISSING_WORDS = {"vertical": "vertical", "kpi": "primary KPI", "geo": "geo", "budget": "budget"}


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def build_form(
    slots: Slots,
    missing: list[str],
    *,
    form_id: str,
    inferred: list[str] | None = None,
    client_suggestions: list[str] | None = None,
    confirm: bool = False,
) -> dict:
    """The tool output that asks for the missing details.

    `client_suggestions` (the sample briefs' advertisers) are offered as you type in the client
    field; any name is accepted.
    """
    inferred = set(inferred or [])

    def field(name: str, type_: str, **extra) -> dict:
        value = getattr(slots, name)
        if name in ("vertical", "kpi", "geo", "budget") and value is not None and not slot_valid(name, value):
            value = None  # never pre-fill an invalid value ("LATAM" as a geo)
        return {
            "name": name,
            "label": FIELD_LABELS[name],
            "type": type_,
            "required": name in ("vertical", "kpi", "geo", "budget"),
            "value": value,
            "missing": name in missing,
            "inferred": name in inferred,
            **extra,
        }

    missing_labels = _join([MISSING_WORDS.get(m, m) for m in missing]) if missing else ""
    return {
        "status": "FORM_REQUIRED",
        "form_id": form_id,
        "missing": list(missing),
        "form_config": {
            "title": "Confirm the brief",
            "description": (
                f"I need the {missing_labels} to recommend products."
                if missing
                else "Confirm these details, or change any of them, then run it." if confirm else "Confirm the details."
            ),
            "fields": [
                field("client_name", "text", placeholder="Type or select an advertiser…", suggestions=list(client_suggestions or [])),
                field("vertical", "choice", options=[{"value": v, "label": v} for v in VERTICALS]),
                field("kpi", "choice", options=[{"value": k, "label": KPI_LABELS[k].capitalize() if k == "in_view_rate" else KPI_LABELS[k]} for k in KPIS]),
                field("geo", "choice", options=[{"value": g, "label": g} for g in GEOS]),
                field("budget", "number", presets=BUDGET_PRESETS, min=1),
                field("impression_goal", "number", min=0),
            ],
            "submit_label": "Get recommendation",
            "next_tool": "run_recommendation",
        },
    }
