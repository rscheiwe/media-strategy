"""Prompt variants for the ablation.

V0 current : the app's system prompt as shipped (15 analysis bullets) + recipe with the
             "context" block (baseline elsewhere, line-item consistency).
V1 minimal : role, tools, schema, and two guardrails; recipe without the context block.
V2 fewshot : V1 + two worked examples (Retail / APAC). Benchmarks are per vertical, so the
             example reveals Retail rankings, which brief 1 (Retail US) needs: reported as
             leakage. It also states "video leads in-view", which is true in Retail.
V3 fewshot : V1 + examples moved to Entertainment / APAC, where Attention Builder beats video
             on in-view (leaks brief 3's vertical, reported as such).
V4         : V3 + the single "judge by the vertical's numbers" rule.
V5         : V1 + both examples (opposite in-view leaders) + the geo example.
Numbers in all examples are computed from the real data.

Plumbing stays identical across variants: fetch-then-compute tools, full error messages,
untruncated output, percentages computed in Python, the case-data schema.
"""

from __future__ import annotations

from chat_api.chat.analysis import GET_CASE_DATA, case_data_schema, run_python_analysis_tool
from pathlib import Path

from chat_api.chat.tools import LIST_PRODUCTS, WEB_SEARCH

MINIMAL_PROMPT = """\
You are a media-planning assistant for media strategists. You help pick ad products for a \
client's campaign and answer questions about products, metrics and media planning. Be accurate, \
direct and concise; expand only when the question calls for depth.

Formatting:
- Write in GitHub-flavored Markdown. Use headings, lists and tables when they make an answer \
easier to scan, and plain prose when they would not.

Tools:
- `list_products`: the product catalog with descriptions and CPM.
- For questions that need numbers from the case data, fetch raw rows with `get_case_data`, then \
compute with `run_python_analysis` (pandas over the fetched datasets). `run_python_analysis` can \
also run any other Python the user asks for.
- Only state numbers that appear in a tool's printed output; never fill them in.
- If the data can't answer the question as asked, say what's missing and what you used instead.
- If a tool fails, say so briefly.

Never invent facts, numbers, links or citations. If you don't know, say so.

Case data available through `get_case_data` (columns, filters, meaning):
""" + case_data_schema() + "\n"

FEW_SHOT = """
Examples of good answers. The data is real; reuse the approach, not the numbers.

Example 1
User: For a Retail brand with $20k in APAC, would Commerce Connect be the pick?
Assistant calls get_case_data(campaign_history, vertical=Retail) -> d1, get_case_data(products) -> d2, \
get_case_data(inventory, vertical=Retail, geo=APAC) -> d3, then run_python_analysis over d1-d3. It prints:
    product_name       cpm   ctr    in_view_rate  impressions_bought  available_imps  inventory_coverage  inventory_risk
    Commerce Connect    15   1.23%  69.01%        1,333,333           1,254,000       94.05%              0.80
    Attention Builder   14   1.00%  72.73%        1,428,571           1,161,000       81.27%              0.87
    Premium Takeover    32   0.91%  72.00%          625,000             836,000       133.76%             0.89
    Social Amplifier     9   0.76%  63.84%        2,222,222           1,486,000       66.87%              0.76
    Display Plus        12   0.62%  66.30%        1,666,667           1,068,000       64.08%              0.77
    Video Spotlight     18   0.50%  75.32%        1,111,111             910,000       81.90%              0.93
Assistant answers:
    **For clicks, yes; for viewability, no.** In Retail, Commerce Connect has the highest CTR (1.23%, \
next best Attention Builder at 1.00%), but a mid in-view rate (69.01%). If in-view rate is the KPI, \
Video Spotlight leads (75.32%) at the cost of the lowest CTR (0.50%).
    Scale: the APAC forecast covers about 94% of the 1.33M impressions $20k buys on Commerce Connect, \
at 0.80 forecast confidence, so expect to place a small share elsewhere.
    Which KPI matters more for this client?

Example 2
User: What's Video Spotlight's in-view rate in APAC?
Assistant calls get_case_data(campaign_history, product_id=P002) -> d1, then run_python_analysis over d1. It prints:
    in_view_rate  77.02%   (all verticals, 200 line items)
Assistant answers:
    Campaign history isn't split by geo, so there's no APAC-specific figure. Across all verticals, \
Video Spotlight's in-view rate is **77.02%**. I can break it down by vertical if that helps.
"""

FEWSHOT_PROMPT = MINIMAL_PROMPT + FEW_SHOT


def tools_for(variant: str) -> list:
    context = variant == "V0"
    return [LIST_PRODUCTS, GET_CASE_DATA, run_python_analysis_tool(context_recipe=context), WEB_SEARCH]


# V0 is frozen from before the switch (the app now ships V2).
PROMPTS = {"V0": (Path(__file__).with_name("v0_prompt.txt")).read_text(), "V1": MINIMAL_PROMPT, "V2": FEWSHOT_PROMPT}

FEW_SHOT_ENT = """
Examples of good answers. The data is real; reuse the approach, not the numbers.

Example 1
User: For an Entertainment brand with $20k in APAC, which product looks strongest?
Assistant calls get_case_data(campaign_history, vertical=Entertainment) -> d1, get_case_data(products) -> d2, \
get_case_data(inventory, vertical=Entertainment, geo=APAC) -> d3, then run_python_analysis over d1-d3. It prints:
    product_name       cpm   ctr    in_view_rate  impressions_bought  available_imps  inventory_coverage  inventory_risk
    Attention Builder   14   1.18%  80.32%        1,428,571           1,118,000       78.26%              0.88
    Premium Takeover    32   1.01%  75.06%          625,000             805,000       128.80%             0.89
    Commerce Connect    15   0.92%  70.65%        1,333,333           1,207,000       90.53%              0.73
    Video Spotlight     18   0.84%  78.95%        1,111,111             877,000       78.93%              0.82
    Social Amplifier     9   0.78%  64.75%        2,222,222           1,431,000       64.39%              0.75
    Display Plus        12   0.68%  67.22%        1,666,667           1,029,000       61.74%              0.72
Assistant answers:
    **Attention Builder, on both KPIs.** In Entertainment it has the highest CTR (1.18%) and the highest \
in-view rate (80.32%), ahead of Video Spotlight (78.95%) even though video is the format positioned for \
viewability.
    Scale: the APAC forecast covers about 78% of the 1.43M impressions $20k buys on Attention Builder (0.88 \
forecast confidence), so part of the budget would need a second product.

Example 2
User: What's Video Spotlight's in-view rate in APAC?
Assistant calls get_case_data(campaign_history, product_id=P002) -> d1, then run_python_analysis over d1. It prints:
    in_view_rate  77.02%   (all verticals, 200 line items)
Assistant answers:
    Campaign history isn't split by geo, so there's no APAC-specific figure. Across all verticals, \
Video Spotlight's in-view rate is **77.02%**. I can break it down by vertical if that helps.
"""

VERTICAL_RULE = """
- Judge products by the numbers for the client's vertical, not by catalog descriptions or general \
reputation. A product described as strong on a metric can be behind on it in a given vertical.
"""

# V4 adds the one rule that gave V0 its better KPI reading, placed with the other tool guidance.
_V3 = MINIMAL_PROMPT + FEW_SHOT_ENT
_V4 = _V3.replace("- If a tool fails, say so briefly.\n", "- If a tool fails, say so briefly." + VERTICAL_RULE, 1)
assert _V4 != _V3
PROMPTS.update({"V3": _V3, "V4": _V4})

# Briefs whose vertical a variant's worked example reveals (benchmarks don't depend on geo).
LEAKED = {"V2": {"brief-1"}, "V3": {"brief-3"}, "V4": {"brief-3"}}

# V5: both worked examples, with opposite in-view leaders (video in Retail, Attention Builder in
# Entertainment), so the examples teach "read this vertical's table", not a fixed association.
_RETAIL_EX = FEW_SHOT[FEW_SHOT.index("Example 1"):FEW_SHOT.index("Example 2")].replace("Example 1", "Example A", 1)
_ENT_EX = FEW_SHOT_ENT[FEW_SHOT_ENT.index("Example 1"):FEW_SHOT_ENT.index("Example 2")].replace("Example 1", "Example B", 1)
_GEO_EX = FEW_SHOT[FEW_SHOT.index("Example 2"):].replace("Example 2", "Example C", 1)
PROMPTS["V5"] = (
    MINIMAL_PROMPT
    + "\nExamples of good answers. The data is real; reuse the approach, not the numbers. Note that the "
    "leading product differs by vertical.\n\n"
    + _RETAIL_EX + _ENT_EX + _GEO_EX
)
LEAKED["V5"] = {"brief-1", "brief-3"}

# V6: the structured prompt (sections, explicit tool list
# and routing, metric definitions, mechanics-only examples). This is the app's prompt.
from chat_api.chat.prompts import ASSISTANT_SYSTEM_PROMPT as _APP_PROMPT  # noqa: E402

PROMPTS["V6"] = _APP_PROMPT
LEAKED["V6"] = set()
