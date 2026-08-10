import re

_COLLECTION_SLUG = "vinyl-1"

# En-dash and em-dash are in the class because 34 live titles separate with
# "–" rather than "-" ("U.K. Subs – Endangered Species"). Whitespace is
# required on at least one side so hyphenated artist names survive: 18 live
# artists carry an internal hyphen with no surrounding space (Anti-Flag,
# Blink-182, Buck-O-Nine, Ann-Margret, Eek-A-Mouse).
_TITLE_RE = re.compile(r'^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$')
_TRAILING_PARENS_RE = re.compile(r'\s*\([^()]*\)\s*$')


class Crawler:
    site_name: str = "Cleopatra Records"
    base_url: str = "https://cleorecs.com"
    crawler_type: str = "catalog"

    @classmethod
    def _parse_artist_title(cls, title: str):
        # `vendor` is the imprint on every live product (Cleopatra Records,
        # Purple Pyramid Records, Deadline Music, ...) and never the artist, so
        # the sibling crawlers' vendor fallback is deliberately not used here.
        #
        # The split point is found on the title with trailing parentheticals
        # removed, because 11 live titles carry no artist prefix but do contain
        # " - " inside their trailing bracket. The album text keeps the
        # parentheticals: db.py's _library_match_fragment matches
        # exact-or-prefix-with-space, so a colour/format suffix doesn't block a
        # catalog match.
        base = title.strip()
        stripped = cls._strip_trailing_parens(base)
        m = _TITLE_RE.match(stripped)
        if not m:
            return "Various Artists", base
        return m.group("artist").strip(), base[m.start("album"):].strip()

    @staticmethod
    def _strip_trailing_parens(title: str) -> str:
        stripped = title.strip()
        while True:
            shorter = _TRAILING_PARENS_RE.sub('', stripped)
            if shorter == stripped:
                return stripped
            stripped = shorter
