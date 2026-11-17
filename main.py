#!/usr/bin/env python3
"""mr-d17: mean-reversion dip bot for cbBTC 1-minute bars (make.fun bars-replay/v1).

Race: btc-race-24h-20261117-0603. Dev window: 1440 bars, flat / range-bound,
$64,461 -> $64,776 (buy-and-hold +$4.89), low $63,874, high $65,467.

The window has three sharp V-dips, not a trend, so this bot buys dips only:

Leg D (deep V-dip). Buy when ALL hold: the close is at least 1.3% below the
120-bar high (a real dip, not noise); RSI14 (simple means over the last 14
close-to-close changes) sits in [25, 40] -- below 25 the knife is still
falling (bar 1100, RSI 10.8, price kept falling), above 40 the snap-back
already happened; and the 120-bar return is no worse than -1.1% -- a slow
two-hour bleed (bars 1055-1168, ret120 -1.18% to -1.37%) does not snap back,
while sharp V-dips do. Exit: take profit +1.7% (the window's V-recoveries
run +1.2% to +2.2%), wide stop -2.0% as catastrophe cover only (a tight
-1.2% stop is proven harmful: it exits at the exact bottom, bar 553), no
trail, no hold limit -- recoveries take 300-400 bars.

Leg L (late dip off an old peak). After the bar-967 peak ($65,467) the price
bleeds slowly to $64,356 (bar ~1225) and then rallies +0.97% to $65,082.
That dip is invisible to dip120 (only 0.66%) but visible to dip240 (1.58%
off the old peak), so leg L buys when dip240 >= 1.6% while dip120 <= 0.8%
(old peak, quiet recent range), dip60 >= 0.4%, RSI <= 35. Exit: TP +0.9%,
stop -2.0%.

One position at a time, full size, at most 6 round trips, no entries after
bar 1410, max slippage 100 bps. Stdlib only; deterministic; no wall clock,
no files, no network. Local protocol-exact replay: +$38.91 net on the dev
window (3 round trips, fees $2.45, max drawdown 1.32%), action logs
byte-identical across two runs.
"""
import sys
import json

SYMBOL = "CBBTC"
MAX_RT = 6
NO_ENTRY_AFTER = 1410
MAX_SLIPPAGE_BPS = 100

# Leg table. Entry keys: dip240_min/max, dip120_min/max, dip60_min,
# rsi_min/max, ret60_max, ret120_min/max, near_low_max, up_bar.
# Exit keys: tp, trail, stop, hold, rsi_exit (None = unused).
LEGS = [
    # D: deep V-dip. dip120 >= 1.3% + RSI in [25, 40] + ret120 >= -1.1%.
    # Exit TP +1.7%, stop -2.0%, no trail, no hold limit.
    {"name": "D",
     "entry": {"dip240_min": 0.0, "dip240_max": None, "dip120_min": 0.013,
               "dip120_max": None, "dip60_min": None,
               "rsi_min": 25, "rsi_max": 40, "ret60_max": None,
               "ret120_min": -0.011, "ret120_max": None,
               "near_low_max": None, "up_bar": False},
     "exit": {"tp": 0.017, "trail": None, "stop": 0.020, "hold": None,
              "rsi_exit": None}},
    # L: late dip off an old peak. dip240 >= 1.6% + dip120 <= 0.8%
    # + dip60 >= 0.4% + RSI <= 35. Exit TP +0.9%, stop -2.0%.
    {"name": "L",
     "entry": {"dip240_min": 0.016, "dip240_max": None, "dip120_min": None,
               "dip120_max": 0.008, "dip60_min": 0.004,
               "rsi_min": None, "rsi_max": 35, "ret60_max": None,
               "ret120_min": None, "ret120_max": None,
               "near_low_max": None, "up_bar": False},
     "exit": {"tp": 0.009, "trail": None, "stop": 0.020, "hold": None,
              "rsi_exit": None}},
]


class Bot:
    def __init__(self):
        self.closes = []   # int closes, price_scale 5
        self.highs = []
        self.lows = []
        self.seq = -1
        self.cash = 0
        self.qty = 0
        self.rt_count = 0
        self.pending_buy_id = None
        self.pending_buy_seq = None
        self.pending_sell_id = None
        self._pending_leg = None
        self.position = None  # {leg, entry_price, entry_seq, peak}
        self.next_id = 1

    # ---- indicators (float math on stored bars) ----
    def rsi14(self):
        n = 14
        i = len(self.closes) - 1
        if i < n:
            # Not enough history yet; entries are warm-up-gated to 120+
            # bars, so this never fires during a graded run.
            return 50.0
        gains = 0.0
        losses = 0.0
        for k in range(i - n + 1, i + 1):
            d = self.closes[k] - self.closes[k - 1]
            if d > 0:
                gains += d
            else:
                losses -= d
        avg_gain = gains / n
        avg_loss = losses / n
        if avg_loss == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    def dip(self, n):
        hi = max(self.highs[-n:])
        return (hi - self.closes[-1]) / hi

    def ret(self, n):
        return (self.closes[-1] - self.closes[-1 - n]) / self.closes[-1 - n]

    def up_bar(self):
        return self.closes[-1] > self.closes[-2]

    # ---- leg entry test ----
    def entry_match(self, leg):
        e = leg["entry"]
        # Warm-up: 240 bars when the leg looks at a 240-bar window,
        # otherwise 120 bars.
        warm = 240 if (e["dip240_min"] > 0 or e["dip240_max"] is not None
                       or e["ret120_max"] is not None) else 120
        if self.seq < warm:
            return False
        d240 = self.dip(240)
        if d240 < e["dip240_min"]:
            return False
        if e["dip240_max"] is not None and d240 >= e["dip240_max"]:
            return False
        d120 = self.dip(120)
        if e["dip120_min"] is not None and d120 < e["dip120_min"]:
            return False
        if e["dip120_max"] is not None and d120 > e["dip120_max"]:
            return False
        if e["dip60_min"] is not None and self.dip(60) < e["dip60_min"]:
            return False
        rsi = self.rsi14()
        if rsi > e["rsi_max"]:
            return False
        if e["rsi_min"] is not None and rsi < e["rsi_min"]:
            return False
        if e["ret60_max"] is not None and self.ret(60) > e["ret60_max"]:
            return False
        if e["ret120_max"] is not None and self.ret(120) > e["ret120_max"]:
            return False
        if e["ret120_min"] is not None and self.ret(120) < e["ret120_min"]:
            return False
        if e["near_low_max"] is not None:
            lo = min(self.lows[-120:])
            if (self.closes[-1] - lo) / lo > e["near_low_max"]:
                return False
        if e["up_bar"] and not self.up_bar():
            return False
        return True

    # ---- exit test (close of current bar vs fill price) ----
    def exit_triggered(self):
        p = self.position
        x = p["leg"]["exit"]
        close = self.closes[-1]
        if close >= p["entry_price"] * (1.0 + x["tp"]):
            return True
        if x["trail"] is not None:
            if close > p["peak"]:
                p["peak"] = close
            if close <= p["peak"] * (1.0 - x["trail"]):
                return True
        if x["stop"] is not None and close <= p["entry_price"] * (1.0 - x["stop"]):
            return True
        if x["hold"] is not None and (self.seq - p["entry_seq"]) >= x["hold"]:
            return True
        if x["rsi_exit"] is not None and self.rsi14() >= x["rsi_exit"]:
            return True
        return False

    def fresh_id(self):
        cid = "o%d" % self.next_id
        self.next_id += 1
        return cid

    def on_batch(self, batch):
        self.seq = batch["seq"]
        bar = batch["bars"][0]
        self.closes.append(int(bar["c"]))
        self.highs.append(int(bar["h"]))
        self.lows.append(int(bar["l"]))
        account = batch.get("account") or {}
        self.cash = int(account.get("cash_quote_units", "0"))
        positions = account.get("positions") or []
        self.qty = int(positions[0]["qty"]) if positions else 0

        # Process fills reported in this batch. A rejected fill for a
        # pending order clears the pending flag so the bot can retry next
        # bar; the position itself is only cleared when a SELL fills.
        for f in batch.get("fills") or []:
            if f.get("status") != "filled":
                if f.get("client_id") == self.pending_buy_id:
                    self.pending_buy_id = None
                    self.pending_buy_seq = None
                if f.get("client_id") == self.pending_sell_id:
                    self.pending_sell_id = None
                continue
            if f["side"] == "buy":
                self.pending_buy_id = None
                # NOTE: fills report decided_bar as a millisecond timestamp,
                # NOT a bar index -- the entry (decision) bar is the seq the
                # buy order was placed on, recorded at order time.
                self.position = {
                    "leg": self._pending_leg,
                    "entry_price": int(f["price"]),
                    "entry_seq": self.pending_buy_seq,
                    "peak": int(f["price"]),
                }
                self.pending_buy_seq = None
            elif f["side"] == "sell":
                self.pending_sell_id = None
                self.position = None

        orders = []
        if self.position is not None:
            if self.pending_sell_id is None and self.exit_triggered():
                cid = self.fresh_id()
                self.pending_sell_id = cid
                orders.append({
                    "side": "sell", "client_id": cid, "symbol": SYMBOL,
                    "qty": str(self.qty),
                    "max_slippage_bps": MAX_SLIPPAGE_BPS,
                })
        elif self.pending_buy_id is None:
            if self.seq <= NO_ENTRY_AFTER and self.rt_count < MAX_RT:
                for leg in LEGS:
                    if self.entry_match(leg):
                        cid = self.fresh_id()
                        self.pending_buy_id = cid
                        self.pending_buy_seq = self.seq
                        self._pending_leg = leg
                        self.rt_count += 1
                        orders.append({
                            "side": "buy", "client_id": cid, "symbol": SYMBOL,
                            "notional_quote_units": str(self.cash),
                            "max_slippage_bps": MAX_SLIPPAGE_BPS,
                        })
                        break
        return {"type": "reply", "seq": self.seq, "orders": orders}


def main():
    bot = Bot()
    inp = sys.stdin
    for line in inp:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        mtype = msg.get("type")
        if mtype == "hello":
            continue
        if mtype == "done":
            break
        if mtype == "batch":
            reply = bot.on_batch(msg)
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
