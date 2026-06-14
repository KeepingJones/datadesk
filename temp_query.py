import sqlite3
con = sqlite3.connect('platform.db')
row = con.execute("""
    SELECT name, metrics FROM backtest_runs 
    WHERE json_extract(metrics, '$.sharpe') IS NOT NULL 
    AND json_extract(metrics, '$.max_drawdown') >= -0.30 
    ORDER BY json_extract(metrics, '$.sharpe') DESC 
    LIMIT 1
""").fetchone()
print(row)
