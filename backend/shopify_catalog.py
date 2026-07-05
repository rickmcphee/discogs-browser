import asyncio
from typing import AsyncIterator, Optional
import httpx

_PAGE_LIMIT = 250
_PAGE_DELAY_SECONDS = 1.0


async def iter_products(base_url: str, collection_slug: str) -> AsyncIterator[dict]:
    """Paginate a Shopify collection's public products.json endpoint until exhausted."""
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            url = f"{base_url}/collections/{collection_slug}/products.json"
            r = await client.get(url, params={"limit": _PAGE_LIMIT, "page": page})
            r.raise_for_status()
            products = r.json().get("products", [])
            if not products:
                break
            for product in products:
                yield product
            page += 1
            await asyncio.sleep(_PAGE_DELAY_SECONDS)


def has_tag(product: dict, tag: str) -> bool:
    """Case-insensitive membership check against a Shopify product's tags array."""
    needle = tag.strip().lower()
    return any((t or "").strip().lower() == needle for t in product.get("tags", []))


def strip_vendor_prefix(title: str, vendor: str) -> str:
    """Strip a leading "{vendor} - " from a product title, if present; otherwise return it unchanged."""
    vendor = (vendor or "").strip()
    prefix = f"{vendor} - "
    if vendor and title.startswith(prefix):
        return title[len(prefix):]
    return title


def resolve_cover_image(product: dict, variant: dict) -> Optional[str]:
    """Prefer the variant's own image (e.g. a specific vinyl color), falling back to the product's first image."""
    featured = variant.get("featured_image") or {}
    if featured.get("src"):
        return featured["src"]
    images = product.get("images") or []
    return images[0].get("src") if images else None
