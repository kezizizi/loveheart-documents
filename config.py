"""全局常量与配置。

本模块只放**不涉及 I/O 的纯常量**，以及一个容错的配置加载器。
所有秘密（app_id / app_secret / base_token）都从 secrets 或环境变量读取，
绝不硬编码在本文件里。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent
ASSETS_DIR: Final[Path] = PROJECT_ROOT / "assets"
LOGO_DIR: Final[Path] = ASSETS_DIR / "logo"
MASCOT_DIR: Final[Path] = ASSETS_DIR / "mascot"
FONT_DIR: Final[Path] = ASSETS_DIR / "fonts"
CSS_DIR: Final[Path] = ASSETS_DIR / "css"

FONT_CANDIDATES: Final[tuple[str, ...]] = (
    str(FONT_DIR / "NotoSansSC-VF.ttf"),
    "C:/Windows/Fonts/NotoSansSC-VF.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)

# --------------------------------------------------------------------------
# 品牌
# --------------------------------------------------------------------------

ORG_NAME: Final = "北京大学学生爱心社"
ORG_SLOGAN: Final = "奉献爱心，呼唤爱心，自我教育"
SUPPLEMENT_FORM_URL: Final = (
    "https://acngyried1he.feishu.cn/share/base/form/shrcnsgqhMNq6pJ43llUQ7M7rgg"
)

BRAND_CRIMSON: Final = "#81021F"        # 栏图底色，主品牌色
BRAND_CRIMSON_DEEP: Final = "#5E0116"   # 深一档，用于 hover / 渐变收尾
CREAM_GOLD: Final = "#F9F6E5"           # 栏图上的奶油色，深底上的文字用它

# 配色卡（Morandi 低饱和）——全站中性色与点缀色都从这里取
PALETTE_BLUE: Final = "#99CDD8"
PALETTE_MIST: Final = "#D6E4E3"
PALETTE_BLUSH: Final = "#F3D9D3"
PALETTE_CORAL: Final = "#F3B2B2"
PALETTE_GREY: Final = "#D9D6D3"
PALETTE_SAGE: Final = "#657166"

# 界面中性色
SURFACE_BG: Final = "#FAFAF8"
SURFACE_CARD: Final = "#FFFFFF"
SURFACE_LINE: Final = "#E7E5DF"
SURFACE_TINT: Final = "#F4F5F1"
INK: Final = "#2F332C"
INK_SOFT: Final = "#75796F"
INK_FAINT: Final = "#A6AAA0"

# 部组配色：沿用「低饱和、中明度」的调性，与配色卡同族
GROUP_COLORS: Final[dict[str, str]] = {
    "儿童部": "#F3B2B2",
    "护老部": "#E8B48A",
    "手语分社": "#99CDD8",
    "外联部": "#8FB4C9",
    "无障碍部": "#A9C79E",
    "项目组": "#B9AECB",
    "宣传部": "#E7C3BC",
    "资助部": "#8FBDB6",
    "组织部": "#C7B79E",
}
GROUP_FALLBACK_COLOR: Final = "#CFCEC7"
UNKNOWN_GROUP_LABEL: Final = "其他"

# --------------------------------------------------------------------------
# 两库元信息
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BaseSpec:
    """一个学年库的元信息。token 与主表 id 由 secrets 提供。"""

    key: str
    label: str
    base_token: str
    main_table_id: str


BASE_KEYS: Final[tuple[str, ...]] = ("base_25_26", "base_26_27")

BASE_LABELS: Final[dict[str, str]] = {
    "base_25_26": "25-26 学年",
    "base_26_27": "26-27 学年",
}

# 学期标签统一成四位年份 + 季节，例如「2025秋」。
# 季节序号用于同一年内的先后：春 < 暑 < 秋。
_SEASON_WEIGHT: Final[dict[str, float]] = {
    "春": 0.1,
    "暑": 0.2,
    "夏": 0.2,
    "秋": 0.3,
}

# 两套写法：四位年份（2025秋）与两位年份（25秋）
_TERM_RE_4 = re.compile(r"(20\d{2})\s*年?\s*(暑|秋|春|寒假|暑假|夏)")
_TERM_RE_2 = re.compile(r"(?<!\d)(2[0-9])\s*年?\s*(暑|秋|春|寒假|暑假|夏)")


def normalize_term(text: str | None) -> str | None:
    """把任意学期写法归一成「2025秋」这种四位形式。"""
    if not text:
        return None
    m = _TERM_RE_4.search(text)
    if m:
        return f"{m.group(1)}{_season_of(m.group(2))}"
    m = _TERM_RE_2.search(text)
    if m:
        return f"20{m.group(1)}{_season_of(m.group(2))}"
    return None


def _season_of(raw: str) -> str:
    if raw.startswith("寒"):
        return "寒"
    if raw.startswith("暑") or raw.startswith("夏"):
        return "暑"
    return raw[0]


def term_sort_value(term: str | None) -> float | None:
    """学期 → 可比较数值。无法识别返回 None。"""
    normalized = normalize_term(term)
    if not normalized:
        return None
    year = int(normalized[:4])
    season = normalized[4:]
    return year + _SEASON_WEIGHT.get(season, 0.0)


# 「23秋以前」这类「之前」口径，排在所有具体学期之前
BEFORE_ALL_TERMS: Final[frozenset[str]] = frozenset(
    {"23秋以前", "2023秋以前", "23秋及以前", "2023秋及以前"}
)

# --------------------------------------------------------------------------
# 字段策略：白名单 + 黑名单
# --------------------------------------------------------------------------

# 主表里允许读取并进入内存的字段。其余一律不请求。
MEMBER_READ_WHITELIST: Final[tuple[str, ...]] = (
    "姓名",
    "学号",
    "性别",
    "院系",
    "年级",
    "入社日期",
    "入社时间",
    "骨干",
    "主要活动的部组",
    "主要活动/感兴趣的部组",
    "想说的话",
    "想对爱心社说的话",
    "扫码交社费",
    "请扫码交10元社费，然后在群聊“26秋爱心社招新预热🔥”中添加想去部组的部长微信~",
)

# 绝对不允许读取或输出的敏感字段。命中即视为代码缺陷。
SENSITIVE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "您的身份证",
        "身份证",
        "身份证号",
        "您的微信号",
        "微信号",
        "您在志愿北京的志愿者编号",
        "志愿者编号",
        "您的志愿者编号",
    }
)

# 永不读取的表（即使出现在 block 树里也直接跳过）
FORBIDDEN_TABLE_IDS: Final[frozenset[str]] = frozenset(
    {
        "tbleCOz8lHcOHXvs",  # 志愿者编号信息统计（含身份证 / 志愿北京编号）
    }
)

# 主表上这些 link 字段不是活动，构建注册表时必须排除
NON_ACTIVITY_LINK_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "父记录",
        "子记录",
    }
)

# 活动表里这些字段不是活动内容，展示时跳过
NON_CONTENT_FIELD_PATTERNS: Final[tuple[str, ...]] = (
    "自动编号",
    "署名昵称",
    "扫码",
    "请扫描",
)

# 非活动表的名称特征（模板表等）
NON_ACTIVITY_TABLE_NAME_PATTERNS: Final[tuple[str, ...]] = (
    "模板勿用",
    "意见反馈渠道",
    "志愿者编号",
)

# 活动表名里的问卷后缀，展示前要剥掉。
# 顺序有意义：长后缀先剥，避免「学时问卷」把「反馈、学时问卷」切得不干净。
ACTIVITY_NAME_SUFFIXES: Final[tuple[str, ...]] = (
    "反馈、学时问卷",
    "反馈学时问卷",
    "反馈、学时收集",
    "反馈学时收集",
    "反馈、学时",
    "反馈学时",
    "反馈问卷",
    "反馈收集",
    "学时问卷",
    "学时收集",
    "问卷",
    "（模板勿用）",
    "(模板勿用)",
    " - 结果分析",
    "-结果分析",
)
# 尾部噪声：副本标记、空白、连接符
_TRAILING_NOISE_RE: Final = re.compile(
    r"[\s\-—_·、]*(副本|自填|补录|新|模板勿用|（模板勿用）|\d+)[\s\-—_·、]*$"
)
_TRAILING_SEP_RE: Final = re.compile(r"[\s\-—_·、]+$")
_LEADING_SEP_RE: Final = re.compile(r"^[\s\-—_·]+")


def clean_activity_name(raw_name: str) -> str:
    """把「2025秋敬老院活动反馈、学时问卷 副本」洗成「2025秋敬老院活动」。"""
    name = (raw_name or "").strip()
    if not name:
        return ""

    changed = True
    safety = 0
    while changed and safety < 12:
        changed = False
        safety += 1
        # 先剥尾部噪声（副本 / 数字编号）
        stripped = _TRAILING_NOISE_RE.sub("", name)
        if stripped != name:
            name = stripped
            changed = True
            continue
        # 再剥问卷后缀
        for suffix in ACTIVITY_NAME_SUFFIXES:
            if name.endswith(suffix):
                candidate = name[: -len(suffix)]
                candidate = _TRAILING_SEP_RE.sub("", candidate)
                if candidate:
                    name = candidate
                    changed = True
                    break

    name = _TRAILING_SEP_RE.sub("", name)
    return _LEADING_SEP_RE.sub("", name).strip() or (raw_name or "").strip()

# --------------------------------------------------------------------------
# 动态字段探测（字段命名不统一，只能靠正则）
# --------------------------------------------------------------------------

HOURS_RE: Final = re.compile(r"(志愿)?(时长|学时|小时|工时)")
# 参与时间字段的命名很野：实际数据里既有「参与时间」，也有「填写日期」「签到时间」，
# 所以「时间」和「日期」都要认。
TIME_RE: Final = re.compile(r"(参与|活动|服务|填写|报名|签到|录入)?(时间|日期)")

# 强烈暗示某字段是「参与时间」的关键词（优先级高于泛化的 TIME_RE）
TIME_STRONG_HINTS: Final = (
    "参与时间",
    "活动时间",
    "服务时间",
    "签到时间",
    "填写日期",
    "参与日期",
    "活动日期",
    "日期",
    "时间",
)

# --------------------------------------------------------------------------
# 缓存与并发
# --------------------------------------------------------------------------

DEFAULT_REGISTRY_TTL: Final = 86_400
DEFAULT_MEMBER_TTL: Final = 600
DEFAULT_MAX_WORKERS: Final = 8
DEFAULT_TIMEOUT: Final = 15
CO_PARTNER_CAP: Final = 30

# 主表字段类型编号（飞书 bitable）
FIELD_TYPE_TEXT: Final = 1
FIELD_TYPE_NUMBER: Final = 2
FIELD_TYPE_SELECT: Final = 3
FIELD_TYPE_MULTI_SELECT: Final = 4
FIELD_TYPE_DATETIME: Final = 5
FIELD_TYPE_CHECKBOX: Final = 7
FIELD_TYPE_USER: Final = 11
FIELD_TYPE_PHONE: Final = 13
FIELD_TYPE_URL: Final = 15
FIELD_TYPE_LINK: Final = 18
FIELD_TYPE_LINK_DUPLEX: Final = 21
LINK_FIELD_TYPES: Final[tuple[int, ...]] = (FIELD_TYPE_LINK, FIELD_TYPE_LINK_DUPLEX)


# --------------------------------------------------------------------------
# 配置加载
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    app_id: str
    app_secret: str
    bases: tuple[BaseSpec, ...]
    registry_ttl: int
    member_ttl: int
    max_workers: int


class ConfigError(RuntimeError):
    """配置缺失或不合法。"""


def _read_source() -> dict:
    """按 secrets → 环境变量 的顺序取配置，返回普通 dict。"""
    try:  # pragma: no cover - 依赖 Streamlit 运行时
        import streamlit as st

        secrets = st.secrets
        if secrets:
            return {k: secrets[k] for k in secrets}
    except Exception:
        pass
    return {}


def _source_get(source: dict, section: str, field: str, env_key: str) -> str | None:
    try:
        block = source.get(section)
        if block and field in block:
            return str(block[field])
    except Exception:
        pass
    return os.environ.get(env_key)


def load_settings(*, require_credentials: bool = True) -> Settings:
    """装配运行期配置。

    require_credentials=False 时允许 app_id/app_secret 为空（供离线测试与
    资源构建使用）。
    """
    source = _read_source()

    app_id = _source_get(source, "feishu", "app_id", "APP_ID") or ""
    app_secret = _source_get(source, "feishu", "app_secret", "APP_SECRET") or ""

    if require_credentials and (not app_id or not app_secret):
        raise ConfigError(
            "缺少飞书应用凭证。请在 .streamlit/secrets.toml 的 [feishu] 段填写 "
            "app_id 与 app_secret。"
        )

    bases: list[BaseSpec] = []
    for key in BASE_KEYS:
        token = _source_get(source, key, "base_token", f"{key.upper()}_TOKEN")
        table = _source_get(source, key, "main_table_id", f"{key.upper()}_TABLE_ID")
        if not token or not table:
            raise ConfigError(
                f"缺少 {key} 的 base_token 或 main_table_id，请检查 secrets.toml。"
            )
        bases.append(
            BaseSpec(
                key=key,
                label=BASE_LABELS[key],
                base_token=token,
                main_table_id=table,
            )
        )

    registry_ttl = int(
        _source_get(source, "registry", "ttl_seconds", "REGISTRY_TTL")
        or DEFAULT_REGISTRY_TTL
    )
    member_ttl = int(
        _source_get(source, "query", "member_ttl_seconds", "MEMBER_TTL")
        or DEFAULT_MEMBER_TTL
    )
    max_workers = int(
        _source_get(source, "query", "max_workers", "MAX_WORKERS")
        or DEFAULT_MAX_WORKERS
    )

    return Settings(
        app_id=app_id,
        app_secret=app_secret,
        bases=tuple(bases),
        registry_ttl=registry_ttl,
        member_ttl=member_ttl,
        max_workers=max_workers,
    )


def group_color(group: str | None) -> str:
    """取部组配色，未知部组回退到中性色。"""
    if not group:
        return GROUP_FALLBACK_COLOR
    return GROUP_COLORS.get(group, GROUP_FALLBACK_COLOR)
