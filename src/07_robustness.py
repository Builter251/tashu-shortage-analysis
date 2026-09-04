#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
07_robustness.py — 반례와 민감도 분석: 이 결론은 어떤 조건에서 달라지는가

리포트의 결론이 "언제나 참"인지, 아니면 특정 선택(기간·집계 단위·가정)에 기대고 있는지를
스스로 흔들어 본다. 결론을 방어하는 게 아니라 **깨질 수 있는 지점을 찾는 것**이 목적이다.

  반례 A — 기간을 바꾸면 순유출 랭킹이 얼마나 바뀌는가 (성수기 vs 비수기)
  반례 B — 집계 단위를 일 단위로 올리면 무엇이 사라지는가
  반례 C — 재배치 시각 추정 가정(공백의 중간점)을 바꾸면 결론이 흔들리는가

산출: images/11_robustness.png · data/processed/robustness.csv

실행:  python3 src/07_robustness.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import viz_style as vs

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
IMG = ROOT / "images"
vs.apply()

MIN_RENT = 300          # 기간이 짧아지면 표본이 줄어든다. 최소 대여 300건 이상만 비교
PEAK = ("2025-04-01", "2025-09-30")     # 성수기
LOW = ("2025-11-01", "2026-02-28")      # 비수기
FOCUS = "ST0051"                         # 순유출 1위 대여소

con = duckdb.connect(); con.execute("PRAGMA memory_limit='2GB'")
con.execute(f"CREATE VIEW h AS SELECT * FROM read_parquet('{PROC/'station_hourly.parquet'}')")
con.execute(f"CREATE VIEW mv AS SELECT * FROM read_parquet('{PROC/'bike_moves.parquet'}')")
master = pd.read_csv(PROC / "station_master.csv"); con.register("m", master)
nm = master.set_index("station_id")["name"]

obs = con.execute("""SELECT h.ts::DATE AS d, sum(h.rentals_real) AS n
    FROM h JOIN m ON h.station_id=m.station_id WHERE m.station_type<>'CONTROL'
    GROUP BY 1 HAVING sum(h.rentals_real) > 0""").df()
obs["d"] = pd.to_datetime(obs["d"]); con.register("obs", obs[["d"]])


def period(a, b):
    return con.execute(f"""
        SELECT h.station_id AS sid, sum(h.net_out) AS cum,
               sum(h.net_out)/count(DISTINCT h.ts::DATE) AS npd, sum(h.rentals) AS rent
        FROM h JOIN m ON h.station_id=m.station_id JOIN obs o ON h.ts::DATE=o.d
        WHERE m.station_type='ACTIVE' AND h.ts::DATE BETWEEN DATE '{a}' AND DATE '{b}'
        GROUP BY 1 HAVING sum(h.rentals) >= {MIN_RENT}""").df().set_index("sid")


full, peak, low = period("2024-08-01", "2026-03-31"), period(*PEAK), period(*LOW)
rows = []
for name, d in [("성수기", peak), ("비수기", low)]:
    c = full.index.intersection(d.index)
    rows.append({
        "구간": name, "공통 대여소": len(c),
        "순위상관": round(full.loc[c, "npd"].rank().corr(d.loc[c, "npd"].rank()), 3),
        "Top10 겹침": len(set(full.loc[c].nlargest(10, "npd").index)
                        & set(d.loc[c].nlargest(10, "npd").index)),
        "부호 뒤집힘": int(((full.loc[c, "npd"] > 0) != (d.loc[c, "npd"] > 0)).sum()),
        "뒤집힘 비율%": round(((full.loc[c, "npd"] > 0) != (d.loc[c, "npd"] > 0)).mean() * 100, 1)})
tbl = pd.DataFrame(rows)
print("[반례 A] 기간을 바꾸면"); print(tbl.to_string(index=False))
tbl.to_csv(PROC / "robustness.csv", index=False, encoding="utf-8-sig")

common = peak.index.intersection(low.index)
cmp = pd.DataFrame({"peak": peak.loc[common, "npd"], "low": low.loc[common, "npd"],
                    "rent": full.loc[common, "rent"]}).dropna()
flip = cmp[((cmp.peak > 0) & (cmp.low < 0)) | ((cmp.peak < 0) & (cmp.low > 0))]
print(f"\n계절에 따라 방향이 뒤집히는 대여소 {len(flip)}개 / {len(cmp)}개 "
      f"({len(flip)/len(cmp)*100:.1f}%)")

# ── 반례 C: 재배치 시각 가정 ────────────────────────────────────────────────
ASSUME = [("공백의 중간점 (현재 가정)", "gap_start + (gap_end - gap_start)/2", vs.SERIES[1]),
          ("공백 시작 직후", "gap_start", vs.SERIES[0]),
          ("공백 종료 직전", "gap_end", vs.SERIES[2])]
curves = {}
for label, expr, _ in ASSUME:
    d = con.execute(f"""SELECT hour({expr}) AS hh, count(*) AS n FROM mv
        WHERE move_type='RELOCATION' AND NOT spans_gap AND to_st='{FOCUS}'
        GROUP BY 1""").df().set_index("hh").reindex(range(24)).fillna(0)["n"]
    curves[label] = d / d.sum() * 100
    print(f"  {label:20s} 피크 {int(d.idxmax()):2d}시 · 새벽(0~5시) 비중 {curves[label][0:6].sum():.1f}%")

# ── 그림 11 ────────────────────────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.9), gridspec_kw={"width_ratios": [1, 1.05]})

ax = axes[0]
lim = np.nanpercentile(np.abs(np.r_[cmp.peak, cmp.low]), 99)
ax.axhspan(-lim * 1.15, 0, xmin=0.5, color=vs.POS, alpha=.07)
ax.axhspan(0, lim * 1.15, xmax=0.5, color=vs.POS, alpha=.07)
ax.axhline(0, color=vs.BASELINE, lw=1); ax.axvline(0, color=vs.BASELINE, lw=1)
ax.plot([-lim, lim], [-lim, lim], color=vs.MUTED, lw=1, ls=":")
ok = cmp.drop(flip.index)
ax.scatter(ok.peak, ok.low, s=13, color=vs.NEG, alpha=.45, linewidths=0)
ax.scatter(flip.peak, flip.low, s=17, color=vs.POS, alpha=.75, linewidths=0)
ax.set_xlim(-lim * 1.15, lim * 1.15); ax.set_ylim(-lim * 1.15, lim * 1.15)
ax.set_title("반례 A — 기간을 바꾸면 방향이 뒤집히는 대여소가 있다")
vs.subtitle(ax, f"점 하나가 대여소 하나. 붉은 영역(={len(flip)}개, {len(flip)/len(cmp)*100:.0f}%)은 성수기와 비수기의 부호가 반대인 곳.")
ax.set_xlabel("성수기 일평균 순유출 (대/일)  ▶ 오른쪽일수록 여름에 빠짐")
ax.set_ylabel("비수기 일평균 순유출 (대/일)")
ax.grid(alpha=.5)
ax.text(lim * .95, -lim * .95, "여름엔 빠지고\n겨울엔 쌓임", ha="right", va="bottom",
        fontsize=8.5, color=vs.POS)

ax = axes[1]
for (label, _, color), (k, v) in zip(ASSUME, curves.items()):
    ax.plot(range(24), v.values, lw=2, color=color, label=label,
            ls="-" if "중간점" in label else "--")
ax.set_xticks(range(0, 24, 3)); ax.set_xticklabels([f"{x}시" for x in range(0, 24, 3)])
ax.set_title("반례 C — '언제 채워지는가'는 가정이 정한다")
vs.subtitle(ax, f"{nm.get(FOCUS, FOCUS)[:20]}의 재배치 유입 시각 분포. 같은 데이터인데 가정에 따라 피크가 15시↔18시↔21시로 움직인다.")
ax.set_xlabel("추정한 재배치 시각"); ax.set_ylabel("유입 건수 비중 (%)")
ax.grid(axis="y"); ax.legend(loc="upper left", fontsize=8.5)
fig.tight_layout()
vs.source(fig)
fig.savefig(IMG / "11_robustness.png"); plt.close(fig)
print("\n그림 저장 → images/11_robustness.png")
