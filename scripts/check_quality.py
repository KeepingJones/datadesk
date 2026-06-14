import sqlite3

con = sqlite3.connect("altdata.db")
rows = con.execute(
    """
    SELECT r.ticker, i.sector, r.market_cap, r.debt_to_equity, r.net_margin
    FROM equity_ratios r
    INNER JOIN (SELECT ticker, MAX(id) AS max_id FROM equity_ratios GROUP BY ticker) l
        ON r.id = l.max_id
    LEFT JOIN equity_info i ON r.ticker = i.ticker
    ORDER BY r.market_cap DESC NULLS LAST
    """
).fetchall()
con.close()

no_data, small_cap, high_de, loss, passing = [], [], [], [], []
for ticker, sector, mkt, de, nm in rows:
    if mkt is None and de is None and nm is None:
        no_data.append(ticker)
    elif mkt is not None and mkt < 50e6:
        small_cap.append(ticker)
    elif de is not None and de > 5.0:
        high_de.append((ticker, sector, de, mkt))
    elif nm is not None and nm < -0.5:
        loss.append((ticker, sector, nm))
    else:
        passing.append(ticker)

print("HIGH D/E (sorted):")
for t, s, de, m in sorted(high_de, key=lambda x: -x[2])[:20]:
    cap = str(round(m/1e9, 1)) + "B" if m else "—"
    print(f"  {t:<14} {(s or '—'):<22} D/E={de:.1f}  mkt={cap}")

print("\nDEEPLY LOSS-MAKING:")
for t, s, nm in loss:
    print(f"  {t:<14} {(s or '—'):<22} net_margin={nm:.1%}")

print(f"\nno_data={len(no_data)}, small_cap={len(small_cap)}, high_DE={len(high_de)}, loss={len(loss)}, pass={len(passing)}")
print(f"no_data tickers (first 20): {no_data[:20]}")
