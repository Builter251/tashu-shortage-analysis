#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03_analyze.py — 지표 계산 + 그림 생성

만드는 것
  images/01_daily_trend.png        일별 대여량 + 7일/30일 이동평균 + 결측 구간 표시
  images/02_hour_dow_heatmap.png   시간대 × 요일 히트맵
  images/03_netflow_ranking.png    대여소별 일평균 순유출 상·하위 15
  images/04_inventory_profile.png  문제 대여소의 하루 재고 프로파일(재배치 반영 전/후)
  images/05_netflow_map.png        순유출 지도
  images/06_cpi_wr_scatter.png     CPI×WR 이용 유형 분류
  images/07_stl_decomposition.png  STL 분해 (추세/계절/잔차)
  data/processed/station_metrics.csv   대여소별 지표 표

실행:  python3 src/03_analyze.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb
import matplotlib as mpl
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

DOW_KR = ["월", "화", "수", "목", "금", "토", "일"]
PEAK_HOURS = [7, 8, 9, 17, 18, 19]          # CPI 정의: 하루 24시간의 정확히 25%
CPI_COMMUTE, WR_COMMUTE = 0.40, 0.80
CPI_LEISURE, WR_LEISURE = 0.30, 1.10
MIN_MONTHLY_RENTALS = 30                    # 이 미만은 '판정 불가'

MISSING_NOTES = [("2025-02-26", "2025-02-28", "시스템 장애"),
                 ("2025-03-01", "2025-03-03", "시스템 장애"),
                 ("2025-12-01", "2025-12-31", "파일 중복 배포로 데이터 없음")]


def log(m, t0=None):
    print(f"[03] {m}" + (f"  ({time.time()-t0:.1f}s)" if t0 else ""), flush=True)


con = duckdb.connect()
con.execute("PRAGMA memory_limit='2GB'")
con.execute(f"CREATE VIEW h AS SELECT * FROM read_parquet('{PROC/'station_hourly.parquet'}')")
master = pd.read_csv(PROC / "station_master.csv")
con.register("m", master)
REAL = "m.station_type <> 'CONTROL'"        # 관제센터는 물리적 대여소가 아니다
ACTIVE = "m.station_type = 'ACTIVE'"

# ============================================================ 1) 일별 추세
t0 = time.time()
daily = con.execute(f"""
    SELECT h.ts::DATE AS d, sum(h.rentals_real) AS rentals
    FROM h JOIN m ON h.station_id = m.station_id
    WHERE {REAL} GROUP BY 1 ORDER BY 1""").df()
daily["d"] = pd.to_datetime(daily["d"])
full = pd.date_range(daily["d"].min(), daily["d"].max(), freq="D")
s = daily.set_index("d")["rentals"].reindex(full)
# 함정: 재배치 이벤트는 결측 구간 한가운데(공백의 중간점)에도 찍히므로,
# 2025-12 같은 '데이터가 아예 없는 날'도 패널에는 행이 존재한다(대여는 0).
# 대여 0건인 날은 실제로 운영이 멈춘 게 아니라 데이터가 없는 날이므로 결측 처리한다.
s = s.mask(s.fillna(0) == 0)                       # 결측일은 NaN → 선이 끊긴다
ma7 = s.rolling(7, min_periods=7).mean()
ma30 = s.rolling(30, min_periods=30).mean()
log(f"일별 시계열 {len(s)}일 중 관측 {int(s.notna().sum())}일 / 결측 {int(s.isna().sum())}일", t0)

# 관측일 목록 — 이후 모든 '평균' 계산의 분모로 쓴다(결측일을 분모에 넣으면 과소추정된다)
obsdays = pd.DataFrame({"d": s.dropna().index})
con.register("obsdays", obsdays)
OBS = "JOIN obsdays od ON h.ts::DATE = od.d"

fig, ax = plt.subplots(figsize=(11, 4.2))
ax.plot(s.index, s.values, lw=0.8, color=vs.BLUES[3], label="일별 대여")
ax.plot(ma7.index, ma7.values, lw=2.0, color=vs.SERIES[0], label="7일 이동평균")
ax.plot(ma30.index, ma30.values, lw=2.0, color=vs.SERIES[1], label="30일 이동평균")
for a, b, why in MISSING_NOTES:
    ax.axvspan(pd.Timestamp(a), pd.Timestamp(b) + pd.Timedelta(days=1),
               color="#f0efec", zorder=0)
ax.text(pd.Timestamp("2025-12-16"), s.max() * 0.93, "2025-12\n데이터 없음",
        ha="center", fontsize=8.5, color=vs.INK2)
ax.set_title("타슈 일별 대여량 — 여름 성수기·겨울 비수기의 뚜렷한 연 주기")
vs.subtitle(ax, "회색 띠는 데이터 결측 구간(총 37일). 보간하지 않고 선을 끊어 표시했다.")
ax.set_ylabel("대여 건수 / 일"); ax.grid(axis="y"); ax.legend(loc="upper left", ncol=3)
vs.source(fig); fig.savefig(IMG / "01_daily_trend.png"); plt.close(fig)

# ============================================================ 2) 시간대 × 요일
t0 = time.time()
hd = con.execute(f"""
    SELECT dayofweek(h.ts) AS dw, hour(h.ts) AS hh, sum(h.rentals_real) AS n
    FROM h JOIN m ON h.station_id = m.station_id {OBS}
    WHERE {REAL} GROUP BY 1, 2""").df()
hd["dow"] = (hd["dw"] + 6) % 7                       # duckdb 0=일 → 0=월
piv_n = hd.pivot(index="dow", columns="hh", values="n").reindex(range(7)).fillna(0)
dcount = (obsdays["d"].dt.dayofweek.value_counts().sort_index())   # 0=월
piv = piv_n.div(dcount, axis=0)                      # 요일별 관측일수로 나눠 평균화

fig, ax = plt.subplots(figsize=(11, 3.6))
im = ax.imshow(piv.values, aspect="auto", cmap=vs.CMAP_SEQ, origin="upper")
ax.set_xticks(range(0, 24, 2)); ax.set_xticklabels([f"{x}시" for x in range(0, 24, 2)])
ax.set_yticks(range(7)); ax.set_yticklabels(DOW_KR)
ax.set_title("시간대 × 요일 평균 대여 건수 — 평일은 아침 8시·저녁 18시 이중 피크, 주말은 오후 한 덩어리")
vs.subtitle(ax, "요일별 관측 일수로 나눈 '시간당 평균 대여 건수'. 결측일은 분모에서 자동 제외된다.")
cb = fig.colorbar(im, ax=ax, pad=0.015, fraction=0.03)
cb.set_label("시간당 평균 대여 건수", fontsize=9, color=vs.INK2)
cb.outline.set_visible(False)
for d in range(7):
    hpk = int(np.argmax(piv.values[d]))
    ax.text(hpk, d, "▲", ha="center", va="center", fontsize=7,
            color="#ffffff" if piv.values[d, hpk] > piv.values.max() * 0.55 else vs.INK)
vs.source(fig); fig.savefig(IMG / "02_hour_dow_heatmap.png"); plt.close(fig)
log("그림 02 완료 (▲ = 요일별 최대 시간대)", t0)

# ============================================================ 3) 대여소 지표
t0 = time.time()
peak = ",".join(map(str, PEAK_HOURS))
met = con.execute(f"""
WITH agg AS (
  SELECT h.station_id AS station_id,
    sum(h.rentals) AS rentals, sum(h."returns") AS returns_n,
    sum(h.rentals_real) AS rentals_real,
    sum(h.net_out) AS net_out,
    sum(h.reloc_in_phys) AS reloc_in, sum(h.reloc_out_phys) AS reloc_out,
    sum(h.rentals_to_ctrl) + sum(h.returns_from_ctrl) AS ctrl_events,
    sum(CASE WHEN dayofweek(h.ts) BETWEEN 1 AND 5 THEN h.rentals_real ELSE 0 END) AS wd_rent,
    sum(CASE WHEN dayofweek(h.ts) BETWEEN 1 AND 5
              AND hour(h.ts) IN ({peak}) THEN h.rentals_real ELSE 0 END) AS wd_peak,
    sum(CASE WHEN dayofweek(h.ts) IN (0,6) THEN h.rentals_real ELSE 0 END) AS we_rent
  FROM h JOIN m ON h.station_id = m.station_id {OBS}
  WHERE {ACTIVE} GROUP BY 1)
SELECT * FROM agg""").df()

dow_obs = obsdays["d"].dt.dayofweek
n_weekday = int((dow_obs <= 4).sum())
n_weekend = int((dow_obs >= 5).sum())
n_days = n_weekday + n_weekend
n_months = n_days / 30.44

met = met.merge(master[["station_id", "name", "gu", "dong", "lat", "lon", "total_events"]],
                on="station_id", how="left")
met["net_out_per_day"] = (met["net_out"] / n_days).round(3)
met["CPI"] = (met["wd_peak"] / met["wd_rent"].replace(0, np.nan)).round(4)
met["WR"] = ((met["we_rent"] / n_weekend) / (met["wd_rent"] / n_weekday).replace(0, np.nan)).round(4)
met["ctrl_pct"] = (100 * met["ctrl_events"] / (met["rentals"] + met["returns_n"])).round(2)
met["reloc_net_in"] = met["reloc_in"] - met["reloc_out"]
met["unmet"] = met["net_out"] - met["reloc_net_in"]        # 재배치로도 안 메워진 몫
met["monthly_rentals"] = (met["rentals_real"] / n_months).round(1)

judgeable = met["monthly_rentals"] >= MIN_MONTHLY_RENTALS
met["use_type"] = np.where(~judgeable, "판정 불가",
    np.where((met.CPI >= CPI_COMMUTE) & (met.WR <= WR_COMMUTE), "출퇴근형",
    np.where((met.WR >= WR_LEISURE) & (met.CPI <= CPI_LEISURE), "여가형", "혼합/생활형")))
met.sort_values("net_out_per_day", ascending=False).to_csv(
    PROC / "station_metrics.csv", index=False, encoding="utf-8-sig")
log(f"대여소 지표 {len(met)}개 (관측일 {n_days}일: 평일 {n_weekday} / 주말 {n_weekend})", t0)
log("  이용 유형: " + " / ".join(f"{k}={v}" for k, v in met["use_type"].value_counts().items()))

# ============================================================ 그림 03 랭킹
N = 15
top = met.nlargest(N, "net_out_per_day"); bot = met.nsmallest(N, "net_out_per_day")
sel = pd.concat([bot.iloc[::-1], top.iloc[::-1]])
labels = [f"{r['name'][:22]}" for _, r in sel.iterrows()]
vals = sel["net_out_per_day"].values
lim = np.abs(vals).max()
colors = [vs.POS if v > 0 else vs.NEG for v in vals]

fig, ax = plt.subplots(figsize=(9.5, 8.2))
ax.barh(range(len(vals)), vals, color=colors, height=0.72)
ax.axvline(0, color=vs.BASELINE, lw=1)
ax.set_yticks(range(len(vals))); ax.set_yticklabels(labels, fontsize=8.5, color=vs.INK2)
ax.set_xlim(-lim * 1.35, lim * 1.35)
# 부호 대신 방향(순유출/순유입)으로 읽히게 하므로 막대 값은 절대 크기로 적는다
for i, v in enumerate(vals):
    ax.text(v + (0.06 * lim if v > 0 else -0.06 * lim), i, f"{abs(v):.2f}",
            va="center", ha="left" if v > 0 else "right", fontsize=8, color=vs.INK2)
ax.set_title("대여소별 일평균 순유출·순유입 — 각 상위 15")
vs.subtitle(ax, "대여 − 반납이 양수면 순유출(빨강, 자전거가 빠짐), 음수면 순유입(파랑, 자전거가 쌓임). 막대 값은 절대 크기.")
# 눈금도 부호를 지운다. 방향은 색과 좌우로 읽고, 숫자는 크기만 나타낸다.
ax.xaxis.set_major_formatter(mpl.ticker.FuncFormatter(lambda v, _: f"{abs(v):g}"))
ax.set_xlabel("←  순유입 (대/일)                    순유출 (대/일)  →"); ax.grid(axis="x")
vs.source(fig); fig.savefig(IMG / "03_netflow_ranking.png"); plt.close(fig)

# ============================================================ 그림 04 재고 프로파일
t0 = time.time()
focus = list(top["station_id"].head(3)) + [met.nlargest(1, "rentals")["station_id"].iloc[0]]
fnames = {r.station_id: r["name"] for _, r in met[met.station_id.isin(focus)].iterrows()}
ids = ",".join(f"'{x}'" for x in focus)
prof = con.execute(f"""
    SELECT station_id, hour(ts) AS hh,
           sum("returns" - rentals) AS raw_flow,
           sum(net_flow) AS with_reloc,
           count(DISTINCT ts::DATE) AS days
    FROM h {OBS.replace("JOIN m ON h.station_id = m.station_id ", "")}
    WHERE station_id IN ({ids}) AND dayofweek(ts) BETWEEN 1 AND 5
    GROUP BY 1, 2 ORDER BY 1, 2""").df()

fig, axes = plt.subplots(2, 2, figsize=(11.5, 6.6), sharex=True)
for ax, sid in zip(axes.ravel(), focus):
    g = prof[prof.station_id == sid].set_index("hh").reindex(range(24)).fillna(0)
    cum_raw = (g["raw_flow"] / n_weekday).cumsum()
    cum_all = (g["with_reloc"] / n_weekday).cumsum()
    ax.axhline(0, color=vs.BASELINE, lw=1)
    ax.plot(cum_raw.index, cum_raw.values, color=vs.SERIES[0], label="이용만 반영")
    ax.plot(cum_all.index, cum_all.values, color=vs.SERIES[1], ls="--", label="재배치까지 반영")
    ax.fill_between(cum_raw.index, cum_raw.values, 0,
                    where=(cum_raw.values < 0), color=vs.POS, alpha=0.12)
    lo = int(cum_raw.idxmin())
    ax.plot([lo], [cum_raw.min()], "o", color=vs.POS, ms=7, zorder=5)
    right = lo >= 17          # 오른쪽 끝에서는 라벨을 왼쪽으로 뽑아야 잘리지 않는다
    ax.annotate(f"최저 {lo}시  {cum_raw.min():+.1f}대", (lo, cum_raw.min()),
                textcoords="offset points", xytext=(-8 if right else 8, 10),
                ha="right" if right else "left", va="bottom", fontsize=8, color=vs.POS)
    ax.set_xlim(-0.5, 23.5)
    ax.set_title(f"{fnames.get(sid, sid)[:24]}", fontsize=10.5)
    ax.grid(axis="y")
axes[0, 0].legend(loc="lower left")
for ax in axes[1]:
    ax.set_xticks(range(0, 24, 3)); ax.set_xticklabels([f"{x}시" for x in range(0, 24, 3)])
fig.suptitle("평일 하루 재고 변화 프로파일 — 0시를 0으로 둔 상대 재고", x=0.005, y=0.995,
             ha="left", va="top", fontsize=13, fontweight="bold", color=vs.INK)
fig.text(0.005, 0.945, "앞 3개는 순유출 상위 대여소, 마지막은 대여량 1위 대여소(대조군). 파랑=이용만, 주황=재배치까지 반영.",
         fontsize=9.5, color=vs.INK2, va="top")
fig.tight_layout(rect=[0, 0, 1, 0.90])
vs.source(fig); fig.savefig(IMG / "04_inventory_profile.png"); plt.close(fig)
log("그림 04 완료", t0)

# ============================================================ 그림 05 지도
mp = met.dropna(subset=["lat", "lon"])
lim = mp["net_out_per_day"].abs().quantile(0.98)
fig, ax = plt.subplots(figsize=(8.6, 8.0))
sc = ax.scatter(mp["lon"], mp["lat"], c=mp["net_out_per_day"].clip(-lim, lim),
                s=np.sqrt(mp["total_events"]) * 0.28, cmap=vs.CMAP_DIV,
                vmin=-lim, vmax=lim, linewidths=0.4, edgecolors="#fcfcfb")
# 라벨은 서로 겹치지 않도록 지시선을 달고 상·하로 번갈아 배치한다
offsets = [(30, 20), (34, -30), (-34, 28), (-38, -26), (34, 26), (36, -34)]

def short(nm, k=12):
    t = nm[:k].rstrip(" (,·")            # 괄호가 열린 채 잘리면 지저분하다
    return t + ("…" if len(nm) > len(t) else "")

for (idx, r), off in zip(pd.concat([top.head(3), bot.head(3)]).iterrows(), offsets):
    ax.annotate(short(r["name"]), (r["lon"], r["lat"]), fontsize=7.5, color=vs.INK,
                textcoords="offset points", xytext=off,
                ha="left" if off[0] > 0 else "right",
                bbox=dict(boxstyle="round,pad=0.22", fc=vs.SURFACE, ec="none", alpha=0.85),
                arrowprops=dict(arrowstyle="-", lw=0.7, color=vs.MUTED,
                                shrinkA=0, shrinkB=3))
cb = fig.colorbar(sc, ax=ax, fraction=0.035, pad=0.02)
cb.set_label("일평균 (대/일) — 위쪽 빨강 순유출 / 아래쪽 파랑 순유입", fontsize=9, color=vs.INK2)
cb.outline.set_visible(False)
fig.text(0.005, -0.035, f"색 눈금은 ±{lim:.1f}대/일에서 잘랐다(상위 2% 극단값이 나머지를 눌러버리는 것을 막기 위함).",
         fontsize=8, color=vs.MUTED, ha="left", va="top")
ax.set_title("대여소 순유출·순유입 지도 — 빨강은 비는 곳(순유출), 파랑은 쌓이는 곳(순유입)")
vs.subtitle(ax, "점 크기는 총 이용량. 좌표는 원본의 X(위도)·Y(경도)를 바로잡아 사용했다.")
ax.set_xlabel("경도"); ax.set_ylabel("위도"); ax.grid(alpha=0.5)
ax.set_aspect(1 / np.cos(np.radians(36.35)))
vs.source(fig); fig.savefig(IMG / "05_netflow_map.png"); plt.close(fig)

# ============================================================ 그림 06 CPI × WR
j = met[met["monthly_rentals"] >= MIN_MONTHLY_RENTALS].dropna(subset=["CPI", "WR"])
cmap_t = {"출퇴근형": vs.SERIES[0], "여가형": vs.SERIES[1], "혼합/생활형": "#c3c2b7"}
fig, ax = plt.subplots(figsize=(8.4, 6.0))
for t in ["혼합/생활형", "출퇴근형", "여가형"]:
    g = j[j.use_type == t]
    ax.scatter(g["CPI"], g["WR"], s=18, color=cmap_t[t], alpha=0.75,
               linewidths=0.3, edgecolors="#fcfcfb", label=f"{t} ({len(g)}개)")
ax.axvline(0.25, color=vs.MUTED, ls=":", lw=1)
ax.text(0.252, ax.get_ylim()[1] * 0.86, " CPI = 0.25\n 하루에 고르게 퍼졌을 때의 기준선",
        fontsize=8, color=vs.MUTED, va="top")
ax.axhline(1.0, color=vs.MUTED, ls=":", lw=1)
ax.set_title("이용 유형 분류 — 첨두 집중도(CPI) × 주말비(WR)")
vs.subtitle(ax, "CPI = 평일 07–09·17–19시 대여 비중(6시간 = 하루의 25%). WR = 주말/평일 일평균 대여 비.")
ax.set_xlabel("CPI  (첨두 집중도)"); ax.set_ylabel("WR  (주말비)")
ax.grid(alpha=0.6); ax.legend(loc="upper right")
vs.source(fig); fig.savefig(IMG / "06_cpi_wr_scatter.png"); plt.close(fig)

# ============================================================ 그림 07 STL 분해
from statsmodels.tsa.seasonal import STL
si = s.interpolate(limit_direction="both")           # 분해에는 연속 시계열이 필요하다
res = STL(si, period=7, robust=True).fit()
fig, axes = plt.subplots(4, 1, figsize=(11, 7.2), sharex=True)
for ax, (dat, ttl, col) in zip(axes, [
        (si, "관측값", vs.BLUES[4]), (res.trend, "추세(trend)", vs.SERIES[0]),
        (res.seasonal, "주간 계절성(seasonal, 주기 7일)", vs.SERIES[2]),
        (res.resid, "잔차(residual)", vs.MUTED)]):
    ax.plot(dat.index, dat.values, lw=1.4, color=col)
    ax.set_ylabel(ttl, fontsize=9); ax.grid(axis="y")
    for a, b, _ in MISSING_NOTES:
        ax.axvspan(pd.Timestamp(a), pd.Timestamp(b) + pd.Timedelta(days=1),
                   color="#f0efec", zorder=0)
# 잔차가 가장 크게 음수인 날 = 이용량이 설명 밖으로 급감한 날(강수·한파 의심)
lo = res.resid.nsmallest(3)
axes[3].set_ylim(res.resid.min() * 1.35, res.resid.max() * 1.15)
for k, (d, v) in enumerate(lo.items()):
    axes[3].annotate(d.strftime("%Y-%m-%d"), (d, v), fontsize=8, color=vs.POS,
                     textcoords="offset points", xytext=(6, -12 - 12 * k),
                     arrowprops=dict(arrowstyle="-", lw=0.7, color=vs.POS))
fig.suptitle("일별 대여량 STL 분해 — 추세 / 주간 계절성 / 잔차", x=0.005, y=0.995,
             ha="left", va="top", fontsize=13, fontweight="bold", color=vs.INK)
fig.text(0.005, 0.952, "회색 띠는 결측 구간. 분해에는 연속 시계열이 필요해 이 구간만 선형 보간했고, 해석에서는 제외한다.",
         fontsize=9.5, color=vs.INK2, va="top")
fig.tight_layout(rect=[0, 0, 1, 0.91])
vs.source(fig); fig.savefig(IMG / "07_stl_decomposition.png"); plt.close(fig)

log("모든 그림 저장 완료 → images/")
print("\n[순유출 상위 5]")
print(top.head(5)[["station_id", "name", "net_out", "net_out_per_day",
                   "reloc_net_in", "unmet", "CPI", "WR", "use_type"]].to_string(index=False))
print("\n[대여량 상위 5의 순유출]")
print(met.nlargest(5, "rentals")[["name", "rentals", "net_out", "net_out_per_day",
                                  "CPI", "WR", "use_type"]].to_string(index=False))
