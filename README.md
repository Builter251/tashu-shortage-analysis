# 타슈 대여소 자전거 부족 분석 (Codyssey M1-1)

대전 공영자전거 **타슈**의 대여 이력 19개월치를 이용해
"어느 대여소가, 언제, 왜 자전거가 부족한가"를 분석한다.

- 설계 문서: [`PLAN.md`](PLAN.md)
- 적재 단계 데이터 품질 진단: [`reports/01_ingest_findings.md`](reports/01_ingest_findings.md)
- **최종 리포트: [`REPORT.md`](REPORT.md)** ★
- 단계별 상세 기록: [`reports/`](reports/)

## 데이터 출처

| 데이터 | 출처 | 기간 |
|---|---|---|
| 대전시 공영자전거 타슈 대여이력 정보 | 공공데이터포털 / 대전교통공사 · https://www.data.go.kr/data/15137219/fileData.do | 2024-08 ~ 2026-03 |
| 대전광역시 공영자전거(타슈) 위치 현황 | 공공데이터포털 | 2026-06-24 기준 |
| (선택) 타슈 OpenAPI 실시간 현황 | https://bike.tashu.or.kr/noticeDetail.do?seq=28 | 키 발급 후 수집 |

원본 CSV는 용량(약 2.3GB) 때문에 저장소에 포함하지 않는다.
위 링크에서 내려받아 `data/대전교통공사_대전시 공영자전거 타슈 대여이력 정보_20260331/` 에 둔다.
공공데이터 이용약관에 따른 출처 표시 조건을 지킨다.

## 실행 방법

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python3 src/01_ingest.py        # 원본 CSV → 정제 parquet + 대여소 마스터 (약 80초)
python3 src/02_build_panel.py   # 중복 제거 · 재배치 탐지 · 대여소×시간 패널   (약 23초)
python3 src/verify_02.py        # 재배치 로직 교차 검증 (pandas 독립 재구현 대조)
python3 src/03_analyze.py       # 지표 계산 및 그림 01~07
python3 src/04_forecast.py      # 재고 소진 진단 · 예측 백테스트 · 그림 08~10
python3 src/verify_report.py    # REPORT.md 인용 수치 23건 자동 재검증
```

실시간 수집 모듈은 독립이다 → [`realtime/README.md`](realtime/README.md)

## 알려진 데이터 제약 (반드시 읽을 것)

1. **2025년 12월 데이터는 존재하지 않는다.** 배포된 "25년12월" 파일은 2025년 1월 데이터의 완전 중복이다(일치율 100%).
2. 2025-02-26~28, 2025-03-01~03 은 시스템 장애로 결측이다.
3. 파일마다 **인코딩이 다르다** (cp949 / utf-8-sig 혼재). 2025년 3월만 **날짜에 초가 없다.**
4. `X좌표`가 위도, `Y좌표`가 경도다. 통념과 반대다.
5. 대여소 이름·주소 표기가 월마다 달라진다. **식별은 반드시 `ST####` ID로 한다.**

자세한 근거는 `reports/01_ingest_findings.md` 참조.
