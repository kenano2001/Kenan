"""
THE MODE — Fantasy Sportsbook Odds Model
=========================================
League: The Mode (Sleeper ID: 1312211986242621440)
Format: 10-team, 0.5 PPR, Superflex (2 QB), Dynasty/Keeper
Season: 14-week regular season, 6 playoff spots

USAGE
-----
This module produces American odds for every market used in the
HTML betting board. Feed it fresh weekly projections and it returns
a fully-priced odds board ready to drop into the front-end data object.

PIPELINE (in order)
-------------------
1. Input: projected starter totals + optional injury flags per player
2. Matchup win probability  →  moneyline / spread / total lines
3. Player prop lines        →  position-skewed O/U prices
4. Multi-way markets        →  TD scorer, top output, season futures
5. Book margin applied      →  all fair probs scaled by MARGIN factor
6. Output: dict of American odds per market
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# CONFIGURATION — tweak these to recalibrate the model
# ---------------------------------------------------------------------------

# Normal-distribution SD used to convert projected margin → win probability.
# Empirically, fantasy weekly team scores have a combined SD of ~28-32 pts.
TEAM_SD: float = 29.0

# Book margin (overround). 1.08 = 8% house edge applied across every market.
# Set to 1.0 for true no-vig / fair-value odds.
MARGIN: float = 1.08

# Position-based Under skew at the median projection line.
# Fantasy scoring is right-skewed (hard floor, open upside) so at the
# median projection, true P(Under) > 50%. Values are the raw probability
# assigned to the Under BEFORE margin is applied.
POSITION_SKEW: dict[str, float] = {
    "QB":  0.535,   # least volatile week-to-week
    "RB":  0.545,
    "WR":  0.555,   # boom/bust
    "TE":  0.550,
    "DEF": 0.565,   # most volatile / game-script dependent
}

# Additional Under probability bump for injury-risk players (stacked on top
# of position baseline). Applied when a player is flagged as "questionable".
INJURY_SKEW_BUMP: float = 0.025


# ---------------------------------------------------------------------------
# CORE MATH HELPERS
# ---------------------------------------------------------------------------

def win_prob_from_margin(margin_pts: float, sd: float = TEAM_SD) -> float:
    """
    P(team A wins) given projected margin = projA - projB.
    Uses a normal CDF approximation (error function).
    """
    z = margin_pts / sd
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def prob_to_american(p: float) -> int:
    """Convert a win probability to American odds (integer, rounded)."""
    p = max(0.001, min(0.999, p))
    if p >= 0.5:
        return round(-100 * p / (1 - p))
    else:
        return round(100 * (1 - p) / p)


def american_to_decimal(odds: int) -> float:
    """American odds → decimal multiplier."""
    if odds > 0:
        return 1 + odds / 100
    else:
        return 1 + 100 / abs(odds)


def decimal_to_american(d: float) -> int:
    if d >= 2:
        return round((d - 1) * 100)
    else:
        return round(-100 / (d - 1))


def apply_margin_to_prob(p: float, margin: float = MARGIN) -> float:
    """Scale a fair probability up by the book margin."""
    return p * margin


def price_two_way(p_side_a: float, margin: float = MARGIN) -> tuple[int, int]:
    """
    Given fair P(A), apply margin and return (american_A, american_B).
    p_side_a should be the FAIR probability (sums with complement to 1.0).
    """
    p_a = apply_margin_to_prob(p_side_a, margin)
    p_b = apply_margin_to_prob(1 - p_side_a, margin)
    return prob_to_american(p_a), prob_to_american(p_b)


def price_multiway(fair_probs: list[float], margin: float = MARGIN) -> list[int]:
    """
    Given a list of fair probabilities (should sum to ~1.0),
    apply margin and return American odds for each outcome.
    """
    scaled = [p * margin for p in fair_probs]
    return [prob_to_american(p) for p in scaled]


# ---------------------------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------------------------

@dataclass
class Player:
    name: str
    position: str          # "QB", "RB", "WR", "TE", "DEF"
    projection: float      # Sleeper/manual projected fantasy points
    questionable: bool = False
    out: bool = False      # If True, exclude from lineups entirely


@dataclass
class TeamLineup:
    team_name: str
    starters: list[Player] = field(default_factory=list)

    @property
    def projected_total(self) -> float:
        return sum(p.projection for p in self.starters if not p.out)


# ---------------------------------------------------------------------------
# MATCHUP LINE BUILDER
# ---------------------------------------------------------------------------

@dataclass
class MatchupOdds:
    team_a: TeamLineup
    team_b: TeamLineup
    margin: float = MARGIN
    team_sd: float = TEAM_SD

    # ----- computed properties -----

    @property
    def proj_margin(self) -> float:
        return self.team_a.projected_total - self.team_b.projected_total

    @property
    def win_prob_a(self) -> float:
        return win_prob_from_margin(self.proj_margin, self.team_sd)

    @property
    def win_prob_b(self) -> float:
        return 1 - self.win_prob_a

    # ----- game lines -----

    def moneyline(self) -> dict:
        odds_a, odds_b = price_two_way(self.win_prob_a, self.margin)
        return {
            self.team_a.team_name: odds_a,
            self.team_b.team_name: odds_b,
        }

    def spread(self, hook: float = 0.5) -> dict:
        """
        Main spread: set at the projected margin (rounded + half-point hook).
        The team giving points is the favourite.
        """
        raw = round(self.proj_margin)
        spread_val = raw + hook if raw >= 0 else raw - hook
        # Spread is a 50/50 market at fair odds; margin shifts it symmetrically
        odds_fav, odds_dog = price_two_way(0.5, self.margin)
        fav = self.team_a.team_name if self.proj_margin >= 0 else self.team_b.team_name
        dog = self.team_b.team_name if self.proj_margin >= 0 else self.team_a.team_name
        label_fav = f"{fav} -{abs(spread_val)}"
        label_dog = f"{dog} +{abs(spread_val)}"
        return {label_fav: odds_fav, label_dog: odds_dog}

    def total(self, hook: float = 0.5) -> dict:
        """
        Total O/U: projected sum of both teams + half-point hook.
        """
        proj_sum = self.team_a.projected_total + self.team_b.projected_total
        line = round(proj_sum) + hook
        odds_over, odds_under = price_two_way(0.5, self.margin)
        return {f"Over {line}": odds_over, f"Under {line}": odds_under}

    # ----- player props -----

    def player_props(self, team: TeamLineup) -> list[dict]:
        """
        For each starter, produce an O/U line at their projection with
        position-based skew applied. Returns list of market dicts.
        """
        props = []
        for player in team.starters:
            if player.out:
                continue
            base_skew = POSITION_SKEW.get(player.position, 0.545)
            p_under = base_skew + (INJURY_SKEW_BUMP if player.questionable else 0.0)
            p_under = min(p_under, 0.85)  # cap to avoid degenerate lines
            p_over = 1 - p_under
            odds_over, odds_under = price_two_way(p_over, self.margin)
            line = round(player.projection * 2) / 2  # round to nearest 0.5
            props.append({
                "label": f"{player.name} {line}",
                "Over": odds_over,
                "Under": odds_under,
            })
        return props

    def all_props(self) -> dict:
        return {
            self.team_a.team_name: self.player_props(self.team_a),
            self.team_b.team_name: self.player_props(self.team_b),
        }

    # ----- first TD / top output markets -----

    def td_scorer_market(
        self,
        candidates: list[tuple[str, float]],  # (name, raw_weight)
        field_weight: float = 0.15,
    ) -> dict:
        """
        Build a First TD scorer market.
        candidates: list of (player_name, raw_weight) — weights are relative,
                    not required to sum to 1.
        field_weight: probability mass reserved for "Field" (no named player).
        Returns dict of {name: american_odds}.
        """
        total_raw = sum(w for _, w in candidates)
        fair_probs = [(name, (w / total_raw) * (1 - field_weight)) for name, w in candidates]
        fair_probs.append(("Field", field_weight))
        probs = [p for _, p in fair_probs]
        names = [n for n, _ in fair_probs]
        odds = price_multiway(probs, self.margin)
        return dict(zip(names, odds))

    def top_output_market(
        self,
        candidates: list[tuple[str, float]],
        field_weight: float = 0.12,
    ) -> dict:
        """Same structure as td_scorer_market, for highest single score."""
        return self.td_scorer_market(candidates, field_weight)

    def top_score_line(self, proj_max: float, hook: float = 0.5) -> dict:
        """
        O/U on the highest individual player score in the matchup.
        proj_max: expected top output (typically the highest-projected player).
        """
        line = round(proj_max) + hook
        odds_over, odds_under = price_two_way(0.5, self.margin)
        return {f"Over {line}": odds_over, f"Under {line}": odds_under}

    # ----- convenience: full board -----

    def full_board(
        self,
        td_candidates: Optional[list[tuple[str, float]]] = None,
        top_candidates: Optional[list[tuple[str, float]]] = None,
        proj_max_score: Optional[float] = None,
    ) -> dict:
        """
        Returns the complete odds board for this matchup as a nested dict,
        matching the shape expected by the front-end JS data object.
        """
        board = {
            "game_lines": {
                "Moneyline": self.moneyline(),
                "Spread": self.spread(),
                "Total": self.total(),
            },
            "player_props": self.all_props(),
        }
        if td_candidates:
            board["first_td"] = self.td_scorer_market(td_candidates)
        if top_candidates:
            board["top_output"] = self.top_output_market(top_candidates)
        if proj_max_score is not None:
            board["top_score_line"] = self.top_score_line(proj_max_score)
        return board


# ---------------------------------------------------------------------------
# SEASON FUTURES BUILDER
# ---------------------------------------------------------------------------

def season_win_total_lines(
    team_win_totals: dict[str, float],
    alt_step: float = 2.0,
    margin: float = MARGIN,
    n_games: int = 14,
) -> dict[str, dict]:
    """
    For each team, produce main + 2 alt O/U lines on season win total.

    The alt-line variance uses a binomial approximation:
    - Per-game win prob p = win_total / n_games
    - Season SD ≈ sqrt(n_games * p * (1-p))
    - P(Over alt_line) = 1 - normal_cdf((alt_line + 0.5 - mean) / SD)

    Returns nested dict: {team_name: {label: {Over: odds, Under: odds}}}
    """
    results = {}
    for team, win_total in team_win_totals.items():
        p_per_game = win_total / n_games
        season_sd = math.sqrt(n_games * p_per_game * (1 - p_per_game))
        markets = {}

        for delta in [-alt_step, 0, alt_step]:
            line = win_total + delta
            label = f"{team} {line:.1f}".replace(".0", "")
            # P(actual wins > line) using normal approximation
            z_over = (line + 0.5 - win_total) / season_sd
            p_over_fair = 1 - 0.5 * (1 + math.erf(z_over / math.sqrt(2)))
            p_over_fair = max(0.005, min(0.995, p_over_fair))
            p_under_fair = 1 - p_over_fair
            odds_over, odds_under = price_two_way(p_over_fair, margin)
            markets[label] = {"Over": odds_over, "Under": odds_under}

        results[team] = markets
    return results


def season_championship_market(
    power_scores: dict[str, float],
    margin: float = MARGIN,
) -> dict[str, int]:
    """
    Championship futures. power_scores: {team_name: projected_weekly_pts}.
    Converts scores to relative championship probabilities using a
    softmax-style transformation (exponential weighting favours stronger teams).
    """
    scores = list(power_scores.values())
    names = list(power_scores.keys())
    # exponential weighting: stronger teams get disproportionately higher prob
    base = min(scores)
    weights = [math.exp((s - base) / 10) for s in scores]
    total = sum(weights)
    fair_probs = [w / total for w in weights]
    odds = price_multiway(fair_probs, margin)
    return dict(zip(names, odds))


def season_playoff_markets(
    playoff_probs: dict[str, float],  # {team: fair P(makes playoffs)}
    margin: float = MARGIN,
) -> dict[str, dict[str, int]]:
    """
    Yes/No playoff market per team.
    playoff_probs: manually estimated or model-derived fair probabilities.
    """
    results = {}
    for team, p_yes in playoff_probs.items():
        odds_yes, odds_no = price_two_way(p_yes, margin)
        results[team] = {"Yes": odds_yes, "No": odds_no}
    return results


# ---------------------------------------------------------------------------
# WEEK 1 EXAMPLE — replace each week with fresh projections
# ---------------------------------------------------------------------------

def week1_teams() -> list[tuple[str, TeamLineup, TeamLineup]]:
    """
    Returns (matchup_name, team_a, team_b) for every Week 1 matchup.
    Replace `projection=` values each week with fresh data from Sleeper
    or your preferred source. Shared by build_week1_board() and any other
    consumer (e.g. the front-end data generator) that needs the raw
    rosters rather than the priced board.
    """

    # ---- MATCHUP 1: CDZ NUTZ vs Mr. Big Chest ----
    cdz = TeamLineup("CDZ NUTZ", starters=[
        Player("Mayfield",       "QB",  19.78),
        Player("Darnold",        "QB",  18.71),
        Player("J.Taylor",       "RB",  17.36),
        Player("Hubbard",        "RB",  10.59),
        Player("Chase",          "WR",  16.45),
        Player("Smith-Njigba",   "WR",  15.85),
        Player("Lamb",           "WR",  14.12),
        Player("McBride",        "TE",  12.22),
        Player("DEN DEF",        "DEF",  7.51),
    ])
    big_chest = TeamLineup("Mr. Big Chest", starters=[
        Player("C.Williams",     "QB",  21.75),
        Player("C.Brown",        "RB",  14.63),
        Player("Barkley",        "RB",  12.83),
        Player("Henry",          "RB",  15.06),
        Player("Corum",          "RB",   8.73),
        Player("A.Brown",        "WR",  14.15),
        Player("Lemon",          "WR",   7.96, questionable=True),
        Player("Bowers",         "TE",  12.72),
        Player("NYG DEF",        "DEF",  5.76),
    ])

    # ---- MATCHUP 2: kyleullrich8 vs leagueisass ----
    kyle = TeamLineup("kyleullrich8", starters=[
        Player("Mahomes",        "QB",  17.83, questionable=True),
        Player("Prescott",       "QB",  20.88),
        Player("B.Robinson",     "RB",  19.55),
        Player("Gibbs",          "RB",  20.25, questionable=True),
        Player("McCaffrey",      "RB",  17.20, questionable=True),
        Player("Nabers",         "WR",  10.36, questionable=True),
        Player("Jefferson",      "WR",  13.82),
        Player("Loveland",       "TE",  12.42),
        Player("BAL DEF",        "DEF",  7.90),
    ])
    league = TeamLineup("leagueisass", starters=[
        Player("Goff",           "QB",  20.07),
        Player("D.Jones",        "QB",  17.32),
        Player("Love",           "RB",  13.69),
        Player("Etienne",        "RB",  10.74),
        Player("Judkins",        "RB",  10.19),
        Player("Pickens",        "WR",  13.59),
        Player("McConkey",       "WR",  10.79),
        Player("Kraft",          "TE",   9.60, questionable=True),
        Player("TB DEF",         "DEF",  5.64),
    ])

    # ---- MATCHUP 3: Buc-cee's vs fjoutlet ----
    buccees = TeamLineup("Buc-cee's", starters=[
        Player("Burrow",         "QB",  21.67),
        Player("Purdy",          "QB",  18.73),
        Player("Cook",           "RB",  16.06),
        Player("Irving",         "RB",  10.97),
        Player("Skattebo",       "RB",  12.63),
        Player("Harrison",       "WR",   9.01),
        Player("Nacua",          "WR",  16.28),
        Player("LaPorta",        "TE",   9.75),
        Player("SF DEF",         "DEF",  6.26),
    ])
    fjoutlet = TeamLineup("fjoutlet", starters=[
        Player("Dart",           "QB",  20.49),
        Player("Hurts",          "QB",  20.26),
        Player("K.Walker",       "RB",  14.08),
        Player("Hampton",        "RB",  13.96),
        Player("Rice",           "WR",  10.76),
        Player("D.Moore",        "WR",  10.04),
        Player("Olave",          "WR",  12.08),
        Player("Schultz",        "TE",   6.88),
        Player("HOU DEF",        "DEF",  7.42),
    ])

    # ---- MATCHUP 4: AZ Rapids vs omaralb ----
    az = TeamLineup("AZ Rapids", starters=[
        Player("J.Allen",        "QB",  23.30),
        Player("Nix",            "QB",  19.74),
        Player("Jacobs",         "RB",  12.77, questionable=True),
        Player("K.Williams",     "RB",  12.73),
        Player("T.Henderson",    "RB",  10.79),
        # Two empty WR slots — no player object added
        Player("Andrews",        "TE",   8.92),
        Player("PIT DEF",        "DEF",  8.61),
    ])
    omar = TeamLineup("omaralb", starters=[
        Player("L.Jackson",      "QB",  22.14),
        Player("B.Hall",         "RB",  12.57),
        Player("Swift",          "RB",  11.65),
        Player("St.Brown",       "WR",  16.26),
        Player("Collins",        "WR",  13.99),
        Player("Flowers",        "WR",  13.32, questionable=True),
        Player("D.Smith",        "WR",  12.23, questionable=True),
        Player("Kelce",          "TE",   9.28),
        Player("DET DEF",        "DEF",  7.78),
    ])

    # ---- MATCHUP 5: The Sopranos vs The Notorious Ones ----
    sopranos = TeamLineup("The Sopranos", starters=[
        Player("Herbert",        "QB",  19.68),
        Player("Lawrence",       "QB",  19.57),
        Player("Achane",         "RB",  15.89),
        Player("J.Warren",       "RB",  11.71),
        Player("McMillan",       "WR",  11.72),
        Player("Burden",         "WR",  10.49, questionable=True),
        Player("Tyson",          "WR",   8.96),
        Player("T.Warren",       "TE",  10.66),
        Player("PHI DEF",        "DEF",  8.15),
    ])
    notorious = TeamLineup("The Notorious Ones", starters=[
        Player("Maye",           "QB",  20.03),
        Player("Daniels",        "QB",  20.95),
        Player("Jeanty",         "RB",  13.31),
        Player("J.Williams",     "RB",  13.44),
        Player("London",         "WR",  12.76),
        Player("Higgins",        "WR",  13.92),
        Player("Waddle",         "WR",  12.19, questionable=True),
        Player("H.Henry",        "TE",   7.60),
        Player("SEA DEF",        "DEF",  9.11),
    ])

    return [
        ("CDZ NUTZ vs Mr. Big Chest",     cdz,      big_chest),
        ("kyleullrich8 vs leagueisass",   kyle,     league),
        ("Buc-cee's vs fjoutlet",         buccees,  fjoutlet),
        ("AZ Rapids vs omaralb",          az,       omar),
        ("The Sopranos vs The Notorious", sopranos, notorious),
    ]


# ---- SEASON FUTURES INPUTS ----

def week1_season_inputs() -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """Returns (power_scores, win_totals, playoff_probs) for Week 1 futures."""
    power_scores = {
        "kyleullrich8":      142.21,
        "CDZ NUTZ":          132.59,
        "The Notorious Ones": 123.31,
        "fjoutlet":          122.21,
        "omaralb":           119.23,
        "The Sopranos":      116.83,
        "Buc-cee's":         115.11,
        "Mr. Big Chest":     113.59,
        "leagueisass":       109.63,
        "AZ Rapids":          96.85,
    }
    # Main season win totals (pre-model medians; adjust each week as data accumulates)
    win_totals = {
        "kyleullrich8":       9.5,
        "CDZ NUTZ":           9.5,
        "The Notorious Ones": 8.5,
        "fjoutlet":           8.5,
        "omaralb":            7.5,
        "The Sopranos":       7.5,
        "Buc-cee's":          6.5,
        "Mr. Big Chest":      6.5,
        "leagueisass":        5.5,
        "AZ Rapids":          4.5,
    }
    # Fair (no-vig) playoff probabilities — estimated from power ranking
    playoff_probs = {
        "kyleullrich8":       0.88,
        "CDZ NUTZ":           0.80,
        "The Notorious Ones": 0.65,
        "fjoutlet":           0.62,
        "omaralb":            0.55,
        "The Sopranos":       0.52,
        "Buc-cee's":          0.48,
        "Mr. Big Chest":      0.42,
        "leagueisass":        0.32,
        "AZ Rapids":          0.16,
    }

    return power_scores, win_totals, playoff_probs


def build_week1_board() -> dict:
    """
    Builds the full Week 1 odds board using the projections established
    in the session. Replace the `projection=` values in week1_teams() each
    week with fresh data from Sleeper or your preferred source.
    """
    power_scores, win_totals, playoff_probs = week1_season_inputs()

    board = {
        "matchups": {
            name: MatchupOdds(team_a, team_b).full_board()
            for name, team_a, team_b in week1_teams()
        },
        "season": {
            "championship":  season_championship_market(power_scores),
            "most_pf":       season_championship_market(power_scores),  # same weights, different market
            "playoffs":      season_playoff_markets(playoff_probs),
            "win_totals":    season_win_total_lines(win_totals),
        },
    }
    return board


# ---------------------------------------------------------------------------
# PARLAY CALCULATOR  (mirrors the front-end JS logic exactly)
# ---------------------------------------------------------------------------

def parlay_odds(legs: list[int]) -> int:
    """
    Given a list of American odds (one per leg), return combined parlay odds.
    Matches the decimal-multiplication method used in the front-end JS.
    """
    decimal = 1.0
    for o in legs:
        decimal *= american_to_decimal(o)
    return decimal_to_american(decimal)


def parlay_payout(legs: list[int], stake: float) -> float:
    """Return gross payout (stake + profit) for a parlay."""
    decimal = 1.0
    for o in legs:
        decimal *= american_to_decimal(o)
    return round(stake * decimal, 2)


# ---------------------------------------------------------------------------
# QUICK SANITY CHECK
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    board = build_week1_board()

    # Print moneylines for all 5 matchups
    print("=" * 60)
    print("THE MODE — WEEK 1 MONEYLINES")
    print("=" * 60)
    for matchup_name, data in board["matchups"].items():
        ml = data["game_lines"]["Moneyline"]
        teams = list(ml.items())
        print(f"\n{matchup_name}")
        for team, odds in teams:
            sign = "+" if odds > 0 else ""
            print(f"  {team}: {sign}{odds}")

    # Print championship futures
    print("\n" + "=" * 60)
    print("CHAMPIONSHIP FUTURES")
    print("=" * 60)
    for team, odds in sorted(board["season"]["championship"].items(), key=lambda x: x[1]):
        sign = "+" if odds > 0 else ""
        print(f"  {team}: {sign}{odds}")

    # Print a sample 3-leg parlay
    sample_legs = [-408, 321, 125]   # CDZ NUTZ ML, AZ Rapids ML, Sopranos ML
    print(f"\n3-leg parlay {sample_legs}: {parlay_odds(sample_legs):+d}")
    print(f"$10 stake payout: ${parlay_payout(sample_legs, 10):.2f}")
