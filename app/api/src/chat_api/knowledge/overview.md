# What the Media Planner does

A media strategist gets a client brief and has to answer one question: **which of our ad products should this client buy with this budget, and why?** The brief gives the vertical (Retail, Finance, Travel, QSR or Entertainment), the KPI to optimize (CTR or in-view rate), the geo (US, EMEA or APAC), the budget, and sometimes an impression goal.

Doing that by hand means three things:
- **Pull the benchmarks:** how each product has performed on that KPI for that vertical.
- **Check what can run:** whether there's enough forecast inventory in that geo to absorb the budget.
- **Pick and justify:** choose one product or a small bundle, and say why it beat the alternatives.

The Media Planner does this in a chat. A brief returns a recommendation card: the plan, what the budget delivers, the benchmarks and inventory behind it, the higher-ranked products that weren't chosen and why, the tradeoffs, and a short written rationale.

## The principle: the model handles language, code decides and computes

- **The model** reads the brief, asks for anything missing, and writes the explanation.
- **Code** makes every pick and computes every number, from the case data. The same brief always gets the same answer, and the answer can be checked.
- The written rationale is boxed in: the model is given the finished numbers and reasons, must explain each product ranked above the pick, and falls back to template text if it fails.

## What the assistant can do in chat

- Run a recommendation from a brief (missing details are asked for in a pre-filled form).
- Run one step on its own: benchmarks for a vertical, products ranked for a KPI and budget, or the inventory check for a geo and budget.
- Answer questions about the catalog and analyze the case data (campaign history, inventory forecasts, sample briefs).
- Explain how the recommendation works and why it decided what it did.

## The case data

| File | Contents |
|---|---|
| Product catalog | 6 products, with description and CPM |
| Campaign history | 1,200 past line items: impressions, clicks, viewable impressions, per product and vertical (no geo, no dates) |
| Inventory forecast | Forecast impressions and forecast confidence, per product, vertical and geo (no time dimension) |
| Client briefs | 7 sample briefs |

See also: `workflow`, `selection`, `inventory`, `card`.
