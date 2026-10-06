-- Case-study data, loaded from the four source files into a read-only SQLite file.
-- It is rebuilt on every API start, so it can't drift from the sources.
--
-- The views define the metrics once. Rates are ratio-of-sums over line items
-- (SUM(clicks) / SUM(impressions)), never the mean of per-line-item rates, so
-- every consumer (the analysis tool, later the recommendation workflow) gets the
-- same number for the same question. eCPC and vCPM follow from the same sums.

CREATE TABLE products (
    product_id          TEXT PRIMARY KEY,
    product_name        TEXT NOT NULL,
    product_description TEXT NOT NULL,
    cpm                 REAL NOT NULL          -- USD per 1,000 impressions
);

CREATE TABLE campaign_line_items (
    line_item_id         INTEGER PRIMARY KEY,  -- 1-based row order in campaign_history.csv
    product_id           TEXT NOT NULL REFERENCES products(product_id),
    vertical             TEXT NOT NULL,
    impressions          INTEGER NOT NULL,
    clicks               INTEGER NOT NULL,
    viewable_impressions INTEGER NOT NULL
);

CREATE TABLE inventory (
    product_id      TEXT NOT NULL REFERENCES products(product_id),
    vertical        TEXT NOT NULL,
    geo             TEXT NOT NULL,
    available_imps  INTEGER NOT NULL,
    inventory_risk  REAL NOT NULL,             -- forecast confidence, 0..1, higher = more confident
    PRIMARY KEY (product_id, vertical, geo)
);

CREATE TABLE client_briefs (
    brief_id INTEGER PRIMARY KEY,              -- 1-based index in client_briefs.json
    text     TEXT NOT NULL
);

CREATE VIEW benchmarks AS
SELECT
    li.product_id,
    p.product_name,
    li.vertical,
    p.cpm,
    COUNT(*)                                              AS line_items,
    SUM(li.impressions)                                   AS impressions,
    SUM(li.clicks)                                        AS clicks,
    SUM(li.viewable_impressions)                          AS viewable_impressions,
    CAST(SUM(li.clicks) AS REAL) / SUM(li.impressions)               AS ctr,
    CAST(SUM(li.viewable_impressions) AS REAL) / SUM(li.impressions) AS in_view_rate,
    -- Efficiency per dollar: eCPC = USD per click; vCPM = USD per 1,000 viewable impressions.
    p.cpm * SUM(li.impressions) / (1000.0 * SUM(li.clicks))               AS ecpc,
    p.cpm * SUM(li.impressions) / CAST(SUM(li.viewable_impressions) AS REAL) AS vcpm
FROM campaign_line_items li
JOIN products p USING (product_id)
GROUP BY li.product_id, li.vertical;

CREATE VIEW benchmarks_by_product AS
SELECT
    li.product_id,
    p.product_name,
    p.cpm,
    COUNT(*)                                              AS line_items,
    SUM(li.impressions)                                   AS impressions,
    SUM(li.clicks)                                        AS clicks,
    SUM(li.viewable_impressions)                          AS viewable_impressions,
    CAST(SUM(li.clicks) AS REAL) / SUM(li.impressions)               AS ctr,
    CAST(SUM(li.viewable_impressions) AS REAL) / SUM(li.impressions) AS in_view_rate,
    -- Efficiency per dollar: eCPC = USD per click; vCPM = USD per 1,000 viewable impressions.
    p.cpm * SUM(li.impressions) / (1000.0 * SUM(li.clicks))               AS ecpc,
    p.cpm * SUM(li.impressions) / CAST(SUM(li.viewable_impressions) AS REAL) AS vcpm
FROM campaign_line_items li
JOIN products p USING (product_id)
GROUP BY li.product_id;

CREATE VIEW inventory_with_products AS
SELECT i.product_id, p.product_name, i.vertical, i.geo, i.available_imps, i.inventory_risk, p.cpm
FROM inventory i
JOIN products p USING (product_id);
