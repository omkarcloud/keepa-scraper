"""Keepa transport: one WebSocket, plain curl_cffi, no browser.

keepa.com serves no data over HTTP. Its page opens

    wss://push.keepa.com/apps/cloud/?app=keepaWebsite&version=3.0&sp=1

and every call is one frame out and one frame back, matched by `id`:

    sent      a JSON object, zlib-deflated, as a binary frame
    received  a JSON object: zstd-compressed when the frame opens with the
              zstd magic (28 b5 2f fd), plain text otherwise

The first frame the server sends is a hello with status 108. A visitor who
holds a guest token names it in the handshake (Sec-WebSocket-Protocol).

Two kinds of call (measured 2026-11-20, notes/config/keepa.md):

  * OPEN    `product` frames of type "casin" (the page's "compare
    international prices" request) and "variation" (its variations tab)
    answer with no token at all: 400 of 400 on one connection from a home
    line, 300 of 300 from the pod's own IP. `seller/name` is open too.
  * CLEARED `service/search`, `service/deals`, a product with
    refreshProduct, and the page's own product frame (type "ws") answer
    {"checkHuman": true} with status 200 until the guest token is cleared:
    a Cloudflare Turnstile token for the site's key, sent as
    {"path": "turnstile"}. A solver's proxyless Turnstile task supplies it
    (no browser; 12 of 12 accepted). One clearance is worth about 100
    units: a product or a refresh 1, a deals page about 2.5, a search 10 to
    12. The clearance belongs to the token, not to the address or the
    connection, so the cleared tokens live in one identity_pool.Pool that
    every request thread shares; a thread keeps only its own connections.

An open product frame that ever answers checkHuman is sent again as the
page's own "ws" frame on a cleared token (the same record, field by field).

Egress: the pod's own IP (config.KEEPA_EXIT). A WebSocket cannot ride the
gateway exit; the knob takes "direct", "datacenter" or a country.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  KeepaUpstreamError  connect / read failure, a closed socket, 5xx  — retryable
  KeepaBlocked        403 / 429, a clearance the site refused       — retryable
  KeepaBadRequest     status 400 (a marketplace or an input it rejects) — never retried
  KeepaNotFound       raised by the parsers' callers (api.py)        — never retried
"""
import inspect
import json
import os
import sys
import threading
import time
import zlib
from select import select

# Allow direct execution (python keepa/fetch.py): flat imports resolve
# like under the server. Idempotent when imported normally.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import egress
import identity_pool
from keepa.refs import DEALS_PER_PAGE, SITE
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from scraper_utils import retry

IMPERSONATE = "chrome"
WS_URL = "wss://push.keepa.com/apps/cloud/?app=keepaWebsite&version=3.0&sp=1"
# The handshake as the page sends it. User-Agent, Accept-Language and the
# Sec-WebSocket-* headers are the session's own.
HANDSHAKE_HEADERS = {"Origin": SITE, "Cache-Control": "no-cache", "Pragma": "no-cache"}
HOST_MARK = "y05NLUjUS87PBQA="     # the page's `h` on its first frame: base64(deflate-raw("keepa.com"))
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
CAPTCHA_API = "https://api.capsolver.com"
SOLVE_POLL = 1.0

CONNECT_TIMEOUT = 15
ANSWER_TIMEOUT = 30      # a family of 205 variations took 2.9 s
REFRESH_TIMEOUT = 60     # Keepa re-reads Amazon: p50 4.4 s, p95 11.4 s (25 calls, 2026-11-20)

# What one call takes from a clearance's ~100 units (2026-11-20: 100 product
# frames, 25 refreshes + 74 products, 8 to 10 searches, 39 to 42 deals pages).
COST_PRODUCT = 1
COST_SEARCH = 12
COST_DEALS = 3
COST_CATEGORIES = 2      # not measured alone: priced above a product to stay on the safe side


class KeepaUpstreamError(UpstreamError):
    """Connect / read failure, a closed socket, an answer that is not JSON, 5xx — retryable."""


class KeepaBlocked(KeepaUpstreamError, Blocked):
    """Status 403 / 429 / 402, or a Turnstile token the site refused — retryable."""


class KeepaBadRequest(BadRequest):
    """Status 400: the site rejects the input — never retried."""


class KeepaNotFound(NotFound):
    """The product / marketplace has no such record — never retried."""


class KeepaCaptchaNotConfigured(RuntimeError):
    """No CAPSOLVER_API_KEY: search, deals and refresh cannot run at all, so
    this is a configuration error (500, never retried), not a block."""


# ---- frames -----------------------------------------------------------------------------------------------

def pack(message):
    """A request object -> the binary frame the site expects (zlib)."""
    return zlib.compress(json.dumps(message, separators=(",", ":")).encode("utf-8"))


def _unzstd(raw):
    try:
        from compression import zstd       # Python 3.14+
        return zstd.decompress(raw)
    except ImportError:
        import zstandard
        return zstandard.ZstdDecompressor().decompressobj().decompress(raw)


def unpack(data):
    """A received frame -> the answer object. KeepaUpstreamError for anything that is not a JSON object."""
    raw = data if isinstance(data, (bytes, bytearray)) else str(data).encode("utf-8")
    try:
        if raw[:4] == ZSTD_MAGIC:
            raw = _unzstd(bytes(raw))
        answer = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise KeepaUpstreamError(f"unreadable frame ({len(raw)} bytes): {type(e).__name__}: {e}")
    if not isinstance(answer, dict):
        raise KeepaUpstreamError(f"unexpected frame: {str(answer)[:120]}")
    return answer


_recv_takes_timeout = None


def _recv(ws, timeout):
    """One whole message from the socket within `timeout` seconds. Newer
    curl_cffi takes the timeout itself; older ones loop for ever on a silent
    socket, so there the fragments are read here against a deadline."""
    global _recv_takes_timeout
    if _recv_takes_timeout is None:
        _recv_takes_timeout = "timeout" in inspect.signature(ws.recv).parameters
    if _recv_takes_timeout:
        data, _flags = ws.recv(timeout=timeout)
        return data
    from curl_cffi.const import CurlECode, CurlInfo, CurlWsFlag
    from curl_cffi.curl import CurlError
    deadline = time.monotonic() + timeout
    sock = ws.curl.getinfo(CurlInfo.ACTIVESOCKET)
    chunks = []
    while True:
        try:
            chunk, frame = ws.recv_fragment()
        except CurlError as e:
            if e.code != CurlECode.AGAIN:
                raise
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"no frame in {timeout}s")
            select([sock], [], [], min(left, 0.5))
            continue
        if frame.flags & CurlWsFlag.CLOSE:
            raise ConnectionError("the server closed the socket")
        if frame.flags & (CurlWsFlag.PING | CurlWsFlag.PONG):
            continue
        chunks.append(chunk)
        if frame.bytesleft == 0 and frame.flags & CurlWsFlag.CONT == 0:
            return b"".join(chunks)


class Channel:
    """One WebSocket to the site: its curl session, the guest token it was
    opened with (None = an anonymous connection) and the next frame id."""

    def __init__(self, token=None):
        if config.gateway_exit(config.KEEPA_EXIT):
            raise KeepaUpstreamError("KEEPA_EXIT names the gateway exit, and a WebSocket cannot ride it")
        self.token = token
        self.last_used = time.monotonic()
        self.next_id = int(time.time() * 1000) % 12345      # the page starts at a random id below 12345
        self.sess = egress.exit_session(config.KEEPA_EXIT, impersonate=IMPERSONATE)
        headers = dict(HANDSHAKE_HEADERS)
        if token:
            headers["Sec-WebSocket-Protocol"] = token
        try:
            self.ws = self.sess.ws_connect(WS_URL, headers=headers, timeout=CONNECT_TIMEOUT)
            hello = unpack(_recv(self.ws, CONNECT_TIMEOUT))
        except KeepaUpstreamError:
            self.close()
            raise
        except Exception as e:
            self.close()
            raise KeepaUpstreamError(f"websocket connect failed: {type(e).__name__}: {e}")
        if hello.get("status") != 108:
            self.close()
            raise KeepaUpstreamError(f"unexpected hello frame: status {hello.get('status')}")

    def _send(self, message):
        message = dict(message, id=self.next_id, version=config.KEEPA_FRAME_VERSION)
        self.next_id += 1
        try:
            self.ws.send(pack(message))
        except Exception as e:
            raise KeepaUpstreamError(f"websocket send failed: {type(e).__name__}: {e}")
        return message["id"]

    def _read(self, deadline):
        left = deadline - time.monotonic()
        if left <= 0:
            raise KeepaUpstreamError("no answer in time")
        try:
            return unpack(_recv(self.ws, left))
        except KeepaUpstreamError:
            raise
        except Exception as e:
            raise KeepaUpstreamError(f"websocket read failed: {type(e).__name__}: {e}")

    def ask_many(self, messages, timeout=ANSWER_TIMEOUT):
        """Write every frame, then read until each has its answer (the site
        answers out of order; status 100 frames are progress). Answers align
        with `messages`."""
        ids = [self._send(m) for m in messages]
        deadline = time.monotonic() + timeout
        got = {}
        while len(got) < len(ids):
            answer = self._read(deadline)
            if answer.get("id") in ids and answer.get("status") != 100:
                got[answer["id"]] = answer
        self.last_used = time.monotonic()
        return [got[i] for i in ids]

    def ask(self, message, timeout=ANSWER_TIMEOUT):
        return self.ask_many([message], timeout)[0]

    def close(self):
        for thing in (getattr(self, "ws", None), getattr(self, "sess", None)):
            try:
                if thing is not None:
                    thing.close()
            except Exception:
                pass


# ---- the thread's connections -------------------------------------------------------------------------------
# One connection of each kind per request thread: a socket must not be shared
# across threads. The thread keeps the CONNECTION only; the cleared token it
# was opened with lives in the pool below.
_local = threading.local()


def _channel(slot, token=None):
    ch = getattr(_local, slot, None)
    if ch is not None and (ch.token != token or time.monotonic() - ch.last_used > config.KEEPA_CHANNEL_MAX_IDLE):
        ch.close()
        ch = None
    if ch is None:
        ch = Channel(token)
        setattr(_local, slot, ch)
    return ch


def _drop_channels():
    """Close the thread's connections so the retry opens fresh ones."""
    for slot in ("open_channel", "cleared_channel"):
        ch = getattr(_local, slot, None)
        setattr(_local, slot, None)
        if ch is not None:
            ch.close()


def _retrying(fn):
    """Run fn() under the shared retry policy (KeepaUpstreamError only;
    BadRequest surfaces immediately)."""
    return retry.run(fn, config, KeepaUpstreamError, lambda e: _drop_channels())


def _checked(answer, label):
    """Map an answer's status to the error taxonomy; return the answer."""
    status = answer.get("status")
    if status == 200:
        return answer
    detail = str(answer.get("errorMsg") or answer.get("message") or "")[:160]
    if status == 400:
        raise KeepaBadRequest(f"{label}: the site rejects the request" + (f" ({detail})" if detail else ""))
    if status in (402, 403, 429):
        raise KeepaBlocked(f"{label}: refused with status {status} {detail}".strip())
    if status == 505:
        raise KeepaUpstreamError(f"{label}: the site calls frame version {config.KEEPA_FRAME_VERSION} outdated "
                                 "(config.KEEPA_FRAME_VERSION: the /cdn/<version>/keepa.js of keepa.com's page)")
    raise KeepaUpstreamError(f"{label}: status {status} {detail}".strip())


# ---- clearances (cleared guest tokens) ------------------------------------------------------------------------

def _turnstile_token():
    """One Cloudflare Turnstile token for the site's widget (single-use),
    from the solver's proxyless task; the page renders it with action
    "blocked"."""
    key = getattr(config, "CAPSOLVER_API_KEY", "")
    if not key:
        raise KeepaCaptchaNotConfigured(
            "set CAPSOLVER_API_KEY to use this endpoint: Keepa asks a Cloudflare Turnstile "
            "token before it answers a search, a deals page or a refresh (see config.py)")
    from curl_cffi import requests as curl_requests
    task = {"type": "AntiTurnstileTaskProxyLess", "websiteURL": SITE + "/", "websiteKey": config.KEEPA_TURNSTILE_SITEKEY,
            "metadata": {"action": "blocked"}}
    payload = {"clientKey": key, "task": task}
    if getattr(config, "CAPSOLVER_APP_ID", None):
        payload["appId"] = config.CAPSOLVER_APP_ID
    started = time.monotonic()
    try:
        with curl_requests.Session() as s:
            created = s.post(f"{CAPTCHA_API}/createTask", json=payload, timeout=30).json()
            task_id = created.get("taskId")
            if not task_id:
                raise KeepaBlocked(f"the solver refused the Turnstile task: {created.get('errorCode')} {created.get('errorDescription')}")
            while time.monotonic() - started < config.KEEPA_SOLVE_TIMEOUT:
                time.sleep(SOLVE_POLL)
                result = s.post(f"{CAPTCHA_API}/getTaskResult", json={"clientKey": key, "taskId": task_id}, timeout=30).json()
                if result.get("status") == "ready":
                    token = (result.get("solution") or {}).get("token")
                    if not token:
                        raise KeepaBlocked("the solver finished the Turnstile task but returned no token")
                    return token
                if result.get("errorId"):
                    raise KeepaBlocked(f"the Turnstile task failed: {result.get('errorCode')} {result.get('errorDescription')}")
    except KeepaUpstreamError:
        raise
    except Exception as e:
        raise KeepaUpstreamError(f"solver request failed: {type(e).__name__}: {e}")
    raise KeepaUpstreamError(f"the Turnstile solve timed out after {config.KEEPA_SOLVE_TIMEOUT}s")


def _mint_clearance(key, hint):
    """A new guest token, cleared: the page's first frame (user/settings
    getSettings, which issues the token), then one Turnstile token sent as
    {"path": "turnstile"}. On a connection of its own, closed when done: the
    clearance belongs to the token."""
    turnstile = _turnstile_token()
    channel = Channel()
    try:
        settings = channel.ask({"path": "user/settings", "type": "getSettings", "l": "keepa.com", "u": "/",
                                "ua": channel.sess.headers.get("user-agent") or "", "h": HOST_MARK})
        token = settings.get("token")
        if not token:
            raise KeepaUpstreamError(f"no guest token issued (status {settings.get('status')})")
        verdict = channel.ask({"path": "turnstile", "turnstile": turnstile})
        if verdict.get("status") != 200 or verdict.get("error"):
            raise KeepaBlocked(f"the site refused the Turnstile token (status {verdict.get('status')})")
        return identity_pool.Identity(token)
    finally:
        channel.close()


# Shared: one cleared token that every thread uses at once. A use is one
# unit; a call takes as many as it costs. A spare is minted in the
# background before the units run out, so a request rarely waits for a solve.
_clearances = identity_pool.Pool(
    "keepa-clearance", _mint_clearance,
    budget=lambda: config.KEEPA_CLEARANCE_UNITS,
    max_age=lambda: config.KEEPA_CLEARANCE_MAX_AGE,
    mint_ahead_at=lambda: config.KEEPA_CLEARANCE_MINT_AHEAD,
    mint_wait=lambda: config.KEEPA_SOLVE_TIMEOUT + 15,
    mints_per_minute=lambda: config.KEEPA_CLEARANCES_PER_MINUTE,
)


def _clearance(cost, replace=None):
    """The shared cleared token, charged `cost` units."""
    try:
        ident = _clearances.get(replace=replace)
        for _ in range(cost - 1):
            ident = _clearances.get()
        return ident
    except identity_pool.MintBudgetExceeded as e:
        raise KeepaBlocked(str(e))


def _ask_open(message, timeout=ANSWER_TIMEOUT):
    return _channel("open_channel").ask(message, timeout)


def _ask_cleared(message, cost, label, timeout=ANSWER_TIMEOUT):
    """One call on a cleared token. A checkHuman answer means the clearance
    is spent before our count said so: the token is replaced once (one mint
    for every thread that finds it spent) and the frame goes out again."""
    ident = _clearance(cost)
    answer = _channel("cleared_channel", ident.value).ask(message, timeout)
    if answer.get("checkHuman"):
        ident = _clearance(cost, replace=ident)
        answer = _channel("cleared_channel", ident.value).ask(dict(message, isRetry=True), timeout)
        if answer.get("checkHuman"):
            _clearances.retire(ident)
            raise KeepaBlocked(f"{label}: the site asks for a human check on a fresh clearance")
    return answer


# ---- the calls ------------------------------------------------------------------------------------------------

def product_frame(asin, domain_id, history):
    """The page's "compare international prices" frame. `stats` (any number
    of days) adds the stats block: current values, all-time lows, the
    out-of-stock share of 90 days."""
    return {"path": "product", "type": "casin", "offerPages": 0, "history": bool(history), "maxAge": 60 if history else 360,
            "refreshProduct": False, "domainId": domain_id, "asin": asin, "stats": 90}


def _page_product_frame(asin, domain_id):
    """The product page's own frame (cleared tokens only): the same record."""
    return {"path": "product", "type": "ws", "history": True, "domainId": domain_id, "asin": asin, "maxAge": 3,
            "refreshProduct": False, "stats": 90}


def fetch_product(asin, domain_id, history=False, refresh=False):
    """The product answer ({"basicProducts": [...]}, or {"variationProducts":
    [...]} for a parent ASIN). `history` adds the price series; `refresh`
    makes Keepa read Amazon again first (a cleared call, seconds)."""
    label = f"product {asin}"

    def once():
        frame = product_frame(asin, domain_id, history)
        if refresh:
            frame.update(refreshProduct=True, maxAge=0)
            return _checked(_ask_cleared(frame, COST_PRODUCT, label, REFRESH_TIMEOUT), label)
        answer = _ask_open(frame)
        if answer.get("checkHuman"):
            answer = _ask_cleared(_page_product_frame(asin, domain_id), COST_PRODUCT, label)
        return _checked(answer, label)
    return _retrying(once)


def fetch_products(asin, domain_ids):
    """The same ASIN on several marketplaces: one open frame each, written
    together on one connection. Answers align with `domain_ids`; a
    marketplace the site rejects yields its status-400 answer, not an error."""
    def once():
        answers = _channel("open_channel").ask_many([product_frame(asin, d, False) for d in domain_ids])
        if any(a.get("checkHuman") for a in answers):
            raise KeepaBlocked(f"product {asin}: the site asks for a human check on the open call")
        for answer in answers:
            if answer.get("status") not in (200, 400):
                _checked(answer, f"product {asin}")
        return answers
    return _retrying(once)


def variations_frame(asin, domain_id):
    """The search grid's "show variations" frame: for a parent ASIN every
    variation (no price series), for any other ASIN the product itself."""
    return {"path": "product", "type": "variation", "offerPages": 0, "maxAge": 1440, "history": False, "stats": 90,
            "refreshProduct": False, "domainId": domain_id, "asin": asin}


def fetch_variations(asin, domain_id):
    label = f"variations {asin}"

    def once():
        answer = _ask_open(variations_frame(asin, domain_id))
        if answer.get("checkHuman"):
            raise KeepaBlocked(f"{label}: the site asks for a human check on the open call")
        return _checked(answer, label)
    return _retrying(once)


def fetch_search(query, domain_id):
    """service/search productBasic: up to 20 rows (50 on amazon.com.au), one
    page; an ASIN or a UPC / EAN answers its one product."""
    frame = {"content": query, "domainId": str(domain_id), "path": "service/search", "type": "productBasic"}
    return _retrying(lambda: _checked(_ask_cleared(frame, COST_SEARCH, "search"), "search"))


def fetch_categories(query, domain_id):
    """service/search category: the categories whose name matches."""
    frame = {"path": "service/search", "type": "category", "domainId": domain_id, "content": query}
    return _retrying(lambda: _checked(_ask_cleared(frame, COST_CATEGORIES, "category search"), "category search"))


def deals_frame(deal_request):
    """The deals page's frame around a dealRequest (refs / api build the
    filters). The keys and their order are the page's own."""
    request = {
        "construct": None, "page": 0, "domainId": "1", "excludeCategories": [], "includeCategories": [], "priceTypes": [0],
        "deltaRange": [0, 2147483647], "deltaPercentRange": [0, 100], "salesRankRange": [-1, -1], "currentRange": [0, 2147483647],
        "minRating": -1, "isLowest": False, "isLowest90": False, "isLowestOffer": False, "isOutOfStock": False,
        "titleSearch": "", "isRangeEnabled": False, "isFilterEnabled": False, "filterErotic": True, "singleVariation": True,
        "hasReviews": False, "isPrimeExclusive": False, "mustHaveAmazonOffer": False, "mustNotHaveAmazonOffer": False,
        "sortType": 4, "dateRange": 1, "warehouseConditions": [1, 2, 3, 4, 5], "settings": {"viewTyp": 0},
        "perPage": DEALS_PER_PAGE, "salesRankDisplayGroup": [], "websiteDisplayGroupName": [], "websiteDisplayGroup": [], "type": [],
        "manufacturer": [], "brand": [], "brandStoreName": [], "brandStoreUrlName": [], "model": [], "color": [], "size": [],
        "unitType": [], "scent": [], "itemForm": [], "pattern": [], "style": [], "material": [], "itemTypeKeyword": [],
        "targetAudienceKeyword": [], "edition": [], "format": [], "author": [], "binding": [], "languages": [], "partNumber": [],
    }
    unknown = set(deal_request) - set(request)
    if unknown:
        raise ValueError(f"not a dealRequest key: {sorted(unknown)}")
    request.update(deal_request)
    return {"path": "service/deals", "type": "ws", "dealRequest": request}


def fetch_deals(deal_request):
    """service/deals: 150 rows a page, 0-based `page`; a page past the end
    answers status 200 with an empty `deals`."""
    frame = deals_frame(deal_request)
    return _retrying(lambda: _checked(_ask_cleared(frame, COST_DEALS, "deals"), "deals"))
