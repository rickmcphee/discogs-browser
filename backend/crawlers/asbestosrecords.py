import html
import re

# Whitespace required on at least one side of the hyphen, matching the
# repo's standard fix for this bug class: a plain \s*-\s* form would clip a
# hyphenated word with no surrounding space (confirmed live here --
# "The Suicide Machines-On the Eve of Destruction 2xLP" -- into
# "The Suicide Machines" plus a mangled album).
_TITLE_RE = re.compile(r'^(?P<artist>.+?)(?:\s+-\s*|\s*-\s+)(?P<album>.+)$')
_VARIOUS_RE = re.compile(r'^various(?:\s+artists)?$', re.IGNORECASE)


class Crawler:
    site_name: str = "Asbestos Records"
    base_url: str = "https://asbestosrecords.bigcartel.com"
    genre_summary: str = "Ska, punk, and hardcore label and record store."
    crawler_type: str = "catalog"

    @classmethod
    def _parse_artist_title(cls, name: str, artists: list):
        # Bigcartel's `artists` field is store-curated per product (unlike a
        # Shopify `vendor`, which is one label name repeated on every row),
        # but it's only trustworthy as a fallback: some tagged artists don't
        # literally match the title's billing (e.g. a member's solo release
        # tagged under their main band), so a literal title split always
        # wins when one exists.
        clean = html.unescape(name).strip()
        m = _TITLE_RE.match(clean)
        if m:
            artist = m.group("artist").strip()
            album = m.group("album").strip()
            if _VARIOUS_RE.match(artist):
                artist = "Various"
            return artist, album
        if artists:
            return html.unescape(artists[0].get("name") or "").strip(), clean
        return None, clean
