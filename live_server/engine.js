"use strict";
const fs = require("fs");
const path = require("path");
const sleeper = require("./sleeper.js");
const espn = require("./espn.js");
const pricing = require("./pricing.js");

const ROSTER = JSON.parse(fs.readFileSync(path.join(__dirname, "roster.json"), "utf8"));

const SEASON = "2026";
const CURRENT_WEEK = 1; // bump manually each week
const POLL_INTERVAL_MS = 20 * 1000;
const SUSPEND_SWING_THRESHOLD = 0.15;
const SUSPEND_MIN_MARGIN_DELTA = 8;
const PROP_SUSPEND_POINTS_DELTA = 4; // player props swing on raw implied-points movement, not win-prob (their O/U price is always ~-117/-117 by design -- the LINE moves instead)
const SUSPEND_DURATION_MS = 20 * 1000;
const PROJECTIONS_REFRESH_MS = 30 * 60 * 1000; // pregame projections change slowly; no need to refetch every poll

// roster_id -> team name, derived once from roster.json's matchups (team
// name is the only stable identifier we keep; roster_id mapping itself
// comes from Sleeper's own rosters endpoint the first time we need it).
let rosterIdToTeam = null;

let state = {
  odds: {}, // matchupName -> { teamA, teamB, impliedTotalA, impliedTotalB, winProbA, moneylineA, moneylineB, sd, updatedAt }
  props: {}, // playerName -> { line, over, under, implied, updatedAt }
  suspended: {}, // matchupName -> timestamp when suspended
  mode: "pregame",
  lastUpdate: 0,
};

let cachedProjections = null;
let cachedProjectionsAt = 0;

function getState() { return state; }

async function ensureRosterIdMap() {
  if (rosterIdToTeam) return rosterIdToTeam;
  const https = require("https");
  const data = await new Promise((resolve, reject) => {
    https.get(`https://api.sleeper.app/v1/league/${sleeper.LEAGUE_ID}/rosters`, { headers: { "User-Agent": "fantasize-live-server" } }, (res) => {
      let body = "";
      res.on("data", (c) => { body += c; });
      res.on("end", () => { try { resolve(JSON.parse(body)); } catch (e) { reject(e); } });
    }).on("error", reject);
  });
  const usersData = await new Promise((resolve, reject) => {
    https.get(`https://api.sleeper.app/v1/league/${sleeper.LEAGUE_ID}/users`, { headers: { "User-Agent": "fantasize-live-server" } }, (res) => {
      let body = "";
      res.on("data", (c) => { body += c; });
      res.on("end", () => { try { resolve(JSON.parse(body)); } catch (e) { reject(e); } });
    }).on("error", reject);
  });
  const uidToName = {};
  for (const u of usersData) {
    uidToName[u.user_id] = ((u.metadata || {}).team_name) || u.display_name;
  }
  const map = {};
  for (const r of data) {
    const name = (uidToName[r.owner_id] || "").trim();
    // roster.json's team names are the source of truth; match by trimmed name.
    const match = Object.keys(ROSTER.roster).find((t) => t.trim() === name);
    if (match) map[r.roster_id] = match;
  }
  rosterIdToTeam = map;
  return map;
}

async function getProjections() {
  const now = Date.now();
  if (cachedProjections && now - cachedProjectionsAt < PROJECTIONS_REFRESH_MS) return cachedProjections;
  cachedProjections = await sleeper.fetchProjections(SEASON, CURRENT_WEEK);
  cachedProjectionsAt = now;
  return cachedProjections;
}

function buildPlayerState(player, livePoints, projections, gameClocks) {
  const proj = projections[player.sleeper_id];
  const pregameProjection = proj && proj.pts != null ? proj.pts : 0;
  const nflTeam = proj ? proj.team : null;
  const clock = nflTeam ? gameClocks[nflTeam] : null;
  return {
    liveScore: livePoints || 0,
    pregameProjection,
    gameFractionRemaining: clock ? clock.fractionRemaining : 1,
  };
}

async function pollOnce() {
  const [liveMatchups, projections, gameClocks, idMap] = await Promise.all([
    sleeper.fetchLiveMatchups(CURRENT_WEEK),
    getProjections(),
    espn.fetchGameClocks(CURRENT_WEEK, SEASON).catch(() => ({})),
    ensureRosterIdMap(),
  ]);

  const now = Date.now();
  const newOdds = {};
  const newProps = {};
  const newSuspended = {};
  const prevOdds = state.odds || {};
  const prevSuspended = state.suspended || {};

  for (const m of ROSTER.matchups) {
    const rosterA = ROSTER.roster[m.teamA];
    const rosterB = ROSTER.roster[m.teamB];

    const rosterIdA = Object.keys(idMap).find((rid) => idMap[rid] === m.teamA);
    const rosterIdB = Object.keys(idMap).find((rid) => idMap[rid] === m.teamB);
    const livePointsA = rosterIdA ? liveMatchups[rosterIdA]?.playersPoints || {} : {};
    const livePointsB = rosterIdB ? liveMatchups[rosterIdB]?.playersPoints || {} : {};

    const statesA = rosterA.map((p) => buildPlayerState(p, livePointsA[p.sleeper_id], projections, gameClocks));
    const statesB = rosterB.map((p) => buildPlayerState(p, livePointsB[p.sleeper_id], projections, gameClocks));

    const result = pricing.priceMatchup(statesA, statesB);
    const prev = prevOdds[m.name];
    const swung = prev
      && Math.abs(result.winProbA - prev.winProbA) >= SUSPEND_SWING_THRESHOLD
      && Math.abs((result.impliedTotalA - result.impliedTotalB) - (prev.impliedTotalA - prev.impliedTotalB)) >= SUSPEND_MIN_MARGIN_DELTA;

    newOdds[m.name] = { teamA: m.teamA, teamB: m.teamB, ...result, updatedAt: now };

    if (swung) {
      newSuspended[m.name] = now;
    } else if (prevSuspended[m.name] && now - prevSuspended[m.name] < SUSPEND_DURATION_MS) {
      newSuspended[m.name] = prevSuspended[m.name];
    }

    // Player props for every skill-position starter (not DEF/K-style entries).
    rosterA.concat(rosterB).forEach((p, i) => {
      const st = (i < rosterA.length ? statesA[i] : statesB[i - rosterA.length]);
      if (p.position === "DEF") return;
      const prop = pricing.pricePlayerProp(st, 8.0);
      newProps[p.name] = { ...prop, position: p.position, team: rosterA.includes(p) ? m.teamA : m.teamB, updatedAt: now };

      const propKey = "prop:" + p.name;
      const prevProp = (state.props || {})[p.name];
      const propSwung = prevProp && Math.abs(prop.implied - prevProp.implied) >= PROP_SUSPEND_POINTS_DELTA;
      if (propSwung) {
        newSuspended[propKey] = now;
      } else if (prevSuspended[propKey] && now - prevSuspended[propKey] < SUSPEND_DURATION_MS) {
        newSuspended[propKey] = prevSuspended[propKey];
      }
    });
  }

  // Only report "live" when a real NFL game is actually in progress --
  // otherwise (offseason, weekday, pregame) every player's projection-only
  // implied total collapses to the same pick'em price, which is correct
  // math but not something that should ever be shown as a live line.
  const anyGameLive = Object.values(gameClocks).some((c) => c.state === "in");

  state = { odds: newOdds, props: newProps, suspended: newSuspended, mode: anyGameLive ? "live" : "pregame", lastUpdate: now };
}

function start() {
  pollOnce().catch((e) => console.error("initial poll failed:", e.message));
  setInterval(() => {
    pollOnce().catch((e) => console.error("poll failed:", e.message));
  }, POLL_INTERVAL_MS);
}

module.exports = { start, getState, pollOnce, SUSPEND_DURATION_MS };
