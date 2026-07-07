import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime
from openai import OpenAI
import pdfplumber
import io
import requests
import zipfile
import xml.etree.ElementTree as ET
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

st.set_page_config(
    page_title="국내 주식 대시보드",
    page_icon="📈",
    layout="wide"
)

STOCKS = {
    "삼성전자": "005930.KS",
    "SK하이닉스": "000660.KS",
    "LG에너지솔루션": "373220.KS",
    "삼성바이오로직스": "207940.KS",
    "현대차": "005380.KS",
    "POSCO홀딩스": "005490.KS",
    "카카오": "035720.KS",
    "NAVER": "035420.KS",
    "셀트리온": "068270.KS",
    "기아": "000270.KS",
}

# ── 사이드바 ──────────────────────────────────────────────
st.sidebar.header("⚙️ 설정")

# API Key 입력
st.sidebar.subheader("🔑 OpenAI API Key")
api_key_input = st.sidebar.text_input(
    "API Key를 입력하세요",
    type="password",
    placeholder="sk-...",
    help="OpenAI API Key는 저장되지 않으며 현재 세션에서만 사용됩니다."
)
if api_key_input:
    st.session_state["openai_api_key"] = api_key_input

st.sidebar.subheader("🏛️ OpenDART API Key")
dart_key_input = st.sidebar.text_input(
    "DART API Key를 입력하세요",
    type="password",
    placeholder="발급받은 API Key 입력",
    help="금융감독원 OpenDART(dart.fss.or.kr)에서 발급받은 API Key입니다."
)
if dart_key_input:
    st.session_state["dart_api_key"] = dart_key_input

st.sidebar.subheader("📧 이메일 설정 (SMTP)")
smtp_sender = st.sidebar.text_input(
    "발신 이메일 (Gmail)",
    placeholder="example@gmail.com",
    key="smtp_sender_input",
    help="Gmail 주소를 입력하세요."
)
if smtp_sender:
    st.session_state["smtp_sender"] = smtp_sender

smtp_password = st.sidebar.text_input(
    "앱 비밀번호",
    type="password",
    placeholder="Gmail 앱 비밀번호 16자리",
    key="smtp_password_input",
    help="Google 계정 → 보안 → 앱 비밀번호에서 발급받으세요."
)
if smtp_password:
    st.session_state["smtp_password"] = smtp_password

st.sidebar.divider()

period_map = {
    "1개월": "1mo",
    "3개월": "3mo",
    "6개월": "6mo",
    "1년": "1y",
    "2년": "2y",
}
selected_period_label = st.sidebar.selectbox("조회 기간", list(period_map.keys()), index=2)
selected_period = period_map[selected_period_label]

selected_stocks = st.sidebar.multiselect(
    "종목 선택 (차트용)",
    list(STOCKS.keys()),
    default=["삼성전자", "SK하이닉스", "현대차"]
)

st.sidebar.divider()

# PDF 업로드
st.sidebar.subheader("📄 PDF 파일 업로드")
sidebar_pdf = st.sidebar.file_uploader(
    "PDF를 업로드하면 챗봇에서 활용됩니다",
    type=["pdf"],
    key="sidebar_pdf_uploader",
    help="업로드한 PDF는 'PDF 챗봇' 탭에서 질문할 수 있습니다."
)
if sidebar_pdf is not None:
    # UploadedFile 객체 대신 bytes + 메타로 저장 (재렌더링 시 스트림 소진 방지)
    file_key_new = f"{sidebar_pdf.name}_{sidebar_pdf.size}"
    if st.session_state.get("sidebar_pdf_key") != file_key_new:
        sidebar_pdf.seek(0)
        st.session_state["sidebar_pdf_bytes"] = sidebar_pdf.read()
        st.session_state["sidebar_pdf_name"]  = sidebar_pdf.name
        st.session_state["sidebar_pdf_size"]  = sidebar_pdf.size
        st.session_state["sidebar_pdf_key"]   = file_key_new

# ── 데이터 로드 ───────────────────────────────────────────
@st.cache_data(ttl=300)
def load_stock_data(ticker, period):
    stock = yf.Ticker(ticker)
    hist = stock.history(period=period)
    try:
        info = dict(stock.fast_info)
    except Exception:
        info = {}
    return hist, info

@st.cache_data(ttl=300)
def build_stock_context():
    """챗봇에 전달할 주식 컨텍스트 문자열 생성"""
    lines = [f"오늘 날짜: {datetime.now().strftime('%Y년 %m월 %d일')}", ""]
    lines.append("=== 국내 주요 주식 현황 (최근 5거래일 기준) ===")
    for name, ticker in STOCKS.items():
        try:
            hist, _ = load_stock_data(ticker, "1mo")
            if hist.empty:
                continue
            cur  = hist["Close"].iloc[-1]
            prev = hist["Close"].iloc[-2] if len(hist) >= 2 else cur
            chg  = cur - prev
            pct  = (chg / prev) * 100
            vol  = hist["Volume"].iloc[-1]
            high = hist["High"].max()
            low  = hist["Low"].min()
            ma5  = hist["Close"].tail(5).mean()
            ma20 = hist["Close"].tail(20).mean() if len(hist) >= 20 else None
            ma20_str = f"{ma20:,.0f}원" if ma20 else "데이터 부족"
            lines.append(
                f"[{name} / {ticker}]\n"
                f"  현재가: {cur:,.0f}원  전일대비: {chg:+,.0f}원 ({pct:+.2f}%)\n"
                f"  거래량: {vol:,.0f}주  1개월 고가: {high:,.0f}원  1개월 저가: {low:,.0f}원\n"
                f"  5일 이평: {ma5:,.0f}원  20일 이평: {ma20_str}"
            )
        except Exception as e:
            lines.append(f"[{name}] 데이터 오류: {e}")
    return "\n".join(lines)

# ── 메인 타이틀 ───────────────────────────────────────────
st.title("📈 국내 주식 대시보드")
st.markdown("KOSPI 주요 종목 10개의 실시간 데이터를 제공합니다.")

# ── 탭 구성 ──────────────────────────────────────────────
tab_dashboard, tab_chat, tab_pdf, tab_news, tab_dart, tab_report = st.tabs([
    "📊 대시보드", "🤖 AI 챗봇 (주식)", "📄 PDF 챗봇", "📰 기업 뉴스", "🏛️ 공시 정보", "📧 보고서 발송"
])

# ════════════════════════════════════════════════════════
# TAB 1: 대시보드
# ════════════════════════════════════════════════════════
with tab_dashboard:
    st.subheader("📊 종목 현황 요약")
    with st.spinner("데이터 불러오는 중..."):
        summary_rows = []
        for name, ticker in STOCKS.items():
            try:
                hist, _ = load_stock_data(ticker, "5d")
                if hist.empty:
                    continue
                current_price = hist["Close"].iloc[-1]
                prev_price    = hist["Close"].iloc[-2] if len(hist) >= 2 else current_price
                change        = current_price - prev_price
                change_pct    = (change / prev_price) * 100
                volume        = hist["Volume"].iloc[-1]
                summary_rows.append({
                    "종목명":       name,
                    "현재가 (₩)":  f"{current_price:,.0f}",
                    "전일 대비 (₩)": f"{change:+,.0f}",
                    "등락률 (%)":   f"{change_pct:+.2f}%",
                    "거래량":       f"{volume:,.0f}",
                })
            except Exception:
                summary_rows.append({
                    "종목명": name, "현재가 (₩)": "오류",
                    "전일 대비 (₩)": "-", "등락률 (%)": "-", "거래량": "-",
                })

    df_summary = pd.DataFrame(summary_rows)
    st.dataframe(df_summary, use_container_width=True, hide_index=True)

    if selected_stocks:
        st.subheader(f"📉 주가 추이 ({selected_period_label})")
        fig = go.Figure()
        with st.spinner("차트 데이터 로딩 중..."):
            for name in selected_stocks:
                hist, _ = load_stock_data(STOCKS[name], selected_period)
                if not hist.empty:
                    fig.add_trace(go.Scatter(
                        x=hist.index, y=hist["Close"], mode="lines", name=name,
                        hovertemplate="%{x}<br>%{y:,.0f}원<extra>" + name + "</extra>"
                    ))
        fig.update_layout(
            xaxis_title="날짜", yaxis_title="종가 (₩)", hovermode="x unified",
            height=480,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("🕯️ 캔들스틱 차트")
        candle_stock = st.selectbox("종목 선택", selected_stocks)
        if candle_stock:
            hist, _ = load_stock_data(STOCKS[candle_stock], selected_period)
            if not hist.empty:
                fig_c = go.Figure(data=[go.Candlestick(
                    x=hist.index,
                    open=hist["Open"], high=hist["High"],
                    low=hist["Low"],  close=hist["Close"],
                    name=candle_stock
                )])
                fig_c.update_layout(
                    xaxis_title="날짜", yaxis_title="가격 (₩)", height=420,
                    xaxis_rangeslider_visible=False,
                    margin=dict(l=0, r=0, t=10, b=0),
                )
                st.plotly_chart(fig_c, use_container_width=True)

    st.subheader("📦 최근 거래량 비교")
    vol_data = []
    for name, ticker in STOCKS.items():
        try:
            hist, _ = load_stock_data(ticker, "5d")
            if not hist.empty:
                vol_data.append({"종목": name, "거래량": int(hist["Volume"].iloc[-1])})
        except Exception:
            pass
    if vol_data:
        df_vol = pd.DataFrame(vol_data).sort_values("거래량", ascending=False)
        fig_bar = px.bar(df_vol, x="종목", y="거래량", color="거래량",
                         color_continuous_scale="Blues", height=350)
        fig_bar.update_layout(margin=dict(l=0, r=0, t=10, b=0), coloraxis_showscale=False)
        st.plotly_chart(fig_bar, use_container_width=True)

    st.caption(
        f"데이터 출처: Yahoo Finance (yfinance) | "
        f"마지막 갱신: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 캐시 유효시간: 5분"
    )

# ════════════════════════════════════════════════════════
# TAB 2: AI 챗봇
# ════════════════════════════════════════════════════════
with tab_chat:
    st.subheader("🤖 주식 AI 챗봇")
    st.markdown(
        "현재 수집된 **국내 주식 10개 데이터**를 기반으로 질문에 답변합니다.  \n"
        "사이드바에서 **OpenAI API Key**를 먼저 입력해주세요."
    )

    # API Key 상태 표시
    api_key = st.session_state.get("openai_api_key", "")
    if not api_key:
        st.warning("⬅️ 사이드바에서 OpenAI API Key를 입력하면 챗봇을 사용할 수 있습니다.")
    else:
        st.success("✅ API Key가 설정되었습니다. 질문을 입력하세요!")

    # 대화 기록 초기화
    if "chat_messages" not in st.session_state:
        st.session_state["chat_messages"] = []

    # 대화 초기화 버튼
    col1, col2 = st.columns([6, 1])
    with col2:
        if st.button("🗑️ 초기화", use_container_width=True):
            st.session_state["chat_messages"] = []
            st.rerun()

    # 이전 대화 출력
    for msg in st.session_state["chat_messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # 채팅 입력
    user_input = st.chat_input(
        "예) 삼성전자 오늘 주가 어때? / 가장 많이 오른 종목은? / SK하이닉스 매수 타이밍은?",
        disabled=(not api_key)
    )

    if user_input and api_key:
        # 사용자 메시지 추가 & 표시
        st.session_state["chat_messages"].append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # GPT 응답 생성
        with st.chat_message("assistant"):
            with st.spinner("분석 중..."):
                try:
                    stock_context = build_stock_context()

                    system_prompt = f"""당신은 국내 주식 전문 AI 애널리스트입니다.
아래의 실시간 주식 데이터를 바탕으로 사용자의 질문에 친절하고 전문적으로 답변하세요.

답변 원칙:
- 제공된 데이터에 근거하여 객관적으로 분석하세요.
- 수치를 인용할 때는 정확하게 언급하세요.
- 투자는 개인의 판단이므로 최종 결정은 사용자에게 있다고 안내하세요.
- 한국어로 답변하세요.

{stock_context}"""

                    client = OpenAI(api_key=api_key)
                    messages_to_send = [{"role": "system", "content": system_prompt}]
                    # 최근 대화 10턴만 전달 (컨텍스트 절약)
                    for m in st.session_state["chat_messages"][-10:]:
                        messages_to_send.append({"role": m["role"], "content": m["content"]})

                    stream = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=messages_to_send,
                        stream=True,
                        temperature=0.7,
                    )

                    response_text = st.write_stream(stream)

                except Exception as e:
                    error_msg = str(e)
                    if "401" in error_msg or "Incorrect API key" in error_msg:
                        response_text = "❌ API Key가 올바르지 않습니다. 사이드바에서 다시 확인해주세요."
                    elif "429" in error_msg:
                        response_text = "❌ API 요청 한도를 초과했습니다. 잠시 후 다시 시도해주세요."
                    else:
                        response_text = f"❌ 오류가 발생했습니다: {error_msg}"
                    st.error(response_text)

        st.session_state["chat_messages"].append({"role": "assistant", "content": response_text})

# ════════════════════════════════════════════════════════
# TAB 3: PDF 챗봇
# ════════════════════════════════════════════════════════
with tab_pdf:
    st.subheader("📄 PDF 기반 AI 챗봇")
    st.markdown(
        "PDF 파일을 업로드하면 문서 내용을 분석하여 질문에 답변합니다.  \n"
        "사이드바에서 **OpenAI API Key**를 먼저 입력해주세요."
    )

    api_key = st.session_state.get("openai_api_key", "")
    if not api_key:
        st.warning("⬅️ 사이드바에서 OpenAI API Key를 입력하면 챗봇을 사용할 수 있습니다.")
    else:
        st.success("✅ API Key가 설정되었습니다.")

    def extract_pdf_text(file_bytes: bytes) -> tuple[str, int]:
        text_parts = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            total_pages = len(pdf.pages)
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(f"[{i+1}페이지]\n{page_text}")
        return "\n\n".join(text_parts), total_pages

    # 사이드바 bytes 우선, 없으면 탭 내 업로드
    sidebar_bytes = st.session_state.get("sidebar_pdf_bytes")
    sidebar_name  = st.session_state.get("sidebar_pdf_name")
    sidebar_size  = st.session_state.get("sidebar_pdf_size")

    tab_uploaded = st.file_uploader(
        "또는 여기서 PDF를 업로드하세요",
        type=["pdf"],
        help="사이드바에서 업로드해도 동일하게 동작합니다."
    )

    # 사용할 파일 결정 (사이드바 우선)
    if sidebar_bytes:
        active_bytes = sidebar_bytes
        active_name  = sidebar_name
        active_size  = sidebar_size
        st.info(f"📎 사이드바에서 업로드된 파일: **{sidebar_name}**")
    elif tab_uploaded:
        tab_uploaded.seek(0)
        active_bytes = tab_uploaded.read()
        active_name  = tab_uploaded.name
        active_size  = tab_uploaded.size
    else:
        active_bytes = None
        active_name  = None
        active_size  = None

    # PDF 텍스트 추출 및 세션 저장
    if active_bytes:
        file_key = f"{active_name}_{active_size}"
        if st.session_state.get("pdf_file_key") != file_key:
            with st.spinner("PDF 분석 중..."):
                pdf_text, total_pages = extract_pdf_text(active_bytes)
                st.session_state["pdf_text"]     = pdf_text
                st.session_state["pdf_file_key"] = file_key
                st.session_state["pdf_filename"] = active_name
                st.session_state["pdf_pages"]    = total_pages
                st.session_state["pdf_messages"] = []  # 파일 바뀌면 대화 초기화

        pdf_text   = st.session_state.get("pdf_text", "")
        total_pages = st.session_state.get("pdf_pages", 0)
        filename   = st.session_state.get("pdf_filename", "")

        # 파일 정보 표시
        col_info1, col_info2, col_info3 = st.columns(3)
        col_info1.metric("파일명", filename[:25] + ("..." if len(filename) > 25 else ""))
        col_info2.metric("페이지 수", f"{total_pages}페이지")
        col_info3.metric("추출 글자 수", f"{len(pdf_text):,}자")

        if not pdf_text.strip():
            st.error("텍스트를 추출할 수 없는 PDF입니다. (스캔 이미지 PDF는 지원하지 않습니다)")
        else:
            st.divider()

            # 대화 초기화 버튼
            col_a, col_b = st.columns([6, 1])
            with col_b:
                if st.button("🗑️ 초기화", key="pdf_reset", use_container_width=True):
                    st.session_state["pdf_messages"] = []
                    st.rerun()

            # 이전 대화 출력
            if "pdf_messages" not in st.session_state:
                st.session_state["pdf_messages"] = []

            for msg in st.session_state["pdf_messages"]:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

            # 채팅 입력
            pdf_input = st.chat_input(
                "PDF 내용에 대해 질문하세요. 예) 이 문서의 핵심 내용은? / 3페이지 내용 요약해줘",
                disabled=(not api_key),
                key="pdf_chat_input"
            )

            if pdf_input and api_key:
                st.session_state["pdf_messages"].append({"role": "user", "content": pdf_input})
                with st.chat_message("user"):
                    st.markdown(pdf_input)

                with st.chat_message("assistant"):
                    with st.spinner("문서 분석 중..."):
                        try:
                            # gpt-4o-mini: 128k 토큰 지원 → 80,000자(≈20,000 토큰)까지 허용
                            MAX_CHARS = 80000
                            total_len = len(pdf_text)

                            if total_len <= MAX_CHARS:
                                context_text   = pdf_text
                                truncated_note = ""
                            else:
                                # 앞 40% / 중간 20% / 뒤 40% 균등 분배
                                front  = int(MAX_CHARS * 0.4)
                                middle = int(MAX_CHARS * 0.2)
                                back   = MAX_CHARS - front - middle
                                mid_start = (total_len - middle) // 2
                                context_text = (
                                    pdf_text[:front]
                                    + f"\n\n--- (중략: {mid_start - front:,}자 생략) ---\n\n"
                                    + pdf_text[mid_start: mid_start + middle]
                                    + f"\n\n--- (중략: {total_len - mid_start - middle - back:,}자 생략) ---\n\n"
                                    + pdf_text[-back:]
                                )
                                truncated_note = (
                                    f"\n※ 문서({total_len:,}자)가 길어 앞·중간·뒤 구간을 균등 추출했습니다."
                                )

                            system_prompt = f"""당신은 문서 분석 전문 AI 어시스턴트입니다.
아래 PDF 문서 내용을 바탕으로 사용자의 질문에 정확하고 친절하게 답변하세요.

답변 원칙:
- 문서에 있는 내용만 근거로 답변하세요.
- 문서에 없는 내용은 "문서에서 해당 내용을 찾을 수 없습니다"라고 안내하세요.
- 페이지 번호를 언급할 때는 [X페이지] 형식으로 표기하세요.
- 한국어로 답변하세요.
{truncated_note}

=== PDF 문서: {filename} ({total_pages}페이지) ===

{context_text}"""

                            client = OpenAI(api_key=api_key)
                            messages_to_send = [{"role": "system", "content": system_prompt}]
                            for m in st.session_state["pdf_messages"][-10:]:
                                messages_to_send.append({"role": m["role"], "content": m["content"]})

                            stream = client.chat.completions.create(
                                model="gpt-4o-mini",
                                messages=messages_to_send,
                                stream=True,
                                temperature=0.3,
                            )
                            pdf_response = st.write_stream(stream)

                        except Exception as e:
                            error_msg = str(e)
                            if "401" in error_msg or "Incorrect API key" in error_msg:
                                pdf_response = "❌ API Key가 올바르지 않습니다. 사이드바에서 다시 확인해주세요."
                            elif "429" in error_msg:
                                pdf_response = "❌ API 요청 한도를 초과했습니다. 잠시 후 다시 시도해주세요."
                            else:
                                pdf_response = f"❌ 오류가 발생했습니다: {error_msg}"
                            st.error(pdf_response)

                st.session_state["pdf_messages"].append({"role": "assistant", "content": pdf_response})
    if not active_bytes:
        st.info("⬆️ 사이드바 또는 위 업로더에서 PDF 파일을 업로드하면 챗봇이 활성화됩니다.")

# ════════════════════════════════════════════════════════
# TAB 4: 기업 뉴스
# ════════════════════════════════════════════════════════
with tab_news:
    st.subheader("📰 기업 뉴스")
    st.markdown("종목을 선택하면 관련 최신 뉴스를 수집합니다.")

    @st.cache_data(ttl=600)
    def fetch_news(ticker: str) -> list[dict]:
        stock = yf.Ticker(ticker)
        raw = stock.news or []
        results = []
        for item in raw:
            content = item.get("content", {})
            title   = content.get("title", item.get("title", "제목 없음"))
            summary = content.get("summary", "")
            pub_raw = content.get("pubDate", "")
            # 발행일 파싱
            try:
                pub_dt  = datetime.strptime(pub_raw, "%Y-%m-%dT%H:%M:%SZ")
                pub_str = pub_dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                pub_str = pub_raw[:16] if pub_raw else "-"
            # URL
            click_url = ""
            for ch in content.get("clickThroughUrl", {}).values() if isinstance(content.get("clickThroughUrl"), dict) else []:
                click_url = ch
                break
            if not click_url:
                click_url = item.get("link", item.get("url", ""))
            # 썸네일
            thumb = ""
            try:
                thumb = content["thumbnail"]["resolutions"][0]["url"]
            except Exception:
                pass
            # 제공사
            provider = content.get("provider", {}).get("displayName", item.get("publisher", ""))
            results.append({
                "title":    title,
                "summary":  summary,
                "pub_date": pub_str,
                "url":      click_url,
                "thumb":    thumb,
                "provider": provider,
            })
        return results

    # 컨트롤 행
    col_sel, col_cnt, col_btn = st.columns([3, 2, 1])
    with col_sel:
        news_stock = st.selectbox("종목 선택", list(STOCKS.keys()), key="news_stock_sel")
    with col_cnt:
        news_count = st.slider("표시할 뉴스 수", min_value=3, max_value=20, value=8, key="news_count_slider")
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        refresh = st.button("🔄 새로고침", use_container_width=True, key="news_refresh")
        if refresh:
            st.cache_data.clear()

    ticker_sel = STOCKS[news_stock]

    with st.spinner(f"{news_stock} 뉴스 불러오는 중..."):
        news_list = fetch_news(ticker_sel)

    if not news_list:
        st.warning("수집된 뉴스가 없습니다. 잠시 후 다시 시도해주세요.")
    else:
        st.caption(f"총 {len(news_list)}건 수집 · 최대 {news_count}건 표시 · 캐시 10분")
        st.divider()

        for idx, news in enumerate(news_list[:news_count]):
            with st.container():
                if news["thumb"]:
                    img_col, txt_col = st.columns([1, 4])
                    with img_col:
                        st.image(news["thumb"], use_container_width=True)
                    with txt_col:
                        _title = f"[{news['title']}]({news['url']})" if news["url"] else news["title"]
                        st.markdown(f"#### {_title}")
                        if news["summary"]:
                            st.markdown(f"{news['summary']}")
                        meta = []
                        if news["provider"]: meta.append(f"📌 {news['provider']}")
                        if news["pub_date"]: meta.append(f"🕐 {news['pub_date']}")
                        st.caption("  |  ".join(meta))
                else:
                    _title = f"[{news['title']}]({news['url']})" if news["url"] else news["title"]
                    st.markdown(f"#### {_title}")
                    if news["summary"]:
                        st.markdown(f"{news['summary']}")
                    meta = []
                    if news["provider"]: meta.append(f"📌 {news['provider']}")
                    if news["pub_date"]: meta.append(f"🕐 {news['pub_date']}")
                    st.caption("  |  ".join(meta))
            if idx < min(news_count, len(news_list)) - 1:
                st.divider()

        # ── 뉴스 기반 AI 챗봇 ───────────────────────────────
        st.divider()
        st.subheader("🤖 뉴스 AI 챗봇")
        st.markdown(f"수집된 **{news_stock}** 뉴스를 바탕으로 질문에 답변합니다.")

        api_key = st.session_state.get("openai_api_key", "")
        if not api_key:
            st.warning("⬅️ 사이드바에서 OpenAI API Key를 입력하면 챗봇을 사용할 수 있습니다.")
        else:
            news_chat_key = f"news_messages_{news_stock}"
            if news_chat_key not in st.session_state:
                st.session_state[news_chat_key] = []

            col_nc1, col_nc2 = st.columns([6, 1])
            with col_nc2:
                if st.button("🗑️ 초기화", key="news_chat_reset", use_container_width=True):
                    st.session_state[news_chat_key] = []
                    st.rerun()

            for msg in st.session_state[news_chat_key]:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

            news_user_input = st.chat_input(
                f"예) {news_stock} 관련 최근 이슈는? / 긍정적인 뉴스 요약해줘 / 주가에 미칠 영향은?",
                key="news_chat_input"
            )

            if news_user_input and api_key:
                st.session_state[news_chat_key].append({"role": "user", "content": news_user_input})
                with st.chat_message("user"):
                    st.markdown(news_user_input)

                with st.chat_message("assistant"):
                    with st.spinner("뉴스 분석 중..."):
                        try:
                            news_context_lines = [
                                f"=== {news_stock} 관련 최신 뉴스 ({len(news_list)}건) ===\n"
                            ]
                            for i, n in enumerate(news_list[:15], 1):
                                news_context_lines.append(
                                    f"[뉴스 {i}] {n['pub_date']} | {n['provider']}\n"
                                    f"제목: {n['title']}\n"
                                    f"요약: {n['summary']}\n"
                                )
                            news_context = "\n".join(news_context_lines)

                            news_system_prompt = f"""당신은 주식 및 기업 뉴스 전문 AI 애널리스트입니다.
아래 수집된 뉴스 데이터를 바탕으로 사용자의 질문에 답변하세요.

답변 원칙:
- 수집된 뉴스에 근거하여 분석하세요.
- 뉴스 번호([뉴스 N])를 인용하여 근거를 명시하세요.
- 투자 판단은 사용자 본인의 책임임을 안내하세요.
- 한국어로 답변하세요.

{news_context}"""

                            client = OpenAI(api_key=api_key)
                            messages_to_send = [{"role": "system", "content": news_system_prompt}]
                            for m in st.session_state[news_chat_key][-10:]:
                                messages_to_send.append({"role": m["role"], "content": m["content"]})

                            stream = client.chat.completions.create(
                                model="gpt-4o-mini",
                                messages=messages_to_send,
                                stream=True,
                                temperature=0.5,
                            )
                            news_response = st.write_stream(stream)

                        except Exception as e:
                            err = str(e)
                            if "401" in err or "Incorrect API key" in err:
                                news_response = "❌ API Key가 올바르지 않습니다."
                            elif "429" in err:
                                news_response = "❌ API 요청 한도를 초과했습니다. 잠시 후 다시 시도해주세요."
                            else:
                                news_response = f"❌ 오류: {err}"
                            st.error(news_response)

                st.session_state[news_chat_key].append({"role": "assistant", "content": news_response})

# ════════════════════════════════════════════════════════
# TAB 5: 공시 정보 (OpenDART)
# ════════════════════════════════════════════════════════
with tab_dart:
    st.subheader("🏛️ 기업 공시 정보 (OpenDART)")
    st.markdown("금융감독원 OpenDART API를 통해 기업 공시를 조회합니다.")

    dart_key = st.session_state.get("dart_api_key", "")
    if not dart_key:
        st.warning("⬅️ 사이드바에서 OpenDART API Key를 입력해주세요.  \n"
                   "[dart.fss.or.kr](https://opendart.fss.or.kr) 에서 무료로 발급받을 수 있습니다.")
        st.stop()

    # 기업코드 매핑 (기본값 + corpcode.csv 우선 적용)
    DEFAULT_CORP_CODES = {
        "삼성전자":       "00126380",
        "SK하이닉스":     "00164779",
        "LG에너지솔루션": "01426674",
        "삼성바이오로직스":"00714802",
        "현대차":         "00164742",
        "POSCO홀딩스":    "00107139",
        "카카오":         "00676033",
        "NAVER":          "00266961",
        "셀트리온":       "00421029",
        "기아":           "00134957",
    }

    @st.cache_data(ttl=3600)
    def load_corp_codes_from_dart(api_key: str) -> dict:
        """OpenDART API에서 전체 기업코드 zip 다운로드 후 dict 반환"""
        url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={api_key}"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            xml_data = z.read("CORPCODE.xml")
        root = ET.fromstring(xml_data)
        mapping = {}
        for item in root.findall("list"):
            name  = (item.findtext("corp_name") or "").strip()
            code  = (item.findtext("corp_code") or "").strip()
            stock = (item.findtext("stock_code") or "").strip()
            if name and code:
                mapping[name] = {"corp_code": code, "stock_code": stock}
        return mapping

    def get_corp_code(stock_name: str, dart_api_key: str) -> str:
        """기업코드 반환: corpcode.csv → DART API → 기본값 순으로 조회"""
        # 1) corpcode.csv 파일이 있으면 우선 사용
        csv_path = os.path.join(os.path.dirname(__file__), "corpcode.csv")
        if os.path.exists(csv_path):
            try:
                df_csv = pd.read_csv(csv_path, dtype=str)
                # 컬럼명 유연하게 처리
                name_col = next((c for c in df_csv.columns if "name" in c.lower() or "회사" in c), None)
                code_col = next((c for c in df_csv.columns if "code" in c.lower() or "코드" in c), None)
                if name_col and code_col:
                    row = df_csv[df_csv[name_col].str.strip() == stock_name]
                    if not row.empty:
                        return str(row.iloc[0][code_col]).zfill(8)
            except Exception:
                pass
        # 2) DART API 전체 코드 조회
        try:
            all_codes = load_corp_codes_from_dart(dart_api_key)
            if stock_name in all_codes:
                return all_codes[stock_name]["corp_code"]
        except Exception:
            pass
        # 3) 기본값
        return DEFAULT_CORP_CODES.get(stock_name, "")

    @st.cache_data(ttl=600)
    def fetch_disclosures(corp_code: str, api_key: str, bgn_de: str, end_de: str, pblntf_ty: str) -> dict:
        url = "https://opendart.fss.or.kr/api/list.json"
        params = {
            "crtfc_key": api_key,
            "corp_code":  corp_code,
            "bgn_de":     bgn_de,
            "end_de":     end_de,
            "pblntf_ty":  pblntf_ty,
            "page_count": 20,
        }
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ── 조회 조건 ──
    col_d1, col_d2, col_d3, col_d4 = st.columns([2, 2, 2, 2])
    with col_d1:
        dart_stock = st.selectbox("종목 선택", list(STOCKS.keys()), key="dart_stock_sel")
    with col_d2:
        PBLNTF_TYPES = {
            "전체": "A",
            "정기공시": "A001",
            "주요사항보고": "B",
            "발행공시": "C",
            "지분공시": "D",
            "기타공시": "E",
            "외부감사": "F",
            "펀드공시": "G",
            "자산유동화": "H",
            "거래소공시": "I",
            "공정위공시": "J",
        }
        dart_pblntf = st.selectbox("공시 유형", list(PBLNTF_TYPES.keys()), key="dart_pblntf_sel")
    with col_d3:
        dart_start = st.date_input(
            "시작일",
            value=datetime(datetime.now().year, 1, 1).date(),
            key="dart_start"
        )
    with col_d4:
        dart_end = st.date_input("종료일", value=datetime.now().date(), key="dart_end")

    if st.button("🔍 공시 조회", use_container_width=True, key="dart_search_btn"):
        corp_code = get_corp_code(dart_stock, dart_key)
        if not corp_code:
            st.error(f"'{dart_stock}'의 기업코드를 찾을 수 없습니다.")
        else:
            with st.spinner(f"{dart_stock} 공시 조회 중..."):
                try:
                    result = fetch_disclosures(
                        corp_code,
                        dart_key,
                        dart_start.strftime("%Y%m%d"),
                        dart_end.strftime("%Y%m%d"),
                        PBLNTF_TYPES[dart_pblntf],
                    )
                    if result.get("status") == "000":
                        disclosures = result.get("list", [])
                        st.session_state["dart_disclosures"] = disclosures
                        st.session_state["dart_stock_name"]  = dart_stock
                        st.session_state["dart_corp_code"]   = corp_code
                    elif result.get("status") == "013":
                        st.session_state["dart_disclosures"] = []
                        st.session_state["dart_stock_name"]  = dart_stock
                        st.info("조회된 공시가 없습니다.")
                    else:
                        st.error(f"오류: {result.get('message', '알 수 없는 오류')}")
                except Exception as e:
                    st.error(f"API 요청 실패: {e}")

    # ── 공시 결과 표시 ──
    disclosures = st.session_state.get("dart_disclosures")
    dart_stock_name = st.session_state.get("dart_stock_name", "")

    if disclosures is not None and len(disclosures) > 0:
        st.caption(f"**{dart_stock_name}** 공시 총 {len(disclosures)}건")
        st.divider()

        for disc in disclosures:
            rcept_no   = disc.get("rcept_no", "")
            report_nm  = disc.get("report_nm", "")
            rcept_dt   = disc.get("rcept_dt", "")
            flr_nm     = disc.get("flr_nm", "")    # 제출인
            rm         = disc.get("rm", "")         # 비고
            dart_url   = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

            # 날짜 포맷
            try:
                dt_str = datetime.strptime(rcept_dt, "%Y%m%d").strftime("%Y-%m-%d")
            except Exception:
                dt_str = rcept_dt

            with st.container():
                c1, c2 = st.columns([5, 1])
                with c1:
                    st.markdown(f"**[{report_nm}]({dart_url})**")
                    meta_parts = []
                    if dt_str:   meta_parts.append(f"🕐 {dt_str}")
                    if flr_nm:   meta_parts.append(f"📋 {flr_nm}")
                    if rm:       meta_parts.append(f"📌 {rm}")
                    st.caption("  |  ".join(meta_parts))
                with c2:
                    st.markdown(f"[원문 보기 ↗]({dart_url})")
            st.divider()

# ════════════════════════════════════════════════════════
# TAB 6: 보고서 작성 & 이메일 발송
# ════════════════════════════════════════════════════════
with tab_report:
    st.subheader("📧 주식 리포트 작성 및 이메일 발송")
    st.markdown("선택한 종목의 **주가 데이터 + 최신 뉴스**를 GPT가 분석하여 보고서를 작성하고 이메일로 발송합니다.")

    api_key      = st.session_state.get("openai_api_key", "")
    smtp_sender  = st.session_state.get("smtp_sender", "")
    smtp_pw      = st.session_state.get("smtp_password", "")

    # ── 설정 상태 표시 ──
    col_s1, col_s2, col_s3 = st.columns(3)
    col_s1.metric("OpenAI API", "✅ 설정됨" if api_key   else "❌ 미설정")
    col_s2.metric("발신 계정",  "✅ 설정됨" if smtp_sender else "❌ 미설정")
    col_s3.metric("앱 비밀번호","✅ 설정됨" if smtp_pw    else "❌ 미설정")

    if not api_key:
        st.warning("⬅️ 사이드바에서 OpenAI API Key를 먼저 입력해주세요.")

    st.divider()

    # ── 보고서 설정 ──
    col_r1, col_r2 = st.columns([3, 2])
    with col_r1:
        report_stocks = st.multiselect(
            "📌 보고서에 포함할 종목",
            list(STOCKS.keys()),
            default=["삼성전자", "SK하이닉스", "현대차"],
            key="report_stocks_sel"
        )
    with col_r2:
        report_period_label = st.selectbox(
            "📅 분석 기간",
            ["1개월", "3개월", "6개월", "1년"],
            index=1,
            key="report_period_sel"
        )
        report_period = {"1개월": "1mo", "3개월": "3mo", "6개월": "6mo", "1년": "1y"}[report_period_label]

    col_r3, col_r4 = st.columns([3, 2])
    with col_r3:
        recipient_email = st.text_input(
            "📬 수신 이메일",
            placeholder="받는 사람 이메일 주소",
            key="recipient_email_input"
        )
    with col_r4:
        report_title = st.text_input(
            "📝 보고서 제목",
            value=f"주식 시장 분석 리포트 ({datetime.now().strftime('%Y-%m-%d')})",
            key="report_title_input"
        )

    # ── 보고서 데이터 수집 함수 ──
    def collect_report_data(stock_names: list, period: str) -> str:
        """선택 종목의 주가 데이터 + 뉴스를 하나의 문자열로 수집"""
        sections = []
        for name in stock_names:
            ticker = STOCKS[name]
            section_lines = [f"\n{'='*50}", f"■ {name} ({ticker})", f"{'='*50}"]

            # 주가 데이터
            try:
                hist, _ = load_stock_data(ticker, period)
                if not hist.empty:
                    cur   = hist["Close"].iloc[-1]
                    prev  = hist["Close"].iloc[-2] if len(hist) >= 2 else cur
                    chg   = cur - prev
                    pct   = (chg / prev) * 100
                    high  = hist["High"].max()
                    low   = hist["Low"].min()
                    vol   = hist["Volume"].mean()
                    ma5   = hist["Close"].tail(5).mean()
                    ma20  = hist["Close"].tail(20).mean() if len(hist) >= 20 else None
                    # 기간 수익률
                    first_price = hist["Close"].iloc[0]
                    period_ret  = (cur - first_price) / first_price * 100

                    section_lines += [
                        f"[주가 현황 - {report_period_label} 기준]",
                        f"  현재가       : {cur:,.0f}원",
                        f"  전일 대비    : {chg:+,.0f}원 ({pct:+.2f}%)",
                        f"  기간 수익률  : {period_ret:+.2f}%",
                        f"  기간 최고가  : {high:,.0f}원",
                        f"  기간 최저가  : {low:,.0f}원",
                        f"  평균 거래량  : {vol:,.0f}주",
                        f"  5일 이동평균 : {ma5:,.0f}원",
                        f"  20일 이동평균: {ma20:,.0f}원" if ma20 else "  20일 이동평균: 데이터 부족",
                    ]
            except Exception as e:
                section_lines.append(f"  주가 데이터 오류: {e}")

            # 뉴스
            try:
                news_items = fetch_news(ticker)
                section_lines.append(f"\n[최신 뉴스 - 최대 5건]")
                for i, n in enumerate(news_items[:5], 1):
                    section_lines.append(
                        f"  {i}. [{n['pub_date']}] {n['title']}\n"
                        f"     {n['summary'][:120] + '...' if len(n['summary']) > 120 else n['summary']}"
                    )
                if not news_items:
                    section_lines.append("  수집된 뉴스 없음")
            except Exception as e:
                section_lines.append(f"  뉴스 수집 오류: {e}")

            sections.append("\n".join(section_lines))

        return "\n".join(sections)

    def generate_report_html(raw_data: str, stock_names: list,
                              period_label: str, title: str, api_key: str) -> str:
        """GPT로 보고서 텍스트 생성 후 HTML 래핑"""
        client = OpenAI(api_key=api_key)
        prompt = f"""당신은 전문 주식 애널리스트입니다.
아래 데이터를 바탕으로 투자자를 위한 주식 분석 보고서를 작성하세요.

보고서 구성:
1. 📋 요약 (전체 시장 흐름 및 핵심 포인트 3~5줄)
2. 📊 종목별 분석 (각 종목: 주가 동향 / 뉴스 이슈 / 투자 관점)
3. ⚠️ 리스크 요인
4. 💡 종합 의견

작성 원칙:
- 수치를 구체적으로 인용하세요.
- 전문적이되 읽기 쉽게 작성하세요.
- 투자 판단은 참고용이며 최종 결정은 투자자 본인에게 있음을 명시하세요.
- 한국어로 작성하세요.

분석 종목: {', '.join(stock_names)}
분석 기간: {period_label}
작성일: {datetime.now().strftime('%Y년 %m월 %d일')}

=== 수집 데이터 ===
{raw_data}"""

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )
        report_md = resp.choices[0].message.content

        # Markdown → HTML (줄바꿈·굵기·헤딩 간단 변환)
        import re
        html_body = report_md
        html_body = re.sub(r"^#{1,3} (.+)$", r"<h3>\1</h3>", html_body, flags=re.MULTILINE)
        html_body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html_body)
        html_body = re.sub(r"\n", "<br>", html_body)

        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: '맑은 고딕', Arial, sans-serif; color: #222; max-width: 800px; margin: 0 auto; padding: 24px; }}
  h1   {{ color: #1a3c6e; border-bottom: 2px solid #1a3c6e; padding-bottom: 8px; }}
  h3   {{ color: #1a3c6e; margin-top: 24px; }}
  .meta {{ color: #666; font-size: 0.9em; margin-bottom: 20px; }}
  .content {{ line-height: 1.8; }}
  .footer {{ margin-top: 40px; padding-top: 12px; border-top: 1px solid #ddd;
             color: #999; font-size: 0.8em; }}
</style>
</head>
<body>
  <h1>📈 {title}</h1>
  <p class="meta">
    분석 종목: <strong>{', '.join(stock_names)}</strong> &nbsp;|&nbsp;
    분석 기간: <strong>{period_label}</strong> &nbsp;|&nbsp;
    작성일: <strong>{datetime.now().strftime('%Y-%m-%d %H:%M')}</strong>
  </p>
  <div class="content">{html_body}</div>
  <div class="footer">
    ※ 본 보고서는 AI가 자동 생성한 참고용 자료입니다. 투자 판단의 최종 책임은 투자자 본인에게 있습니다.<br>
    생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Powered by GPT-4o-mini & Yahoo Finance
  </div>
</body>
</html>"""
        return html, report_md

    def send_email(sender: str, password: str, recipient: str,
                   subject: str, html_content: str, plain_content: str):
        """Gmail SMTP SSL로 HTML 메일 발송"""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = sender
        msg["To"]      = recipient
        msg.attach(MIMEText(plain_content, "plain", "utf-8"))
        msg.attach(MIMEText(html_content,  "html",  "utf-8"))

        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())

    # ── 보고서 생성 버튼 ──
    st.divider()
    col_btn1, col_btn2 = st.columns(2)

    with col_btn1:
        gen_btn = st.button(
            "🤖 보고서 생성",
            disabled=(not api_key or not report_stocks),
            use_container_width=True,
            key="gen_report_btn"
        )
    with col_btn2:
        send_btn = st.button(
            "📤 이메일 발송",
            disabled=(not smtp_sender or not smtp_pw or not recipient_email
                      or "report_html" not in st.session_state),
            use_container_width=True,
            key="send_report_btn"
        )

    # ── 보고서 생성 ──
    if gen_btn:
        if not report_stocks:
            st.error("보고서에 포함할 종목을 1개 이상 선택해주세요.")
        else:
            with st.spinner("데이터 수집 및 보고서 작성 중... (30초~1분 소요)"):
                try:
                    progress = st.progress(0, text="주가 데이터 및 뉴스 수집 중...")
                    raw_data = collect_report_data(report_stocks, report_period)
                    progress.progress(50, text="GPT 보고서 작성 중...")
                    html_content, md_content = generate_report_html(
                        raw_data, report_stocks, report_period_label,
                        report_title, api_key
                    )
                    progress.progress(100, text="완료!")
                    st.session_state["report_html"] = html_content
                    st.session_state["report_md"]   = md_content
                    st.session_state["report_title"] = report_title
                    progress.empty()
                    st.success("✅ 보고서가 생성되었습니다! 아래에서 내용을 확인하고 이메일로 발송하세요.")
                except Exception as e:
                    st.error(f"보고서 생성 오류: {e}")

    # ── 이메일 발송 ──
    if send_btn:
        missing = []
        if not smtp_sender:   missing.append("발신 이메일")
        if not smtp_pw:       missing.append("앱 비밀번호")
        if not recipient_email: missing.append("수신 이메일")
        if missing:
            st.error(f"다음 정보를 입력해주세요: {', '.join(missing)}")
        else:
            with st.spinner(f"{recipient_email} 로 발송 중..."):
                try:
                    send_email(
                        smtp_sender, smtp_pw, recipient_email,
                        st.session_state.get("report_title", report_title),
                        st.session_state["report_html"],
                        st.session_state["report_md"],
                    )
                    st.success(f"✅ 이메일이 **{recipient_email}** 으로 성공적으로 발송되었습니다!")
                    st.balloons()
                except smtplib.SMTPAuthenticationError:
                    st.error("❌ 인증 실패: Gmail 앱 비밀번호를 확인해주세요.\n\n"
                             "Google 계정 → 보안 → 2단계 인증 활성화 → 앱 비밀번호 생성")
                except smtplib.SMTPRecipientsRefused:
                    st.error("❌ 수신 이메일 주소가 올바르지 않습니다.")
                except Exception as e:
                    st.error(f"❌ 발송 실패: {e}")

    # ── 생성된 보고서 미리보기 ──
    if "report_md" in st.session_state:
        st.divider()
        st.subheader("📄 생성된 보고서 미리보기")

        # 다운로드 버튼
        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button(
                "⬇️ HTML 다운로드",
                data=st.session_state["report_html"].encode("utf-8"),
                file_name=f"stock_report_{datetime.now().strftime('%Y%m%d_%H%M')}.html",
                mime="text/html",
                use_container_width=True,
            )
        with col_dl2:
            st.download_button(
                "⬇️ TXT 다운로드",
                data=st.session_state["report_md"].encode("utf-8"),
                file_name=f"stock_report_{datetime.now().strftime('%Y%m%d_%H%M')}.txt",
                mime="text/plain",
                use_container_width=True,
            )

        st.markdown(st.session_state["report_md"])
