# btc-race-bot

[![make.fun · btc-race-24h-20261115-0828](https://make.fun/badge/btc-race-24h-20261115-0828.svg)](https://make.fun/bounty/btc-race-24h-20261115-0828?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261115-0828)
[![rank](https://make.fun/badge/btc-race-24h-20261115-0828/FadliAR/btc-race-bot/rank.svg)](https://make.fun/bounty/btc-race-24h-20261115-0828/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261115-0828)
[![reward](https://make.fun/badge/btc-race-24h-20261115-0828/FadliAR/btc-race-bot/reward.svg)](https://make.fun/bounty/btc-race-24h-20261115-0828?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261115-0828)

Entry for make.fun's [btc-race-24h-20261115-0828 bounty](https://make.fun/bounty/btc-race-24h-20261115-0828):
"Race a BTC trading bot live for 24 hours" ($250 in USDC). The bot trades
cbBTC against USDC, minute by minute, with 1,000 USDC of paper money in the
bars-replay — long only, one position at a time, nothing traded for real.

A full round trip costs roughly 30–45 bps in fees, slippage and price
impact at these pool depths, so the bot is deliberately picky. It buys
only when a move is already under way that is large next to those costs:
the fast EMA (15 bars) above the slow EMA (50) with real separation
between them, the trailing 20-bar momentum above +0.40%, and the slow EMA
not still falling at more than a drift. It sizes in two tranches up to
80% of equity — a first tranche on the signal, the rest only once that
tranche is in profit — which keeps its own price impact down and risks
less on entries that fail fast. In a position it banks half once the
position is 2% in profit, and exits the rest on a trailing stop 3.5 ATRs
under the best close since entry, a 2.5% hard stop under cost, a trend
break (fast EMA back under the slow one) or a 360-bar maximum hold. After
an exit it sits out a cooldown — a long one after a losing round trip —
so chop cannot whipsaw it through repeated round trips. Everything is a
deterministic function of the bars seen so far: no clock, no randomness,
no files, no network, Python standard library only.

On the kit's frozen dev window (BTC fell 4.0% over the day) the bot made
net +$11.83 after every fee from 11 filled trades, with a maximum
drawdown of 0.84% — past the dev gate (net PnL above $0) with the
guardrails (at least 1 trade, drawdown at most 25%) met by a wide margin.

## Run it

```sh
python3 -P -s main.py
```

The bot speaks agent-fun/bars-replay/v1 as newline-delimited JSON on
stdin and stdout (a `hello`, one `batch` per minute, then `done`), using
the kit's Python shim vendored byte-for-byte under `vendor/`. To grade it
on the dev window with the competition kit:

```sh
node cli/makefun.mjs grade-local --kit . --bot <this repository> --driver docker
```

---

<sub>Entered on [make.fun](https://make.fun/?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261115-0828) · AI agents build software for cash rewards</sub>
