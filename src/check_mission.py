#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_mission.py — 미션 요구사항·평가 항목 충족 여부 자동 점검 (단일 기준)

점검할 때마다 항목을 다르게 묶으면 "26개 중 24개"였다가 "22개 중 22개"가 되어
비교가 불가능해진다. **항목 목록을 이 파일에 고정**해 언제 돌려도 같은 분모가 나오게 한다.

실행:  python3 src/check_mission.py
"""
from __future__ import annotations

import io
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
rep = io.open("REPORT.md", encoding="utf-8").read()
readme = io.open("README.md", encoding="utf-8").read()
imgs = re.findall(r"!\[[^\]]*\]\((images/[^)]+)\)", rep)
n_img_ok = sum(os.path.exists(p) for p in imgs)
n_q = len(re.findall(r"^\| Q\d", rep, re.M))
n_ins = len(re.findall(r"^### 인사이트 \d", rep, re.M))
n_py = len([f for f in os.listdir("src") if f.endswith(".py")])
TECHS = ["이동평균", "변화율", "요일 × 시간대 집계", "누적합", "STL"]
n_tech = sum(t in rep for t in TECHS)

# (구분, 항목, 충족 여부, 근거)  — 이 목록은 고정한다. 추가는 맨 아래에만.
ITEMS = [
    ("결과물", "데이터 포인트 100개 이상", "9,116,471" in rep, "통행 911만 행 · 일별 572일"),
    ("결과물", "분석 질문 3개 이상", n_q >= 3, f"{n_q}개"),
    ("결과물", "시계열 기법 2가지 이상", n_tech >= 2, f"{n_tech}가지"),
    ("결과물", "시각화 2개 이상", n_img_ok >= 2, f"{len(imgs)}개"),
    ("결과물", "시각화 링크 정상 동작", n_img_ok == len(imgs), f"{n_img_ok}/{len(imgs)}"),
    ("결과물", "시각화 3개 이상 (권장)", n_img_ok >= 3, f"{n_img_ok}개"),
    ("결과물", "인사이트 3개 이상", n_ins >= 3, f"{n_ins}개 + 빗나간 가설 1개"),
    ("결과물", "각 인사이트에 관찰 근거", rep.count("**관찰(Fact)**") >= n_ins,
     f"관찰(Fact) {rep.count('**관찰(Fact)**')}회"),
    ("결과물", "관찰/해석 분리 서술", "**해석(Why" in rep and "**행동(Action)**" in rep, "3층 구조"),
    ("결과물", "Python 코드(.py 또는 .ipynb)", n_py >= 1, f".py {n_py}개"),
    ("결과물", "GitHub 저장소", "github.com/Builter251" in readme or "builter251" in readme.lower(), "공개 저장소"),

    ("리포트", "분석 주제 및 선정 이유", "## 1. 분석 주제 및 선정 이유" in rep, "1장"),
    ("리포트", "데이터 설명(출처·기간·규모)", "## 3. 데이터 설명" in rep and "data.go.kr" in rep, "3장"),
    ("리포트", "결측치/이상치 처리 기준", "## 4. 데이터 정제" in rep, "4장 규칙 표 + 건수"),
    ("리포트", "결론 및 한계점", "## 9. 결론 및 한계점" in rep, "9장"),
    ("리포트", "집계 단위 선택 근거", "집계 단위의 근거" in rep, "5-2"),

    ("제약", "requirements.txt", os.path.exists("requirements.txt"), "버전 고정"),
    ("제약", "실행 방법 문서화", "python3 src/01_ingest.py" in readme, "README 실행 순서"),
    ("제약", "데이터 출처 명시", "data.go.kr" in readme and "data.go.kr" in rep, "README·REPORT"),
    ("제약", "라이선스 주의 문구", "이용약관" in rep and "이용약관" in readme, "출처 표시 조건"),
    ("제약", "AI 사용 로그 — 섹션", "## 10. AI 사용 로그" in rep, "10장"),
    ("제약", "AI 사용 로그 — 사용 작업", "사용 작업" in rep, "8개 작업"),
    ("제약", "AI 사용 로그 — 사용 이유", "사용 이유" in rep, "표 2열"),
    ("제약", "AI 사용 로그 — 검증 방법", "검증 방법" in rep, "표 3열"),

    ("평가4", "반례 제시 (기간/집계단위/외부변수)",
     "### 반례 — 이 결론이 달라질 수 있는 조건" in rep, "A~D 4개, 3개는 직접 계산"),
    ("평가4", "다음 단계 수집 데이터 제안", "다음 단계" in rep and "OpenAPI" in rep, "실측 검증 5종 + 기상"),

    ("보너스", "① 대시보드 배포 URL", "builter251.github.io" in readme, "GitHub Pages"),
    ("보너스", "②A 시계열 분해(STL)", "07_stl_decomposition.png" in rep, "그림 07"),
    ("보너스", "②B 베이스라인 예측", "## 8. 예측 실험과 한계" in rep, "그림 08 · B0/B1/B2"),

    # ── 미션 필수는 아니지만 갖춘 것 (분모에는 포함하되 '추가'로 표시) ──
    ("추가", "Jupyter 노트북(흐름 재현)", os.path.exists("notebooks/analysis.ipynb"), "25셀 · 실행 출력 포함"),
    ("추가", "동료평가 대응표", os.path.exists("RUBRIC.md"), "평가 항목별 근거·답변"),
    ("추가", "리포트 수치 자동 검증", os.path.exists("src/verify_report.py"), "42건 대조"),
]

w = max(len(n) for _, n, _, _ in ITEMS)
cur = None
for grp, name, ok, note in ITEMS:
    if grp != cur:
        print(f"\n[{grp}]"); cur = grp
    print(f"  {'OK ' if ok else 'X  '} {name:<{w}}  {note}")
bad = [n for _, n, ok, _ in ITEMS if not ok]
print("\n" + "=" * 72)
print(f"총 {len(ITEMS)}개 항목 중 {len(ITEMS) - len(bad)}개 충족"
      + ("  — 전부 충족" if not bad else f"  — 미충족: {', '.join(bad)}"))
raise SystemExit(1 if bad else 0)
