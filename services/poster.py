"""业务层：分享海报生成（服务端 Pillow 拼版）。

版式（1080×1620）：
    ① 顶部品牌带 —— 官方栏图（手 + 心），全宽出血
    ② 深红姓名带 —— 与栏图底色无缝衔接，奶油色字
    ③ 三张统计卡
    ④ 时间轴（按可用高度动态决定放几条）
    ⑤ 页脚（小爱线稿 · 口号 · 二维码 · 数据截止日）

Streamlit Cloud 上没有无头浏览器，截图方案不可行，因此用 Pillow 直接画。
中文字体**必须随仓库提交**（``assets/fonts/NotoSansSC-VF.ttf``，OFL 开源）。
"""

from __future__ import annotations

import io
from datetime import date
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config import (
    BRAND_CRIMSON,
    CREAM_GOLD,
    FONT_CANDIDATES,
    INK,
    INK_FAINT,
    INK_SOFT,
    LOGO_DIR,
    MASCOT_DIR,
    ORG_NAME,
    ORG_SLOGAN,
    SURFACE_BG,
    SURFACE_CARD,
    SURFACE_LINE,
    UNKNOWN_GROUP_LABEL,
    group_color,
)
from feishu.models import Footprint, MembershipDate
from services.membership_date import format_date

POSTER_WIDTH = 1080
POSTER_HEIGHT = 1620

_PAD = 72
_CARD_RADIUS = 26
_LINE_H = 50
_FOOTER_H = 206

BANNER_FILE = "banner_band.png"
QR_FILE = "qr.png"

# 深红底上的次要文字：奶油色压暗一档
_ON_CRIMSON_SOFT = "#D8CDA8"


# --------------------------------------------------------------------------
# 字体
# --------------------------------------------------------------------------


@lru_cache(maxsize=32)
def _load_font(size: int, bold: bool = False):
    last_error: Exception | None = None
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            font = ImageFont.truetype(str(path), size)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
        if bold:
            _try_set_bold(font)
        return font
    raise RuntimeError(f"找不到可用的中文字体，海报无法渲染：{last_error}")


def _try_set_bold(font) -> None:
    for name in ("Bold", "Black", "SemiBold"):
        try:
            font.set_variation_by_name(name)
            return
        except Exception:  # noqa: BLE001 - 非可变字体直接跳过
            continue


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    try:
        return draw.textlength(text, font=font)
    except Exception:  # noqa: BLE001
        return len(text) * font.size * 0.6


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> str:
    if _text_w(draw, text, font) <= max_width:
        return text
    trimmed = text
    while trimmed and _text_w(draw, trimmed + "…", font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + "…") if trimmed else ""


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return tuple(int(v[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _center_text(
    draw: ImageDraw.ImageDraw, text: str, font, y: int, fill: str, width: int
) -> None:
    draw.text(((width - _text_w(draw, text, font)) / 2, y), text, font=font, fill=fill)


# --------------------------------------------------------------------------
# 素材
# --------------------------------------------------------------------------


def _open_asset(name: str) -> Image.Image | None:
    path = LOGO_DIR / name
    if not path.exists():
        return None
    try:
        return Image.open(path).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def _pick_mascot() -> Path | None:
    if not MASCOT_DIR.exists():
        return None
    files = sorted(MASCOT_DIR.glob("xiaoai_*.jpg")) + sorted(
        MASCOT_DIR.glob("xiaoai_*.png")
    )
    return files[0] if files else None


def _load_line_art(path: Path, max_size: tuple[int, int]) -> Image.Image | None:
    """手绘线稿（白底黑线）→ 透明底墨色图层，避免贴上白方块。"""
    try:
        gray = Image.open(path).convert("L")
    except Exception:  # noqa: BLE001
        return None

    gray.thumbnail(max_size, Image.LANCZOS)

    def _to_alpha(value: int) -> int:
        if value >= 214:
            return 0
        return int((255 - value) * 1.06) if value < 150 else int((214 - value) * 2.6)

    layer = Image.new("RGBA", gray.size, _hex_to_rgb(INK) + (0,))
    layer.putalpha(gray.point(_to_alpha))
    return layer


# --------------------------------------------------------------------------
# 主渲染
# --------------------------------------------------------------------------


def render_poster(
    *,
    name: str,
    footprint: Footprint,
    membership: MembershipDate,
    data_as_of: date | None = None,
    subtitle: str = "",
) -> bytes:
    """渲染 1080×1620 的爱心足迹海报，返回 PNG 字节。

    海报上**只出现姓名**，不含院系、年级、学号等额外个人信息。
    """
    img = Image.new("RGB", (POSTER_WIDTH, POSTER_HEIGHT), SURFACE_BG)
    draw = ImageDraw.Draw(img)

    # ---- ① 品牌带：官方栏图 ---------------------------------------------
    banner = _open_asset(BANNER_FILE)
    y = 0
    if banner is not None:
        banner_h = int(POSTER_WIDTH * banner.height / banner.width)
        img.paste(
            banner.convert("RGB").resize((POSTER_WIDTH, banner_h), Image.LANCZOS),
            (0, 0),
        )
        y = banner_h

    # ---- ② 深红姓名带（与栏图底色无缝衔接） ------------------------------
    band_h = 176
    draw.rectangle((0, y, POSTER_WIDTH, y + band_h), fill=BRAND_CRIMSON)

    f_name = _load_font(62, bold=True)
    _center_text(draw, f"{name}的爱心足迹", f_name, y + 44, CREAM_GOLD, POSTER_WIDTH)

    meta_parts: list[str] = []
    join_date = format_date(membership.date_ms)
    if join_date:
        meta_parts.append(("≈ " if membership.is_estimated else "") + join_date + " 入社")
    if subtitle:
        meta_parts.append(subtitle)
    if meta_parts:
        f_meta = _load_font(28)
        _center_text(
            draw,
            "　·　".join(meta_parts),
            f_meta,
            y + 122,
            _ON_CRIMSON_SOFT,
            POSTER_WIDTH,
        )
    y += band_h

    # ---- ③ 统计卡 --------------------------------------------------------
    y += 40
    card_h = 164
    gap = 24
    card_w = (POSTER_WIDTH - _PAD * 2 - gap * 2) // 3
    f_label = _load_font(27)
    f_value = _load_font(62, bold=True)
    f_unit = _load_font(26)

    stats = [
        ("累计志愿学时", _fmt_hours(footprint.total_hours), "小时"),
        ("参与活动", str(footprint.distinct_activity_count), "场"),
        ("足迹部组", str(len(footprint.groups)), "个"),
    ]
    for index, (label, value, unit) in enumerate(stats):
        x = _PAD + index * (card_w + gap)
        draw.rounded_rectangle(
            (x, y, x + card_w, y + card_h),
            radius=_CARD_RADIUS,
            fill=SURFACE_CARD,
            outline=SURFACE_LINE,
            width=2,
        )
        draw.text((x + 26, y + 26), label, font=f_label, fill=INK_SOFT)
        draw.text((x + 26, y + 66), value, font=f_value, fill=BRAND_CRIMSON)
        unit_x = x + 26 + _text_w(draw, value, f_value) + 6
        draw.text((unit_x, y + 100), unit, font=f_unit, fill=INK_SOFT)
    y += card_h + 34

    # ---- ④ 时间轴 --------------------------------------------------------
    f_section = _load_font(32, bold=True)
    draw.rounded_rectangle((_PAD, y + 6, _PAD + 5, y + 34), radius=3, fill=BRAND_CRIMSON)
    draw.text((_PAD + 18, y), "爱心足迹", font=f_section, fill=INK)
    y += 54

    footer_top = POSTER_HEIGHT - _PAD - _FOOTER_H

    f_date = _load_font(24)
    f_act = _load_font(28)
    f_hours = _load_font(26)
    dot_x = _PAD + 12

    total_items = len(footprint.timeline)
    capacity = int(max(1, (footer_top - 14 - y) // _LINE_H))
    shown = capacity if total_items <= capacity else max(1, capacity - 1)

    for item in footprint.timeline[:shown]:
        color = group_color(item.group)
        draw.ellipse((dot_x - 8, y + 13, dot_x + 8, y + 29), fill=color)
        when = format_date(item.event_time_ms, "%Y.%m.%d") or (item.term or "—")
        draw.text((_PAD + 34, y + 9), when, font=f_date, fill=INK_FAINT)

        title = _fit(draw, item.activity_name, f_act, 500)
        draw.text((_PAD + 206, y), title, font=f_act, fill=INK)

        group_text = _fit(draw, item.group or UNKNOWN_GROUP_LABEL, f_date, 130)
        draw.text((_PAD + 726, y + 9), group_text, font=f_date, fill=color)

        if item.hours:
            hours_text = f"{_fmt_hours(item.hours)}h"
            draw.text(
                (POSTER_WIDTH - _PAD - _text_w(draw, hours_text, f_hours), y + 3),
                hours_text,
                font=f_hours,
                fill=BRAND_CRIMSON,
            )
        y += _LINE_H

    remaining = total_items - shown
    if remaining > 0:
        draw.text(
            (_PAD + 34, y + 9),
            f"…还有 {remaining} 条记录没画下",
            font=f_date,
            fill=INK_FAINT,
        )

    # ---- ⑤ 页脚 ----------------------------------------------------------
    mascot = _pick_mascot()
    if mascot is not None:
        art = _load_line_art(mascot, (140, 140))
        if art is not None:
            img.paste(art, (_PAD, footer_top + 14), art)

    text_x = _PAD + 164
    f_slogan = _load_font(30, bold=True)
    f_org = _load_font(26)
    draw.text((text_x, footer_top + 26), ORG_SLOGAN, font=f_slogan, fill=BRAND_CRIMSON)
    draw.text((text_x, footer_top + 74), ORG_NAME, font=f_org, fill=INK_SOFT)
    draw.text(
        (text_x, footer_top + 116),
        f"数据截至 {(data_as_of or date.today()).strftime('%Y.%m.%d')}",
        font=f_org,
        fill=INK_FAINT,
    )

    qr = _open_asset(QR_FILE)
    if qr is not None:
        qr_size = 122
        qr = qr.resize((qr_size, qr_size), Image.LANCZOS)
        qr_x = POSTER_WIDTH - _PAD - qr_size
        qr_y = footer_top + 26
        backing = Image.new("RGBA", (qr_size + 16, qr_size + 16), (0, 0, 0, 0))
        ImageDraw.Draw(backing).rounded_rectangle(
            (0, 0, qr_size + 15, qr_size + 15), radius=10, fill=(255, 255, 255, 255)
        )
        img.paste(backing, (qr_x - 8, qr_y - 8), backing)
        img.paste(qr, (qr_x, qr_y), qr)
        f_qr = _load_font(19)
        draw.text(
            (qr_x - 8, qr_y + qr_size + 4),
            "扫码关注爱心社",
            font=f_qr,
            fill=INK_FAINT,
        )

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _fmt_hours(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}"


def poster_file_name(name: str, data_as_of: date | None = None) -> str:
    stamp = (data_as_of or date.today()).strftime("%Y%m%d")
    safe = (name or "社员").replace("/", "_").replace("\\", "_").strip()
    return f"{safe}_爱心足迹_{stamp}.png"
