#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
02_build_panel.py — 통행 데이터 → 대여소×시간 패널 + 재배치 이벤트 + 대여소 마스터

이 스크립트가 프로젝트의 핵심이다. 하는 일은 네 가지다.

  (1) 전역 중복 제거
      "25년12월" 파일은 25년 1월 데이터의 완전 중복이다(일치율 100%).
      통행키(자전거번호+대여일시+반납일시+대여/반납 대여소) 기준으로 전역 dedup 하면
      해당 파일이 통째로 사라진다. → 실질 19개월.

  (2) 자전거 궤적 재구성 → 재배치(rebalancing) 이벤트 탐지
      자전거 b를 시간순으로 보면   ... → A에 반납  ...  B에서 대여 → ...
      A ≠ B 라면 그 사이에 **사람이 옮긴 것**이다. 대여이력만으로 트럭의 움직임을
      역추적하는 유일한 방법이며, 이 프로젝트의 가장 강력한 무기다.

  (3) 대여소 × 1시간 패널
      대여수 / 반납수 / 재배치 유입·유출 / 재고변화를 시간 단위로 집계한다.
      재고 변화 = 반납 - 대여 + 재배치유입 - 재배치유출   (자전거 보존 법칙)

  (4) 대여소 유형(station_type) 6분류
      CONTROL / TEMPORARY / NEW / CLOSED / LOW_USE / ACTIVE

무거운 정렬·조인은 DuckDB로 처리한다 (메모리 3GB 환경에서 900만 행을 안전하게 다루기 위해).

실행:  python3 src/02_build_panel.py
"""
from __future__ import annotations

import time
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
STATION_CSV = ROOT / "data" / "대전광역시_공영자전거(타슈) 위치 현황_20260624.csv"

# 대여소 유형 판정 기준 (PLAN.md 4-1)
NEW_WINDOW_DAYS = 90        # 데이터 종료 90일 이내에 처음 등장 → 신설
CLOSED_WINDOW_DAYS = 90     # 데이터 종료 90일 이전에 마지막 등장 → 폐쇄
TEMP_SPAN_DAYS = 60         # 존재 기간이 이보다 짧으면 임시 후보
TEMP_TAIL_DAYS = 7          # 단, 관측이 데이터 끝에 붙어 있으면 임시가 아니라 신설
LOW_USE_RATIO = 0.5
LOW_USE_EVENTS = 500
TEMP_NAME_PATTERN = "축제|행사|임시|팝업|이벤트"

# 관제센터 대여소 ID (물리적 대여소가 아닌 가상 노드).
# 이름에 '관제'가 들어간 대여소를 1단계 마스터에서 확인해 고정했다.
CONTROL_IDS = ("ST0001", "ST1220")
# 궤적 단절 구간이 이보다 짧으면 재배치 시각 추정을 신뢰(high)로 본다
RELOC_HIGH_CONF_MIN = 24 * 60

# 데이터 결측 구간을 건너뛴 '재배치'는 신뢰할 수 없으므로 표시해 둔다
MISSING_WINDOWS = [
    ("2025-02-26", "2025-03-04"),   # 시스템 장애 (2/26~28, 3/1~3)
    ("2025-12-01", "2026-01-01"),   # 12월 데이터 자체가 없음
]


def log(msg, t0=None):
    tail = f"  ({time.time() - t0:.1f}s)" if t0 else ""
    print(f"[02] {msg}{tail}", flush=True)


def main() -> int:
    t_all = time.time()
    con = duckdb.connect()
    con.execute("PRAGMA memory_limit='2GB'")
    con.execute("PRAGMA threads=4")
    con.execute("PRAGMA temp_directory='/tmp/duckdb_tmp'")

    # 주의: 산출물 trips_dedup.parquet 이 같은 폴더에 있으므로 글롭을 좁힌다.
    #       trips_*.parquet 로 잡으면 자기 출력까지 읽어 스키마 충돌이 난다.
    src = str(PROC / "trips_20*.parquet")

    # ---------------------------------------------------------------- 1) dedup
    t0 = time.time()
    con.execute(f"CREATE VIEW raw AS SELECT * FROM read_parquet('{src}')")
    n_raw = con.execute("SELECT count(*) FROM raw").fetchone()[0]
    con.execute("""
        CREATE TABLE trips AS
        SELECT bike_id, rent_dt, rent_st, ret_dt, ret_st, dur_min, dist_km,
               f_ctrl_rent, f_ctrl_ret, f_cancel, src_month
        FROM (
            SELECT *, row_number() OVER (
                       PARTITION BY bike_id, rent_dt, ret_dt, rent_st, ret_st
                       ORDER BY src_month) AS rn
            FROM raw)
        WHERE rn = 1 AND NOT f_time_bad
    """)
    n_trips = con.execute("SELECT count(*) FROM trips").fetchone()[0]
    log(f"전역 중복 제거: {n_raw:,} → {n_trips:,}행 (제거 {n_raw - n_trips:,})", t0)

    kept = con.execute("""SELECT src_month, count(*) n FROM trips
                          GROUP BY 1 ORDER BY 1""").df().set_index("src_month")["n"]
    allm = con.execute("""SELECT src_month, count(*) n FROM raw
                          GROUP BY 1 ORDER BY 1""").df().set_index("src_month")["n"]
    for mkey in allm.index:
        lost = int(allm[mkey]) - int(kept.get(mkey, 0))
        if lost:
            log(f"    파일월 {mkey}: {lost:,}행 제거 (남은 {int(kept.get(mkey, 0)):,}행)")

    period = con.execute("SELECT min(rent_dt), max(rent_dt) FROM trips").fetchone()
    data_end = pd.Timestamp(period[1]).normalize()
    log(f"분석 기간: {period[0]} ~ {period[1]}")

    # ------------------------------------------------- 2) 궤적 → 재배치 이벤트
    t0 = time.time()
    cond = " OR ".join(
        f"(gap_start < TIMESTAMP '{b}' AND gap_end > TIMESTAMP '{a}')"
        for a, b in MISSING_WINDOWS)
    con.execute(f"""
        CREATE TABLE moves AS
        SELECT bike_id, from_st, to_st, gap_start, gap_end,
               datediff('minute', gap_start, gap_end) AS gap_min,
               -- 재배치 시각은 알 수 없다. 공백 구간의 중간점으로 둔다(민감도 분석 대상).
               gap_start + (gap_end - gap_start) / 2 AS reloc_ts,
               ({cond}) AS spans_gap,
               -- 관제센터는 물리적 대여소가 아니므로 물리적 재배치와 반드시 구분한다
               CASE WHEN from_st IN {CONTROL_IDS} AND to_st IN {CONTROL_IDS} THEN 'CONTROL_INTERNAL'
                    WHEN from_st IN {CONTROL_IDS} THEN 'CONTROL_OUT'
                    WHEN to_st   IN {CONTROL_IDS} THEN 'CONTROL_IN'
                    ELSE 'RELOCATION' END AS move_type,
               -- 공백이 길수록 '언제 옮겨졌는지'가 불확실하다
               CASE WHEN datediff('minute', gap_start, gap_end) <= {RELOC_HIGH_CONF_MIN}
                    THEN 'high' ELSE 'low' END AS confidence
        FROM (
            SELECT bike_id,
                   lag(ret_st) OVER w AS from_st,
                   lag(ret_dt) OVER w AS gap_start,
                   rent_st            AS to_st,
                   rent_dt            AS gap_end
            FROM trips
            -- 정렬 키에 반드시 tie-breaker를 넣는다.
            -- 2025년 3월 파일은 초 단위가 없어(분 해상도) 같은 자전거의 서로 다른 통행이
            -- 동일한 rent_dt 를 갖는 경우가 377건 있다. rent_dt 만으로 정렬하면 순서가
            -- 실행할 때마다 달라져 재배치 건수가 매번 바뀐다(= 재현성 붕괴).
            WINDOW w AS (PARTITION BY bike_id
                         ORDER BY rent_dt, ret_dt, rent_st, ret_st, dur_min))
        WHERE from_st IS NOT NULL AND from_st <> to_st
    """)
    n_moves, n_span = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE spans_gap) FROM moves").fetchone()
    n_links = con.execute("""SELECT count(*) FROM (
        SELECT lag(ret_st) OVER (PARTITION BY bike_id
                   ORDER BY rent_dt, ret_dt, rent_st, ret_st, dur_min) p
        FROM trips) WHERE p IS NOT NULL""").fetchone()[0]
    log(f"재배치 이벤트 {n_moves:,}건 / 연속 통행 연결 {n_links:,}건 "
        f"({n_moves / n_links * 100:.1f}%)", t0)
    log(f"    결측 구간을 건너뛴 건: {n_span:,}건 (해석 시 제외 권장)")
    for r in con.execute("""SELECT move_type, confidence, count(*) n FROM moves
                            GROUP BY 1,2 ORDER BY 1,2""").fetchall():
        log(f"    {r[0]:<17s} confidence={r[1]:<5s} {r[2]:>7,}건")

    # ------------------------------------------------- 3) 대여소 × 1시간 패널
    t0 = time.time()
    con.execute("""
        CREATE TABLE hourly AS
        WITH rent AS (
            SELECT rent_st AS station_id, date_trunc('hour', rent_dt) AS ts,
                   count(*) AS rentals,
                   count(*) FILTER (WHERE NOT f_cancel) AS rentals_real,
                   -- 이 대여의 '반납지'가 관제로 기록됨 → 어느 대여소가 +1을 받았어야 하는지 미상
                   count(*) FILTER (WHERE f_ctrl_ret) AS rentals_to_ctrl
            FROM trips GROUP BY 1, 2),
        ret AS (
            SELECT ret_st AS station_id, date_trunc('hour', ret_dt) AS ts,
                   count(*) AS returns,
                   count(*) FILTER (WHERE NOT f_cancel) AS returns_real,
                   -- 이 반납의 '대여지'가 관제로 기록됨 → 어느 대여소가 -1을 냈어야 하는지 미상
                   count(*) FILTER (WHERE f_ctrl_rent) AS returns_from_ctrl
            FROM trips GROUP BY 1, 2),
        rout AS (
            SELECT from_st AS station_id, date_trunc('hour', reloc_ts) AS ts,
                   count(*) AS reloc_out,
                   count(*) FILTER (WHERE move_type='RELOCATION') AS reloc_out_phys
            FROM moves GROUP BY 1, 2),
        rin AS (
            SELECT to_st AS station_id, date_trunc('hour', reloc_ts) AS ts,
                   count(*) AS reloc_in,
                   count(*) FILTER (WHERE move_type='RELOCATION') AS reloc_in_phys
            FROM moves GROUP BY 1, 2),
        keys AS (
            SELECT station_id, ts FROM rent
            UNION SELECT station_id, ts FROM ret
            UNION SELECT station_id, ts FROM rout
            UNION SELECT station_id, ts FROM rin)
        SELECT k.station_id, k.ts,
               coalesce(rent.rentals, 0)::SMALLINT      AS rentals,
               coalesce(ret.returns, 0)::SMALLINT       AS returns,
               coalesce(rent.rentals_real, 0)::SMALLINT AS rentals_real,
               coalesce(ret.returns_real, 0)::SMALLINT  AS returns_real,
               coalesce(rent.rentals_to_ctrl, 0)::SMALLINT   AS rentals_to_ctrl,
               coalesce(ret.returns_from_ctrl, 0)::SMALLINT  AS returns_from_ctrl,
               coalesce(rin.reloc_in, 0)::SMALLINT      AS reloc_in,
               coalesce(rout.reloc_out, 0)::SMALLINT    AS reloc_out,
               coalesce(rin.reloc_in_phys, 0)::SMALLINT   AS reloc_in_phys,
               coalesce(rout.reloc_out_phys, 0)::SMALLINT AS reloc_out_phys,
               (coalesce(rent.rentals, 0) - coalesce(ret.returns, 0))::SMALLINT AS net_out,
               (coalesce(ret.returns, 0) - coalesce(rent.rentals, 0)
                + coalesce(rin.reloc_in, 0) - coalesce(rout.reloc_out, 0))::SMALLINT AS net_flow
        FROM keys k
        LEFT JOIN rent USING (station_id, ts)
        LEFT JOIN ret  USING (station_id, ts)
        LEFT JOIN rout USING (station_id, ts)
        LEFT JOIN rin  USING (station_id, ts)
    """)
    n_hourly = con.execute("SELECT count(*) FROM hourly").fetchone()[0]
    log(f"시간 패널(희소): {n_hourly:,}행", t0)

    # ------------------------------------------------- 4) 대여소 마스터 + 유형
    t0 = time.time()
    daily = con.execute("""
        SELECT station_id,
               min(ts::DATE) AS first_seen,
               max(ts::DATE) AS last_seen,
               count(DISTINCT ts::DATE) AS active_days,
               sum(rentals)::BIGINT AS rent_events,
               sum(returns)::BIGINT AS ret_events
        FROM hourly GROUP BY 1
    """).df()

    attrs = pd.read_csv(PROC / "station_master_raw.csv",
                        usecols=["station_id", "name", "lat", "lon", "gu", "dong", "addr"])
    m = daily.merge(attrs, on="station_id", how="left")
    m["first_seen"] = pd.to_datetime(m["first_seen"])
    m["last_seen"] = pd.to_datetime(m["last_seen"])
    m["span_days"] = (m["last_seen"] - m["first_seen"]).dt.days + 1
    m["activity_ratio"] = (m["active_days"] / m["span_days"]).round(4)
    m["total_events"] = m["rent_events"] + m["ret_events"]

    cur = pd.read_csv(STATION_CSV, encoding="cp949")
    m["in_current_list"] = m["station_id"].isin(set(cur.iloc[:, 0].astype(str).str.strip()))

    name = m["name"].fillna("")
    is_ctrl = name.str.contains("관제")
    is_named_temp = name.str.contains(TEMP_NAME_PATTERN, regex=True)
    short = ((m["span_days"] < TEMP_SPAN_DAYS)
             & (m["last_seen"] < data_end - pd.Timedelta(days=TEMP_TAIL_DAYS)))
    is_new = m["first_seen"] > data_end - pd.Timedelta(days=NEW_WINDOW_DAYS)
    is_closed = m["last_seen"] < data_end - pd.Timedelta(days=CLOSED_WINDOW_DAYS)
    is_low = ((m["activity_ratio"] < LOW_USE_RATIO)
              & (m["total_events"] < LOW_USE_EVENTS)
              & (m["span_days"] >= TEMP_SPAN_DAYS))

    m["station_type"] = "ACTIVE"          # 순서가 중요하다 — 뒤가 앞을 덮어쓴다
    m.loc[is_low, "station_type"] = "LOW_USE"
    m.loc[is_closed, "station_type"] = "CLOSED"
    m.loc[is_new, "station_type"] = "NEW"
    m.loc[is_named_temp | short, "station_type"] = "TEMPORARY"
    m.loc[is_ctrl, "station_type"] = "CONTROL"

    m = m.sort_values("total_events", ascending=False)
    m.to_csv(PROC / "station_master.csv", index=False, encoding="utf-8-sig")
    log("대여소 마스터 %d개  %s" % (
        len(m), " / ".join(f"{k}={v}" for k, v in m["station_type"].value_counts().items())), t0)

    # ------------------------------------------------------------- 5) 저장
    t0 = time.time()
    con.execute(f"COPY hourly TO '{PROC / 'station_hourly.parquet'}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    con.execute(f"COPY moves  TO '{PROC / 'bike_moves.parquet'}'   (FORMAT PARQUET, COMPRESSION ZSTD)")
    con.execute(f"COPY trips  TO '{PROC / 'trips_dedup.parquet'}'  (FORMAT PARQUET, COMPRESSION ZSTD)")
    log("parquet 저장 완료", t0)

    # ------------------------------------------------- 6) 자전거 보존 검증
    c = con.execute("""SELECT sum(rentals) a, sum(returns) b, sum(reloc_in) c,
                              sum(reloc_out) d, sum(net_flow) e FROM hourly""").fetchone()
    log("── 자전거 보존 검증 ──")
    log(f"  대여 합계 {c[0]:,} = 반납 합계 {c[1]:,}      → {'OK' if c[0] == c[1] else '불일치'}")
    log(f"  재배치 유입 {c[2]:,} = 유출 {c[3]:,}        → {'OK' if c[2] == c[3] else '불일치'}")
    log(f"  전체 net_flow 합 {c[4]:,}                  → {'OK' if c[4] == 0 else '불일치'}")
    log(f"완료. 총 {time.time() - t_all:.1f}초")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
