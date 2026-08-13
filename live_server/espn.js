"use strict";
const { execFile } = require("child_process");

// ESPN's scoreboard endpoint blocks Node's https/fetch clients at the
// TLS/HTTP fingerprint level (verified: a real 403 even with a full
// Chrome User-Agent, while curl against the identical URL succeeds
// reliably). Shelling out to curl sidesteps it rather than risking the
// same block on the deployed host. Sleeper's API has no such issue and
// uses native fetch (see sleeper.js).
function curlJson(url) {
  return new Promise((resolve, reject) => {
    execFile("curl", ["-sS", "--max-time", "10", url], { maxBuffer: 10 * 1024 * 1024 }, (err, stdout) => {
      if (err) { reject(err); return; }
      try { resolve(JSON.parse(stdout)); } catch (e) { reject(e); }
    });
  });
}

function parseClock(displayClock) {
  // "12:34" -> 12.5666... minutes
  const m = /^(\d+):(\d+)$/.exec((displayClock || "").trim());
  if (!m) return 0;
  return parseInt(m[1], 10) + parseInt(m[2], 10) / 60;
}

/**
 * team abbreviation -> { fractionRemaining: 0..1, completed: boolean, state: 'pre'|'in'|'post' }
 * fractionRemaining is this specific team's own game clock, not the
 * fantasy matchup's -- each player's "time left" comes from their real
 * NFL game, which finishes independently of everyone else's.
 */
async function fetchGameClocks(week, year) {
  const data = await curlJson(`https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?seasontype=2&week=${week}&year=${year}`);
  const out = {};
  for (const event of data.events || []) {
    for (const comp of event.competitions || []) {
      const status = comp.status || {};
      const state = ((status.type || {}).state) || "pre";
      const completed = !!(status.type || {}).completed;
      let fractionRemaining;
      if (completed || state === "post") {
        fractionRemaining = 0;
      } else if (state === "pre") {
        fractionRemaining = 1;
      } else {
        const period = status.period || 1;
        const minutesLeftInPeriod = parseClock(status.displayClock);
        const elapsed = Math.max(0, (period - 1) * 15 + (15 - minutesLeftInPeriod));
        fractionRemaining = Math.max(0, Math.min(1, 1 - elapsed / 60));
      }
      for (const competitor of comp.competitors || []) {
        const abbr = (competitor.team || {}).abbreviation;
        if (abbr) out[abbr] = { fractionRemaining, completed, state };
      }
    }
  }
  return out;
}

module.exports = { fetchGameClocks };
