"""
chat.tools — Vel ToolSpec definitions for the chat agent.

- `list_products`: the product catalog (case.db's `products`, built from product_catalog.csv).
- `get_case_data` + `run_python_analysis`: fetch raw case data, then analyse it in Python
  (see `chat.analysis`).
- `web_search`: Linkup web search, offered only when
  LINKUP_API_KEY is set.

Handler signature: (input: dict, ctx: dict) -> dict
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from vel.tools.registry import ToolSpec  # type: ignore

from chat_api.casedata import connect_readonly, ensure_case_db, source_dir
from chat_api.chat.analysis import GET_CASE_DATA, RUN_PYTHON_ANALYSIS

# ---------------------------------------------------------------------------
# Web search (Linkup)
# ---------------------------------------------------------------------------
#
# API verified against docs.linkup.so:
#   POST https://api.linkup.so/v1/search
#   Authorization: Bearer <LINKUP_API_KEY>
#   required: q, depth, outputType

LINKUP_SEARCH_URL = "https://api.linkup.so/v1/search"

# `deep` iterates and can run well past a default client timeout, so this is
# generous on purpose. It still bounds the call rather than hanging the turn.
_LINKUP_TIMEOUT_SECONDS = 120.0


def build_linkup_body(inp: dict) -> dict:
    """
    Map our model-facing parameters onto Linkup's wire format.

    Our name is `query` because that is what reads well in a tool schema;
    Linkup's field is `q`. `depth` and `outputType` are required by the API
    rather than defaulted, so they are always sent.
    """
    body: dict[str, Any] = {
        "q": inp.get("query"),
        "depth": inp.get("depth") or "standard",
        "outputType": inp.get("outputType") or "sourcedAnswer",
    }
    if inp.get("maxResults") is not None:
        body["maxResults"] = inp["maxResults"]
    if inp.get("includeDomains"):
        body["includeDomains"] = inp["includeDomains"]
    if inp.get("excludeDomains"):
        body["excludeDomains"] = inp["excludeDomains"]
    if inp.get("fromDate") is not None:
        body["fromDate"] = inp["fromDate"]
    if inp.get("toDate") is not None:
        body["toDate"] = inp["toDate"]
    return body


async def _web_search(inp: dict, ctx: dict) -> dict:
    """
    The only async handler in this module, and deliberately so.

    Vel invokes tool handlers in-context rather than on a threadpool (see
    `wrap_tools_with_db_scope`), so a synchronous call here would block the
    event loop — and the whole server — for the duration of the search. Every
    other tool is local or DB-bound and returns in milliseconds; this one waits
    on a third party that can take a minute in `deep` mode.

    Being async also means `wrap_tools_with_db_scope` skips it, which is correct:
    it touches no database and so needs no transaction scope.
    """
    import os

    import httpx

    query = str(inp.get("query") or "").strip()
    if not query:
        return {"success": False, "error": "query is required"}

    api_key = (os.getenv("LINKUP_API_KEY") or "").strip()
    if not api_key:
        return {"success": False, "error": "LINKUP_API_KEY is not configured."}

    try:
        async with httpx.AsyncClient(timeout=_LINKUP_TIMEOUT_SECONDS) as client:
            response = await client.post(
                LINKUP_SEARCH_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=build_linkup_body({**inp, "query": query}),
            )
    except httpx.TimeoutException:
        return {"success": False, "error": "Web search timed out."}
    except httpx.HTTPError as exc:
        return {"success": False, "error": f"Web search failed: {exc}"}

    if response.status_code >= 400:
        # Surface the provider's own message — "invalid API key" and "rate
        # limited" need different responses from the model, and a bare status
        # code tells it neither. Bounded so a long HTML error page cannot land
        # in the transcript.
        detail = response.text[:500]
        return {
            "success": False,
            "error": f"Web search failed ({response.status_code}): {detail}",
        }

    try:
        return response.json()
    except ValueError:
        return {"success": False, "error": "Web search returned a non-JSON response."}


WEB_SEARCH = ToolSpec(
    name="web_search",
    description=(
        "Search the live web and return an answer with citations, or raw ranked results. "
        "Use for anything time-sensitive, external, or outside your training data. "
        "Returns sources you can quote and link."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language search query. Be specific; this is not a keyword search.",
            },
            "outputType": {
                "type": "string",
                "enum": ["sourcedAnswer", "searchResults"],
                "description": (
                    "sourcedAnswer (default) returns a synthesised answer plus citations — prefer it "
                    "for factual questions. searchResults returns raw ranked pages with full content — "
                    "use it when you need to read sources yourself. searchResults returns far more text."
                ),
            },
            "depth": {
                "type": "string",
                "enum": ["fast", "standard", "deep"],
                "description": (
                    "fast is sub-second; standard (default) uses agentic search; deep iterates for "
                    "comprehensive coverage and is materially slower and more expensive."
                ),
            },
            "maxResults": {
                "type": "integer",
                "minimum": 1,
                "description": "Cap on results returned.",
            },
            "includeDomains": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'Restrict the search to these domains, e.g. ["arxiv.org"].',
            },
            "excludeDomains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exclude these domains.",
            },
            "fromDate": {
                "type": "string",
                "description": "Only results from this date onward, YYYY-MM-DD.",
            },
            "toDate": {
                "type": "string",
                "description": "Only results up to this date, YYYY-MM-DD.",
            },
        },
        "required": ["query"],
    },
    output_schema={"type": "object"},
    handler=_web_search,
    # Read-only against a third party and touching no session state, so it is
    # safe to run alongside other tools in one batch.
    parallel_safe=True,
    # Offered only when a Linkup key is configured (vel >= 0.5.2 applies `enabled`
    # to tools passed to the agent directly).
    enabled=lambda ctx: bool((os.getenv("LINKUP_API_KEY") or "").strip()),
)


# ---------------------------------------------------------------------------
# Product catalog
# ---------------------------------------------------------------------------

def catalog_path() -> Path:
    """The source product_catalog.csv that case.db's `products` table is built from."""
    return source_dir() / "product_catalog.csv"


def _list_products(inp: dict, ctx: dict) -> dict:
    try:
        with closing(connect_readonly(ensure_case_db())) as db:
            rows = db.execute(
                "SELECT product_id, product_name, product_description, cpm FROM products ORDER BY rowid"
            ).fetchall()
    except (FileNotFoundError, sqlite3.Error) as exc:
        return {"error": f"Product catalog unavailable: {exc}"}
    return {"products": [dict(row) for row in rows]}


LIST_PRODUCTS = ToolSpec(
    name="list_products",
    description=(
        "List the ad products with their id, name, description and CPM (cost per thousand "
        "impressions, USD). Use it whenever the user asks which products exist, what a product "
        "does, or what it costs. Never guess product details or prices."
    ),
    input_schema={"type": "object", "properties": {}},
    output_schema={"type": "object"},
    handler=_list_products,
    parallel_safe=True,
)


def build_tools() -> list[ToolSpec]:
    """Tools for the chat agent. `web_search` is listed but only offered while
    LINKUP_API_KEY is set (its `enabled` check runs when vel builds the schemas)."""
    from .step_tools import STEP_TOOLS

    return [LIST_PRODUCTS, GET_CASE_DATA, RUN_PYTHON_ANALYSIS, *STEP_TOOLS, WEB_SEARCH]
