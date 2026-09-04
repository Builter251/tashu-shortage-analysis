# 실시간 수집 스키마 · API 명세

본 모듈은 이력 분석과 **독립**이다. 분석 코드는 이 폴더를 import 하지 않으며,
연결은 `src/05_validate_with_realtime.py` 한 곳에서만 일어난다.

## API 명세 (확인 완료)

출처: <https://bike.tashu.or.kr/noticeDetail.do?seq=28>

```
GET  https://bikeapp.tashu.or.kr:50041/v1/openapi/station
헤더  api-token: <발급받은 인증키>
응답  JSON — 대여소 목록
```

| 응답 필드 | 의미 |
|---|---|
| `id` | 대여소 식별자 |
| `name` / `name_en` / `name_cn` | 대여소명 (한/영/중) |
| `x_pos` | **위도** — 이력 CSV의 `X좌표`와 같은 관례(통념과 반대) |
| `y_pos` | **경도** |
| `address` | 주소 |
| `parking_count` | **대여 가능 자전거 수량** |

> ★ `parking_count` 는 **거치대 총 수가 아니라 지금 빌릴 수 있는 자전거 수**다.
> 따라서 이 API로는 거치대 용량(capacity)을 알 수 없고,
> REPORT.md 한계 2번(거치대 용량 미상)은 이 API로 해소되지 않는다.
> 재고 오프셋 확정(한계 1번)은 가능하다.

호출 제한: 일일 한도 내 호출 가능, 과도한 트래픽 시 차단.

## 저장 테이블 `station_status` (SQLite)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `collected_at` | TEXT | 수집 시각 (KST, 초 단위 ISO8601) |
| `station_id` | TEXT | API `id` |
| `station_name` | TEXT | API `name` |
| `bikes_available` | INTEGER | API `parking_count` |
| `lat` / `lon` | REAL | API `x_pos` / `y_pos` (범위 검사 후 필요 시 교정) |
| `address` | TEXT | API `address` |
| `raw_json` | TEXT | 원본 응답 그대로 (스키마 변경 대비 보험) |

기본키 `(collected_at, station_id)` · 보조 인덱스 `(station_id, collected_at)`
수집 성공/실패 이력은 `collect_log` 테이블에 남는다.

## 아직 확정하지 못한 것

- **최상위 응답 구조** — 배열인지 `{"results": [...]}` 같은 래퍼인지는 실제 키를 받아야 안다.
  `api_client.unwrap()` 이 흔한 래퍼 키(`results`/`data`/`items`/…)를 순서대로 시도하고,
  못 찾으면 무엇을 고쳐야 하는지 알려주며 멈춘다. **실제 응답을 처음 본 날 이 함수만 고치면 된다.**
- **`id` 체계** — 이력 데이터의 `ST####` 와 동일한지. `05_validate_with_realtime.py` 의
  검증 ①이 매칭률을 계산하고, 90% 미만이면 이름·좌표 기반 fallback 매핑이 필요하다고 알린다.

## 수집 주기

기본 5분(`TASHU_POLL_INTERVAL_SEC=300`). 재고가 몇 분 만에 소진되는 대여소를 잡으려면 5분이 하한.
검증 착수 권장 기준은 **14일 축적**이다.
