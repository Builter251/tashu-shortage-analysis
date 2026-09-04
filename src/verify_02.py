#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_02.py — 재배치 탐지 로직 교차 검증

AI가 만든 SQL을 그대로 믿지 않기 위한 독립 검증이다. 두 가지를 한다.

  (A) 다른 코드 경로로 재구현: DuckDB 윈도우 함수 결과를 pandas(정렬+shift)로 다시 계산해
      자전거 표본에 대해 건별로 대조한다. 두 구현이 일치해야 한다.
  (B) 사람이 직접 읽는 검증: 자전거 몇 대의 통행 궤적을 시간순으로 출력해
      "반납지 ≠ 다음 대여지" 지점이 실제로 재배치로 잡혔는지 눈으로 확인한다.

실행:  python3 src/verify_02.py
"""
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
SORT_KEYS = ["rent_dt", "ret_dt", "rent_st", "ret_st", "dur_min"]
N_SAMPLE_BIKES = 300

con = duckdb.connect()
con.execute("PRAGMA memory_limit='2GB'")
con.execute(f"CREATE VIEW tr AS SELECT * FROM read_parquet('{PROC/'trips_dedup.parquet'}')")
con.execute(f"CREATE VIEW mv AS SELECT * FROM read_parquet('{PROC/'bike_moves.parquet'}')")

# ---------------------------------------------------------------- (A) 재구현 대조
bikes = con.execute(
    "SELECT DISTINCT bike_id FROM tr ORDER BY bike_id LIMIT ?", [N_SAMPLE_BIKES]).df()["bike_id"]
ph = ", ".join(["?"] * len(bikes))

t = con.execute(f"SELECT * FROM tr WHERE bike_id IN ({ph})", list(bikes)).df()
t = t.sort_values(["bike_id"] + SORT_KEYS).reset_index(drop=True)

prev_st = t.groupby("bike_id")["ret_st"].shift(1)
prev_dt = t.groupby("bike_id")["ret_dt"].shift(1)
mask = prev_st.notna() & (prev_st != t["rent_st"])
mine = pd.DataFrame({
    "bike_id": t.loc[mask, "bike_id"],
    "from_st": prev_st[mask],
    "to_st": t.loc[mask, "rent_st"],
    "gap_start": prev_dt[mask],
    "gap_end": t.loc[mask, "rent_dt"],
}).sort_values(["bike_id", "gap_start"]).reset_index(drop=True)

theirs = con.execute(
    f"SELECT bike_id, from_st, to_st, gap_start, gap_end FROM mv WHERE bike_id IN ({ph}) "
    f"ORDER BY bike_id, gap_start", list(bikes)).df().reset_index(drop=True)

same = mine.equals(theirs[mine.columns])
print("=" * 70)
print(f"(A) 독립 재구현 대조 — 자전거 {len(bikes)}대")
print(f"    pandas 구현 재배치: {len(mine):,}건")
print(f"    DuckDB 구현 재배치: {len(theirs):,}건")
print(f"    건별 완전 일치: {'예 ✅' if same else '아니오 ❌'}")
if not same:
    print(mine.compare(theirs[mine.columns]).head(10))

# ------------------------------------------------------- (B) 사람이 읽는 궤적 검증
master = pd.read_csv(PROC / "station_master.csv", usecols=["station_id", "name", "station_type"])
nm = master.set_index("station_id")["name"].to_dict()
tp = master.set_index("station_id")["station_type"].to_dict()


def label(st):
    return f"{st}({nm.get(st, '?')[:16]}{'·관제' if tp.get(st) == 'CONTROL' else ''})"


target = con.execute(f"""
    SELECT bike_id FROM mv WHERE move_type='RELOCATION' AND confidence='high'
    GROUP BY 1 ORDER BY count(*) DESC LIMIT 2""").df()["bike_id"].tolist()
target += con.execute("""
    SELECT bike_id FROM mv WHERE move_type='CONTROL_OUT' GROUP BY 1
    ORDER BY count(*) DESC LIMIT 1""").df()["bike_id"].tolist()

for b in target:
    seq = con.execute(
        f"SELECT * FROM tr WHERE bike_id=? ORDER BY {', '.join(SORT_KEYS)} LIMIT 12", [b]).df()
    mvs = con.execute(
        "SELECT gap_start, gap_end, from_st, to_st, move_type, confidence "
        "FROM mv WHERE bike_id=? ORDER BY gap_start", [b]).df()
    print("\n" + "=" * 70)
    print(f"(B) 자전거 {b} — 앞 12개 통행 (재배치 총 {len(mvs)}건)")
    prev = None
    for _, r in seq.iterrows():
        if prev is not None and prev != r.rent_st:
            hit = mvs[(mvs.to_st == r.rent_st) & (mvs.from_st == prev)]
            tag = f"  ⇐ 재배치 탐지 {hit.iloc[0].move_type}/{hit.iloc[0].confidence}" if len(hit) else "  ⇐ ❌ 미탐지"
            print(f"      ····· {label(prev)} → {label(r.rent_st)}{tag}")
        print(f"  {r.rent_dt:%m-%d %H:%M} {label(r.rent_st):<32s} → "
              f"{r.ret_dt:%m-%d %H:%M} {label(r.ret_st):<32s} {r.dur_min:>4d}분 {r.dist_km:>5.1f}km")
        prev = r.ret_st

# ------------------------------------------------------------- (C) 패널 재계산 검증
print("\n" + "=" * 70)
print("(C) 패널 집계 재검증 — 특정 대여소·날짜를 원본 통행에서 직접 세어 대조")
st, day = con.execute("""
    SELECT station_id, ts::DATE d FROM read_parquet('%s')
    GROUP BY 1,2 ORDER BY sum(rentals) DESC LIMIT 1""" % (PROC / "station_hourly.parquet")).fetchone()
panel = con.execute(f"""
    SELECT sum(rentals) r, sum(returns) b FROM read_parquet('{PROC/'station_hourly.parquet'}')
    WHERE station_id=? AND ts::DATE=?""", [st, day]).fetchone()
direct = con.execute("""
    SELECT (SELECT count(*) FROM tr WHERE rent_st=? AND rent_dt::DATE=?),
           (SELECT count(*) FROM tr WHERE ret_st=?  AND ret_dt::DATE=?)""",
    [st, day, st, day]).fetchone()
print(f"    대상: {label(st)}  {day}")
print(f"    패널 집계  대여 {panel[0]:,} / 반납 {panel[1]:,}")
print(f"    원본 직접  대여 {direct[0]:,} / 반납 {direct[1]:,}")
print(f"    일치: {'예 ✅' if tuple(panel) == tuple(direct) else '아니오 ❌'}")
