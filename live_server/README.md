# Fantasize live-odds server

Always-on Node server that continuously polls Sleeper (live scores +
projections) and ESPN (game clocks), prices live team moneylines and
player props, and serves them to the app -- replacing the earlier
Apps-Script-based live engine, which was capped at once-a-minute updates
by Google's trigger limits. This server polls every 20 seconds instead.

No external dependencies -- just Node's built-in `http`/`https` modules,
plus shelling out to `curl` for ESPN specifically (see `espn.js` for why).

## Deploying (Render.com, free tier)

1. Go to [render.com](https://render.com) and sign up / log in (can use your GitHub account)
2. **New +** -> **Web Service**
3. Connect the `kenano2001/Kenan` repository
4. Settings:
   - **Root Directory**: `live_server`
   - **Runtime**: Node
   - **Build Command**: `npm install`
   - **Start Command**: `npm start`
   - **Instance Type**: Free
5. Click **Create Web Service**

Render will give you a URL like `https://fantasize-live-xxxx.onrender.com`
-- send that to me once it's up so I can point the app at it.

Note: on Render's free tier, the service spins down after ~15 minutes with
no incoming requests and takes 30-60 seconds to wake back up on the next
request. In practice this is fine for this use case -- it'll be actively
polled while anyone has the app open during a game, and there's no reason
for it to run between game windows anyway.

## Environment variables (optional overrides)

- `ALLOWED_ORIGIN` -- defaults to `https://kenano2001.github.io`
- `APPS_SCRIPT_URL` -- defaults to the current deployed Apps Script `/exec` URL (used to persist accepted live bets into the same Google Sheet)
- `PORT` -- set automatically by Render, no need to configure

## Local testing

    cd live_server
    npm install
    npm start

Then `curl http://localhost:8787/live` to see current pricing, or
`curl http://localhost:8787/health`.

## Regenerating roster.json

If the rosters change, regenerate from the Python source of truth:

    cd ..
    python3 -c "
    import json
    import odds_model
    teams = odds_model.week1_teams()
    roster = {}
    matchups = []
    for name, ta, tb in teams:
        roster[ta.team_name] = [{'name': p.name, 'sleeper_id': p.sleeper_id, 'position': p.position} for p in ta.starters]
        roster[tb.team_name] = [{'name': p.name, 'sleeper_id': p.sleeper_id, 'position': p.position} for p in tb.starters]
        matchups.append({'name': name, 'teamA': ta.team_name, 'teamB': tb.team_name})
    json.dump({'roster': roster, 'matchups': matchups}, open('live_server/roster.json', 'w'), indent=2)
    "

## Each new week

Update `CURRENT_WEEK` at the top of `engine.js` and redeploy (Render
auto-deploys on push to the repo).
