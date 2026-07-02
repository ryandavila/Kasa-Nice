"""Sunrise/sunset times from latitude/longitude, in pure Python.

The Schedules feature can fire a rule at sunrise or sunset for the server's
configured location. Rather than pull in a dependency (``astral`` and friends)
for a handful of trig, this is the NOAA general solar-position calculation —
~40 lines of math, accurate to a minute or two, which is well inside the
tolerance a home-automation timer needs. Keeping the dependency footprint at
zero was the deciding factor: this code has no state, no I/O, and is trivially
unit-tested against almanac values (see ``tests/test_solar.py``).

The public helpers (:func:`sunrise`, :func:`sunset`) return timezone-aware UTC
datetimes; the caller converts to local time and compares HH:MM against the
scheduler's minute cursor. ``None`` means the sun doesn't cross the horizon that
day (polar day/night), in which case the rule simply doesn't fire.

References: NOAA Solar Calculator equations
(https://gml.noaa.gov/grad/solcalc/solareqns.PDF).
"""

import datetime
import math

# Standard sunrise/sunset zenith: the sun's centre sits 0.833° below the
# geometric horizon at the moment the upper limb touches it (34' atmospheric
# refraction + 16' solar semidiameter).
_ZENITH_DEG = 90.833


def _fractional_year_rad(day_of_year: int) -> float:
    """NOAA fractional year γ (radians) for a day, evaluated at solar noon.

    The tiny intra-day drift of the equation of time and declination is well
    under our minute-scale tolerance, so γ is taken at midday (the ``(hour-12)``
    term drops out) rather than iterating on the unknown event time.
    """
    return 2.0 * math.pi / 365.0 * (day_of_year - 1)


def _equation_of_time_min(gamma: float) -> float:
    """Equation of time in minutes — apparent-minus-mean solar time (NOAA fit)."""
    return 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )


def _solar_declination_rad(gamma: float) -> float:
    """Sun's declination in radians (NOAA Fourier fit)."""
    return (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )


def sun_times(
    day: datetime.date, latitude: float, longitude: float
) -> tuple[datetime.datetime | None, datetime.datetime | None]:
    """Return ``(sunrise, sunset)`` as UTC datetimes for a date and location.

    ``longitude`` is degrees positive **east** (the usual GIS/`°E` convention).
    Either element is ``None`` when the sun stays entirely above or below the
    horizon that day; the two are independent (a polar day has a sunset but no
    sunrise near the transition, etc.), so each is decided on its own.
    """
    day_of_year = day.timetuple().tm_yday
    gamma = _fractional_year_rad(day_of_year)
    eqtime = _equation_of_time_min(gamma)
    decl = _solar_declination_rad(gamma)
    lat_rad = math.radians(latitude)

    # Hour angle of the sun at the sunrise/sunset zenith. Out of [-1, 1] means no
    # crossing (polar day or night) — the sun never reaches that altitude.
    cos_ha = math.cos(math.radians(_ZENITH_DEG)) / (
        math.cos(lat_rad) * math.cos(decl)
    ) - math.tan(lat_rad) * math.tan(decl)

    base = datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.UTC)

    def _event(sign: int) -> datetime.datetime | None:
        # sign=+1 for sunrise (morning hour angle), -1 for sunset.
        if cos_ha < -1.0 or cos_ha > 1.0:
            return None
        ha_deg = math.degrees(math.acos(cos_ha))
        # Minutes past 00:00 UTC (NOAA: solar noon is 720 − 4·longitude − eqtime,
        # longitude positive east; the hour angle shifts sunrise earlier and
        # sunset later). The result can fall on the previous/next UTC day, which
        # timedelta carries correctly.
        minutes = 720.0 - 4.0 * longitude - sign * 4.0 * ha_deg - eqtime
        return base + datetime.timedelta(minutes=minutes)

    return _event(1), _event(-1)


def sunrise(
    day: datetime.date, latitude: float, longitude: float
) -> datetime.datetime | None:
    """Sunrise as a UTC datetime, or ``None`` on a polar day/night."""
    return sun_times(day, latitude, longitude)[0]


def sunset(
    day: datetime.date, latitude: float, longitude: float
) -> datetime.datetime | None:
    """Sunset as a UTC datetime, or ``None`` on a polar day/night."""
    return sun_times(day, latitude, longitude)[1]
