from routers.logs import _line_visible, _parse_levels

INFO_LINE = "2026-07-18 20:19:18  INFO      main  ready"
DEBUG_LINE = "2026-07-18 20:19:18  DEBUG     crawlers.amazon  [Amazon] searching"
ERROR_LINE = "2026-07-18 20:19:18  ERROR     crawler  boom"
CONTINUATION_LINE = "    File \"x.py\", line 1, in <module>"


def test_no_levels_shows_all():
    assert _line_visible(INFO_LINE, None)
    assert _line_visible(DEBUG_LINE, None)


def test_filters_out_unwanted_levels():
    wanted = {"INFO", "WARNING", "ERROR"}
    assert _line_visible(INFO_LINE, wanted)
    assert _line_visible(ERROR_LINE, wanted)
    assert not _line_visible(DEBUG_LINE, wanted)


def test_unparseable_lines_always_shown():
    # Tracebacks / continuation lines carry no level and must not be dropped
    assert _line_visible(CONTINUATION_LINE, {"INFO"})


def test_parse_levels_normalizes_and_ignores_blanks():
    assert _parse_levels("info, debug ,,") == {"INFO", "DEBUG"}
    assert _parse_levels(None) is None
    assert _parse_levels("") is None
