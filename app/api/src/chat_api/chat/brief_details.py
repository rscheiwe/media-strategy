"""The brief's required details and their allowed values, as one prompt section (brief req 7).

Shared by the router prompt (approach B) and the chat prompt, so both read the same
checklist and the same option lists (a "validate you have" checklist, then named AVAILABLE_* lists with the exact values the tools accept). The values
come from workflow/domain.py; the phrasings are what each value covers.
"""

from __future__ import annotations

from chat_api.workflow.domain import GEOS, KPIS, VERTICALS

VERTICAL_PHRASES = {
    "Retail": "retailers, e-commerce, shops and stores, apparel, shoes, fashion, department stores",
    "Finance": "banks, insurance, fintech, credit cards, lending, financial services",
    "Travel": "airlines, hotels, booking sites, cruises, tourism",
    "QSR": "quick-service restaurants, fast food, burger, pizza or chicken chains",
    "Entertainment": "film, cinema, TV, streaming, music, gaming",
}
KPI_PHRASES = {
    "ctr": "click-through rate, CTR, clicks",
    "in_view_rate": "in-view rate, viewability, viewable impressions, visibility, attention",
}
GEO_PHRASES = {
    "US": "United States, USA, America",
    "EMEA": "Europe, UK, Middle East, Africa",
    "APAC": "Asia, Asia-Pacific, Australia, Japan, Southeast Asia",
}

assert set(VERTICAL_PHRASES) == set(VERTICALS) and set(KPI_PHRASES) == set(KPIS) and set(GEO_PHRASES) == set(GEOS)


def _options(name: str, phrases: dict[str, str]) -> str:
    rows = "\n".join(f"  - {value}: {covers}" for value, covers in phrases.items())
    return f"#### {name}\n{rows}"


BRIEF_DETAILS = f"""\
<brief_details>
A recommendation needs these details. Take each from anything the user has said in the \
conversation, not only the latest message. Use exactly one value from the matching list \
below; if a detail isn't stated, or doesn't fit any value, leave it null. Never guess or \
pick a default: the workflow shows the user a form with these options for anything null.

1. client_name (optional): the advertiser or brand as the user wrote it ("Acme Shoes"). A \
name says nothing about the vertical ("Wanderlust Air" is not a vertical).
2. vertical (required): one of AVAILABLE_VERTICALS. Anything else (a mobile app, auto, \
CPG) is null.
3. kpi (required): one of AVAILABLE_KPIS. "Awareness" alone, or both weighted equally, is null.
4. geo (required): one of AVAILABLE_GEOS. Any other region (LATAM, Canada) is null.
5. budget (required): US dollars as a number: "$25k", "25 grand" and "twenty-five \
thousand" are 25000; "$1.2M" is 1200000. A range or a per-month amount is null.
Also impression_goal (optional): only a stated minimum ("at least 800k impressions" is \
800000).

{_options("AVAILABLE_VERTICALS", VERTICAL_PHRASES)}
{_options("AVAILABLE_KPIS", KPI_PHRASES)}
{_options("AVAILABLE_GEOS", GEO_PHRASES)}
</brief_details>"""
