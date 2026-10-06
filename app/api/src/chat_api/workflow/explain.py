"""The rationale (brief requirements 9.5 and 9.6), as a schema the model fills and code checks.

The deterministic steps decide; `explain_input` lays out what they computed as labelled facts.
The model returns `RationaleOutput`: one field per requirement (why the pick is the best fit;
each rejected top alternative with its explanation; tradeoffs come from code). `check` verifies it in code
(every product ranked above the pick, no more and no fewer; the pick named); one retry states
the problems; anything still missing falls back to the templated sentence for that product.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import re

from pydantic import BaseModel, Field, create_model

from .domain import KPI_LABELS, Recommendation, RejectedNote, Rejection, Tradeoff


class RejectedExplanation(BaseModel):
    product: str = Field(description="The product's name exactly as written under 'Ranked above the pick'")
    explanation: str = Field(description="One sentence: why it wasn't chosen, with the reason and its figures")


class RationaleOutput(BaseModel):
    best_fit: str = Field(description="1-2 sentences: why the recommended product or bundle is the best fit")
    rejected_top_alternatives: list[RejectedExplanation] = Field(
        description="Exactly one entry per product listed under 'Ranked above the pick'; empty if none"
    )
    # Tradeoffs (8.7) aren't written by the model: the card shows the computed ones
    # (steps.tradeoffs), which are always right; a model-written one wasn't, reliably.
    tradeoff: str = ""


Explainer = Callable[[Recommendation], Awaitable["RationaleOutput | str"]]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def output_schema(rec: Recommendation) -> type[BaseModel]:
    """The schema the model fills for this brief: one REQUIRED field per product ranked above the
    pick, so structured output itself guarantees none is missing, extra or misnamed."""
    fields = {
        _slug(name): (str, Field(description=f"One sentence: why {name} wasn't chosen, with its reason and figures"))
        for _, name in rejected_leaders(rec)
    }
    # The description lives on the model (its docstring): OpenAI strict mode rejects a
    # description next to a $ref.
    rejected = create_model("RejectedTopAlternatives", __doc__="Why each product ranked above the pick wasn't chosen", **fields)
    return create_model(
        "Rationale",
        best_fit=(str, RationaleOutput.model_fields["best_fit"]),
        rejected_top_alternatives=(rejected, ...),
    )


def from_schema(obj: dict, rec: Recommendation) -> RationaleOutput:
    """Map the per-brief schema's output back to RationaleOutput (field slug → product name)."""
    by_slug = {_slug(name): name for _, name in rejected_leaders(rec)}
    rejected = obj.get("rejected_top_alternatives") or {}
    return RationaleOutput(
        best_fit=obj.get("best_fit", ""),
        rejected_top_alternatives=[RejectedExplanation(product=by_slug[k], explanation=v) for k, v in rejected.items() if k in by_slug and v],
    )

EXPLAIN_PROMPT = """\
You fill in the explanation part of a media-plan recommendation that a media strategist \
reviews. The facts below were computed from the campaign history, product catalog and \
inventory forecast; they are the decision. Explain it; don't revisit it or add to it.

Critical steps (one per field; tradeoffs are shown separately, from the facts):
1. best_fit (requirement: a rationale explaining why the selected product is the best fit): \
1-2 sentences on why the recommended product or bundle is the best fit for the client's \
primary KPI, given that it can carry the brief. Name it. For a bundle, say why no single \
product could carry the brief and how the budget is split.
2. rejected_top_alternatives (requirement: any rejected top alternatives with explanations): \
it has one field per product listed under "Ranked above the pick"; fill each with one \
sentence giving the reason listed there, with its figures. (No fields when that says None.)

Be exact, not creative:
- Use the figures exactly as given, and only those figures.
- KPI rates come from the vertical's campaign history across all geos; only inventory is per geo.
- Mention no products other than the pick and the ones ranked above it (and a listed bundle).
- Don't mention internal field names."""


def _pct(x: float) -> str:
    return f"{x:.2%}"


def _money(x: float) -> str:
    return f"${x:,.0f}" if float(x).is_integer() else f"${x:,.2f}"


def _n(x: float) -> str:
    return f"{round(x):,}"


def reason_text(r: Rejection) -> str:
    d = r.detail
    if r.code == "INVENTORY_BELOW_BUDGET":
        return f"usable inventory of {_n(d['capacity'])} impressions can't absorb the {_n(d['needed'])} the budget buys (short {_n(d['shortfall'])})"
    if r.code == "BUDGET_BELOW_GOAL":
        return f"the budget buys only {_n(d['affordable'])} impressions at its CPM, below the {_n(d['goal'])} goal"
    if r.code == "INVENTORY_BELOW_GOAL":
        return f"usable inventory of {_n(d['capacity'])} impressions is below the {_n(d['goal'])} goal"
    if r.code == "BELOW_MIN_CONFIDENCE":
        return f"forecast confidence {d['confidence']:.2f} is below the policy minimum of {d['min']:.2f}"
    return r.code


def tradeoff_text(t: Tradeoff, names: dict[str, str], kpi: str) -> str:
    f = t.facts
    if t.code == "KPI_LEADER_NOT_VIABLE":
        return f"The {kpi} leader, {names[f['product_id']]}, can't carry the brief on its own."
    if t.code == "BUNDLE_UPLIFT":
        pair = " + ".join(names[p] for p in f["product_ids"])
        return f"Bundling {pair} would raise blended {kpi} by {f['uplift_pct']}% over the pick ({f['kpi_points']:+.2f} points)."
    if t.code == "NEAR_TIE":
        return f"{names[f['product_id']]} is within {abs(f['kpi_points']):.2f} points of the pick on {kpi}: a near-tie."
    if t.code == "LOW_CONFIDENCE_LINE":
        return f"{names[f['product_id']]}'s forecast confidence is low ({f['confidence']:.2f}), so its delivery is less certain."
    if t.code == "THIN_MARGIN":
        return f"{names[f['product_id']]}'s usable inventory covers the buy with only {f['margin_pct']}% to spare."
    if t.code == "UNSPENT_BUDGET":
        why = {
            "line_limit": f"the bundle is at its {f.get('max_lines', 3)}-product limit, each product at its usable inventory",
            "no_inventory": "no other product has usable inventory left for it",
        }.get(f.get("reason"), "it's below the minimum line spend")
        return f"{_money(f['unspent'])} of the budget stays unspent: {why}."
    if t.code == "GOAL_MARGIN":
        word = "exceeds" if f["margin_pct"] >= 0 else "misses"
        return f"The plan {word} the {_n(f['goal'])} impression goal by {abs(f['margin_pct'])}%."
    if t.code == "NO_VIABLE_PLAN":
        return "No product or small bundle can deliver this brief under the current inventory policy."
    return t.code


def explain_input(rec: Recommendation) -> str:
    """Everything the workflow computed, as labelled markdown the explainer reads."""
    req = rec.requirements
    kpi = KPI_LABELS[req.kpi]
    names = {c.product_id: c.product_name for c in rec.candidates}
    bench = {b.product_id: b for b in rec.benchmarks}
    scale = {s.product_id: s for s in rec.scale}
    in_plan = {line.product_id for line in rec.plan.lines}

    lines = [
        "## Brief",
        f"- Client: {req.client_name or 'not given'}",
        f"- Vertical: {req.vertical}",
        f"- Primary KPI: {kpi}",
        f"- Geo: {req.geo}",
        f"- Budget: {_money(req.budget)}",
        f"- Impression goal: {_n(req.impression_goal) if req.impression_goal else 'none'}",
        "",
        "## Our planning rules (not the client's)",
        f"- Inventory: {rec.policy.get('note', '')}",
        "- Selection: " + (
            "the highest-KPI product that can carry the brief alone; a bundle only when none can"
            if (rec.policy.get("selection") or {}).get("mode", "single_first") == "single_first"
            else "the highest blended KPI, single product or bundle"
        ),
        "",
        f"## Products, ranked by {kpi} (KPI rates: {req.vertical} campaign history, all geos; inventory: {req.geo} forecast)",
        "| Rank | Product | CTR | In-view rate | CPM | Impressions the budget buys | Forecast available | Confidence | Usable inventory | Can carry the brief alone? |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in sorted(rec.candidates, key=lambda c: c.kpi_rank):
        b, s = bench[c.product_id], scale[c.product_id]
        reasons = rec.rejections.get(c.product_id) or []
        alone = "yes" if not reasons else "no: " + "; ".join(reason_text(r) for r in reasons)
        tag = " (recommended)" if c.product_id in in_plan else ""
        lines.append(
            f"| {c.kpi_rank} | {c.product_name}{tag} | {_pct(b.ctr)} | {_pct(b.in_view_rate)} | {_money(c.cpm)} "
            f"| {_n(c.affordable_imps)} | {_n(s.available_imps)} | {s.confidence:.2f} | {_n(s.capacity)} | {alone} |"
        )

    plan = rec.plan
    lines += ["", "## Recommended plan"]
    if plan.kind == "none":
        lines.append("- No viable plan.")
    else:
        lines.append(f"- Type: {'single product' if plan.kind == 'single' else 'bundle'}")
        for line in plan.lines:
            lines.append(f"- {line.product_name}: {_money(line.spend)} spend, {_n(line.impressions)} impressions, {kpi} {_pct(line.kpi_value)}")
        if plan.kind == "bundle":
            lines.append(f"- Total: {_money(plan.total_spend)} spend, {_n(plan.total_impressions)} impressions, blended {kpi} {_pct(plan.blended_kpi)}")
        if plan.unspent:
            lines.append(f"- Unspent budget: {_money(plan.unspent)}")
        if req.impression_goal:
            lines.append(f"- Meets the impression goal: {'yes' if plan.meets_goal else 'no'}")

    # The section the model must cover entry by entry (requirement 9.6); always present.
    above = [a for a in rec.alternatives if a.kind == "rejected_leader"]
    bundle = next((a for a in rec.alternatives if a.kind == "bundle"), None)
    rank = {c.product_id: c.kpi_rank for c in rec.candidates}
    lines += ["", f"## Ranked above the pick on {kpi}, not chosen"]
    lines += [f"- {names[a.product_ids[0]]} (#{rank[a.product_ids[0]]}, {kpi} {_pct(a.kpi_value)}): "
              + "; ".join(reason_text(r) for r in rec.rejections.get(a.product_ids[0], [])) + "." for a in above] or [
        "- None."]
    if bundle:
        sel = (rec.policy.get("selection") or {}).get("mode", "single_first")
        why = ("the selection rule picks a single product whenever one can carry the brief alone"
               if sel == "single_first" and plan.kind == "single" else "see tradeoffs")
        who = " + ".join(names[p] for p in bundle.product_ids)
        lines += ["", "## Bundle not chosen", f"- {who} (blended {kpi} {_pct(bundle.kpi_value)}): not chosen because {why}."]

    if rec.tradeoffs:
        lines += ["", "## Tradeoffs"] + [f"- {tradeoff_text(t, names, kpi)}" for t in rec.tradeoffs]
    return "\n".join(lines)


def rejected_leaders(rec: Recommendation) -> list[tuple[str, str]]:
    """(product_id, name) of every product ranked above the pick, in rank order."""
    names = {c.product_id: c.product_name for c in rec.candidates}
    return [(a.product_ids[0], names[a.product_ids[0]]) for a in rec.alternatives if a.kind == "rejected_leader"]


def template_rejected(rec: Recommendation) -> list[RejectedNote]:
    """The templated explanation for each rejected top alternative (the fallback for 9.6)."""
    kpi = KPI_LABELS[rec.requirements.kpi]
    by_id = {c.product_id: c for c in rec.candidates}
    out = []
    for pid, name in rejected_leaders(rec):
        why = "; ".join(reason_text(r) for r in rec.rejections.get(pid, [])) or "it can't carry the brief alone"
        out.append(RejectedNote(product_id=pid, product_name=name,
                                explanation=f"{name} has a higher {kpi} ({_pct(by_id[pid].kpi_value)}), but {why}."))
    return out


def check(out: RationaleOutput, rec: Recommendation) -> list[str]:
    """What's wrong with a filled-in rationale; empty if it covers the requirements."""
    problems = []
    expected = {name for _, name in rejected_leaders(rec)}
    got = [r.product for r in out.rejected_top_alternatives]
    if missing := sorted(expected - set(got)):
        problems.append(f"rejected_top_alternatives is missing: {', '.join(missing)}")
    if extra := sorted(set(got) - expected):
        problems.append(f"rejected_top_alternatives must not include: {', '.join(extra)} (not ranked above the pick)")
    if len(got) != len(set(got)):
        problems.append("rejected_top_alternatives lists a product more than once")
    for line in rec.plan.lines:
        if line.product_name not in out.best_fit:
            problems.append(f"best_fit must name {line.product_name}")
    return problems


def to_rationale(out: RationaleOutput, rec: Recommendation):
    """Build the Rationale the card renders; any rejected leader the model missed gets the template."""
    from .domain import Rationale

    llm = {r.product: r.explanation for r in out.rejected_top_alternatives}
    templ = {n.product_name: n for n in template_rejected(rec)}
    rejected = [
        RejectedNote(product_id=pid, product_name=name, explanation=llm[name], source="llm")
        if llm.get(name) else templ[name]
        for pid, name in rejected_leaders(rec)
    ]
    text = " ".join(p for p in (out.best_fit.strip(), out.tradeoff.strip()) if p)
    return Rationale(text=text, source="llm", best_fit=out.best_fit.strip(), tradeoff=out.tradeoff.strip() or None, rejected=rejected)


def vel_explainer(model: dict) -> Explainer:
    """An explainer backed by a vel Agent with structured output; one retry if `check` fails."""

    async def run(messages: list[dict], rec: Recommendation) -> RationaleOutput:
        from vel import Agent  # type: ignore

        # Temperature 0: the rationale restates computed facts; no room for creativity.
        agent = Agent(id="workflow-explain", model=model, tools=[], system_prompt=EXPLAIN_PROMPT,
                      output_type=output_schema(rec), generation_config={"temperature": 0})
        async for event in agent.run_stream({"messages": messages}, stateless=True):
            if isinstance(event, dict) and event.get("type") == "data-object-complete":
                return from_schema(event["data"]["object"], rec)
            if isinstance(event, dict) and event.get("type") == "error":
                raise RuntimeError(event.get("errorText") or "explain failed")
        raise RuntimeError("explainer returned no structured output")

    async def explain(rec: Recommendation) -> RationaleOutput:
        messages = [{"role": "user", "content": explain_input(rec)}]
        out = await run(messages, rec)
        if problems := check(out, rec):  # the schema covers the products; this backs up best_fit
            messages += [{"role": "assistant", "content": out.model_dump_json()},
                         {"role": "user", "content": "Fix these and return the full corrected answer: " + "; ".join(problems)}]
            out = await run(messages, rec)
        return out

    return explain
