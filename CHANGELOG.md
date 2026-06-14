# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `init-db` command in `main.py` to auto-initialize all database schemas securely without needing migrations.
- `--preset ai_semi` flag to `backfill` command for instant on-boarding.
- Explicit zero-arg Quickstart guide when running `python main.py`.
- T212 Broker Integration (ISA execution path) for commission-free, zero-CGT UK equities.
- Comprehensive `OPERATIONS.md` documenting the daily and weekly operational workflows.

### Changed
- Refactored `OMS.fast_path()` to persist broker `order_id` back to `shadow_signals` for reconciliation.
- Hardened default `DATADESK_ARM_BROKER` logic to fail-safe shadow mode (0) by default.
- Enhanced rebalancer loop to dynamically query `platform.db` for the highest Sharpe strategy rather than using a hard-coded sweep reference.
- Detailed architectural breakdown in `README.md` targeting quant-dev employer personas.

### Fixed
- Sharpe and Max Drawdown reconciliation metrics explicitly resolved for Gate 1.
