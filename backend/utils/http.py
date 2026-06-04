"""
Shared HTTP client for all scrapers.
"""
from typing import Optional
from curl_cffi.requests import AsyncSession

# "chrome" tracks the latest Chrome profile curl_cffi ships. Pin a specific
# version (e.g. "chrome131") for reproducibility, and refresh profiles
# periodically with the `curl-cffi update` CLI (curl_cffi >= 0.15.1).
DEFAULT_IMPERSONATE = "chrome"
DEFAULT_TIMEOUT = 20.0

def browser_session(proxy: Optional[str] = None, **kwargs) -> AsyncSession:
    """
    Create an AsyncSession that impersonates a real browser.

    Args:
        proxy: Optional proxy URL ("scheme://user:pass@host:port"). Applied to
            both http and https. For the Walmart scraper this MUST be the same
            sticky residential session used to warm the browser, or PerimeterX
            will invalidate the warmed _px3 cookie (it's bound to the exit IP).
 
    Notes:
      - Let impersonation own the fingerprint headers (user-agent, sec-ch-ua*).
        Only pass request-specific headers (content-type, referer, custom x-*).
        Hand-setting a user-agent here risks a mismatch with the impersonated
        TLS fingerprint, which defeats the purpose.
      - The session keeps its own cookie jar, so cookies set on one request are
        reused on the next within the same `async with` block.
    """
    kwargs.setdefault("impersonate", DEFAULT_IMPERSONATE)
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    if proxy:
        kwargs.setdefault("proxies", {"http": proxy, "https": proxy})
    return AsyncSession(**kwargs)