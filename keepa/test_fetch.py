"""Offline tests for the Keepa transport: the frame codec, the error
taxonomy, the open product call and its cleared fallback, the shared pool of
cleared guest tokens (one solve for every thread, units per call, one
replacement when the site asks again) and the retry on a fresh connection.
Fake connections and a fake solver; no network.

    python -m pytest keepa/test_fetch.py -q
"""
import json
import os
import sys
import threading
import zlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from keepa import fetch  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
CHECK = {"checkHuman": True, "status": 200}
PRODUCT = {"basicProducts": [{"asin": "B0CNY1F31S", "productType": 0, "title": "x"}], "status": 200}


def fixture(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return json.load(f)


class FakeSite:
    """What the fake connections talk to: `answer(channel, message)` decides."""

    def __init__(self, answer):
        self.answer = answer
        self.channels = []
        self.solves = 0
        self.lock = threading.Lock()

    def channel(self, token=None):
        ch = FakeChannel(self, token)
        with self.lock:
            self.channels.append(ch)
        return ch


class FakeChannel:
    def __init__(self, site, token):
        self.site, self.token, self.sent, self.closed = site, token, [], False
        self.last_used = fetch.time.monotonic()
        self.sess = type("S", (), {"headers": {"user-agent": "UA"}})()

    def ask_many(self, messages, timeout=fetch.ANSWER_TIMEOUT):
        self.sent.extend(messages)
        out = []
        for message in messages:
            answer = self.site.answer(self, message)
            if isinstance(answer, Exception):
                raise answer
            out.append(answer)
        return out

    def ask(self, message, timeout=fetch.ANSWER_TIMEOUT):
        return self.ask_many([message], timeout)[0]

    def close(self):
        self.closed = True


@pytest.fixture
def site(monkeypatch):
    """A site where a token is cleared for `units` calls after a Turnstile frame."""
    state = {"units": {}, "issued": 0}

    def answer(channel, message):
        path = message.get("path")
        if path == "user/settings":
            state["issued"] += 1
            channel.token = f"guest-{state['issued']}"
            return {"status": 401, "token": channel.token, "tokenIssued": True}
        if path == "turnstile":
            state["units"][channel.token] = fake.units_per_clearance
            return {"status": 200}
        gated = path in ("service/search", "service/deals") or message.get("refreshProduct") or message.get("type") == "ws"
        if path == "product" and not gated:
            return fake.open_answer(message) if fake.open_answer else PRODUCT
        cost = fake.cost_of(message)
        left = state["units"].get(channel.token, 0)
        if left < cost:
            state["units"][channel.token] = 0
            return dict(CHECK)
        state["units"][channel.token] = left - cost
        return {"status": 200, "searchProducts": [], "deals": {}, "basicProducts": PRODUCT["basicProducts"]}

    fake = FakeSite(answer)
    fake.units_per_clearance = 100
    fake.cost_of = lambda message: {"service/search": 10, "service/deals": 3}.get(message.get("path"), 1)
    fake.open_answer = None
    fake.state = state

    def solve():
        with fake.lock:
            fake.solves += 1
        return "turnstile-token"

    monkeypatch.setattr(fetch, "Channel", fake.channel)
    monkeypatch.setattr(fetch, "_turnstile_token", solve)
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    monkeypatch.setattr(config, "KEEPA_CLEARANCE_MINT_AHEAD", None)      # no background solve unless a test asks
    fetch._clearances.reset()
    fetch._drop_channels()
    yield fake
    fetch._clearances.reset()
    fetch._drop_channels()


# ---- frames -----------------------------------------------------------------------------------------------------

def test_pack_is_zlib_json():
    frame = fetch.pack({"path": "product", "asin": "B0CNY1F31S", "id": 7})
    assert json.loads(zlib.decompress(frame)) == {"path": "product", "asin": "B0CNY1F31S", "id": 7}


def test_unpack_plain_and_zstd():
    assert fetch.unpack(b'{"status":108,"guest":1}') == {"status": 108, "guest": 1}
    assert fetch.unpack('{"status":200}') == {"status": 200}
    try:
        from compression import zstd
        packed = zstd.compress(b'{"id": 3, "status": 200}')
    except ImportError:
        zstandard = pytest.importorskip("zstandard")
        packed = zstandard.ZstdCompressor().compress(b'{"id": 3, "status": 200}')
    assert packed[:4] == fetch.ZSTD_MAGIC
    assert fetch.unpack(packed) == {"id": 3, "status": 200}


@pytest.mark.parametrize("frame", [b"\xe8\x03", b"not json", b"[1, 2]", fetch.ZSTD_MAGIC + b"broken"])
def test_unpack_refuses_junk(frame):
    with pytest.raises(fetch.KeepaUpstreamError):
        fetch.unpack(frame)


def test_host_mark_is_the_page_s_own():
    import base64
    assert zlib.decompress(base64.b64decode(fetch.HOST_MARK), -15) == b"keepa.com"


# ---- status -> errors -------------------------------------------------------------------------------------------

def test_checked_statuses():
    assert fetch._checked({"status": 200, "x": 1}, "p") == {"status": 200, "x": 1}
    with pytest.raises(fetch.KeepaBadRequest):
        fetch._checked(fixture("bad_request_400.json"), "product B0")
    for status in (402, 403, 429):
        with pytest.raises(fetch.KeepaBlocked):
            fetch._checked({"status": status}, "p")
    for status in (500, 505, 404, None):
        with pytest.raises(fetch.KeepaUpstreamError) as e:
            fetch._checked({"status": status}, "p")
        assert not isinstance(e.value, fetch.KeepaBlocked)


def test_error_classes_map_to_the_shared_taxonomy():
    from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
    assert issubclass(fetch.KeepaBlocked, Blocked) and issubclass(fetch.KeepaBlocked, fetch.KeepaUpstreamError)
    assert issubclass(fetch.KeepaUpstreamError, UpstreamError)
    assert issubclass(fetch.KeepaBadRequest, BadRequest) and issubclass(fetch.KeepaNotFound, NotFound)
    assert not issubclass(fetch.KeepaCaptchaNotConfigured, UpstreamError)


# ---- the frames the calls send ----------------------------------------------------------------------------------

def test_product_frames():
    assert fetch.product_frame("B0CNY1F31S", 1, False) == {
        "path": "product", "type": "casin", "offerPages": 0, "history": False, "maxAge": 360, "refreshProduct": False,
        "domainId": 1, "asin": "B0CNY1F31S", "stats": 90}
    with_history = fetch.product_frame("B0CNY1F31S", 3, True)
    assert with_history["history"] is True and with_history["maxAge"] == 60 and with_history["domainId"] == 3
    assert fetch.variations_frame("B0HQNZW4FH", 1) == {
        "path": "product", "type": "variation", "offerPages": 0, "maxAge": 1440, "history": False, "stats": 90,
        "refreshProduct": False, "domainId": 1, "asin": "B0HQNZW4FH"}


def test_deals_frame_is_the_page_s_request():
    frame = fetch.deals_frame({"page": 2, "domainId": "3", "priceTypes": [18]})
    request = frame["dealRequest"]
    assert (frame["path"], frame["type"]) == ("service/deals", "ws")
    assert (request["page"], request["domainId"], request["priceTypes"], request["perPage"]) == (2, "3", [18], 150)
    assert request["isRangeEnabled"] is False and request["isFilterEnabled"] is False
    assert request["filterErotic"] is True and request["singleVariation"] is True
    assert len(request) == 54                    # every key the page sends, in its order (the 24 empty attribute filters included)
    assert list(request)[:5] == ["construct", "page", "domainId", "excludeCategories", "includeCategories"]
    with pytest.raises(ValueError):
        fetch.deals_frame({"perpage": 10})


# ---- the open product call --------------------------------------------------------------------------------------

def test_an_open_product_needs_no_clearance(site):
    assert fetch.fetch_product("B0CNY1F31S", 1) == PRODUCT
    assert site.solves == 0 and len(site.channels) == 1 and site.channels[0].token is None
    assert site.channels[0].sent[0]["type"] == "casin"
    fetch.fetch_product("B0CNY1YVDD", 1)
    assert len(site.channels) == 1               # the thread keeps its connection


def test_an_open_product_that_is_gated_falls_back_to_the_page_s_frame(site):
    site.open_answer = lambda message: dict(CHECK)
    answer = fetch.fetch_product("B0CNY1F31S", 1, history=True)
    assert answer["basicProducts"] and site.solves == 1
    cleared = [ch for ch in site.channels if ch.token and any(m.get("type") == "ws" for m in ch.sent)]
    assert len(cleared) == 1 and cleared[0].sent[-1]["asin"] == "B0CNY1F31S"


def test_refresh_is_a_cleared_call(site):
    fetch.fetch_product("B0CNY1F31S", 1, refresh=True)
    sent = [m for ch in site.channels for m in ch.sent if m.get("path") == "product"]
    assert sent == [dict(fetch.product_frame("B0CNY1F31S", 1, False), refreshProduct=True, maxAge=0)]
    assert site.solves == 1


def test_products_on_several_marketplaces_ride_one_connection(site):
    site.open_answer = lambda message: {"status": 400} if message["domainId"] == 7 else PRODUCT
    answers = fetch.fetch_products("B0CNY1F31S", [1, 7, 3])
    assert [a["status"] for a in answers] == [200, 400, 200]          # a refused marketplace is an answer, not an error
    assert len(site.channels) == 1 and [m["domainId"] for m in site.channels[0].sent] == [1, 7, 3]


def test_status_400_is_final(site):
    site.open_answer = lambda message: fixture("bad_request_400.json")
    with pytest.raises(fetch.KeepaBadRequest):
        fetch.fetch_product("B0CNY1F3", 1)
    assert len(site.channels) == 1               # not retried


# ---- clearances -------------------------------------------------------------------------------------------------

def test_one_clearance_serves_many_calls(site):
    for n in range(5):
        fetch.fetch_search(f"word {n}", 1)
    assert site.solves == 1
    assert fetch._clearances.stats()["left"]["use"] == config.KEEPA_CLEARANCE_UNITS - 5 * fetch.COST_SEARCH


def test_the_budget_moves_on_to_a_new_clearance(site, monkeypatch):
    monkeypatch.setattr(config, "KEEPA_CLEARANCE_UNITS", 30)
    for n in range(5):                           # 5 x 12 units against 30 a clearance
        fetch.fetch_search(f"word {n}", 1)
    assert site.solves == 2
    assert not [m for ch in site.channels for m in ch.sent if m.get("isRetry")]      # and the site never had to refuse


def test_a_spent_clearance_is_replaced_once_and_the_frame_resent(site):
    site.units_per_clearance = 25                # the site allows less than we count on
    for n in range(4):
        assert "searchProducts" in fetch.fetch_search(f"word {n}", 1)
    assert site.solves == 2
    resent = [m for ch in site.channels for m in ch.sent if m.get("isRetry")]
    assert len(resent) == 1 and resent[0]["content"] == "word 2"


def test_a_refusal_on_a_fresh_clearance_is_a_block(site):
    site.units_per_clearance = 0
    with pytest.raises(fetch.KeepaBlocked):
        fetch.fetch_deals({"page": 0})
    assert site.solves <= 2 * config.MAX_RETRIES


def test_threads_share_one_clearance(site):
    errors = []

    def work(n):
        try:
            fetch.fetch_deals({"page": n})
        except Exception as e:               # pragma: no cover
            errors.append(e)
        finally:
            fetch._drop_channels()

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and site.solves == 1
    assert len({ch.token for ch in site.channels if ch.token and any(m.get("path") == "service/deals" for m in ch.sent)}) == 1


def test_a_clearance_is_minted_on_a_connection_of_its_own(site):
    fetch.fetch_search("air fryer", 1)
    minted = [ch for ch in site.channels if any(m.get("path") == "turnstile" for m in ch.sent)]
    assert len(minted) == 1 and minted[0].closed
    assert [m["path"] for m in minted[0].sent] == ["user/settings", "turnstile"]
    assert minted[0].sent[0]["h"] == fetch.HOST_MARK and minted[0].sent[1]["turnstile"] == "turnstile-token"


def test_no_solver_key_is_a_configuration_error(monkeypatch):
    monkeypatch.setattr(config, "CAPSOLVER_API_KEY", "")
    with pytest.raises(fetch.KeepaCaptchaNotConfigured):
        fetch._turnstile_token()


# ---- retries ----------------------------------------------------------------------------------------------------

def test_a_broken_connection_is_retried_on_a_fresh_one(site):
    calls = {"n": 0}

    def flaky(message):
        calls["n"] += 1
        return fetch.KeepaUpstreamError("websocket read failed") if calls["n"] == 1 else PRODUCT

    site.open_answer = flaky
    assert fetch.fetch_product("B0CNY1F31S", 1) == PRODUCT
    assert len(site.channels) == 2 and site.channels[0].closed


def test_retries_run_out(site):
    site.open_answer = lambda message: fetch.KeepaUpstreamError("down")
    with pytest.raises(fetch.KeepaUpstreamError):
        fetch.fetch_variations("B0HQNZW4FH", 1)
    assert len(site.channels) == config.MAX_RETRIES


def test_an_idle_connection_is_opened_anew(site, monkeypatch):
    fetch.fetch_product("B0CNY1F31S", 1)
    site.channels[0].last_used -= config.KEEPA_CHANNEL_MAX_IDLE + 1
    fetch.fetch_product("B0CNY1F31S", 1)
    assert len(site.channels) == 2 and site.channels[0].closed


@pytest.mark.skipif(not config.gateway_exit("gateway"), reason="no gateway exit here (the open-source kit's config)")
def test_the_gateway_exit_cannot_carry_a_websocket(monkeypatch):
    monkeypatch.setattr(config, "KEEPA_EXIT", "gateway")
    with pytest.raises(fetch.KeepaUpstreamError):
        fetch_channel = fetch.Channel.__new__(fetch.Channel)
        fetch.Channel.__init__(fetch_channel)
