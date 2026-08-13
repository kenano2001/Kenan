"use strict";
const https = require("https");

const LEAGUE_ID = "1312211986242621440";

function getJson(url) {
  return new Promise((resolve, reject) => {
    https.get(url, { headers: { "User-Agent": "fantasize-live-server (contact: kenano2001@gmail.com)" } }, (res) => {
      if (res.statusCode !== 200) {
        res.resume();
        reject(new Error(`GET ${url} -> ${res.statusCode}`));
        return;
      }
      let body = "";
      res.on("data", (chunk) => { body += chunk; });
      res.on("end", () => {
        try { resolve(JSON.parse(body)); } catch (e) { reject(e); }
      });
    }).on("error", reject);
  });
}

/** roster_id -> { points, playersPoints: {sleeper_id: points} } for the given week. */
async function fetchLiveMatchups(week) {
  const data = await getJson(`https://api.sleeper.app/v1/league/${LEAGUE_ID}/matchups/${week}`);
  const out = {};
  for (const r of data) {
    out[r.roster_id] = {
      matchupId: r.matchup_id,
      points: r.points || 0,
      playersPoints: r.players_points || {},
    };
  }
  return out;
}

/** sleeper_id -> { pts: number|null, team: string|null } for the given week's pregame projections. */
async function fetchProjections(season, week) {
  const data = await getJson(`https://api.sleeper.app/projections/nfl/${season}/${week}?season_type=regular`);
  const out = {};
  for (const entry of data) {
    const pid = entry.player_id;
    if (!pid) continue;
    const stats = entry.stats || {};
    const player = entry.player || {};
    out[pid] = { pts: stats.pts_half_ppr ?? null, team: entry.team || player.team || null };
  }
  return out;
}

module.exports = { fetchLiveMatchups, fetchProjections, LEAGUE_ID };
