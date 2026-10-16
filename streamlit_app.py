import streamlit as st
import pandas as pd
import numpy as np
import datetime
import pytz
import requests
import io

st.set_page_config(page_title="台股每日戰情室 v31.5", layout="wide")

st.markdown("# 🏛️ 台股每日戰情室 v31.5 (Streamlit 雲端伺服器版)")
st.markdown("### 📊 3張 0050正2 ＋ 1張 0050「不清零・滾大張數」操盤室 ｜ ⚡ 個股七大實戰名將雷達")

st.sidebar.markdown("### 🛳️ 母子艦隊參數設定")
in_lots_2x = st.sidebar.number_input("0050正2 持有張數", value=3.0, step=0.1)
in_cost_2x = st.sidebar.number_input("0050正2 平均成本 (元)", value=34.76, step=0.1)
in_lots_1x = st.sidebar.number_input("0050 壓艙石張數", value=1.0, step=0.1)
in_cost_1x = st.sidebar.number_input("0050 平均成本 (元)", value=101.90, step=0.1)
in_cash_pool = st.sidebar.number_input("已收割待買回現金池 (元)", value=0.0, step=1000.0)

twii_now = 23200.0
p_2x = 37.15
p_1x = 109.0

val_2x = int(round(in_lots_2x * 1000 * p_2x))
cost_total_2x = int(round(in_lots_2x * 1000 * in_cost_2x))
pnl_2x = val_2x - cost_total_2x
pnl_2x_pct = round(((p_2x - in_cost_2x) / in_cost_2x) * 100, 2)

val_1x = int(round(in_lots_1x * 1000 * p_1x))
cost_total_1x = int(round(in_lots_1x * 1000 * in_cost_1x))
pnl_1x = val_1x - cost_total_1x
pnl_1x_pct = round(((p_1x - in_cost_1x) / in_cost_1x) * 100, 2)

total_fleet_val = val_2x + val_1x + int(in_cash_pool)
total_pnl = pnl_2x + pnl_1x
ratio_2x_pct = round((val_2x / max(1, total_fleet_val)) * 100, 1)
effective_leverage = round(((val_1x * 1.0 + val_2x * 2.0) / max(1, total_fleet_val)) * 100, 1)

tab1, tab2, tab3 = st.tabs([
    "📊 0050正2 ＋ 0050 母子艦隊滾雪球戰情室", 
    "⚡ 短線操作精選區 (七大實戰名將)", 
    "🏛️ 長期投資波段區"
])

with tab1:
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("### 🏛️ 加權指數司令塔")
        st.metric("加權指數", f"{twii_now:,.1f} 點", "+0.35%")
        st.text("20MA乖離: +1.2% | 60MA乖離: +2.5%\n日KD: K9=65.2 / D9=58.1 (金叉)")
    with col2:
        st.markdown(f"### 🚀 0050正2 (子艦隊)")
        st.metric("現價 / 損益", f"{p_2x} 元", f"{pnl_2x:+:,} 元 ({pnl_2x_pct:+}% )")
        st.text(f"當前市值: {val_2x:,} 元 (佔比 {ratio_2x_pct}%)")
    with col3:
        st.markdown(f"### 🛡️ 0050 壓艙石 ＆ 總艦隊")
        st.metric("艦隊總市值", f"{total_fleet_val:,} 元", f"總獲利 {total_pnl:+:,} 元")
        st.text(f"整體有效曝險: {effective_leverage}%")

    st.markdown("---")
    st.markdown("### 💡 母子艦隊「不清零・滾大張數」今日行動指令")
    st.info("☀️ **建議配置：3 張正2 ＋ 1 張 0050 (多頭主升段，全艦隊續抱)**\n\n目前處於單邊多頭趨勢！手頭 3 張正2 ＋ 1 張 0050 請安心抱緊。下一個高檔收割第 1 張的目標價定錨在 **42.19 元**（大盤布林上軌），若於該處賣出並回測月線買回，可免費多滾出更多股數！")

    st.markdown("#### 🎯 大盤三層指標 ➔ 0050正2 波段收割與回補精算表")
    df_t50_levels = pd.DataFrame([
        {"波段階段與大盤指標錨點": "🔥 階段1：高檔收割點 (大盤布林上軌)", "加權指數點位": "50305.9 點", "0050正2 目標價": "42.19 元", "操作指令": "賣出第 1 張機動正2", "增股效益試算": "回收約 42,021 元現金"},
        {"波段階段與大盤指標錨點": "📈 階段2：多頭回測承接 (大盤20MA月線)", "加權指數點位": "48121.2 點", "0050正2 目標價": "38.74 元", "操作指令": "多頭回測有守買回", "增股效益試算": "若高檔賣1張於此買回 ➔ 變 1,084 股 (+84股)"},
        {"波段階段與大盤指標錨點": "🛡️ 階段3：波段黃金買回點 (大盤60MA季線)", "加權指數點位": "46055.8 點", "0050正2 目標價": "35.49 元", "操作指令": "季線有守且 KD 金叉買回", "增股效益試算": "若高檔賣1張於此買回 ➔ 變 1,184 股 (+184股)"}
    ])
    st.dataframe(df_t50_levels, use_container_width=True)

with tab2:
    st.markdown("### 🔥 【表一：階段 3(3) 旱地拔蔥 ＆ 七大實戰名將點火榜】")
    df_launch = pd.DataFrame([
        {"實戰優先分": 100, "診斷": "🌸 瑞昱/矽創型：均線糾結後帶量紅棒", "代碼": "2379", "股名": "瑞昱", "現價": 778.0, "族群": "IC設計與IP", "今日%": 2.64},
        {"實戰優先分": 96, "診斷": "💎 智原型：綠柱收斂帶量上攻碰近高", "代碼": "3035", "股名": "智原", "現價": 211.0, "族群": "IC設計與IP", "今日%": 9.9}
    ])
    st.dataframe(df_launch, use_container_width=True)

with tab3:
    st.markdown("### 🏛️ 長期投資波段區 (週月MACD雙多)")
    df_long = pd.DataFrame([
        {"波段總分": 100, "長性格局定性": "✅ 雙守有基＋週月MACD多頭", "代碼": "3264", "股名": "欣銓", "現價": 258.5, "族群": "晶圓與封測", "400張大戶%": "65.5%"}
    ])
    st.dataframe(df_long, use_container_width=True)
