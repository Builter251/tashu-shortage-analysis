# -*- coding: utf-8 -*-
"""viz_style.py — 모든 그림이 하나의 시스템으로 보이도록 공통 스타일을 고정한다.

색은 '역할'로만 쓴다.
  - 순차(sequential): 크기(magnitude) 표현 → 파랑 한 색조를 밝은→어두운으로
  - 발산(diverging) : 부호(polarity) 표현 → 파랑 ↔ 빨강, 중간은 회색
  - 범주(categorical): 정체(identity) 표현 → 고정 순서로만 배정, 순환 금지
"""
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

# ── 잉크/표면 ────────────────────────────────────────────────────────────────
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"

# ── 범주형 (고정 순서) ──────────────────────────────────────────────────────
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]

# ── 순차형: 파랑 100→700 ────────────────────────────────────────────────────
BLUES = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec",
         "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab",
         "#184f95", "#104281", "#0d366b"]
CMAP_SEQ = LinearSegmentedColormap.from_list("seq_blue", BLUES)

# ── 발산형: 파랑 ↔ 회색 ↔ 빨강 (빨강 = 순유출 = 자전거가 빠지는 쪽) ─────────
CMAP_DIV = LinearSegmentedColormap.from_list(
    "div_blue_red", ["#0d366b", "#2a78d6", "#9ec5f4", "#f0efec",
                     "#f3a3a2", "#e34948", "#a32120"])
POS = "#e34948"   # 순유출(부족 위험)
NEG = "#2a78d6"   # 순유입(과잉)

# 한글 폰트: 이 리눅스 환경에는 Noto Sans CJK 만 있고 matplotlib 에는 JP 페이스로
# 등록돼 있다. Noto CJK 는 한 파일에 한글 글리프를 함께 담고 있어 정상 출력된다.
_CANDIDATES = ["Noto Sans CJK KR", "Noto Sans CJK JP", "NanumGothic",
               "Malgun Gothic", "AppleGothic"]


def _pick_korean_font():
    """설치돼 있는 한글 폰트를 골라 쓴다. 없는 이름을 넘기면 경고가 쏟아지므로
    matplotlib 이 실제로 인식하는 목록과 교집합만 남긴다."""
    import matplotlib.font_manager as fm
    have = {f.name for f in fm.fontManager.ttflist}
    picked = [n for n in _CANDIDATES if n in have]
    if not picked:
        print("[경고] 한글 폰트를 찾지 못했습니다. 라벨이 □ 로 보일 수 있습니다.")
    return picked + ["DejaVu Sans"]


KOREAN_FONTS = _pick_korean_font()


def apply():
    mpl.rcParams.update({
        "font.family": KOREAN_FONTS,
        "axes.unicode_minus": False,          # 한글 폰트에서 마이너스가 깨지는 것 방지
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.edgecolor": BASELINE,
        "axes.linewidth": 0.8,
        "axes.labelcolor": INK2,
        "axes.titlecolor": INK,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.titlepad": 30,
        "axes.labelsize": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.color": MUTED, "ytick.color": MUTED,
        "xtick.labelsize": 9, "ytick.labelsize": 9,
        "grid.color": GRID, "grid.linewidth": 0.7,
        "legend.frameon": False, "legend.fontsize": 9,
        "lines.linewidth": 2.0, "lines.markersize": 5,
        "figure.dpi": 130, "savefig.dpi": 130,
        "savefig.bbox": "tight",
    })


def subtitle(ax, text):
    """제목 아래 한 줄 설명 — '무엇을 보라'를 문장으로 남긴다."""
    ax.text(0, 1.012, text, transform=ax.transAxes, fontsize=9.5,
            color=INK2, va="bottom", ha="left")


def source(fig, text="출처: 공공데이터포털 · 대전교통공사 타슈 대여이력 (2024-08~2026-03)"):
    fig.text(0.005, -0.02, text, fontsize=8, color=MUTED, ha="left", va="top")
