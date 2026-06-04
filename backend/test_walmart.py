"""
Standalone Walmart smoke test — run from the backend/ directory:

    python test_walmart.py

Bypasses uvicorn so you can watch the warm browser window and read the scraper's
log lines directly. The key signal is in those logs: if you see "blocked", the
anti-bot layer rejected the replay; if you see a product count, you're through.
"""
import asyncio
from dotenv import load_dotenv

load_dotenv()  # the API does this in main.py; the test has to do it itself

from scrapers.walmart.walmart import search_walmart

QUERY = "sour cream"


async def main():
    products = await search_walmart(QUERY)
    print(f"\n=== {len(products)} products for '{QUERY}' ===")
    for p in products[:10]:
        sale = " (SALE)" if p.on_sale else ""
        oos = "" if p.in_stock else " [out of stock]"
        price = f"${p.price:.2f}" if p.price is not None else "$ ?"
        print(f"  {price:>8}  {p.name}{sale}{oos}")
    if not products:
        print("  (none — check the log lines above for blocked vs. empty)")


if __name__ == "__main__":
    asyncio.run(main())