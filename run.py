#!/usr/bin/env python3
"""Nightly/morning PAPER-trading bot. No real money, no API keys, standard library only.

Each run:
  1. downloads recent hourly candles (Binance public market data)
  2. runs every strategy on every symbol, paper-trading only NEW closed candles
  3. writes docs/data/report.json (+ latest.md) which the phone app reads

Usage:
  python bot/run.py                 # normal run (label chosen from Nairobi time)
  python bot/run.py --label night   # force label
  python bot/run.py --offline       # fake random-walk data, for testing
  python bot/run.py --offline --shift 24   # pretend 24 more hours passed (testing)
"""
import json, os, sys, math, random, argparse, urllib.request
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "data")
STATE_F = os.path.join(DATA, "state.json")
REPORT_F = os.path.join(DATA, "report.json")
LATEST_F = os.path.join(DATA, "latest.md")

# ---------------- settings you can edit ----------------
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
INTERVAL = "1h"
CANDLES = 500            # history downloaded each run (max 1000)
START_BALANCE = 1000.0   # paper dollars per strategy+symbol account
RISK_PER_TRADE = 0.01    # risk 1% of the account per trade
FEE = 0.001              # 0.1% per side (fees + slippage guess)
MAX_HOLD_BARS = 48       # close a trade after 48 hours if SL/TP not hit
NAIROBI = timezone(timedelta(hours=3))
HOUR_MS = 3600 * 1000
# --------------------------------------------------------


# ---------------- data ----------------
def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "nightly-bot/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def fetch_candles(symbol):
    last_err = None
    for host in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            raw = http_json(f"{host}/api/v3/klines?symbol={symbol}&interval={INTERVAL}&limit={CANDLES}")
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            out = []
            for k in raw:
                if int(k[6]) < now_ms:  # keep closed candles only
                    out.append({"ts": int(k[0]), "o": float(k[1]), "h": float(k[2]),
                                "l": float(k[3]), "c": float(k[4])})
            return out
        except Exception as e:  # try next host
            last_err = e
    raise RuntimeError(f"could not download {symbol}: {last_err}")


def fake_candles(symbol, shift):
    rnd = random.Random(sum(ord(ch) for ch in symbol))
    base = (1760000000000 // HOUR_MS) * HOUR_MS
    price = 100.0 * (1 + (sum(ord(ch) for ch in symbol) % 7))
    out = []
    for i in range(CANDLES + shift):
        drift = math.sin(i / 40.0) * 0.0015
        o = price
        c = o * (1 + drift + rnd.gauss(0, 0.006))
        h = max(o, c) * (1 + abs(rnd.gauss(0, 0.002)))
        l = min(o, c) * (1 - abs(rnd.gauss(0, 0.002)))
        out.append({"ts": base + i * HOUR_MS, "o": o, "h": h, "l": l, "c": c})
        price = c
    return out


# ---------------- indicators ----------------
def ema(v, n):
    k, e, out = 2 / (n + 1), None, []
    for i, x in enumerate(v):
        e = x if e is None else x * k + e * (1 - k)
        out.append(e if i >= n - 1 else None)
    return out


def rsi(c, n=14):
    out = [None] * len(c)
    if len(c) <= n:
        return out
    gain = loss = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        gain += max(d, 0)
        loss += max(-d, 0)
    gain, loss = gain / n, loss / n
    out[n] = 100 - 100 / (1 + gain / loss) if loss else 100
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        gain = (gain * (n - 1) + max(d, 0)) / n
        loss = (loss * (n - 1) + max(-d, 0)) / n
        out[i] = 100 - 100 / (1 + gain / loss) if loss else 100
    return out


def bollinger(c, n=20, k=2.0):
    mid, up, lo = [None] * len(c), [None] * len(c), [None] * len(c)
    for i in range(n - 1, len(c)):
        w = c[i - n + 1:i + 1]
        m = sum(w) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / n)
        mid[i], up[i], lo[i] = m, m + k * sd, m - k * sd
    return mid, up, lo


def atr(cd, n=14):
    out, a = [None] * len(cd), None
    for i in range(1, len(cd)):
        tr = max(cd[i]["h"] - cd[i]["l"], abs(cd[i]["h"] - cd[i - 1]["c"]), abs(cd[i]["l"] - cd[i - 1]["c"]))
        if i < n:
            a = tr if a is None else a + tr
            if i == n - 1:
                a = a / n
        else:
            a = (a * (n - 1) + tr) / n
            out[i] = a
    return out


def indicators(cd):
    c = [x["c"] for x in cd]
    mid, up, lo = bollinger(c)
    return {"c": c, "rsi": rsi(c), "mid": mid, "up": up, "lo": lo,
            "e20": ema(c, 20), "e50": ema(c, 50), "atr": atr(cd)}


# ---------------- strategies: return (side, stop_distance, target_distance) or None ----------------
def s_rsi_bb(ind, i):
    c, a, mid = ind["c"][i], ind["atr"][i], ind["mid"][i]
    if None in (a, mid, ind["rsi"][i], ind["lo"][i]):
        return None
    if c < ind["lo"][i] and ind["rsi"][i] < 30:
        return ("long", 1.5 * a, max(mid - c, 1.0 * a))
    if c > ind["up"][i] and ind["rsi"][i] > 70:
        return ("short", 1.5 * a, max(c - mid, 1.0 * a))
    return None


def s_ema_cross(ind, i):
    a, e20, e50 = ind["atr"][i], ind["e20"], ind["e50"]
    if i < 1 or None in (a, e20[i], e50[i], e20[i - 1], e50[i - 1]):
        return None
    if e20[i - 1] <= e50[i - 1] and e20[i] > e50[i]:
        return ("long", 2.0 * a, 3.0 * a)
    if e20[i - 1] >= e50[i - 1] and e20[i] < e50[i]:
        return ("short", 2.0 * a, 3.0 * a)
    return None


def s_bb_mid(ind, i):
    c, a, mid, r = ind["c"], ind["atr"][i], ind["mid"], ind["rsi"][i]
    if i < 1 or None in (a, mid[i], mid[i - 1], r):
        return None
    if c[i - 1] <= mid[i - 1] and c[i] > mid[i] and r > 50:
        return ("long", 1.5 * a, 2.5 * a)
    if c[i - 1] >= mid[i - 1] and c[i] < mid[i] and r < 50:
        return ("short", 1.5 * a, 2.5 * a)
    return None


STRATEGIES = {
    "rsi_bb": ("RSI + Bollinger reversal", s_rsi_bb),
    "ema_cross": ("EMA 20/50 cross", s_ema_cross),
    "bb_mid": ("Bollinger middle-line cross", s_bb_mid),
}


# ---------------- paper trading engine ----------------
def simulate(cd, ind, strat_fn, start, pos, bal, key):
    """Walk candles from index `start`. Returns (closed_trades, open_pos, balance)."""
    trades = []
    for i in range(start, len(cd)):
        k = cd[i]
        if pos and k["ts"] > pos["entry_ts"]:
            pos["bars"] += 1
            d = 1 if pos["side"] == "long" else -1
            exit_px = reason = None
            hit_sl = k["l"] <= pos["sl"] if d == 1 else k["h"] >= pos["sl"]
            hit_tp = k["h"] >= pos["tp"] if d == 1 else k["l"] <= pos["tp"]
            if hit_sl:                       # if both hit in one candle, assume the worst
                exit_px, reason = pos["sl"], "stop"
            elif hit_tp:
                exit_px, reason = pos["tp"], "target"
            elif pos["bars"] >= MAX_HOLD_BARS:
                exit_px, reason = k["c"], "time"
            if exit_px is not None:
                pnl = (exit_px - pos["entry"]) * pos["qty"] * d - FEE * (pos["entry"] + exit_px) * pos["qty"]
                bal += pnl
                trades.append({"key": key, "side": pos["side"], "entry_ts": pos["entry_ts"], "exit_ts": k["ts"],
                               "entry": round(pos["entry"], 4), "exit": round(exit_px, 4),
                               "reason": reason, "pnl": round(pnl, 2), "balance": round(bal, 2)})
                pos = None
        if pos is None:
            sig = strat_fn(ind, i)
            if sig:
                side, sl_d, tp_d = sig
                if sl_d > 0 and bal > 0:
                    entry = k["c"]
                    qty = min(bal * RISK_PER_TRADE / sl_d, bal / entry)   # no leverage
                    d = 1 if side == "long" else -1
                    pos = {"side": side, "entry": entry, "entry_ts": k["ts"], "qty": qty,
                           "sl": entry - d * sl_d, "tp": entry + d * tp_d, "bars": 0}
    return trades, pos, bal


def stats(trades):
    n = len(trades)
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [-t["pnl"] for t in trades if t["pnl"] <= 0]
    gp, gl = sum(wins), sum(losses)
    return {"trades": n, "wins": len(wins),
            "win_rate": round(100 * len(wins) / n, 1) if n else None,
            "profit_factor": round(gp / gl, 2) if gl else (None if not gp else 99.0),
            "net": round(gp - gl, 2)}


# ---------------- main ----------------
def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", choices=["night", "morning"])
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--shift", type=int, default=0)
    args = ap.parse_args()

    os.makedirs(DATA, exist_ok=True)
    now = datetime.now(timezone.utc)
    label = args.label or ("night" if now.astimezone(NAIROBI).hour >= 12 else "morning")

    state = json.load(open(STATE_F)) if os.path.exists(STATE_F) else {
        "started": iso(int(now.timestamp() * 1000)), "last_ts": {}, "pos": {}, "bal": {}, "trades": [], "runs": []}

    new_trades, backtest, marks, errors = [], {}, {}, []
    for sym in SYMBOLS:
        try:
            cd = fake_candles(sym, args.shift) if args.offline else fetch_candles(sym)
        except Exception as e:
            errors.append(str(e))
            continue
        ind = indicators(cd)
        marks[sym] = cd[-1]["c"]
        for sid, (_, fn) in STRATEGIES.items():
            key = f"{sid}|{sym}"
            # reference backtest over the downloaded history (fresh account, same rules)
            bt_trades, _, bt_bal = simulate(cd, ind, fn, 60, None, START_BALANCE, key)
            st = stats(bt_trades)
            st["return_pct"] = round(100 * (bt_bal / START_BALANCE - 1), 1)
            st["days"] = round((cd[-1]["ts"] - cd[60]["ts"]) / 86400000, 1)
            backtest[key] = st
            # live paper trading: only candles newer than the last one already processed
            if key not in state["last_ts"]:
                state["last_ts"][key] = cd[-1]["ts"]      # start fresh from now
                state["bal"][key] = START_BALANCE
                state["pos"][key] = None
                continue
            start = next((i for i, k in enumerate(cd) if k["ts"] > state["last_ts"][key]), len(cd))
            tr, pos, bal = simulate(cd, ind, fn, start, state["pos"].get(key), state["bal"][key], key)
            state["last_ts"][key], state["pos"][key], state["bal"][key] = cd[-1]["ts"], pos, bal
            new_trades += tr

    state["trades"] = (state["trades"] + new_trades)[-1000:]

    # per-account summary
    accounts, total_eq = {}, 0.0
    for key, bal in state["bal"].items():
        sid, sym = key.split("|")
        pos, eq = state["pos"].get(key), bal
        open_info = None
        if pos and sym in marks:
            d = 1 if pos["side"] == "long" else -1
            eq += (marks[sym] - pos["entry"]) * pos["qty"] * d
            open_info = {"side": pos["side"], "entry": round(pos["entry"], 4), "sl": round(pos["sl"], 4),
                         "tp": round(pos["tp"], 4), "since": iso(pos["entry_ts"])}
        mine = [t for t in state["trades"] if t["key"] == key]
        accounts[key] = {"strategy": sid, "symbol": sym, "name": STRATEGIES[sid][0], "balance": round(bal, 2),
                         "equity": round(eq, 2), "open": open_info, **stats(mine)}
        total_eq += eq

    start_total = START_BALANCE * len(state["bal"]) or 1.0
    prev = state["runs"][-1]["equity"] if state["runs"] else start_total
    run = {"ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "label": label, "equity": round(total_eq, 2),
           "change": round(total_eq - prev, 2), "new_trades": len(new_trades),
           "new_pnl": round(sum(t["pnl"] for t in new_trades), 2),
           "open": sum(1 for a in accounts.values() if a["open"]), "errors": errors}
    state["runs"] = (state["runs"] + [run])[-300:]

    report = {"updated": run["ts"], "started": state["started"], "paper_only": True,
              "start_equity": start_total, "equity": run["equity"],
              "return_pct": round(100 * (total_eq / start_total - 1), 2),
              "settings": {"symbols": SYMBOLS, "interval": INTERVAL, "risk_per_trade_pct": RISK_PER_TRADE * 100,
                           "fee_pct_per_side": FEE * 100, "max_hold_hours": MAX_HOLD_BARS},
              "last_run": run, "runs": state["runs"], "accounts": accounts,
              "trades": state["trades"][-60:], "backtest": backtest}

    json.dump(state, open(STATE_F, "w"), separators=(",", ":"))
    json.dump(report, open(REPORT_F, "w"), separators=(",", ":"))

    local = now.astimezone(NAIROBI).strftime("%a %d %b %H:%M")
    lines = [f"# {label.title()} report - {local} (Nairobi)", "",
             f"Paper equity: ${run['equity']:.2f} ({report['return_pct']:+.2f}% since start)",
             f"Change this run: ${run['change']:+.2f} | new trades: {run['new_trades']} | open: {run['open']}", ""]
    for key, a in accounts.items():
        lines.append(f"- {a['name']} {a['symbol']}: ${a['equity']:.2f}, {a['trades']} trades")
    if errors:
        lines += ["", "Errors: " + "; ".join(errors)]
    open(LATEST_F, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if errors and not marks else 0


if __name__ == "__main__":
    sys.exit(main())
