"""
Builds docs/demo.html -- a self-contained, client-side-only replay of a
real historical NFL week (2025 Week 3 final box scores for this league's
actual rostered players), compressed into a scripted 1-hour playback.

Zero dependency on the Apps Script deployment -- this exists purely to
demo the live-odds/suspension mechanism safely, without any risk to the
real production system. Uses the same win-probability math (ported to JS)
as both odds_model.py and the real Code.gs live engine.

Run:
    python3 generate_demo.py
"""

from __future__ import annotations
import hashlib
import json
from pathlib import Path

import odds_model

ROOT = Path(__file__).parent
NUM_CHECKPOINTS = 16
CHECKPOINT_INTERVAL_MS = 225000  # 3.75 min real time * 16 = 60 min total


def split_into_events(name: str, total: float, n: int = NUM_CHECKPOINTS) -> list[float]:
    if total == 0:
        return [0.0] * n
    seed = int(hashlib.md5(name.encode()).hexdigest(), 16)
    state = seed

    def rnd() -> float:
        nonlocal state
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF

    num_events = 2 if abs(total) < 8 else (3 if abs(total) < 18 else 4)
    candidates = list(range(1, n))
    chosen = []
    for _ in range(num_events):
        idx = int(rnd() * len(candidates))
        chosen.append(candidates.pop(idx % len(candidates)))
    chosen.sort()

    weights = [0.15 + rnd() * 0.6 for _ in chosen]
    wsum = sum(weights)
    deltas = [0.0] * n
    for cp, w in zip(chosen, weights):
        deltas[cp] = round(total * (w / wsum), 2)
    drift = round(total - sum(deltas), 2)
    if chosen:
        deltas[chosen[-1]] = round(deltas[chosen[-1]] + drift, 2)
    return deltas


def build_demo_data() -> dict:
    hist = json.loads((ROOT / "demo_hist_week3_2025.json").read_text())
    teams = odds_model.week1_teams()

    schedule = {}
    for _, team_a, team_b in teams:
        for team in (team_a, team_b):
            for p in team.starters:
                total = hist.get(p.name, 0.0)
                schedule[p.name] = {"team": team.team_name, "deltas": split_into_events(p.name, total)}

    # Featured storyline: Gibbs' real 24.4-point day lands as one dramatic
    # jump at checkpoint 6, guaranteeing a clean swing/suspension moment on
    # the closest matchup of the week (kyleullrich8 vs leagueisass, decided
    # by 1.8 points in real life).
    gibbs_total = hist.get("Gibbs", 0.0)
    gibbs_deltas = [0.0] * NUM_CHECKPOINTS
    gibbs_deltas[2] = round(gibbs_total * 0.15, 2)
    gibbs_deltas[6] = round(gibbs_total * 0.65, 2)
    gibbs_deltas[11] = round(gibbs_total - gibbs_deltas[2] - gibbs_deltas[6], 2)
    schedule["Gibbs"]["deltas"] = gibbs_deltas

    matchups = [
        {
            "name": name,
            "teamA": team_a.team_name,
            "teamB": team_b.team_name,
            "playersA": [p.name for p in team_a.starters],
            "playersB": [p.name for p in team_b.starters],
        }
        for name, team_a, team_b in teams
    ]

    return {
        "numCheckpoints": NUM_CHECKPOINTS,
        "checkpointIntervalMs": CHECKPOINT_INTERVAL_MS,
        "players": schedule,
        "matchups": matchups,
    }


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Fantasize -- Live Demo</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="theme-color" content="#8b3ff0" />
<style>
  :root {
    --bg: #f7f4fb; --surface: #ffffff; --surface-2: #f4eefc; --border: #e4d9f7;
    --text: #1c1428; --text-dim: #4a3f5c; --text-faint: #8a7f9c;
    --accent: #8b3ff0; --on-accent: #ffffff;
    --favorite: #1f9d55; --underdog: #d92b2b; --warn: #b5750a; --warn-soft: #fdf0da;
    --danger: #d92b2b; --shadow: 0 8px 30px rgba(30,10,60,0.10);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #120c1a; --surface: #1c1428; --surface-2: #251b34; --border: #382a4d;
      --text: #f3edfb; --text-dim: #c3b4dd; --text-faint: #8d7fa8;
      --favorite: #4ee08a; --underdog: #ff5b6a; --warn: #f0b545; --warn-soft: #3a2c14;
      --danger: #ff5b6a; --shadow: 0 8px 30px rgba(0,0,0,0.4);
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, sans-serif; }
  .wrap { max-width: 900px; margin: 0 auto; padding: 20px 16px 60px; }
  header { text-align: center; margin-bottom: 20px; }
  header h1 { font-size: 22px; margin: 0 0 4px; }
  header p { font-size: 13px; color: var(--text-faint); margin: 0; }
  .clock-bar {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    padding: 14px 18px; margin-bottom: 18px; box-shadow: var(--shadow);
  }
  .clock { font-family: 'SF Mono', Menlo, monospace; font-size: 20px; font-weight: 700; }
  .clock-label { font-size: 11px; color: var(--text-faint); text-transform: uppercase; letter-spacing: 0.05em; }
  #start-btn {
    appearance: none; border: none; background: var(--accent); color: var(--on-accent);
    font-weight: 700; font-size: 14px; padding: 10px 18px; border-radius: 8px; cursor: pointer;
  }
  #start-btn:disabled { opacity: 0.5; cursor: default; }
  #reset-btn { appearance: none; border: 1px solid var(--border); background: none; color: var(--text-dim); font-size: 12px; padding: 8px 12px; border-radius: 8px; cursor: pointer; }

  .card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    padding: 14px 16px; margin-bottom: 12px; box-shadow: var(--shadow);
  }
  .card-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
  .card-head .title { font-weight: 700; font-size: 14px; }
  .live-badge {
    display: none; align-items: center; gap: 6px; font-size: 10.5px; font-weight: 800;
    letter-spacing: 0.04em; text-transform: uppercase; color: var(--danger);
  }
  .live-badge.show { display: inline-flex; }
  .live-badge.suspended { color: var(--warn); }
  .live-badge .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--danger); animation: pulse 1.4s ease-in-out infinite; }
  .live-badge.suspended .dot { background: var(--warn); animation: none; }
  @keyframes pulse { 0%,100% {opacity:1;} 50% {opacity:0.3;} }

  .team-row { display: flex; align-items: center; justify-content: space-between; padding: 6px 0; }
  .team-row .name { font-size: 13.5px; }
  .team-row .score { font-family: monospace; font-size: 13.5px; color: var(--text-dim); margin-right: 10px; }
  .ml-pill {
    appearance: none; border: 1px solid var(--border); background: var(--surface-2);
    font-family: monospace; font-weight: 700; font-size: 13px; padding: 6px 12px; border-radius: 8px;
    cursor: pointer; min-width: 64px; text-align: center;
  }
  .ml-pill.fav { color: var(--favorite); }
  .ml-pill.dog { color: var(--underdog); }
  .ml-pill.suspended { opacity: 0.5; cursor: default; }
  .ml-pill-row { display: flex; align-items: center; gap: 10px; }

  #log-panel, #bets-panel {
    background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    padding: 14px 16px; margin-top: 20px; box-shadow: var(--shadow);
  }
  #log-panel h2, #bets-panel h2 { font-size: 13px; margin: 0 0 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-faint); }
  #log-list, #bets-list { list-style: none; margin: 0; padding: 0; max-height: 320px; overflow-y: auto; }
  #log-list li, #bets-list li { font-size: 13px; padding: 6px 0; border-bottom: 1px solid var(--border); line-height: 1.5; }
  #log-list li:last-child, #bets-list li:last-child { border-bottom: none; }
  #log-list li .t { color: var(--text-faint); font-family: monospace; font-size: 11px; margin-right: 6px; }
  #log-list li.swing { color: var(--warn); font-weight: 700; }

  .overlay {
    display: none; position: fixed; inset: 0; z-index: 80; background: rgba(18,12,26,0.55);
    align-items: center; justify-content: center; padding: 16px;
  }
  .overlay.show { display: flex; }
  .modal { width: 100%; max-width: 340px; background: var(--surface); border: 1px solid var(--border); border-radius: 16px; box-shadow: var(--shadow); padding: 20px; }
  .modal h3 { margin: 0 0 10px; font-size: 17px; }
  .modal .sub { font-size: 12.5px; color: var(--text-faint); margin: 0 0 12px; }
  .modal-row { display: flex; align-items: center; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid var(--border); }
  .modal-row .odds { font-family: monospace; font-weight: 700; }
  .modal input { width: 100%; box-sizing: border-box; border: 1px solid var(--border); background: var(--surface-2); border-radius: 8px; padding: 9px 11px; font-size: 14px; color: var(--text); margin: 12px 0; }
  .modal .confirm { display: block; width: 100%; appearance: none; border: none; background: var(--accent); color: var(--on-accent); font-weight: 700; font-size: 14px; padding: 11px; border-radius: 10px; cursor: pointer; margin-bottom: 8px; }
  .modal .cancel { display: block; width: 100%; background: none; border: none; color: var(--text-faint); font-size: 12px; font-weight: 700; padding: 8px; cursor: pointer; }
  .modal .err { display: none; font-size: 12px; color: var(--underdog); background: var(--warn-soft); border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; }
  .modal .err.show { display: block; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Fantasize -- Live Demo</h1>
    <p>Replaying real 2025 Week 3 results, compressed into 1 hour. Refresh any time -- it picks up exactly where it left off.</p>
  </header>

  <div class="clock-bar">
    <div>
      <div class="clock-label">Compressed game clock</div>
      <div class="clock" id="clock">Not started</div>
    </div>
    <div style="display:flex; gap:8px;">
      <button id="reset-btn">Reset</button>
      <button id="start-btn">Start Demo</button>
    </div>
  </div>

  <div id="cards"></div>

  <div id="log-panel">
    <h2>Play-by-play</h2>
    <ul id="log-list"></ul>
  </div>

  <div id="bets-panel" style="display:none;">
    <h2>Your demo bets</h2>
    <ul id="bets-list"></ul>
  </div>
</div>

<div class="overlay" id="bet-overlay">
  <div class="modal">
    <h3>Live Bet</h3>
    <p class="sub" id="bet-matchup">--</p>
    <div class="err" id="bet-err"></div>
    <div class="modal-row"><span id="bet-team">--</span><span class="odds" id="bet-odds">--</span></div>
    <input id="bet-stake" type="number" min="1" step="1" value="10" />
    <button class="confirm" id="bet-confirm">Place Live Bet</button>
    <button class="cancel" id="bet-cancel">Cancel</button>
  </div>
</div>

<script>
const DEMO = __DEMO_DATA_JSON__;
</script>
<script>
(function () {
  "use strict";
  const START_KEY = "fantasize_demo_start_ts";
  const BETS_KEY = "fantasize_demo_bets";
  const SUSPEND_SWING_THRESHOLD = 0.08;
  const SUSPEND_DURATION_MS = 90 * 1000;
  const TEAM_SD = 29.0;

  function erf(x) {
    const sign = x < 0 ? -1 : 1; x = Math.abs(x);
    const a1=0.254829592,a2=-0.284496736,a3=1.421413741,a4=-1.453152027,a5=1.061405429,p=0.3275911;
    const t = 1/(1+p*x);
    const y = 1 - (((((a5*t+a4)*t)+a3)*t+a2)*t+a1)*t*Math.exp(-x*x);
    return sign*y;
  }
  function winProb(margin, sd) { return 0.5 * (1 + erf((margin/sd) / Math.sqrt(2))); }
  function probToAmerican(p) {
    // Clamped tighter than the pregame model (which rarely sees margins
    // this decisive) so a real blowout reads as "extremely lopsided" (e.g.
    // -19900) rather than a broken-looking number like -99900.
    p = Math.max(0.005, Math.min(0.995, p));
    return p >= 0.5 ? Math.round(-100*p/(1-p)) : Math.round(100*(1-p)/p);
  }
  function americanToDecimal(o) { return o > 0 ? 1 + o/100 : 1 + 100/Math.abs(o); }
  function priceTwoWay(pA, margin) { return [probToAmerican(pA*margin), probToAmerican((1-pA)*margin)]; }
  function fmtOdds(o) { return (o > 0 ? "+" : "") + o; }

  function getStart() { const v = localStorage.getItem(START_KEY); return v ? parseInt(v, 10) : null; }
  function setStart(ts) { localStorage.setItem(START_KEY, String(ts)); }

  function elapsedInfo() {
    const start = getStart();
    if (!start) return null;
    const elapsed = Date.now() - start;
    const cpFloat = Math.min(DEMO.numCheckpoints - 1, elapsed / DEMO.checkpointIntervalMs);
    return { elapsed, checkpoint: Math.floor(cpFloat), done: elapsed >= DEMO.checkpointIntervalMs * (DEMO.numCheckpoints - 1) };
  }

  function cumulativeAt(playerName, checkpoint) {
    const deltas = DEMO.players[playerName].deltas;
    let sum = 0;
    for (let i = 0; i <= checkpoint; i++) sum += deltas[i];
    return Math.round(sum * 10) / 10;
  }

  function teamTotals(matchup, checkpoint) {
    const a = matchup.playersA.reduce((s, n) => s + cumulativeAt(n, checkpoint), 0);
    const b = matchup.playersB.reduce((s, n) => s + cumulativeAt(n, checkpoint), 0);
    return [Math.round(a*10)/10, Math.round(b*10)/10];
  }

  function liveSdFor(checkpoint) {
    // Floor raised (vs the real Code.gs engine's 0.05) purely for demo
    // display purposes -- keeps compressed blowouts from producing
    // vertigo-inducing odds like -99900 on-screen.
    const fractionRemaining = Math.max(0.12, 1 - checkpoint / (DEMO.numCheckpoints - 1));
    return TEAM_SD * Math.sqrt(fractionRemaining);
  }

  // Everything below is a pure function of (start_ts, wall-clock now) --
  // no mutable state is kept between renders, so a hard refresh at any
  // point produces exactly the same result as having watched it tick
  // there live. That matters because suspension state has to survive
  // "I will have the platform open and refresh" (the actual real usage
  // pattern), not just a continuously-open tab.

  function winProbAt(matchup, checkpoint) {
    if (checkpoint < 0) return 0.5;
    const [ptsA, ptsB] = teamTotals(matchup, checkpoint);
    return winProb(ptsA - ptsB, liveSdFor(checkpoint));
  }

  function checkpointStartTime(checkpoint) {
    return getStart() + checkpoint * DEMO.checkpointIntervalMs;
  }

  function suspensionInfo(matchup, checkpoint, now) {
    if (checkpoint <= 0) return { suspended: false, justSwung: false };
    const prevP = winProbAt(matchup, checkpoint - 1);
    const curP = winProbAt(matchup, checkpoint);
    const swungThisCheckpoint = Math.abs(curP - prevP) >= SUSPEND_SWING_THRESHOLD;
    if (!swungThisCheckpoint) return { suspended: false, justSwung: false };
    const withinWindow = now - checkpointStartTime(checkpoint) < SUSPEND_DURATION_MS;
    return { suspended: withinWindow, justSwung: true };
  }

  function scorerAt(matchup, checkpoint) {
    const all = [...matchup.playersA, ...matchup.playersB];
    return all.filter((n) => (DEMO.players[n].deltas[checkpoint] || 0) > 0.4)
      .map((n) => ({ name: n, team: DEMO.players[n].team, pts: DEMO.players[n].deltas[checkpoint] }));
  }

  // Rebuilt from scratch every render (cheap -- 16 checkpoints x 5
  // matchups) so the log is always the full, correct history up through
  // the current checkpoint, regardless of when the page was opened.
  function renderLog(uptoCheckpoint) {
    const entries = [];
    for (let cp = 1; cp <= uptoCheckpoint; cp++) {
      const mins = (cp * DEMO.checkpointIntervalMs / 60000).toFixed(1);
      DEMO.matchups.forEach((m) => {
        scorerAt(m, cp).forEach((s) => {
          entries.push({ mins, isSwing: false, text: s.name + " (" + s.team + ") scores -- +" + s.pts.toFixed(1) + " pts" });
        });
        const { justSwung } = suspensionInfo(m, cp, Infinity);
        if (justSwung) {
          entries.push({ mins, isSwing: true, text: "⚠️ Big swing on " + m.name + " -- betting paused while the line resets" });
        }
      });
    }
    entries.reverse();
    const list = document.getElementById("log-list");
    list.innerHTML = "";
    entries.slice(0, 80).forEach((e) => {
      const li = document.createElement("li");
      if (e.isSwing) li.className = "swing";
      li.innerHTML = '<span class="t">' + e.mins + 'm</span>' + e.text;
      list.appendChild(li);
    });
  }

  let currentModal = null;

  function renderCards() {
    const info = elapsedInfo();
    const root = document.getElementById("cards");
    root.innerHTML = "";

    DEMO.matchups.forEach((m) => {
      const card = document.createElement("div");
      card.className = "card";

      if (!info) {
        card.innerHTML = '<div class="card-head"><span class="title">' + m.name + '</span></div>' +
          '<div class="team-row"><span class="name">' + m.teamA + '</span><span class="score">proj</span></div>' +
          '<div class="team-row"><span class="name">' + m.teamB + '</span><span class="score">proj</span></div>';
        root.appendChild(card);
        return;
      }

      const cp = info.checkpoint;
      const [ptsA, ptsB] = teamTotals(m, cp);
      const margin = ptsA - ptsB;
      const sd = liveSdFor(cp);
      const pA = winProb(margin, sd);
      const [mlA, mlB] = priceTwoWay(pA, 1.08);

      const { suspended: isSuspended } = suspensionInfo(m, cp, Date.now());

      card.innerHTML =
        '<div class="card-head"><span class="title">' + m.name + '</span>' +
        '<span class="live-badge show' + (isSuspended ? ' suspended' : '') + '">' +
        '<span class="dot"></span><span>' + (isSuspended ? "Paused" : "Live") + '</span></span></div>' +
        '<div class="team-row"><span class="name">' + m.teamA + '</span>' +
        '<span class="ml-pill-row"><span class="score">' + ptsA.toFixed(1) + '</span>' +
        '<button class="ml-pill ' + (mlA < 0 ? "fav" : "dog") + (isSuspended ? " suspended" : "") + '" data-team="A" data-matchup="' + m.name + '">' + fmtOdds(mlA) + '</button></span></div>' +
        '<div class="team-row"><span class="name">' + m.teamB + '</span>' +
        '<span class="ml-pill-row"><span class="score">' + ptsB.toFixed(1) + '</span>' +
        '<button class="ml-pill ' + (mlB < 0 ? "fav" : "dog") + (isSuspended ? " suspended" : "") + '" data-team="B" data-matchup="' + m.name + '">' + fmtOdds(mlB) + '</button></span></div>';

      root.appendChild(card);
    });

    root.querySelectorAll(".ml-pill").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (btn.classList.contains("suspended")) {
          alert("Betting is paused on this game for a moment while the odds catch up -- try again shortly.");
          return;
        }
        openBetModal(btn.getAttribute("data-matchup"), btn.getAttribute("data-team"), btn.textContent);
      });
    });
  }

  function openBetModal(matchupName, side, oddsText) {
    const m = DEMO.matchups.find((x) => x.name === matchupName);
    const teamName = side === "A" ? m.teamA : m.teamB;
    currentModal = { matchupName, side, odds: parseInt(oddsText, 10) };
    document.getElementById("bet-matchup").textContent = matchupName;
    document.getElementById("bet-team").textContent = teamName;
    document.getElementById("bet-odds").textContent = oddsText;
    document.getElementById("bet-err").classList.remove("show");
    document.getElementById("bet-stake").value = "10";
    document.getElementById("bet-overlay").classList.add("show");
  }
  function closeBetModal() { document.getElementById("bet-overlay").classList.remove("show"); currentModal = null; }
  document.getElementById("bet-cancel").addEventListener("click", closeBetModal);

  function loadDemoBets() { try { return JSON.parse(localStorage.getItem(BETS_KEY) || "[]"); } catch (e) { return []; } }
  function saveDemoBets(bets) { localStorage.setItem(BETS_KEY, JSON.stringify(bets)); }
  function renderDemoBets() {
    const bets = loadDemoBets();
    const panel = document.getElementById("bets-panel");
    const list = document.getElementById("bets-list");
    if (!bets.length) { panel.style.display = "none"; return; }
    panel.style.display = "block";
    list.innerHTML = "";
    bets.slice().reverse().forEach((b) => {
      const li = document.createElement("li");
      li.textContent = "$" + b.stake.toFixed(2) + " on " + b.team + " (" + fmtOdds(b.odds) + ") -- to win $" + b.toWin.toFixed(2);
      list.appendChild(li);
    });
  }

  document.getElementById("bet-confirm").addEventListener("click", () => {
    if (!currentModal) return;
    const info = elapsedInfo();
    const m = DEMO.matchups.find((x) => x.name === currentModal.matchupName);
    const { suspended: isSuspended } = suspensionInfo(m, info.checkpoint, Date.now());
    if (isSuspended) {
      const err = document.getElementById("bet-err");
      err.textContent = "Betting is paused on this game for a moment while the odds catch up -- try again shortly.";
      err.classList.add("show");
      return;
    }
    const stake = parseFloat(document.getElementById("bet-stake").value) || 0;
    if (stake <= 0) return;
    const payout = Math.round(stake * americanToDecimal(currentModal.odds) * 100) / 100;
    const bets = loadDemoBets();
    bets.push({ team: document.getElementById("bet-team").textContent, odds: currentModal.odds, stake, toWin: payout - stake });
    saveDemoBets(bets);
    renderDemoBets();
    closeBetModal();
  });

  function tick() {
    const info = elapsedInfo();
    const clockEl = document.getElementById("clock");
    if (!info) {
      clockEl.textContent = "Not started";
      document.getElementById("start-btn").disabled = false;
      document.getElementById("log-list").innerHTML = "";
    } else {
      const mins = Math.floor(info.elapsed / 60000);
      const secs = Math.floor((info.elapsed % 60000) / 1000);
      clockEl.textContent = (info.done ? "FINAL -- " : "") + mins + ":" + String(secs).padStart(2, "0") + " elapsed";
      document.getElementById("start-btn").disabled = true;
      renderCards();
      renderLog(info.checkpoint);
    }
    renderDemoBets();
  }

  document.getElementById("start-btn").addEventListener("click", () => {
    setStart(Date.now());
    saveDemoBets([]);
    tick();
  });
  document.getElementById("reset-btn").addEventListener("click", () => {
    localStorage.removeItem(START_KEY);
    saveDemoBets([]);
    tick();
  });

  tick();
  setInterval(tick, 5000);
})();
</script>
</body>
</html>
"""


def main() -> None:
    data = build_demo_data()
    html = TEMPLATE.replace("__DEMO_DATA_JSON__", json.dumps(data))
    out = ROOT / "docs" / "demo.html"
    out.write_text(html)
    print(f"Wrote {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
