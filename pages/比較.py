"""
多股走勢疊圖比較頁面。

功能：
  1. 最多 4 支股票同時比較
  2. 股價標準化（第一日 = 100），讓不同價位股票可直接比較漲跌幅
  3. 並發抓取（asyncio.gather），部分失敗時顯示警告但不中斷
  4. 技術指標評分比較表
"""

from __future__ import annotations

import asyncio
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from streamlit_searchbox import st_searchbox

from app import analyzer
from app.twse_client import (
    InsufficientDataError,
    StockNotFoundError,
    TWSEUnavailableError,
    fetch_stock_data,
    fetch_stock_list,
    fetch_stock_name,
)

load_dotenv()

st.set_page_config(
    page_title="多股走勢比較",
    page_icon="📊",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Stock search helpers (mirror of streamlit_app.py — kept local to avoid
# cross-page Streamlit cache conflicts)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=86400)
def _load_stock_list() -> list[tuple[str, str]]:
    return fetch_stock_list()


def _search_stocks(query: str) -> list[str]:
    if not query or not query.strip():
        return []
    query = query.strip()
    stock_list = _load_stock_list()
    results = []
    if query.isdigit():
        for code, name in stock_list:
            if code.startswith(query):
                results.append(f"{code} {name}")
    else:
        for code, name in stock_list:
            if query in name:
                results.append(f"{code} {name}")
    return results[:50]


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

@st.cache_data(ttl=600)
def _cached_fetch(stock_no: str) -> tuple[str, pd.DataFrame | None, str, str]:
    """同步包裝，供 st.cache_data 使用（10 分鐘 cache，避免重複打 TWSE）。"""
    async def _inner():
        try:
            df, name = await asyncio.gather(
                fetch_stock_data(stock_no),
                fetch_stock_name(stock_no),
            )
            return stock_no, df, name, ""
        except StockNotFoundError:
            return stock_no, None, "", f"查無股票代號「{stock_no}」"
        except InsufficientDataError as exc:
            return stock_no, None, "", f"「{stock_no}」資料不足：{exc}"
        except TWSEUnavailableError:
            return stock_no, None, "", f"「{stock_no}」TWSE API 無法存取"
    return asyncio.run(_inner())


def _fetch_all(stock_nos: list[str]):
    results = []
    for sno in stock_nos:
        results.append(_cached_fetch(sno))
        time.sleep(0.5)
    return results


# ---------------------------------------------------------------------------
# Chart builder
# ---------------------------------------------------------------------------

_LINE_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]


def _build_overlay_chart(series: list[tuple[str, list[str], list[float]]]) -> go.Figure:
    """
    Args:
        series: list of (label, dates, normalized_closes)
    """
    fig = go.Figure()
    for (label, dates, norm_closes), color in zip(series, _LINE_COLORS):
        fig.add_trace(go.Scatter(
            x=dates,
            y=norm_closes,
            mode="lines",
            name=label,
            line={"color": color, "width": 2},
            hovertemplate="%{x}<br>" + label + "：%{y:+.2f}%<extra></extra>",
        ))

    fig.add_hline(y=0, line_dash="dot", line_color="gray", line_width=1)

    fig.update_layout(
        title="股價走勢比較（相對漲跌幅，起始日 = 0%）",
        xaxis_title="日期",
        yaxis_title="漲跌幅（%）",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.08, "x": 0},
        height=420,
        margin={"t": 70, "b": 40},
    )
    return fig


# ---------------------------------------------------------------------------
# Score table builder
# ---------------------------------------------------------------------------

def _build_score_table(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows).set_index("股票")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _setup_sidebar() -> None:
    st.markdown("""
    <style>
    [data-testid="stSidebarNav"]::before {
        content: "📈 台股助理";
        display: block;
        margin: 1.2rem 0.75rem 0.4rem;
        font-size: 1.15rem;
        font-weight: 700;
        letter-spacing: 0.02em;
    }
    [data-testid="stSidebarNav"] a {
        border-radius: 0.5rem;
    }
    </style>
    """, unsafe_allow_html=True)
    with st.sidebar:
        st.caption("資料來源：TWSE｜AI 說明僅供參考，非投資建議")


def main() -> None:
    _setup_sidebar()
    _load_stock_list()  # 頁面載入時預先暖 cache，避免首次打字時卡頓
    st.title("📊 多股走勢比較")
    st.caption("同時比較最多 4 支股票的近期走勢與技術指標評分。股價已標準化，起始日收盤價 = 100。")

    # --- Stock inputs (2x2 grid) ---
    col1, col2 = st.columns(2)
    col3, col4 = st.columns(2)

    with col1:
        s1 = st_searchbox(_search_stocks, key="cmp_s1", placeholder="股票 1（必填）", label="股票 1")
    with col2:
        s2 = st_searchbox(_search_stocks, key="cmp_s2", placeholder="股票 2（必填）", label="股票 2")
    with col3:
        s3 = st_searchbox(_search_stocks, key="cmp_s3", placeholder="股票 3（選填）", label="股票 3")
    with col4:
        s4 = st_searchbox(_search_stocks, key="cmp_s4", placeholder="股票 4（選填）", label="股票 4")

    selected_raw = [s1, s2, s3, s4]
    selected = [s.split()[0] for s in selected_raw if s]

    if len(selected) < 2:
        st.info("請至少輸入 2 支股票後開始比較。")
        return

    # deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for sno in selected:
        if sno not in seen:
            seen.add(sno)
            unique.append(sno)
    selected = unique

    if st.button("開始比較", type="primary"):
        with st.spinner(f"正在抓取 {len(selected)} 支股票資料..."):
            results = _fetch_all(selected)

        # --- Process results ---
        overlay_series: list[tuple[str, list[str], list[float]]] = []
        score_rows: list[dict] = []
        errors: list[str] = []

        for stock_no, df, name, err in results:
            if df is None:
                errors.append(err)
                continue

            label = f"{stock_no} {name}".strip() if name else stock_no
            closes = df["close"]
            normalized = (((closes / closes.iloc[0]) - 1) * 100).round(2).tolist()
            dates = [d.strftime("%Y-%m-%d") for d in df["date"]]
            overlay_series.append((label, dates, normalized))

            result = analyzer.analyze(df, stock_no)
            s = result.scores
            score_rows.append({
                "股票": label,
                "MA交叉": f"{s.ma_crossover_score}/25",
                "成交量": f"{s.volume_surge_score}/25",
                "價格趨勢": f"{s.price_trend_score}/20",
                "RSI": f"{s.rsi_score}/20",
                "穩定性": f"{s.stability_score}/10",
                "總分": result.total_score,
                "結論": result.verdict,
            })

        for err in errors:
            st.warning(err)

        if len(overlay_series) < 1:
            st.error("所有股票資料均抓取失敗，無法顯示比較圖。")
            return

        # --- Chart ---
        st.plotly_chart(_build_overlay_chart(overlay_series), use_container_width=True)

        # --- Score table ---
        st.subheader("技術指標評分比較")
        df_scores = _build_score_table(score_rows)
        st.dataframe(
            df_scores.style.map(
                lambda v: "color: green; font-weight: bold" if v == "值得關注" else
                          "color: red" if v == "暫不關注" else "",
                subset=["結論"],
            ),
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
