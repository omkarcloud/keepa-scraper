# 🚀 Keepa Scraper

Keepa Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Keepa data for free.

## ✨ What Can I Get?

- 🛒 **Full details of 7B+ Amazon products** — every current price, all-time lows with their dates, rating, offer counts, images, EANs & variations
- 📈 **Price history in 27 series** — Amazon, new, used, buy box, FBA, FBM, warehouse & lightning-deal prices, offer counts and rating, since Keepa started tracking
- 🔥 **690K+ live price drops** — drop %, the average price it fell from, current prices, offer counts and 20+ filters: category, price, rating, lowest ever
- 🌍 **Prices on 13 Amazon marketplaces** — the same product on Amazon US, UK, Germany, France, Japan, Canada, Italy, Spain, India, Mexico, Brazil, Australia & the Netherlands, in local currency

## 🛒 Example: A Full Keepa Product

```json
{
  "asin": "B07QTFTCV2",
  "link": "https://keepa.com/#!product/1-B07QTFTCV2",
  "amazon_link": "https://www.amazon.com/dp/B07QTFTCV2",
  "country": "US",
  "currency": "USD",
  "title": "PAVOI 14K Gold Plated Crystal Solitaire 1.5 Carat (7.3mm) Cubic Zirconia Dainty Choker Necklace | Gold Necklaces for Women",
  "manufacturer": "PAVOI",
  "parent_asin": "B0F48ZYTDC",
  "image": "https://m.media-amazon.com/images/I/61X-F7dVj7L.jpg",
  "eans": ["6261881838661"],
  "rating": 4.4,
  "rating_count": 18275,
  "prices": { "amazon": 16.47, "new": 11.71, "used": 10.67, "list_price": 14.64, "warehouse": 10.67, "new_fba": 11.71, "buy_box": 11.71 },
  "offer_counts": { "new": 2, "used": 3 },
  "lowest_prices": {
    "amazon": { "price": 13.45, "date": "2023-03-23T12:40:00Z" },
    "buy_box": { "price": 9.07, "date": "2021-11-20T08:08:00Z" },
    "lightning_deal": { "price": 10.47, "date": "2024-07-16T07:00:00Z" }
  },
  "out_of_stock_percent_90_days": { "amazon": 0, "new": 0, "used": 17, "buy_box": 0 },
  "variations": [
    { "asin": "B07QQ3YZB6", "attributes": [{ "name": "Color", "value": "Rose Gold" }] },
    { "asin": "B07QSFHT27", "attributes": [{ "name": "Color", "value": "Yellow Gold" }] }
  ],
  "variations_count": 3,
  "tracking_since": "2019-06-08T09:38:00Z",
  "last_updated": "2026-11-20T14:43:00Z"
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Keepa Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/keepa-scraper
cd keepa-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

No proxy needed: Keepa answers every call from your own connection.

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/products/details?product=B07QTFTCV2"
```

```json
{
  "asin": "B07QTFTCV2",
  "link": "https://keepa.com/#!product/1-B07QTFTCV2",
  "amazon_link": "https://www.amazon.com/dp/B07QTFTCV2",
  "country": "US",
  "currency": "USD",
  "title": "PAVOI 14K Gold Plated Crystal Solitaire 1.5 Carat (7.3mm) Cubic Zirconia Dainty Choker Necklace | Gold Necklaces for Women",
  "manufacturer": "PAVOI",
  "rating": 4.4,
  "rating_count": 18275,
  "prices": { "amazon": 16.47, "new": 11.71, "used": 10.67, "buy_box": 11.71 },
  "offer_counts": { "new": 2, "used": 3 },
  "lowest_prices": {
    "amazon": { "price": 13.45, "date": "2023-03-23T12:40:00Z" },
    "buy_box": { "price": 9.07, "date": "2021-11-20T08:08:00Z" }
  },
  "variations_count": 3,
  "tracking_since": "2019-06-08T09:38:00Z",
  "last_updated": "2026-11-20T14:43:00Z"
}
```

All 7 endpoints are now live at `http://localhost:8000`. Product details, price history, international prices and variations work straight away.

### 🔓 Unlock Search, Deals & Categories

Keepa puts a Cloudflare Turnstile check on its search, deals and category calls. The scraper solves it for you with CapSolver in about 4 seconds, no browser needed:

1. Get a [CapSolver](https://dashboard.capsolver.com/passport/register?inviteCode=lvdYBC4sYKRm) API key. One solve covers about 8 searches or 30 deals pages, at about $1.20 per 1,000 solves.
2. Start the API with it:
   ```bash
   CAPSOLVER_API_KEY=CAP-... python run.py
   ```

The same key unlocks `refresh=true` (the price of this minute) and `include_buy_box=true` (buy box, stock and coupons) on Product Details. See [config.py](config.py) for every option.

## 📚 Endpoints

7 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Product Details | `/products/details` | Every current price, all-time lows, rating and variations in one call |
| Price History | `/products/price-history` | Every price series since Keepa started tracking, point by point |
| International Prices | `/products/international-prices` | The same product's prices on all 13 Amazon marketplaces |
| Variations | `/products/variations` | Every color and size with prices, rating and review history |
| Search Products | `/products/search` | Amazon products by keyword, ASIN, UPC or EAN, with current prices |
| Deals | `/deals/list` | 690K+ live price drops with 20+ filters, 150 per page |
| Search Categories | `/categories/search` | Category ids, parents and product counts for the deals filter |

## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-keepa-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-keepa-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-keepa-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/products/details",
    params={"product": "B07QTFTCV2"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Keepa Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://www.omkar.cloud/l/keepa-scraper-github-whatsapp)

[![Ask Us by Email about Keepa Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Keepa%20Scraper%20API&body=I%20need%20help%20using%20the%20Keepa%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper-api) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities
- [**Etsy Scraper**](https://www.omkar.cloud/tools/etsy-scraper) — Etsy products: prices, discounts, shops & variations

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/keepa-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/keepa-scraper)

*Disclosure: the CapSolver link above is a referral link. CapSolver pays us a small commission for those solves. It costs you nothing extra. This helps support the project.*
