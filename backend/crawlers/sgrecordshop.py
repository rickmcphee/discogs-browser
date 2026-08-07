import html
import re

_BLOCK_RE = re.compile(
    r'<div class="producttitlelink product-grid-variant".*?'
    r'(?=<div class="producttitlelink product-grid-variant"|\Z)', re.S)
_PID_RE = re.compile(r'/p/(\d+)/')
_TITLE_ATTR_RE = re.compile(r'<a href="[^"]+" title="([^"]+)"')
_PRODUCT_TITLE_RE = re.compile(r'product-title">\s*([^<]+)')
_FORMAT_RE = re.compile(r'see-more-format">\s*([^<]+?)\s*<span')
_PRICE_RE = re.compile(r'itemprop="price">([\d.]+)</span>')
_IMG_RE = re.compile(r'data-src="([^"]+)"')
_UNAVAILABLE_RE = re.compile(r'product-variant-unavailable')


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


class Crawler:
    site_name: str = "The Sound Garden"
    base_url: str = "https://www.sgrecordshop.com"
    crawler_type: str = "catalog"

    # path + querystring exactly as sourced from the site's own nav --
    # af= tokens are opaque per-category filter ids, not derivable.
    _CATEGORIES = [
        "/c/2724/record-shop-rock-pop-indie?&so=9&af=-3011|-3010|-3008|-10|-2",
        "/c/2726/record-shop-soul-funk-rnb?&so=9&af=-10|-2003|-2",
        "/c/2725/record-shop-beats-hip-hop?&so=9&af=-10|-2003|-2",
        "/c/2756/record-shop-jazz-fusion?&so=9&af=-3008|-10|-2",
        "/c/2738/record-shop-electronic?&so=9&af=-10|-2003|-2013|-2",
        "/c/2773/record-shop-goth-industrial?&so=9&af=-10|-2003|-2",
        "/c/2728/record-shop-metal?&so=9&af=-10|-2003|-2",
        "/c/2758/record-shop-punk-hardcore?&so=9&af=-10|-2036|-2003|-2",
        "/c/2759/record-shop-folk-country-americana?&so=9&af=-10|-2003|-2",
        "/c/2767/record-shop-blues?&so=9&af=-10|-2003|-2",
        "/c/2760/record-shop-dub-reggae?&so=9&af=-10|-2003|-2013|-2",
        "/c/2762/record-shop-world?&so=9&af=-10|-2003|-2",
        "/c/2765/record-shop-soundtracks?&so=9&af=-10|-2003|-2",
        "/c/2753/record-shop-experimental-modern-classical?&so=9&af=-10|-2003",
    ]

    @classmethod
    def _parse_items(cls, fragment_html: str) -> list:
        items = []
        for block in _BLOCK_RE.findall(fragment_html):
            if _UNAVAILABLE_RE.search(block):
                continue  # "Not available" -- no price, not purchasable
            pid_m, price_m = _PID_RE.search(block), _PRICE_RE.search(block)
            if not (pid_m and price_m):
                continue

            artist_m = _PRODUCT_TITLE_RE.search(block)
            artist = _norm(artist_m.group(1)) if artist_m else ""
            title_attr_m = _TITLE_ATTR_RE.search(block)
            full = _norm(title_attr_m.group(1)) if title_attr_m else ""
            prefix = artist + "/"
            if full.startswith(prefix):
                remainder = full[len(prefix):]
            else:
                head = full.split("@", 1)[0]
                _, _, remainder = head.rpartition("/")
            title = remainder.split("@", 1)[0].strip()

            fmt_m = _FORMAT_RE.search(block)
            img_m = _IMG_RE.search(block)
            items.append({
                "pid": pid_m.group(1),
                "artist": artist,
                "title": title,
                "format": _norm(fmt_m.group(1)) if fmt_m else "Vinyl",
                "price": float(price_m.group(1)),
                "currency": "USD",
                "url": f"{cls.base_url}/p/{pid_m.group(1)}/",
                "cover_image_url": img_m.group(1) if img_m else None,
            })
        return items
