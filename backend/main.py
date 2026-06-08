import os
import asyncio
import contextlib
import time
from fastapi import FastAPI, Query, HTTPException
from dotenv import load_dotenv
from models.base import Product
from scrapers.instacart import search_cub_foods, search_coborns, search_aldi
from scrapers.hyvee import search_hyvee
from scrapers.walmart.walmart import search_walmart
from scrapers.walmart.walmart_session import get_warmed_session, SESSION_TTL_SECONDS
from typing import Optional

load_dotenv()

async def _prewarm_loop():
    while True:
        try:
            s = await get_warmed_session()                 # warms only if missing/expired
            if time.time() - s.created_at > SESSION_TTL_SECONDS * 0.8:
                await get_warmed_session(force=True)        # refresh ahead of expiry
        except Exception as e:
            print(f"Walmart pre-warm failed: {e}")
        await asyncio.sleep(60)

@contextlib.asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(_prewarm_loop())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

app = FastAPI(title="Cheddar API")

CUB_SHOP_ID = os.getenv("CUB_SHOP_ID")
CUB_ZONE_ID = os.getenv("CUB_ZONE_ID")
COBORNS_SHOP_ID = os.getenv("COBORNS_SHOP_ID")
COBORNS_ZONE_ID = os.getenv("COBORNS_ZONE_ID")
ALDI_SHOP_ID = os.getenv("ALDI_SHOP_ID")
ALDI_ZONE_ID = os.getenv("ALDI_ZONE_ID")
HYVEE_STORE_ID = os.getenv("HYVEE_STORE_ID")

# Map store names to their scraper functions
STORE_SCRAPERS = {
    "cub-foods": lambda q: search_cub_foods(q, CUB_SHOP_ID, CUB_ZONE_ID),
    "coborns": lambda q: search_coborns(q, COBORNS_SHOP_ID, COBORNS_ZONE_ID),
    "aldi": lambda q: search_aldi(q, ALDI_SHOP_ID, ALDI_ZONE_ID),
    "hy-vee": lambda q: search_hyvee(q, HYVEE_STORE_ID),
    "walmart": lambda q: search_walmart(q),
}

# Normalize store names from search query to match scraper keys
def _normalize_store_name(name: str) -> Optional[str]:
    name_lower = name.lower()
    if "cub" in name_lower:
        return "cub-foods"
    if "aldi" in name_lower:
        return "aldi"
    if "coborn" in name_lower:
        return "coborns"
    if "hy-vee" in name_lower or "hyvee" in name_lower or "hy vee" in name_lower:
        return "hy-vee"
    if "walmart" in name_lower:
        return "walmart"
    return None

# Search for a product across multiple stores in parallel
@app.get("/search", response_model=list[Product])
async def search_products(
    query: str = Query(..., description="Product search term"),
    stores: str = Query(..., description="Comma-separated list of store names"),
):
    requested_stores = [s.strip() for s in stores.split(",")]

    # Build list of coroutines for requested stores we support
    tasks = []
    store_keys = []
    for store in requested_stores:
        key = _normalize_store_name(store)
        scraper = STORE_SCRAPERS.get(key) if key else None
        if scraper:
            tasks.append(scraper(query))
            store_keys.append(store)
        else:
            print(f"No scraper found for store: {store}")

    if not tasks:
        raise HTTPException(
            status_code=400,
            detail=f"No supported stores in: {stores}"
        )

    # Run all store searches in parallel
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Flatten results, skip any stores that errored
    products = []
    for store_name, result in zip(store_keys, results):
        if isinstance(result, Exception):
            print(f"Error scraping {store_name}: {result}")
            continue
        products.extend(result)

    return products

@app.get("/supported-stores")
async def supported_stores(
    stores: str = Query(..., description="Comma-separated list of store names"),
):
    requested = [s.strip() for s in stores.split(",")]
    return {
        "supported": [s for s in requested if _normalize_store_name(s)],
        "unsupported": [s for s in requested if not _normalize_store_name(s)],
    }

@app.get("/health")
async def health():
    return {"status": "ok"}