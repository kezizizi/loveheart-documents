"""UI 层：页面编排。"""

from __future__ import annotations

import csv
import io
from datetime import date

import streamlit as st

from config import (
    ORG_NAME,
    ORG_SLOGAN,
    UNKNOWN_GROUP_LABEL,
)
from feishu.member_query import MemberQueryResult
from services.footprint import count_co_partners, group_breakdown, summarize
from services.membership_date import (
    days_since,
    derive_membership_date,
    format_date,
    join_term_hint,
)
from services.poster import poster_file_name, render_poster
from ui import animations, components, state, theme

_POSTER_KEY = "ax_poster"
_PARTNERS_KEY = "ax_partners"
_ERROR_KEY = "ax_error"
_FLASH_KEY = "ax_flash"


# --------------------------------------------------------------------------
# 侧边栏
# --------------------------------------------------------------------------


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown(
            """
<div class="ax-side-title">怎么用</div>
<div class="ax-side-text">输入你入社时填写的<b>姓名</b>与<b>学号</b>，点一下按钮，就能看到你在爱心社留下的全部足迹。</div>
<ol>
  <li>姓名、学号需与入社问卷完全一致</li>
  <li>系统同时查询 25-26 与 26-27 两个学年档案库</li>
  <li>两个库里都有你的档案时会自动合并</li>
</ol>
""",
            unsafe_allow_html=True,
        )
        st.markdown(
            """
<div class="ax-side-title">能查到什么</div>
<ol>
  <li>参加过哪些活动、各拿多少志愿学时</li>
  <li>按时间排列的完整足迹</li>
  <li>累计学时、参与场次、足迹部组</li>
  <li>可导出 CSV，也可生成一张分享海报</li>
</ol>
""",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"""
<div class="ax-side-title">数据口径</div>
<div class="ax-side-text">
入社时间优先取档案里填写的真实日期；没有日期字段时用首次入档时间估算，并在页面上标出「估算」。<br/>
部组标签按<b>活动实际归属</b>判定，不是你入社时填的「感兴趣的部组」。
</div>
""",
            unsafe_allow_html=True,
        )
        st.markdown(
            """
<div class="ax-side-title">隐私说明</div>
<div class="ax-side-text">
本系统只查询你本人输入的那一条记录，不展示任何其他社员的信息。<br/>
身份证号、志愿者编号、微信号等字段在设计上就<b>不会被读取</b>，也从不出现在页面或导出文件里。
</div>
""",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"""
<div class="ax-side-title">关于</div>
<div class="ax-side-text">{ORG_NAME}<br/>{ORG_SLOGAN}</div>
""",
            unsafe_allow_html=True,
        )


# --------------------------------------------------------------------------
# 主体
# --------------------------------------------------------------------------


def render_app() -> None:
    theme.inject_theme()
    render_sidebar()

    _html(
        components.hero(
            "爱心足迹",
            "看看你在爱心社走过的路——每一场活动、每一个小时，都被认真记下来。",
        )
    )

    _apply_query_params()
    name, sid, clicked = _query_panel()
    _handle_submit(clicked, name, sid)

    error = st.session_state.pop(_ERROR_KEY, None)
    if error:
        _html(
            components.note(
                "error",
                "查询过程中出错了，请稍后重试。如果一直失败，请联系技术负责人反馈。<br/>"
                f"<span style='font-size:12px;opacity:.75'>技术信息：{components.esc(error)}</span>",
                html=True,
            )
        )

    query = state.stored_query()
    result = state.stored_result()
    if query and result is None and not error:
        result = _execute(query)
        state.store_result(result, query)

    if result is not None and query is not None:
        _render_result(result, query)

    _render_footer()


def _apply_query_params() -> None:
    """支持 ``?name=张三&sid=2300017473`` 直接带参查询，便于分享与调试。

    同一个 (姓名, 学号) 只自动触发一次，避免每次 rerun 重复查询。
    """
    try:
        params = st.query_params
    except Exception:  # noqa: BLE001 - 极老版本或无运行时
        return

    name = str(params.get("name") or "").strip()
    sid = str(params.get("sid") or "").strip()
    if not name or not sid:
        return
    if st.session_state.get("ax_autoloaded") == (name, sid):
        return

    st.session_state["ax_name"] = name
    st.session_state["ax_sid"] = sid
    st.session_state["ax_autoloaded"] = (name, sid)
    st.session_state.pop(_POSTER_KEY, None)
    st.session_state.pop(_PARTNERS_KEY, None)
    state.store_result(None, (name, sid))


def _query_panel():
    with st.container(key="ax-query-panel"):
        st.markdown(
            '<div class="ax-panel-title">查询我的爱心足迹</div>'
            '<div class="ax-panel-hint">姓名与学号需与入社问卷填写的一致</div>',
            unsafe_allow_html=True,
        )
        col1, col2 = st.columns(2, gap="medium")
        name = col1.text_input("姓名", key="ax_name", placeholder="例：张三")
        sid = col2.text_input("学号", key="ax_sid", placeholder="例：2300017473")
        clicked = st.button("生成我的爱心足迹", key="ax_submit", width="stretch")
    return name.strip(), sid.strip(), clicked


def _handle_submit(clicked: bool, name: str, sid: str) -> None:
    if clicked:
        if not name or not sid:
            st.session_state[_FLASH_KEY] = "请同时填写姓名和学号哦。"
        else:
            st.session_state[_FLASH_KEY] = None
            st.session_state.pop(_POSTER_KEY, None)
            st.session_state.pop(_PARTNERS_KEY, None)
            state.store_result(None, (name, sid))

    flash = st.session_state.pop(_FLASH_KEY, None)
    if flash:
        _html(components.note("warn", flash))


def _execute(query: tuple[str, str]) -> MemberQueryResult | None:
    name, sid = query
    placeholder = animations.heartbeat()
    try:
        result = state.run_query(name, sid)
    except Exception as exc:  # noqa: BLE001 - 统一转成页面提示
        st.session_state[_ERROR_KEY] = str(exc)
        return None
    finally:
        placeholder.empty()
    return result


# --------------------------------------------------------------------------
# 结果
# --------------------------------------------------------------------------


def _render_result(result: MemberQueryResult, query: tuple[str, str]) -> None:
    as_of = state.data_as_of()

    if not result.found:
        _html(components.not_found_note(result.not_found_reason))
        _render_issues(result)
        return

    member = result.member
    assert member is not None
    footprint = summarize(result.participations)
    membership = derive_membership_date(member)

    join_label = format_date(membership.date_ms) or join_term_hint(member) or "未记录"
    base_labels = [
        {"base_25_26": "25-26 学年", "base_26_27": "26-27 学年"}.get(k, k)
        for k in member.base_keys
    ]

    _html(
        components.result_header(
            member.name,
            join_label=join_label,
            join_badge=components.join_badge(membership.is_estimated),
            days=days_since(membership.date_ms),
            base_labels=base_labels,
            duplicate=member.has_duplicate_records,
        )
    )

    # 统计卡：数字滚动增长（一次性动效）
    slots = st.empty()
    animations.count_up_stats(slots, components.stat_items(footprint))

    _render_issues(result)

    if footprint.activity_count == 0:
        # 一条活动记录都没有：时间轴、CSV、海报全都无从谈起。
        # 与其摆一排 0（并且 old 代码会在这里抛 st.html 空 body 把整页打挂），
        # 不如给一句话，顺手指个方向。
        _html(components.empty_footprint_note())
        return

    # ---- 参与浓度 ----
    _html(components.section("参与浓度"))
    breakdown = group_breakdown(result.participations)
    _html(
        components.metric_row(
            [
                ("足迹部组", str(len(footprint.groups))),
                ("活动场次", str(footprint.distinct_activity_count)),
                ("活动记录", f"{footprint.activity_count} 条"),
            ]
        )
    )
    _html(components.group_pills(footprint.groups))
    if footprint.count_by_term:
        _html(components.term_chips(footprint.count_by_term))
    _render_partners(result, member)

    # ---- 时间轴 ----
    _html(
        components.section(
            "爱心足迹",
            f"共 {footprint.activity_count} 条记录，按时间排列",
        )
    )
    _html(components.timeline(footprint.timeline))

    # ---- 导出与分享 ----
    _html(components.section("带走这份足迹"))
    _render_tools(result, member, footprint, membership, as_of)


def _render_issues(result: MemberQueryResult) -> None:
    if result.errors:
        lines = "<br/>".join(f"· {components.esc(e)}" for e in result.errors[:6])
        _html(
            components.note(
                "warn",
                f"有 {len(result.errors)} 处数据没能取到，结果可能不完整：<br/>{lines}",
                html=True,
            )
        )
    if result.low_confidence_count:
        _html(
            components.note(
                "info",
                f"有 {result.low_confidence_count} 条记录只凭姓名匹配、缺乏更硬的证据"
                "（档案关联或学号），已标记为「待确认」。同名社员较多时请留意核对。",
            )
        )


def _render_partners(result: MemberQueryResult, member) -> None:
    with st.container(key="ax-partners-box"):
        st.markdown(
            '<div class="ax-panel-title">与谁同行</div>'
            '<div class="ax-panel-hint">统计和你出现在同一场活动里的其他社员（按需计算，可能需要几秒）</div>',
            unsafe_allow_html=True,
        )
        if st.button("统计同行伙伴", key="ax_partners_btn", width="stretch"):
            with st.spinner("正在翻活动签到表…"):
                count, truncated = count_co_partners(
                    state.get_client(),
                    state.load_registry(),
                    member,
                    result.participations,
                )
            st.session_state[_PARTNERS_KEY] = (count, truncated)

        cached = st.session_state.get(_PARTNERS_KEY)
        if cached is not None:
            count, truncated = cached
            if count is None:
                _html(components.note("info", "暂时没能统计出同行伙伴。"))
            else:
                suffix = "（仅统计了最近若干场活动）" if truncated else ""
                _html(
                    components.metric_row([("曾与你同场活动的伙伴", f"{count} 位")])
                    + components.note("info", f"这些人和你在同一场活动里出过勤{suffix}。")
                )


def _render_tools(result, member, footprint, membership, as_of: date) -> None:
    with st.container(key="ax-export-box"):
        col1, col2 = st.columns(2, gap="medium")

        with col1:
            st.download_button(
                "下载活动记录 CSV",
                data=_build_csv(member, result.participations),
                file_name=f"{member.name}_活动记录_{as_of:%Y%m%d}.csv",
                mime="text/csv",
                key="ax_csv",
                width="stretch",
            )

        with col2:
            if st.button("生成分享海报", key="ax_poster_btn", width="stretch"):
                with st.spinner("正在排版海报…"):
                    st.session_state[_POSTER_KEY] = render_poster(
                        name=member.name,
                        footprint=footprint,
                        membership=membership,
                        data_as_of=as_of,
                        subtitle=join_term_hint(member),
                    )
            poster = st.session_state.get(_POSTER_KEY)
            if poster:
                st.download_button(
                    "下载海报 PNG",
                    data=poster,
                    file_name=poster_file_name(member.name, as_of),
                    mime="image/png",
                    key="ax_poster_dl",
                    width="stretch",
                )

        poster = st.session_state.get(_POSTER_KEY)
        if poster:
            st.image(poster, width="stretch")


def _build_csv(member, participations) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["姓名", "学号", "活动名称", "所属部组", "学期", "参与日期", "志愿学时", "置信度", "感想"]
    )
    for p in participations:
        writer.writerow(
            [
                member.name,
                member.sid,
                p.activity_name,
                p.group or UNKNOWN_GROUP_LABEL,
                p.term or "",
                format_date(p.event_time_ms),
                f"{p.hours:g}",
                {"high": "高", "medium": "中", "low": "低"}.get(p.confidence, p.confidence),
                p.reflection.replace("\n", " "),
            ]
        )
    return buffer.getvalue().encode("utf-8-sig")


def _html(markup: str) -> None:
    """渲染一段 HTML；空串直接跳过。

    ⚠️ Streamlit 对空 body 会抛 ``st.html body cannot be empty``，把整页打挂。
    组件在「无事可画」时习惯返回空串（例如一个部组标签都没有时 group_pills 返回 ""），
    所以统一在这里兜住 —— 这是本模块唯一的 st.html 出口。
    """
    if markup and markup.strip():
        st.html(markup)


def _render_footer() -> None:
    _html(
        components.footnote(
            [
                f"{ORG_NAME} · {ORG_SLOGAN}",
                "本系统数据来自飞书多维表格，仅供个人查询自己的活动记录。",
            ]
        )
    )
