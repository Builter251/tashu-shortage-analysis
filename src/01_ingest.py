#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
01_ingest.py — 타슈 대여이력 원본 CSV(CP949) → 정제 Parquet + 대여소 마스터 원자료

무엇을 하는가
  1) 월별 원본 CSV를 청크 단위로 스트리밍 읽는다 (2.3GB를 한 번에 올리지 않는다)
  2) PLAN.md 4장의 정제 규칙을 '플래그 컬럼'으로 부여한다 — 행을 지우지 않는다
  3) 월별 trips_YYYYMM.parquet 로 저장한다 (컬럼 최소화 + 압축)
  4) 대여소별 활동 원자료를 누적한다 (first_seen / last_seen / active_days / 이벤트 수)
  5) 정제 통계표(월별 행 수, 플래그별 건수·비율)를 CSV로 남긴다

주의 (데이터 함정)
  * 인코딩은 CP949. UTF-8로 읽으면 전부 깨진다.
  * 'X좌표'가 위도(36.xx), 'Y좌표'가 경도(127.xx)다. 통상 표기와 반대이므로
    여기서 lat / lon 으로 이름을 바로잡아 저장한다.
  * 대여소명에 쉼표가 들어간 행이 있다(예: "자양동 우송대,솔펫동물병원").
    반드시 CSV 파서로 읽어야 한다 (split(',') 금지).

실행
  python3 src/01_ingest.py
"""
from __future__ import annotations

import codecs
import re
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# 경로 설정
# ----------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "대전교통공사_대전시 공영자전거 타슈 대여이력 정보_20260331"
OUT_DIR = ROOT / "data" / "processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CHUNKSIZE = 400_000
ENCODING_CANDIDATES = ("utf-8", "cp949")   # 시도 순서 (BOM은 별도 처리)

# ----------------------------------------------------------------------------
# 정제 기준값 (PLAN.md 4장과 1:1 대응 — 여기만 고치면 전체가 따라온다)
# ----------------------------------------------------------------------------
MAX_DURATION_MIN = 24 * 60      # 24시간 초과 = 분실/미반납 의심
MAX_DISTANCE_KM = 40.0          # 공영자전거 단거리 특성과 불일치
CANCEL_DURATION_MIN = 1         # 1분 이하 AND
CANCEL_DISTANCE_KM = 0.0        # 0km  → 대여 취소로 간주
CONTROL_KEYWORD = "관제"        # 타슈관제센터 = 물리적 대여소 아님

RENAME = {
    "자전거번호": "bike_id",
    "대여일시": "rent_dt",
    "대여_대여소ID": "rent_st",
    "대여_대여소명": "rent_name",
    "대여_X좌표": "rent_lat",   # X좌표가 위도다
    "대여_Y좌표": "rent_lon",   # Y좌표가 경도다
    "대여_구": "rent_gu",
    "대여_동": "rent_dong",
    "대여_대여소주소": "rent_addr",
    "반납일시": "ret_dt",
    "반납_대여소ID": "ret_st",
    "반납_대여소명": "ret_name",
    "반납_X좌표": "ret_lat",
    "반납_Y좌표": "ret_lon",
    "반납_구": "ret_gu",
    "반납_동": "ret_dong",
    "반납_대여소주소": "ret_addr",
    "이용시간(분)": "dur_min",
    "이용거리(km)": "dist_km",
}

STR_COLS = [
    "자전거번호", "대여_대여소ID", "대여_대여소명", "대여_구", "대여_동", "대여_대여소주소",
    "반납_대여소ID", "반납_대여소명", "반납_구", "반납_동", "반납_대여소주소",
]

TRIP_COLS = [
    "bike_id", "rent_dt", "rent_st", "ret_dt", "ret_st",
    "dur_min", "dist_km",
    "f_ctrl_rent", "f_ctrl_ret", "f_cancel", "f_dur_out", "f_dist_out", "f_time_bad",
]

# 통행을 유일하게 식별하는 키 (중복 배포 파일 탐지에 사용)
TRIP_KEY = ["bike_id", "rent_dt", "ret_dt", "rent_st", "ret_st"]

FLAGS = ["f_ctrl_rent", "f_ctrl_ret", "f_cancel", "f_dur_out", "f_dist_out", "f_time_bad", "f_dup"]


def detect_encoding(path: Path, nbytes: int = 1_000_000) -> str:
    """파일 인코딩 자동 판별.

    이 데이터셋은 **월별로 인코딩이 다르다.** (2026-03 배포본 기준 cp949 8개 / utf-8-sig 12개)
    안내문에는 CP949라고만 적혀 있어서 한 가지로 고정하면 중간에 터진다.
    증분 디코더를 쓰는 이유: 앞부분만 잘라 읽으면 멀티바이트 문자가 중간에서
    끊겨 멀쩡한 파일도 디코드 실패로 오판할 수 있다.
    """
    head = path.open("rb").read(nbytes)
    if head[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    for enc in ENCODING_CANDIDATES:
        try:
            codecs.getincrementaldecoder(enc)().decode(head, False)
            return enc
        except UnicodeDecodeError:
            continue
    raise ValueError(f"인코딩을 판별하지 못했습니다: {path.name}")


def month_key(filename: str) -> str:
    """'...(25년09월).csv' → '202509'

    macOS 파일명은 한글이 NFD(자모 분리) 형태로 저장된다. 그대로 정규식을 걸면
    '년'/'월' 같은 완성형 글자와 매칭되지 않으므로 반드시 NFC로 정규화한다.
    """
    filename = unicodedata.normalize("NFC", filename)
    m = re.search(r"\((\d{2})년(\d{2})월\)", filename)
    if not m:
        raise ValueError(f"파일명에서 연월을 찾지 못했습니다: {filename}")
    return f"20{m.group(1)}{m.group(2)}"


DT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


def parse_datetime(col: pd.Series) -> pd.Series:
    """일시 컬럼 파싱.

    월별로 포맷이 다르다. 대부분은 '2025-09-01 00:00:05'(초 포함)이지만
    2025년 3월 파일만 '2025-03-04 11:02'(초 없음)이다. 포맷을 하나로 고정하면
    그 달 전체가 조용히 NaT가 되어 **한 달치가 통째로 사라진다.**
    (실제로 1차 적재에서 이 사고가 났고, 날짜 커버리지 점검으로 잡아냈다.)
    """
    out = pd.to_datetime(col, format=DT_FORMATS[0], errors="coerce")
    for fmt in DT_FORMATS[1:]:
        miss = out.isna() & col.notna()
        if not miss.any():
            return out
        out.loc[miss] = pd.to_datetime(col[miss], format=fmt, errors="coerce")
    miss = out.isna() & col.notna()
    if miss.any():   # 그래도 남으면 마지막으로 자동 추론
        out.loc[miss] = pd.to_datetime(col[miss], errors="coerce", format="mixed")
    return out


def clean_chunk(df: pd.DataFrame) -> pd.DataFrame:
    """원본 청크 → 컬럼 정리 + 정제 플래그 부여. 행은 지우지 않는다."""
    df = df.rename(columns=RENAME)

    df["rent_dt"] = parse_datetime(df["rent_dt"])
    df["ret_dt"] = parse_datetime(df["ret_dt"])

    for c in ("dur_min", "dist_km", "rent_lat", "rent_lon", "ret_lat", "ret_lon"):
        df[c] = pd.to_numeric(df[c], errors="coerce")

    rent_name = df["rent_name"].fillna("")
    ret_name = df["ret_name"].fillna("")

    df["f_ctrl_rent"] = rent_name.str.contains(CONTROL_KEYWORD, regex=False)
    df["f_ctrl_ret"] = ret_name.str.contains(CONTROL_KEYWORD, regex=False)
    df["f_cancel"] = (df["dur_min"] <= CANCEL_DURATION_MIN) & (df["dist_km"] <= CANCEL_DISTANCE_KM)
    df["f_dur_out"] = df["dur_min"] > MAX_DURATION_MIN
    df["f_dist_out"] = df["dist_km"] > MAX_DISTANCE_KM
    df["f_time_bad"] = (
        df["rent_dt"].isna() | df["ret_dt"].isna() | (df["ret_dt"] < df["rent_dt"])
    )

    for c in FLAGS:          # f_dup 은 월 단위로 뒤에서 계산하므로 여기서는 건너뛴다
        if c in df.columns:
            df[c] = df[c].fillna(False).astype(bool)

    df["dur_min"] = df["dur_min"].fillna(-1).astype("int32")
    df["dist_km"] = df["dist_km"].astype("float32")
    return df


def station_frames(df: pd.DataFrame):
    """대여소 마스터용 (대여소, 날짜) 이벤트 집계 + 대여소 속성 스냅샷."""
    rent = pd.DataFrame({
        "station_id": df["rent_st"],
        "date": df["rent_dt"].dt.normalize(),
    }).dropna()
    ret = pd.DataFrame({
        "station_id": df["ret_st"],
        "date": df["ret_dt"].dt.normalize(),
    }).dropna()

    rent_cnt = rent.groupby(["station_id", "date"], observed=True).size().rename("rent_events")
    ret_cnt = ret.groupby(["station_id", "date"], observed=True).size().rename("ret_events")

    attrs = pd.concat([
        df[["rent_st", "rent_name", "rent_lat", "rent_lon", "rent_gu", "rent_dong", "rent_addr"]]
          .rename(columns=lambda c: c.replace("rent_", "")),
        df[["ret_st", "ret_name", "ret_lat", "ret_lon", "ret_gu", "ret_dong", "ret_addr"]]
          .rename(columns=lambda c: c.replace("ret_", "")),
    ], ignore_index=True).rename(columns={"st": "station_id"})
    attrs = attrs.dropna(subset=["station_id"]).drop_duplicates(subset=["station_id"], keep="last")
    return rent_cnt, ret_cnt, attrs


def main() -> int:
    files = sorted(RAW_DIR.glob("*.csv"), key=lambda p: month_key(p.name))
    if not files:
        print(f"[에러] 원본 CSV를 찾지 못했습니다: {RAW_DIR}", file=sys.stderr)
        return 1

    print(f"[시작] 대상 파일 {len(files)}개  ({RAW_DIR})", flush=True)

    stats_rows = []
    daily_parts = []          # (station_id, date) 이벤트 수 누적
    attr_parts = []           # 대여소 속성 스냅샷 누적
    t_all = time.time()

    for i, path in enumerate(files, 1):
        ym = month_key(path.name)
        t0 = time.time()
        chunks, n_rows = [], 0
        flag_counts = {f: 0 for f in FLAGS}

        enc = detect_encoding(path)
        reader = pd.read_csv(
            path, encoding=enc, chunksize=CHUNKSIZE,
            dtype={c: "object" for c in STR_COLS},
        )
        for chunk in reader:
            missing = set(RENAME) - set(chunk.columns)
            if missing:
                raise ValueError(f"{path.name}: 예상 컬럼 누락 {sorted(missing)}")
            c = clean_chunk(chunk)
            n_rows += len(c)
            for f in FLAGS:
                if f in c:
                    flag_counts[f] += int(c[f].sum())

            r_cnt, t_cnt, attrs = station_frames(c)
            daily_parts.append(pd.concat([r_cnt, t_cnt], axis=1).fillna(0).astype("int32"))
            attr_parts.append(attrs)

            chunks.append(c[[col for col in TRIP_COLS if col in c]])

        out = pd.concat(chunks, ignore_index=True)

        # 파일 내 완전 중복 통행 표시 (지우지 않고 플래그만)
        out["f_dup"] = out.duplicated(subset=TRIP_KEY, keep="first")
        flag_counts["f_dup"] = int(out["f_dup"].sum())

        # 파일명이 말하는 달과 실제 데이터의 달이 일치하는가
        dm = out["rent_dt"].dt.strftime("%Y%m")
        data_months = dm.dropna().value_counts()
        main_month = data_months.index[0] if len(data_months) else None
        mismatch = int((dm != ym).sum())

        out["src_month"] = ym          # 파일명 기준 (출처 추적용)
        out_path = OUT_DIR / f"trips_{ym}.parquet"
        out.to_parquet(out_path, index=False, compression="zstd")

        row = {"month": ym, "file": unicodedata.normalize("NFC", path.name),
               "encoding": enc, "rows": n_rows,
               "data_month_main": main_month,
               "data_days": int(out["rent_dt"].dt.normalize().nunique()),
               "rows_off_month": mismatch,
               "mb_out": round(out_path.stat().st_size / 1e6, 1),
               "sec": round(time.time() - t0, 1)}
        row.update(flag_counts)
        stats_rows.append(row)
        print(f"  [{i:2d}/{len(files)}] {ym}  rows={n_rows:>9,}  "
              f"out={row['mb_out']:>6.1f}MB  enc={enc:<10s} {row['sec']:>6.1f}s", flush=True)

        del chunks, out

    # ---- 정제 통계표 -------------------------------------------------------
    stats = pd.DataFrame(stats_rows)
    for f in FLAGS:
        stats[f + "_pct"] = (stats[f] / stats["rows"] * 100).round(2)
    stats.to_csv(OUT_DIR / "ingest_stats.csv", index=False, encoding="utf-8-sig")

    # ---- 대여소 활동 원자료 ------------------------------------------------
    daily = (pd.concat(daily_parts)
               .groupby(level=[0, 1], observed=True).sum()
               .reset_index())
    daily.to_parquet(OUT_DIR / "station_daily_events.parquet", index=False, compression="zstd")

    agg = daily.groupby("station_id").agg(
        first_seen=("date", "min"),
        last_seen=("date", "max"),
        active_days=("date", "nunique"),
        rent_events=("rent_events", "sum"),
        ret_events=("ret_events", "sum"),
    )
    agg["span_days"] = (agg["last_seen"] - agg["first_seen"]).dt.days + 1
    agg["activity_ratio"] = (agg["active_days"] / agg["span_days"]).round(4)
    agg["total_events"] = agg["rent_events"] + agg["ret_events"]

    # 컬럼별로 '가장 최근에 관측된 결측 아닌 값'을 취한다.
    # (GroupBy.last() 는 NaN을 건너뛰므로, 최신 월에 값이 비어 있어도 과거 값이 살아남는다)
    attrs = (pd.concat(attr_parts, ignore_index=True)
               .groupby("station_id", sort=False)
               .last())
    master = agg.join(attrs, how="left").reset_index()
    master.to_csv(OUT_DIR / "station_master_raw.csv", index=False, encoding="utf-8-sig")

    total_rows = int(stats["rows"].sum())
    print(f"\n[완료] 총 {total_rows:,}행 / 대여소 {len(master):,}개 / "
          f"{time.time() - t_all:.1f}초", flush=True)
    print(f"  → {OUT_DIR}/trips_*.parquet")
    print(f"  → {OUT_DIR}/ingest_stats.csv")
    print(f"  → {OUT_DIR}/station_daily_events.parquet")
    print(f"  → {OUT_DIR}/station_master_raw.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
