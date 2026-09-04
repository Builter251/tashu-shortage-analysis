#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
05_validate_with_realtime.py — 실측(OpenAPI) 데이터로 이력 기반 추정을 검증한다

이 스크립트는 **선택 사항**이다. 수집 데이터가 없으면 무엇을 해야 하는지 안내하고 정상 종료한다.
본 분석(01~04)은 이 파일 없이도 완결된다. 연결 지점은 오직 `realtime/storage/*.sqlite` 하나다.

    python3 src/05_validate_with_realtime.py                       # 실측 DB 사용
    python3 src/05_validate_with_realtime.py --sample              # 합성 데이터로 코드 경로 점검

검증 항목
  ① 대여소 ID 매핑    API 의 id 가 이력의 ST#### 와 같은 체계인가
  ② 변화 패턴 상관    추정 Δ재고 vs 실측 Δ재고 (대여소별 상관계수 분포)
  ③ 재고 소진 실재    순유출 상위 대여소가 실제로 0대인 시간이 더 잦은가
  ④ 관제센터 정체     관제 노드에 실제 자전거가 거치돼 있는가 → 가설 A/B 판별
  ⑤ 재고 오프셋       실측 평균 − 추정 상대재고 평균 = 대여소별 오프셋 확정
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
IMG = ROOT / "images"
REPORTS = ROOT / "reports"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "realtime"))
import viz_style as vs                        # noqa: E402
import storage as st                          # noqa: E402

REAL_DB = ROOT / "realtime" / "storage" / "tashu_status.sqlite"
SAMPLE_DB = ROOT / "realtime" / "storage" / "tashu_status_SAMPLE.sqlite"
MIN_SNAPSHOTS = 200          # 이보다 적으면 검증이 무의미 (5분 간격 ≈ 17시간)
RECOMMENDED_DAYS = 14


def guide(db: Path) -> int:
    print("=" * 74)
    print("실측 검증을 아직 할 수 없습니다 — 수집된 데이터가 없습니다.")
    print("=" * 74)
    print(f"  찾은 경로: {db}")
    print()
    print("  지금 할 일")
    print("   1. 타슈 앱 → 사이드메뉴 → 자전거정보 > OpenAPI 에서 키 신청")
    print("   2. 승인되면  realtime/.env  에  TASHU_API_KEY=발급키  저장")
    print("   3. 수집 시작:  python3 realtime/collect.py --loop")
    print("      (또는 cron 5분 간격 --once. 자세한 건 realtime/README.md)")
    print(f"   4. {RECOMMENDED_DAYS}일 축적 후 이 스크립트를 다시 실행")
    print()
    print("  키를 기다리는 동안 코드 경로만 점검하려면:")
    print("     python3 realtime/simulate.py --days 14   # 합성 데이터 생성")
    print("     python3 src/05_validate_with_realtime.py --sample")
    print()
    print("  본 분석(01~04)은 이 검증 없이도 완결되어 있습니다.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="합성 데이터로 코드 경로만 점검")
    ap.add_argument("--db", default=None)
    a = ap.parse_args()
    db = Path(a.db) if a.db else (SAMPLE_DB if a.sample else REAL_DB)
    if not db.exists():
        return guide(db)

    con_s = st.connect_readonly(db)
    obs = pd.read_sql_query(
        "SELECT collected_at, station_id, bikes_available FROM station_status", con_s)
    if len(obs) == 0 or obs.collected_at.nunique() < MIN_SNAPSHOTS:
        print(f"수집 스냅샷이 {obs.collected_at.nunique()}개뿐입니다 "
              f"(최소 {MIN_SNAPSHOTS}개 필요). 더 쌓은 뒤 다시 실행하세요.")
        return 0
    obs["ts"] = pd.to_datetime(obs.collected_at, format="ISO8601").dt.tz_localize(None)

    banner = ("⚠ 합성 데이터(SAMPLE)입니다 — 코드 경로 점검 전용이며 검증 결과로 인용할 수 없습니다."
              if a.sample or "SAMPLE" in db.name else "실측 데이터 기반 검증")
    print("=" * 74); print(banner); print("=" * 74)
    print(f"스냅샷 {obs.collected_at.nunique():,}개 / 대여소 {obs.station_id.nunique():,}개 / "
          f"{obs.ts.min():%Y-%m-%d %H:%M} ~ {obs.ts.max():%Y-%m-%d %H:%M}")

    con = duckdb.connect(); con.execute("PRAGMA memory_limit='2GB'")
    con.execute(f"CREATE VIEW h AS SELECT * FROM read_parquet('{PROC/'station_hourly.parquet'}')")
    master = pd.read_csv(PROC / "station_master.csv")
    met = pd.read_csv(PROC / "station_metrics.csv")
    lines = []

    # ── ① 대여소 ID 매핑 ────────────────────────────────────────────────
    hist_ids = set(master.station_id)
    api_ids = set(obs.station_id)
    hit = len(api_ids & hist_ids) / len(api_ids) * 100
    print(f"\n[① ID 매핑] API {len(api_ids):,}개 중 이력과 매칭 {len(api_ids & hist_ids):,}개 ({hit:.1f}%)")
    if hit < 90:
        print("   ⚠ 매칭률이 낮습니다. 대여소명·좌표 기반 fallback 매핑이 필요합니다.")
    lines.append(f"| ① ID 매핑 | API {len(api_ids):,}개 중 {hit:.1f}% 매칭 |")

    # ── ② 변화 패턴 상관 ────────────────────────────────────────────────
    ids = ",".join(f"'{x}'" for x in (api_ids & hist_ids))
    est = con.execute(f"""
        SELECT station_id, ts, sum(net_flow) OVER (PARTITION BY station_id ORDER BY ts) AS inv_est
        FROM h WHERE station_id IN ({ids})""").df()
    est["ts"] = pd.to_datetime(est.ts)
    act = (obs.set_index("ts").groupby("station_id")["bikes_available"]
           .resample("1h").mean().rename("inv_act").reset_index())
    mg = est.merge(act, on=["station_id", "ts"], how="inner").dropna()
    d = mg.sort_values(["station_id", "ts"]).copy()
    d["d_est"] = d.groupby("station_id")["inv_est"].diff()
    d["d_act"] = d.groupby("station_id")["inv_act"].diff()
    corr = (d.dropna().groupby("station_id")
            .apply(lambda g: g.d_est.corr(g.d_act) if len(g) > 24 else np.nan, include_groups=False)
            .dropna())
    print(f"\n[② 변화 패턴 상관] 대여소 {len(corr):,}개 · 시간별 Δ재고 상관")
    print(f"   중앙값 {corr.median():.3f} / 25~75분위 {corr.quantile(.25):.3f}~{corr.quantile(.75):.3f}"
          f" / 0.5 이상 비율 {(corr >= .5).mean()*100:.1f}%")
    lines.append(f"| ② Δ재고 상관 | 중앙값 {corr.median():.3f}, 0.5 이상 {(corr>=.5).mean()*100:.1f}% |")

    # ── ③ 재고 소진 실재 여부 ───────────────────────────────────────────
    zero = obs.groupby("station_id")["bikes_available"].apply(lambda s: (s == 0).mean() * 100)
    z = pd.DataFrame({"zero_pct": zero}).join(met.set_index("station_id")[["net_out_per_day", "name"]])
    z = z.dropna()
    c3 = z.zero_pct.corr(z.net_out_per_day)
    hi = z[z.net_out_per_day > z.net_out_per_day.quantile(.75)].zero_pct.mean()
    lo = z[z.net_out_per_day < z.net_out_per_day.quantile(.25)].zero_pct.mean()
    print(f"\n[③ 재고 소진 실재] 0대 시간 비율 vs 일평균 순유출 상관 {c3:.3f}")
    print(f"   순유출 상위 25% 대여소 평균 {hi:.1f}%  vs  하위 25% {lo:.1f}%")
    lines.append(f"| ③ 0대 시간 비율 | 순유출 상위 {hi:.1f}% vs 하위 {lo:.1f}% (상관 {c3:.3f}) |")

    # ── ④ 관제센터의 정체 ───────────────────────────────────────────────
    ctrl = master[master.station_type == "CONTROL"].station_id.tolist()
    got = obs[obs.station_id.isin(ctrl)]
    if len(got):
        m = got.bikes_available.mean()
        print(f"\n[④ 관제센터] 실측에 등장 — 평균 거치 {m:.1f}대")
        print("   → 자전거가 실제로 거치돼 있다면 '실제 정비 반출입'(가설 B) 쪽 근거")
        lines.append(f"| ④ 관제센터 | 실측 평균 {m:.1f}대 거치 |")
    else:
        print("\n[④ 관제센터] 실측 목록에 없음 → '장부상 보정'(가설 A) 쪽 근거")
        lines.append("| ④ 관제센터 | 실측 목록에 없음 (가설 A 지지) |")

    # ── ⑤ 재고 오프셋 확정 ──────────────────────────────────────────────
    off = (mg.groupby("station_id").apply(
        lambda g: g.inv_act.mean() - g.inv_est.mean(), include_groups=False).rename("offset"))
    print(f"\n[⑤ 재고 오프셋] 대여소 {len(off):,}개 확정 — 중앙값 {off.median():.1f}대")
    off.to_csv(PROC / "inventory_offset.csv", encoding="utf-8-sig")
    lines.append(f"| ⑤ 재고 오프셋 | {len(off):,}개 대여소 확정 (중앙값 {off.median():.1f}대) |")

    # ── 그림 11 ─────────────────────────────────────────────────────────
    vs.apply()
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.2))
    ax = axes[0]
    ax.hist(corr, bins=24, color=vs.SERIES[0], edgecolor=vs.SURFACE, linewidth=0.6)
    ax.axvline(corr.median(), color=vs.POS, lw=1.6, ls="--")
    ax.text(corr.median(), ax.get_ylim()[1] * 0.95, f" 중앙값 {corr.median():.2f}",
            color=vs.POS, fontsize=9, va="top")
    ax.set_title("추정 Δ재고 vs 실측 Δ재고 상관 분포")
    vs.subtitle(ax, "1에 가까울수록 이력만으로 복원한 재고 변화가 실제와 같다는 뜻")
    ax.set_xlabel("대여소별 상관계수"); ax.set_ylabel("대여소 수"); ax.grid(axis="y")

    ax = axes[1]
    ax.scatter(z.net_out_per_day, z.zero_pct, s=22, color=vs.SERIES[0], alpha=0.75,
               linewidths=0.3, edgecolors=vs.SURFACE)
    ax.set_title("일평균 순유출 vs 실제 0대 시간 비율")
    vs.subtitle(ax, f"오른쪽 위로 향하면 '순유출이 큰 곳이 실제로 빈다'는 뜻 (상관 {c3:.3f})")
    ax.set_xlabel("일평균 순유출 (대/일)"); ax.set_ylabel("0대 시간 비율 (%)"); ax.grid(alpha=.6)
    fig.tight_layout()
    vs.source(fig, "출처: 타슈 OpenAPI 실측" + (" — ⚠ SAMPLE(합성)" if a.sample else ""))
    out = IMG / ("11_realtime_validation_SAMPLE.png" if a.sample else "11_realtime_validation.png")
    fig.savefig(out); plt.close(fig)
    print(f"\n그림 저장 → {out.relative_to(ROOT)}")

    # ── 리포트 자동 생성 ────────────────────────────────────────────────
    md = REPORTS / ("05_realtime_validation_SAMPLE.md" if a.sample else "05_realtime_validation.md")
    md.write_text(
        f"# 6단계 — 실측(OpenAPI) 검증\n\n> {banner}\n\n"
        f"수집: 스냅샷 {obs.collected_at.nunique():,}개 / 대여소 {obs.station_id.nunique():,}개 / "
        f"{obs.ts.min():%Y-%m-%d} ~ {obs.ts.max():%Y-%m-%d}\n\n"
        "| 검증 | 결과 |\n|---|---|\n" + "\n".join(lines) +
        f"\n\n![검증]({out.relative_to(ROOT)})\n", encoding="utf-8")
    print(f"리포트 저장 → {md.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
