"""
2025년 9월 22일 ~ 2026년 1월 2일 (15주) KRX 과거 데이터 수집 스크립트
n=30 DiD Z-score 확보용
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import date, timedelta
from krx_data_fetcher import (
    fetch_weekly_etf_data, load_cache, save_cache
)

TARGET_WEEKS = [
    (date(2025,  9, 22), date(2025,  9, 26)),
    (date(2025,  9, 29), date(2025, 10,  3)),
    (date(2025, 10,  6), date(2025, 10, 10)),
    (date(2025, 10, 13), date(2025, 10, 17)),
    (date(2025, 10, 20), date(2025, 10, 24)),
    (date(2025, 10, 27), date(2025, 10, 31)),
    (date(2025, 11,  3), date(2025, 11,  7)),
    (date(2025, 11, 10), date(2025, 11, 14)),
    (date(2025, 11, 17), date(2025, 11, 21)),
    (date(2025, 11, 24), date(2025, 11, 28)),
    (date(2025, 12,  1), date(2025, 12,  5)),
    (date(2025, 12,  8), date(2025, 12, 12)),
    (date(2025, 12, 15), date(2025, 12, 19)),
    (date(2025, 12, 22), date(2025, 12, 26)),
    (date(2025, 12, 29), date(2026,  1,  2)),
]

def week_label(mon, fri):
    return f"{mon.month}.{mon.day}-{fri.month}.{fri.day}"

def main():
    cache = load_cache()
    existing = set(cache.keys())
    to_collect = [(mon, fri) for mon, fri in TARGET_WEEKS
                  if week_label(mon, fri) not in existing]
    total = len(to_collect)
    if total == 0:
        print("All weeks already collected.")
        return

    print(f"=== KRX history collection: {total} weeks ===")
    success = 0
    for idx, (mon, fri) in enumerate(to_collect, 1):
        lbl = week_label(mon, fri)
        pct = int(idx / total * 100)
        print(f"\n[{idx}/{total} {pct}%] {lbl} collecting...", flush=True)
        try:
            df = fetch_weekly_etf_data(mon, fri)
            if df is not None and not df.empty:
                cache[lbl] = df
                save_cache(cache)
                success += 1
                print(f"  OK: {lbl} ({len(df)} ETFs) — {success} done so far", flush=True)
            else:
                print(f"  EMPTY: {lbl} (holiday week?) — skipping", flush=True)
        except Exception as e:
            err = str(e)
            if any(kw in err.lower() for kw in ["login","session","auth","401","403"]):
                print(f"  SESSION ERROR: {err}")
                print("  => Stopping. Check VPN and re-run.")
                sys.exit(1)
            else:
                print(f"  FAIL: {lbl}: {err}", flush=True)
        if idx < total:
            time.sleep(3)

    print(f"\n=== Done: {success}/{total} weeks collected ===")
    print(f"Cache now has {len(load_cache())} weeks total")

if __name__ == "__main__":
    main()
