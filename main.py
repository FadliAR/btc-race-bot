"""Trend-and-momentum bot for the make.fun BTC race (agent-fun/bars-replay/v1).

Long-only cbBTC against USDC, one position at a time, paper money. The bot
speaks the bars-replay protocol through the kit's Python shim, vendored
byte-for-byte under vendor/ (the grader runs `python3 -P -s /bot/main.py`,
which leaves this file's directory off sys.path, so it is added by hand).

Strategy, in one paragraph: a full round trip costs roughly 30-45 bps in
fees, slippage and price impact at these pool depths, so the bot only buys
when a move is already under way that is large compared with those costs --
the fast EMA above the slow EMA with real separation between them, and the
trailing momentum above a floor, with the slow EMA not still falling at
more than a drift. It sizes in two tranches up to 80% of equity -- a first
tranche on the signal, the rest only once that tranche is in profit -- to
keep its own price impact down and to risk less on entries that fail fast.
While it holds, it banks half the position once it is well in profit, and
a trailing stop a few ATRs under the best close since entry, a hard stop
under the entry cost, a trend break (fast EMA back under the slow one) and
a maximum hold each take the rest out; after any exit it waits out a
cooldown, a long one after a losing round trip, so a choppy patch cannot
whipsaw it through repeated round trips. Everything is a deterministic
function of the bars seen so far: no clock, no randomness, no files, no
network.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"
PRICE_SCALE = 100000  # hello: CBBTC prices carry 5 decimals

# --- strategy parameters (module constants; tuned on the dev window) ---
EMA_FAST = 15          # fast EMA span, bars
EMA_SLOW = 50          # slow EMA span, bars
ATR_N = 30             # Wilder ATR span, bars
MOM_N = 20             # momentum lookback, bars
MOM_MIN = 0.0040       # trailing momentum must exceed +0.40% to enter
SEP_MIN_ATR = 0.35     # (fast - slow) EMA must exceed this many ATRs
SIZE_BPS = 8000        # spend 80% of equity per entry
TRAIL_ATR = 3.5        # trailing stop: this many ATRs under the peak close
HARD_STOP_BPS = 250    # exit at once if the position is 2.5% under its cost
TP_BPS = 200           # bank half the position once it is 2% in profit
SLOPE_N = 25           # slow-EMA slope lookback, bars
SLOPE_MIN = -0.0010    # enter only if the slow EMA fell less than this
LOSS_COOLDOWN = 120    # bars to sit out after a losing round trip
FIRST_BPS = 5500       # first tranche: 55% of equity on the signal
ADD_GAIN_BPS = 40      # add the rest once the position is 0.4% in profit
ADD_MIN_BARS = 3       # ... and at least this many bars into the hold
MAX_HOLD = 360         # never hold longer than this many bars
COOLDOWN = 30          # bars to sit out after an exit
WINDOW_BARS = 1440     # both windows of this race run 1,440 scored bars
MIN_BARS_LEFT = 45     # no fresh entries this close to the window's end
BUY_SLIP_BPS = 150     # max_slippage_bps on buys
SELL_SLIP_BPS = 500    # max_slippage_bps on sells (exits must get out)

MIN_NOTIONAL = 1_000_000  # hello.fees.min_notional_quote_units (1 USDC)


class Bot:
    """The strategy's whole state: indicators over the closes seen so far."""

    def __init__(self):
        self.closes = []        # float prices, one per batch that carried a bar
        self.ema_fast = None
        self.ema_slow = None
        self.slow_hist = []     # slow EMA after each bar, for its slope
        self.atr = None         # Wilder ATR in price units
        self.prev_close = None
        self.peak = None        # best close since the current position opened
        self.held_bars = 0      # batches answered while holding
        self.cooldown = 0       # batches of re-entry ban left
        self.was_holding = False
        self.tp_done = False    # the profit bank already taken on this position
        self.added = False      # the second tranche already bought
        self.entry_equity = None  # equity when the current entry was decided

    # -- indicators ------------------------------------------------------
    def _step(self, price):
        prev = self.closes[-1] if self.closes else None
        self.closes.append(price)
        if self.ema_fast is None:
            self.ema_fast = price
            self.ema_slow = price
        else:
            kf = 2.0 / (EMA_FAST + 1)
            ks = 2.0 / (EMA_SLOW + 1)
            self.ema_fast += kf * (price - self.ema_fast)
            self.ema_slow += ks * (price - self.ema_slow)
        self.slow_hist.append(self.ema_slow)
        if prev is not None:
            tr = abs(price - prev)
            self.atr = tr if self.atr is None else self.atr + (tr - self.atr) / ATR_N

    # -- the per-batch decision ------------------------------------------
    def on_batch(self, state, batch):
        bar = None
        for b in batch["bars"]:
            if b["symbol"] == SYMBOL:
                bar = b
        if bar is None:
            return []
        price = int(bar["c"]) / PRICE_SCALE
        self._step(price)
        if state.warming_up:
            return []

        holding = state.positions.get(SYMBOL)
        if holding is not None:
            if not self.was_holding:
                self.peak = price
                self.held_bars = 0
                self.tp_done = False
                self.added = False
            self.was_holding = True
            self.held_bars += 1
            if price > self.peak:
                self.peak = price
            if self._exit(state, holding, price):
                return [{"side": "sell", "symbol": SYMBOL, "qty": holding.qty,
                         "max_slippage_bps": SELL_SLIP_BPS}]
            if not self.tp_done:
                value = state.value(SYMBOL, holding.qty)
                if value * 10000 >= holding.cost * (10000 + TP_BPS):
                    half = holding.qty // 2
                    if half > 0 and state.value(SYMBOL, half) >= MIN_NOTIONAL:
                        self.tp_done = True
                        return [{"side": "sell", "symbol": SYMBOL, "qty": half,
                                 "max_slippage_bps": SELL_SLIP_BPS}]
            if not self.added and self.held_bars >= ADD_MIN_BARS:
                # Add to a winner: the rest of the size only once the first
                # tranche is in profit and the trend still stands, so a
                # flicker that fails fast only ever risks the first tranche.
                value = state.value(SYMBOL, holding.qty)
                if (value * 10000 >= holding.cost * (10000 + ADD_GAIN_BPS)
                        and self.ema_fast is not None
                        and self.ema_fast > self.ema_slow):
                    room = state.equity * SIZE_BPS // 10000 - value
                    amount = min(state.cash, room)
                    if amount >= MIN_NOTIONAL:
                        self.added = True
                        return [{"side": "buy", "symbol": SYMBOL,
                                 "notional_quote_units": amount,
                                 "max_slippage_bps": BUY_SLIP_BPS}]
            return []

        if self.was_holding:
            # The position vanished since the last batch: an exit filled.
            # A round trip that lost money earns a longer sit-out: hostile
            # regimes are where repeated entries bleed the costs.
            lost = (self.entry_equity is not None
                    and state.equity < self.entry_equity)
            self.was_holding = False
            self.peak = None
            self.held_bars = 0
            self.tp_done = False
            self.added = False
            self.entry_equity = None
            self.cooldown = LOSS_COOLDOWN if lost else COOLDOWN
        if self.cooldown > 0:
            self.cooldown -= 1
            return []
        if self._enter(state, price):
            amount = min(state.cash, state.equity * FIRST_BPS // 10000)
            if amount >= MIN_NOTIONAL:
                self.entry_equity = state.equity
                return [{"side": "buy", "symbol": SYMBOL,
                         "notional_quote_units": amount,
                         "max_slippage_bps": BUY_SLIP_BPS}]
        return []

    def _exit(self, state, holding, price):
        if self.held_bars >= MAX_HOLD:
            return True
        value = state.value(SYMBOL, holding.qty)
        if value * 10000 <= holding.cost * (10000 - HARD_STOP_BPS):
            return True
        if self.atr is not None and self.peak is not None:
            if price <= self.peak - TRAIL_ATR * self.atr:
                return True
        if (self.ema_fast is not None and self.ema_slow is not None
                and len(self.closes) > EMA_SLOW and self.ema_fast < self.ema_slow):
            return True
        return False

    def _enter(self, state, price):
        n = len(self.closes)
        if n <= max(EMA_SLOW, MOM_N) or self.atr is None:
            return False
        scored = state.seq - state.warmup_bars
        if WINDOW_BARS - scored < MIN_BARS_LEFT:
            # Too close to the end: a fresh round trip cannot pay its
            # costs back before the flatten.
            return False
        if self.ema_fast <= self.ema_slow:
            return False
        if self.ema_fast - self.ema_slow < SEP_MIN_ATR * self.atr:
            return False
        if len(self.slow_hist) > SLOPE_N:
            then = self.slow_hist[-1 - SLOPE_N]
            if then > 0 and self.ema_slow / then - 1.0 < SLOPE_MIN:
                # The medium trend is still falling at more than a drift:
                # bounces inside it are flickers, not turns.
                return False
        past = self.closes[-1 - MOM_N]
        if past <= 0 or price / past - 1.0 < MOM_MIN:
            return False
        return True


_BOT = Bot()


def on_hello(state):
    global _BOT
    _BOT = Bot()


def on_batch(state, batch):
    return _BOT.on_batch(state, batch)


if __name__ == "__main__":
    makefun_bars.run(on_batch, on_hello=on_hello)
