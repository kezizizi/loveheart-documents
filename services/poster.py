"""业务层：分享海报生成（服务端 Pillow 拼版）。

Streamlit Cloud 上没有无头浏览器，截图方案不可行，因此用 Pillow 直接画。
中文字体**必须随仓库提交**（``assets/fonts/NotoSansSC-VF.ttf``，OFL 开源），
否则云端会画成一堆方框。
"""

from __future__ import annotations

import io
from datetime import date
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config import (
    BRAND_RED,
    CARD_WHITE,
    CREAM,
    FONT_CANDIDATES,
    INK,
    INK_SOFT,
    LOGO_DIR,
    MASCOT_DIR,
    ORG_NAME,
    ORG_SLOGAN,
    SUN_ORANGE,
    UNKNOWN_GROUP_LABEL,
    WARM_YELLOW,
    group_color,
)
from feishu.models import Footprint, MembershipDate
from services.membership_date import format_date

POSTER_WIDTH = 1080
POSTER_HEIGHT = 1620
MAX_TIMELINE_ITEMS = 12

_PAD = 72
_CARD_RADIUS = 28


# --------------------------------------------------------------------------
# 字体
# --------------------------------------------------------------------------


@lru_cache(maxsize=32)
def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
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


def _try_set_bold(font: ImageFont.FreeTypeFont) -> None:
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


def _fit(
    draw: ImageDraw.ImageDraw, text: str, font, max_width: float
) -> str:
    """超宽则截断并加省略号。"""
    if _text_w(draw, text, font) <= max_width:
        return text
    trimmed = text
    while trimmed and _text_w(draw, trimmed + "…", font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + "…") if trimmed else ""


# --------------------------------------------------------------------------
# 绘制原语
# --------------------------------------------------------------------------


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return tuple(int(v[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _draw_vertical_gradient(
    img: Image.Image,
    box: tuple[int, int, int, int],
    top: str,
    bottom: str,
) -> None:
    x0, y0, x1, y1 = box
    height = max(1, y1 - y0)
    c1 = _hex_to_rgb(top)
    c2 = _hex_to_rgb(bottom)
    draw = ImageDraw.Draw(img)
    for offset in range(height):
        ratio = offset / height
        color = tuple(
            int(c1[i] + (c2[i] - c1[i]) * ratio) for i in range(3)
        )
        draw.line([(x0, y0 + offset), (x1, y0 + offset)], fill=color)


def _draw_diagonal_gradient(
    img: Image.Image,
    box: tuple[int, int, int, int],
    start: str,
    end: str,
) -> None:
    """135° 渐变：按 (x + y) 投影插值。用于顶部品牌带。"""
    x0, y0, x1, y1 = box
    c1 = _hex_to_rgb(start)
    c2 = _hex_to_rgb(end)
    span = max(1, (x1 - x0) + (y1 - y0))
    draw = ImageDraw.Draw(img)
    for offset in range(0, span):
        ratio = offset / span
        color = tuple(int(c1[i] + (c2[i] - c1[i]) * ratio) for i in range(3))
        draw.line(
            [(x0 + offset, y0), (x0, y0 + offset)], fill=color, width=3
        )
        draw.line(
            [(x1, y0 + offset - (x1 - x0)), (x1 - offset + (x1 - x0), y1)],
            fill=color,
            width=3,
        )


def _paste_rounded_logo(
    img: Image.Image, logo_path: Path, center: tuple[int, int], diameter: int
) -> bool:
    if not logo_path.exists():
        return False
    try:
        logo = Image.open(logo_path).convert("RGBA")
    except Exception:  # noqa: BLE001
        return False

    logo.thumbnail((int(diameter * 0.72), int(diameter * 0.72)), Image.LANCZOS)

    circle = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    ImageDraw.Draw(circle).ellipse(
        (0, 0, diameter - 1, diameter - 1), fill=(255, 255, 255, 235)
    )
    px = center[0] - diameter // 2
    py = center[1] - diameter // 2
    img.paste(circle, (px, py), circle)
    img.paste(
        logo,
        (
            center[0] - logo.width // 2,
            center[1] - logo.height // 2,
        ),
        logo,
    )
    return True


def _pick_mascot() -> Path | None:
    if not MASCOT_DIR.exists():
        return None
    candidates = sorted(MASCOT_DIR.glob("xiaoai_*.jpg")) + sorted(
        MASCOT_DIR.glob("xiaoai_*.png")
    )
    return candidates[0] if candidates else None


def _load_line_art(path: Path, max_size: tuple[int, int]) -> Image.Image | None:
    """把手绘线稿（白底黑线）转成透明底的墨色 PNG 图层。

    做法：以亮度作为不透明度——越黑越不透明，越白越透明。
    这样贴到米白背景上不会出现一个难看的白方块。
    """
    try:
        gray = Image.open(path).convert("L")
    except Exception:  # noqa: BLE001
        return None

    gray.thumbnail(max_size, Image.LANCZOS)

    def _to_alpha(value: int) -> int:
        if value >= 214:  # 接近白 → 全透明，顺手去掉 JPEG 噪点
            return 0
        return int((255 - value) * 1.06) if value < 150 else int((214 - value) * 2.6)

    alpha = gray.point(_to_alpha)
    layer = Image.new("RGBA", gray.size, _hex_to_rgb(INK) + (0,))
    layer.putalpha(alpha)
    return layer


def _load_stamp(max_size: tuple[int, int], opacity: float) -> Image.Image | None:
    stamp_path = LOGO_DIR / "stamp_transparent.png"
    if not stamp_path.exists():
        return None
    try:
        stamp = Image.open(stamp_path).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
    stamp.thumbnail(max_size, Image.LANCZOS)
    alpha = stamp.split()[-1].point(lambda a: int(a * opacity))
    stamp.putalpha(alpha)
    return stamp


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
    img = Image.new("RGB", (POSTER_WIDTH, POSTER_HEIGHT), CREAM)
    draw = ImageDraw.Draw(img)

    band_height = 400
    _draw_vertical_gradient(img, (0, 0, POSTER_WIDTH, band_height), SUN_ORANGE, BRAND_RED)

    _paste_rounded_logo(
        img, LOGO_DIR / "emblem_clean.png", (POSTER_WIDTH // 2, 150), 150
    )

    f_name = _load_font(72, bold=True)
    f_sub = _load_font(34)
    f_slogan = _load_font(28)

    name_text = f"{name} 的爱心足迹"
    draw.text(
        ((POSTER_WIDTH - _text_w(draw, name_text, f_name)) / 2, 250),
        name_text,
        font=f_name,
        fill=(255, 255, 255),
    )

    sub_parts: list[str] = []
    join_date = format_date(membership.date_ms)
    if join_date:
        prefix = "≈ " if membership.is_estimated else ""
        sub_parts.append(f"{prefix}{join_date} 入社")
    if subtitle:
        sub_parts.append(subtitle)
    if sub_parts:
        sub_text = "　·　".join(sub_parts)
        draw.text(
            ((POSTER_WIDTH - _text_w(draw, sub_text, f_sub)) / 2, 336),
            sub_text,
            font=f_sub,
            fill=(255, 236, 224),
        )

    # ---- 三张统计卡 -----------------------------------------------------
    cards_y = band_height + 44
    cards_h = 190
    card_gap = 24
    card_w = (POSTER_WIDTH - _PAD * 2 - card_gap * 2) // 3

    stats = [
        ("累计志愿学时", _fmt_hours(footprint.total_hours), "小时"),
        ("参与活动", str(footprint.distinct_activity_count), "场"),
        ("足迹部组", str(len(footprint.groups)), "个"),
    ]
    f_label = _load_font(28)
    f_value = _load_font(66, bold=True)
    f_unit = _load_font(28)

    for index, (label, value, unit) in enumerate(stats):
        x = _PAD + index * (card_w + card_gap)
        draw.rounded_rectangle(
            (x, cards_y, x + card_w, cards_y + cards_h),
            radius=_CARD_RADIUS,
            fill=CARD_WHITE,
            outline=(242, 226, 214),
            width=2,
        )
        draw.text(
            (x + 28, cards_y + 30), label, font=f_label, fill=INK_SOFT
        )
        value_y = cards_y + 78
        draw.text((x + 28, value_y), value, font=f_value, fill=BRAND_RED)
        unit_x = x + 28 + _text_w(draw, value, f_value) + 8
        draw.text(
            (unit_x, value_y + 34), unit, font=f_unit, fill=INK_SOFT
        )

    # ---- 时间轴 ---------------------------------------------------------
    y = cards_y + cards_h + 46
    f_section = _load_font(34, bold=True)
    draw.text((_PAD, y), "爱心足迹", font=f_section, fill=INK)
    y += 62

    f_dot_date = _load_font(26)
    f_act = _load_font(30)
    f_hours = _load_font(28)
    line_h = 56

    items = list(footprint.timeline)[:MAX_TIMELINE_ITEMS]
    dot_x = _PAD + 14

    for item in items:
        color = group_color(item.group)
        draw.ellipse(
            (dot_x - 9, y + 14, dot_x + 9, y + 32), fill=color
        )
        date_text = format_date(item.event_time_ms, "%Y.%m.%d") or (
            item.term or "—"
        )
        draw.text((_PAD + 40, y + 10), date_text, font=f_dot_date, fill=INK_SOFT)

        title = _fit(draw, item.activity_name, f_act, 560)
        draw.text((_PAD + 220, y), title, font=f_act, fill=INK)

        if item.hours:
            hours_text = f"{_fmt_hours(item.hours)}h"
            draw.text(
                (POSTER_WIDTH - _PAD - _text_w(draw, hours_text, f_hours), y + 4),
                hours_text,
                font=f_hours,
                fill=BRAND_RED,
            )
        y += line_h

    if len(footprint.timeline) > MAX_TIMELINE_ITEMS:
        more = len(footprint.timeline) - MAX_TIMELINE_ITEMS
        draw.text(
            (_PAD + 40, y + 4),
            f"…还有 {more} 场没画下",
            font=f_dot_date,
            fill=INK_SOFT,
        )
        y += line_h

    # ---- 部组色标 -------------------------------------------------------
    y += 18
    f_pill = _load_font(26)
    pill_x = _PAD
    for group in footprint.groups:
        text = group or UNKNOWN_GROUP_LABEL
        width = int(_text_w(draw, text, f_pill)) + 40
        if pill_x + width > POSTER_WIDTH - _PAD:
            pill_x = _PAD
            y += 54
        chip = Image.new("RGBA", (width, 42), (0, 0, 0, 0))
        ImageDraw.Draw(chip).rounded_rectangle(
            (0, 0, width - 1, 41), radius=21, fill=group_color(group) + "CC"
        )
        img.paste(chip, (pill_x, y), chip)
        draw.text((pill_x + 20, y + 6), text, font=f_pill, fill=(60, 44, 34))
        pill_x += width + 14
    content_bottom = y + 42

    # ---- 页脚（整体贴底，统一留白） --------------------------------------
    footer_height = 210
    footer_top = POSTER_HEIGHT - _PAD - footer_height
    gap = footer_top - content_bottom

    # 留白够大时，在正中压一枚淡社章——像真的盖了个章，同时填空
    if gap >= 260:
        stamp = _load_stamp((360, 360), 0.20)
        if stamp is not None:
            img.paste(
                stamp,
                (
                    POSTER_WIDTH // 2 - stamp.width // 2,
                    content_bottom + (gap - stamp.height) // 2,
                ),
                stamp,
            )

    mascot = _pick_mascot()
    if mascot is not None:
        art = _load_line_art(mascot, (160, 160))
        if art is not None:
            img.paste(art, (_PAD, footer_top + 6), art)

    slogan_font = _load_font(32, bold=True)
    right_x = POSTER_WIDTH - _PAD
    right_rows = [
        (ORG_SLOGAN, slogan_font, BRAND_RED, footer_top + 18),
        (ORG_NAME, f_slogan, INK_SOFT, footer_top + 70),
        (
            f"数据截至 {(data_as_of or date.today()).strftime('%Y.%m.%d')}",
            f_slogan,
            INK_SOFT,
            footer_top + 122,
        ),
    ]
    for text, font, color, row_y in right_rows:
        draw.text((right_x - _text_w(draw, text, font), row_y), text, font=font, fill=color)

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
