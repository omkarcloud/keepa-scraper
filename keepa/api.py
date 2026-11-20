"""The /keepa/* endpoint functions (route_glue's route_table calls them with
the schema's loaded kwargs).

Upstream calls, one frame each on the site's WebSocket (keepa/fetch.py):

    products/details               the open product frame, no price series
      refresh=true                 the same frame with refreshProduct, on a cleared token
      include_buy_box=true         plus one search by ASIN (cleared), cached on its own
    products/price-history         the open product frame with the price series
    products/variations            the open variations frame (twice when the
                                   ASIN is a variation: its parent is asked next)
    products/international-prices  the open product frame once per marketplace
    products/search                service/search productBasic (cleared)
    deals/list                     service/deals (cleared)
    categories/search              service/search category (cleared)

Every endpoint caches its own answer here (cache.cached_call, TTLs in
cache_config.py), so the routes are registered without cache=.
"""
import math
from datetime import datetime, timedelta, timezone

from cache import cached_call
from keepa import cache_config as ttl
from keepa import fetch, parsers, refs
from keepa.fetch import KeepaNotFound

SEARCH_PAGE_SIZE = 20     # the site's own (50 on amazon.com.au); one page, no next


def _not_found(asin, country):
    return KeepaNotFound(f"Keepa has no product {asin} on amazon.{refs.marketplace(country)['tld']}.")


# ---- products/details -----------------------------------------------------------------------------------------

def _record(product, country, refresh=False):
    answer = fetch.fetch_product(product, refs.marketplace(country)["domain_id"], history=False, refresh=refresh)
    kind, rows = parsers.product_rows(answer)
    if kind == "family":
        return parsers.parse_parent(rows, product, country)
    if kind is None or not parsers.is_found(rows[0]):
        raise _not_found(product, country)
    return parsers.parse_product(rows[0], country)


def _market(product, country):
    """The offer-side blocks of one product: its row in a search by ASIN."""
    rows = parsers.as_list(fetch.fetch_search(product, refs.marketplace(country)["domain_id"]).get("searchProducts"))
    row = next((r for r in rows if isinstance(r, dict) and r.get("asin") == product), None)
    return parsers.parse_market(row, country) if row else None


def _cached_record(product, country, refresh=False):
    """One cache row per product and marketplace; refresh=true overwrites it with what Keepa just read from Amazon."""
    return cached_call("keepa.product", {"product": product, "country": country},
                       lambda **key: _record(refresh=refresh, **key), ttl.PRODUCT_CACHE, refresh=refresh)


def product_details(product, country, refresh=False, include_buy_box=False):
    """The product as Keepa knows it now: identity, pictures, every current
    price, the all-time lows, offer counts, its variation list. With
    include_buy_box the buy box, stock, sales-rank drops, coupons and deal
    badges are filled in too (they are null otherwise)."""
    key = {"product": product, "country": country}
    record = dict(_cached_record(product, country, refresh))
    if include_buy_box and not record["is_variation_parent"]:
        market = cached_call("keepa.market", key, _market, ttl.MARKET_CACHE, refresh=refresh)
        if market:
            record.update(market)
    return record


# ---- products/price-history -----------------------------------------------------------------------------------

def _history(product, country):
    # The details record first (cached, one small frame): a parent ASIN would answer the price series of its
    # whole family here (11 MB for a family of 205), only to be refused.
    kind = "family" if _cached_record(product, country)["is_variation_parent"] else None
    rows = []
    if kind is None:
        kind, rows = parsers.product_rows(fetch.fetch_product(product, refs.marketplace(country)["domain_id"], history=True))
    if kind == "family":
        raise fetch.KeepaBadRequest(
            f"{product} is a variation parent: it has no prices of its own. Ask for one of its variations "
            "(/keepa/products/variations lists them).")
    if kind is None or not parsers.is_found(rows[0]):
        raise _not_found(product, country)
    record = parsers.parse_product(rows[0], country)
    return {"asin": record["asin"], "title": record["title"], "link": record["link"], "amazon_link": record["amazon_link"],
            "country": country, "currency": record["currency"], "last_updated": record["last_updated"],
            "series": parsers.parse_history(rows[0], country)}


def product_price_history(product, country, price_types=None, days=None):
    """Every price series Keepa holds for the product: Amazon, new, used,
    list price, buy box, FBA, FBM, warehouse, lightning deals, used by
    condition, offer counts, rating. `price_types` picks series, `days`
    cuts each to its last N days (the full series is cached once and cut
    per request)."""
    full = cached_call("keepa.history", {"product": product, "country": country}, _history, ttl.HISTORY_CACHE)
    series = full["series"]
    if price_types:
        series = [s for s in series if s["price_type"] in price_types]
    if days:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        series = parsers.cut_series(series, since)
    return {**{k: v for k, v in full.items() if k != "series"}, "days": days, "series_count": len(series), "series": series}


# ---- products/variations ---------------------------------------------------------------------------------------

def _variations(product, country):
    domain_id = refs.marketplace(country)["domain_id"]
    kind, rows = parsers.product_rows(fetch.fetch_variations(product, domain_id))
    parent = None
    if kind == "family":
        parent = next((r.get("parentAsin") for r in rows if r.get("parentAsin")), product)
    elif kind == "product" and parsers.is_found(rows[0]):
        parent = rows[0].get("parentAsin") if rows[0].get("parentAsin") != product else None
        rows = []
        if parent:
            kind, rows = parsers.product_rows(fetch.fetch_variations(parent, domain_id))
            if kind != "family":
                rows = []
    else:
        raise _not_found(product, country)
    # the family answer lists the parent itself too, as a row with no values
    attributes = parsers.family_attributes(rows)
    variations = [parsers.parse_variation(row, country, attributes.get(row.get("asin")))
                  for row in rows if row.get("asin") and row.get("asin") != parent]
    return {"asin": product, "parent_asin": parent, "country": country, "currency": refs.marketplace(country)["currency"],
            "variations_count": len(variations), "variations": variations}


def product_variations(**data):
    """Every variation of the product's family (colours, sizes …) with its
    current prices, rating and review-count history. `product` may be any
    member or the parent; a product without variations answers an empty list."""
    return cached_call("keepa.variations", data, _variations, ttl.VARIATIONS_CACHE)


# ---- products/international-prices -----------------------------------------------------------------------------

def _international(product, countries):
    answers = fetch.fetch_products(product, [refs.marketplace(c)["domain_id"] for c in countries])
    marketplaces = []
    for country, answer in zip(countries, answers):
        kind, rows = parsers.product_rows(answer)
        market = refs.marketplace(country)
        entry = {"country": country, "currency": market["currency"], "is_listed": False, "asin": product,
                 "link": refs.keepa_link(product, country), "amazon_link": refs.amazon_link(product, country),
                 "title": None, "rating": None, "rating_count": None, "prices": None, "offer_counts": None, "last_updated": None}
        if kind == "product" and parsers.is_found(rows[0]):
            record = parsers.parse_product(rows[0], country)
            entry.update(is_listed=True, **{k: record[k] for k in ("title", "rating", "rating_count", "prices", "offer_counts", "last_updated")})
        elif kind == "family":
            entry.update(is_listed=True)
        marketplaces.append(entry)
    listed = [m for m in marketplaces if m["is_listed"]]
    if not listed:
        raise KeepaNotFound(f"Keepa has no product {product} on any of: {', '.join(c.lower() for c in countries)}.")
    return {"asin": product, "listed_count": len(listed), "marketplaces": marketplaces}


def product_international_prices(product, countries=None):
    """The same ASIN on every Amazon marketplace: where it is listed and its
    current prices there, each in the marketplace's own currency."""
    data = {"product": product, "countries": list(countries or refs.COUNTRIES)}
    return cached_call("keepa.international", data, _international, ttl.INTERNATIONAL_CACHE)


# ---- products/search -------------------------------------------------------------------------------------------

def _search(query, country):
    rows = parsers.parse_search(fetch.fetch_search(query, refs.marketplace(country)["domain_id"]), country)
    return {"query": query, "country": country, "currency": refs.marketplace(country)["currency"], "count": len(rows), "results": rows}


def products_search(**data):
    """Keepa's product search: keywords, or an ASIN / UPC / EAN (which
    answers its one product). One page of up to 20 rows; each row carries
    current prices, the buy box, offer counts, stock and sales-rank drops."""
    return cached_call("keepa.search", data, _search, ttl.SEARCH_CACHE)


# ---- deals/list ------------------------------------------------------------------------------------------------

def _minor(value, country):
    return int(round(value * refs.marketplace(country)["scale"]))


def deal_request(country, page, price_type, drop_interval, sort, order, query, categories, exclude_categories,
                 min_drop_percent, max_drop_percent, min_drop, max_drop, min_price, max_price, min_rating,
                 lowest_ever, lowest_90_days, lowest_new_offer, out_of_stock, has_reviews, prime_exclusive,
                 amazon_offer, include_adult, all_variations, warehouse_conditions):
    """The validated params -> the dealRequest keys that differ from the
    site's unfiltered form (fetch.deals_frame holds that form). Ranges and
    checkbox filters each have a master switch on the page; it goes on only
    when one of them is asked for."""
    request = {"page": page - 1, "domainId": str(refs.marketplace(country)["domain_id"]),
               "priceTypes": [refs.DEAL_PRICE_TYPES[price_type]], "dateRange": refs.DEAL_INTERVALS[drop_interval],
               "sortType": refs.deal_sort_type(sort, order), "titleSearch": query or "",
               "includeCategories": list(categories or []), "excludeCategories": list(exclude_categories or [])}
    ranges = (min_drop_percent, max_drop_percent, min_drop, max_drop, min_price, max_price, min_rating)
    if any(v is not None for v in ranges):
        request.update(
            isRangeEnabled=True,
            deltaPercentRange=[int(round(min_drop_percent or 0)), int(round(max_drop_percent if max_drop_percent is not None else 100))],
            deltaRange=[_minor(min_drop or 0, country), _minor(max_drop, country) if max_drop is not None else refs.NO_LIMIT],
            currentRange=[_minor(min_price or 0, country), _minor(max_price, country) if max_price is not None else refs.NO_LIMIT],
            minRating=int(round(min_rating * 10)) if min_rating is not None else -1)
    checks = {"isLowest": lowest_ever, "isLowest90": lowest_90_days, "isLowestOffer": lowest_new_offer, "isOutOfStock": out_of_stock,
              "hasReviews": has_reviews, "isPrimeExclusive": prime_exclusive,
              "mustHaveAmazonOffer": amazon_offer == "with", "mustNotHaveAmazonOffer": amazon_offer == "without"}
    if any(checks.values()) or include_adult or all_variations:
        request.update(isFilterEnabled=True, filterErotic=not include_adult, singleVariation=not all_variations,
                       **{key: bool(value) for key, value in checks.items()})
    if warehouse_conditions:
        request["warehouseConditions"] = sorted(warehouse_conditions)
    return request


def _deals(**data):
    country, page = data["country"], data["page"]
    price_type = data["price_type"] = data["price_type"] or ("new" if country == "IN" else "amazon")
    answer = fetch.fetch_deals(deal_request(**data))
    rows, total, categories = parsers.parse_deals(answer, country, price_type, data["drop_interval"])
    reachable = min(total, refs.DEALS_PER_PAGE * (refs.DEALS_MAX_PAGE - 1) + 100) if total is not None else None
    return {
        "country": country, "currency": refs.marketplace(country)["currency"], "price_type": price_type,
        "drop_interval": data["drop_interval"],
        "results": rows,
        "categories": categories,
        "pagination": {
            "page": page,
            "items_per_page": refs.DEALS_PER_PAGE,
            "total_pages": math.ceil(reachable / refs.DEALS_PER_PAGE) if reachable is not None else None,
            "total_count": total,
            # a page past the end carries no total: there is nothing after it
            **({"has_next": False} if total is None else {}),
        },
    }


def deals_list(**data):
    """Keepa's deals: products whose price just dropped, 150 a page, with
    every filter and sort of the site's own deals page, and the number of
    deals per root category."""
    return cached_call("keepa.deals", data, _deals, ttl.DEALS_CACHE)


# ---- categories/search -----------------------------------------------------------------------------------------

def _categories(query, country):
    rows = parsers.parse_categories(fetch.fetch_categories(query, refs.marketplace(country)["domain_id"]))
    return {"query": query, "country": country, "count": len(rows), "results": rows}


def categories_search(**data):
    """Amazon categories by name, with their ids (the `categories` filter of
    /keepa/deals/list), parents, children and product counts."""
    return cached_call("keepa.categories", data, _categories, ttl.CATEGORIES_CACHE)
