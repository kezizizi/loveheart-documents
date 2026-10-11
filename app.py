"""爱心社「爱心足迹」查询系统 —— Streamlit 入口。

本地运行：
    streamlit run app.py

演示模式（不需要飞书密钥，用内置假数据预览界面）：
    AX_DEMO=1 streamlit run app.py        # macOS / Linux
    set AX_DEMO=1 && streamlit run app.py # Windows cmd
"""

from __future__ import annotations

import streamlit as st

from config import ORG_NAME
from ui import pages, state, theme

st.set_page_config(
    page_title=f"爱心足迹 · {ORG_NAME}",
    page_icon=theme.page_icon(),
    layout="centered",
    initial_sidebar_state="collapsed",
)


def main() -> None:
    try:
        pages.render_app()
    except Exception as exc:  # noqa: BLE001 - 兜底，避免整页白屏
        theme.inject_theme()
        hint = ""
        if "凭证" in str(exc) or "secrets" in str(exc).lower():
            hint = (
                "看起来还没有配置飞书密钥。请在 <code>.streamlit/secrets.toml</code> 的 "
                "<code>[feishu]</code> 段填写 app_id 与 app_secret。<br/>"
                "想先看看界面，可以设 <code>AX_DEMO=1</code> 用演示数据启动。"
            )
        st.markdown(
            f"<div class='ax-note ax-note-error'><div><b>启动失败</b><br/>{hint}"
            f"<br/><span style='font-size:12px;opacity:.75'>技术信息：{exc}</span></div></div>",
            unsafe_allow_html=True,
        )


if __name__ == "__main__":
    main()


def _demo_flag() -> bool:  # pragma: no cover - 供文档与调试参考
    return state.demo_mode()
