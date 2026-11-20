"""Offline tests for the Keepa parsers and reference resolvers.

    python -m pytest keepa/test_parsers.py -q

Fixtures are real answers of push.keepa.com captured 2026-11-20 as a guest
(keepa/fixtures/, listed in parsers.py's docstring). No network.
"""
import gzip
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from keepa import parsers as P, refs  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    path = os.path.join(FX, name)
    with (gzip.open(path, "rt", encoding="utf-8") if name.endswith(".gz") else open(path, encoding="utf-8")) as f:
        return json.load(f)


def row_of(name):
    kind, rows = P.product_rows(fixture(name))
    assert kind == "product"
    return rows[0]


@pytest.fixture(scope="module")
def product():
    return P.parse_product(row_of("product_light_B0CNY1F31S_us.json"), "US")


@pytest.fixture(scope="module")
def history():
    return P.parse_history(row_of("product_history_B0CNY1F31S_us.json"), "US")


# ---- values ------------------------------------------------------------------------------------------------

def test_keepa_time_is_minutes_since_2011():
    assert P.keepa_time(8355496) == "2026-11-20T10:16:00Z"
    assert P.keepa_time(0) is None and P.keepa_time(-1) is None and P.keepa_time(None) is None and P.keepa_time("x") is None


@pytest.mark.parametrize("value, scale, expected", [
    (4999, 100, 49.99), (5000, 100, 50), (0, 100, 0), (1812, 1, 1812), (-1, 100, None), (-2, 100, None), (None, 100, None), (True, 100, None),
])
def test_money(value, scale, expected):
    assert P.money(value, scale) == expected


def test_slot_value_reads_each_kind():
    assert P.slot_value(0, 4999, 100) == 49.99            # a price
    assert P.slot_value(11, 12, 100) == 12                # a count is never divided
    assert P.slot_value(16, 46, 100) == 4.6               # a rating is times ten
    assert P.slot_value(16, 0, 100) is None and P.slot_value(17, -2, 100) is None


def test_every_published_slot_has_one_name():
    assert len(set(refs.PRICE_TYPES)) == len(refs.SLOTS)
    assert refs.SALES_RANK_SLOT not in refs.SLOTS and 15 not in refs.SLOTS
    assert set(refs.DEAL_PRICE_TYPES) <= set(refs.PRICE_TYPES)
    assert all(refs.SLOT_OF[name] == slot for name, slot in refs.DEAL_PRICE_TYPES.items())


# ---- products/details -----------------------------------------------------------------------------------------

def test_product_identity(product):
    assert product["asin"] == "B0CNY1F31S"
    assert product["link"] == "https://keepa.com/#!product/1-B0CNY1F31S"
    assert product["amazon_link"] == "https://www.amazon.com/dp/B0CNY1F31S"
    assert (product["country"], product["currency"]) == ("US", "USD")
    assert product["title"].startswith("Chefman Air Fryer")
    assert product["product_type"] == "standard" and product["is_variation_parent"] is False
    assert product["manufacturer"] == "Chefman" and product["parent_asin"] == "B0HQNZW4FH"
    assert product["image"] == "https://m.media-amazon.com/images/I/71Ld+1SBc5L.jpg"
    assert product["images"][0] == {"link": product["image"], "width": 2241, "height": 2241,
                                    "thumbnail": "https://m.media-amazon.com/images/I/41sufOtQorL.jpg", "variant": "MAIN"}
    assert len(product["images"]) == 7
    assert product["category_ids"] == [17659096011]
    assert product["eans"] == ["0810087847654"] and product["upcs"] == ["810087847654"] and product["gtins"] == ["60810087847656"]


def test_product_current_values(product):
    assert product["rating"] == 4.6 and product["rating_count"] == 7607
    prices = product["prices"]
    assert (prices["amazon"], prices["new"], prices["used"], prices["list_price"], prices["buy_box"]) == (49.99, 49.99, 43.99, 99.99, 49.99)
    assert prices["warehouse"] == 43.99 and prices["used_like_new"] == 45.49
    assert prices["new_fba"] is None and prices["lightning_deal"] is None        # -1: no offer
    assert prices["buy_box_used"] is None                                        # -2: not shown to a guest
    assert set(prices) == {name for name, kind, _s in refs.SLOTS.values() if kind == refs.PRICE}
    assert product["offer_counts"] == {"new": 1, "used": 12, "refurbished": None, "collectible": None, "new_fba": None, "new_fbm": None}


def test_product_lows_and_stock_share(product):
    assert product["lowest_prices"]["amazon"] == {"price": 49.97, "date": "2025-12-26T18:44:00Z"}
    assert product["lowest_prices"]["collectible"] is None
    assert product["out_of_stock_percent_90_days"]["amazon"] == 3
    assert product["out_of_stock_percent_90_days"]["collectible"] is None       # -1


def test_product_variations_and_dates(product):
    assert product["variations_count"] == 2
    assert product["variations"][0] == {"asin": "B0CNY1F31S", "attributes": [{"name": "Capacity", "value": "4 quarts"}], "image": None}
    assert product["tracking_since"] == "2024-05-22T21:26:00Z"      # (7042886 + 21564000) * 60
    assert product["last_updated"] == "2026-11-20T11:19:00Z"
    assert product["last_price_change"] and product["last_price_change"] <= product["last_updated"]


def test_product_market_blocks_are_present_and_null(product):
    assert all(key in product and product[key] is None for key in P.EMPTY_MARKET)


def test_sparse_product_uk():
    record = P.parse_product(row_of("product_sparse_B07Z14CY6R_uk.json"), "GB")
    assert record["currency"] == "GBP" and record["amazon_link"] == "https://www.amazon.co.uk/dp/B07Z14CY6R"
    assert record["link"] == "https://keepa.com/#!product/2-B07Z14CY6R"
    assert record["parent_asin"] is None and record["variations"] == [] and record["variations_count"] == 0
    assert record["item_highlights"]
    assert record["prices"]["amazon"] == 12.42 and record["prices"]["used"] is None


def test_not_found_placeholder():
    kind, rows = P.product_rows(fixture("product_not_found.json"))
    assert kind == "product" and rows[0]["productType"] == 4
    assert P.is_found(rows[0]) is False
    assert P.is_found(row_of("product_light_B0CNY1F31S_us.json")) is True


def test_no_rows_at_all():
    assert P.product_rows(fixture("product_no_rows_au.json")) == (None, [])
    assert P.product_rows(None) == (None, []) and P.product_rows({"basicProducts": [None]}) == (None, [])
    assert P.product_rows(fixture("check_human.json")) == (None, [])


def test_parent_asin_answers_its_family():
    kind, rows = P.product_rows(fixture("product_parent_B0HQNZW4FH.json"))
    assert kind == "family" and len(rows) == 3
    record = P.parse_parent(rows, "B0HQNZW4FH", "US")
    assert record["asin"] == "B0HQNZW4FH" and record["is_variation_parent"] is True and record["product_type"] == "variation_parent"
    assert record["title"].startswith("Chefman Air Fryer") and record["prices"]["amazon"] is None       # the parent's own row
    assert record["images"] and record["image"] == record["images"][0]["link"]
    assert [v["asin"] for v in record["variations"]] == ["B0CNY1YVDD", "B0CNY1F31S"]      # the parent is not its own variation
    assert record["variations"][0]["attributes"] == [{"name": "Capacity", "value": "6 quarts"}]
    assert record["variations"][0]["image"].startswith(refs.IMAGE_BASE)
    assert record["variations_count"] == 2 and record["last_updated"]
    assert set(record) == set(P.parse_product(row_of("product_light_B0CNY1F31S_us.json"), "US"))


# ---- products/price-history -----------------------------------------------------------------------------------

def test_history_series(history):
    names = [s["price_type"] for s in history]
    assert names == ["amazon", "new", "used", "list_price", "new_fbm", "lightning_deal", "warehouse", "new_fba", "new_offer_count",
                     "used_offer_count", "rating", "rating_count", "buy_box", "used_like_new", "used_very_good", "used_good",
                     "used_acceptable", "prime_exclusive"]
    by = {s["price_type"]: s for s in history}
    assert by["amazon"]["kind"] == "price" and by["amazon"]["has_shipping"] is False and by["amazon"]["points_count"] == 111
    assert by["amazon"]["points"][-1] == {"date": "2026-11-15T23:26:00Z", "price": 49.99}
    assert by["warehouse"]["points_count"] == 939


def test_history_series_with_shipping_are_triples(history):
    by = {s["price_type"]: s for s in history}
    assert by["buy_box"]["has_shipping"] is True and by["buy_box"]["points_count"] == 150
    assert by["buy_box"]["points"][-1] == {"date": "2026-11-15T23:26:00Z", "price": 49.99, "shipping": 0}
    assert by["new_fbm"]["points"][-1] == {"date": "2026-11-19T16:04:00Z", "price": None, "shipping": None}     # the offer ended
    raw = row_of("product_history_B0CNY1F31S_us.json")["csv"]
    for slot, (_name, _kind, with_shipping) in refs.SLOTS.items():
        if raw[slot]:
            assert len(raw[slot]) % (3 if with_shipping else 2) == 0, slot


def test_history_counts_and_rating(history):
    by = {s["price_type"]: s for s in history}
    assert by["rating"]["kind"] == "rating" and by["rating"]["points"] == [{"date": "2026-11-06T13:58:00Z", "value": 4.6}]
    assert by["rating_count"]["kind"] == "count" and by["rating_count"]["points"][-1]["value"] == 7607


def test_history_leaves_out_what_a_guest_is_not_shown(history):
    names = {s["price_type"] for s in history}
    assert "buy_box_used" not in names        # its only value is -2
    assert not ({"sales_rank"} & names)
    for series in history:
        dates = [p["date"] for p in series["points"]]
        assert dates == sorted(dates)


def test_history_of_a_row_without_series():
    assert P.parse_history(row_of("product_light_B0CNY1F31S_us.json"), "US") == []
    assert P.parse_history({}, "US") == []


def test_cut_series_keeps_the_value_the_window_opens_with(history):
    amazon = [s for s in history if s["price_type"] == "amazon"]
    cut = P.cut_series(amazon, "2026-10-20T00:00:00Z")[0]
    assert cut["points_count"] == len(cut["points"]) == 4
    assert cut["points"][0]["date"] < "2026-10-20T00:00:00Z" <= cut["points"][1]["date"]
    assert P.cut_series(amazon, "2030-01-01T00:00:00Z")[0]["points"] == amazon[0]["points"][-1:]
    assert P.cut_series(amazon, "2000-01-01T00:00:00Z")[0]["points"] == amazon[0]["points"]


# ---- products/variations --------------------------------------------------------------------------------------

def test_variation_rows():
    kind, rows = P.product_rows(fixture("variations_parent_B0HQNZW4FH.json"))
    assert kind == "family"
    assert sorted(r["asin"] for r in rows) == ["B0CNY1F31S", "B0CNY1YVDD", "B0HQNZW4FH"]     # the family lists its parent too
    by = {r["asin"]: r for r in rows}                    # the site's order changes from answer to answer
    attributes = P.family_attributes(rows)
    assert attributes == {"B0CNY1F31S": [{"name": "Capacity", "value": "4 quarts"}], "B0CNY1YVDD": [{"name": "Capacity", "value": "6 quarts"}]}
    assert P.parse_variation(by["B0CNY1YVDD"], "US")["attributes"] == []
    first = P.parse_variation(by["B0CNY1F31S"], "US", attributes["B0CNY1F31S"])
    assert first["attributes"] == [{"name": "Capacity", "value": "4 quarts"}]
    assert first["asin"] == "B0CNY1F31S" and first["color"] == "Black" and first["size"] == "4 Quart - No Window"
    assert first["title"].startswith("Chefman") and first["category_group"] == "Kitchen"
    assert first["prices"]["amazon"] == 49.99 and first["rating"] == 4.6 and first["rating_count"] == 7607
    assert first["review_count"] and first["review_count"] < first["rating_count"]
    assert first["rating_count_history"][0]["value"] == 0
    assert first["variation_rating_count"] == first["rating_count_history"][-1]["value"] == 349          # its own; rating_count is the family's
    assert first["review_count_history"][-1]["value"] == first["review_count"]
    assert first["image"].startswith(refs.IMAGE_BASE) and first["images"]
    assert first["lowest_prices"]["amazon"]["price"] and first["last_updated"]


def test_product_without_variations():
    kind, rows = P.product_rows(fixture("variations_none_uk.json"))
    assert kind == "product" and P.is_found(rows[0]) and not rows[0].get("parentAsin")


# ---- products/search ------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def search():
    return P.parse_search(fixture("search_air_fryer_us.json.gz"), "US")


def test_search_rows(search):
    assert len(search) == 20
    row = search[0]
    assert row["asin"] == "B0CNY1F31S" and row["parent_asin"] == "B0HQNZW4FH" and row["parent_title"]
    assert row["color"] == "Black" and row["size"] == "4 Quart - No Window"
    assert row["materials"] == ["Metal", "Plastic"] and len(row["special_features"]) == 5
    assert row["unit_count"] == {"type": "Count", "value": 1.0}
    assert row["category_group"] == "Kitchen" and row["root_category_id"] == 1055398 and row["sales_rank_category_id"] == 284507
    assert row["rating"] == 4.6 and row["rating_count"] == 7607 and row["review_count"]
    assert row["prices"]["amazon"] == 49.99 and row["offer_counts"]["new"] == 1
    assert row["referral_fee_percent"] == 15.0
    assert row["is_adult_product"] is False and row["has_reviews"] is True and row["is_eligible_for_super_saver_shipping"] is True


def test_search_row_buy_box_and_stock(search):
    row = search[0]
    box = row["buy_box"]
    assert box["price"] == 49.99 and box["shipping"] == 0 and box["condition"] == "new"
    assert box["is_amazon"] is True and box["is_fba"] is True and box["is_prime_eligible"] is True
    assert (box["saving_basis"], box["saving_basis_type"], box["saving_percent"]) == (99.99, "list_price", 50)
    assert box["last_updated"] == "2026-11-20T10:16:00Z"
    assert row["buy_box_used"] == {"price": 44.99, "shipping": 0, "seller_id": "A2L77EE7U53NWQ", "condition": "used_very_good", "is_fba": True}
    assert row["stock"] == {"amazon": 3, "buy_box": 3}
    assert row["total_offer_count"] == 13 and row["fba_offer_count"] == 1 and row["fbm_offer_count"] == 0
    assert row["sales_rank_drops"] == {"days_30": 42, "days_90": 108, "days_180": 216, "days_365": 469}
    assert row["amazon_out_of_stock_count"] == {"days_30": 0, "days_90": 1}
    assert row["amazon_availability"] == "in_stock"
    assert "list_price" in row["lowest_ever_price_types"] and "amazon" in row["lowest_90_days_price_types"]


def test_search_row_coupons_and_badges(search):
    row = search[0]
    assert row["coupon_history"][0] == {"date": row["coupon_history"][0]["date"], "amount": 10, "percent": None,
                                        "subscribe_and_save_amount": None, "subscribe_and_save_percent": None}
    assert row["coupon"] is None                                # the last entry ends the coupon
    assert row["deal_badges"] == [{"type": "special_event_sale", "badge": "Black Friday Deal", "access": "all"}]
    stores = [r["brand_store"] for r in search if r["brand_store"]]
    assert stores and stores[0]["link"].startswith("https://www.amazon.com/stores/")


def test_search_rows_keep_one_shape(search):
    keys = set(search[0])
    assert all(set(row) == keys for row in search)
    assert set(P.EMPTY_MARKET) <= keys
    assert set(P.parse_market(fixture("search_by_asin_B0BN72FYFG.json")["searchProducts"][0], "US")) == set(P.EMPTY_MARKET)


def test_search_by_asin_and_no_result():
    rows = P.parse_search(fixture("search_by_asin_B0BN72FYFG.json"), "US")
    assert len(rows) == 1 and rows[0]["asin"] == "B0BN72FYFG"
    box = rows[0]["buy_box"]                    # a third-party buy box: the seller's id is not shown to a guest
    assert box["price"] == 298.78 and box["is_amazon"] is False and box["seller_id"] is None and box["condition"] is None
    assert rows[0]["buy_box_used"] is None and rows[0]["stock"] == {"amazon": None, "buy_box": 23}
    assert P.parse_search(fixture("search_no_result.json"), "US") == []
    assert P.parse_search({}, "US") == []


# ---- deals/list -----------------------------------------------------------------------------------------------

def test_deals_page():
    rows, total, categories = P.parse_deals(fixture("deals_us_page0.json.gz"), "US", "amazon", "week")
    assert len(rows) == 150 and total == 108824
    row = rows[0]
    assert row["asin"] == "B0F7J6589M" and row["title"] == "AG Jeans Women's Ex-Boyfriend Mid Rise Slouchy Short"
    assert row["image"] == "https://m.media-amazon.com/images/I/31hd2uR5qqL.jpg"
    assert row["price_type"] == "amazon" and row["price"] == 10.7
    assert row["drops"][1] == {"interval": "week", "average_price": 53.6, "drop": 42.9, "drop_percent": 80}
    assert [d["interval"] for d in row["drops"]] == ["day", "week", "month", "3_months"]
    assert row["drop_interval"] == "week" and row["warehouse_condition"] is None
    assert row["prices"]["list_price"] == 178 and row["category_ids"] == [2348585011, 1048186]
    assert row["deal_found_at"] and row["price_since"]
    assert "graph_price" not in row
    assert all(set(r) == set(row) for r in rows)


def test_deal_rows_carry_the_interval_the_list_was_asked_for():
    answer = fixture("deals_us_page0.json.gz")          # a week list: the site sends dateIntervalIndex 0 on every row
    assert {r["dateIntervalIndex"] for r in answer["deals"]["drBasic"]} == {0}
    rows, _total, _ = P.parse_deals(answer, "US", "amazon", "week")
    assert {r["drop_interval"] for r in rows} == {"week"}
    assert any(r["drops"][0]["drop_percent"] == 0 for r in rows)          # rows with no drop in the day: not "day" deals
    raw = dict(answer["deals"]["drBasic"][0], dateIntervalIndex=2)
    assert P.parse_deal(raw, "US", "amazon", "3_months")["drop_interval"] == "3_months"
    assert P.parse_deal(raw, "US", "amazon", "all")["drop_interval"] == "month"       # every interval: the row's own index


def test_deals_category_counts():
    _rows, _total, categories = P.parse_deals(fixture("deals_us_page0.json.gz"), "US", "amazon")
    assert {"id": 165796011, "name": "Baby Products", "deals_count": 745} in categories
    assert all(c["name"] != "?" for c in categories)            # the bucket of deals without a root category


def test_deals_of_the_warehouse_type():
    rows, total, _ = P.parse_deals(fixture("deals_warehouse_like_new.json.gz"), "US", "warehouse")
    assert rows and total
    assert {r["warehouse_condition"] for r in rows} == {"like_new"}
    assert all(r["price"] is not None for r in rows)


def test_deals_in_yen_are_whole_units():
    rows, _total, _ = P.parse_deals(fixture("deals_jp_page0.json.gz"), "JP", "amazon")
    assert rows[0]["currency"] == "JPY" and rows[0]["price"] == 1812 and rows[0]["prices"]["list_price"] == 16600
    assert rows[0]["amazon_link"].startswith("https://www.amazon.co.jp/dp/")


def test_deals_last_page_past_end_and_no_result():
    rows, total, _ = P.parse_deals(fixture("deals_last_page.json.gz"), "US", "amazon")
    assert len(rows) == 100 and total > 10000
    assert P.parse_deals(fixture("deals_past_end.json"), "US", "amazon") == ([], None, [])
    assert P.parse_deals(fixture("deals_no_result.json"), "US", "amazon") == ([], 0, [])


# ---- categories/search ----------------------------------------------------------------------------------------

def test_categories():
    rows = P.parse_categories(fixture("categories_kitchen_us.json"))
    assert len(rows) == 53
    assert rows[0] == {"id": 1055398, "name": "Home & Kitchen", "parent_id": None, "children_ids": [13900821, 1063498, 13900811, 258599011],
                       "children_count": 4, "product_count": 111365840, "is_browse_node": True, "is_match": True}
    matched = [r["is_match"] for r in rows]
    assert matched == sorted(matched, reverse=True) and sum(matched) == 22
    assert P.parse_categories(fixture("categories_no_result.json")) == []


# ---- refs -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("B0BN72FYFG", "B0BN72FYFG"), (" b0bn72fyfg ", "B0BN72FYFG"), ("0306406152", "0306406152"),
    ("https://www.amazon.com/dp/B0BN72FYFG", "B0BN72FYFG"),
    ("https://www.amazon.de/Apple-iPhone/dp/B0BN72FYFG/ref=sr_1_1?th=1", "B0BN72FYFG"),
    ("amazon.co.uk/gp/product/B07Z14CY6R", "B07Z14CY6R"),
    ("https://keepa.com/#!product/1-B0BN72FYFG", "B0BN72FYFG"),
    ("https://keepa.com/product/3-B0BN72FYFG", "B0BN72FYFG"),
])
def test_resolve_product(value, expected):
    assert refs.resolve_product(value) == expected


@pytest.mark.parametrize("value", ["", "B0BN72FYF", "ABCDEFGHIJ", "hello world", "https://www.amazon.com/s?k=air+fryer",
                                   "https://www.ebay.com/dp/B0BN72FYFG", "https://www.amazon.cn/dp/B0BN72FYFG",
                                   "https://keepa.com/#!product/7-B0BN72FYFG", "https://keepa.com/#!deals"])
def test_resolve_product_refuses(value):
    with pytest.raises(ValueError):
        refs.resolve_product(value)


@pytest.mark.parametrize("value, expected", [
    ("https://www.amazon.de/dp/B0BN72FYFG", "DE"), ("https://www.amazon.co.uk/dp/B07Z14CY6R", "GB"),
    ("https://www.amazon.com.au/dp/B07PQDHBJG", "AU"), ("https://www.amazon.com/dp/B0BN72FYFG", "US"),
    ("https://keepa.com/#!product/5-B0BN72FYFG", "JP"), ("B0BN72FYFG", None), ("", None), ("https://keepa.com/#!deals", None),
])
def test_country_of(value, expected):
    assert refs.country_of(value) == expected


def test_country_codes():
    assert refs.country("us") == "US" and refs.country("UK") == "GB" and refs.country(" gb ") == "GB"
    for bad in ("cn", "xx", ""):
        with pytest.raises(ValueError):
            refs.country(bad)
    assert len(refs.COUNTRIES) == 13 and len({m["domain_id"] for m in refs.MARKETPLACES.values()}) == 13
    assert 7 not in {m["domain_id"] for m in refs.MARKETPLACES.values()}


def test_deal_sort_type():
    assert refs.deal_sort_type("percent", None) == 4 and refs.deal_sort_type("percent", "asc") == -4
    assert refs.deal_sort_type("price", None) == 5 and refs.deal_sort_type("price", "desc") == -5
    assert refs.deal_sort_type("drop", "asc") == -2 and refs.deal_sort_type("sales_rank", "desc") == -3
    assert refs.deal_sort_type("newest", "desc") == 1
    with pytest.raises(ValueError):
        refs.deal_sort_type("newest", "asc")
