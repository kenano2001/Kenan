"""
Pulls live weekly data from Sleeper (+ ESPN for kickoff/venue, + NWS for
weather) and applies it to a week's TeamLineups, so the board reflects
real projection/injury/lineup movement through the week instead of a
static snapshot.

Usage:
    from sleeper_live import apply_live_data
    teams = odds_model.week1_teams()
    apply_live_data(teams, season="2026", week=1)   # mutates player projections in place

This module is intentionally side-effect-light and network-call-light: it
fetches each of its three feeds once per call, not once per player, so a
weekly refresh (or a manual rerun before publishing) stays cheap. The
Apps Script live-odds engine (in-game, minute-by-minute) is a separate,
faster-cadence system that reuses the same weather math but reads live
scores rather than projections — see live_odds.gs.
"""

from __future__ import annotations
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from nfl_weather import fetch_forecast, weather_adjustment
from odds_model import Player, TeamLineup

USER_AGENT = "fantasize-odds-model (contact: kenano2001@gmail.com)"

LEAGUE_ID = "1312211986242621440"  # "The Mode"

SLEEPER_PROJECTIONS_URL = "https://api.sleeper.app/projections/nfl/{season}/{week}?season_type=regular"
SLEEPER_ROSTERS_URL = "https://api.sleeper.app/v1/league/{league_id}/rosters"
SLEEPER_USERS_URL = "https://api.sleeper.app/v1/league/{league_id}/users"
SLEEPER_MATCHUPS_URL = "https://api.sleeper.app/v1/league/{league_id}/matchups/{week}"
SLEEPER_PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?week={week}&seasontype=2&year={season}"

# Sleeper's full player dump is ~15MB and only changes daily -- cache it to
# disk instead of refetching every run. Not checked into git (see .gitignore).
PLAYERS_CACHE_PATH = Path(__file__).parent / ".sleeper_players_cache.json"
PLAYERS_CACHE_MAX_AGE_HOURS = 24

# The Apps Script deployment already used for auth/bet-logging also serves
# a read-only ?action=overrides endpoint for the "Lineup Overrides" sheet
# tab. Server-to-server requests (this script calling Apps Script) aren't
# subject to browser CORS, so this works even though a browser fetch()
# against the same URL wouldn't (see sportsbook.template.html's history).
APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbx_nCEKgDRY7wYPMMYkQRFtbPfg5UGaNNyVJU3bKDW4u0V8p7naeO8tHQTXxEZC0OL8Wg/exec"

INJURY_OUT_STATUSES = {"Out", "IR", "PUP", "Suspended", "Doubtful"}
INJURY_QUESTIONABLE_STATUSES = {"Questionable"}


def _http_get_json(url: str) -> Optional[object]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


def fetch_projections(season: str, week: int) -> dict[str, dict]:
    """sleeper_id -> {"pts": float|None, "injury_status": str|None}"""
    data = _http_get_json(SLEEPER_PROJECTIONS_URL.format(season=season, week=week))
    if not data:
        return {}
    out = {}
    for entry in data:
        pid = entry.get("player_id")
        if not pid:
            continue
        stats = entry.get("stats") or {}
        player = entry.get("player") or {}
        out[pid] = {
            "pts": stats.get("pts_half_ppr"),
            "injury_status": player.get("injury_status"),
            "team": entry.get("team") or player.get("team"),
        }
    return out


def fetch_schedule(season: str, week: int) -> dict[str, dict]:
    """team_abbr -> {"kickoff": datetime, "opponent": str}"""
    data = _http_get_json(ESPN_SCOREBOARD_URL.format(season=season, week=week))
    if not data:
        return {}
    out = {}
    for event in data.get("events", []):
        for comp in event.get("competitions", []):
            kickoff_str = comp.get("date")
            try:
                kickoff = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00")) if kickoff_str else None
            except ValueError:
                kickoff = None
            competitors = comp.get("competitors", [])
            teams = {c.get("homeAway"): (c.get("team") or {}).get("abbreviation") for c in competitors}
            home_abbr = teams.get("home")
            away_abbr = teams.get("away")
            if home_abbr:
                out[home_abbr] = {"kickoff": kickoff, "opponent": away_abbr, "venue_team": home_abbr}
            if away_abbr:
                out[away_abbr] = {"kickoff": kickoff, "opponent": home_abbr, "venue_team": home_abbr}
    return out


def _load_players_cache() -> dict:
    if PLAYERS_CACHE_PATH.exists():
        age_hours = (time.time() - PLAYERS_CACHE_PATH.stat().st_mtime) / 3600
        if age_hours < PLAYERS_CACHE_MAX_AGE_HOURS:
            try:
                return json.loads(PLAYERS_CACHE_PATH.read_text())
            except (json.JSONDecodeError, OSError):
                pass
    data = _http_get_json(SLEEPER_PLAYERS_URL)
    if not isinstance(data, dict):
        return {}
    try:
        PLAYERS_CACHE_PATH.write_text(json.dumps(data))
    except OSError:
        pass
    return data


def _team_name_by_roster_id(league_id: str) -> dict[int, str]:
    rosters = _http_get_json(SLEEPER_ROSTERS_URL.format(league_id=league_id)) or []
    users = _http_get_json(SLEEPER_USERS_URL.format(league_id=league_id)) or []
    user_by_id = {u["user_id"]: u for u in users}
    out = {}
    for r in rosters:
        owner = user_by_id.get(r.get("owner_id"), {})
        meta = owner.get("metadata") or {}
        name = meta.get("team_name") or owner.get("display_name") or f"roster{r['roster_id']}"
        out[r["roster_id"]] = name.strip()
    return out


def _player_display(pid: str, players: dict) -> tuple[str, str]:
    """(display_name, position) for a Sleeper player_id, including DEF entries (keyed by team abbreviation)."""
    meta = players.get(pid) or {}
    position = meta.get("position", "")
    if position == "DEF":
        return f"{meta.get('team', pid)} DEF", "DEF"
    first, last = meta.get("first_name", ""), meta.get("last_name", "")
    name = (f"{first} {last}".strip()) if (first or last) else pid
    return name, position


def fetch_real_starters(week: int, league_id: str = LEAGUE_ID) -> dict[str, list[dict]]:
    """
    team_name -> starter list (name/sleeper_id/position), read directly from
    each manager's actual live Sleeper lineup for this week -- the same
    lineup they set in the Sleeper app, no manual entry required.
    """
    matchups = _http_get_json(SLEEPER_MATCHUPS_URL.format(league_id=league_id, week=week))
    if not isinstance(matchups, list):
        return {}
    team_names = _team_name_by_roster_id(league_id)
    players = _load_players_cache()

    out = {}
    for m in matchups:
        team_name = team_names.get(m.get("roster_id"))
        starters = [pid for pid in (m.get("starters") or []) if pid and pid != "0"]
        if not team_name or not starters:
            continue
        out[team_name] = [
            {"name": (d := _player_display(pid, players))[0], "sleeper_id": pid, "position": d[1]}
            for pid in starters
        ]
    return out


def apply_real_lineups(teams: list[tuple[str, TeamLineup, TeamLineup]], week: int, league_id: str = LEAGUE_ID) -> list[str]:
    """
    Replaces each team's starters with whatever that manager has actually
    set as their live Sleeper lineup for this week. This is the default
    lineup source now -- call apply_lineup_overrides() afterward so a manual
    override (private-league edge cases Sleeper doesn't capture correctly)
    still wins if one is on file for that team/week.
    """
    real = fetch_real_starters(week, league_id)
    if not real:
        return []
    notes = []
    for _, team_a, team_b in teams:
        for team in (team_a, team_b):
            starters = real.get(team.team_name.strip())
            if not starters:
                continue
            old_by_id = {p.sleeper_id: p.name for p in team.starters if p.sleeper_id}
            new_by_id = {r["sleeper_id"]: r["name"] for r in starters}
            out_names = [old_by_id[pid] for pid in old_by_id if pid not in new_by_id]
            in_names = [new_by_id[pid] for pid in new_by_id if pid not in old_by_id]
            if out_names or in_names:
                diff = []
                if out_names: diff.append("OUT: " + ", ".join(out_names))
                if in_names: diff.append("IN: " + ", ".join(in_names))
                notes.append(f"{team.team_name}: lineup change -- {'; '.join(diff)}")
            team.starters = [
                Player(name=r["name"], position=r["position"], projection=0.0, sleeper_id=r["sleeper_id"])
                for r in starters
            ]
    return notes


def fetch_lineup_overrides(week: int) -> dict[str, list[dict]]:
    """
    team_name -> full replacement starter list, for teams with a manual
    override on the "Lineup Overrides" sheet tab for this week (private
    league, so Sleeper's own reported lineup isn't always trustworthy).
    Each entry: {"name": str, "sleeper_id": str, "position": str}.
    Teams with no override rows for this week are simply absent.
    """
    url = APPS_SCRIPT_URL + "?" + urllib.parse.urlencode({"action": "overrides", "week": week})
    data = _http_get_json(url)
    if not isinstance(data, dict) or "overrides" not in data:
        return {}
    return data["overrides"]


def apply_lineup_overrides(teams: list[tuple[str, TeamLineup, TeamLineup]], week: int) -> list[str]:
    """
    Replaces a team's entire starters list with its manual override, if one
    exists for this week. Overridden players start with projection=0 -- run
    apply_live_data() afterward to fill in real projections by sleeper_id.
    """
    overrides = fetch_lineup_overrides(week)
    if not overrides:
        return []
    notes = []
    for _, team_a, team_b in teams:
        for team in (team_a, team_b):
            override = overrides.get(team.team_name.strip())
            if not override:
                continue
            team.starters = [
                Player(
                    name=row["name"],
                    position=row["position"],
                    projection=0.0,
                    sleeper_id=row.get("sleeper_id", ""),
                )
                for row in override
            ]
            notes.append(f"{team.team_name}: lineup replaced with manual override ({len(override)} starters)")
    return notes


def apply_live_data(
    teams: list[tuple[str, TeamLineup, TeamLineup]],
    season: str,
    week: int,
    apply_weather: bool = True,
) -> list[str]:
    """
    Mutates each Player's projection/questionable/out fields in place using
    live Sleeper projections + injury status, and (if apply_weather) a
    weather adjustment for their real NFL game's venue. Returns a list of
    human-readable change notes for logging/debugging.
    """
    projections = fetch_projections(season, week)
    schedule = fetch_schedule(season, week) if apply_weather else {}
    weather_cache: dict[str, float] = {}
    notes: list[str] = []

    for _, team_a, team_b in teams:
        for team in (team_a, team_b):
            for player in team.starters:
                if not player.sleeper_id:
                    continue
                live = projections.get(player.sleeper_id)
                if not live:
                    continue

                if live["pts"] is not None and abs(live["pts"] - player.projection) > 0.05:
                    notes.append(f"{player.name}: {player.projection:.2f} -> {live['pts']:.2f} (Sleeper projection)")
                    player.projection = round(live["pts"], 2)

                status = live.get("injury_status")
                was_out, was_q = player.out, player.questionable
                player.out = status in INJURY_OUT_STATUSES
                player.questionable = status in INJURY_QUESTIONABLE_STATUSES
                if player.out != was_out or player.questionable != was_q:
                    notes.append(f"{player.name}: injury_status -> {status!r}")

                nfl_team = live.get("team")
                if apply_weather and nfl_team and not player.out:
                    if nfl_team not in weather_cache:
                        game = schedule.get(nfl_team)
                        if game and game["kickoff"]:
                            forecast = fetch_forecast(game["venue_team"], game["kickoff"])
                            weather_cache[nfl_team] = weather_adjustment(forecast)
                        else:
                            weather_cache[nfl_team] = 1.0
                    adj = weather_cache[nfl_team]
                    if adj != 1.0:
                        before = player.projection
                        player.projection = round(player.projection * adj, 2)
                        notes.append(f"{player.name}: weather adj x{adj:.2f} ({before:.2f} -> {player.projection:.2f})")

    return notes
