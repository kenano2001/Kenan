"""
NFL stadium locations + live weather lookups, used to fold real-world game
conditions into weekly line movement (see odds_model.py's weather adjustment).

STADIUMS is a static table (32 teams -> lat/lon + dome/outdoor) since venues
essentially never change mid-season. fetch_forecast() hits the National
Weather Service API (api.weather.gov) — free, no API key, US government
data. Domes and retractable roofs are treated as weather-neutral; retractable
roofs are conservatively assumed closed in bad weather (which is how teams
actually operate them), so they're excluded from adjustment same as domes.

Usage:
    from nfl_weather import STADIUMS, fetch_forecast, weather_adjustment

    forecast = fetch_forecast("GB", when=some_kickoff_datetime)
    adj = weather_adjustment(forecast)   # -> dict of scoring-context deltas
"""

from __future__ import annotations
import urllib.request
import urllib.error
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

USER_AGENT = "fantasize-odds-model (contact: kenano2001@gmail.com)"

# team abbreviation (Sleeper/ESPN convention) -> (lat, lon, is_weather_neutral)
# is_weather_neutral = True for fixed domes and retractable-roof stadiums.
STADIUMS: dict[str, tuple[float, float, bool]] = {
    "ARI": (33.5276, -112.2626, True),   # State Farm Stadium (retractable)
    "ATL": (33.7554, -84.4008, True),    # Mercedes-Benz Stadium (retractable)
    "BAL": (39.2780, -76.6227, False),   # M&T Bank Stadium
    "BUF": (42.7738, -78.7870, False),   # Highmark Stadium
    "CAR": (35.2258, -80.8528, False),   # Bank of America Stadium
    "CHI": (41.8623, -87.6167, False),   # Soldier Field
    "CIN": (39.0955, -84.5160, False),   # Paycor Stadium
    "CLE": (41.5061, -81.6995, False),   # Huntington Bank Field
    "DAL": (32.7473, -97.0945, True),    # AT&T Stadium (retractable)
    "DEN": (39.7439, -105.0201, False),  # Empower Field at Mile High
    "DET": (42.3400, -83.0456, True),    # Ford Field (dome)
    "GB":  (44.5013, -88.0622, False),   # Lambeau Field
    "HOU": (29.6847, -95.4107, True),    # NRG Stadium (retractable)
    "IND": (39.7601, -86.1639, True),    # Lucas Oil Stadium (retractable)
    "JAX": (30.3239, -81.6373, False),   # EverBank Stadium
    "KC":  (39.0489, -94.4839, False),   # GEHA Field at Arrowhead
    "LAC": (33.9535, -118.3392, True),   # SoFi Stadium (fixed roof)
    "LAR": (33.9535, -118.3392, True),   # SoFi Stadium (fixed roof)
    "LV":  (36.0909, -115.1833, True),   # Allegiant Stadium (dome)
    "MIA": (25.9580, -80.2389, False),   # Hard Rock Stadium (open-air)
    "MIN": (44.9737, -93.2578, True),    # U.S. Bank Stadium (dome)
    "NE":  (42.0909, -71.2643, False),   # Gillette Stadium
    "NO":  (29.9511, -90.0812, True),    # Caesars Superdome
    "NYG": (40.8135, -74.0745, False),   # MetLife Stadium
    "NYJ": (40.8135, -74.0745, False),   # MetLife Stadium
    "PHI": (39.9008, -75.1675, False),   # Lincoln Financial Field
    "PIT": (40.4468, -80.0158, False),   # Acrisure Stadium
    "SEA": (47.5952, -122.3316, False),  # Lumen Field
    "SF":  (37.4030, -121.9700, False),  # Levi's Stadium
    "TB":  (27.9759, -82.5033, False),   # Raymond James Stadium
    "TEN": (36.1665, -86.7713, False),   # Nissan Stadium
    "WAS": (38.9076, -77.0208, False),   # Northwest Stadium
}


@dataclass
class Forecast:
    wind_mph: float
    precip_pct: float  # probability of precipitation, 0-100
    temp_f: float
    short_forecast: str


def _http_get_json(url: str) -> Optional[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


def _nearest_period(periods: list[dict], when: datetime) -> Optional[dict]:
    """NWS forecasts come as a list of time windows; pick the one containing `when`."""
    best, best_delta = None, None
    for period in periods:
        start = datetime.fromisoformat(period["startTime"])
        end = datetime.fromisoformat(period["endTime"])
        when_cmp = when if when.tzinfo else when.astimezone()
        if start <= when_cmp <= end:
            return period
        delta = min(abs((start - when_cmp).total_seconds()), abs((end - when_cmp).total_seconds()))
        if best_delta is None or delta < best_delta:
            best, best_delta = period, delta
    return best


def fetch_forecast(team_abbr: str, when: datetime) -> Optional[Forecast]:
    """
    Returns a Forecast for the given team's stadium at the given time, or
    None if the stadium is weather-neutral (dome/retractable) or the NWS
    lookup fails (e.g. an international game outside the continental US).
    """
    entry = STADIUMS.get(team_abbr)
    if entry is None:
        return None
    lat, lon, neutral = entry
    if neutral:
        return None

    points = _http_get_json(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
    if not points or "properties" not in points:
        return None
    forecast_url = points["properties"].get("forecastHourly") or points["properties"].get("forecast")
    if not forecast_url:
        return None

    forecast = _http_get_json(forecast_url)
    if not forecast or "properties" not in forecast:
        return None
    periods = forecast["properties"].get("periods", [])
    period = _nearest_period(periods, when)
    if not period:
        return None

    wind_str = period.get("windSpeed", "0 mph")
    try:
        wind_mph = float(wind_str.split()[0])
    except (ValueError, IndexError):
        wind_mph = 0.0

    return Forecast(
        wind_mph=wind_mph,
        precip_pct=float(period.get("probabilityOfPrecipitation", {}).get("value") or 0),
        temp_f=float(period.get("temperature", 60)),
        short_forecast=period.get("shortForecast", ""),
    )


def weather_adjustment(forecast: Optional[Forecast]) -> float:
    """
    Multiplicative adjustment applied to a game's total projected scoring,
    given the forecast at kickoff. 1.0 = no adjustment (indoors, or no
    meaningful weather). High wind and heavy precipitation suppress passing
    volume/accuracy more than they help the run game, so net scoring drops;
    extreme cold has a smaller, similar dampening effect.
    """
    if forecast is None:
        return 1.0

    adj = 1.0
    if forecast.wind_mph >= 20:
        adj -= 0.06
    elif forecast.wind_mph >= 15:
        adj -= 0.03

    if forecast.precip_pct >= 60:
        adj -= 0.04
    elif forecast.precip_pct >= 30:
        adj -= 0.015

    if forecast.temp_f <= 20:
        adj -= 0.02

    return max(0.85, adj)
