#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04_forecast.py — 재고 소진 의심 구간 정량화 + 다음 1시간 순유출 예측(베이스라인 사다리)

Part A. 재고 소진 의심 구간 — 포아송 기준선 대비 '초과 0건'
  관측 대여 = min(실제 수요, 재고) 이므로 '재고 0'은 데이터에 0으로만 남는다.
  직접 관측할 수 없어 대리지표로 추정하는데, 순진하게 '수요 슬롯인데 0건'을 세면
  **우연히 손님이 없었던 시간까지 전부 재고 소진으로 잡힌다.**
  (1차 시도에서 실제로 그랬다: λ≈1 슬롯의 2시간 연속 0 확률이 이론상 13.5%인데
   산출된 재고 소진율 중앙값이 13.45%로 거의 일치했다 = 노이즈만 센 것.)

  그래서 기준선을 세운다. 대여 건수가 평균 λ 인 포아송 과정이라면
  0건이 나올 확률은 exp(-λ) 다. 이보다 **얼마나 더 자주 0인가**가 재고 소진 신호다.

      초과 0건 비율 = (실제 0건 시간 − Σ exp(-λ_t)) / 수요 슬롯 시간 × 100

  단, λ 를 (요일 × 시간대) 평균으로 고정하면 안 된다. 이용량은 계절에 따라 3.2배
  변하므로 겨울의 한산한 0건까지 재고 소진으로 잡힌다. 그래서 λ 를 그날의
  도시 전체 활동량으로 보정한다.

      λ_t = 기본기대(대여소, 요일, 시간대) × 그날의 도시활동지수
      도시활동지수 = 그날 대전 전체 대여 건수 ÷ 전체 평균

  이 지수 하나로 계절·요일·날씨·행사 효과가 한꺼번에 흡수된다.

Part B. 다음 1시간 순유출 예측 (미션 보너스 5-2-B)
  B0 Naive         : 168시간 전(지난주 같은 요일·같은 시각) 값
  B1 Seasonal Mean : 최근 8주의 (요일 × 시간대) 평균
  B2 회귀(GBM)     : lag / rolling / 순환 인코딩 피처 → HistGradientBoostingRegressor
  검증: 마지막 4주 홀드아웃(시간 순서 유지), MAE·RMSE, B0 대비 개선율

실행:  python3 src/04_forecast.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

sys.path.insert(0, str(Path(__file__).resolve().parent))
import viz_style as vs

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
IMG = ROOT / "images"
vs.apply()

# ── Part A 설정 ─────────────────────────────────────────────────────────────
DEMAND_THRESH = 2.0        # (요일×시간대) 평균 대여 λ 가 이 값 이상인 '수요가 있는 슬롯'
                           # λ=2 면 우연한 0건 확률이 13.5% → 신호가 노이즈 위로 올라온다
MIN_RUN = 3                # 연속 몇 시간 이상이어야 '지속 소진'로 볼지
MIN_DEMAND_HOURS = 200     # 수요 슬롯이 이보다 적으면 비율이 불안정 → 순위에서 제외
MIN_MONTHLY = 100          # 월 대여 이 미만 대여소는 기대값이 불안정 → 제외

# ── Part B 설정 ─────────────────────────────────────────────────────────────
N_FORECAST_STATIONS = 20   # 순유출 상위 N개 대여소
HOLDOUT_WEEKS = 4
SEASONAL_WEEKS = 8
LAGS = [1, 2, 3, 24, 168]
ROLLS = [3, 24, 168]


def log(m, t0=None):
    print(f"[04] {m}" + (f"  ({time.time()-t0:.1f}s)" if t0 else ""), flush=True)


con = duckdb.connect()
con.execute("PRAGMA memory_limit='2GB'")
con.execute("PRAGMA threads=4")
con.execute("PRAGMA temp_directory='/tmp/duckdb_tmp'")
con.execute(f"CREATE VIEW h AS SELECT * FROM read_parquet('{PROC/'station_hourly.parquet'}')")
master = pd.read_csv(PROC / "station_master.csv")
met = pd.read_csv(PROC / "station_metrics.csv")
con.register("m", master)

# 관측일 = 실제로 데이터가 있는 날. 결측일을 분모/시계열에 넣으면 안 된다.
obs = con.execute("""
    SELECT h.ts::DATE AS d, sum(h.rentals_real) AS n
    FROM h JOIN m ON h.station_id=m.station_id
    WHERE m.station_type <> 'CONTROL' GROUP BY 1 HAVING sum(h.rentals_real) > 0""").df()
obs["d"] = pd.to_datetime(obs["d"])
obsdays = obs[["d"]].sort_values("d")
con.register("obsdays", obsdays)
log(f"관측일 {len(obsdays)}일 ({obsdays.d.min():%Y-%m-%d} ~ {obsdays.d.max():%Y-%m-%d})")

# ============================================================================
# Part A — 재고 소진 의심 구간
# ============================================================================
t0 = time.time()
sel = met[(met.monthly_rentals >= MIN_MONTHLY)][["station_id", "name", "gu",
                                                 "net_out_per_day", "rentals"]]
# 대여소마다 존재한 기간이 다르다. 개설 전·폐쇄 후 구간을 그대로 두면
# 그 시기의 0건이 전부 '재고 소진'으로 잡힌다(신설 대여소가 순위를 독식하게 된다).
sel = sel.merge(master[["station_id", "first_seen", "last_seen"]], on="station_id", how="left")
sel["first_seen"] = pd.to_datetime(sel["first_seen"])
sel["last_seen"] = pd.to_datetime(sel["last_seen"])
con.register("sel", sel)

# 도시 전체 활동 지수 — 그날이 얼마나 붐볐는지. 계절·날씨·행사를 한 번에 흡수한다.
day_idx = obs.copy()
day_idx["day_index"] = day_idx["n"] / day_idx["n"].mean()
con.register("day_idx", day_idx[["d", "day_index"]])

con.execute("""
    CREATE TABLE dense AS
    SELECT s.station_id, c.ts, coalesce(h.rentals_real, 0)::INTEGER AS rentals,
           coalesce(h.net_flow, 0)::INTEGER AS net_flow,
           di.day_index
    FROM sel s
    CROSS JOIN (SELECT unnest(generate_series(TIMESTAMP '2024-08-01 00:00:00',
                                              TIMESTAMP '2026-03-31 23:00:00',
                                              INTERVAL 1 HOUR)) AS ts) c
    JOIN obsdays od ON c.ts::DATE = od.d
    JOIN day_idx di ON c.ts::DATE = di.d
    LEFT JOIN h ON h.station_id = s.station_id AND h.ts = c.ts
    WHERE c.ts::DATE BETWEEN s.first_seen AND s.last_seen   -- 그 대여소가 실제로 존재한 기간만
""")
n_dense = con.execute("SELECT count(*) FROM dense").fetchone()[0]
full = len(sel) * 572 * 24
log(f"희소 패널 → 조밀 패널 복원: 대여소 {len(sel):,}개 = {n_dense:,}행 "
    f"(대여소 존재 기간으로 제한 — 전 기간이면 {full:,}행)", t0)

t0 = time.time()
con.execute(f"""
    CREATE TABLE flagged AS
    WITH expect AS (
        -- 도시활동지수로 나눈 뒤 평균 → 계절 효과가 제거된 '기본 기대 대여'
        SELECT station_id, dayofweek(ts) AS dw, hour(ts) AS hh,
               avg(rentals / day_index) AS base_lam
        FROM dense GROUP BY 1, 2, 3),
    joined AS (
        SELECT d.station_id, d.ts, d.rentals,
               e.base_lam * d.day_index AS lam,   -- 그날의 붐빔을 다시 곱해 되돌린다
               (e.base_lam >= {DEMAND_THRESH}) AS demand_slot,
               (e.base_lam >= {DEMAND_THRESH} AND d.rentals = 0) AS zero_hit
        FROM dense d
        JOIN expect e ON d.station_id = e.station_id
                     AND dayofweek(d.ts) = e.dw AND hour(d.ts) = e.hh),
    grouped AS (
        SELECT *,
               row_number() OVER (PARTITION BY station_id ORDER BY ts)
             - row_number() OVER (PARTITION BY station_id, zero_hit ORDER BY ts) AS grp
        FROM joined)
    SELECT *, count(*) OVER (PARTITION BY station_id, zero_hit, grp) AS run_len
    FROM grouped
""")
stock = con.execute(f"""
    SELECT station_id,
           count(*) FILTER (WHERE demand_slot)                        AS demand_hours,
           count(*) FILTER (WHERE zero_hit)                           AS zero_hours,
           sum(CASE WHEN demand_slot THEN exp(-lam) ELSE 0 END)       AS expected_zero_hours,
           count(*) FILTER (WHERE zero_hit AND run_len >= {MIN_RUN})  AS long_run_hours,
           max(run_len) FILTER (WHERE zero_hit)                       AS longest_run
    FROM flagged GROUP BY 1""").df()
stock["excess_zero_rate"] = (100 * (stock.zero_hours - stock.expected_zero_hours)
                             / stock.demand_hours.replace(0, np.nan)).round(2)
stock["zero_ratio"] = (stock.zero_hours / stock.expected_zero_hours.replace(0, np.nan)).round(2)
stock["naive_rate"] = (100 * stock.zero_hours / stock.demand_hours.replace(0, np.nan)).round(2)
stock = stock.merge(sel, on="station_id", how="left")
stock["rankable"] = stock.demand_hours >= MIN_DEMAND_HOURS
stock = stock.sort_values("excess_zero_rate", ascending=False)
stock.to_csv(PROC / "stockout_by_station.csv", index=False, encoding="utf-8-sig")

rk = stock[stock.rankable]
log(f"재고 소진 산출 — 순위 가능 대여소 {len(rk):,}개 / "
    f"초과 0건 비율 중앙값 {rk.excess_zero_rate.median():.2f}%p "
    f"(순진한 0건 비율 중앙값 {rk.naive_rate.median():.2f}%)", t0)
log(f"  초과 0건 비율 vs 일평균 순유출 상관: {rk.excess_zero_rate.corr(rk.net_out_per_day):.3f}"
    f"   (순진한 지표는 {rk.naive_rate.corr(rk.net_out_per_day):.3f})")
rk = rk.assign(reloc_net_in=rk.station_id.map(met.set_index("station_id")["reloc_net_in"]))
log(f"  초과 0건 비율 vs 재배치 순유입 상관: {rk.excess_zero_rate.corr(rk.reloc_net_in):.3f}"
    f"  (자주 채워주는 곳일수록 비는 곳이라면 양수여야 한다)")

hourly_dist = con.execute(f"""
    SELECT hour(ts) AS hh,
           (count(*) FILTER (WHERE zero_hit)
            - sum(CASE WHEN demand_slot THEN exp(-lam) ELSE 0 END))
           / nullif(count(*) FILTER (WHERE demand_slot), 0) * 100 AS rate
    FROM flagged GROUP BY 1 ORDER BY 1""").df()

# ── 그림 09 ────────────────────────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(12, 4.4), gridspec_kw={"width_ratios": [1.15, 1]})
ax = axes[0]
ax.bar(hourly_dist.hh, hourly_dist.rate, color=vs.SERIES[0], width=0.72)
pk = hourly_dist.loc[hourly_dist.rate.idxmax()]
ax.bar([pk.hh], [pk.rate], color=vs.POS, width=0.72)
ax.annotate(f"최대 {int(pk.hh)}시 {pk.rate:.1f}%", (pk.hh, pk.rate), fontsize=9, color=vs.POS,
            textcoords="offset points", xytext=(0, 6), ha="center")
ax.set_xticks(range(0, 24, 3)); ax.set_xticklabels([f"{x}시" for x in range(0, 24, 3)])
ax.axhline(0, color=vs.BASELINE, lw=1)
ax.set_title("시간대별 '초과 0건' 비율 — 우연으로 설명되지 않는 몫")
ax.set_ylabel("초과 0건 비율 (%p)")
vs.subtitle(ax, f"수요 슬롯(요일×시간대 평균 대여 λ ≥ {DEMAND_THRESH}건)에서 실제 0건 비율 − 포아송 기대치 exp(−λ)")
ax.grid(axis="y")

ax = axes[1]
tp = rk.dropna(subset=["excess_zero_rate"]).nlargest(12, "excess_zero_rate").iloc[::-1]
ax.barh(range(len(tp)), tp.excess_zero_rate, color=vs.POS, height=0.72)
ax.set_yticks(range(len(tp)))
ax.set_yticklabels([n[:20] for n in tp.name], fontsize=8.5, color=vs.INK2)
for i, (v, no) in enumerate(zip(tp.excess_zero_rate, tp.net_out_per_day)):
    ax.text(v + 0.4, i, f"{v:.1f}%p  (순유출 {no:+.2f})", va="center", fontsize=8, color=vs.INK2)
ax.set_xlim(0, tp.excess_zero_rate.max() * 1.6)
ax.set_title("초과 0건 비율 상위 12 대여소")
ax.set_xlabel("초과 0건 비율 (%p)"); ax.grid(axis="x")
fig.tight_layout()
vs.source(fig); fig.savefig(IMG / "09_stockout.png"); plt.close(fig)
log("그림 09 저장")

# ----------------------------------------------------------------------------
# Part A-2 — 결정적 검정: 재고가 낮을 때 실제로 대여가 덜 일어나는가
#
# '초과 0건'만으로는 재고 소진과 단순한 수요 변동을 구분할 수 없다(상관이 0에 가깝다).
# 그래서 2단계에서 복원한 상대 재고를 직접 쓴다.
#   · 누적 재고 = Σ net_flow (반납 − 대여 + 재배치유입 − 재배치유출)
#   · 누적값은 미탐지 재배치 때문에 장기 드리프트가 있으므로,
#     최근 7일(168시간) 평균을 빼서 '평소 대비 지금 얼마나 낮은가'만 남긴다.
#   · 그 편차를 대여소별 십분위로 나누고, 각 구간에서  실제 대여 ÷ 기대 대여  를 본다.
#
# 재고 소진이 실재한다면 재고가 가장 낮은 구간에서 이 비율이 뚜렷이 1 아래로 떨어져야 한다.
#
# ★ 인과 방향 주의 — 1차 시도에서 정확히 여기서 틀렸다.
#   같은 시각의 재고 편차로 묶으면 "많이 빌려 갔기 때문에 재고가 낮아진" 시간이
#   저재고 구간에 몰린다(역인과). 실제로 최저 분위의 비율이 1.057로 가장 높게 나왔다.
#   → 반드시 **직전 시각의 재고**로 묶어야 한다. 원인이 결과보다 앞서야 한다.
# ----------------------------------------------------------------------------
t0 = time.time()
con.execute(f"""
    CREATE TABLE invtest AS
    WITH cum AS (
        SELECT station_id, ts, rentals, day_index,
               sum(net_flow) OVER (PARTITION BY station_id ORDER BY ts) AS inv
        FROM dense),
    dev AS (
        SELECT *, inv - avg(inv) OVER (PARTITION BY station_id ORDER BY ts
                                       ROWS BETWEEN 168 PRECEDING AND CURRENT ROW) AS inv_dev
        FROM cum),
    expect AS (
        SELECT station_id, dayofweek(ts) AS dw, hour(ts) AS hh,
               avg(rentals / day_index) AS base_lam
        FROM dense GROUP BY 1, 2, 3)
    SELECT d.station_id, d.ts, d.rentals,
           -- 이번 시간의 대여를 설명하는 것은 '직전 시각의 재고 상태'다
           lag(d.inv_dev) OVER (PARTITION BY d.station_id ORDER BY d.ts) AS inv_dev_prev,
           e.base_lam * d.day_index AS lam
    FROM dev d
    JOIN expect e ON d.station_id = e.station_id
                 AND dayofweek(d.ts) = e.dw AND hour(d.ts) = e.hh
    WHERE e.base_lam >= {DEMAND_THRESH} AND d.inv_dev IS NOT NULL
""")
con.execute("DELETE FROM invtest WHERE inv_dev_prev IS NULL")
curve = con.execute("""
    SELECT decile,
           sum(rentals) AS actual, sum(lam) AS expected,
           sum(rentals) / sum(lam) AS ratio,
           count(*) AS n,
           avg(inv_dev_prev) AS mean_dev
    FROM (SELECT *, ntile(10) OVER (PARTITION BY station_id ORDER BY inv_dev_prev) AS decile
          FROM invtest)
    GROUP BY 1 ORDER BY 1""").df()
log("재고 편차 십분위별  실제 대여 ÷ 기대 대여", t0)
for _, r in curve.iterrows():
    bar = "█" * int(round(r.ratio * 30))
    log(f"   {int(r.decile):2d}분위 (평균 편차 {r.mean_dev:+6.2f}대)  {r.ratio:5.3f}  {bar}")
gap = curve.ratio.iloc[0] / curve.ratio.iloc[-1]
log(f"   최저 분위 / 최고 분위 = {gap:.3f}  → 1보다 뚜렷이 작으면 재고 소진이 실재한다는 증거")
curve.to_csv(PROC / "inventory_response_curve.csv", index=False, encoding="utf-8-sig")

# ── 그림 10 ────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(9.2, 4.6))
cols = [vs.POS if r < 0.95 else (vs.NEG if r > 1.05 else "#c3c2b7") for r in curve.ratio]
ax.bar(curve.decile, curve.ratio, color=cols, width=0.72)
ax.axhline(1.0, color=vs.INK, lw=1.2)
ax.text(10.5, 1.0, " 기대치", fontsize=8.5, color=vs.INK, va="center")
for _, r in curve.iterrows():
    ax.text(r.decile, r.ratio, f"{r.ratio:.2f}", ha="center", va="bottom",
            fontsize=8.5, color=vs.INK2)
ax.set_xticks(range(1, 11))
ax.set_xticklabels([f"{i}\n({d:+.1f}대)" for i, d in zip(curve.decile, curve.mean_dev)], fontsize=8)
ax.set_xlabel("직전 시각의 상대 재고 편차 십분위 (왼쪽 = 평소보다 재고가 적은 상태)")
ax.set_ylabel("실제 대여 ÷ 기대 대여")
ax.set_title("재고가 적을 때 대여가 실제로 덜 일어나는가 — 재고 소진의 직접 증거")
vs.subtitle(ax, "수요 슬롯만 대상. 원인이 결과보다 앞서도록 직전 시각의 재고로 묶었다. 기대 대여 = (요일×시간대) 기본기대 × 그날의 도시활동지수.")
ax.grid(axis="y")
vs.source(fig); fig.savefig(IMG / "10_inventory_response.png"); plt.close(fig)
log("그림 10 저장")

# ============================================================================
# Part B — 예측
# ============================================================================
t0 = time.time()
targets = met.nlargest(N_FORECAST_STATIONS, "net_out_per_day")["station_id"].tolist()
ids = ",".join(f"'{x}'" for x in targets)
panel = con.execute(f"""
    SELECT s.station_id, c.ts,
           coalesce(h.net_out, 0)::INTEGER AS net_out,
           (od.d IS NOT NULL) AS observed
    FROM (SELECT unnest([{ids}]) AS station_id) s
    CROSS JOIN (SELECT unnest(generate_series(TIMESTAMP '2024-08-01 00:00:00',
                                              TIMESTAMP '2026-03-31 23:00:00',
                                              INTERVAL 1 HOUR)) AS ts) c
    LEFT JOIN obsdays od ON c.ts::DATE = od.d
    LEFT JOIN h ON h.station_id = s.station_id AND h.ts = c.ts
    ORDER BY s.station_id, c.ts""").df()
panel["ts"] = pd.to_datetime(panel["ts"])
# 결측일의 값은 0이 아니라 '모름'이다. 시간 축의 연속성은 유지하되 값만 비운다.
panel.loc[~panel["observed"], "net_out"] = np.nan
log(f"예측용 패널: {len(targets)}개 대여소 × {panel.ts.nunique():,}시간 = {len(panel):,}행", t0)


def build_features(g: pd.DataFrame) -> pd.DataFrame:
    """시간 축이 연속이라는 전제 위에서만 lag/rolling 이 의미를 갖는다."""
    g = g.sort_values("ts").copy()
    y = g["net_out"]
    for L in LAGS:
        g[f"lag_{L}"] = y.shift(L)
    for R in ROLLS:
        g[f"roll_mean_{R}"] = y.shift(1).rolling(R, min_periods=max(2, R // 3)).mean()
        g[f"roll_std_{R}"] = y.shift(1).rolling(R, min_periods=max(2, R // 3)).std()
    hh, dw = g.ts.dt.hour, g.ts.dt.dayofweek
    g["hour_sin"], g["hour_cos"] = np.sin(2 * np.pi * hh / 24), np.cos(2 * np.pi * hh / 24)
    g["dow_sin"], g["dow_cos"] = np.sin(2 * np.pi * dw / 7), np.cos(2 * np.pi * dw / 7)
    g["is_weekend"] = (dw >= 5).astype(int)
    g["hh"], g["dw"] = hh, dw
    return g


feat = pd.concat([build_features(g) for _, g in panel.groupby("station_id")], ignore_index=True)

split = feat.ts.max() - pd.Timedelta(weeks=HOLDOUT_WEEKS)
log(f"홀드아웃 경계: {split:%Y-%m-%d %H:%M} 이후 {HOLDOUT_WEEKS}주 (시간 순서 유지 분할)")

# ── B1: 학습 구간의 최근 8주 (요일×시간대) 평균 ─────────────────────────────
recent = feat[(feat.ts <= split) & (feat.ts > split - pd.Timedelta(weeks=SEASONAL_WEEKS))]
seasonal = (recent.groupby(["station_id", "dw", "hh"])["net_out"].mean()
            .rename("b1_pred").reset_index())
feat = feat.merge(seasonal, on=["station_id", "dw", "hh"], how="left")
feat["b0_pred"] = feat["lag_168"]

FEATS = ([f"lag_{L}" for L in LAGS]
         + [f"roll_mean_{R}" for R in ROLLS] + [f"roll_std_{R}" for R in ROLLS]
         + ["hour_sin", "hour_cos", "dow_sin", "dow_cos", "is_weekend"])

train = feat[(feat.ts <= split) & feat.net_out.notna()].dropna(subset=["lag_168"])
test = feat[(feat.ts > split) & feat.net_out.notna()].copy()
log(f"학습 {len(train):,}행 / 검증 {len(test):,}행")

model = HistGradientBoostingRegressor(
    max_iter=300, learning_rate=0.06, max_depth=6,
    early_stopping=True, validation_fraction=0.15, random_state=42)
model.fit(train[FEATS], train["net_out"])
test["b2_pred"] = model.predict(test[FEATS])


def score(y, p):
    ok = y.notna() & p.notna()
    e = (y[ok] - p[ok])
    return len(e), float(e.abs().mean()), float(np.sqrt((e ** 2).mean()))


rows = []
for tag, col in [("B0  Naive (168시간 전)", "b0_pred"),
                 ("B1  Seasonal Mean (최근 8주 요일×시간대)", "b1_pred"),
                 ("B2  GBM (lag·rolling·순환 인코딩)", "b2_pred")]:
    n, mae, rmse = score(test["net_out"], test[col])
    rows.append({"모델": tag, "평가행": n, "MAE": round(mae, 4), "RMSE": round(rmse, 4)})
res = pd.DataFrame(rows)
base = res.loc[0, "MAE"]
res["B0 대비 MAE 개선"] = ((base - res["MAE"]) / base * 100).round(1).astype(str) + "%"
res.to_csv(PROC / "forecast_metrics.csv", index=False, encoding="utf-8-sig")
print("\n" + res.to_string(index=False))

# 참고: '항상 0으로 예측'하는 무지성 기준선 (순유출은 평균 0 근처라 이게 은근히 강하다)
n0, mae0, rmse0 = score(test["net_out"], pd.Series(0.0, index=test.index))
print(f"\n(참고) 항상 0 예측: MAE {mae0:.4f} / RMSE {rmse0:.4f}")

# ── 그림 08 ────────────────────────────────────────────────────────────────
sid = targets[0]
sname = met.loc[met.station_id == sid, "name"].iloc[0]
g = test[test.station_id == sid].sort_values("ts")
wk = g[g.ts >= g.ts.max() - pd.Timedelta(days=7)]

fig, axes = plt.subplots(2, 1, figsize=(11.5, 6.6),
                         gridspec_kw={"height_ratios": [1.5, 1]})
ax = axes[0]
ax.axhline(0, color=vs.BASELINE, lw=1)
ax.plot(wk.ts, wk.net_out, color=vs.INK, lw=1.8, label="실제")
ax.plot(wk.ts, wk.b0_pred, color=vs.SERIES[0], lw=1.4, ls=":", label="B0 Naive")
ax.plot(wk.ts, wk.b2_pred, color=vs.SERIES[1], lw=1.6, ls="--", label="B2 GBM")
ax.set_title(f"홀드아웃 마지막 7일 — {sname[:24]} 시간별 순유출 예측")
vs.subtitle(ax, "순유출 = 대여 − 반납. 양수면 그 시간에 자전거가 빠져나갔다는 뜻이다.")
ax.set_ylabel("순유출 (대/시간)"); ax.grid(axis="y"); ax.legend(ncol=3, loc="upper left")

ax = axes[1]
x = np.arange(len(res))
ax.bar(x - 0.19, res.MAE, width=0.36, color=vs.SERIES[0], label="MAE")
ax.bar(x + 0.19, res.RMSE, width=0.36, color=vs.SERIES[2], label="RMSE")
ax.axhline(mae0, color=vs.POS, lw=1.2, ls="--")
ax.text(len(res) - 0.5, mae0, f" '항상 0' MAE {mae0:.3f}", color=vs.POS, fontsize=8.5, va="bottom")
for i, (a, b) in enumerate(zip(res.MAE, res.RMSE)):
    ax.text(i - 0.19, a, f"{a:.3f}", ha="center", va="bottom", fontsize=8, color=vs.INK2)
    ax.text(i + 0.19, b, f"{b:.3f}", ha="center", va="bottom", fontsize=8, color=vs.INK2)
ax.set_xticks(x); ax.set_xticklabels(["B0\nNaive", "B1\nSeasonal Mean", "B2\nGBM"], fontsize=9)
ax.set_title(f"베이스라인 사다리 — 순유출 상위 {N_FORECAST_STATIONS}개 대여소, 마지막 {HOLDOUT_WEEKS}주 백테스트")
ax.set_ylabel("오차 (대/시간)"); ax.grid(axis="y"); ax.legend(ncol=2, loc="upper right")
fig.tight_layout()
vs.source(fig); fig.savefig(IMG / "08_forecast.png"); plt.close(fig)
log("그림 08 저장")

print("\n[초과 0건 비율 상위 10]")
print(rk.nlargest(10, "excess_zero_rate")[
    ["station_id", "name", "gu", "demand_hours", "naive_rate", "excess_zero_rate",
     "zero_ratio", "longest_run", "net_out_per_day"]].to_string(index=False))
print("\n[비교] 순진한 지표 상위 5 — 대부분 우연이다")
print(rk.nlargest(5, "naive_rate")[
    ["name", "naive_rate", "excess_zero_rate", "net_out_per_day"]].to_string(index=False))
