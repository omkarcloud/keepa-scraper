"""Cache TTL per /keepa/* endpoint (cache.py, keyed on the validated params).

Keepa itself is a cache of Amazon: a product record says when Keepa last
read it (`last_updated`; minutes old for a best seller, a day or more for a
slow one), so an hour of our own caching adds little staleness. A caller
who needs the price of this minute sends `refresh=true`, which skips the
read and overwrites the row.
"""
from datetime import timedelta

# The details record (one open frame, no price series).
PRODUCT_CACHE = timedelta(hours=1)
# The price series (one open frame, up to ~1.5 MB): a new point appears only when a price moves.
HISTORY_CACHE = timedelta(hours=1)
# A family's members change when a seller adds a colour or a size.
VARIATIONS_CACHE = timedelta(hours=6)
# One open frame per marketplace.
INTERNATIONAL_CACHE = timedelta(hours=1)
# Search costs a tenth of a Turnstile clearance: keep an answer as long as its prices are useful.
SEARCH_CACHE = timedelta(hours=2)
# The offer-side blocks of include_buy_box (one search by ASIN): the same cost, the same life.
MARKET_CACHE = timedelta(hours=2)
# Deals move by the minute; the page itself reloads on demand.
DEALS_CACHE = timedelta(minutes=10)
# Amazon's category tree changes over months.
CATEGORIES_CACHE = timedelta(days=7)
