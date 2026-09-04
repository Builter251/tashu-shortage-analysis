# realtime — 타슈 OpenAPI 실시간 수집 모듈 (독립 실행)

이력 분석(`src/01`~`04`)과 **완전히 분리**되어 있다.
**키가 없어도 본 분석은 그대로 완결되고, 이 모듈의 코드 경로도 지금 전부 점검할 수 있다.**
키가 나오면 `.env` 에 넣는 것만으로 동작한다.

## 지금(키 없이) 할 수 있는 것

```bash
python3 realtime/collect.py --selftest    # 합성 응답으로 파싱·저장·조회 전 경로 점검
python3 realtime/collect.py --check       # 엔드포인트 도달 여부만 확인 (401 이면 정상)
python3 realtime/simulate.py --days 14    # 합성 실측 데이터 생성 (코드 경로 점검 전용)
python3 src/05_validate_with_realtime.py --sample   # 검증 스크립트 전 경로 실행
```

> `simulate.py` 로 만든 데이터는 이력 기반 추정에서 파생되므로,
> 이걸로 상관을 재면 **추정을 추정으로 검증하는 순환 논증**이 된다.
> 파일명과 로그에 `SAMPLE` 을 박아 실측과 절대 섞이지 않게 했다. 검증 결과로 인용하면 안 된다.

## 키가 나온 뒤

```bash
cp realtime/.env.example realtime/.env    # TASHU_API_KEY=발급키 입력
python3 realtime/collect.py --once        # 1회 수집
python3 realtime/collect.py --loop        # 주기 수집 (기본 300초)
python3 realtime/collect.py --status      # 얼마나 쌓였는지
```

cron 등록 (5분 간격):

```
*/5 * * * * cd /path/to/project && /usr/bin/python3 realtime/collect.py --once >> realtime/storage/collect.log 2>&1
```

**주의**: SQLite 는 네트워크 드라이브·클라우드 동기화 폴더·컨테이너 마운트 위에서
파일 잠금이 동작하지 않아 `disk I/O error` 가 난다. 그런 위치라면
`--db ~/tashu_status.sqlite` 처럼 로컬 디스크 경로를 지정한다.

## 키 발급 방법

1. 타슈 앱 로그인 → 사이드메뉴 → 자전거정보 > OpenAPI 에서 신청
2. 실무담당자 검토 후 승인 시 인증키 부여

## 축적 후 검증

```bash
python3 src/05_validate_with_realtime.py    # 14일 축적 후
```

| 검증 | 내용 |
|---|---|
| ① ID 매핑 | API `id` 가 이력의 `ST####` 와 같은 체계인가 |
| ② 변화 패턴 상관 | 추정 Δ재고 vs 실측 Δ재고, 대여소별 상관계수 분포 |
| ③ 재고 소진 실재 | 순유출 상위 대여소가 실제로 0대인 시간이 더 잦은가 |
| ④ 관제센터 정체 | 관제 노드에 실제 자전거가 거치돼 있는가 (가설 A/B 판별) |
| ⑤ 재고 오프셋 | 실측 평균 − 추정 상대재고 평균 = 대여소별 오프셋 확정 |

스키마와 API 필드는 [`schema.md`](schema.md) 참조.

## 파일

| 파일 | 역할 |
|---|---|
| `api_client.py` | 엔드포인트·인증·응답 정규화. **응답 구조가 다르면 여기만 고친다** |
| `storage.py` | SQLite 스키마와 적재 |
| `collect.py` | CLI (`--selftest` / `--check` / `--once` / `--loop` / `--status`) |
| `simulate.py` | 합성 데이터 생성 (코드 경로 점검 전용) |
