"""Offline tests for the /keepa/* request schemas.

    python -m pytest keepa/test_schemas.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from keepa import refs, schemas  # noqa: E402
from schema_fields import load_query  # noqa: E402

ALL = [schemas.ProductDetailsSchema, schemas.PriceHistorySchema, schemas.ProductSchema, schemas.InternationalPricesSchema,
       schemas.ProductsSearchSchema, schemas.CategoriesSearchSchema, schemas.DealsListSchema]
VALID = {"product": "B0BN72FYFG", "query": "air fryer"}


def ok(schema, **query):
    data, error = load_query(schema, query)
    assert error is None, error
    return data


def refused(schema, field, **query):
    data, error = load_query(schema, query)
    assert data is None and field in error["errors"], error
    return " ".join(error["errors"][field]) if isinstance(error["errors"][field], list) else str(error["errors"][field])


# ---- product + country ----------------------------------------------------------------------------------------

def test_product_details_defaults():
    assert ok(schemas.ProductDetailsSchema, product="b0bn72fyfg") == {
        "product": "B0BN72FYFG", "country": "US", "refresh": False, "include_buy_box": False}


@pytest.mark.parametrize("product, country", [
    ("https://www.amazon.de/dp/B0BN72FYFG", "DE"),
    ("https://www.amazon.co.uk/Some-Title/dp/B07Z14CY6R?th=1", "GB"),
    ("https://keepa.com/#!product/5-B0BN72FYFG", "JP"),
    ("https://www.amazon.com/dp/B0BN72FYFG", "US"),
])
def test_a_link_names_its_marketplace(product, country):
    data = ok(schemas.ProductDetailsSchema, product=product)
    assert data["country"] == country and len(data["product"]) == 10


def test_an_explicit_country_wins_over_the_link():
    assert ok(schemas.ProductSchema, product="https://www.amazon.de/dp/B0BN72FYFG", country="fr")["country"] == "FR"
    assert ok(schemas.ProductSchema, product="https://www.amazon.de/dp/B0BN72FYFG", country="")["country"] == "DE"


@pytest.mark.parametrize("value, expected", [("us", "US"), ("UK", "GB"), ("gb", "GB"), ("Au", "AU"), ("nl", "NL")])
def test_country(value, expected):
    assert ok(schemas.ProductSchema, product="B0BN72FYFG", country=value)["country"] == expected


def test_country_keepa_does_not_track():
    assert "us, gb, de" in refused(schemas.ProductSchema, "country", product="B0BN72FYFG", country="cn")
    refused(schemas.ProductsSearchSchema, "country", query="x", country="zz")


@pytest.mark.parametrize("value", ["B0BN72FYF", "air fryer", "https://www.amazon.com/s?k=fryer", "https://www.amazon.cn/dp/B0BN72FYFG"])
def test_product_refused(value):
    refused(schemas.ProductSchema, "product", product=value)


def test_product_is_required():
    assert refused(schemas.ProductSchema, "product")
    assert refused(schemas.ProductSchema, "product", product="  ") == "Must not be empty."


def test_flags():
    data = ok(schemas.ProductDetailsSchema, product="B0BN72FYFG", refresh="true", include_buy_box="1")
    assert data["refresh"] is True and data["include_buy_box"] is True
    refused(schemas.ProductDetailsSchema, "refresh", product="B0BN72FYFG", refresh="maybe")


# ---- price history ----------------------------------------------------------------------------------------------

def test_price_history_params():
    assert ok(schemas.PriceHistorySchema, product="B0BN72FYFG") == {"product": "B0BN72FYFG", "country": "US", "price_types": None, "days": None}
    data = ok(schemas.PriceHistorySchema, product="B0BN72FYFG", price_types="Amazon, buy_box,amazon", days="90")
    assert data["price_types"] == ["amazon", "buy_box"] and data["days"] == 90


def test_price_history_refuses_what_the_site_has_not():
    assert "sales_rank" in refused(schemas.PriceHistorySchema, "price_types", product="B0BN72FYFG", price_types="sales_rank")
    refused(schemas.PriceHistorySchema, "days", product="B0BN72FYFG", days="0")
    refused(schemas.PriceHistorySchema, "days", product="B0BN72FYFG", days="abc")


# ---- international prices ---------------------------------------------------------------------------------------

def test_international_prices():
    assert ok(schemas.InternationalPricesSchema, product="B0BN72FYFG") == {"product": "B0BN72FYFG", "countries": None}
    assert ok(schemas.InternationalPricesSchema, product="B0BN72FYFG", countries="us, uk,DE,us")["countries"] == ["US", "GB", "DE"]
    assert "'cn'" in refused(schemas.InternationalPricesSchema, "countries", product="B0BN72FYFG", countries="us,cn")
    refused(schemas.InternationalPricesSchema, "country", product="B0BN72FYFG", country="us")       # an unknown param


# ---- search -----------------------------------------------------------------------------------------------------

def test_search_schemas():
    assert ok(schemas.ProductsSearchSchema, query="  air   fryer ") == {"query": "air fryer", "country": "US"}
    assert ok(schemas.CategoriesSearchSchema, query="kitchen", country="de") == {"query": "kitchen", "country": "DE"}
    refused(schemas.ProductsSearchSchema, "query")
    refused(schemas.ProductsSearchSchema, "query", query="x" * 201)
    refused(schemas.ProductsSearchSchema, "page", query="x", page="2")           # search has one page


# ---- deals ------------------------------------------------------------------------------------------------------

def test_deals_defaults():
    data = ok(schemas.DealsListSchema)
    assert (data["country"], data["page"], data["drop_interval"], data["sort"]) == ("US", 1, "week", "percent")
    assert data["price_type"] is None and data["order"] is None and data["query"] is None
    assert data["categories"] is None and data["min_price"] is None and data["amazon_offer"] is None
    assert all(data[k] is False for k in ("lowest_ever", "lowest_90_days", "lowest_new_offer", "out_of_stock", "has_reviews",
                                         "prime_exclusive", "include_adult", "all_variations"))


def test_deals_filters():
    data = ok(schemas.DealsListSchema, country="uk", page="3", price_type="Buy_Box", drop_interval="3_months", sort="price", order="desc",
              query="lego", categories="1055398, 172282,1055398", exclude_categories="283155", min_drop_percent="20",
              max_drop_percent="80", min_price="5", max_price="400.5", min_rating="4.5", lowest_ever="true", amazon_offer="without")
    assert (data["country"], data["page"], data["price_type"], data["drop_interval"], data["sort"], data["order"]) == \
        ("GB", 3, "buy_box", "3_months", "price", "desc")
    assert data["categories"] == [1055398, 172282] and data["exclude_categories"] == [283155]
    assert (data["min_drop_percent"], data["max_price"], data["min_rating"]) == (20.0, 400.5, 4.5)
    assert data["lowest_ever"] is True and data["amazon_offer"] == "without"


def test_deals_warehouse_conditions():
    data = ok(schemas.DealsListSchema, price_type="warehouse", warehouse_conditions="like_new, good")
    assert data["warehouse_conditions"] == [2, 4]
    assert refused(schemas.DealsListSchema, "warehouse_conditions", warehouse_conditions="like_new") == "Only with price_type=warehouse."
    refused(schemas.DealsListSchema, "warehouse_conditions", price_type="warehouse", warehouse_conditions="mint")


@pytest.mark.parametrize("field, query", [
    ("price_type", {"price_type": "sales_rank"}),            # shown to Keepa Pro only
    ("price_type", {"price_type": "buy_box_used"}),
    ("sort", {"sort": "rating"}),
    ("order", {"order": "up"}),
    ("order", {"sort": "newest", "order": "asc"}),
    ("drop_interval", {"drop_interval": "year"}),
    ("page", {"page": "0"}),
    ("page", {"page": str(refs.DEALS_MAX_PAGE + 1)}),
    ("max_price", {"min_price": "50", "max_price": "10"}),
    ("max_drop_percent", {"min_drop_percent": "60", "max_drop_percent": "40"}),
    ("max_drop_percent", {"max_drop_percent": "120"}),
    ("min_rating", {"min_rating": "6"}),
    ("categories", {"categories": "kitchen"}),
    ("amazon_offer", {"amazon_offer": "yes"}),
    ("prime_exclusive", {"prime_exclusive": "true", "price_type": "new"}),
    ("lowest_ever", {"lowest_ever": "perhaps"}),
    ("brand", {"brand": "lego"}),                            # an attribute filter of Keepa Pro: not a param
])
def test_deals_refuses(field, query):
    refused(schemas.DealsListSchema, field, **query)


def test_prime_exclusive_is_checked_against_the_marketplace_s_default_type():
    assert ok(schemas.DealsListSchema, prime_exclusive="true")["prime_exclusive"] is True             # amazon is the default type
    assert ok(schemas.DealsListSchema, country="in", price_type="amazon", prime_exclusive="true")["price_type"] == "amazon"
    # on amazon.in an absent price_type means new (api._deals)
    assert refused(schemas.DealsListSchema, "prime_exclusive", country="in", prime_exclusive="true") == "Only with price_type=amazon."


def test_deals_last_page_is_accepted():
    assert ok(schemas.DealsListSchema, page=str(refs.DEALS_MAX_PAGE))["page"] == refs.DEALS_MAX_PAGE


# ---- every schema -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("schema_cls", ALL)
def test_every_field_is_described(schema_cls):
    for name, field in schema_cls().fields.items():
        assert field.metadata.get("description"), f"{schema_cls.__name__}.{name}"


@pytest.mark.parametrize("schema_cls", ALL)
def test_an_empty_optional_param_is_the_absent_param(schema_cls):
    fields = {field.data_key or name: field for name, field in schema_cls().fields.items()}
    base = {key: VALID[key] for key, field in fields.items() if field.required}
    expected, error = load_query(schema_cls, base)
    assert error is None
    for key, field in fields.items():
        data, error = load_query(schema_cls, dict(base, **{key: ""}))      # raises (= a 500) on a crash
        if field.required:
            assert data is None and key in error["errors"]
        else:
            assert error is None and data == expected, key
    everything = {key: "  " for key in fields if key not in base}
    assert load_query(schema_cls, dict(base, **everything)) == (expected, None)


@pytest.mark.parametrize("schema_cls", ALL)
def test_unknown_params_are_refused(schema_cls):
    base = {key: VALID[key] for key, field in schema_cls().fields.items() if field.required}
    data, error = load_query(schema_cls, dict(base, nonsense="1"))
    assert data is None and "nonsense" in error["errors"]
