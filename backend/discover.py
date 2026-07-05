import ast
from pathlib import Path
from typing import AsyncIterator
from config import CRAWLERS_DIR, load_config
from crawler import validate_crawler_code
from logging_config import get_logger

log = get_logger("discover")

# discover.router is not registered in main.py — this flow is currently dormant.
# It only knows the release-crawler interface (search()); it has no awareness
# of the catalog crawler_type (crawl_catalog()) added by In Stock. If this is
# ever reactivated, add catalog support here and in validate_crawler_code
# before trusting it to generate a Shopify-backed catalog crawler.
CRAWLER_INTERFACE = '''
from playwright.async_api import Page

class Crawler:
    site_name: str = "SiteName"      # human-readable site name, e.g. "Amazon"
    base_url: str = "https://..."    # site root URL

    async def search(self, release: dict, page: Page) -> list[dict]:
        """
        Search the site for the given record release.

        release dict keys:
          - discogs_id: str
          - artist: str
          - title: str
          - year: int | None
          - label: str
          - format: str  (e.g. "Vinyl")

        Return a list of match dicts (empty list if not found). Each match:
          - url: str (required) — direct link to the listing
          - price: float | None
          - shipping: float | None
          - currency: str | None  (e.g. "USD")
          - condition: str | None  (e.g. "Very Good Plus (VG+)")

        Notes:
        - The Playwright Page object is shared. Always navigate to a neutral
          URL (about:blank or the site homepage) when done.
        - Add random delays between actions: await asyncio.sleep(random.uniform(1, 3))
        - Only return matches that are the same pressing/version of the record.
        - Extract price as a float (strip currency symbols).
        """
'''

SYSTEM_PROMPT = f"""You are an expert Python web scraper developer specializing in record-selling websites.

Your task: find a record-selling website that is NOT already supported (you will be told which sites are already supported), research its structure, and implement a Python crawler class for it.

The crawler MUST implement this exact interface:

```python
{CRAWLER_INTERFACE}
```

Requirements:
- Import `asyncio` and `random` at the top of the file
- Add `await asyncio.sleep(random.uniform(1, 3))` between page interactions
- Navigate to `about:blank` at the end of `search()` to reset the page
- Use Playwright's page.goto(), page.locator(), page.wait_for_selector() etc.
- Set `site_name` and `base_url` as class-level string attributes
- The file must be self-contained (only stdlib + playwright imports)
- Return ONLY the Python source code, no explanation or markdown fences
"""


async def run_discovery(existing_sites: list[str]) -> AsyncIterator[dict]:
    import anthropic

    config = load_config()
    api_key = config.get("anthropic_api_key", "")
    if not api_key:
        yield {"type": "error", "message": "Anthropic API key not configured"}
        return

    client = anthropic.Anthropic(api_key=api_key)
    existing_str = ", ".join(existing_sites) if existing_sites else "none"
    user_message = (
        f"Already supported sites: {existing_str}\n\n"
        "Find a new record-selling website (not in the list above) and implement a complete "
        "Python crawler class for it. Return only the Python source code."
    )

    log.info("Sending discovery request to Claude (existing: %s)", existing_str)
    yield {"type": "status", "message": "Asking Claude to find and implement a new crawler..."}

    code_chunks = []
    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        for text in stream.text_stream:
            code_chunks.append(text)
            yield {"type": "token", "text": text}

    full_code = "".join(code_chunks).strip()
    log.info("Claude returned %d characters of code", len(full_code))

    # Strip markdown fences if the model wrapped anyway
    if full_code.startswith("```"):
        lines = full_code.splitlines()
        full_code = "\n".join(
            line for line in lines if not line.startswith("```")
        ).strip()

    log.info("Validating generated crawler code")
    yield {"type": "status", "message": "Validating generated code..."}

    if not validate_crawler_code(full_code):
        log.error("Generated code failed validation — no Crawler class with async search method")
        yield {"type": "error", "message": "Generated code failed validation (no Crawler class with search method)"}
        return

    # Extract site_name from the code to use as filename
    try:
        tree = ast.parse(full_code)
        site_name = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "Crawler":
                for item in node.body:
                    if isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name) and target.id == "site_name":
                                if isinstance(item.value, ast.Constant):
                                    site_name = item.value.value
        if not site_name:
            site_name = "discovered_site"
    except Exception as e:
        log.warning("Could not extract site_name from AST: %s", e)
        site_name = "discovered_site"

    safe_name = site_name.lower().replace(" ", "_").replace(".", "_")
    output_path = CRAWLERS_DIR / f"{safe_name}.py"
    output_path.write_text(full_code)
    log.info("Wrote crawler for '%s' to %s", site_name, output_path)

    yield {
        "type": "complete",
        "site_name": site_name,
        "path": str(output_path),
    }
