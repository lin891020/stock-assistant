"""
Streamlit frontend for Stock Assistant.

直接 import app/ 模組，不透過 FastAPI HTTP，避免多一層網路延遲。

頁面結構：
  1. 標題 + 股票代號輸入框
  2. 分析後：資料區間與總評分 badge
  3. 股價折線圖（含 5日/20日 MA）
  4. 成交量柱狀圖
  5. 各指標評分 progress bar
  6. LLM 說明
"""

import asyncio
import json
import os

import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from streamlit_searchbox import st_searchbox

from app import analyzer
from app.llm import stream_analysis
from app.models import LLMOutput
from app.twse_client import (
    InsufficientDataError,
    StockNotFoundError,
    TWSEUnavailableError,
    fetch_stock_data,
    fetch_stock_list,
    fetch_stock_name,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Page config — 必須是第一個 Streamlit 呼叫
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="台股關注度分析",
    page_icon="📈",
    layout="wide",
)

_provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
_MODEL_LABEL = "GitHub Models (gpt-4o-mini)" if _provider == "github" else "Claude"


@st.cache_data(ttl=86400)
def _load_stock_list() -> list[tuple[str, str]]:
    return fetch_stock_list()


def _search_stocks(query: str) -> list[str]:
    """搜尋函式供 st_searchbox 使用。

    數字輸入 → 股票代號從頭比對；中文輸入 → 股票名稱任意位置比對。
    最多回傳 50 筆。
    """
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
# Async helpers
# ---------------------------------------------------------------------------

async def _fetch_data_and_name(stock_no: str):
    """並發抓取股票資料與名稱，名稱失敗時靜默回傳空字串。"""
    async def safe_fetch_name(sno: str) -> str:
        try:
            return await fetch_stock_name(sno)
        except Exception:
            return ""

    df, name = await asyncio.gather(
        fetch_stock_data(stock_no),
        safe_fetch_name(stock_no),
    )
    return df, name


# ---------------------------------------------------------------------------
# Chart builders
# ---------------------------------------------------------------------------

def _build_price_chart(
    dates: list[str],
    closes: list[float],
    ma5_series: list[float],
    ma20_series: list[float],
) -> go.Figure:
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=dates,
        y=closes,
        mode="lines",
        name="收盤價",
        line={"color": "#1f77b4", "width": 2},
    ))

    fig.add_trace(go.Scatter(
        x=dates,
        y=ma5_series,
        mode="lines",
        name="MA5（5日均線）",
        line={"color": "#ff7f0e", "width": 1.5, "dash": "dash"},
    ))

    fig.add_trace(go.Scatter(
        x=dates,
        y=ma20_series,
        mode="lines",
        name="MA20（20日均線）",
        line={"color": "#2ca02c", "width": 1.5, "dash": "dot"},
    ))

    fig.update_layout(
        title="股價走勢",
        xaxis_title="日期",
        yaxis_title="收盤價（元）",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.08, "x": 0},
        height=350,
        margin={"t": 60, "b": 40},
    )
    return fig


def _build_volume_chart(dates: list[str], volumes: list[float]) -> go.Figure:
    fig = go.Figure()

    volumes_wan = [v / 10_000 for v in volumes]
    fig.add_trace(go.Bar(
        x=dates,
        y=volumes_wan,
        name="成交量",
        marker_color="#aec7e8",
    ))

    fig.update_layout(
        title="成交量",
        xaxis_title="日期",
        yaxis_title="成交股數（萬股）",
        height=250,
        margin={"t": 40, "b": 40},
    )
    fig.update_traces(hovertemplate="%{x}<br>成交量：%{y:,.0f} 萬股<extra></extra>")
    fig.update_yaxes(tickformat=",d")
    return fig


# ---------------------------------------------------------------------------
# Score display
# ---------------------------------------------------------------------------

def _render_score_bars(scores, total_score: int) -> None:
    st.subheader("指標評分明細")

    indicators = [
        ("MA 黃金交叉", scores.ma_crossover_score, 25,
         "5日MA vs 20日MA"),
        ("成交量放大", scores.volume_surge_score, 25,
         f"近5日/近20日均量 = {scores.volume_surge_ratio:.2f}x"),
        ("價格趨勢", scores.price_trend_score, 20,
         f"近30日漲跌幅 = {scores.price_trend_pct * 100:.2f}%"),
        ("RSI 14日", scores.rsi_score, 20,
         f"RSI = {scores.rsi:.1f}（健康區間 45–65）"),
        ("價格穩定性", scores.stability_score, 10,
         f"變異係數 = {scores.stability_cv:.4f}（門檻 < 0.05）"),
    ]

    for name, score, max_score, detail in indicators:
        col1, col2 = st.columns([3, 1])
        with col1:
            st.caption(f"{name}　{detail}")
            st.progress(score / max_score)
        with col2:
            st.metric(label="", value=f"{score} / {max_score}")

    st.divider()
    st.metric("總評分", f"{total_score} / 100", help="≥ 60 分判定為「值得關注」")


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

def main() -> None:
    st.title("📈 台股關注度分析")
    st.caption("輸入股票代號，系統將從 TWSE 抓取近 60 筆交易日資料，計算技術指標後由 AI 生成分析說明。")

    # --- Input ---
    selected = st_searchbox(
        _search_stocks,
        key="stock_search",
        placeholder="輸入代號或名稱，例如：2330 或 台積電",
        label="股票代號或名稱",
    )

    if not selected:
        return

    stock_no = selected.split()[0]

    # --- Fetch & analyze ---
    with st.spinner("正在從 TWSE 抓取資料..."):
        try:
            df, stock_name = asyncio.run(_fetch_data_and_name(stock_no))
        except StockNotFoundError:
            st.error(f"查無股票代號「{stock_no}」，請確認代號是否正確（目前僅支援上市股票）。")
            return
        except InsufficientDataError as exc:
            st.warning(f"資料不足，無法計算完整指標：{exc}")
            return
        except TWSEUnavailableError:
            st.error("TWSE API 目前無法存取，請稍後再試。")
            return

    result = analyzer.analyze(df, stock_no)

    # --- Summary header ---
    verdict_color = "green" if result.verdict == "值得關注" else "red"
    display_name = f"{stock_no} {stock_name}" if stock_name else stock_no
    st.markdown(
        f"### 股票 {display_name}　"
        f":{verdict_color}[**{result.verdict}**]　"
        f"評分：**{result.total_score}** / 100"
    )
    st.caption(f"資料區間：{result.start_date} ～ {result.end_date}（共 {len(df)} 個交易日）")

    # --- Charts ---
    st.plotly_chart(
        _build_price_chart(result.dates, result.closes, result.ma5_series, result.ma20_series),
        use_container_width=True,
    )
    st.plotly_chart(
        _build_volume_chart(result.dates, result.volumes),
        use_container_width=True,
    )

    st.divider()

    # --- LLM analysis (streaming) ---
    st.subheader("AI 分析說明")
    st.caption(f"以下說明由 {_MODEL_LABEL} 根據上方量化指標生成，僅供參考，非投資建議。")

    stream_placeholder = st.empty()
    chunks: list[str] = []
    for chunk in stream_analysis(result):
        chunks.append(chunk)
        stream_placeholder.markdown("".join(chunks) + "▌")
    raw_output = "".join(chunks)
    stream_placeholder.empty()

    try:
        llm = LLMOutput(**json.loads(raw_output))
    except Exception:
        st.markdown(raw_output)
        return

    verdict_color = "green" if llm.verdict == "值得關注" else "red"
    tab_retail, tab_inst = st.tabs(["散戶模式", "法人模式"])

    with tab_retail:
        st.markdown(f":{verdict_color}[**{llm.verdict}**]　信心：**{llm.confidence}**")
        st.markdown(llm.summary)
        st.markdown("**⚠️ 風險提示**")
        st.markdown("\n".join(f"- {r}" for r in llm.risks))

    with tab_inst:
        st.markdown(f":{verdict_color}[**{llm.verdict}**]　信心：**{llm.confidence}**")
        st.divider()
        _render_score_bars(result.scores, result.total_score)
        st.divider()
        st.markdown("**關鍵訊號**")
        st.markdown("\n".join(f"- {s}" for s in llm.key_signals))
        st.markdown("**⚠️ 風險提示**")
        st.markdown("\n".join(f"- {r}" for r in llm.risks))



if __name__ == "__main__":
    main()
