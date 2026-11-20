"""Reference resolvers and the fixed tables of /keepa/*.

ONE param per input: `product` accepts an ASIN (or an ISBN-10, which Amazon
uses as the ASIN of a book), an Amazon product link or a Keepa product link.
Resolvers raise ValueError with a user-facing message (RefField turns it
into a 400). A pasted link also names its marketplace (amazon.de, or the
`3-` in keepa.com/#!product/3-<ASIN>): country_of() reads it and the schemas
use it when `country` is absent.

Marketplaces (the site's own table in c-XGDIARAG.js, read 2026-11-20):
Keepa numbers them 1-14 ("domainId"). 7 (amazon.cn) answers status 400
("Unknown Amazon locale"), so it is not listed.
"""
import re
from urllib.parse import parse_qs, urlparse

SITE = "https://keepa.com"
IMAGE_BASE = "https://m.media-amazon.com/images/I/"

DEFAULT_COUNTRY = "US"

# country -> Keepa domainId, Amazon host suffix, currency, minor units per
# unit (a yen price is whole yen: deals_jp_page0 on 2026-11-20 priced a
# curtain at 1812).
MARKETPLACES = {
    "US": {"domain_id": 1, "tld": "com", "currency": "USD", "scale": 100},
    "GB": {"domain_id": 2, "tld": "co.uk", "currency": "GBP", "scale": 100},
    "DE": {"domain_id": 3, "tld": "de", "currency": "EUR", "scale": 100},
    "FR": {"domain_id": 4, "tld": "fr", "currency": "EUR", "scale": 100},
    "JP": {"domain_id": 5, "tld": "co.jp", "currency": "JPY", "scale": 1},
    "CA": {"domain_id": 6, "tld": "ca", "currency": "CAD", "scale": 100},
    "IT": {"domain_id": 8, "tld": "it", "currency": "EUR", "scale": 100},
    "ES": {"domain_id": 9, "tld": "es", "currency": "EUR", "scale": 100},
    "IN": {"domain_id": 10, "tld": "in", "currency": "INR", "scale": 100},
    "MX": {"domain_id": 11, "tld": "com.mx", "currency": "MXN", "scale": 100},
    "BR": {"domain_id": 12, "tld": "com.br", "currency": "BRL", "scale": 100},
    "AU": {"domain_id": 13, "tld": "com.au", "currency": "AUD", "scale": 100},
    "NL": {"domain_id": 14, "tld": "nl", "currency": "EUR", "scale": 100},
}
COUNTRIES = tuple(MARKETPLACES)
COUNTRY_ALIASES = {"UK": "GB"}
_BY_DOMAIN_ID = {m["domain_id"]: country for country, m in MARKETPLACES.items()}
_BY_HOST = {"amazon." + m["tld"]: country for country, m in MARKETPLACES.items()}

_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
_ASIN_PATH_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|product-reviews|gp/offer-listing|dp/product)/([A-Z0-9]{10})(?=[/?#]|$)", re.I)
# keepa.com/#!product/1-B0BN72FYFG and keepa.com/product/1-B0BN72FYFG
_KEEPA_PRODUCT_RE = re.compile(r"product/(\d{1,2})-([A-Z0-9]{10})(?=[/?#&]|$)", re.I)

PRODUCT_MESSAGE = ("Must be an ASIN (B0BN72FYFG), an Amazon product link (https://www.amazon.com/dp/B0BN72FYFG) "
                   "or a Keepa product link (https://keepa.com/#!product/1-B0BN72FYFG).")


def country(value):
    """'us' / 'UK' / 'gb' -> the marketplace's ISO code ('US', 'GB'); ValueError for a country Keepa does not track."""
    code = (value or "").strip().upper()
    code = COUNTRY_ALIASES.get(code, code)
    if code not in MARKETPLACES:
        raise ValueError(f"Must be one of: {', '.join(c.lower() for c in COUNTRIES)} (uk is accepted for gb).")
    return code


def country_of_domain_id(domain_id):
    return _BY_DOMAIN_ID.get(domain_id)


def marketplace(country_code):
    return MARKETPLACES[country_code]


def _host(value):
    url = value if "://" in value else "https://" + value.lstrip("/")
    return urlparse(url)


def _is_link(value):
    low = value.lower()
    return low.startswith(("http://", "https://", "//")) or "amazon." in low or "keepa.com" in low


def _amazon_country(hostname):
    host = (hostname or "").lower()
    for suffix, code in _BY_HOST.items():
        if host == suffix or host.endswith("." + suffix):
            return code
    return None


def country_of(value):
    """The marketplace a pasted link names (ISO code), None for a bare ASIN or a link that names none."""
    value = (value or "").strip()
    if not value or not _is_link(value):
        return None
    parsed = _host(value)
    host = (parsed.hostname or "").lower()
    if host == "keepa.com" or host.endswith(".keepa.com"):
        match = _KEEPA_PRODUCT_RE.search(value)
        return country_of_domain_id(int(match.group(1))) if match else None
    return _amazon_country(host)


def resolve_product(value):
    """An ASIN / ISBN-10, an Amazon product link or a Keepa product link -> the ASIN."""
    value = (value or "").strip()
    if _ASIN_RE.match(value.upper()) and not value.isalpha():
        return value.upper()
    if _is_link(value):
        parsed = _host(value)
        host = (parsed.hostname or "").lower()
        if host == "keepa.com" or host.endswith(".keepa.com"):
            match = _KEEPA_PRODUCT_RE.search(value)
            if match:
                if country_of_domain_id(int(match.group(1))) is None:
                    raise ValueError("That Keepa link is for a marketplace Keepa no longer tracks.")
                return match.group(2).upper()
        elif "amazon." in host:
            if _amazon_country(host) is None:
                raise ValueError(f"Keepa does not track {host}. Marketplaces: {', '.join(c.lower() for c in COUNTRIES)}.")
            match = _ASIN_PATH_RE.search(parsed.path)
            if match:
                return match.group(1).upper()
            asin = parse_qs(parsed.query).get("asin", [None])[0]
            if asin and _ASIN_RE.match(asin.upper()):
                return asin.upper()
    raise ValueError(PRODUCT_MESSAGE)


def resolve_category(value):
    """A Keepa / Amazon category (browse node) id -> int."""
    value = (value or "").strip()
    if value.isdigit() and 0 < len(value) <= 15:
        return int(value)
    raise ValueError("Must be a category id (the `id` of a /keepa/categories/search row, e.g. 1055398).")


def amazon_link(asin, country_code):
    return f"https://www.amazon.{MARKETPLACES[country_code]['tld']}/dp/{asin}"


def keepa_link(asin, country_code):
    return f"{SITE}/#!product/{MARKETPLACES[country_code]['domain_id']}-{asin}"


# ---- price types ------------------------------------------------------------------------------------------
# The 36 slots of every `csv`, `stats.current`, `stats.min`, deal `current` … list (the site's own table in
# c-ZADD4X2Y.js, read 2026-11-20): slot -> (public name, kind, with shipping). A series "with shipping" is a
# run of (time, price, shipping) triples, the others of (time, value) pairs.
# kind: price | count | rating. Not published: slot 3 (sales rank) answers -2 to a guest, slot 15 is the
# site's internal update log, and 23-27, 30 and 31 (collectible by condition, refurbished with shipping,
# trade-in, rent) are switched off in the site's own table and never held a value in the answers saved.
PRICE, COUNT, RATING = "price", "count", "rating"
SLOTS = {
    0: ("amazon", PRICE, False),
    1: ("new", PRICE, False),
    2: ("used", PRICE, False),
    4: ("list_price", PRICE, False),
    5: ("collectible", PRICE, False),
    6: ("refurbished", PRICE, False),
    7: ("new_fbm", PRICE, True),
    8: ("lightning_deal", PRICE, False),
    9: ("warehouse", PRICE, False),
    10: ("new_fba", PRICE, False),
    11: ("new_offer_count", COUNT, False),
    12: ("used_offer_count", COUNT, False),
    13: ("refurbished_offer_count", COUNT, False),
    14: ("collectible_offer_count", COUNT, False),
    16: ("rating", RATING, False),
    17: ("rating_count", COUNT, False),
    18: ("buy_box", PRICE, True),
    19: ("used_like_new", PRICE, True),
    20: ("used_very_good", PRICE, True),
    21: ("used_good", PRICE, True),
    22: ("used_acceptable", PRICE, True),
    28: ("ebay_new", PRICE, True),
    29: ("ebay_used", PRICE, True),
    32: ("buy_box_used", PRICE, True),
    33: ("prime_exclusive", PRICE, False),
    34: ("new_fba_offer_count", COUNT, False),
    35: ("new_fbm_offer_count", COUNT, False),
}
SALES_RANK_SLOT = 3
SLOT_OF = {name: slot for slot, (name, _kind, _shipping) in SLOTS.items()}
PRICE_TYPES = tuple(name for name, _kind, _shipping in SLOTS.values())

# ---- deals --------------------------------------------------------------------------------------------------
# What the deals page's own controls send (v-deals.js, 2026-11-20), each seen changing the answer.
DEAL_PRICE_TYPES = {     # `price_type` -> dealRequest.priceTypes[0]; sales rank (3) and used buy box (32) answer -2 to a guest
    "amazon": 0, "new": 1, "used": 2, "collectible": 5, "refurbished": 6, "new_fbm": 7, "lightning_deal": 8,
    "warehouse": 9, "new_fba": 10, "buy_box": 18, "used_like_new": 19, "used_very_good": 20, "used_good": 21,
    "used_acceptable": 22, "prime_exclusive": 33,
}
DEAL_INTERVALS = {"day": 0, "week": 1, "month": 2, "3_months": 3, "all": 4}       # dealRequest.dateRange
DEAL_INTERVAL_NAMES = ("day", "week", "month", "3_months")                          # the 4 rows of a deal's avg / delta / deltaPercent
# sort -> (sortType, the order that number means); the other order is the negative number. "newest" has one order.
DEAL_SORTS = {"percent": (4, "desc"), "drop": (2, "desc"), "price": (5, "asc"), "newest": (1, "desc"), "sales_rank": (3, "asc")}
DEAL_ORDERS = ("asc", "desc")
WAREHOUSE_CONDITIONS = {"new": 1, "like_new": 2, "very_good": 3, "good": 4, "acceptable": 5}
WAREHOUSE_CONDITION_NAMES = {v: k for k, v in WAREHOUSE_CONDITIONS.items()}
DEALS_PER_PAGE = 150          # fixed by the site (perPage 50 and 300 answered the same 150 rows)
DEALS_MAX_PAGE = 67           # 10,000 rows deep: page 67 (the 0-based 66) holds the last 100
NO_LIMIT = 2147483647         # the page's own "no upper bound"


def deal_sort_type(sort, order):
    """The dealRequest.sortType for a sort and an order (None = the sort's own); ValueError for an order it lacks."""
    number, natural = DEAL_SORTS[sort]
    if order is None or order == natural:
        return number
    if sort == "newest":
        raise ValueError("sort=newest has one order: the newest deal first.")
    return -number


# Amazon's product-type numbers on a product record.
PRODUCT_TYPES = {0: "standard", 1: "downloadable", 2: "ebook", 3: "inaccessible", 4: "invalid", 5: "variation_parent"}
NOT_FOUND_TYPES = (3, 4)
