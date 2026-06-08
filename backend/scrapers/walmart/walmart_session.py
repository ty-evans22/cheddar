"""
Walmart session warming.

Walmart runs two anti-bot layers in series:
  - Akamai Bot Manager (edge WAF): inspects the TLS/JA3-JA4 fingerprint, HTTP/2
    frame ordering, header order/casing, and the _abck / ak_bmsc cookies.
  - PerimeterX / HUMAN: a JavaScript behavioral layer that mints a _px3 cookie
    from canvas/WebGL/audio fingerprints and mouse/scroll biometrics, and
    surfaces a "Press & Hold" challenge when a session's score drops.

curl_cffi clears the Akamai TLS layer on its own (that's the whole reason we
impersonate Chrome), but it cannot execute the PerimeterX JavaScript, so it can
never mint a valid _px3 by itself. This module "warms" a session with a real
(stealth) Chrome via nodriver so PerimeterX actually runs and validates the
cookies, then hands the resulting cookies + headers to curl_cffi for the fast
subsequent search/item requests.

Coherence rules that keep the warmed cookies valid under curl_cffi:
  1. Same IP. The browser warm and every curl_cffi replay MUST go through the
     exact same residential IP (a sticky proxy session). PerimeterX binds _px3
     to the IP; rotate the IP and the cookie dies. We achieve this by baking one
     generated sticky-session id into the proxy URL and reusing it for both.
  2. Same browser shape. We warm with Chrome (nodriver) and replay with
     curl_cffi impersonate="chrome", so the TLS/UA fingerprint stays consistent.
  3. Store context. Walmart prices are store-specific and the chosen store is
     carried in cookies, so the store is selected during the warm.

Re-warm when the cookie TTL expires or when a replay comes back challenged
(see is_challenged() in walmart.py).
"""
import os
import json
import time
import uuid
import asyncio
import random
from dataclasses import dataclass, field
from typing import Optional
import nodriver as uc

# A proxy URL template for the residential pool. Use {session} as a placeholder
# for the sticky-session id; we substitute one generated id so the warm browser
# and the curl_cffi replays land on the same exit IP. Example:
#   http://user-session-{session}:pass@gate.provider.com:7777
WALMART_PROXY_TEMPLATE = os.getenv("WALMART_PROXY_TEMPLATE")

# The Walmart store whose local pricing we want. Selected during the warm.
WALMART_STORE_ID = os.getenv("WALMART_STORE_ID")

# How long a warmed session is trusted before we re-warm. _px3 lives longer than
# this, but the behavioral score decays, so we refresh conservatively.
SESSION_TTL_SECONDS = float(os.getenv("WALMART_SESSION_TTL", "900"))  # 15 min

# How long to dwell on the page after load so PerimeterX scoring settles and the
# _abck cookie transitions to a validated state before we read cookies.
WARM_DWELL_SECONDS = 6.0
WARM_NAV_TIMEOUT = 45.0

_SESSION_FILE = os.getenv("WALMART_SESSION_FILE", ".walmart_session.json")

def _save_session(s: "WarmedSession") -> None:
    try:
        with open(_SESSION_FILE, "w") as f:
            json.dump({"cookies": s.cookies, "user_agent": s.user_agent,
                       "proxy_url": s.proxy_url, "created_at": s.created_at}, f)
    except Exception as e:
        print(f"Could not persist warmed session: {e}")

def _load_session() -> Optional["WarmedSession"]:
    try:
        with open(_SESSION_FILE) as f:
            d = json.load(f)
    except (FileNotFoundError, ValueError):
        return None
    s = WarmedSession(cookies=d.get("cookies", {}), user_agent=d.get("user_agent", ""),
                      proxy_url=d.get("proxy_url"), created_at=d.get("created_at", 0))
    return None if s.is_expired() else s

def _clear_session() -> None:
    try:
        os.remove(_SESSION_FILE)
    except FileNotFoundError:
        pass


@dataclass
class WarmedSession:
    """
    A validated browser session captured for replay by curl_cffi.

    Members:
        cookies (dict): name -> value for all cookies set during the warm
            (includes _px3, _abck, ak_bmsc, and the store-selection cookies).
        user_agent (str): the exact UA string Chrome reported; replayed verbatim.
        proxy_url (str): the fully-resolved sticky proxy URL (session id baked in)
            that curl_cffi must reuse so replays share the warm's exit IP.
        created_at (float): monotonic-ish wall time the session was minted.
    """
    cookies: dict = field(default_factory=dict)
    user_agent: str = ""
    proxy_url: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > SESSION_TTL_SECONDS


# Cache one warmed session per process, mirroring the _sessions pattern in the
# other scrapers. A lock prevents a thundering herd of concurrent warms when the
# session expires under parallel /search load.
_warmed: Optional[WarmedSession] = None
_warm_lock = asyncio.Lock()


def _resolve_proxy_url() -> Optional[str]:
    """
    Resolve the proxy template into a concrete sticky-session URL by generating
    one session id and substituting it. If the template has no {session}
    placeholder we return it as-is (e.g. the provider does stickiness another
    way, or you're testing without a proxy -> None).
    """
    if not WALMART_PROXY_TEMPLATE:
        return None
    sticky_id = uuid.uuid4().hex[:16]
    return WALMART_PROXY_TEMPLATE.replace("{session}", sticky_id)


def _split_proxy(proxy_url: str) -> tuple[str, Optional[str], Optional[str]]:
    """
    Split "scheme://user:pass@host:port" into ("scheme://host:port", user, pass).

    Chrome's --proxy-server flag does not accept inline credentials, so we pass
    only the host:port to the flag and feed the credentials to a CDP auth
    handler. If your residential provider supports IP-allowlist auth instead,
    point WALMART_PROXY_TEMPLATE at a credential-free URL and you can drop the
    auth handler entirely.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(proxy_url)
    hostport = f"{parts.scheme}://{parts.hostname}"
    if parts.port:
        hostport += f":{parts.port}"
    return hostport, parts.username, parts.password


async def _select_store(page) -> None:
    """
    Select WALMART_STORE_ID so local pricing/stock is carried in the session.

    TODO(live-verify): the exact store-selection mechanism must be confirmed
    against a live page. Two known approaches, pick whichever your captured
    flow shows:
      (a) Navigate to a store-scoped URL and let Walmart set the location
          cookie, e.g. https://www.walmart.com/store/{WALMART_STORE_ID}
      (b) Drive the "Pickup/Delivery" location modal in the header and choose
          the store, then confirm.
    The resulting cookie (historically assortmentStoreId / a locationData blob)
    is what makes prices store-specific. Confirm the cookie name and that it
    survives into the curl_cffi replay.
    """
    if not WALMART_STORE_ID:
        print("WALMART_STORE_ID not set; proceeding with default (national) pricing")
        return
    try:
        await page.get(f"https://www.walmart.com/store/{WALMART_STORE_ID}")
        await page.sleep(3.0)
        print(f"Store {WALMART_STORE_ID} selected for session")
    except Exception as e:
        print(f"Store selection step failed (continuing): {e}")


async def _warm() -> WarmedSession:
    """
    Drive a real Chrome through the sticky residential proxy, let Akamai +
    PerimeterX run, select the store, and capture the validated cookies + UA.
    """
    proxy_url = _resolve_proxy_url()
    browser_args = ["--lang=en-US"]
    proxy_user = proxy_pass = None

    if proxy_url:
        hostport, proxy_user, proxy_pass = _split_proxy(proxy_url)
        browser_args.append(f"--proxy-server={hostport}")
        print(f"Warming Walmart session via proxy {hostport}")
    else:
        print("Warming Walmart session with no proxy (local IP)")

    # headless=False is materially harder for PerimeterX to flag than headless.
    # Run behind a virtual display (xvfb) on a headless server.
    browser = await uc.start(headless=False, browser_args=browser_args)
    try:
        # Authenticated proxies: answer Chrome's proxy auth challenge over CDP.
        # (Skip this block entirely if you use IP-allowlist proxy auth.)
        if proxy_user and proxy_pass:
            await _install_proxy_auth(browser, proxy_user, proxy_pass)

        page = await browser.get("https://www.walmart.com")

        # Add a couple of scrolls and randomized dwells to give PerimeterX more behavioral signals
        await page.evaluate("window.scrollTo(0, 500)")
        await page.sleep(random.uniform(0.8, 1.8))
        await page.evaluate("window.scrollTo(0, 1200)")
        await page.sleep(random.uniform(1.2, 2.5))

        await _select_store(page)

        # Read the UA Chrome actually presented; curl_cffi will replay it verbatim.
        user_agent = await page.evaluate("navigator.userAgent")

        # Export every cookie the session accumulated.
        raw_cookies = await browser.cookies.get_all()
        cookies = {c.name: c.value for c in raw_cookies}

        if "_px3" not in cookies:
            # Not fatal (some sessions validate via _pxhd/_pxvid only), but log it
            # — a missing _px3 is the usual reason replays start getting challenged.
            print("WARNING: warm completed without a _px3 cookie; replays may be challenged")

        print(f"Warmed Walmart session: {len(cookies)} cookies, UA captured")
        return WarmedSession(
            cookies=cookies,
            user_agent=user_agent or "",
            proxy_url=proxy_url,
        )
    finally:
        browser.stop()


async def _install_proxy_auth(browser, user: str, password: str) -> None:
    """
    Register a CDP Fetch.authRequired handler so Chrome answers the proxy's
    Basic auth challenge. This uses nodriver's CDP passthrough; method/handler
    names are version-sensitive, so verify against your installed nodriver
    release. If this proves brittle, IP-allowlist proxy auth removes the need
    for it altogether.
    """
    from nodriver import cdp

    tab = browser.main_tab

    async def _auth_handler(event: cdp.fetch.AuthRequired):
        await tab.send(
            cdp.fetch.continue_with_auth(
                request_id=event.request_id,
                auth_challenge_response=cdp.fetch.AuthChallengeResponse(
                    response="ProvideCredentials",
                    username=user,
                    password=password,
                ),
            )
        )

    async def _request_paused(event: cdp.fetch.RequestPaused):
        await tab.send(cdp.fetch.continue_request(request_id=event.request_id))

    tab.add_handler(cdp.fetch.AuthRequired, _auth_handler)
    tab.add_handler(cdp.fetch.RequestPaused, _request_paused)
    await tab.send(cdp.fetch.enable(handle_auth_requests=True))


async def get_warmed_session(force: bool = False) -> WarmedSession:
    """
    Return a valid warmed session, minting a fresh one if missing/expired or if
    force=True (call with force=True after a replay comes back challenged).
    Serialized so concurrent /search requests share one warm instead of each
    spawning a browser.
    """
    global _warmed
    async with _warm_lock:
        if force:
            print("[session] forced re-warm: clearing memory + disk")
            _clear_session()
            _warmed = None
        if _warmed and not _warmed.is_expired():
            age = int(time.time() - _warmed.created_at)
            print(f"[session] reuse in-memory (age {age}s / TTL {int(SESSION_TTL_SECONDS)}s)")
            return _warmed
        disk = _load_session()
        if disk:
            _warmed = disk
            age = int(time.time() - disk.created_at)
            print(f"[session] loaded from disk (age {age}s / TTL {int(SESSION_TTL_SECONDS)}s)")
            return _warmed
        print("[session] WARMING fresh session")
        _warmed = await _warm()
        _save_session(_warmed)
        return _warmed