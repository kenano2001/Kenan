"""
Builds the JS data object consumed by the sportsbook front-end
(sportsbook.html). Reuses odds_model.py for every price calculation so the
board shown in the browser is always identical to what the model computes —
this script only adds display metadata (position tags, injury flags,
TD-scorer / top-output candidate pools) that the pricing engine itself
doesn't need to track.

Run after updating week1_teams() / week1_season_inputs() in odds_model.py:

    python3 generate_board_data.py > board_data.js
"""

from __future__ import annotations
import json

from odds_model import (
    MatchupOdds,
    Player,
    TeamLineup,
    season_championship_market,
    season_playoff_markets,
    season_win_total_lines,
    week1_season_inputs,
    week1_teams,
)

SKILL_POSITIONS = ("RB", "WR", "TE")


def team_props_with_metadata(matchup: MatchupOdds, team: TeamLineup) -> list[dict]:
    starters = [p for p in team.starters if not p.out]
    props = matchup.player_props(team)
    enriched = []
    for player, prop in zip(starters, props):
        enriched.append({
            **prop,
            "position": player.position,
            "questionable": player.questionable,
        })
    return enriched


def td_candidates_for(team_a: TeamLineup, team_b: TeamLineup) -> list[tuple[str, float]]:
    pool = [p for p in (*team_a.starters, *team_b.starters) if p.position in SKILL_POSITIONS and not p.out]
    return [(p.name, p.projection) for p in pool]


def top_output_candidates_for(team_a: TeamLineup, team_b: TeamLineup) -> list[tuple[str, float]]:
    pool = [p for p in (*team_a.starters, *team_b.starters) if p.position != "DEF" and not p.out]
    return [(p.name, p.projection) for p in pool]


def build_matchup_entry(name: str, team_a: TeamLineup, team_b: TeamLineup) -> dict:
    m = MatchupOdds(team_a, team_b)
    td_candidates = sorted(td_candidates_for(team_a, team_b), key=lambda t: -t[1])[:6]
    top_candidates = sorted(top_output_candidates_for(team_a, team_b), key=lambda t: -t[1])[:6]
    proj_max = max(w for _, w in top_candidates)

    return {
        "team_a": team_a.team_name,
        "team_b": team_b.team_name,
        "proj_a": round(team_a.projected_total, 2),
        "proj_b": round(team_b.projected_total, 2),
        "game_lines": {
            "Moneyline": m.moneyline(),
            "Spread": m.spread(),
            "Total": m.total(),
        },
        "player_props": {
            team_a.team_name: team_props_with_metadata(m, team_a),
            team_b.team_name: team_props_with_metadata(m, team_b),
        },
        "first_td": m.td_scorer_market(td_candidates),
        "top_output": m.top_output_market(top_candidates),
        "top_score_line": m.top_score_line(proj_max),
    }


def build_board(live: bool = False) -> dict:
    power_scores, win_totals, playoff_probs = week1_season_inputs()

    teams = week1_teams()
    lock_times: dict[str, str | None] = {}
    if live:
        from sleeper_live import apply_real_lineups, apply_lineup_overrides, apply_live_data, compute_matchup_lock_times
        for note in apply_real_lineups(teams, week=1):
            print(f"[live] {note}")
        for note in apply_lineup_overrides(teams, week=1):
            print(f"[live] {note}")
        for note in apply_live_data(teams, season="2026", week=1):
            print(f"[live] {note}")
        lock_times = compute_matchup_lock_times(teams, week=1)

    matchups = {
        name: {**build_matchup_entry(name, team_a, team_b), "lock_at": lock_times.get(name)}
        for name, team_a, team_b in teams
    }

    team_meta = {
        team: {
            "power_score": power_scores[team],
            "win_total": win_totals[team],
            "playoff_prob": playoff_probs[team],
        }
        for team in power_scores
    }

    return {
        "week": 1,
        "matchups": matchups,
        "season": {
            "championship": season_championship_market(power_scores),
            "most_pf": season_championship_market(power_scores),
            "playoffs": season_playoff_markets(playoff_probs),
            "win_totals": season_win_total_lines(win_totals),
            "team_meta": team_meta,
        },
    }


if __name__ == "__main__":
    board = build_board()
    print("const BOARD_DATA = " + json.dumps(board, indent=2) + ";")
