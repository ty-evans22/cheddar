import os
import json
import uuid
from dotenv import load_dotenv
from typing import Optional
from curl_cffi.requests import AsyncSession
from models.base import Product
from utils.http import browser_session

load_dotenv()

def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set — add it to backend/.env. "
            f"(Persisted-query hashes and POSTAL_CODE are required for Instacart stores.)"
        )
    return value

ITEM_PLACEMENT_TYPES = {
    "SearchContentManagementSearchItemGrid",
    "SearchContentManagementSearchItemCarousel",
}

HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "x-client-identifier": "web",
    "x-ic-view-layer": "true",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

_sessions: dict[str, dict] = {}

async def _get_session(client: AsyncSession, domain: str) -> dict:
    if domain in _sessions:
        return _sessions[domain]

    print(f"Initializing session for {domain}...")
    homepage_response = await client.get(
        f"https://{domain}",
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

    cookies = dict(homepage_response.cookies)
    print(f"Session cookies obtained for {domain}: {list(cookies.keys())}")
    _sessions[domain] = cookies
    return cookies

def _collect_search_item_ids(placements: list) -> list[str]:
    """
    Collect itemIds from direct search result placements only.
    Stops at the first large carousel which marks the start of recommendations.
    """
    item_ids = []
    seen = set()
    for placement in placements:
        content = placement.get("content", {})
        typename = content.get("__typename", "")
        if typename not in ITEM_PLACEMENT_TYPES:
            continue

        ids = content.get("itemIds", [])

        # Large carousels are recommendation sections, not search results
        if typename == "SearchContentManagementSearchItemCarousel" and len(ids) > 10:
            break

        for item_id in ids:
            if item_id not in seen:
                seen.add(item_id)
                item_ids.append(item_id)

    print(f"Collected {len(item_ids)} search result itemIds")
    return item_ids

async def _fetch_items(
    client: AsyncSession,
    domain: str,
    shop_id: str,
    zone_id: str,
    postal_code: str,
    item_ids: list[str],
    session_cookies: dict,
    page_view_id: str,
) -> list[dict]:
    """
    Bulk fetch full item details for a list of itemIds.
    """
    items_hash = _require_env("ITEMS_QUERY_HASH")

    variables = {
        "ids": item_ids,
        "shopId": shop_id,
        "zoneId": zone_id,
        "postalCode": postal_code,
    }

    extensions = {
        "persistedQuery": {
            "version": 1,
            "sha256Hash": items_hash,
        }
    }

    params = {
        "operationName": "Items",
        "variables": json.dumps(variables, separators=(',', ':')),
        "extensions": json.dumps(extensions, separators=(',', ':')),
    }

    response = await client.get(
        f"https://{domain}/graphql",
        params=params,
        headers={
            **HEADERS,
            "x-page-view-id": page_view_id,
            "x-ic-qp": str(uuid.uuid4()),
        },
        cookies=session_cookies,
    )
    response.raise_for_status()
    data = response.json()

    items = (
        (data.get("data") or {})
        .get("items", [])
    )
    print(f"Fetched {len(items)} items from bulk Items query")
    return items

async def search_instacart_store(
    domain: str,
    shop_id: str,
    zone_id: str,
    store_name: str,
    query: str,
    postal_code: Optional[str] = None,
    limit: int = 40,
) -> list[Product]:
    """
    Search for products in a specific Instacart store and return a list of products.
    """
    postal_code = postal_code or _require_env("POSTAL_CODE")
    search_hash = _require_env("SEARCH_QUERY_HASH")

    page_view_id = str(uuid.uuid4())

    variables = {
        "action": None,
        "query": query,
        "pageViewId": page_view_id,
        "elevatedProductId": None,
        "searchSource": "search",
        "filters": [],
        "disableReformulation": False,
        "disableLlm": False,
        "forceInspiration": False,
        "orderBy": "bestMatch",
        "clusterId": None,
        "includeDebugInfo": False,
        "clusteringStrategy": None,
        "contentManagementSearchParams": {"itemGridColumnCount": 4},
        "shopId": shop_id,
        "postalCode": postal_code,
        "zoneId": zone_id,
        "first": limit,
    }

    extensions = {
        "persistedQuery": {
            "version": 1,
            "sha256Hash": search_hash,
        }
    }

    params = {
        "operationName": "SearchResultsPlacements",
        "variables": json.dumps(variables, separators=(',', ':')),
        "extensions": json.dumps(extensions, separators=(',', ':')),
    }

    async with browser_session() as client:
        session_cookies = await _get_session(client, domain)

        # ── Step 1: get search placements and collect itemIds ──────────────────
        search_response = await client.get(
            f"https://{domain}/graphql",
            params=params,
            headers={
                **HEADERS,
                "referer": f"https://{domain}/store/s?k={query.replace(' ', '+')}",
                "x-page-view-id": page_view_id,
                "x-ic-qp": str(uuid.uuid4()),
            },
            cookies=session_cookies,
        )
        search_response.raise_for_status()
        search_data = search_response.json()

        if search_data.get("errors"):
            print(f"GraphQL errors for {store_name}: {search_data['errors']}")

        data_root = search_data.get("data")
        if not data_root:
            print(f"No data block for {store_name}; full response: {search_data}")
            return []

        placements = (
            (data_root.get("searchResultsPlacements") or {})
            .get("placements", [])
        )

        item_ids = _collect_search_item_ids(placements)

        if not item_ids:
            print(f"No itemIds found for {store_name}")
            return []

        # ── Step 2: bulk fetch full item details ───────────────────────────────
        raw_items = await _fetch_items(
            client=client,
            domain=domain,
            shop_id=shop_id,
            zone_id=zone_id,
            postal_code=postal_code,
            item_ids=item_ids,
            session_cookies=session_cookies,
            page_view_id=page_view_id,
        )

    products = _parse_instacart_items(raw_items, store_name)
    return products

def _parse_instacart_items(raw_items: list[dict], store_name: str) -> list[Product]:
    """
     Call _parse_instacart_item for each raw item and collect valid Products.
    """
    products = []
    for item in raw_items:
        product = _parse_instacart_item(item, store_name)
        if product:
            products.append(product)
    return products

def _parse_instacart_item(item: dict, store_name: str) -> Optional[Product]:
    """
     Extract relevant product info from raw item data.
    """
    try:
        view_section = item.get("viewSection", {})
        tracking_properties = view_section.get("trackingProperties", {})

        # Stock level
        stock_level = tracking_properties.get("stock_level")
        in_stock = stock_level == "in_stock" or stock_level == "highly_in_stock"

        # Price information
        price_section = (
            item.get("price", {})
            .get("viewSection", {})
            .get("itemCard", {})
        )
        price_str = price_section.get("priceString", "")
        full_price_str = price_section.get("plainFullPriceString", "")

        price = _parse_price(price_str)
        regular_price = _parse_price(full_price_str) if full_price_str else price
        on_sale = (
            price is not None
            and regular_price is not None
            and price < regular_price
        )

        price_badge = (
            item.get("price", {})
            .get("viewSection", {})
            .get("badge", {})
        )
        sale_conditions = price_badge.get("trackingProperties", {}).get("save_amount") if price_badge else None

        # Image URL
        image_url = None
        item_image = view_section.get("itemImage", {})
        if item_image:
            image_url = item_image.get("url")

        # UPC
        upc = None
        retailer_code = view_section.get("retailerLookupCodeString", "")
        if retailer_code and retailer_code.startswith("UPC: "):
            upc = retailer_code.replace("UPC: ", "").strip()

        # Descriptors
        descriptors = _extract_descriptors(item)

        # In-Store Location
        location_section = (
            item.get("inStoreItemLocation", {})
            .get("viewSection", {})
        )
        store_location = location_section.get("locationString")

        return Product(
            id = item.get("id", ""),
            name=item.get("name", ""),
            brand=item.get("brandName"),
            size=item.get("size"),
            price=price,
            regular_price=regular_price,
            on_sale=on_sale,
            image_url=image_url,
            store=store_name,
            store_location=store_location,
            in_stock=in_stock,
            upc=upc,
            descriptors=descriptors,
            sale_conditions=sale_conditions,
        )
    except Exception as e:
        print(f"Error parsing item: {e}")
        return None
    
def _extract_descriptors(item: dict) -> list[str]:
    """
     Extract product descriptors from the dietary section of the item data.
    """
    descriptors = []

    try:
        attributes = item.get("dietary", {}).get("mlShoppingAttributes", {})
        
        for attribute in attributes:
            descriptors.append(attribute)
    except Exception as e:
        print(f"Error extracting descriptors: {e}")

    return descriptors
    
def _parse_price(price_str: str) -> Optional[float]:
    if not price_str:
        return None
    try:
        return float(price_str.replace("$", "").replace(",", "").strip())
    except ValueError:
        return None
    
async def search_cub_foods(
    query: str,
    shop_id: str,
    zone_id: str,
) -> list[Product]:
    return await search_instacart_store(
        domain="www.cub.com",
        shop_id=shop_id,
        zone_id=zone_id,
        store_name="Cub Foods",
        query=query,
    )


async def search_coborns(
    query: str,
    shop_id: str,
    zone_id: str,
) -> list[Product]:
    return await search_instacart_store(
        domain="shop.coborns.com",
        shop_id=shop_id,
        zone_id=zone_id,
        store_name="Coborn's",
        query=query,
    )

async def search_aldi(
    query: str,
    shop_id: str,
    zone_id: str,
) -> list[Product]:
    return await search_instacart_store(
        domain="www.aldi.us",
        shop_id=shop_id,
        zone_id=zone_id,
        store_name="ALDI",
        query=query,
    )