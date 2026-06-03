"""
Shared HTTP client for all scrapers.
"""
from curl_cffi.requests import AsyncSession

# "chrome" tracks the latest Chrome profile curl_cffi ships. Pin a specific
# version (e.g. "chrome131") for reproducibility, and refresh profiles
# periodically with the `curl-cffi update` CLI (curl_cffi >= 0.15.1).
DEFAULT_IMPERSONATE = "chrome"
DEFAULT_TIMEOUT = 20.0

def browser_session(**kwargs) -> AsyncSession:
    """
    Create an AsyncSession that impersonates a real browser.
 
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
    return AsyncSession(**kwargs)