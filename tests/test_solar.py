"""Sunrise/sunset math vs published almanac values (a few minutes' tolerance).

The NOAA fit is accurate to a minute or two, which is all a wall-clock timer
needs; each case asserts within ``_TOL`` of the known UTC time so a small model
refinement won't make the suite brittle.
"""

import datetime

from api import solar

# The NOAA general equations are good to ~1-2 minutes; allow a little slack so an
# almanac-rounding difference doesn't fail the suite.
_TOL = datetime.timedelta(minutes=4)


def _utc(y: int, mo: int, d: int, h: int, mi: int) -> datetime.datetime:
    return datetime.datetime(y, mo, d, h, mi, tzinfo=datetime.UTC)


def _close(got: datetime.datetime | None, want: datetime.datetime) -> bool:
    return got is not None and abs(got - want) <= _TOL


def test_new_york_summer_solstice():
    # NYC (40.7128, -74.0060), 2024-06-21: sunrise 05:25 EDT, sunset 20:31 EDT.
    rise, set_ = solar.sun_times(datetime.date(2024, 6, 21), 40.7128, -74.0060)
    assert _close(rise, _utc(2024, 6, 21, 9, 25))
    assert _close(set_, _utc(2024, 6, 22, 0, 31))


def test_london_equinox():
    # London (51.5074, -0.1278), 2024-03-20: sunrise 06:02 UTC, sunset 18:14 UTC.
    rise, set_ = solar.sun_times(datetime.date(2024, 3, 20), 51.5074, -0.1278)
    assert _close(rise, _utc(2024, 3, 20, 6, 2))
    assert _close(set_, _utc(2024, 3, 20, 18, 14))


def test_sydney_summer_solstice_southern_hemisphere():
    # Sydney (-33.8688, 151.2093), 2024-12-21: sunrise 05:41 AEDT (18:41 UTC prev
    # day), sunset 20:05 AEDT (09:05 UTC). Exercises east longitude + southern lat.
    rise, set_ = solar.sun_times(datetime.date(2024, 12, 21), -33.8688, 151.2093)
    assert _close(rise, _utc(2024, 12, 20, 18, 41))
    assert _close(set_, _utc(2024, 12, 21, 9, 5))


def test_convenience_helpers_match_tuple():
    day, lat, lon = datetime.date(2024, 6, 21), 40.7128, -74.0060
    rise, set_ = solar.sun_times(day, lat, lon)
    assert solar.sunrise(day, lat, lon) == rise
    assert solar.sunset(day, lat, lon) == set_


def test_polar_night_returns_none():
    # Tromsø (69.65, 18.96) in deep winter: the sun never rises.
    rise, set_ = solar.sun_times(datetime.date(2024, 1, 1), 69.6492, 18.9553)
    assert rise is None
    assert set_ is None


def test_polar_day_returns_none():
    # Tromsø at midsummer: the sun never sets (nor dips to the sunrise zenith).
    rise, set_ = solar.sun_times(datetime.date(2024, 6, 21), 69.6492, 18.9553)
    assert rise is None
    assert set_ is None
