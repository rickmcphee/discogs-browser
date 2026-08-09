from datetime import datetime

import pytest

import recommendations_import as ri

KEY_A = "a" * 64
KEY_B = "b" * 64
HEADER = "artist,title,format,price,source,link,reason,item_key,recommended,judged_at"


def _csv(*rows):
    return "\n".join([HEADER, *rows]) + "\n"


def test_parses_a_valid_row():
    text = _csv(f"Artist A,Album A,LP,10.0,Amazon,https://x/1,looks good,{KEY_A},true,2026-08-09T14:03:22.481923")
    judgments, errors, skipped = ri.parse_judgment_csv(text)
    assert errors == []
    assert skipped == 0
    assert judgments == [{
        "item_key": KEY_A,
        "recommended": True,
        "reason": "looks good",
        "judged_at": datetime(2026, 8, 9, 14, 3, 22, 481923),
    }]


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("TRUE", True), ("True", True), ("t", True), ("yes", True), ("1", True),
    ("false", False), ("FALSE", False), ("False", False), ("f", False), ("no", False), ("0", False),
])
def test_accepts_documented_boolean_spellings(raw, expected):
    text = _csv(f"A,B,,,,,,{KEY_A},{raw},2026-08-09T14:03:22")
    judgments, errors, skipped = ri.parse_judgment_csv(text)
    assert errors == []
    assert judgments[0]["recommended"] is expected


def test_empty_reason_becomes_none():
    text = _csv(f"A,B,,,,,,{KEY_A},true,2026-08-09T14:03:22")
    judgments, _, _ = ri.parse_judgment_csv(text)
    assert judgments[0]["reason"] is None


def test_missing_reason_column_is_tolerated():
    text = "item_key,recommended,judged_at\n" + f"{KEY_A},true,2026-08-09T14:03:22\n"
    judgments, errors, skipped = ri.parse_judgment_csv(text)
    assert errors == []
    assert judgments[0]["reason"] is None


@pytest.mark.parametrize("bad_key", ["", "abc", "A" * 64, "z" * 64, "a" * 63, "a" * 65])
def test_skips_rows_with_a_bad_item_key(bad_key):
    text = _csv(
        f"A,B,,,,,,{bad_key},true,2026-08-09T14:03:22",
        f"C,D,,,,,,{KEY_B},true,2026-08-09T14:03:22",
    )
    judgments, errors, skipped = ri.parse_judgment_csv(text)
    assert [j["item_key"] for j in judgments] == [KEY_B]
    assert skipped == 1
    assert errors[0]["line"] == 2
    assert "item_key" in errors[0]["error"]


def test_skips_rows_with_an_unparseable_or_missing_judged_at():
    text = _csv(
        f"A,B,,,,,,{KEY_A},true,not-a-date",
        f"C,D,,,,,,{KEY_B},true,",
    )
    judgments, errors, skipped = ri.parse_judgment_csv(text)
    assert judgments == []
    assert skipped == 2
    assert [e["line"] for e in errors] == [2, 3]
    assert all("judged_at" in e["error"] for e in errors)


def test_skips_rows_with_an_unrecognized_recommended_value():
    text = _csv(f"A,B,,,,,,{KEY_A},maybe,2026-08-09T14:03:22")
    judgments, errors, skipped = ri.parse_judgment_csv(text)
    assert judgments == []
    assert skipped == 1
    assert "recommended" in errors[0]["error"]


def test_reported_line_numbers_survive_an_embedded_newline():
    text = _csv(
        f'A,B,,,,,"reason\nwith a newline",{KEY_A},true,2026-08-09T14:03:22',
        f"C,D,,,,,,{KEY_B},true,nope",
    )
    _, errors, _ = ri.parse_judgment_csv(text)
    # csv.reader.line_num counts physical lines, so the quoted newline pushes
    # the bad row to line 4, not 3.
    assert errors[0]["line"] == 4


@pytest.mark.parametrize("stamp", ["2026-08-09T14:03:22Z", "2026-08-09T10:03:22-04:00"])
def test_offset_and_z_timestamps_normalize_to_naive_utc(stamp):
    text = _csv(f"A,B,,,,,,{KEY_A},true,{stamp}")
    judgments, errors, _ = ri.parse_judgment_csv(text)
    assert errors == []
    assert judgments[0]["judged_at"] == datetime(2026, 8, 9, 14, 3, 22)
    assert judgments[0]["judged_at"].tzinfo is None


def test_duplicate_item_key_collapses_to_the_newest_and_counts_as_skipped():
    text = _csv(
        f"A,B,,,,,older,{KEY_A},false,2026-08-01T00:00:00",
        f"A,B,,,,,newer,{KEY_A},true,2026-08-09T00:00:00",
    )
    judgments, errors, skipped = ri.parse_judgment_csv(text)
    assert len(judgments) == 1
    assert judgments[0]["reason"] == "newer"
    assert judgments[0]["recommended"] is True
    assert skipped == 1
    assert "duplicate" in errors[0]["error"]


def test_duplicate_kept_row_wins_regardless_of_file_order():
    text = _csv(
        f"A,B,,,,,newer,{KEY_A},true,2026-08-09T00:00:00",
        f"A,B,,,,,older,{KEY_A},false,2026-08-01T00:00:00",
    )
    judgments, _, skipped = ri.parse_judgment_csv(text)
    assert len(judgments) == 1
    assert judgments[0]["reason"] == "newer"
    assert skipped == 1


def test_missing_required_header_column_rejects_the_whole_file():
    text = "artist,title,recommended,judged_at\nA,B,true,2026-08-09T14:03:22\n"
    with pytest.raises(ri.InvalidImportError) as exc:
        ri.parse_judgment_csv(text)
    assert "item_key" in str(exc.value)


def test_empty_file_rejects_as_invalid():
    with pytest.raises(ri.InvalidImportError):
        ri.parse_judgment_csv("")


def test_row_count_over_the_cap_rejects_the_whole_file(monkeypatch):
    monkeypatch.setattr(ri, "MAX_ROWS", 2)
    text = _csv(*[
        f"A,B,,,,,,{format(i, '064x')},true,2026-08-09T14:03:22" for i in range(3)
    ])
    with pytest.raises(ri.InvalidImportError) as exc:
        ri.parse_judgment_csv(text)
    assert "row" in str(exc.value).lower()


def test_error_list_is_capped_but_skipped_count_is_not(monkeypatch):
    monkeypatch.setattr(ri, "MAX_REPORTED_ERRORS", 3)
    text = _csv(*[f"A,B,,,,,,badkey,true,2026-08-09T14:03:22" for _ in range(10)])
    judgments, errors, skipped = ri.parse_judgment_csv(text)
    assert judgments == []
    assert len(errors) == 3
    assert skipped == 10


def test_tolerates_a_utf8_bom_on_the_header():
    text = "﻿" + _csv(f"A,B,,,,,,{KEY_A},true,2026-08-09T14:03:22")
    judgments, errors, _ = ri.parse_judgment_csv(text)
    assert errors == []
    assert judgments[0]["item_key"] == KEY_A
