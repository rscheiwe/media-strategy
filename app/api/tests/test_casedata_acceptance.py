from __future__ import annotations

import csv
import sqlite3
from collections import defaultdict

import pytest

from chat_api.casedata import build_case_db


def test_case_db_matches_sources_and_benchmarks_are_ratio_of_sums(tmp_path):
    root = __import__("pathlib").Path(__file__).resolve().parents[1] / "case_data"
    db_path = build_case_db(root, tmp_path / "case.db")
    with sqlite3.connect(db_path) as db:
        assert db.execute("select count(*) from products").fetchone()[0] == sum(1 for _ in csv.DictReader((root / "product_catalog.csv").open()))
        assert db.execute("select count(*) from campaign_line_items").fetchone()[0] == sum(1 for _ in csv.DictReader((root / "campaign_history.csv").open()))
        assert db.execute("select count(*) from inventory").fetchone()[0] == sum(1 for _ in csv.DictReader((root / "inventory_forecaster.csv").open()))
        assert db.execute("select count(*) from client_briefs").fetchone()[0] == len(__import__("json").loads((root / "client_briefs.json").read_text()))
        actual = {(row[0], row[1]): row[2:] for row in db.execute("select product_id, vertical, ctr, in_view_rate from benchmarks")}
    totals = defaultdict(lambda: [0, 0, 0])
    for row in csv.DictReader((root / "campaign_history.csv").open()):
        key = row["product_id"], row["vertical"]
        totals[key][0] += int(row["impressions"]); totals[key][1] += int(row["clicks"]); totals[key][2] += int(row["viewable_impressions"])
    assert set(actual) == set(totals)
    for key, (impressions, clicks, viewable) in totals.items():
        assert actual[key][0] == pytest.approx(clicks / impressions, rel=1e-12)
        assert actual[key][1] == pytest.approx(viewable / impressions, rel=1e-12)
