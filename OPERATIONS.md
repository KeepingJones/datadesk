# Operations Manual

This document outlines the daily and weekly operational workflows for managing DataDesk. It details exactly how to run the system, monitor its health, and perform routine maintenance.

## Daily Routine (Trading Days)

### 1. Pre-Market (08:00 - 09:30 ET)
- **Check Analyst Reports**: Open the dashboard (`python main.py serve`) and review the out-of-session analyst output from the nightly run.
- **Review Strategy Rebalancer Queue**: Check if any trades have been queued due to closed exchanges from the previous day.
- **Account Sync**: Confirm that Alpaca paper / T212 live balances in the dashboard match the broker applications.

### 2. Intraday Monitoring (09:30 - 15:30 ET)
- **Status Dashboard**: Ensure the active monitors (`rebalancer`, `price_feed`, `news_monitor`) are running and healthy on the daemon control panel.
- **Event Risk**: The dashboard will flag major news events (e.g. CPI prints, Fed decisions). The `trump_monitor` and `news_monitor` will log relevant sentiment shifts in real time.

### 3. Market On Close (MOC) Window (15:45 - 16:00 ET)
- **Daily Rebalance**: The `rebalancer` daemon fires automatically at 15:48 ET. It queries `platform.db` for the highest Sharpe strategy, calculates target weights, compares against current active positions, and executes drift-adjusted MOC orders.
- **Execution Validation**: Check the terminal output or the Shadow Audit logs in the dashboard to confirm all `submit_signal` calls routed successfully either to the broker or shadow record.

### 4. Post-Market (16:30 - 18:00 ET)
- **Out-of-Session Analysts**:
  - `research_analyst` automatically scans the alternate data stores to flag new momentum breakouts.
  - `risk_analyst` computes portfolio beta, sector concentration, and pairwise correlation.
- **Database Backup**: The SQLite databases use WAL mode. Routine file backups should occur post-market.

## Weekly Routine (Saturdays 07:00 UTC)

### 1. Data Gap Fill & Refresh
- **Run the Weekly Update**: Either press the `Weekly Update` button on the dashboard or run `python main.py weekly-update`. This ensures any missing daily bars from the week are populated into `history.db` and refreshes fundamental snapshots (e.g. P/E, EPS) into `altdata.db`.
- **Enrich Tickers**: Run `python main.py enrich` to fetch missing fundamentals for newly discovered tickers.

### 2. Universe Expansion
- **Thematic Screening**: Run `python main.py screen` to view the S-curve thematic radar. If a new theme (e.g. `QUANTUM`) turns HOT, consider expanding the universe.
- **Execute Expansion**: Run `python main.py universe-expand --theme <THEME>` to onboard new ETFs/components.

### 3. Holdout & Validation
- **Run OOS Holdout**: Execute `python main.py holdout` to ensure the core strategy is still meeting the Gate 1 hurdles (Sharpe > 1.0, MaxDD > -30%).
- **Run Sweep**: Periodically trigger a parameter sweep (`POST /api/sweep/run`) to allow the `strategy_analyst` to discover optimized parameter combinations and promote them to the active rebalancer.

## System Failure Procedures

- **Broker Disconnect**: If the `price_feed` drops, trailing stops will not execute intra-day. Ensure `rebalancer` is active as it will true-up positions at MOC regardless of intra-day trailing stop failures.
- **Data Fetch Rate Limits**: If `yfinance` rate limits trigger during a backfill, the script automatically pauses and retries. Do not force restart.

## Adding a New Data Provider
1. Secure the API key and place it in the `.env` file.
2. Build the fetcher in `datadesk.ingest`.
3. Add the initialisation schema to `cmd_init_db()` in `main.py` if a new SQLite table is required.
