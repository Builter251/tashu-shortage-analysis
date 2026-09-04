#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
06_export_dashboard.py — 대시보드용 집계 데이터 추출 (dashboard_data.json)

대시보드는 자체 완결(self-contained) HTML 이어야 하므로, 618만 행 패널을 그대로 넣을 수 없다.
탐색에 필요한 최소 집계만 뽑는다.

  months          분석 대상 월 목록 (19개)
  daily           일별 총 대여량 (572일) — 결측일은 null
  stations        대여소별 속성·전체기간 지표
  stationMonthly  대여소 × 월 대여/반납 (기간 필터가 지표를 실제로 바꾸게 하려면 필요)
  profile         대여소별 평일 시간대(24) 평균 대여/반납
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
OUT = PROC / "dashboard_data.json"

con = duckdb.connect(); con.execute("PRAGMA memory_limit='2GB'")
con.execute(f"CREATE VIEW h AS SELECT * FROM read_parquet('{PROC/'station_hourly.parquet'}')")
master = pd.read_csv(PROC / "station_master.csv"); con.register("m", master)
met = pd.read_csv(PROC / "station_metrics.csv")
REAL = "m.station_type <> 'CONTROL'"

# ── 일별 ───────────────────────────────────────────────────────────────────
daily = con.execute(f"""SELECT h.ts::DATE AS d, sum(h.rentals_real) AS n
    FROM h JOIN m ON h.station_id=m.station_id WHERE {REAL} GROUP BY 1 ORDER BY 1""").df()
daily["d"] = pd.to_datetime(daily["d"])
idx = pd.date_range(daily.d.min(), daily.d.max(), freq="D")
s = daily.set_index("d")["n"].reindex(idx)
s = s.mask(s.fillna(0) == 0)            # 결측일(2025-12 등)은 0이 아니라 null
obs_days = s.dropna().index

# ── 대여소 × 월 ────────────────────────────────────────────────────────────
sm = con.execute(f"""
    SELECT h.station_id AS sid, strftime(h.ts, '%Y-%m') AS ym,
           sum(h.rentals) AS r, sum(h."returns") AS b
    FROM h JOIN m ON h.station_id=m.station_id
    WHERE m.station_type='ACTIVE' AND h.ts::DATE IN (SELECT unnest(?))
    GROUP BY 1,2""", [[d.date() for d in obs_days]]).df()
months = sorted(sm.ym.unique())
mi = {m_: i for i, m_ in enumerate(months)}
days_per_month = pd.Series(obs_days).dt.strftime("%Y-%m").value_counts().to_dict()

# ── 요일 × 시간대 프로파일 ────────────────────────────────────────────────
# 평일 24시간만 뽑던 것을 요일×시간(7×24)으로 확장한다.
# 이 하나로 (1) 전체·대여소별 평일/주말 프로파일 (2) 시간대×요일 히트맵을 모두 만들 수 있다.
pf = con.execute(f"""
    SELECT h.station_id AS sid, (dayofweek(h.ts) + 6) % 7 AS dw, hour(h.ts) AS hh,
           sum(h.rentals_real) AS r, sum(h."returns_real") AS b
    FROM h JOIN m ON h.station_id=m.station_id
    WHERE m.station_type='ACTIVE' AND h.ts::DATE IN (SELECT unnest(?))
    GROUP BY 1,2,3""", [[d.date() for d in obs_days]]).df()
dow_obs = pd.Series(obs_days).dt.dayofweek
days_per_dow = [int((dow_obs == i).sum()) for i in range(7)]   # 0=월
n_wd = sum(days_per_dow[:5])

stations, monthly, profile = [], {}, {}
for _, r in met.iterrows():
    sid = r.station_id
    stations.append({
        "id": sid, "n": r["name"], "g": r.gu, "d": r.dong,
        "la": round(float(r.lat), 5), "lo": round(float(r.lon), 5),
        "cpi": None if pd.isna(r.CPI) else round(float(r.CPI), 3),
        "wr": None if pd.isna(r.WR) else round(float(r.WR), 3),
        "t": r.use_type,
        "ri": int(r.reloc_in), "ro": int(r.reloc_out),
    })
    g = sm[sm.sid == sid]
    ra, ba = [0] * len(months), [0] * len(months)
    for _, x in g.iterrows():
        ra[mi[x.ym]] = int(x.r); ba[mi[x.ym]] = int(x.b)
    monthly[sid] = [ra, ba]
    # 요일×시간 원자료(합계)를 정수로 저장한다. 평균은 화면에서 관측일수로 나눈다.
    g2 = pf[pf.sid == sid]
    rr = [0] * 168; bb = [0] * 168
    for _, x in g2.iterrows():
        k = int(x.dw) * 24 + int(x.hh); rr[k] = int(x.r); bb[k] = int(x.b)
    profile[sid] = [rr, bb]

data = {
    "meta": {
        "period": [str(idx.min().date()), str(idx.max().date())],
        "obsDays": len(obs_days), "missingDays": int(s.isna().sum()),
        "weekdayDays": n_wd, "daysPerDow": days_per_dow, "totalTrips": 9_116_471,
        "generated": pd.Timestamp.now().strftime("%Y-%m-%d"),
    },
    "months": months,
    "daysPerMonth": [int(days_per_month.get(m_, 0)) for m_ in months],
    "dailyDates": [d.strftime("%Y-%m-%d") for d in idx],
    "daily": [None if np.isnan(v) else int(v) for v in s.values],
    "stations": stations, "monthly": monthly, "profile": profile,
}
OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"저장 {OUT}  ({OUT.stat().st_size/1e6:.2f} MB)")
print(f"  월 {len(months)}개 / 대여소 {len(stations)}개 / 일별 {len(idx)}일(관측 {len(obs_days)})")
