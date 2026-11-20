"""Offline tests for the /keepa/* endpoint functions: which frames an
endpoint asks for, what it answers for a parent ASIN, an unknown ASIN and a
page past the end, and how the deals params become the site's dealRequest.
keepa.fetch is replaced by the fixtures; no network, no cache.

    python -m pytest keepa/test_api.py -q
"""
import gzip
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from keepa import api, fetch, refs, schemas  # noqa: E402
from schema_fields import load_query  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    path = os.path.join(FX, name)
    with (gzip.open(path, "rt", encoding="utf-8") if name.endswith(".gz") else open(path, encoding="utf-8")) as f:
        return json.load(f)


PRODUCTS = {
    ("B0CNY1F31S", 1, False): "product_light_B0CNY1F31S_us.json",
    ("B0CNY1F31S", 1, True): "product_history_B0CNY1F31S_us.json",
    ("B07Z14CY6R", 2, False): "product_sparse_B07Z14CY6R_uk.json",
    ("B0HQNZW4FH", 1, False): "product_parent_B0HQNZW4FH.json",
    ("B0ZZZZZZZ9", 1, False): "product_not_found.json",
    ("B0CNY1F31S", 13, False): "product_no_rows_au.json",
}
VARIATIONS = {("B0HQNZW4FH", 1): "variations_parent_B0HQNZW4FH.json", ("B07Z14CY6R", 2): "variations_none_uk.json",
              ("B0CNY1F31S", 1): "product_light_B0CNY1F31S_us.json", ("B0ZZZZZZZ9", 1): "product_not_found.json"}


@pytest.fixture
def calls(monkeypatch):
    log = []
    monkeypatch.setattr(api, "cached_call", lambda namespace, data, fn, ttl, cache=True, refresh=False: fn(**data))

    def product(asin, domain_id, history=False, refresh=False):
        log.append(("product", asin, domain_id, history, refresh))
        return fixture(PRODUCTS.get((asin, domain_id, history), "product_not_found.json"))

    def products(asin, domain_ids):
        log.append(("products", asin, tuple(domain_ids)))
        return [fixture(PRODUCTS.get((asin, d, False), "product_not_found.json")) for d in domain_ids]

    def variations(asin, domain_id):
        log.append(("variations", asin, domain_id))
        return fixture(VARIATIONS[(asin, domain_id)])

    def search(query, domain_id):
        log.append(("search", query, domain_id))
        return fixture({"air fryer": "search_air_fryer_us.json.gz", "B0CNY1F31S": "search_air_fryer_us.json.gz",
                        "B0BN72FYFG": "search_by_asin_B0BN72FYFG.json"}.get(query, "search_no_result.json"))

    def deals(request):
        log.append(("deals", request))
        if request["page"] >= 67:
            return fixture("deals_past_end.json")
        return fixture("deals_no_result.json" if request["titleSearch"] == "zzqq" else "deals_us_page0.json.gz")

    monkeypatch.setattr(fetch, "fetch_product", product)
    monkeypatch.setattr(fetch, "fetch_products", products)
    monkeypatch.setattr(fetch, "fetch_variations", variations)
    monkeypatch.setattr(fetch, "fetch_search", search)
    monkeypatch.setattr(fetch, "fetch_deals", deals)
    monkeypatch.setattr(fetch, "fetch_categories", lambda query, domain_id: fixture(
        "categories_kitchen_us.json" if query == "kitchen" else "categories_no_result.json"))
    return log


def deals_params(**query):
    data, error = load_query(schemas.DealsListSchema, query)
    assert error is None, error
    return data


# ---- products/details -------------------------------------------------------------------------------------------

def test_details_is_one_open_frame(calls):
    record = api.product_details("B0CNY1F31S", "US")
    assert record["title"].startswith("Chefman") and record["buy_box"] is None
    assert calls == [("product", "B0CNY1F31S", 1, False, False)]


def test_details_with_buy_box_adds_one_search_by_asin(calls):
    record = api.product_details("B0CNY1F31S", "US", include_buy_box=True)
    assert calls == [("product", "B0CNY1F31S", 1, False, False), ("search", "B0CNY1F31S", 1)]
    assert record["buy_box"]["price"] == 49.99 and record["stock"] == {"amazon": 3, "buy_box": 3}
    assert record["sales_rank_drops"]["days_30"] == 42 and record["review_count"]
    assert list(record)[:2] == ["asin", "link"] and record["title"].startswith("Chefman")


def test_details_refresh_is_passed_on(calls):
    api.product_details("B07Z14CY6R", "GB", refresh=True)
    assert calls == [("product", "B07Z14CY6R", 2, False, True)]


def test_details_unknown_asin_is_not_found(calls):
    with pytest.raises(fetch.KeepaNotFound) as e:
        api.product_details("B0ZZZZZZZ9", "US")
    assert str(e.value) == "Keepa has no product B0ZZZZZZZ9 on amazon.com."
    with pytest.raises(fetch.KeepaNotFound):
        api.product_details("B0CNY1F31S", "AU")              # an answer with no product at all


def test_details_of_a_parent_asin(calls):
    record = api.product_details("B0HQNZW4FH", "US", include_buy_box=True)
    assert record["is_variation_parent"] is True and record["variations_count"] == 2
    assert [c[0] for c in calls] == ["product"]                # a parent has no offers: no search is spent on it


# ---- products/price-history -------------------------------------------------------------------------------------

def test_history(calls):
    answer = api.product_price_history("B0CNY1F31S", "US")
    assert answer["asin"] == "B0CNY1F31S" and answer["currency"] == "USD" and answer["days"] is None
    assert answer["series_count"] == len(answer["series"]) == 18
    assert list(answer)[-1] == "series"
    assert calls == [("product", "B0CNY1F31S", 1, False, False), ("product", "B0CNY1F31S", 1, True, False)]


def test_history_picks_types_and_cuts_days(calls):
    answer = api.product_price_history("B0CNY1F31S", "US", price_types=["amazon", "buy_box"], days=36000)
    assert [s["price_type"] for s in answer["series"]] == ["amazon", "buy_box"] and answer["series"][0]["points_count"] == 111
    recent = api.product_price_history("B0CNY1F31S", "US", price_types=["amazon"], days=1)
    assert recent["days"] == 1 and 1 <= recent["series"][0]["points_count"] < 111


def test_history_of_a_parent_is_refused_before_its_family_is_fetched(calls):
    with pytest.raises(fetch.KeepaBadRequest) as e:
        api.product_price_history("B0HQNZW4FH", "US")
    assert "variation parent" in str(e.value)
    assert calls == [("product", "B0HQNZW4FH", 1, False, False)]


def test_history_unknown_asin(calls):
    with pytest.raises(fetch.KeepaNotFound):
        api.product_price_history("B0ZZZZZZZ9", "US")


# ---- products/variations ----------------------------------------------------------------------------------------

def test_variations_of_a_parent(calls):
    answer = api.product_variations(product="B0HQNZW4FH", country="US")
    assert answer["parent_asin"] == "B0HQNZW4FH" and answer["variations_count"] == 2
    by = {v["asin"]: v for v in answer["variations"]}
    assert sorted(by) == ["B0CNY1F31S", "B0CNY1YVDD"]                # the parent is not its own variation
    assert by["B0CNY1F31S"]["attributes"] == [{"name": "Capacity", "value": "4 quarts"}]
    assert calls == [("variations", "B0HQNZW4FH", 1)]


def test_variations_of_a_member_asks_its_parent(calls):
    answer = api.product_variations(product="B0CNY1F31S", country="US")
    assert calls == [("variations", "B0CNY1F31S", 1), ("variations", "B0HQNZW4FH", 1)]
    assert answer["asin"] == "B0CNY1F31S" and answer["parent_asin"] == "B0HQNZW4FH" and answer["variations_count"] == 2


def test_variations_of_a_product_without_any(calls):
    answer = api.product_variations(product="B07Z14CY6R", country="GB")
    assert answer["variations"] == [] and answer["parent_asin"] is None and answer["currency"] == "GBP"
    with pytest.raises(fetch.KeepaNotFound):
        api.product_variations(product="B0ZZZZZZZ9", country="US")


# ---- products/international-prices ------------------------------------------------------------------------------

def test_international_prices(calls):
    answer = api.product_international_prices("B0CNY1F31S", ["US", "GB", "AU"])
    assert calls == [("products", "B0CNY1F31S", (1, 2, 13))]
    assert answer["listed_count"] == 1
    us, gb, au = answer["marketplaces"]
    assert us["is_listed"] is True and us["prices"]["amazon"] == 49.99 and us["currency"] == "USD"
    assert gb["is_listed"] is False and gb["prices"] is None and gb["amazon_link"] == "https://www.amazon.co.uk/dp/B0CNY1F31S"
    assert au["is_listed"] is False and set(au) == set(us)


def test_international_prices_default_is_every_marketplace(calls):
    api.product_international_prices("B0CNY1F31S")
    assert calls[0][2] == tuple(m["domain_id"] for m in refs.MARKETPLACES.values())


def test_international_prices_listed_nowhere(calls):
    with pytest.raises(fetch.KeepaNotFound):
        api.product_international_prices("B0ZZZZZZZ9", ["US", "GB"])


# ---- search -----------------------------------------------------------------------------------------------------

def test_search_and_no_result(calls):
    answer = api.products_search(query="air fryer", country="US")
    assert answer["count"] == 20 and answer["results"][0]["asin"] == "B0CNY1F31S"
    assert api.products_search(query="zzqqxx", country="US") == {"query": "zzqqxx", "country": "US", "currency": "USD", "count": 0, "results": []}


def test_categories(calls):
    assert api.categories_search(query="kitchen", country="US")["count"] == 53
    assert api.categories_search(query="zz", country="US")["results"] == []


# ---- deals: params -> the site's dealRequest --------------------------------------------------------------------

def test_deal_request_defaults():
    data = deals_params()
    data["price_type"] = "amazon"
    assert api.deal_request(**data) == {"page": 0, "domainId": "1", "priceTypes": [0], "dateRange": 1, "sortType": 4,
                                        "titleSearch": "", "includeCategories": [], "excludeCategories": []}


def test_deal_request_ranges_switch_the_ranges_on():
    data = deals_params(country="uk", page="3", price_type="buy_box", drop_interval="all", sort="price", order="desc",
                        min_drop_percent="20", max_price="400.5", min_rating="4.5", query="lego", categories="1055398")
    request = api.deal_request(**data)
    assert (request["page"], request["domainId"], request["priceTypes"], request["dateRange"], request["sortType"]) == (2, "2", [18], 4, -5)
    assert request["isRangeEnabled"] is True and request["deltaPercentRange"] == [20, 100]
    assert request["currentRange"] == [0, 40050] and request["deltaRange"] == [0, refs.NO_LIMIT] and request["minRating"] == 45
    assert request["titleSearch"] == "lego" and request["includeCategories"] == [1055398]
    assert "isFilterEnabled" not in request


def test_deal_request_checkboxes_switch_the_filters_on():
    request = api.deal_request(**dict(deals_params(lowest_ever="true", amazon_offer="without", include_adult="true"), price_type="amazon"))
    assert request["isFilterEnabled"] is True and request["isLowest"] is True and request["isLowest90"] is False
    assert request["mustNotHaveAmazonOffer"] is True and request["mustHaveAmazonOffer"] is False
    assert request["filterErotic"] is False and request["singleVariation"] is True
    assert "isRangeEnabled" not in request
    every = api.deal_request(**dict(deals_params(all_variations="true"), price_type="amazon"))
    assert every["isFilterEnabled"] is True and every["singleVariation"] is False and every["filterErotic"] is True


def test_deal_request_yen_and_warehouse():
    request = api.deal_request(**dict(deals_params(country="jp", min_price="1000", max_drop="500"), price_type="amazon"))
    assert request["currentRange"] == [1000, refs.NO_LIMIT] and request["deltaRange"] == [0, 500]
    request = api.deal_request(**deals_params(price_type="warehouse", warehouse_conditions="good,like_new"))
    assert request["priceTypes"] == [9] and request["warehouseConditions"] == [2, 4]


def test_every_deal_request_key_is_one_the_page_sends():
    data = deals_params(price_type="warehouse", warehouse_conditions="new", min_price="1", lowest_90_days="true", exclude_categories="5")
    fetch.deals_frame(api.deal_request(**data))          # raises on a key the page's request does not have


# ---- deals/list -------------------------------------------------------------------------------------------------

def test_deals_page(calls):
    answer = api.deals_list(**deals_params())
    assert answer["price_type"] == "amazon" and answer["drop_interval"] == "week" and len(answer["results"]) == 150
    assert {row["drop_interval"] for row in answer["results"]} == {"week"}        # the rows say the list's interval too
    assert answer["pagination"] == {"page": 1, "items_per_page": 150, "total_pages": 67, "total_count": 108824}
    assert answer["categories"][0]["name"] and calls[0][1]["page"] == 0


def test_deals_default_type_on_amazon_in_is_new(calls):
    assert api.deals_list(**deals_params(country="in"))["price_type"] == "new"
    assert calls[0][1]["priceTypes"] == [1]


def test_deals_small_total_and_no_result(calls):
    answer = api.deals_list(**deals_params(query="zzqq"))
    assert answer["results"] == [] and answer["pagination"]["total_count"] == 0 and answer["pagination"]["total_pages"] == 0


def test_deals_page_past_the_end_is_an_empty_page(calls, monkeypatch):
    monkeypatch.setattr(refs, "DEALS_MAX_PAGE", 200)
    data = dict(deals_params(), page=68)
    answer = api.deals_list(**data)
    assert answer["results"] == [] and answer["pagination"]["has_next"] is False and answer["pagination"]["total_count"] is None
