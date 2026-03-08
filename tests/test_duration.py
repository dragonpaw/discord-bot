import pytest

from dragonpaw_bot.duration import format_duration, parse_duration_minutes

# ---------------------------------------------------------------------------- #
#                           parse_duration_minutes                             #
# ---------------------------------------------------------------------------- #


def test_parse_minutes():
    assert parse_duration_minutes("30m") == 30


def test_parse_hours():
    assert parse_duration_minutes("6h") == 360


def test_parse_days():
    assert parse_duration_minutes("2d") == 2880


def test_parse_weeks():
    assert parse_duration_minutes("1w") == 10080


def test_parse_compound():
    assert parse_duration_minutes("1d12h") == 1440 + 720


def test_parse_long_form_minutes():
    assert parse_duration_minutes("30 minutes") == 30


def test_parse_long_form_hours():
    assert parse_duration_minutes("6 hours") == 360


def test_parse_long_form_days():
    assert parse_duration_minutes("2 days") == 2880


def test_parse_long_form_weeks():
    assert parse_duration_minutes("1 week") == 10080


def test_parse_case_insensitive():
    assert parse_duration_minutes("30M") == 30
    assert parse_duration_minutes("1W") == 10080
    assert parse_duration_minutes("6H") == 360


def test_parse_with_spaces():
    assert parse_duration_minutes("1 d 12 h") == 1440 + 720


def test_parse_zero_raises():
    with pytest.raises(ValueError, match="positive"):
        parse_duration_minutes("0m")


def test_parse_unrecognised_raises():
    with pytest.raises(ValueError, match="Couldn't parse"):
        parse_duration_minutes("asap")


def test_parse_empty_raises():
    with pytest.raises(ValueError, match="Couldn't parse"):
        parse_duration_minutes("")


def test_parse_bare_number_raises():
    with pytest.raises(ValueError, match="Couldn't parse"):
        parse_duration_minutes("100")


def test_parse_plural_forms():
    assert parse_duration_minutes("2 mins") == 2
    assert parse_duration_minutes("3 hrs") == 180
    assert parse_duration_minutes("2 weeks") == 20160


# ---------------------------------------------------------------------------- #
#                             format_duration                                  #
# ---------------------------------------------------------------------------- #


def test_format_weeks():
    assert format_duration(10080) == "1w"


def test_format_days():
    assert format_duration(1440) == "1d"


def test_format_hours():
    assert format_duration(60) == "1h"


def test_format_minutes():
    assert format_duration(30) == "30m"


def test_format_mixed():
    assert format_duration(1440 + 720) == "1d 12h"


def test_format_all_units():
    assert format_duration(10080 + 1440 + 60 + 1) == "1w 1d 1h 1m"


def test_format_zero():
    assert format_duration(0) == "0m"


def test_format_no_trailing_zeros():
    # 1 week exactly — should not include "0d 0h 0m"
    assert format_duration(10080) == "1w"


def test_format_roundtrip():
    for s in ("30m", "6h", "2d", "1w", "1d12h"):
        result = format_duration(parse_duration_minutes(s))
        assert result != ""
        assert result != "0m"
