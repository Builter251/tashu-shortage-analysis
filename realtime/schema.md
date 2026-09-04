# 실시간 수집 스키마 (키 발급 전 확정)

본 모듈은 이력 분석과 **독립**이다. 본 분석 코드는 이 폴더를 import 하지 않으며,
연결은 `src/05_validate_with_realtime.py` 한 곳에서만 일어난다.

## 저장 테이블: `station_status`

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `collected_at` | TIMESTAMP (KST) | 수집 시각. 초 단위까지 기록 |
| `station_id` | TEXT | 대여소 ID. **이력 데이터의 `ST####` 체계와 동일한지 최초 1회 검증 필요** |
| `station_name` | TEXT | API가 주는 대여소명 (매핑 검증용) |
| `bikes_available` | INTEGER | 현재 대여 가능 대수 |
| `racks_total` | INTEGER | 거치대 총 수 (API 제공 시). 이력 데이터에 없는 **핵심 보완 정보** |
| `raw_json` | TEXT | 원본 응답 그대로. 스키마 변경 대비 보험 |

기본키: (`collected_at`, `station_id`)

## 수집 주기

- 기본 5분(`TASHU_POLL_INTERVAL_SEC=300`). 재고가 몇 분 만에 소진되는 대여소를 잡으려면 5분이 하한.
- 최소 2주 축적 후 검증 착수.

## 키 발급 전 체크리스트

- [ ] API 응답의 `station_id` 형식 확인 → 이력 CSV의 `ST####`와 1:1 매핑되는가
- [ ] 매핑 실패 시 대여소명 + 좌표 기반 fallback 매핑 규칙 준비
- [ ] `racks_total` 제공 여부 확인 (제공되면 재고 오프셋 확정 가능)
- [ ] 응답 실패/타임아웃 시 재시도 및 로깅 정책

## 검증 항목 (수집 2주 후)

1. 추정 Δ재고 vs 실측 Δ재고의 대여소별 상관계수 분포
2. 이력으로 지목한 부족 대여소의 실제 0대 시간 비율
3. `racks_total`로 재고 오프셋 확정 → 추정 재고 절대값 보정
