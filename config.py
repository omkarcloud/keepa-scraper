"""Configuration for the Keepa Scraper. Everything can be set with an environment
variable; the defaults work out of the box.

    PORT               port the API listens on (default 8000)

    CAPSOLVER_API_KEY  needed for Search Products, Deals and Search Categories
                       (and for refresh / include_buy_box on Product Details).
                       Keepa asks those calls for a Cloudflare Turnstile check;
                       CapSolver solves it in about 4 seconds, no browser
                       needed, and one solve covers about 8 searches or 30
                       deals pages (about $1.20 per 1,000 solves at
                       CapSolver's normal price):
                       https://dashboard.capsolver.com/passport/register?inviteCode=lvdYBC4sYKRm
                       Product details, price history, international prices
                       and variations need no key at all.

    KEEPA_PROXY        proxy URL for every request, e.g. http://user:pass@host:port
                       (default: none). No proxy needed: Keepa answers every
                       call from your own connection.

Everything else below is a plain constant with a working default.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

KEEPA_PROXY = os.environ.get("KEEPA_PROXY") or None

# --- captcha solving --------------------------------------------------------
CAPSOLVER_API_KEY = os.environ.get("CAPSOLVER_API_KEY", "").strip()
# Our CapSolver developer app id: CapSolver pays us a small commission on the
# captchas you solve, at no extra cost to you (you pay CapSolver's normal price).
CAPSOLVER_APP_ID = "DC601421-43D5-45E4-9FDB-B3BAF7A2C3FD"

# The Turnstile widget behind keepa.com's "checkHuman" (the site's own public key).
KEEPA_TURNSTILE_SITEKEY = "0x4AAAAAABeeF71YWyrb3Qcg"


# ---- what the code reads off config: defaults for a direct run ----
KEEPA_CHANNEL_MAX_IDLE = 240
KEEPA_CLEARANCES_PER_MINUTE = 20
KEEPA_CLEARANCE_MAX_AGE = 1800
KEEPA_CLEARANCE_MINT_AHEAD = 36
KEEPA_CLEARANCE_UNITS = 96
KEEPA_EXIT = KEEPA_PROXY      # the exit every session leaves through: your proxy, or direct
KEEPA_FRAME_VERSION = '20261125'
KEEPA_SOLVE_TIMEOUT = 90
MAX_RETRIES = 3
RETRY_BACKOFF = 2
def gateway_exit(exit=None):
    return None
