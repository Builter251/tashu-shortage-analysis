#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_report.py — REPORT.md 에 인용된 수치를 산출물에서 다시 계산해 대조한다.

리포트에 손으로 옮겨 적은 숫자가 실제 산출과 어긋나는 것이 가장 흔한 사고다.
그래서 주요 주장마다 원천 파일에서 재계산해 자동 대조한다.
"""
from pathlib import Path
import duckdb, pandas as pd

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "data" / "processed"
con = duckdb.connect(); con.execute("PRAGMA memory_limit='2GB'")
con.execute(f"CREATE VIEW tr AS SELECT * FROM read_parquet('{P/'trips_dedup.parquet'}')")
con.execute(f"CREATE VIEW mv AS SELECT * FROM read_parquet('{P/'bike_moves.parquet'}')")
con.execute(f"CREATE VIEW h  AS SELECT * FROM read_parquet('{P/'station_hourly.parquet'}')")
master = pd.read_csv(P / "station_master.csv"); con.register("m", master)
met = pd.read_csv(P / "station_metrics.csv")
ing = pd.read_csv(P / "ingest_stats.csv")
fc = pd.read_csv(P / "forecast_metrics.csv")
curve = pd.read_csv(P / "inventory_response_curve.csv")

obs = con.execute("""SELECT h.ts::DATE d, sum(h.rentals_real) n FROM h JOIN m ON h.station_id=m.station_id
  WHERE m.station_type<>'CONTROL' GROUP BY 1 HAVING sum(h.rentals_real)>0""").df()

checks = []
def chk(label, claim, actual, tol=0.0):
    ok = abs(float(claim) - float(actual)) <= tol
    checks.append((ok, label, claim, round(float(actual), 4)))

chk("원본 총 행 수", 9_320_018, ing["rows"].sum())
chk("중복 제거 후 행 수", 9_116_471, con.execute("SELECT count(*) FROM tr").fetchone()[0])
chk("제거 행 수", 203_547, ing["rows"].sum() - con.execute("SELECT count(*) FROM tr").fetchone()[0])
chk("관측일 수", 572, len(obs))
chk("결측일 수", 37, 609 - len(obs))
chk("ACTIVE 대여소", 1332, (master.station_type == "ACTIVE").sum())
chk("전체 대여소", 1425, len(master))
chk("자전거 대수", 6776, con.execute("SELECT count(DISTINCT bike_id) FROM tr").fetchone()[0])
chk("재배치 총건", 89_660, con.execute("SELECT count(*) FROM mv").fetchone()[0])
chk("물리적 재배치(유효)", 56_041,
    con.execute("SELECT count(*) FROM mv WHERE move_type='RELOCATION' AND NOT spans_gap").fetchone()[0])
chk("하루 평균 물리적 재배치", 98.0,
    con.execute("SELECT count(*) FROM mv WHERE move_type='RELOCATION' AND NOT spans_gap").fetchone()[0] / 572, 0.05)
chk("대여량↔순유출 상관", -0.172, met.rentals.corr(met.net_out_per_day), 0.001)
chk("CPI↔순유출 상관", -0.06, met.CPI.corr(met.net_out_per_day), 0.005)
chk("순유출 1위 일평균", 2.76, met.net_out_per_day.max(), 0.005)
chk("순유출 1위 순유출", 1580, met.loc[met.net_out_per_day.idxmax(), "net_out"])
chk("재고반응 최저분위 비율", 0.821, curve.ratio.iloc[0], 0.001)
chk("재고반응 최고분위 비율", 1.360, curve.ratio.iloc[-1], 0.001)
chk("재고반응 최저/최고", 0.604, curve.ratio.iloc[0] / curve.ratio.iloc[-1], 0.001)
chk("B2 MAE", 0.591, fc.loc[2, "MAE"], 0.001)
chk("B2 RMSE", 1.160, fc.loc[2, "RMSE"], 0.001)
chk("유성구 순유출 합계", 11_652, met[met.gu == "유성구"].net_out.sum())
chk("출퇴근형 대여소 수", 144, (met.use_type == "출퇴근형").sum())
chk("순유출상위30 중 혼합/생활형", 23,
    (met.nlargest(30, "net_out_per_day").use_type == "혼합/생활형").sum())
# 순유입(순유출 음수) 쪽도 검증한다. 앞서 분모(관측일)가 604→572 로 바뀌었을 때
# 이 값이 리포트에 옛 수치(2.08)로 남아 있었다. 같은 사고를 막기 위해 고정한다.
top_in = met.nsmallest(1, "net_out_per_day").iloc[0]
chk("순유입 1위 일평균", 2.19, -top_in.net_out_per_day, 0.005)
chk("순유입 1위 누적", 1253, -top_in.net_out)
chk("순유출 2위 일평균", 2.22, met.nlargest(2, "net_out_per_day").net_out_per_day.iloc[1], 0.005)
chk("목동 재배치 순유입", 1160, met.loc[met.net_out_per_day.idxmax(), "reloc_net_in"])

print("=" * 76)
print(f"{'':2} {'항목':<28}{'리포트':>14}{'재계산':>14}")
print("-" * 76)
for ok, label, claim, actual in checks:
    print(f"{'OK' if ok else '!!':2} {label:<28}{claim:>14}{actual:>14}")
bad = [c for c in checks if not c[0]]
print("=" * 76)
print(f"검증 {len(checks)}건 중 불일치 {len(bad)}건" + ("" if not bad else " ← 리포트 수정 필요"))
raise SystemExit(1 if bad else 0)
