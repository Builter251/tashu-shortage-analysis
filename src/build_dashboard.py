#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_dashboard.py — 템플릿 + 집계 JSON → 자체 완결 대시보드 HTML 2종

  dashboard.html                 : 로컬에서 더블클릭으로 열리는 완전 독립 파일
  index.html                     : dashboard.html 과 동일 (GitHub Pages 배포용 진입점)
  build/artifact_dashboard.html  : 게시용(문서 골격 없이 본문만)
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
tpl = (ROOT / "src" / "dashboard_template.html").read_text(encoding="utf-8")
data = (ROOT / "data" / "processed" / "dashboard_data.json").read_text(encoding="utf-8")
body = tpl.replace("__DATA__", data)

(ROOT / "build").mkdir(exist_ok=True)
(ROOT / "build" / "artifact_dashboard.html").write_text(body, encoding="utf-8")

standalone = ('<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
              '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
              + body.split("</style>")[0] + "</style>\n</head>\n<body>\n"
              + "</style>".join(body.split("</style>")[1:]) + "\n</body>\n</html>\n")
(ROOT / "dashboard.html").write_text(standalone, encoding="utf-8")
# GitHub Pages 는 저장소 루트의 index.html 을 기본으로 서빙한다.
# 대시보드 자체를 index 로 두면 배포 URL 이 곧 대시보드가 된다.
(ROOT / "index.html").write_text(standalone, encoding="utf-8")
(ROOT / ".nojekyll").write_text("", encoding="utf-8")   # Jekyll 처리 건너뛰기

for p in [ROOT / "dashboard.html", ROOT / "index.html", ROOT / "build" / "artifact_dashboard.html"]:
    print(f"{p.relative_to(ROOT)}  {p.stat().st_size/1e6:.2f} MB")
