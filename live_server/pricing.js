"use strict";
/**
 * Live pricing model, shared by team moneylines and player props.
 *
 * Fixes two gaps a historical-week demo run exposed in the earlier
 * (Apps Script) live engine:
 *
 *   1. Every game started flat at 50/50 with identical -117/-117 odds,
 *      because the model only looked at already-scored points and ignored
 *      each team's pregame projection entirely -- so a team loaded with
 *      talent that just hadn't kicked off yet looked identical to a team
 *      that had already busted.
 *   2. The shrinking-uncertainty curve was tied to a made-up "combined
 *      points / 240" proxy instead of anything about the actual game
 *      state, so it didn't reflect how many players/games were genuinely
 *      still live.
 *
 * Both are fixed by tracking, per player: already-scored points (live),
 * their pregame projection, and whether their real NFL game has actually
 * finished (from ESPN). A team's implied total is the sum, across
 * starters, of "whichever is more informative" -- their actual score once
 * it's known to be final, otherwise the better of what they've already
 * produced or what they were projected for (they haven't lost the chance
 * to hit their projection just because they haven't yet). The shrinking
 * SD is tied to what fraction of total pregame-projected production is
 * still genuinely uncertain (games not yet final), not a generic points
 * heuristic.
 */

const TEAM_SD = 29.0;
const PLAYER_SD_FLOOR_FRACTION = 0.25;
const TEAM_SD_FLOOR_FRACTION = 0.15;
const BOOK_MARGIN = 1.08;

function erf(x) {
  const sign = x < 0 ? -1 : 1;
  x = Math.abs(x);
  const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741, a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
  const t = 1 / (1 + p * x);
  const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);
  return sign * y;
}

function winProbFromMargin(marginPts, sd) {
  return 0.5 * (1 + erf((marginPts / sd) / Math.sqrt(2)));
}

function probToAmerican(prob) {
  const p = Math.max(0.005, Math.min(0.995, prob));
  return p >= 0.5 ? Math.round(-100 * p / (1 - p)) : Math.round(100 * (1 - p) / p);
}

function americanToDecimal(odds) {
  return odds > 0 ? 1 + odds / 100 : 1 + 100 / Math.abs(odds);
}

function priceTwoWay(pSideA, margin = BOOK_MARGIN) {
  return [probToAmerican(pSideA * margin), probToAmerican((1 - pSideA) * margin)];
}

/**
 * playerState: {
 *   liveScore: number,
 *   pregameProjection: number,
 *   gameFractionRemaining: number  -- 0 (game over) to 1 (hasn't started),
 *     derived from that player's own real NFL game clock, NOT this
 *     fantasy matchup's clock.
 * }
 *
 * Scaling the outstanding projection by real time remaining (rather than
 * just "projection minus what's in, floored at zero") is what makes the
 * odds move continuously through the game instead of sitting flat until
 * a player either blows past their projection or their game ends --
 * every poll, every player's remaining upside ticks down a little just
 * from the clock, on top of whatever they actually produced that tick.
 */
function playerRemaining(playerState) {
  const outstanding = Math.max(0, (playerState.pregameProjection || 0) - playerState.liveScore);
  const frac = playerState.gameFractionRemaining ?? (playerState.gameFinal ? 0 : 1);
  return outstanding * Math.max(0, Math.min(1, frac));
}

function playerImpliedTotal(playerState) {
  return playerState.liveScore + playerRemaining(playerState);
}

/**
 * roster: array of playerState objects for one team's starters.
 * Returns { impliedTotal, remainingFraction } where remainingFraction is
 * this team's share of pregame-projected production that's still
 * genuinely uncertain (0 = everyone's game is over, 1 = nobody's started).
 */
function teamImplied(roster) {
  let impliedTotal = 0, totalProjection = 0, totalRemaining = 0;
  for (const p of roster) {
    impliedTotal += playerImpliedTotal(p);
    totalProjection += p.pregameProjection || 0;
    totalRemaining += playerRemaining(p);
  }
  const remainingFraction = totalProjection > 0 ? totalRemaining / totalProjection : 0;
  return { impliedTotal, remainingFraction };
}

/**
 * Prices a team moneyline market from two rosters (arrays of playerState).
 * remainingFraction is averaged across both teams (weighted by their own
 * projected total) so a team with players left to play keeps the whole
 * market's uncertainty from collapsing even if their opponent is done.
 */
function priceMatchup(rosterA, rosterB) {
  const a = teamImplied(rosterA);
  const b = teamImplied(rosterB);
  const totalProjA = rosterA.reduce((s, p) => s + (p.pregameProjection || 0), 0);
  const totalProjB = rosterB.reduce((s, p) => s + (p.pregameProjection || 0), 0);
  const totalProj = totalProjA + totalProjB;
  const blendedRemainingFraction = totalProj > 0
    ? (a.remainingFraction * totalProjA + b.remainingFraction * totalProjB) / totalProj
    : 1;
  const sd = TEAM_SD * Math.sqrt(Math.max(TEAM_SD_FLOOR_FRACTION, blendedRemainingFraction));
  const margin = a.impliedTotal - b.impliedTotal;
  const winProbA = winProbFromMargin(margin, sd);
  const [moneylineA, moneylineB] = priceTwoWay(winProbA);
  return { impliedTotalA: a.impliedTotal, impliedTotalB: b.impliedTotal, winProbA, moneylineA, moneylineB, sd };
}

/**
 * Prices a single player's point-total O/U prop. sd shrinks with how much
 * of THEIR OWN projection is still outstanding, floored so an in-progress
 * player never looks fully "decided" the way a completed one does.
 */
function pricePlayerProp(playerState, playerWeeklySd) {
  const implied = playerImpliedTotal(playerState);
  const remaining = playerRemaining(playerState);
  const proj = playerState.pregameProjection || 0;
  const remainingFraction = proj > 0 ? remaining / proj : (playerState.gameFractionRemaining ?? 1);
  const sd = playerWeeklySd * Math.sqrt(Math.max(PLAYER_SD_FLOOR_FRACTION, remainingFraction));
  const line = Math.round(implied * 2) / 2; // nearest half-point, centered on current implied total
  const [over, under] = priceTwoWay(0.5); // symmetric around the live-adjusted line itself
  return { line, over, under, implied, sd };
}

module.exports = {
  TEAM_SD, BOOK_MARGIN,
  erf, winProbFromMargin, probToAmerican, americanToDecimal, priceTwoWay,
  playerImpliedTotal, playerRemaining, teamImplied, priceMatchup, pricePlayerProp,
};
