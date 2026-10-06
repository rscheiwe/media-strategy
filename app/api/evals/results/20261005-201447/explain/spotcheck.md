# Explanation spot-check (human audit of the judge)

For each brief: the decision facts, the first sampled text and the judge. Tick the box if you agree with the judge on every criterion.

## Acme

- plan: Premium Takeover
- rejected leader: {'product': 'Commerce Connect', 'reasons': ["the forecast inventory can't absorb the full budget"]}

> Premium Takeover is the best fit for Acme Shoes' CTR goal because, among products that can carry the brief alone, it delivers the highest CTR at 0.91% and can absorb the full $30,000 budget with 937,500 impressions against 940,540 usable inventory. No bundle is needed because the selection rule picks a single product whenever one can carry the brief alone. Commerce Connect wasn't chosen because its usable inventory of 1,427,600 impressions can't absorb the 2,000,000 the budget buys, leaving it short 572,400. Attention Builder wasn't chosen because its usable inventory of 1,481,490 impressions can't absorb the 2,142,857 the budget buys, leaving it short 661,367.

- judge: {"names_pick": true, "leader_reason_correct": true, "mentions_rejected_alternative": true, "tradeoff_direction_correct": true, "no_contradiction": true, "no_invented_product": true, "note": ""}

- [ ] I agree with the judge

## Burger

- plan: Attention Builder
- rejected leader: {'product': 'Social Amplifier', 'reasons': ["the forecast inventory can't absorb the full budget"]}

> Attention Builder is the best fit for Burger Bazaar’s in-view rate goal because it is the highest in-view rate product that can carry the brief alone, delivering 74.96% in-view rate and 1,071,428 impressions on the $15,000 budget. It can absorb the full budget with 1,142,240 usable impressions and meets the 800,000 impression goal. Social Amplifier was not chosen because its usable inventory of 1,295,580 impressions can't absorb the 1,666,666 the budget buys, leaving a shortfall of 371,086 impressions.

- judge: {"names_pick": true, "leader_reason_correct": true, "mentions_rejected_alternative": true, "tradeoff_direction_correct": null, "no_contradiction": true, "no_invented_product": true, "note": ""}

- [ ] I agree with the judge

## CineVerse

- plan: Attention Builder, Premium Takeover
- rejected leader: None

> The best fit is the Attention Builder + Premium Takeover bundle because CTR is the primary KPI and no single product could carry the brief alone: Attention Builder can absorb only 1,112,280 of the 2,857,142 impressions the budget buys, and Premium Takeover can absorb only 706,020 of the 1,250,000 impressions the budget buys. The budget is split as $15,571.92 to Attention Builder and $22,592.64 to Premium Takeover, delivering a blended CTR of 1.11% while carrying the brief with the available inventory.

- judge: {"names_pick": true, "leader_reason_correct": null, "mentions_rejected_alternative": null, "tradeoff_direction_correct": null, "no_contradiction": true, "no_invented_product": true, "note": ""}

- [ ] I agree with the judge

## Stellar

- plan: Commerce Connect
- rejected leader: {'product': 'Premium Takeover', 'reasons': ['the budget buys fewer impressions than the goal at its CPM']}

> Commerce Connect is the best fit for Stellar Bank’s CTR goal because it is the highest-CTR product that can carry the brief alone, delivering 1.10% CTR while the $20,000 budget buys 1,333,333 impressions, which meets the 800,000 impression goal. It can carry the full brief on its own, so no bundle is needed. Premium Takeover was not chosen because, although it ranks higher on CTR at 1.33%, the $20,000 budget buys only 625,000 impressions at its $32 CPM, below the 800,000 goal.

- judge: {"names_pick": true, "leader_reason_correct": true, "mentions_rejected_alternative": true, "tradeoff_direction_correct": null, "no_contradiction": true, "no_invented_product": true, "note": ""}

- [ ] I agree with the judge

## Wanderlust

- plan: Video Spotlight, Attention Builder
- rejected leader: None

> The best fit is the Video Spotlight + Attention Builder bundle because in-view rate is the primary KPI and no single product could carry the brief alone under the $18,000 budget and APAC inventory constraints. The budget is split as $4,050 to Video Spotlight and $13,950 to Attention Builder, delivering 1,221,428 impressions at a blended in-view rate of 78.36% while meeting the 850,000 impression goal.

- judge: {"names_pick": true, "leader_reason_correct": null, "mentions_rejected_alternative": null, "tradeoff_direction_correct": null, "no_contradiction": true, "no_invented_product": true, "note": ""}

- [ ] I agree with the judge

## Brief6+Entertainment

- plan: Premium Takeover
- rejected leader: {'product': 'Attention Builder', 'reasons': ["the forecast inventory can't absorb the full budget"]}

> Premium Takeover is the best fit for the in-view rate KPI because it is the highest-ranked product on in-view rate that can carry the brief alone, delivering a 75.06% in-view rate and supporting the full $25,000 spend for 781,250 impressions. Attention Builder + Video Spotlight was not selected because the rule is to choose a single product whenever one can carry the brief alone, and Premium Takeover can. Attention Builder was not chosen because its usable inventory of 1,365,260 impressions can't absorb the 1,785,714 the budget buys, leaving it short 420,454, despite its 80.32% in-view rate. Video Spotlight was not chosen because its usable inventory of 950,370 impressions can't absorb the 1,388,888 the budget buys, leaving it short 438,518, despite its 78.95% in-view rate.

- judge: {"names_pick": true, "leader_reason_correct": true, "mentions_rejected_alternative": true, "tradeoff_direction_correct": null, "no_contradiction": false, "no_invented_product": false, "note": "no_contradiction/no_invented_product are false because the text names 'Attention Builder + Video Spotlight' as a product, but the FACTS list only individual products and not that bundle as an all_products entry."}

- [ ] I agree with the judge

## Brief7+CTR

- plan: Premium Takeover, Commerce Connect
- rejected leader: None

> The Premium Takeover + Commerce Connect bundle is the best fit for CTR because no single product could carry the brief alone, and these are the two highest-CTR options at 1.33% and 1.10%. The budget is split as $19,667.84 to Premium Takeover and $5,332.16 to Commerce Connect, delivering a blended CTR of 1.24% while carrying the full $25,000 brief.

- judge: {"names_pick": true, "leader_reason_correct": null, "mentions_rejected_alternative": null, "tradeoff_direction_correct": true, "no_contradiction": true, "no_invented_product": true, "note": ""}

- [ ] I agree with the judge
