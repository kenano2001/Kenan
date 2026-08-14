"""
Regenerates live_server/roster.json from the CURRENT real Sleeper lineups
(same source as build_sportsbook.py --live's auto lineup pull), so the
always-on live-odds server prices in-game markets against who's actually
starting, not a stale manual snapshot. Manual "Lineup Overrides" sheet
entries still take precedence, same as the pregame board.

Run standalone or from the scheduled lineup-check routine:

    python3 regenerate_live_roster.py
"""
from __future__ import annotations
import json
from pathlib import Path

from odds_model import week1_teams
from sleeper_live import apply_real_lineups, apply_lineup_overrides

ROOT = Path(__file__).parent
OUT_PATH = ROOT / "live_server" / "roster.json"


def main() -> None:
    teams = week1_teams()
    for note in apply_real_lineups(teams, week=1):
        print(f"[roster] {note}")
    for note in apply_lineup_overrides(teams, week=1):
        print(f"[roster] {note}")

    roster: dict[str, list[dict]] = {}
    matchups: list[dict] = []
    for name, team_a, team_b in teams:
        roster[team_a.team_name] = [
            {"name": p.name, "sleeper_id": p.sleeper_id, "position": p.position} for p in team_a.starters
        ]
        roster[team_b.team_name] = [
            {"name": p.name, "sleeper_id": p.sleeper_id, "position": p.position} for p in team_b.starters
        ]
        matchups.append({"name": name, "teamA": team_a.team_name, "teamB": team_b.team_name})

    OUT_PATH.write_text(json.dumps({"roster": roster, "matchups": matchups}, indent=2) + "\n")
    print(f"Wrote {OUT_PATH} ({OUT_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
