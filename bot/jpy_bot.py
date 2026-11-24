"""
jpy_bot.py - DEMO ONLY. Places one USDJPY Fall (PUT) 4-hour contract at 19:00 UTC on weekdays.
  python jpy_bot.py test     -> connect to demo, show price/payout, buy nothing
  python jpy_bot.py trade    -> wait until 19:00 UTC, buy one demo contract, log it
  python jpy_bot.py settle   -> fill in results of past contracts and print a summary
Reads APP_ID and the token from ~/.deriv_env. Never prints the token.
There is no code path to a real account: it only accepts a demo websocket URL.
"""
import csv, json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

try:
    import websocket
except ImportError:
    sys.exit("Missing library. Run:  pip install websocket-client")

SYMBOL = "frxUSDJPY"
CONTRACT = "PUT"          # PUT = Fall (equals off)
DURATION, UNIT = 4, "h"
STAKE = 1.0
ENTRY_HOUR_UTC = 19
FILE = os.environ.get("TRADES_FILE", "bot_trades.csv")   # GitHub run sets this to docs/data/jpy_trades.csv
BASE = "https://api.derivws.com"
HEAD = ["date", "contract_id", "buy_price", "payout", "status", "profit"]


def read_env():
    app = tok = None
    names = []
    with open(os.path.expanduser("~/.deriv_env")) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.replace("export ", "").strip().upper()
            v = v.strip().strip("\"'")
            names.append(k)
            if "APP_ID" in k:
                app = v
            elif any(x in k for x in ("TOKEN", "PAT", "API_KEY")):
                tok = v
    if not app or not tok:
        sys.exit("Could not find APP_ID and a token in ~/.deriv_env. Names found: " + ", ".join(names))
    return app, tok


def rest(method, path, app, tok):
    req = urllib.request.Request(
        BASE + path, method=method, data=(b"{}" if method == "POST" else None),
        headers={"Authorization": "Bearer " + tok, "Deriv-App-ID": app,
                 "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} on {path}: {e.read().decode()[:300]}")


def connect():
    app, tok = read_env()
    accts = rest("GET", "/trading/v1/options/accounts", app, tok)["data"]
    demo = [a for a in accts if a["account_type"] == "demo" and a["status"] == "active"]
    if not demo:
        sys.exit("No active DEMO account found. Stopping.")
    a = demo[0]
    url = rest("POST", f"/trading/v1/options/accounts/{a['account_id']}/otp", app, tok)["data"]["url"]
    if "/ws/demo" not in url:
        sys.exit("Safety stop: not a demo websocket URL.")
    return websocket.create_connection(url, timeout=30), a


def call(ws, msg):
    rid = int(time.time() * 1000) % 1000000000
    msg["req_id"] = rid
    ws.send(json.dumps(msg))
    while True:
        r = json.loads(ws.recv())
        if r.get("req_id") == rid:
            return r


def proposal(ws, acct):
    p = call(ws, {"proposal": 1, "amount": STAKE, "basis": "stake", "contract_type": CONTRACT,
                  "currency": acct["currency"], "duration": DURATION, "duration_unit": UNIT,
                  "underlying_symbol": SYMBOL})
    if "error" in p:
        sys.exit("Proposal error: " + json.dumps(p["error"])[:300])
    return p["proposal"]


def load():
    if not os.path.exists(FILE):
        return []
    with open(FILE) as f:
        return [r for r in csv.reader(f) if r and r[0] != "date"]


def save(rows):
    os.makedirs(os.path.dirname(os.path.abspath(FILE)), exist_ok=True)
    with open(FILE, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEAD)
        w.writerows(rows)


def do_test():
    ws, a = connect()
    prop = proposal(ws, a)
    print(f"Connected to DEMO account {a['account_id']}, balance {a['balance']} {a['currency']}")
    print(f"{SYMBOL} {CONTRACT} {DURATION}{UNIT}: stake {STAKE}, ask {prop['ask_price']}, payout {prop['payout']}")
    print("Nothing was bought. Wiring works.")
    ws.close()


def do_trade():
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        sys.exit("Weekend - no trade.")
    target = now.replace(hour=ENTRY_HOUR_UTC, minute=0, second=2, microsecond=0)
    if now > target.replace(minute=5):
        sys.exit("Past 19:05 UTC today - too late, skipping.")
    today = str(now.date())
    if any(r[0] == today for r in load()):
        sys.exit("Already traded today.")
    wait = (target - datetime.now(timezone.utc)).total_seconds() - 30
    if wait > 0:
        print(f"Waiting {wait/60:.1f} min until 18:59:32 UTC...")
        time.sleep(wait)
    ws, a = connect()
    wait = (target - datetime.now(timezone.utc)).total_seconds()
    if wait > 0:
        time.sleep(wait)
    prop = proposal(ws, a)
    if prop["ask_price"] > STAKE * 1.02:
        sys.exit(f"Price moved to {prop['ask_price']}, skipping.")
    b = call(ws, {"buy": prop["id"], "price": prop["ask_price"]})
    if "error" in b:
        sys.exit("Buy error: " + json.dumps(b["error"])[:300])
    buy = b["buy"]
    rows = load()
    rows.append([today, buy["contract_id"], buy["buy_price"], buy["payout"], "open", ""])
    save(rows)
    print(f"Bought DEMO contract {buy['contract_id']} for {buy['buy_price']}, payout {buy['payout']}")
    ws.close()


def do_settle():
    rows = load()
    todo = [r for r in rows if r[4] == "open"]
    if todo:
        ws, a = connect()
        for r in todo:
            res = call(ws, {"proposal_open_contract": 1, "contract_id": int(r[1])})
            c = res.get("proposal_open_contract")
            if not c:
                print("No result yet for", r[1]); continue
            if c.get("is_sold") == 1 or c.get("status") in ("won", "lost", "sold"):
                r[4] = c.get("status") or "sold"
                r[5] = c.get("profit", "")
        ws.close()
        save(rows)
    done = [r for r in rows if r[4] in ("won", "lost", "sold")]
    n = len(done)
    w = sum(1 for r in done if r[4] == "won")
    pnl = sum(float(r[5]) for r in done if r[5] != "")
    print(f"closed trades: {n}, wins: {w}" + (f" ({100*w/n:.0f}%)" if n else "") + f", net profit: {pnl:+.2f}")
    print(f"still open: {sum(1 for r in rows if r[4] == 'open')}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"test": do_test, "trade": do_trade, "settle": do_settle}.get(cmd, lambda: print(__doc__))()
