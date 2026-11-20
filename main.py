"""Use the scraper straight from Python, no server needed.

    python main.py

Each call validates its params with the same schema the API uses (so the
defaults apply) and returns the same JSON the API does, except that a list
keeps its `pagination` block instead of next / previous links. The results
are written to output/*.json.
"""
import json
import os

from keepa import api, schemas
from schema_fields import load_query

os.makedirs("output", exist_ok=True)


def run(schema_cls, impl, **params):
    data, error = load_query(schema_cls, {k: str(v) for k, v in params.items()})
    if error:
        raise ValueError(error)
    return impl(**data)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # The full product: every current price, all-time lows, rating, offer counts, images and variations.
    save("products_details.json", run(schemas.ProductDetailsSchema, api.product_details, product='B07QTFTCV2'))

    # Every price series Keepa holds for it (Amazon, new, used, buy box, FBA, warehouse, rating …), point by point.
    save("products_price_history.json", run(schemas.PriceHistorySchema, api.product_price_history, product='B07QTFTCV2'))

    # The same product on all 13 Amazon marketplaces, with each one's prices in its own currency.
    save("products_international_prices.json", run(schemas.InternationalPricesSchema, api.product_international_prices, product='B07QTFTCV2'))
