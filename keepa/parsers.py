"""Keepa answers (the decoded WebSocket frames) -> clean dicts for /keepa/*.

Pure functions, no I/O. Fixtures (keepa/fixtures/, captured 2026-11-20 from
push.keepa.com as a guest; raw answers, as the site sent them):

    product_light_B0CNY1F31S_us.json      the details call (no price series)
    product_history_B0CNY1F31S_us.json    the price-history call (24 series)
    product_sparse_B07Z14CY6R_uk.json     a product with 11 short series
    product_not_found.json                an unknown ASIN: productType 4, no title
    product_parent_B0HQNZW4FH.json        a parent ASIN: variationProducts
    product_no_rows_au.json               status 200 and no product at all
    variations_parent_B0HQNZW4FH.json     every variation of a parent
    variations_none_uk.json               a product that has no variations
    search_air_fryer_us.json.gz           20 search rows
    search_by_asin_B0BN72FYFG.json        an ASIN typed into search: 1 row
    search_no_result.json                 no row
    deals_us_page0.json.gz                150 deals, the category counts
    deals_warehouse_like_new.json.gz      deals of the warehouse type
    deals_last_page.json.gz               the last page (100 rows)
    deals_past_end.json                   a page past the end
    deals_no_result.json                  a filter nothing matches
    categories_kitchen_us.json            53 categories
    categories_no_result.json             none

Units, as the site sends them:
  * a price is in the currency's minor unit (cents; whole yen on amazon.co.jp)
  * -1 = no offer / out of stock, -2 = no data (or not shown to a guest): both null here
  * a rating is times ten (46 = 4.6)
  * a time is in "Keepa minutes": (minutes + 21564000) * 60 = unix seconds
  * a series is a flat list: (time, value) pairs, or (time, price, shipping)
    triples for the types refs.SLOTS marks "with shipping"
"""
from keepa import refs
from scraper_utils.dates import epoch_utc
from scraper_utils.text import clean
from scraper_utils.values import amount, as_dict, as_list, g

KEEPA_EPOCH_MINUTES = 21564000


def keepa_time(minutes):
    """Keepa minutes -> '2026-11-20T10:16:00Z'; 0, negatives and junk -> None."""
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes <= 0:
        return None
    return epoch_utc((int(minutes) + KEEPA_EPOCH_MINUTES) * 60)


def money(value, scale):
    """A price in minor units -> the amount in the currency's unit; -1 / -2 / junk -> None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value) if scale == 1 else amount(value / scale)


def slot_value(slot, value, scale):
    """One value of a 36-slot list as its type reads: a price, a count or a rating."""
    kind = refs.SLOTS[slot][1]
    if kind == refs.PRICE:
        return money(value, scale)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    if kind == refs.RATING:
        return round(value / 10, 1) if value > 0 else None
    return int(value)


def _slots(values, scale, kinds):
    """{public name: value} for every published slot of the given kinds; the key is always there."""
    values = as_list(values)
    return {name: (slot_value(slot, values[slot], scale) if slot < len(values) else None)
            for slot, (name, kind, _shipping) in refs.SLOTS.items() if kind in kinds}


_COUNT_KEYS = {"new_offer_count": "new", "used_offer_count": "used", "refurbished_offer_count": "refurbished",
               "collectible_offer_count": "collectible", "new_fba_offer_count": "new_fba", "new_fbm_offer_count": "new_fbm"}


def _current(values, scale):
    """A `current` list -> (prices, offer_counts, rating, rating_count)."""
    values = as_list(values)
    prices = _slots(values, scale, (refs.PRICE,))
    counts = _slots(values, scale, (refs.COUNT,))
    offer_counts = {short: counts[name] for name, short in _COUNT_KEYS.items()}
    rating = slot_value(16, values[16], scale) if len(values) > 16 else None
    return prices, offer_counts, rating, counts["rating_count"]


def image_link(name):
    name = clean(name)
    return refs.IMAGE_BASE + name if name else None


def _images(row):
    """The row's pictures: [{link, width, height, thumbnail, variant}]; `link` is the large file (its size beside it), `thumbnail` the 500 px one."""
    out = []
    for image in as_list(row.get("images")):
        image = as_dict(image)
        large = image.get("l") or image.get("m")
        if not large:
            continue
        is_large = bool(image.get("l"))
        out.append({"link": image_link(large), "width": image.get("lW" if is_large else "mW"),
                    "height": image.get("lH" if is_large else "mH"), "thumbnail": image_link(image.get("m")),
                    "variant": image.get("variant")})
    return out


def _identity(asin, country):
    market = refs.marketplace(country)
    return {"asin": asin, "link": refs.keepa_link(asin, country), "amazon_link": refs.amazon_link(asin, country),
            "country": country, "currency": market["currency"]}


def _codes(row):
    return {"eans": as_list(row.get("eanList")), "upcs": as_list(row.get("upcList")), "gtins": as_list(row.get("gtinList"))}


def _dated_lows(values, scale):
    """stats.min -> {price type: {price, date}}: the lowest value Keepa ever saw and when."""
    values = as_list(values)
    out = {}
    for slot, (name, kind, _shipping) in refs.SLOTS.items():
        if kind != refs.PRICE:
            continue
        pair = values[slot] if slot < len(values) else None
        if isinstance(pair, list) and len(pair) == 2 and money(pair[1], scale) is not None:
            out[name] = {"price": money(pair[1], scale), "date": keepa_time(pair[0])}
        else:
            out[name] = None
    return out


def _percents(values):
    values = as_list(values)
    out = {}
    for slot, (name, kind, _shipping) in refs.SLOTS.items():
        if kind == refs.PRICE:
            value = values[slot] if slot < len(values) else None
            out[name] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    return out


def _attributes(variation):
    return [{"name": clean(a.get("dimension")), "value": clean(a.get("value"))}
            for a in as_list(as_dict(variation).get("attributes")) if isinstance(a, dict)]


# ---- the product answer ---------------------------------------------------------------------------------------

def product_rows(answer):
    """(kind, rows) of a product answer: ("product", [row]) for an ASIN,
    ("family", rows) when the ASIN is a variation parent, (None, []) when
    the site has nothing at all for it."""
    answer = as_dict(answer)
    family = [r for r in as_list(answer.get("variationProducts")) if isinstance(r, dict)]
    if family:
        return "family", family
    rows = [r for r in as_list(answer.get("basicProducts") or answer.get("products")) if isinstance(r, dict)]
    return ("product", rows[:1]) if rows else (None, [])


def is_found(row):
    """False for the placeholder the site answers for an ASIN Amazon does not list on this marketplace."""
    row = as_dict(row)
    return row.get("productType") not in refs.NOT_FOUND_TYPES and bool(clean(row.get("title")))


def parse_product(row, country):
    """One product row (the details call) -> the record. The blocks an
    opt-in fills (`buy_box` …) are present and null."""
    scale = refs.marketplace(country)["scale"]
    stats = as_dict(row.get("stats"))
    prices, offer_counts, rating, rating_count = _current(stats.get("current"), scale)
    images = _images(row)
    variations = [{"asin": v.get("asin"), "attributes": _attributes(v), "image": image_link(v.get("image"))}
                  for v in as_list(row.get("variations")) if isinstance(v, dict) and v.get("asin")]
    asin = row.get("asin")
    return {
        **_identity(asin, country),
        "title": clean(row.get("title")),
        "product_type": refs.PRODUCT_TYPES.get(row.get("productType")),
        "is_variation_parent": False,
        "manufacturer": clean(row.get("manufacturer")),
        "parent_asin": row.get("parentAsin") if row.get("parentAsin") != asin else None,
        "item_highlights": clean(row.get("itemHighlights")),
        "image": images[0]["link"] if images else None,
        "images": images,
        "category_ids": as_list(row.get("categories")),
        **_codes(row),
        "rating": rating,
        "rating_count": rating_count,
        "prices": prices,
        "offer_counts": offer_counts,
        "lowest_prices": _dated_lows(stats.get("min"), scale),
        "out_of_stock_percent_90_days": _percents(stats.get("outOfStockPercentage90")),
        "variations": variations,
        "variations_count": len(variations),
        **EMPTY_MARKET,
        "tracking_since": keepa_time(row.get("trackingSince")),
        "last_price_change": keepa_time(row.get("lastPriceChange")),
        "last_updated": keepa_time(row.get("lastUpdate")),
    }


def family_attributes(rows):
    """{asin: [{name, value}]} from the parent's own row of a family answer:
    what tells each variation apart (Capacity: 4 quarts)."""
    out = {}
    for row in rows:
        for variation in as_list(row.get("variations")):
            if isinstance(variation, dict) and variation.get("asin"):
                out[variation["asin"]] = _attributes(variation)
    return out


def parse_parent(rows, asin, country):
    """A parent ASIN (its answer is the family: every variation, then the
    parent's own row) -> the same record shape. The parent has a title and
    pictures but no prices of its own; its variations carry them."""
    own = next((r for r in rows if r.get("asin") == asin), {})
    members = [r for r in rows if r.get("asin") and r.get("asin") != asin]
    attributes = family_attributes(rows)
    variations = []
    for row in members:
        fallback = [{"name": name, "value": clean(row.get(key))} for name, key in (("Color", "color"), ("Size", "size"))
                    if clean(row.get(key))]
        images = _images(row)
        variations.append({"asin": row.get("asin"), "attributes": attributes.get(row.get("asin")) or fallback,
                           "image": image_link(row.get("swatchImage") or row.get("imagesCSV")) or (images[0]["link"] if images else None)})
    images = _images(own)
    empty = {name: None for name, kind, _s in refs.SLOTS.values() if kind == refs.PRICE}
    return {
        **_identity(asin, country),
        "title": clean(own.get("title")),
        "product_type": refs.PRODUCT_TYPES[5],
        "is_variation_parent": True,
        "manufacturer": clean(own.get("manufacturer")),
        "parent_asin": None,
        "item_highlights": clean(own.get("itemHighlights")),
        "image": images[0]["link"] if images else (variations[0]["image"] if variations else None),
        "images": images,
        "category_ids": as_list((own or (rows[0] if rows else {})).get("categories")),
        "eans": [], "upcs": [], "gtins": [],
        "rating": None,
        "rating_count": None,
        "prices": dict(empty),
        "offer_counts": {short: None for short in _COUNT_KEYS.values()},
        "lowest_prices": dict(empty),
        "out_of_stock_percent_90_days": dict(empty),
        "variations": variations,
        "variations_count": len(variations),
        **EMPTY_MARKET,
        "tracking_since": keepa_time(own.get("trackingSince")),
        "last_price_change": None,
        "last_updated": max((keepa_time(r.get("lastUpdate")) or "" for r in rows), default="") or None,
    }


# ---- price history --------------------------------------------------------------------------------------------

def _points(flat, slot, scale):
    """A flat series -> its points, oldest first."""
    _name, kind, with_shipping = refs.SLOTS[slot]
    step = 3 if with_shipping else 2
    points = []
    for i in range(0, len(flat) - step + 1, step):
        date = keepa_time(flat[i])
        if date is None:
            continue
        if kind == refs.PRICE:
            point = {"date": date, "price": money(flat[i + 1], scale)}
            if with_shipping:
                point["shipping"] = money(flat[i + 2], scale)
        else:
            point = {"date": date, "value": slot_value(slot, flat[i + 1], scale)}
        points.append(point)
    return points


def parse_history(row, country):
    """The `csv` of a product row -> [{price_type, kind, has_shipping,
    points_count, points}], in the site's own order. A point is the value
    FROM its date on (a null price = no offer from then). A type with no
    series, or whose every value is hidden from a guest, is left out."""
    scale = refs.marketplace(country)["scale"]
    csv = as_list(row.get("csv"))
    series = []
    for slot, (name, kind, with_shipping) in refs.SLOTS.items():
        flat = csv[slot] if slot < len(csv) else None
        if not isinstance(flat, list) or not flat:
            continue
        points = _points(flat, slot, scale)
        value_key = "price" if kind == refs.PRICE else "value"
        if not any(p[value_key] is not None for p in points):
            continue
        series.append({"price_type": name, "kind": kind, "has_shipping": with_shipping,
                       "points_count": len(points), "points": points})
    return series


def cut_series(series, since):
    """Each series from `since` (an ISO time) on, plus its last point before
    that: the value the window opens with."""
    out = []
    for s in series:
        before = [p for p in s["points"] if p["date"] < since]
        points = before[-1:] + [p for p in s["points"] if p["date"] >= since]
        out.append(dict(s, points_count=len(points), points=points))
    return out


# ---- variations -------------------------------------------------------------------------------------------------

def _pairs(flat):
    flat = as_list(flat)
    return [{"date": keepa_time(flat[i]), "value": flat[i + 1] if isinstance(flat[i + 1], int) and flat[i + 1] >= 0 else None}
            for i in range(0, len(flat) - 1, 2) if keepa_time(flat[i])]


def _last_value(flat):
    pairs = _pairs(flat)
    return pairs[-1]["value"] if pairs else None


def parse_variation(row, country, attributes=None):
    """One row of a family -> the variation with its current values.
    `rating_count` is the family's (7,607 on both rows of
    variations_parent_B0HQNZW4FH); the rating-count history is the
    variation's own (349 and 141 there). The review-count history is passed
    on as the site keeps it (1,077 and 959 there)."""
    scale = refs.marketplace(country)["scale"]
    stats = as_dict(row.get("stats"))
    prices, offer_counts, rating, rating_count = _current(stats.get("current"), scale)
    reviews = as_dict(row.get("reviews"))
    images = _images(row)
    return {
        **_identity(row.get("asin"), country),
        "title": clean(row.get("title")),
        "attributes": attributes or [],
        "color": clean(row.get("color")),
        "size": clean(row.get("size")),
        "image": images[0]["link"] if images else image_link(row.get("imagesCSV")),
        "swatch_image": image_link(row.get("swatchImage")),
        "images": images,
        "category_group": clean(row.get("websiteDisplayGroupName")),
        **_codes(row),
        "rating": rating,
        "rating_count": rating_count,                                              # the whole family's, as Amazon shows it
        "variation_rating_count": _last_value(reviews.get("ratingCount")),         # this variation's own
        "review_count": _last_value(reviews.get("reviewCount")),
        "prices": prices,
        "offer_counts": offer_counts,
        "lowest_prices": _dated_lows(stats.get("min"), scale),
        "out_of_stock_percent_90_days": _percents(stats.get("outOfStockPercentage90")),
        "rating_count_history": _pairs(reviews.get("ratingCount")),
        "review_count_history": _pairs(reviews.get("reviewCount")),
        "tracking_since": keepa_time(row.get("trackingSince")),
        "last_updated": keepa_time(row.get("lastUpdate")),
    }


# ---- search rows --------------------------------------------------------------------------------------------------
# The numbers Amazon's offer data uses (Keepa's own documentation of its product object).
CONDITIONS = {1: "new", 2: "used_like_new", 3: "used_very_good", 4: "used_good", 5: "used_acceptable", 6: "refurbished",
              7: "collectible_like_new", 8: "collectible_very_good", 9: "collectible_good", 10: "collectible_acceptable"}
AMAZON_AVAILABILITY = {-1: "no_amazon_offer", 0: "in_stock", 1: "preorder", 2: "unknown", 3: "backorder", 4: "delayed"}


def _flagged(flags):
    flags = as_list(flags)
    return [name for slot, (name, _kind, _s) in refs.SLOTS.items() if slot < len(flags) and flags[slot] is True]


def _coupons(flat, scale):
    """couponHistory (time, one-time coupon, Subscribe & Save coupon triples;
    a positive number is an amount in minor units, a negative one a percent)
    -> [{date, amount, percent, subscribe_and_save_amount, subscribe_and_save_percent}]."""
    flat = as_list(flat)
    out = []
    for i in range(0, len(flat) - 2, 3):
        one_time, subscribe = flat[i + 1], flat[i + 2]
        out.append({"date": keepa_time(flat[i]),
                    "amount": money(one_time, scale) if one_time and one_time > 0 else None,
                    "percent": -one_time if one_time and one_time < 0 else None,
                    "subscribe_and_save_amount": money(subscribe, scale) if subscribe and subscribe > 0 else None,
                    "subscribe_and_save_percent": -subscribe if subscribe and subscribe < 0 else None})
    return out


def _non_negative(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def parse_market(row, country):
    """The offer-side blocks only a search row carries: the buy box, stock,
    sales-rank drops, coupons, deal badges. These are the keys the details
    endpoint fills when asked (EMPTY_MARKET is their null form)."""
    scale = refs.marketplace(country)["scale"]
    stats = as_dict(row.get("stats"))
    coupons = _coupons(row.get("couponHistory"), scale)
    live_coupon = coupons[-1] if coupons and any(coupons[-1][k] for k in coupons[-1] if k != "date") else None
    shipping_time = as_list(stats.get("buyBoxShippingTime"))
    has_buy_box = money(stats.get("buyBoxPrice"), scale) is not None
    buy_box = {
        "price": money(stats.get("buyBoxPrice"), scale),
        "shipping": money(stats.get("buyBoxShipping"), scale),
        "seller_id": stats.get("buyBoxSellerId"),
        "condition": CONDITIONS.get(stats.get("buyBoxCondition")),
        "is_amazon": stats.get("buyBoxIsAmazon"),
        "is_fba": stats.get("buyBoxIsFBA"),
        "is_prime_eligible": stats.get("buyBoxIsPrimeEligible"),
        "is_prime_exclusive": stats.get("buyBoxIsPrimeExclusive"),
        "is_free_shipping_eligible": stats.get("buyBoxIsFreeShippingEligible"),
        "is_shippable": stats.get("buyBoxIsShippable"),
        "is_preorder": stats.get("buyBoxIsPreorder"),
        "is_backorder": stats.get("buyBoxIsBackorder"),
        "is_warehouse_deal": stats.get("buyBoxIsWarehouseDeal"),
        "is_price_hidden_until_cart": stats.get("buyBoxIsMAP"),
        "is_unqualified": stats.get("buyBoxIsUnqualified"),
        "includes_import_fees": stats.get("buyBoxPriceIncludesImportFees"),
        "availability_message": clean(stats.get("buyBoxAvailabilityMessage")),
        "shipping_country": stats.get("buyBoxShippingCountry"),
        "shipping_days_min": _non_negative(shipping_time[0]) if len(shipping_time) == 2 else None,
        "shipping_days_max": _non_negative(shipping_time[1]) if len(shipping_time) == 2 else None,
        "saving_basis": money(stats.get("buyBoxSavingBasis"), scale),
        "saving_basis_type": (stats.get("buyBoxSavingBasisType") or "").lower() or None,
        "saving_percent": _non_negative(stats.get("buyBoxSavingPercentage")),
        "last_updated": keepa_time(stats.get("lastBuyBoxUpdate")),
    } if has_buy_box else None
    used_price = money(stats.get("buyBoxUsedPrice"), scale)
    buy_box_used = {
        "price": used_price,
        "shipping": money(stats.get("buyBoxUsedShipping"), scale),
        "seller_id": stats.get("buyBoxUsedSellerId"),
        "condition": CONDITIONS.get(stats.get("buyBoxUsedCondition")),
        "is_fba": stats.get("buyBoxUsedIsFBA"),
    } if used_price is not None else None
    return {
        "buy_box": buy_box,
        "buy_box_used": buy_box_used,
        "total_offer_count": _non_negative(stats.get("totalOfferCount")),
        "fba_offer_count": _non_negative(stats.get("offerCountFBA")),
        "fbm_offer_count": _non_negative(stats.get("offerCountFBM")),
        "stock": {"amazon": _non_negative(stats.get("stockAmazon")), "buy_box": _non_negative(stats.get("stockBuyBox"))},
        "amazon_availability": AMAZON_AVAILABILITY.get(row.get("availabilityAmazon")),
        "sales_rank_drops": {"days_30": _non_negative(stats.get("salesRankDrops30")), "days_90": _non_negative(stats.get("salesRankDrops90")),
                             "days_180": _non_negative(stats.get("salesRankDrops180")), "days_365": _non_negative(stats.get("salesRankDrops365"))},
        "monthly_sold_change_percent_90_days": stats.get("deltaPercent90_monthlySold"),
        "amazon_out_of_stock_count": {"days_30": _non_negative(stats.get("outOfStockCountAmazon30")),
                                      "days_90": _non_negative(stats.get("outOfStockCountAmazon90"))},
        "lowest_ever_price_types": _flagged(stats.get("isLowest")),
        "lowest_90_days_price_types": _flagged(stats.get("isLowest90")),
        "coupon": live_coupon,
        "coupon_history": coupons,
        "deal_badges": [{"type": (d.get("dealType") or "").lower() or None, "badge": clean(d.get("badge")),
                         "access": (d.get("accessType") or "").lower() or None}
                        for d in as_list(row.get("deals")) if isinstance(d, dict)],
        "prime_deal_ends_at": keepa_time(row.get("primeDealEndTime")),
        "review_count": _last_value(as_dict(row.get("reviews")).get("reviewCount")),
        "referral_fee_percent": row.get("referralFeePercentage") if row.get("referralFeePercentage") is not None else row.get("referralFeePercent"),
        "last_offers_update": keepa_time(stats.get("lastOffersUpdate")),
        "last_stock_update": keepa_time(row.get("lastStockUpdate")),
    }


EMPTY_MARKET = {key: None for key in (
    "buy_box", "buy_box_used", "total_offer_count", "fba_offer_count", "fbm_offer_count", "stock", "amazon_availability",
    "sales_rank_drops", "monthly_sold_change_percent_90_days", "amazon_out_of_stock_count", "lowest_ever_price_types",
    "lowest_90_days_price_types", "coupon", "coupon_history", "deal_badges", "prime_deal_ends_at", "review_count",
    "referral_fee_percent", "last_offers_update", "last_stock_update")}


def parse_search_row(row, country):
    """One search row -> the product as search knows it: identity, pictures,
    current values, then the offer-side blocks of parse_market."""
    scale = refs.marketplace(country)["scale"]
    stats = as_dict(row.get("stats"))
    prices, offer_counts, rating, rating_count = _current(stats.get("current"), scale)
    reviews = as_dict(row.get("reviews"))
    images = _images(row)
    unit = as_dict(row.get("unitCount"))
    store_path = clean(row.get("brandStoreUrl"))
    asin = row.get("asin")
    market = parse_market(row, country)
    return {
        **_identity(asin, country),
        "title": clean(row.get("title")),
        "parent_asin": row.get("parentAsin") if row.get("parentAsin") != asin else None,
        "parent_title": clean(row.get("parentTitle")),
        "image": images[0]["link"] if images else None,
        "images": images,
        "color": clean(row.get("color")),
        "size": clean(row.get("size")),
        "materials": [clean(m) for m in as_list(row.get("materials")) if clean(m)],
        "special_features": [clean(f) for f in as_list(row.get("specialFeatures")) if clean(f)],
        "item_highlights": clean(row.get("itemHighlights")),
        "unit_count": {"type": clean(unit.get("unitType")), "value": unit.get("unitValue")} if unit else None,
        "category_group": clean(row.get("websiteDisplayGroupName")),
        "root_category_id": row.get("rootCategory") or None,
        "sales_rank_category_id": row.get("salesRankReference") if (row.get("salesRankReference") or 0) > 0 else None,
        "brand_store": {"name": clean(row.get("brandStoreName")),
                        "link": f"https://www.amazon.{refs.marketplace(country)['tld']}{store_path}" if store_path else None}
        if row.get("brandStoreName") else None,
        "rating": rating,
        "rating_count": rating_count if rating_count is not None else _last_value(reviews.get("ratingCount")),
        "prices": prices,
        "offer_counts": offer_counts,
        **market,
        "is_adult_product": row.get("isAdultProduct"),
        "has_reviews": row.get("hasReviews"),
        "is_subscribe_and_save": row.get("isSNS"),
        "is_eligible_for_super_saver_shipping": row.get("isEligibleForSuperSaverShipping"),
        "is_eligible_for_trade_in": row.get("isEligibleForTradeIn"),
        "is_heat_sensitive": row.get("isHeatSensitive"),
        "is_launchpad": row.get("launchpad"),
    }


def parse_search(answer, country):
    return [parse_search_row(row, country) for row in as_list(as_dict(answer).get("searchProducts")) if isinstance(row, dict) and row.get("asin")]


# ---- deals --------------------------------------------------------------------------------------------------------

def _deal_image(value):
    """A deal's picture name arrives as a list of character codes."""
    if isinstance(value, list):
        try:
            value = "".join(chr(c) for c in value)
        except (TypeError, ValueError):
            return None
    return image_link(value)


def parse_deal(row, country, price_type, asked_interval=None):
    """One deals row. `price_type` is the type the list was asked for: its
    current price, since when, and its drop against the average of the day,
    the week, the month and the 3 months. `asked_interval` is the drop interval
    the list was asked for: every row of a one-interval list matched on it
    (the site leaves `dateIntervalIndex` at 0 there: all 550 rows of the
    four week lists saved, 48 of them with no drop in the day at all), so
    the row's own index is read only on a list over all intervals."""
    scale = refs.marketplace(country)["scale"]
    slot = refs.SLOT_OF[price_type]
    current = as_list(row.get("current"))
    since = as_list(row.get("currentSince"))
    prices, offer_counts, _rating, _rating_count = _current(current, scale)
    drops = []
    for index, interval in enumerate(refs.DEAL_INTERVAL_NAMES):
        percent = g(row, "deltaPercent", index, slot)
        drops.append({"interval": interval,
                      "average_price": money(g(row, "avg", index, slot), scale),
                      "drop": money(g(row, "delta", index, slot), scale),
                      "drop_percent": percent if isinstance(percent, int) and percent >= 0 else None})
    matched = row.get("dateIntervalIndex")
    condition = row.get("warehouseCondition")
    return {
        **_identity(row.get("asin"), country),
        "title": clean(row.get("title")),
        "image": _deal_image(row.get("image")),
        "category_ids": as_list(row.get("categories")),
        "item_highlights": clean(row.get("itemHighlights")),
        "price_type": price_type,
        "price": money(current[slot], scale) if slot < len(current) else None,
        "price_since": keepa_time(since[slot]) if slot < len(since) else None,
        "drops": drops,
        "drop_interval": asked_interval if asked_interval in refs.DEAL_INTERVAL_NAMES else (
            refs.DEAL_INTERVAL_NAMES[matched] if isinstance(matched, int) and 0 <= matched < 4 else None),
        "warehouse_condition": refs.WAREHOUSE_CONDITION_NAMES.get(condition),
        "warehouse_condition_comment": clean(row.get("warehouseConditionComment")),
        "prices": prices,
        "offer_counts": offer_counts,
        "deal_found_at": keepa_time(row.get("creationDate")),
    }


def parse_deals(answer, country, price_type, asked_interval=None):
    """A deals answer -> (rows, total, categories). `total` is None on a page
    past the end, where the site sends an empty `deals`. `asked_interval` is the
    drop interval the list was asked for (parse_deal)."""
    answer = as_dict(answer)
    deals = as_dict(answer.get("deals"))
    rows = [parse_deal(row, country, price_type, asked_interval) for row in as_list(deals.get("drBasic") or deals.get("dr")) if isinstance(row, dict)]
    ids, names, counts = as_list(deals.get("categoryIds")), as_list(deals.get("categoryNames")), as_list(deals.get("categoryCount"))
    categories = [{"id": ids[i], "name": clean(names[i]) if i < len(names) else None, "deals_count": counts[i] if i < len(counts) else None}
                  for i in range(len(ids)) if i < len(names) and names[i] != "?"]
    total = answer.get("totalResults")
    return rows, (total if isinstance(total, int) else None), categories


# ---- categories -----------------------------------------------------------------------------------------------------

def parse_categories(answer):
    """service/search category -> the categories, the ones whose own name matched first."""
    rows = []
    for category in as_dict(as_dict(answer).get("categories")).values():
        if not isinstance(category, dict) or not category.get("catId"):
            continue
        rows.append({"id": category.get("catId"), "name": clean(category.get("name")),
                     "parent_id": category.get("parent") or None,
                     "children_ids": as_list(category.get("children")),
                     "children_count": len(as_list(category.get("children"))),
                     "product_count": category.get("productCount"),
                     "is_browse_node": category.get("isBrowseNode"),
                     "is_match": category.get("matched") is True})
    rows.sort(key=lambda c: (not c["is_match"], -(c["product_count"] or 0)))
    return rows
