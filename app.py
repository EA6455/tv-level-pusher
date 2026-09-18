"""TV level pusher — always-on micro-service.

Polls TradingView's public scanner (the same endpoint their website uses)
every 3.5s from THIS service's own IP and pushes the OANDA/FOREXCOM spot
level to the XAUUSD signal desk, which hard-locks its displayed price to
it. Separate IP from the desk = immune to scanner throttling of the
desk's IP. The desk pings /health every 5 min to keep this free instance
awake; these pushes keep the desk awake in return.
"""
import json
import os
import threading
import time
import urllib.request

from flask import Flask, jsonify

PROD_URL = os.environ.get(
    "PROD_URL", "https://render-trading-chart-desk.onrender.com")
SECRET = os.environ.get("TV_PUSH_SECRET", "")
TFS = ["OANDA:XAUUSD", "FOREXCOM:XAUUSD"]

stats = {"tv_ok": 0, "tv_fail": 0, "push_ok": 0, "push_fail": 0,
         "level": None, "last_t": 0.0, "err": None, "delay": 3.5}


def _poll_tv():
    body = json.dumps({"symbols": {"tickers": TFS, "query": {"types": []}},
                       "columns": ["close"]}).encode()
    req = urllib.request.Request(
        "https://scanner.tradingview.com/global/scan", data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0",
                 "Origin": "https://www.tradingview.com",
                 "Referer": "https://www.tradingview.com/"})
    with urllib.request.urlopen(req, timeout=6) as r:
        j = json.load(r)
    closes = [row["d"][0] for row in j.get("data") or []
              if isinstance(row.get("d"), list) and row["d"]
              and isinstance(row["d"][0], (int, float))]
    if not closes:
        raise ValueError("scanner returned no data")
    closes.sort()
    return closes[len(closes) // 2]


def _push(level):
    req = urllib.request.Request(
        PROD_URL + "/api/tv/push",
        data=json.dumps(dict(secret=SECRET, price=level,
                             t=int(time.time()))).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=8).read()


def _loop():
    delay = 3.5
    while True:
        level = None
        try:
            level = _poll_tv()
            stats.update(tv_ok=stats["tv_ok"] + 1, level=level,
                         last_t=time.time(), err=None)
            delay = 3.5
        except Exception as e:  # noqa: BLE001  — throttled / unreachable
            stats.update(tv_fail=stats["tv_fail"] + 1, err=str(e)[:80])
            delay = min(300.0, delay * 2)
        if level is not None:
            try:
                _push(level)
                stats["push_ok"] += 1
            except Exception:  # noqa: BLE001  — desk busy / restarting
                stats["push_fail"] += 1
        stats["delay"] = delay
        time.sleep(delay)


app = Flask(__name__)


@app.route("/health")
def health():
    return jsonify(ok=True, level=stats["level"],
                   age=round(time.time() - stats["last_t"], 1)
                   if stats["last_t"] else None,
                   **{k: v for k, v in stats.items() if k != "last_t"})


threading.Thread(target=_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
