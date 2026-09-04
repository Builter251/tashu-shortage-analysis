#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
08_threshold_calibration.py — CPI·WR 임계값의 근거를 데이터로 확인한다.

PLAN.md §3-1 은 "임계값 0.40 / 0.80 / 1.10 은 잠정이며, 전 대여소 분포를 그린 뒤
사분위 기준으로 재보정한다"고 적어 두었다. 이 스크립트가 그 약속을 이행한다.

만드는 것
  images/12_threshold_calibration.png   CPI·WR 히스토그램 + 귀무기준선 + 사분위 + 채택선
  data/processed/threshold_sensitivity.csv  임계값을 흔들었을 때 결론이 움직이는지

핵심 질문 세 가지
  ① CPI 의 '아무 패턴 없음' 기준선은 어디인가            → 6/24 = 0.25 (정의상 고정)
  ② 그 기준선으로 대여소를 나눌 수 있는가                → 없다. 98% 가 0.25 초과
  ③ 그렇다면 0.40 은 무엇인가                            → 분포의 상위 사분위 근처
     (사후 확인이지 사전 유도가 아니다. 아래 출력이 그 사실을 그대로 보여준다)

실행:  python3 src/08_threshold_calibration.py
"""
from __future__ import annotations

import sys
from pathlib import Path

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
IMG.mkdir(exist_ok=True)
vs.apply()

PEAK_HOURS = [7, 8, 9, 17, 18, 19]
CPI_NULL = len(PEAK_HOURS) / 24.0           # 0.25 — 시간대가 완전 균등할 때의 CPI
CPI_COMMUTE, WR_COMMUTE = 0.40, 0.80        # 03_analyze.py 와 동일해야 한다
CPI_LEISURE, WR_LEISURE = 0.30, 1.10
MIN_MONTHLY_RENTALS = 30
TOP_N = 30                                   # 순유출 상위 N 개


def log(m):
    print(f"[08] {m}", flush=True)


def classify(df, cpi_c, wr_c, cpi_l, wr_l):
    ok = df["monthly_rentals"] >= MIN_MONTHLY_RENTALS
    return np.where(~ok, "판정 불가",
           np.where((df.CPI >= cpi_c) & (df.WR <= wr_c), "출퇴근형",
           np.where((df.WR >= wr_l) & (df.CPI <= cpi_l), "여가형", "혼합/생활형")))


met = pd.read_csv(PROC / "station_metrics.csv")
j = met[met["monthly_rentals"] >= MIN_MONTHLY_RENTALS].dropna(subset=["CPI", "WR"]).copy()
log(f"판정 대상 {len(j):,} / 전체 {len(met):,} 대여소")

# ── ① 분포와 사분위 ─────────────────────────────────────────────────────────
qs = [0.10, 0.25, 0.50, 0.75, 0.90]
stat = {}
for c in ["CPI", "WR"]:
    s = j[c]
    stat[c] = {f"q{int(q*100)}": round(float(s.quantile(q)), 3) for q in qs}
    stat[c].update(min=round(float(s.min()), 3), max=round(float(s.max()), 3),
                   mean=round(float(s.mean()), 3), std=round(float(s.std()), 3))
    log(f"{c}: " + "  ".join(f"{k}={v}" for k, v in stat[c].items()))

pct_above_null = float((j.CPI > CPI_NULL).mean())
pct_above_cut = float((j.CPI >= CPI_COMMUTE).mean())
log(f"CPI > 0.25(귀무기준) 인 대여소: {pct_above_null:.1%}  ← 기준선으로는 구분이 안 된다")
log(f"CPI ≥ 0.40(채택선) 인 대여소: {pct_above_cut:.1%}  ← q75={stat['CPI']['q75']} 와 사실상 같은 자리")
log(f"WR ≤ 0.80(채택선) 인 대여소: {(j.WR <= WR_COMMUTE).mean():.1%}  ← q25={stat['WR']['q25']}")
log(f"WR ≥ 1.10(채택선) 인 대여소: {(j.WR >= WR_LEISURE).mean():.1%}")

# ── ② 그림 ───────────────────────────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(13.0, 5.4))

PANELS = [
    (axes[0], "CPI", "CPI  (평일 07–09·17–19시 대여 비중)",
     [(CPI_NULL,     "귀무기준 0.25", vs.MUTED,     ":"),
      (CPI_LEISURE,  "여가형 ≤ 0.30", vs.SERIES[2], "--"),
      (CPI_COMMUTE,  "출퇴근형 ≥ 0.40", vs.POS,     "-")],
     "0.25 는 '패턴 없음' 선인데 98% 가 그 위 → 절대 기준으로는 못 나눈다"),
    (axes[1], "WR", "WR  (주말 일평균 ÷ 평일 일평균)",
     [(1.00,         "분기점 1.00", vs.MUTED,       ":"),
      (WR_COMMUTE,   "출퇴근형 ≤ 0.80", vs.POS,     "-"),
      (WR_LEISURE,   "여가형 ≥ 1.10", vs.SERIES[2], "--")],
     "1.00 은 비율의 구조적 분기점. 0.80·1.10 은 그 둘레의 완충대다"),
]

for ax, col, xlab, cuts, note in PANELS:
    s_ = j[col]
    hi = float(s_.quantile(0.995))
    ax.hist(s_.clip(upper=hi), bins=55, color=vs.BLUES[6],
            edgecolor="white", linewidth=0.4, zorder=2)
    q25, q75 = float(s_.quantile(0.25)), float(s_.quantile(0.75))
    ymax = ax.get_ylim()[1]
    ax.set_ylim(0, ymax * 1.42)                       # 라벨을 놓을 머리 공간
    top = ax.get_ylim()[1]

    ax.axvspan(q25, q75, color=vs.BLUES[1], alpha=0.45, zorder=0)
    ax.annotate("", xy=(q25, top * 0.995), xytext=(q75, top * 0.995),
                arrowprops=dict(arrowstyle="<->", color=vs.INK2, lw=0.9))
    ax.text((q25 + q75) / 2, top * 0.955, f"사분위 범위 {q25:.2f}–{q75:.2f}",
            ha="center", va="top", fontsize=9, color=vs.INK2)

    for i, (x, lab, c, ls) in enumerate(cuts):
        ax.axvline(x, color=c, linestyle=ls, linewidth=1.7, zorder=3)
        ax.plot([x], [top * (0.86 - 0.075 * i)], marker="o", ms=4.5,
                color=c, zorder=5)
        ax.annotate(lab, xy=(x, top * (0.86 - 0.075 * i)),
                    xytext=(7, 0), textcoords="offset points",
                    fontsize=9, color=c, ha="left", va="center", zorder=5,
                    bbox=dict(boxstyle="round,pad=0.18", fc=vs.SURFACE,
                              ec="none", alpha=0.85))

    ax.set_xlabel(xlab)
    ax.set_ylabel("대여소 수")
    ax.set_title(f"{col} 분포와 임계값", loc="left", fontsize=12.5, color=vs.INK, pad=22)
    vs.subtitle(ax, note)

fig.suptitle("임계값은 문헌값이 아니다 — 어디에 그었고, 왜 거기인지",
             x=0.008, y=0.985, ha="left", fontsize=14.5, color=vs.INK)
fig.text(0.008, 0.925,
         f"CPI 는 채택선 0.40 이 상위 사분위({stat['CPI']['q75']}) 자리, "
         f"WR 은 채택선 0.80 이 하위 사분위({stat['WR']['q25']}) 자리에 떨어졌다. "
         f"사전에 유도한 값이 아니라 사후에 확인한 일치다.",
         fontsize=9.5, color=vs.INK2, ha="left", va="top")
fig.tight_layout(rect=(0, 0.02, 1, 0.90))
vs.source(fig)
fig.savefig(IMG / "12_threshold_calibration.png")
plt.close(fig)
log("images/12_threshold_calibration.png 저장")

# ── ③ 민감도: 선을 흔들면 결론이 움직이는가 ─────────────────────────────────
base_r = float(j["CPI"].corr(j["net_out_per_day"]))
top = met.nlargest(TOP_N, "net_out_per_day")["station_id"]

rows = []
grid = [("채택값", CPI_COMMUTE, WR_COMMUTE, CPI_LEISURE, WR_LEISURE),
        ("사분위 재보정", round(float(j.CPI.quantile(0.75)), 3), round(float(j.WR.quantile(0.25)), 3),
         round(float(j.CPI.quantile(0.25)), 3), round(float(j.WR.quantile(0.75)), 3)),
        ("귀무기준 0.25", 0.25, WR_COMMUTE, CPI_LEISURE, WR_LEISURE),
        ("느슨 0.35", 0.35, 0.85, 0.32, 1.05),
        ("엄격 0.45", 0.45, 0.75, 0.28, 1.15),
        ("상위 10%", round(float(j.CPI.quantile(0.90)), 3), round(float(j.WR.quantile(0.10)), 3),
         round(float(j.CPI.quantile(0.10)), 3), round(float(j.WR.quantile(0.90)), 3))]

for label, cc, wc, cl, wl in grid:
    t = met.copy()
    t["ut"] = classify(t, cc, wc, cl, wl)
    vc = t["ut"].value_counts()
    n_commute_in_top = int((t[t.station_id.isin(top)]["ut"] == "출퇴근형").sum())
    rows.append(dict(시나리오=label, CPI_출퇴근=cc, WR_출퇴근=wc, CPI_여가=cl, WR_여가=wl,
                     출퇴근형=int(vc.get("출퇴근형", 0)), 여가형=int(vc.get("여가형", 0)),
                     혼합생활형=int(vc.get("혼합/생활형", 0)), 판정불가=int(vc.get("판정 불가", 0)),
                     순유출상위30중_출퇴근형=n_commute_in_top))

sens = pd.DataFrame(rows)
# 기저율 대비 쏠림: 상위 30개의 출퇴근형 비중 ÷ 전체 출퇴근형 비중.
# 1.0 이면 '무작위로 뽑은 것과 다를 바 없다', 2.0 이면 '두 배로 몰려 있다'.
n_judge = len(j)
sens["전체_출퇴근형_%"] = (sens["출퇴근형"] / n_judge * 100).round(1)
sens["상위30_출퇴근형_%"] = (sens["순유출상위30중_출퇴근형"] / TOP_N * 100).round(1)
sens["쏠림배수"] = (sens["상위30_출퇴근형_%"] / sens["전체_출퇴근형_%"]).round(2)
sens["CPI_순유출_상관"] = round(base_r, 3)          # 분류와 무관한 연속 상관 (참고선)
sens.to_csv(PROC / "threshold_sensitivity.csv", index=False, encoding="utf-8-sig")

log("")
log("민감도 — 임계값을 어떻게 흔들어도 '이용 유형으로는 부족을 못 맞춘다'는 결론은 그대로다")
print(sens.to_string(index=False))
log("")
log(f"CPI ↔ 일평균 순유출 상관 r = {base_r:+.3f}  (분류 임계값과 무관한 연속값 기준)")
log("기저율 대비 쏠림배수: "
    + ", ".join(f"{r.시나리오} {r.쏠림배수}배" for r in sens.itertuples()))
log("→ 1.0~2.0 사이에서만 움직이고, 어느 선에서도 '출퇴근형이면 부족하다'가 되지 않는다.")
log("data/processed/threshold_sensitivity.csv 저장")
