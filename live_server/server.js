"use strict";
const http = require("http");
const https = require("https");
const { execFile } = require("child_process");
const { URL } = require("url");
const engine = require("./engine.js");

// ESPN game-clock data (used to model each player's remaining upside)
// depends on `curl` being present in this environment -- see espn.js for
// why. It's not fatal if missing (the engine falls back to treating every
// game as not-yet-started), but that's a meaningfully worse model, so
// make it loud in the logs rather than silently degraded.
execFile("curl", ["--version"], (err) => {
  if (err) {
    console.warn("WARNING: `curl` not found on this host -- ESPN game-clock data will be unavailable, " +
      "and live odds won't account for how much of each player's game remains. " +
      "Install curl in the deployment image to fix.");
  }
});

const PORT = process.env.PORT || 8787;
const ALLOWED_ORIGIN = process.env.ALLOWED_ORIGIN || "https://kenano2001.github.io";
const APPS_SCRIPT_URL = process.env.APPS_SCRIPT_URL ||
  "https://script.google.com/macros/s/AKfycbx_nCEKgDRY7wYPMMYkQRFtbPfg5UGaNNyVJU3bKDW4u0V8p7naeO8tHQTXxEZC0OL8Wg/exec";

function setCors(res) {
  res.setHeader("Access-Control-Allow-Origin", ALLOWED_ORIGIN);
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
}

function sendJson(res, status, data) {
  setCors(res);
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(data));
}

function americanToDecimal(odds) {
  return odds > 0 ? 1 + odds / 100 : 1 + 100 / Math.abs(odds);
}

// Server-to-server call to the existing Apps Script deployment to persist
// an accepted bet into the same Google Sheet used for pregame bets -- no
// CORS concern here since this request never touches a browser.
function logBetToSheet(fields) {
  return new Promise((resolve, reject) => {
    const url = new URL(APPS_SCRIPT_URL);
    Object.entries(fields).forEach(([k, v]) => url.searchParams.set(k, v));
    https.get(url.toString(), { headers: { "User-Agent": "fantasize-live-server" } }, (res) => {
      res.resume();
      res.on("end", resolve);
    }).on("error", reject);
  });
}

async function handleBet(req, res, body) {
  let payload;
  try { payload = JSON.parse(body); } catch (e) { return sendJson(res, 400, { ok: false, message: "Bad request body." }); }

  const { matchupName, playerName, side, stake, name: bettorName } = payload;
  const state = engine.getState();
  const stakeNum = parseFloat(stake);
  if (!stakeNum || stakeNum <= 0) return sendJson(res, 400, { ok: false, reason: "bad_stake", message: "Enter a stake." });
  if (!bettorName) return sendJson(res, 400, { ok: false, reason: "no_name", message: "Not signed in." });

  if (matchupName) {
    // Team moneyline bet.
    const market = state.odds[matchupName];
    if (!market) return sendJson(res, 404, { ok: false, reason: "no_market", message: "That market is not live right now." });
    const suspendedAt = state.suspended[matchupName];
    if (suspendedAt && Date.now() - suspendedAt < engine.SUSPEND_DURATION_MS) {
      return sendJson(res, 409, { ok: false, reason: "suspended", message: "Betting is paused on this game for a moment while the odds catch up -- try again shortly." });
    }
    const teamName = side === "A" ? market.teamA : market.teamB;
    const odds = side === "A" ? market.moneylineA : market.moneylineB;
    const payout = Math.round(stakeNum * americanToDecimal(odds) * 100) / 100;
    const toWin = Math.round((payout - stakeNum) * 100) / 100;
    const legs = `LIVE: ${teamName} ML (${odds > 0 ? "+" : ""}${odds}) vs ${side === "A" ? market.teamB : market.teamA} -- ${market.impliedTotalA.toFixed(1)}-${market.impliedTotalB.toFixed(1)} at bet time`;
    await logBetToSheet({ name: bettorName, stake: stakeNum.toFixed(2), parlayOdds: (odds > 0 ? "+" : "") + odds, toWin: toWin.toFixed(2), payout: payout.toFixed(2), legs });
    return sendJson(res, 200, { ok: true, teamName, odds, stake: stakeNum, toWin, payout });
  }

  if (playerName) {
    // Player prop bet.
    const prop = state.props[playerName];
    if (!prop) return sendJson(res, 404, { ok: false, reason: "no_market", message: "That prop is not live right now." });
    const suspendedAt = state.suspended["prop:" + playerName];
    if (suspendedAt && Date.now() - suspendedAt < engine.SUSPEND_DURATION_MS) {
      return sendJson(res, 409, { ok: false, reason: "suspended", message: "Betting is paused on this prop for a moment while the line catches up -- try again shortly." });
    }
    const odds = side === "Over" ? prop.over : prop.under;
    const payout = Math.round(stakeNum * americanToDecimal(odds) * 100) / 100;
    const toWin = Math.round((payout - stakeNum) * 100) / 100;
    const legs = `LIVE PROP: ${playerName} ${side} ${prop.line} (${odds > 0 ? "+" : ""}${odds}) -- currently at ${prop.implied.toFixed(1)}`;
    await logBetToSheet({ name: bettorName, stake: stakeNum.toFixed(2), parlayOdds: (odds > 0 ? "+" : "") + odds, toWin: toWin.toFixed(2), payout: payout.toFixed(2), legs });
    return sendJson(res, 200, { ok: true, playerName, side, odds, stake: stakeNum, toWin, payout });
  }

  return sendJson(res, 400, { ok: false, message: "Missing matchupName or playerName." });
}

const server = http.createServer((req, res) => {
  if (req.method === "OPTIONS") { setCors(res); res.writeHead(204); res.end(); return; }

  if (req.method === "GET" && req.url.startsWith("/live")) {
    return sendJson(res, 200, engine.getState());
  }
  if (req.method === "GET" && req.url.startsWith("/health")) {
    return sendJson(res, 200, { ok: true, lastUpdate: engine.getState().lastUpdate });
  }
  if (req.method === "POST" && req.url.startsWith("/bet")) {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => { handleBet(req, res, body).catch((e) => sendJson(res, 500, { ok: false, message: e.message })); });
    return;
  }
  setCors(res);
  res.writeHead(404, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ error: "not found" }));
});

server.listen(PORT, () => {
  console.log(`Fantasize live server listening on :${PORT}`);
  engine.start();
});
