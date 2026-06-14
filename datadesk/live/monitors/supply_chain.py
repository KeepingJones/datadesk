"""
Supply-chain lead-lag monitor — REAL data path, no simulation.

Matrix from research-handoff-v3 (supply_chain_matrix.json). Polls intraday
moves for focal names; when a focal stock spikes beyond the threshold and a
related name (supplier/customer) hasn't followed yet, emits a BUY signal on
the laggard. Signals land in the shadow store; execution requires arming.

Honest framing (DESIGN §6.2): minute-level lead-lag is heavily arbitraged.
This runs shadow-first precisely because the edge must be proven forward.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datadesk.live.oms import OMSFastPath

logger = logging.getLogger(__name__)

MATRIX_PATH = Path(__file__).parent.parent / "supply_chain_matrix.json"
POLL_SECONDS = 120
SPIKE_THRESHOLD = 0.02  # focal move that counts as an event
LAGGARD_MAX_MOVE = 0.005  # related name still "unmoved" below this
EVENT_WEIGHT = 0.05


def load_matrix(path: Path = MATRIX_PATH) -> dict:
    with open(path) as f:
        return json.load(f)


def find_laggards(
    matrix: dict,
    moves: dict[str, float],
    spike_threshold: float = SPIKE_THRESHOLD,
    laggard_max_move: float = LAGGARD_MAX_MOVE,
) -> list[tuple[str, str, float]]:
    """
    Pure logic, fully testable: given intraday moves {ticker: pct_move},
    return [(laggard, focal, focal_move)] for focal spikes whose related
    names haven't moved yet. Only positive spikes → long-only laggard buys.
    """
    out = []
    for focal, related in matrix.items():
        focal_move = moves.get(focal)
        if focal_move is None or focal_move < spike_threshold:
            continue
        for name in related.get("suppliers", []) + related.get("customers", []):
            move = moves.get(name)
            if move is not None and abs(move) <= laggard_max_move:
                out.append((name, focal, focal_move))
    return out


def fetch_intraday_moves(tickers: list[str]) -> dict[str, float]:
    """Move since today's open from 1-minute bars (yfinance)."""
    import yfinance as yf

    moves: dict[str, float] = {}
    try:
        raw = yf.download(tickers, period="1d", interval="1m", progress=False, group_by="ticker")
        if raw is None or raw.empty:
            return moves
        for t in tickers:
            try:
                closes = raw[t]["Close"].dropna() if len(tickers) > 1 else raw["Close"].dropna()
                if len(closes) >= 2:
                    moves[t] = float(closes.iloc[-1] / closes.iloc[0] - 1)
            except Exception:
                continue
    except Exception as e:
        logger.exception(f"[SUPPLY CHAIN] intraday fetch failed: {e}")
    return moves


class SupplyChainMonitor:
    def __init__(self, oms: "OMSFastPath", matrix: dict | None = None):
        self.oms = oms
        self.is_running = False
        self.last_run = "Never"
        self.matrix = matrix if matrix is not None else load_matrix()
        self._signalled_today: set[str] = set()  # one signal per laggard per day

    def start(self):
        self.is_running = True
        logger.info("[SUPPLY CHAIN] polling intraday moves every %ss", POLL_SECONDS)
        while self.is_running:
            try:
                self.check_matrix()
            except Exception as e:
                logger.exception(f"[SUPPLY CHAIN] check failed: {e}")
            self.last_run = datetime.now().strftime("%H:%M:%S")
            for _ in range(POLL_SECONDS):
                if not self.is_running:
                    return
                time.sleep(1)

    def stop(self):
        self.is_running = False

    def check_matrix(self, moves: dict[str, float] | None = None) -> int:
        """One scan. `moves` injectable for tests; fetched live otherwise."""
        if moves is None:
            universe = sorted(
                set(self.matrix)
                | {n for rel in self.matrix.values() for v in rel.values() for n in v}
            )
            moves = fetch_intraday_moves(universe)
        if not moves:
            return 0

        fired = 0
        for laggard, focal, focal_move in find_laggards(self.matrix, moves):
            if laggard in self._signalled_today:
                continue
            logger.warning(
                f"[SUPPLY CHAIN] {focal} +{focal_move:.1%}, {laggard} unmoved — lead-lag BUY"
            )
            self.oms.submit_signal(
                laggard,
                "BUY",
                weight_pct=EVENT_WEIGHT,
                reason=f"lead-lag: {focal} +{focal_move:.1%}",
                source="supply_chain",
            )
            self._signalled_today.add(laggard)
            fired += 1
        return fired
