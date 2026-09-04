#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
simulate.py — 합성 실측 데이터 생성기 (키 대기 중 검증 스크립트를 미리 돌려보기 위한 것)

    python3 realtime/simulate.py --days 14 --stations 60

★ 이것은 **코드 경로 점검용**이지 과학적 검증이 아니다.
   합성 데이터는 이력 기반 추정 재고에서 만들어지므로, 이걸로 상관을 재면
   "추정을 추정으로 검증"하는 순환 논증이 된다. 실제 검증은 반드시 실측 키로 해야 한다.
   그래서 파일 이름과 로그에 SAMPLE 을 박아 실측과 절대 섞이지 않게 한다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage as st

SAMPLE_DB = ROOT / "realtime" / "storage" / "tashu_status_SAMPLE.sqlite"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--stations", type=int, default=60)
    ap.add_argument("--interval-min", type=int, default=30)
    ap.add_argument("--db", default=str(SAMPLE_DB))
    a = ap.parse_args()

    P = ROOT / "data" / "processed"
    con = duckdb.connect(); con.execute("PRAGMA memory_limit='2GB'")
    con.execute(f"CREATE VIEW h AS SELECT * FROM read_parquet('{P/'station_hourly.parquet'}')")
    met = pd.read_csv(P / "station_metrics.csv")
    targets = pd.concat([met.nlargest(a.stations // 2, "net_out_per_day"),
                         met.nsmallest(a.stations // 2, "net_out_per_day")])["station_id"].tolist()
    ids = ",".join(f"'{x}'" for x in targets)

    end = pd.Timestamp("2026-03-31 23:00:00")
    start = end - timedelta(days=a.days)
    df = con.execute(f"""
        SELECT station_id, ts, sum(net_flow) OVER (PARTITION BY station_id ORDER BY ts) AS inv
        FROM h WHERE station_id IN ({ids}) ORDER BY station_id, ts""").df()
    df["ts"] = pd.to_datetime(df["ts"])
    df = df[(df.ts >= start) & (df.ts <= end)]

    rng = np.random.default_rng(42)
    rows, snapshots = [], 0
    for sid, g in df.groupby("station_id"):
        g = g.set_index("ts")["inv"].resample(f"{a.interval_min}min").ffill().dropna()
        if g.empty:
            continue
        # 절대 재고는 모르므로 임의 오프셋을 부여하고 0 에서 잘라 '재고 소진'을 만든다
        base = rng.integers(3, 12)
        vals = np.clip(g.values - g.values.mean() + base + rng.normal(0, 1.0, len(g)), 0, 25)
        for t, v in zip(g.index, vals.round().astype(int)):
            rows.append((t.strftime("%Y-%m-%dT%H:%M:%S+09:00"), sid, f"SAMPLE {sid}", int(v)))
    # 마운트/동기화 폴더는 SQLite 쓰기를 못 받는 경우가 있으므로 임시 디스크에서 만든 뒤 복사한다
    import shutil, tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / "sample.sqlite"
        con_db = st.connect(tmp)
        con_db.executemany(
            "INSERT OR REPLACE INTO station_status "
            "(collected_at, station_id, station_name, bikes_available) VALUES (?,?,?,?)", rows)
        con_db.commit()
        s = st.summary(con_db)
        con_db.close()
        Path(a.db).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tmp, a.db)
    print(f"[SAMPLE] {a.db}")
    print(f"  {s['rows']:,}행 / 스냅샷 {s['snapshots']:,}개 / 대여소 {s['stations']}개")
    print(f"  {s['first']} ~ {s['last']}")
    print("  ⚠ 합성 데이터입니다. 코드 경로 점검 전용이며 검증 결과로 인용하면 안 됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
