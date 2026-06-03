import uuid
from typing import Optional
from curl_cffi.requests import AsyncSession
from models.base import Product
from utils.http import browser_session

SEARCH_URL = "https://www.hy-vee.com/aisles-online/api/search/products"

HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": "https://www.hy-vee.com",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

_sessions: dict[str, dict] = {}

async def _get_session(client: AsyncSession, domain: str = "www.hy-vee.com") -> dict:
    """
    Load the Aisles Online search page once to pick up session/anti-bot cookies
    (hyveeSessionId, __cf_bm, etc.) that the API endpoint expects.
    """
    if domain in _sessions:
        return _sessions[domain]
 
    print(f"Initializing session for {domain}...")
    resp = await client.get(
        f"https://{domain}/aisles-online/search",
        headers={
            "accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "accept-language": "en-US,en;q=0.9",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
        },
        allow_redirects=True,
    )
    cookies = dict(resp.cookies)
    print(f"Session cookies obtained for {domain}: {list(cookies.keys())}")
    _sessions[domain] = cookies
    return cookies

async def search_hyvee_store(
    store_id: str | int,
    store_name: str,
    query: str,
    limit: int = 60,
) -> list[Product]:
    """
    Search a specific Hy-Vee store and return a list of Products.
 
    Note: the response includes meta.pagination (total / pagesTotal). This
    fetches a single page of up to `limit` items; loop pageNumber if you ever
    need the full result set.
    """
    payload = {
        "pageNumber": 1,
        "pageSize": limit,
        "pageViewId": str(uuid.uuid4()),
        "searchFilters": [],
        "searchTerm": query,
        "sortDirection": "RELEVANCE",
        "storeId": int(store_id),
    }
 
    async with browser_session() as client:
        session_cookies = await _get_session(client)
 
        resp = await client.post(
            SEARCH_URL,
            json=payload,
            headers={
                **HEADERS,
                "referer": f"https://www.hy-vee.com/aisles-online/search?search={query.replace(' ', '+')}",
                "x-hy-vee-correlation-id": str(uuid.uuid4()),
            },
            cookies=session_cookies,
        )
        resp.raise_for_status()
        data = resp.json()
 
    raw_items = data.get("results", [])
    print(f"Fetched {len(raw_items)} results for {store_name}")
    return _parse_hyvee_items(raw_items, store_name)

def _parse_hyvee_items(raw_items: list[dict], store_name: str) -> list[Product]:
    """
    Parse each raw result into a Product. Skips non-product rows (banners,
    shoppable placements) and de-duplicates by id, since sponsored items can
    appear both as an ad and in the organic results.
    """
    products = []
    seen = set()
    for item in raw_items:
        if item.get("type") != "PRODUCT":
            continue
        pid = item.get("id")
        if pid in seen:
            continue
        seen.add(pid)
        product = _parse_hyvee_item(item, store_name)
        if product:
            products.append(product)
    return products

def _parse_hyvee_item(item: dict, store_name: str) -> Optional[Product]:
    """
    Map one raw Hy-Vee item onto the Product model.
 
    Pricing fields observed in the payload:
        tagPriceValue   -> price on the shelf tag right now (current price)
        basePriceValue  -> regular/base price
        memberPrice     -> Fuel Saver / member price (often null)
        computedPrice   -> final computed price after adjustments (often null)
    We treat tagPriceValue as the current price and basePriceValue as the
    regular price. VERIFY against one of the "On Sale" items (the searchFilters
    block reported a few) to confirm which field drops on a sale.
    """
    try:
        pricing = item.get("pricing", {}) or {}
 
        price = pricing.get("tagPriceValue")
        if price is None:
            price = pricing.get("basePriceValue")
 
        regular_price = pricing.get("basePriceValue")
        if regular_price is None:
            regular_price = price
 
        price = _to_float(price)
        regular_price = _to_float(regular_price)
        on_sale = (
            price is not None
            and regular_price is not None
            and price < regular_price
        )
 
        image = item.get("image") or {}
        image_url = image.get("url")
 
        return Product(
            id=str(item.get("id", "")),
            name=item.get("description", ""),
            brand=None,                       # not present in search payload
            size=item.get("unitOfMeasure"),
            price=price,
            regular_price=regular_price,
            on_sale=on_sale,
            sale_conditions=None,             # no promo text in search payload
            image_url=image_url,
            store=store_name,
            store_location=None,              # aisle not in search payload
            in_stock=bool(item.get("isEcommerceActive", True)),
            upc=item.get("upc"),
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
    
async def search_hyvee(
    query: str,
    store_id: str,
) -> list[Product]:
    return await search_hyvee_store(
        store_id=store_id,
        store_name="Hy-Vee",
        query=query,
    )