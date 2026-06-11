<div align="center">
  <h1>⚡ Alpha Command (DataDesk)</h1>
  <p><strong>Next-Generation Event-Driven Trading Engine & AGI Infrastructure Portfolio Manager</strong></p>
</div>

## 🌐 Overview
**Alpha Command** (internally known as *DataDesk*) is an advanced, multi-agent quantitative trading system. It moves beyond traditional quantitative momentum and mean-reversion strategies by heavily weighting unstructured data streams—deploying a continuous local AI inference loop to detect and trade on real-time market anomalies.

The system incorporates the **Situational Awareness Framework**, aggressively positioning around AGI infrastructure bottlenecks (Compute, Fabrication, and Energy).

## ✨ Core Features

- 🧠 **Agentic Background Workers**: Uses continuous polling (via simulated Phi-3.5 local inference) to parse unstructured data like earnings calls, news drops, and regulatory filings, adjusting fundamental fair-value targets on the fly.
- ⚡ **Fast-Path OMS**: Bypasses daily batch processing for latency-sensitive events. Executes intraday signals directly into Alpaca Paper Trading with strict portfolio-level risk limits (Max Position %, Max Daily Drawdown).
- 🚨 **Real-Time Event Daemons**:
  - **Jensen Monitor**: Scans live keynote transcripts for supply-chain partner shoutouts (e.g., DELL, ARM) and fires immediate long signals.
  - **Trump Monitor**: Parses Truth Social feeds, running sentiment analysis to front-run retail volatility.
  - **Supply-Chain Matrix**: Detects lead-lag anomalies (e.g., NVDA surging while TSM is flat) and mechanically exploits the arbitrage.
  - **Global Macro/Geopolitics**: Monitors the Federal Reserve and semiconductor supply-chain tensions.
- 📊 **Blended Backtest Engine**: Combines Price Momentum, Congressional Insider Trading flow, and Mean Reversion, governed by global risk overlays (VIX scaling and SPY trend filtering).
- 💻 **Command & Control Console**: A sleek, real-time dark-mode HTML/FastAPI dashboard to monitor background daemons, AI feeds, Alpaca account balances, and executed live trades.

## 🚀 Getting Started

### Prerequisites
- Python 3.11+
- Alpaca API Keys (for Live Paper Trading)

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/datadesk.git
   cd datadesk
   ```

2. **Set up the environment:**
   Create a `.env` file in the root directory:
   ```env
   ALPACA_API_KEY=your_alpaca_key_here
   ALPACA_SECRET_KEY=your_alpaca_secret_here
   PAPER_TRADE_MODE=True
   ```

3. **Launch the Engine:**
   Simply double click the `launch.bat` file, or run the following commands manually:
   ```bash
   # Generates strategy weights and backtests the core portfolio
   python main.py holdout

   # Launches the Ops Console & starts all AI Daemons
   python main.py serve --port 8000
   ```

## 🏗️ Architecture

- **`datadesk/api/`**: FastAPI backend and routing for the operations dashboard.
- **`datadesk/live/oms.py`**: The Fast-Path Order Management System. Handles position scaling, trailing stops, fundamental stop-losses, and Alpaca order execution.
- **`datadesk/live/monitors/`**: The neural cortex. Contains the async background daemons that watch the market 24/7.
- **`datadesk/strategies/`**: The quantitative logic. Contains modules for Momentum, Mean Reversion, Congressional Trading, and regime overlays.

## ⚠️ Disclaimer
Alpha Command is built for **educational and paper-trading purposes only**. The repository simulates live AI reasoning frameworks and executes simulated paper trades via Alpaca. Do not use this to trade real capital without extensive risk management modifications and compliance checks.

<br>

<div align="center">
  <i>Built for the frontier.</i>
</div>
