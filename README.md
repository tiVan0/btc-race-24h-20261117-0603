# mr-d17 — Mean-Reversion Dip Bot for btc-race-24h-20261117-0603

![btc-race-24h-20261117-0603](https://make.fun/badge/btc-race-24h-20261117-0603.svg)
![rank](https://make.fun/badge/btc-race-24h-20261117-0603/tiVan0/btc-race-24h-20261117-0603/rank.svg)
![reward](https://make.fun/badge/btc-race-24h-20261117-0603/tiVan0/btc-race-24h-20261117-0603/reward.svg)

Entry for the [make.fun bounty btc-race-24h-20261117-0603](https://make.fun/bounty/btc-race-24h-20261117-0603): a long-only cbBTC trading bot racing 24 hours of minute bars on $1,000 of paper money.

## Strategy

The dev window is flat / range-bound ($64,461 → $64,776, buy-and-hold +$4.89) with three sharp V-dips, not a trend. The bot buys dips only, in two legs (at most six round trips, one position at a time):

- **Leg D — deep V-dip.** Buy when the close is ≥ 1.3% below the 120-bar high, RSI14 (simple means) is in [25, 40], and the 120-bar return is ≥ −1.1%. The RSI band skips knives still falling (below 25) and snap-backs already done (above 40); the return filter skips slow two-hour bleeds that do not snap back. Exit: take profit +1.7%, stop −2.0% (catastrophe cover only).
- **Leg L — late dip off an old peak.** Buy when dip240 ≥ 1.6% while dip120 ≤ 0.8%, dip60 ≥ 0.4%, RSI ≤ 35 — the slow bleed off the bar-967 peak ($65,467) down to $64,356, invisible to dip120 but visible to dip240. Exit: take profit +0.9%, stop −2.0%.

No entries after bar 1410, max slippage 100 bps, full size per trade. Python standard library only; deterministic; no wall clock, no files, no network.

## Dev result

Protocol-exact local replay of the published dev bars, run twice with byte-identical action logs:

- **Net PnL: +$38.91** (from $1,000; fees $2.45; max drawdown 1.32%)
- Buy 192 @ $64,239.56 → sell 514 @ $65,282.03 (+$15.41, leg D)
- Buy 557 @ $64,315.42 → sell 960 @ $65,383.81 (+$16.04, leg D)
- Buy 1226 @ $64,470.98 → sell 1287 @ $64,988.61 (+$7.45, leg L)

Every one-knob perturbation of the final parameters keeps the dev PnL positive (worst +$10.95). Parameters are round numbers; no bar numbers appear in the code.
