# Nightly Bot (paper trading)

A bot that runs by itself every night (22:00 Nairobi) and every morning (06:00 Nairobi),
paper-trades 3 strategies on BTC and ETH, records the results and shows them in a phone app.
It uses fake money only and needs no API keys.

## Set up (about 10 minutes, from your laptop)

1. On GitHub, create a **new repository** called `nightly-bot` (public, so GitHub Pages is free).
2. Open the repo, press **Add file > Upload files** and drag in everything from this folder
   (the `bot`, `docs` and `.github` folders). If the hidden `.github` folder will not upload,
   use **Add file > Create new file**, type `.github/workflows/nightly.yml` as the name and paste the contents.
3. **Settings > Pages**: Source = "Deploy from a branch", Branch = `main`, Folder = `/docs`, then Save.
4. **Settings > Actions > General > Workflow permissions**: choose "Read and write permissions", then Save.
5. **Actions tab > nightly-bot > Run workflow** to start the first run right now.
6. Open `https://YOUR-USERNAME.github.io/nightly-bot/` on your phone. Install it with
   Chrome menu (three dots) > **Add to Home screen / Install app**.

## How it works

- `bot/run.py` downloads the last 500 hourly candles, paper-trades only the new candles
  since the previous run, and writes `docs/data/report.json`.
- `.github/workflows/nightly.yml` runs it on a schedule and saves the results back to the repo.
- `docs/index.html` is the app. It reads `report.json` and shows equity, run history,
  strategies, trades and a reference backtest.

## Change things

Edit the top of `bot/run.py`: `SYMBOLS`, `START_BALANCE`, `RISK_PER_TRADE`, `FEE`.
Add a strategy by writing one function like `s_rsi_bb` and adding it to `STRATEGIES`.
To change run times, edit the two `cron` lines (they are in UTC, Nairobi is UTC+3).

## Good to know

- The first run only starts the accounts, so you will see 0 trades. Trades appear over the next runs.
- If GitHub shows no schedule runs after a long quiet period, open the Actions tab and re-enable the workflow.
- Test on your own machine with fake data: `python bot/run.py --offline`

## USDJPY demo forward test (jpy_bot.py)

`bot/jpy_bot.py` is your Termux script, moved here so it runs by itself on weekdays.
It trades a **demo** account only. At 22:00 Nairobi (19:00 UTC) it buys one USDJPY Fall contract
for 4 hours, and each morning at 06:30 Nairobi it fills in the results.
Results are saved to `docs/data/jpy_trades.csv` and shown in the app's Bot tab.

Setup (once):
1. Repo **Settings > Secrets and variables > Actions > New repository secret**.
   Add `DERIV_APP_ID` (your app ID) and `DERIV_TOKEN` (your token). Never put them in a file in the repo.
2. **Actions > usdjpy-demo > Run workflow**, choose `test`. It connects and buys nothing.
3. Stop running `jpy_bot.py trade` in Termux, or you will place two trades a day.

Good to know:
- GitHub can start scheduled jobs late, so the trade job starts at 21:20 Nairobi and waits until 22:00.
  A second attempt starts at 21:40. If both are more than 5 minutes late, that day is skipped.
- Weekend, too-late and already-traded days are normal skips, not errors.
- If you ever change the token, update the secret.
