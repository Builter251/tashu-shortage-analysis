#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_client.py — 타슈 OpenAPI 호출·파싱 계층 (본 분석 코드와 완전히 독립)

명세 (https://bike.tashu.or.kr/noticeDetail.do?seq=28)
    GET  https://bikeapp.tashu.or.kr:50041/v1/openapi/station
    헤더  api-token: <발급받은 인증키>
    응답  JSON — 대여소 목록

    필드            의미
    ----------------------------------------------
    id              대여소 식별자
    name            한글 대여소명
    name_en/name_cn 영문/중문 대여소명
    x_pos           **위도**    ← 이력 CSV의 'X좌표'와 같은 관례(통념과 반대)
    y_pos           **경도**
    address         주소
    parking_count   **대여 가능 자전거 수량**

★ 주의: parking_count 는 '거치대 총 수'가 아니라 '지금 빌릴 수 있는 자전거 수'다.
   따라서 이 API로는 거치대 용량(capacity)을 알 수 없다. REPORT.md 한계 2번은 이 API로 해소되지 않는다.

★ 응답의 최상위 구조(배열인지 {"results": [...]} 같은 래퍼인지)는 실제 키를 받아야 확정된다.
   그래서 unwrap() 이 흔한 래퍼 이름을 순서대로 시도하고, 못 찾으면 명확히 실패한다.
   실제 응답을 처음 본 날 이 함수만 고치면 나머지는 그대로 동작한다.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ENDPOINT = "https://bikeapp.tashu.or.kr:50041/v1/openapi/station"
TOKEN_HEADER = "api-token"
TIMEOUT_SEC = 15
WRAPPER_KEYS = ("results", "result", "data", "items", "stations", "list", "body")


class ApiKeyMissing(RuntimeError):
    pass


def load_env(path: Path | None = None) -> dict[str, str]:
    """의존성을 늘리지 않기 위해 .env 를 직접 읽는다(python-dotenv 불필요)."""
    path = path or Path(__file__).resolve().parent / ".env"
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_token() -> str:
    """환경변수 → .env 순으로 키를 찾는다. 없으면 무엇을 해야 하는지 알려주고 멈춘다."""
    env = load_env()
    token = os.environ.get("TASHU_API_KEY") or env.get("TASHU_API_KEY", "")
    if not token or token.startswith("여기에"):
        raise ApiKeyMissing(
            "타슈 OpenAPI 키가 없습니다.\n"
            "  1) 타슈 앱 로그인 → 사이드메뉴 → 자전거정보 > OpenAPI 에서 신청\n"
            "  2) 승인되면  realtime/.env  에  TASHU_API_KEY=발급키  형태로 저장\n"
            "  (키 없이 코드 경로만 점검하려면:  python3 realtime/collect.py --selftest)")
    return token


def unwrap(payload: Any) -> list[dict]:
    """최상위가 배열이 아닐 수 있으므로 흔한 래퍼 키를 순서대로 벗겨 본다."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in WRAPPER_KEYS:
            v = payload.get(k)
            if isinstance(v, list):
                return v
        # 값이 하나뿐인 dict 라면 그 값이 목록일 가능성이 높다
        vals = [v for v in payload.values() if isinstance(v, list)]
        if len(vals) == 1:
            return vals[0]
    raise ValueError(
        f"응답에서 대여소 목록을 찾지 못했습니다. 최상위 타입={type(payload).__name__} "
        f"키={list(payload)[:10] if isinstance(payload, dict) else '-'}\n"
        f"→ api_client.unwrap() 에 실제 래퍼 키를 추가하세요.")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize(row: dict) -> dict:
    """API 한 건 → 저장 스키마 한 행. 필드명이 조금 달라도 견디도록 대안을 함께 본다."""
    def pick(*names):
        for n in names:
            if n in row and row[n] not in (None, ""):
                return row[n]
        return None

    lat = _num(pick("x_pos", "lat", "latitude"))
    lon = _num(pick("y_pos", "lon", "lng", "longitude"))
    # 대전 범위를 벗어나면 위경도가 뒤바뀐 것으로 보고 되돌린다
    if lat is not None and lon is not None and not (35.9 < lat < 36.8):
        lat, lon = lon, lat
    cnt = pick("parking_count", "bikes_available", "available", "cnt")
    return {
        "station_id": str(pick("id", "station_id", "stationId") or "").strip(),
        "station_name": pick("name", "station_name"),
        "bikes_available": int(_num(cnt)) if _num(cnt) is not None else None,
        "lat": lat,
        "lon": lon,
        "address": pick("address", "addr"),
        "raw_json": json.dumps(row, ensure_ascii=False),
    }


def fetch(token: str | None = None, endpoint: str = ENDPOINT) -> list[dict]:
    """실제 호출. 키가 없으면 ApiKeyMissing 을 던진다."""
    token = token or get_token()
    req = urllib.request.Request(endpoint, headers={
        TOKEN_HEADER: token,
        "Accept": "application/json",
        "User-Agent": "tashu-shortage-analysis/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} — 키가 유효한지, 호출 한도를 넘지 않았는지 확인하세요.") from e
    return [normalize(x) for x in unwrap(payload)]


def fake_payload(n: int = 5, seed: int = 0) -> list[dict]:
    """키 없이 파싱·저장 경로를 점검하기 위한 합성 응답 (명세의 필드명을 그대로 사용)."""
    import random
    rnd = random.Random(seed)
    return [{
        "id": f"ST{1000 + i:04d}",
        "name": f"테스트 대여소 {i}",
        "name_en": f"Test Station {i}",
        "x_pos": round(36.30 + rnd.random() * 0.15, 6),      # 위도
        "y_pos": round(127.32 + rnd.random() * 0.12, 6),     # 경도
        "address": f"대전광역시 유성구 테스트동 {i}",
        "parking_count": rnd.randint(0, 12),
    } for i in range(n)]
