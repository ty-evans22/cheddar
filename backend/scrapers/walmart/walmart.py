"""
Walmart scraper — minimal search-only slice.

Targets Walmart's persisted GraphQL "Search" operation, replayed under a warmed
browser session (see walmart_session). This is the validation build: it pulls
the fields that are clean in the search response (id, name, price, image, stock)
and defers brand/size/upc/location/descriptors to a later pass.

Endpoint shape (from a live capture):
    GET https://www.walmart.com/orchestra/snb/graphql/Search/<hash>/search
        ?variables=<url-encoded JSON>
The persisted-query hash and operation name are in the PATH (not an `extensions`
param). Response: data.search.searchResult.itemStacks[].itemsV2[].

BRITTLE BITS (expect to refresh these when replays start failing):
  - SEARCH_HASH: the persisted-query hash. Changes whenever Walmart revises the
    Search query. When stale, the endpoint errors / stops returning data — grab
    the new hash from the request URL in devtools.
  - PLATFORM_VERSION: the x-o-platform-version. Drifts with frontend releases.
  - UA / sec-ch-ua coherence: we replay the warm browser's UA so it matches the
    cookies. curl_cffi's impersonation sets sec-ch-ua from its own Chrome
    profile, so pin that profile to the SAME Chrome major that nodriver launches
    (see DEFAULT_IMPERSONATE in utils/http). A UA/sec-ch-ua mismatch is the first
    thing to suspect if a warmed session starts getting challenged.
"""
import json
import secrets
from typing import Optional

from models.base import Product
from utils.http import browser_session
from utils.parser import match_brand
from scrapers.walmart.walmart_session import get_warmed_session, WarmedSession

SEARCH_HASH = "49f99afbb6fcc5adeb8df7d55ecc1f8fa3b7448f854f82a04088183137f03cd4"
SEARCH_URL = f"https://www.walmart.com/orchestra/snb/graphql/Search/{SEARCH_HASH}/search"

# Drifts with Walmart's frontend releases; refresh from devtools if needed.
PLATFORM_VERSION = "usweb-1.267.0-153ae89d7141df6b58359e210cffa1b0a852a3b5-6021401r"

# Scopes results to the warmed store for pickup (matches the captured request).
_FACET = "fulfillment_method:Pickup"


# ── request variables ──────────────────────────────────────────────────────
# The captured `variables` blob is huge but almost entirely static feature
# flags; we replicate it verbatim and only swap query/page/ps. If the gateway
# starts rejecting, diff this against a fresh capture — a new required flag is
# the usual culprit.

def _additional_query_params() -> dict:
    return {
        "hidden_facet": None, "translation": None, "isMoreOptionsTileEnabled": True,
        "isGenAiEnabled": True, "rootDimension": "", "altQuery": "", "selectedFilter": "",
        "neuralSearchSeeAll": False, "isModuleArrayReq": False,
        "enableGenericItemTileOptions": False, "isLMPBrowsePage": False,
    }


def _search_args(query: str) -> dict:
    return {"query": query, "cat_id": "", "prg": "mWeb", "facet": _FACET}


def _enable_flags() -> dict:
    return {
        "enableDesktopHighlights": False, "enableVolumePricing": False,
        "enableCopyBlock": False, "enableVariantCount": False, "enableSlaBadgeV2": True,
        "enableUnifiedProductFragment": False, "enableESSCarousel": False,
        "enableSearchBenefitsBanner": False,
    }


def _search_block(query: str, page: int, ps: int, limit: int) -> dict:
    block = {
        "id": "", "dealsId": "", "query": query, "nudgeContext": "", "page": page,
        "prg": "mWeb", "catId": "", "facet": _FACET, "sort": "best_match",
        "rawFacet": _FACET, "seoPath": "", "ps": ps, "limit": limit, "ptss": "",
        "trsp": "", "beShelfId": "", "recall_set": "", "module_search": "",
        "min_price": "", "max_price": "", "storeSlotBooked": "",
        "additionalQueryParams": _additional_query_params(),
        "searchArgs": _search_args(query),
    }
    block.update(_enable_flags())
    block["cat_id"] = ""
    block["_be_shelf_id"] = ""
    return block


def _build_variables(query: str, page: int = 1, ps: int = 40, limit: int = 40) -> dict:
    v = {
        "id": "", "dealsId": "", "query": query, "nudgeContext": "", "page": page,
        "prg": "mWeb", "catId": "", "facet": _FACET, "sort": "best_match",
        "rawFacet": _FACET, "seoPath": "", "ps": ps, "limit": limit, "ptss": "",
        "trsp": "", "beShelfId": "", "recall_set": "", "module_search": "",
        "min_price": "", "max_price": "", "storeSlotBooked": "",
        "additionalQueryParams": _additional_query_params(),
        "searchArgs": _search_args(query),
    }
    v.update(_enable_flags())
    v["fitmentFieldParams"] = {
        "powerSportEnabled": True, "dynamicFitmentEnabled": True,
        "extendedAttributesEnabled": True, "extendedAttributesV2Enabled": False,
        "fuelTypeEnabled": True,
    }
    v["fitmentSearchParams"] = _search_block(query, page, ps, limit)
    v["searchParams"] = _search_block(query, page, ps, limit)
    v.update({
        "fetchBadSplit": True, "enableFashionTopNav": False, "enableUnifiedSchema": False,
        "postProcessingVersion": 1, "version": "v1", "enableRelatedSearches": True,
        "enablePortableFacets": True, "enableFacetCount": True, "fetchMarquee": True,
        "fetchSkyline": True, "fetchGallery": False, "fetchSbaTop": True,
        "fetchDataV1": True, "fetchDataV2": False, "fungibilityEnabled": False,
        "enableAdsPromoData": False, "fetchDac": True, "tenant": "WM_GLASS",
        "enableMultiSave": False, "enableInStoreShelfMessage": False,
        "enableSellerType": False, "enableItemRank": False,
        "enableOptimisticWeightUpdate": False,
        "enableAdditionalSearchDepartmentAnalytics": True,
        "enableFulfillmentTagsEnhacements": False, "enableRxDrugScheduleModal": False,
        "enablePromoData": True, "enableSignInToSeePrice": False,
        "enablePromotionMessages": False, "enableDebugAnalyticsTags": False,
        "enableItemLimits": False, "enableCanAddToList": False,
        "enableIsFreeWarranty": False, "enableShopSimilarBottomSheet": False,
        "adsParams": {"fungibilityEnabled": False},
        "pageType": "SearchPage",
    })
    return v


def _build_headers(session: WarmedSession, query: str) -> dict:
    """
    Replay headers. The x-o-* / wm_* set is what routes the persisted op through
    Walmart's gateway. Per-request ids (correlation, traceparent, client-traceid)
    are generated fresh each call — reusing a captured id across requests is a
    bot tell. sec-ch-ua* are left to curl_cffi's impersonation (see module
    docstring re: keeping that profile aligned with the warmed Chrome).
    """
    corr = secrets.token_urlsafe(27)
    search_ref = (
        f"https://www.walmart.com/search?q={query.replace(' ', '+')}"
        f"&facet=fulfillment_method%3APickup"
    )
    return {
        "accept": "application/json",
        "accept-language": "en-US",
        "content-type": "application/json",
        "user-agent": session.user_agent,        # match the cookies' minting UA
        "referer": search_ref,
        "wm_page_url": search_ref,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "x-apollo-operation-name": "Search",
        "x-o-gql-query": "query Search",
        "x-o-bu": "WALMART-US",
        "x-o-mart": "B2C",
        "x-o-platform": "rweb",
        "x-o-platform-version": PLATFORM_VERSION,
        "x-o-segment": "oaoh",
        "x-o-ccm": "server",
        "tenant-id": "elh9ie",
        "wm_mp": "true",
        "wm_qos.correlation_id": corr,
        "x-o-correlation-id": corr,
        "wm-client-traceid": secrets.token_hex(16),
        "traceparent": f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01",
        "x-enable-server-timing": "1",
        "x-latency-trace": "1",
    }


def _search_result(resp):
    """
    Return the parsed JSON when the response is a real results payload, else None.
    Success = we actually got data.search back, not header equality. Walmart sends
    x-auth-status twice, so curl_cffi joins it to "passed, passed"; matching it
    exactly read success as a block. Checking for the data also separates a real
    block (no data.search) from a genuine empty result (data.search, no items).
    """
    if resp.status_code != 200:
        return None
    if "application/json" not in resp.headers.get("content-type", "").lower():
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    if isinstance(data, dict) and (data.get("data") or {}).get("search"):
        return data
    return None


async def _search_once(query: str, session: WarmedSession, limit: int):
    variables = _build_variables(query, page=1, ps=limit, limit=limit)
    async with browser_session(proxy=session.proxy_url) as client:
        return await client.get(
            SEARCH_URL,
            params={"variables": json.dumps(variables, separators=(",", ":"))},
            headers=_build_headers(session, query),
            cookies=session.cookies,
            allow_redirects=False,   # a redirect here means a challenge, not results
        )


async def search_walmart_store(
    store_name: str,
    query: str,
    limit: int = 40,
) -> list[Product]:
    """
    Search Walmart and return Products. Re-warms once if the first replay is
    blocked, then gives up rather than hammering (which only burns the session
    score further).
    """
    session = await get_warmed_session()
    resp = await _search_once(query, session, limit)
    data = _search_result(resp)

    if data is None:
        print(
            f"Walmart replay not usable (status={resp.status_code}, "
            f"x-auth-status={resp.headers.get('x-auth-status')}, "
            f"content-type={resp.headers.get('content-type')}); re-warming once"
        )
        print(f"  body[:200]: {resp.text[:200]!r}")
        session = await get_warmed_session(force=True)
        resp = await _search_once(query, session, limit)
        data = _search_result(resp)
        if data is None:
            print("Walmart still not usable after re-warm; giving up for this query")
            return []

    raw_items = _extract_items(data)
    if not raw_items:
        print(f"No Walmart items for '{query}' (x-gql-status={resp.headers.get('x-gql-status')})")
        return []

    brands = _extract_brands(data)
    products = _parse_walmart_items(raw_items[:limit], store_name, brands)
    agg = (((data.get("data") or {}).get("search") or {}).get("searchResult") or {}).get("aggregatedCount")
    matched = sum(1 for p in products if p.brand)
    print(f"Parsed {len(products)} Walmart products for '{query}' "
          f"(aggregatedCount={agg}, {len(brands)} brands in facet, {matched} matched)")
    return products


# Stack meta.subType values that are genuine query matches, not recommendation /
# "customers also bought" / sponsored carousels. From the capture, the real
# results stack is stackType STORE_LED / subType EXACT_MATCH. Widen this if the
# diagnostic below shows real results arriving under another subType.
_RESULT_SUBTYPES = {"EXACT_MATCH"}


def _extract_items(data, drop_sponsored=True):
    try:
        stacks = data["data"]["search"]["searchResult"]["itemStacks"]
        for s in stacks or []:
            m = s.get("meta") or {}
            print(f"[stack] type={m.get('stackType')} subType={m.get('subType')} "
                f"count={m.get('totalItemCount')} items={len(s.get('itemsV2') or [])} title={m.get('title')!r}")
    except (KeyError, TypeError):
        return []
    items, seen = [], set()
    for stack in stacks or []:
        meta = stack.get("meta") or {}
        if meta.get("subType") not in _RESULT_SUBTYPES:
            continue  # recommendation / related / sponsored stack
        for item in stack.get("itemsV2") or []:
            if item.get("__typename") != "Product":
                continue
            if drop_sponsored and item.get("sponsoredProduct"):
                continue
            key = item.get("usItemId") or item.get("id")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            items.append(item)
    return items

def _extract_brands(data: dict) -> list[str]:
    """
    Brand vocabulary from the SearchSortFilterModule's brand facet:
        data.contentLayout.modules[type=SearchSortFilterModule]
            .configs.topNavFacets[type=brand].values[].name
    Returns [] when the facet is absent (brand resolution is then skipped and the
    name-parse fallback takes over). valueDisplayLimit is 50, so for most grocery
    queries this is the complete brand set, not a sample.
    """
    try:
        modules = data["data"]["contentLayout"]["modules"]
    except (KeyError, TypeError):
        return []
    for module in modules or []:
        if module.get("type") != "SearchSortFilterModule":
            continue
        facets = (module.get("configs") or {}).get("topNavFacets") or []
        for facet in facets:
            if facet.get("type") == "brand" or (facet.get("name") or "").lower() == "brand":
                names = []
                for v in facet.get("values") or []:
                    label = v.get("name") or v.get("title") or v.get("id")
                    if label:
                        names.append(label)
                return names
    return []

def _parse_walmart_items(raw_items: list[dict], store_name: str, brands: list[str]) -> list[Product]:
    products = []
    for item in raw_items:
        product = _parse_walmart_item(item, store_name, brands)
        if product:
            products.append(product)
    return products


def _parse_walmart_item(item: dict, store_name: str, brands: list[str]) -> Optional[Product]:
    """
    Map one search item onto Product. Deferred fields (brand/size/upc/location/
    descriptors) are intentionally left empty in this slice.
    """
    try:
        name = item.get("name") or ""
        if not name:
            return None

        price_info = item.get("priceInfo") or {}
        current = (price_info.get("currentPrice") or {}).get("price")
        was_obj = price_info.get("listPrice") or price_info.get("wasPrice") or {}
        was = was_obj.get("price") if was_obj else None

        price = _to_float(current)
        regular_price = _to_float(was) if was is not None else price
        on_sale = (
            price is not None
            and regular_price is not None
            and price < regular_price
        )

        image_url = (item.get("imageInfo") or {}).get("thumbnailUrl")
        avail = (item.get("availabilityStatusV2") or {}).get("value", "IN_STOCK")

        return Product(
            id=str(item.get("usItemId") or item.get("id")),
            name=name,
            brand=match_brand(name, brands),
            size=None,                          # derive from unitPrice / name later
            price=price,
            regular_price=regular_price,
            on_sale=on_sale,
            sale_conditions=None,
            image_url=image_url,
            store=store_name,
            store_location=None,                # PDP-only, fetched lazily later
            in_stock=avail != "OUT_OF_STOCK",
            upc=None,                           # PDP-only
            descriptors=[],
        )
    except Exception as e:
        print(f"Error parsing item: {e}")
        return None


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


async def search_walmart(
    query: str,
) -> list[Product]:
    return await search_walmart_store(store_name="Walmart", query=query)