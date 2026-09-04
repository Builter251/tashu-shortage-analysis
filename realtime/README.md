# realtime — 타슈 OpenAPI 실시간 수집 모듈 (독립 실행)

이력 분석(`src/`)과 **완전히 분리**되어 있다. 키가 없어도 본 분석은 그대로 완결된다.

## 실행

```bash
cp .env.example .env      # 발급받은 키 입력
pip install requests python-dotenv
python collect.py         # 1회 수집
```

주기 수집(macOS launchd 또는 cron):

```
*/5 * * * * cd /path/to/realtime && /usr/bin/python3 collect.py >> storage/collect.log 2>&1
```

## 산출물

`storage/tashu_status.sqlite` — 스키마는 `schema.md` 참조.

## 본 분석과의 연결

`src/05_validate_with_realtime.py` 한 곳에서만 이 폴더의 저장 파일을 읽는다.
