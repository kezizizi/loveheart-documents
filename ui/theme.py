"""UI 层：注入品牌样式与资源。"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

import streamlit as st

from config import CSS_DIR, LOGO_DIR, MASCOT_DIR

EMBLEM = "emblem_clean.png"
EMBLEM_TEXT = "emblem_text.jpg"
# 网页版栏图：由 docs/栏图.png 裁掉文字与二维码后压成 WebP（约 55 KB）
BANNER = "banner_web.webp"


@lru_cache(maxsize=4)
def _read_css() -> str:
    return (CSS_DIR / "theme.css").read_text(encoding="utf-8")


def inject_theme() -> None:
    """把品牌样式表注入页面。必须在使用任何自定义 class 之前调用。"""
    st.markdown(f"<style>{_read_css()}</style>", unsafe_allow_html=True)


@lru_cache(maxsize=32)
def _data_uri(path_str: str) -> str:
    path = Path(path_str)
    if not path.exists():
        return ""
    suffix = path.suffix.lower().lstrip(".")
    mime = {
        "jpg": "jpeg",
        "jpeg": "jpeg",
        "png": "png",
        "webp": "webp",
        "svg": "svg+xml",
    }.get(suffix, "png")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/{mime};base64,{encoded}"


def logo_uri(file_name: str = EMBLEM) -> str:
    return _data_uri(str(LOGO_DIR / file_name))


def banner_uri() -> str:
    """顶部品牌带用的栏图。"""
    return _data_uri(str(LOGO_DIR / BANNER)) or _data_uri(str(LOGO_DIR / "banner_band.png"))


def mascot_uri(index: int = 0) -> str:
    files = sorted(MASCOT_DIR.glob("xiaoai_*.jpg")) + sorted(
        MASCOT_DIR.glob("xiaoai_*.png")
    )
    if not files:
        return ""
    return _data_uri(str(files[index % len(files)]))


def page_icon() -> str:
    """浏览器标签页图标（Streamlit 接受本地图片路径）。"""
    path = LOGO_DIR / EMBLEM
    return str(path) if path.exists() else "❤️"
