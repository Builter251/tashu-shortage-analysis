#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect.py — 타슈 OpenAPI 실시간 현황 수집기 (본 분석과 완전 분리, 단독 실행)

키가 없어도 **코드 경로는 지금 전부 검증할 수 있다.** 키가 나오면 .env 에 넣는 것만으로 동작한다.

    python3 realtime/collect.py --selftest        # 키 불필요. 파싱·저장·조회 전 경로 점검
    python3 realtime/collect.py --check           # 키 불필요. 엔드포인트 도달 여부만 확인
    python3 realtime/collect.py --once            # 키 필요. 1회 수집
    python3 realtime/collect.py --loop            # 키 필요. 주기 수집 (기본 300초)
    python3 realtime/collect.py --status          # 지금까지 얼마나 쌓였는지

주기 실행 등록 (macOS / Linux):
    */5 * * * * cd /path/to/project && /usr/bin/python3 realtime/collect.py --once >> realtime/storage/collect.log 2>&1
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_client as api
import storage as st


def cmd_selftest() -> int:
    """키 없이 전 경로 점검: 합성 응답 → 정규화 → 임시 DB 저장 → 재조회."""
    import tempfile
    print("[selftest] 합성 응답으로 파싱·저장 경로를 점검합니다 (API 키 불필요)")
    rows = [api.normalize(r) for r in api.fake_payload(5)]
    assert all(r["station_id"] for r in rows), "station_id 파싱 실패"
    assert all(r["bikes_available"] is not None for r in rows), "parking_count 파싱 실패"
    for r in rows:
        assert 35.9 < r["lat"] < 36.8, f"위도 범위 이상: {r['lat']}"
        assert 126.9 < r["lon"] < 127.9, f"경도 범위 이상: {r['lon']}"
    print(f"  정규화 OK — {len(rows)}건, 예시: {rows[0]['station_id']} "
          f"'{rows[0]['station_name']}' {rows[0]['bikes_available']}대 "
          f"({rows[0]['lat']}, {rows[0]['lon']})")

    # 래퍼 형태가 무엇으로 오든 벗겨지는지
    for wrap in [api.fake_payload(3), {"results": api.fake_payload(3)}, {"data": api.fake_payload(3)}]:
        assert len(api.unwrap(wrap)) == 3
    print("  래퍼 해제 OK — 배열 / results / data 모두 처리")

    with tempfile.TemporaryDirectory() as d:
        con = st.connect(Path(d) / "t.sqlite")
        n1 = st.save(con, rows, "2026-09-04T10:00:00+09:00")
        n2 = st.save(con, [api.normalize(r) for r in api.fake_payload(5, seed=1)],
                     "2026-09-04T10:05:00+09:00")
        s = st.summary(con)
        assert s["rows"] == n1 + n2 and s["snapshots"] == 2, s
        print(f"  저장·조회 OK — {s['rows']}행 / 스냅샷 {s['snapshots']}개 / 대여소 {s['stations']}개")
    print("[selftest] 통과. 키를 .env 에 넣으면 --once 로 바로 수집됩니다.")
    return 0


def cmd_check() -> int:
    """키 없이 엔드포인트 도달 여부만 본다. 401/403 이 오면 '살아 있다'는 뜻이다."""
    print(f"[check] {api.ENDPOINT}")
    req = urllib.request.Request(api.ENDPOINT, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=api.TIMEOUT_SEC) as r:
            print(f"  HTTP {r.status} — 인증 없이도 응답. 본문 앞부분: {r.read(200)!r}")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} — 엔드포인트는 살아 있습니다(인증이 필요하다는 뜻).")
    except Exception as e:
        print(f"  도달 실패: {type(e).__name__}: {e}")
        print("  (이 환경의 네트워크 정책 때문일 수 있습니다. 로컬에서 다시 확인하세요.)")
        return 1
    return 0


def cmd_once(db: Path) -> int:
    con = st.connect(db)
    try:
        rows = api.fetch()
    except api.ApiKeyMissing as e:
        print(e); return 2
    except Exception as e:
        st.log_failure(con, str(e))
        print(f"[수집 실패] {e}"); return 1
    n = st.save(con, rows)
    print(f"[{st.now_kst()}] {n}개 대여소 저장 · "
          f"총 가용 자전거 {sum(r['bikes_available'] or 0 for r in rows):,}대")
    return 0


def cmd_loop(db: Path, interval: int) -> int:
    print(f"[loop] {interval}초 간격 수집 시작. Ctrl+C 로 중단.")
    while True:
        try:
            cmd_once(db)
        except KeyboardInterrupt:
            print("\n중단"); return 0
        except Exception as e:      # 한 번 실패해도 루프는 살아 있어야 한다
            print(f"[예외] {e}")
        time.sleep(interval)


def cmd_status(db: Path) -> int:
    if not Path(db).exists():
        print(f"아직 수집된 데이터가 없습니다 ({db})"); return 0
    s = st.summary(st.connect(db))
    print(f"수집 현황  스냅샷 {s['snapshots']:,}개 / 행 {s['rows']:,} / 대여소 {s['stations']:,}")
    print(f"           {s['first']}  ~  {s['last']}")
    if s["snapshots"]:
        days = s["snapshots"] * 5 / 60 / 24
        print(f"           약 {days:.1f}일치 (5분 간격 기준) — 검증 착수 권장 기준은 14일")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="타슈 OpenAPI 실시간 현황 수집기")
    g = p.add_mutually_exclusive_group(required=True)
    for f in ("selftest", "check", "once", "loop", "status"):
        g.add_argument(f"--{f}", action="store_true")
    p.add_argument("--db", default=str(st.DEFAULT_DB))
    p.add_argument("--interval", type=int,
                   default=int(api.load_env().get("TASHU_POLL_INTERVAL_SEC", 300)))
    a = p.parse_args()
    db = Path(a.db)
    if a.selftest: return cmd_selftest()
    if a.check:    return cmd_check()
    if a.once:     return cmd_once(db)
    if a.loop:     return cmd_loop(db, a.interval)
    if a.status:   return cmd_status(db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
