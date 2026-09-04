#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""storage.py — 수집 결과 SQLite 적재 (스키마는 schema.md 와 1:1)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
DEFAULT_DB = Path(__file__).resolve().parent / "storage" / "tashu_status.sqlite"

DDL = """
CREATE TABLE IF NOT EXISTS station_status (
    collected_at     TEXT    NOT NULL,   -- KST, 초 단위 ISO8601
    station_id       TEXT    NOT NULL,
    station_name     TEXT,
    bikes_available  INTEGER,
    lat              REAL,
    lon              REAL,
    address          TEXT,
    raw_json         TEXT,
    PRIMARY KEY (collected_at, station_id)
);
CREATE INDEX IF NOT EXISTS idx_station_time ON station_status(station_id, collected_at);
CREATE TABLE IF NOT EXISTS collect_log (
    collected_at TEXT PRIMARY KEY,
    n_rows       INTEGER,
    status       TEXT,
    message      TEXT
);
"""


def connect(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    """쓰기용 연결.

    주의: 네트워크 드라이브·클라우드 동기화 폴더·컨테이너 마운트 위에서는 SQLite 의 파일 잠금이
    동작하지 않아 'disk I/O error' 가 난다. 그런 환경이면 로컬 경로를 --db 로 지정해야 한다.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        con = sqlite3.connect(db_path)
        con.executescript(DDL)
        return con
    except sqlite3.OperationalError as e:
        raise sqlite3.OperationalError(
            f"{e}\n  → '{db_path}' 위치가 SQLite 쓰기를 지원하지 않는 것 같습니다"
            f"(네트워크/동기화/마운트 폴더).\n"
            f"     로컬 디스크 경로를 지정하세요:  --db ~/tashu_status.sqlite") from e


def connect_readonly(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    """읽기 전용 연결. 잠금을 요구하지 않아 마운트 폴더에서도 대개 열린다."""
    uri = f"file:{Path(db_path).as_posix()}?mode=ro&immutable=1&nolock=1"
    return sqlite3.connect(uri, uri=True)


def now_kst() -> str:
    return datetime.now(KST).replace(microsecond=0).isoformat()


def save(con: sqlite3.Connection, rows: list[dict], collected_at: str | None = None) -> int:
    collected_at = collected_at or now_kst()
    payload = [(collected_at, r["station_id"], r.get("station_name"), r.get("bikes_available"),
                r.get("lat"), r.get("lon"), r.get("address"), r.get("raw_json"))
               for r in rows if r.get("station_id")]
    con.executemany(
        "INSERT OR REPLACE INTO station_status "
        "(collected_at, station_id, station_name, bikes_available, lat, lon, address, raw_json) "
        "VALUES (?,?,?,?,?,?,?,?)", payload)
    con.execute("INSERT OR REPLACE INTO collect_log VALUES (?,?,?,?)",
                (collected_at, len(payload), "OK", ""))
    con.commit()
    return len(payload)


def log_failure(con: sqlite3.Connection, message: str) -> None:
    con.execute("INSERT OR REPLACE INTO collect_log VALUES (?,?,?,?)",
                (now_kst(), 0, "FAIL", message[:500]))
    con.commit()


def summary(con: sqlite3.Connection) -> dict:
    q = lambda s: con.execute(s).fetchone()
    n, ns, t0, t1 = q("""SELECT count(*), count(DISTINCT station_id),
                                min(collected_at), max(collected_at) FROM station_status""")
    snaps = q("SELECT count(DISTINCT collected_at) FROM station_status")[0]
    return {"rows": n, "stations": ns, "snapshots": snaps, "first": t0, "last": t1}
