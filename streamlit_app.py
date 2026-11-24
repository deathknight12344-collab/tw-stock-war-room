import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import datetime, calendar, pytz, requests, urllib3, io, os, json, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="台股每日戰情室 v62.0", page_icon="🚨", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
.war-hero-box { background: linear-gradient(135deg, #0f2027 0%, #203a43 50%, #2c5364 100%); color: #fff; padding: 20px 24px; border-radius: 14px; border-left: 8px solid #00e676; margin-bottom: 16px; box-shadow: 0 6px 18px rgba(0,0,0,0.25); }
.war-hero-box-danger { background: linear-gradient(135deg, #2b0f14 0%, #451219 50%, #5c1620 100%); color: #fff; padding: 20px 24px; border-radius: 14px; border-left: 8px solid #ff4d4f; margin-bottom: 16px; box-shadow: 0 6px 20px rgba(255,77,79,0.28); }
.war-hero-box-warn { background: linear-gradient(135deg, #2b1d1d 0%, #3d251e 50%, #4a2c2a 100%); color: #fff; padding: 20px 24px; border-radius: 14px; border-left: 8px solid #fbbf24; margin-bottom: 16px; box-shadow: 0 6px 18px rgba(0,0,0,0.25); }
.event-banner-red { background: linear-gradient(90deg, #7f1d1d 0%, #991b1b 100%); color: #fff; padding: 11px 18px; border-radius: 10px; border: 2px solid #f87171; margin-bottom: 12px; font-size: 15px; line-height: 1.6; }
.event-banner-yellow { background: linear-gradient(90deg, #422006 0%, #713f12 100%); color: #fef08a; padding: 11px 18px; border-radius: 10px; border: 2px solid #facc15; margin-bottom: 12px; font-size: 15px; line-height: 1.6; }
.event-banner-blue { background: linear-gradient(90deg, #0f172a 0%, #1e293b 100%); color: #e2e8f0; padding: 11px 18px; border-radius: 10px; border: 1px solid #38bdf8; margin-bottom: 12px; font-size: 15px; line-height: 1.6; }
.radar-grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(440px, 1fr)); gap: 14px; margin: 12px 0 16px 0; }
.radar-panel { background: rgba(15, 23, 42, 0.85); border-radius: 12px; padding: 15px 18px; font-size: 14.8px; line-height: 1.7; border: 1px solid rgba(255,255,255,0.18); }
.war-score-title { font-size: 24px; font-weight: 900; color: #ffd54f; margin-bottom: 8px; line-height: 1.4; }
.war-dim-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(430px, 1fr)); gap: 12px; margin: 12px 0 16px 0; }
.war-dim-item { background: rgba(255,255,255,0.08); padding: 13px 16px; border-radius: 10px; font-size: 14.8px; line-height: 1.7; }
.war-dim-concl { font-size: 16px; font-weight: 900; color: #ffd54f; margin-bottom: 6px; border-bottom: 1px solid rgba(255,255,255,0.15); padding-bottom: 5px; display: block; }
.tab-guide-box { background: #161b22; color: #e5e7eb; border-left: 5px solid #38bdf8; padding: 12px 16px; border-radius: 8px; margin-bottom: 14px; font-size: 14.5px; line-height: 1.7; }
.swap-card { background: #0f172a; color: #f8fafc; border-radius: 12px; padding: 16px 20px; border: 2px solid #38bdf8; margin-bottom: 14px; line-height: 1.72; }
.short-ledger-ok { background: #0d231d; color: #e2e8f0; border-radius: 12px; padding: 15px 19px; border: 2px solid #00e676; margin-bottom: 14px; line-height: 1.7; }
.short-ledger-stop { background: #2d0f14; color: #fff; border-radius: 12px; padding: 15px 19px; border: 2px solid #ff4d4f; margin-bottom: 14px; line-height: 1.7; }
</style>
""", unsafe_allow_html=True)

def get_tw_now():
    return datetime.datetime.now(pytz.timezone("Asia/Taipei"))

def get_target_revenue_ym_list():
    now = get_tw_now()
    last_m_dt = now.replace(day=1) - datetime.timedelta(days=1)
    prev2_m_dt = last_m_dt.replace(day=1) - datetime.timedelta(days=1)
    ry1, m1 = last_m_dt.year - 1911, last_m_dt.month
    ry2, m2 = prev2_m_dt.year - 1911, prev2_m_dt.month
    return [(ry1, m1, f"{ry1}{m1:02d}"), (ry2, m2, f"{ry2}{m2:02d}")]

def get_volume_multiplier():
    now = get_tw_now()
    if now.weekday() >= 5: return 1.0, "休市/週末 (完整收盤量)"
    t_val = now.hour * 60 + now.minute
    if t_val < 540: return 1.0, "開盤前 (前一交易日完整量)"
    if t_val >= 810: return 1.0, "已收盤 (當日完整收盤量)"
    elapsed = max(1, t_val - 540)
    mult = min(4.2, (270.0 / elapsed) * 0.32) if elapsed <= 15 else (min(3.8, (270.0 / elapsed) * 0.48) if elapsed <= 45 else ((270.0 / elapsed) * 0.65 if elapsed <= 90 else ((270.0 / elapsed) * 0.82 if elapsed <= 180 else 270.0 / elapsed)))
    mc = round(max(1.0, min(mult, 4.5)), 2)
    return mc, f"盤中即時推估 (U型倍率 {mc:.2f}x)"

def is_zombie_stock_tiered(code: str, curr_p: float, est_vol: float, ma5_vol: float = 0.0) -> bool:
    if curr_p is None or pd.isna(curr_p) or curr_p <= 0: return True
    eff_vol = max(float(est_vol or 0), float(ma5_vol or 0) * 0.85)
    turnover_yi = (eff_vol * float(curr_p)) / 100000.0
    if curr_p < 150.0: return eff_vol < 1000.0
    elif curr_p < 500.0: return (eff_vol < 500.0) and (turnover_yi < 1.5)
    else: return (eff_vol < 200.0) and (turnover_yi < 2.0)

def style_tw_stock_df(df):
    if df.empty: return df
    def color_tw_val(val):
        try:
            s = str(val).replace("%", "").replace(",", "").replace("+", "").replace("元", "").replace("倍", "").replace("張", "").split(" ")[0].strip()
            if s in ["", "-", "--", "待公告", "未揭露", "虧損/無PE", "N/A"]: return ""
            v = float(s)
            return "color: #ff3b30; font-weight: bold;" if v > 0 else ("color: #00b853; font-weight: bold;" if v < 0 else "")
        except Exception: return ""
    target_cols = [c for c in ["今日%", "3日%", "外資買賣(張)", "投信買賣(張)", "單月營收YoY", "累計年營收YoY", "RS20D%"] if c in df.columns]
    fmt_dict = {k: "{:.2f}" for k in ["現價", "💎EPS合理切入價", "🎯EPS目標價", "預估EPS(元)", "昨日量比", "5MA量比", "天花板(反壓)", "月線承接", "鋼鐵防線", "極限停損"] if k in df.columns}
    for k_int in ["外資買賣(張)", "投信買賣(張)", "預估量(張)", "成交量(張)"]:
        if k_int in df.columns: fmt_dict[k_int] = "{:,.0f}" if "量" in k_int else "{:+,d}"
    if "本益比(PE)" in df.columns: fmt_dict["本益比(PE)"] = "{:.1f}"
    if "今日%" in df.columns: fmt_dict["今日%"] = "{:+.2f}%"
    if "3日%" in df.columns: fmt_dict["3日%"] = "{:+.2f}%"
    if "日J值" in df.columns: fmt_dict["日J值"] = "{:.1f}"
    if "上影線%" in df.columns: fmt_dict["上影線%"] = "{:.1f}%"
    try: return df.style.map(color_tw_val, subset=target_cols).format(fmt_dict, na_rep="-")
    except Exception: return df

# ==========================================
# 模組二 (v62.0 去重修正版)：雙軌自動滾動事件引擎（按 (年, 月, 類別) 精準合併，絕不重複卡片）
# ==========================================
CUSTOM_EVENTS_FILE = "custom_risk_events.json"

OFFICIAL_ANCHOR_EVENTS = [
    {"date": "2026-11-23", "cat": "POLICY", "overrides": ["POLICY"], "short": "限空令3.0上路", "title": "金管會「限空令 3.0」正式上路（全市場禁平盤下放空/融券150%/禁發認售）", "level": "HIGH",
     "bull": "空頭回補與國安基金護盤共振，守住月20MA展開跌深反彈",
     "bear": "國際賣壓過重、量縮盤跌，10日內未連3日站10MA定性為政策逃命波",
     "action": "紅燈減碼避開 09:00-09:10 低點，改 09:15 後或反彈遇壓執行；嚴禁追買"},
    {"date": "2026-11-25", "cat": "PCE", "overrides": ["PCE"], "short": "美10月PCE/GDP", "title": "美國 10 月 PCE 物價指數 ＆ Q3 GDP 修正值（感恩節提前公布）", "level": "HIGH",
     "bull": "核心PCE低於預期 ➔ 美債10Y續跌破4.8%，科技股估值壓力緩解",
     "bear": "通膨黏性回升 ➔ 強化 12 月鷹派升息預期，美債重返 5% 引發二次測底",
     "action": "事件前 3 天內反彈遇壓減碼弱勢股、嚴禁開槓桿"},
    {"date": "2026-11-30", "cat": "MSCI", "overrides": ["MSCI"], "short": "MSCI 11月生效", "title": "MSCI 11 月半年度調整收盤生效（台股新興市場權重升至 29.01%）", "level": "MED",
     "bull": "外資被動買盤湧入新增主流股(南電/台燿/CPO/封測設備)，尾盤爆量拉抬",
     "bear": "剔除股(中租/元太/嘉澤/萬海)尾盤 13:25-13:30 遭大單甩尾砍殺",
     "action": "避開 13:25 追高新增股；留意景碩(3189)處置延後風險"},
    {"date": "2026-12-01", "cat": "NVDA", "overrides": ["NVDA"], "short": "老黃國會聽證", "title": "黃仁勳與輝達法務長赴美國參眾兩院聽證會（12/01~12/03，12/3公開作證）", "level": "HIGH",
     "bull": "釐清對中出口與會計疑慮、未有進一步刑事起訴 ➔ AI 供應鏈情緒修復",
     "bear": "國會祭出更嚴厲調查或出口禁令升級 ➔ 輝達與舊穀倉 ODM 再遭殺估值",
     "action": "嚴守水庫二傳家寶不動，短線資金聚焦無墊料之新穀倉1與00985B防彈"},
    {"date": "2026-12-08", "cat": "FOMC", "overrides": ["FOMC"], "short": "12月FOMC決議", "title": "聯準會 FOMC 利率決議與點陣圖（12/08~12/09）", "level": "HIGH",
     "bull": "升息幅度符合/低於預期且暗示接近終點 ➔ 利空出盡，台股 W 底右腳確立",
     "bear": "點陣圖上調明年終點利率或超鷹派談話 ➔ 美股破底拖累台股測月36MA",
     "action": "關鍵轉折點！靜待落底雷達達 4/5 共振再解鎖分 3 批存股龍頭"},
    {"date": "2026-12-10", "cat": "CPI", "overrides": ["CPI", "TSMC"], "short": "台積營收+美CPI", "title": "台積電 11 月營收 ＋ 美國 11 月 CPI ＋ 商務部長盧特尼克參院作證", "level": "HIGH",
     "bull": "台積電營收續創高且 CPI 降溫 ➔ 基本面拆彈，外資現貨回補啟動",
     "bear": "CPI 爆表或商務部加碼晶片管制 ➔ 半導體雙重承壓",
     "action": "確認 AI 訂單真實續航力，剔除純題材假突破股"},
    {"date": "2026-12-15", "cat": "IPO", "overrides": ["IPO"], "short": "ANT鎖定解禁", "title": "Anthropic (ANT) 首波早期鎖定期解禁（3.03 億股）", "level": "HIGH",
     "bull": "大股東惜售或承接買盤強勁 ➔ 美股流動性抽血結束",
     "bear": "早期投資人獲利了結倒貨 ➔ 拖累美股科技股與 AI 概念股同步回測",
     "action": "觀察是否砸出 12 月中 W 底右腳黃金買點"},
    {"date": "2026-12-21", "cat": "BIS", "overrides": ["BIS"], "short": "ANT納那指+BIS", "title": "ANT 快速納入那斯達克 100 生效 ＆ 美 BIS 晶片新規舊約運送截止日", "level": "HIGH",
     "bull": "被動買盤順利消化解禁籌碼，半導體拉貨回歸常態",
     "bear": "重演 SpaceX 納入日倒貨潮，加上搶運潮退潮引發 1 月營收真空擔憂",
     "action": "提防高位階 AI 股趁納入日利多出盡"},
    {"date": "2026-12-23", "cat": "PCE", "overrides": ["PCE"], "short": "美11月PCE", "title": "美國 11 月 PCE 物價指數（耶誕節提前公布）", "level": "MED",
     "bull": "通膨續降溫助推年底作帳行情", "bear": "通膨反彈壓抑年底封關買盤", "action": "留意美債10Y與外資休假量縮效應"},
    {"date": "2027-01-19", "cat": "BIS", "overrides": ["BIS"], "short": "BIS評論截止", "title": "美國 BIS 晶片新規公眾評論截止日", "level": "HIGH",
     "bull": "美企遊說成功放寬細則 ➔ 先進封測與 CSP 自研晶片迎估值修復",
     "bear": "新規嚴格定案 ➔ 亞洲供應鏈重新定價", "action": "2027 Q1 出口規範定案關鍵時點"},
    {"date": "2027-01-26", "cat": "FOMC", "overrides": ["FOMC"], "short": "27/01 FOMC", "title": "2027年1月 聯準會 FOMC 利率決議 (1/26-1/27)", "level": "HIGH",
     "bull": "通膨受控釋出暫停升息訊號 ➔ 債市與成長股迎元月行情", "bear": "維持鷹派緊縮立場 ➔ 壓抑 Q1 科技股本益比", "action": "依模組0燈號調整水位"},
    {"date": "2027-02-09", "cat": "MSCI", "overrides": ["MSCI"], "short": "MSCI 2月公告", "title": "MSCI 2027年2月季度調整名單公告（3/1 生效）", "level": "MED",
     "bull": "提前卡位新納入主流族群與指標龍頭", "bear": "剔除股面臨外資被動調降壓力", "action": "於 App Tab 4 貼入最新名單更新觀察池"},
    {"date": "2027-03-16", "cat": "FOMC", "overrides": ["FOMC"], "short": "27/03 FOMC", "title": "2027年3月 聯準會 FOMC 利率決議與點陣圖 (3/16-3/17)", "level": "HIGH",
     "bull": "點陣圖下修利率路徑 ➔ 啟動波段主升段", "bear": "利率維持高檔更久 ➔ 高估值股面臨修正", "action": "事件前 3 天降槓桿防劇烈甩尾"},
    {"date": "2027-04-27", "cat": "FOMC", "overrides": ["FOMC"], "short": "27/04 FOMC", "title": "2027年4月 聯準會 FOMC 利率決議 (4/27-4/28)", "level": "HIGH",
     "bull": "政策轉溫和支撐報稅季行情", "bear": "疊加報稅季賣壓放大波動", "action": "檢視第一季財報防墊料與三率體質"},
    {"date": "2027-05-10", "cat": "MSCI", "overrides": ["MSCI"], "short": "MSCI 5月公告", "title": "MSCI 2027年5月半年度調整名單公告（5/28 生效）", "level": "MED",
     "bull": "半年度大換血帶來新資金主流", "bear": "生效尾盤劇烈震盪", "action": "更新 App MSCI 動態池並提防尾盤爆量"},
    {"date": "2027-06-08", "cat": "FOMC", "overrides": ["FOMC"], "short": "27/06 FOMC", "title": "2027年6月 聯準會 FOMC 利率決議與點陣圖 (6/8-6/9)", "level": "HIGH",
     "bull": "進入降息討論窗口 ➔ 債券與權值股雙多", "bear": "通膨反覆推遲寬鬆", "action": "檢視水庫三 00985B 債息與資本利得"},
    {"date": "2027-07-27", "cat": "FOMC", "overrides": ["FOMC"], "short": "27/07 FOMC", "title": "2027年7月 聯準會 FOMC 利率決議 (7/27-7/28)", "level": "HIGH",
     "bull": "資金面寬鬆搭配除權息旺季", "bear": "高檔除息貼息壓力", "action": "多頭綠燈期將股息投入衛星股賺短價差"},
    {"date": "2027-08-12", "cat": "MSCI", "overrides": ["MSCI"], "short": "MSCI 8月公告", "title": "MSCI 2027年8月季度調整名單公告（9/1 生效）", "level": "MED",
     "bull": "外資針對下半年成長股重新配置", "bear": "弱勢權值股遭調降權重", "action": "同步更新 App MSCI 觀察池"},
    {"date": "2027-09-14", "cat": "FOMC", "overrides": ["FOMC"], "short": "27/09 FOMC", "title": "2027年9月 聯準會 FOMC 利率決議與點陣圖 (9/14-9/15)", "level": "HIGH",
     "bull": "確立利率下行循環", "bear": "經濟放緩疑慮引發避險", "action": "檢查美債殖利率與 HYG 信用利差"},
    {"date": "2027-10-26", "cat": "FOMC", "overrides": ["FOMC"], "short": "27/10 FOMC", "title": "2027年10月 聯準會 FOMC 利率決議 (10/26-10/27)", "level": "HIGH",
     "bull": "Q4 旺季作帳啟動", "bear": "年底外資結帳賣壓", "action": "核對第四季旺季營收與庫存"},
    {"date": "2027-11-11", "cat": "MSCI", "overrides": ["MSCI"], "short": "MSCI 11月公告", "title": "MSCI 2027年11月半年度調整名單公告（12/1 生效）", "level": "MED",
     "bull": "外資布局 2028 新主流賽道", "bear": "剔除股尾盤承壓", "action": "更新 App MSCI 動態池"},
    {"date": "2027-11-16", "cat": "FOMC", "overrides": ["FOMC"], "short": "27/11 FOMC", "title": "2027年11月 聯準會 FOMC 利率決議 (11/16-11/17)", "level": "HIGH",
     "bull": "政策明朗利於年底行情", "bear": "意外鷹派壓抑風險資產", "action": "嚴守模組 0 雙雷達燈號"}
]

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime.date:
    cal = calendar.monthcalendar(year, month)
    days = [wk[weekday] for wk in cal if wk[weekday] != 0]
    return datetime.date(year, month, days[min(n - 1, len(days) - 1)])

def _last_weekday(year: int, month: int, weekday: int) -> datetime.date:
    cal = calendar.monthcalendar(year, month)
    days = [wk[weekday] for wk in cal if wk[weekday] != 0]
    return datetime.date(year, month, days[-1])

def _shift_workday(d: datetime.date) -> datetime.date:
    if d.weekday() == 5: return d - datetime.timedelta(days=1)
    if d.weekday() == 6: return d + datetime.timedelta(days=1)
    return d

def get_dynamic_rolling_events(horizon_days: int = 90):
    """以 (年, 月, 類別) 精準去重：若官宣庫已有該月同類事件(如感恩節提前 PCE、12/10 CPI+台積營收)，演算法自動跳過不再重複產生"""
    today_d = get_tw_now().date()
    end_d = today_d + datetime.timedelta(days=horizon_days)
    covered_ym_cats = set()
    all_events = []

    # 1. 先載入官方全年預告與特定催化劑，登記已覆蓋之 (year, month, cat)
    for ev in OFFICIAL_ANCHOR_EVENTS:
        ev_d = datetime.datetime.strptime(ev["date"], "%Y-%m-%d").date()
        for ov_cat in ev.get("overrides", [ev["cat"]]):
            covered_ym_cats.add((ev_d.year, ev_d.month, ov_cat))
        all_events.append(dict(ev))

    # 2. 載入指揮官自訂事件
    if os.path.exists(CUSTOM_EVENTS_FILE):
        try:
            with open(CUSTOM_EVENTS_FILE, "r", encoding="utf-8") as f:
                for ce in json.load(f):
                    all_events.append({
                        "date": ce["date"], "cat": "CUSTOM", "short": ce.get("short", "自訂事件"),
                        "title": ce["title"], "level": ce.get("level", "HIGH"),
                        "bull": ce.get("bull", "利多實現帶動反彈"), "bear": ce.get("bear", "不如預期引發回檔"),
                        "action": ce.get("action", "嚴控倉位")
                    })
        except Exception:
            pass

    # 3. 演算法自動滾動補齊未來 4 個月尚未被官宣覆蓋之例行總經／結算事件
    cur_y, cur_m = today_d.year, today_d.month
    for m_off in range(0, 4):
        yy = cur_y + (cur_m - 1 + m_off) // 12
        mm = (cur_m - 1 + m_off) % 12 + 1
        prev_m_lbl = f"{12 if mm == 1 else mm - 1}月"

        auto_specs = [
            (_nth_weekday(yy, mm, 4, 1), "NFP", f"美{prev_m_lbl}非農", f"美國 {prev_m_lbl} 非農就業報告 (NFP)", "HIGH",
             "就業溫和降溫(金髮女孩) ➔ 減輕升息壓力，利多科技與美債", "就業過熱或急凍 ➔ 雙向引發高波動", "數據公布前不留高槓桿部位"),
            (_shift_workday(datetime.date(yy, mm, 10)), "TSMC", f"台積{prev_m_lbl}營收", f"台積電(2330) {prev_m_lbl} 營收公布（全台月營收截止日）", "MED",
             "營收優於預期且年增續強 ➔ 驗證 AI 實單需求，提振新穀倉1信心", "月減幅大於季節性預期 ➔ 引發半導體庫存調整擔憂", "搭配 Tab 6 月營收快篩檢視破繭股"),
            (_nth_weekday(yy, mm, 2, 2), "CPI", f"美{prev_m_lbl}CPI", f"美國 {prev_m_lbl} CPI 消費者物價指數", "HIGH",
             "核心CPI低於預期 ➔ 殖利率回落、美元轉弱，助外資回補台股", "核心CPI高於預期 ➔ 殺估值壓力重燃，科技股首當其衝", "事件前 3 天嚴禁追高，優先配置防彈衣與 00985B"),
            (_nth_weekday(yy, mm, 2, 3), "SETTLE", f"{mm}月期權結算", f"台指期與選擇權 {mm} 月合約結算日", "MED",
             "外資空單獲利了結順勢回補轉倉 ➔ 結算後盤勢止跌反彈", "外資壓低結算並將空單轉倉至下月 ➔ 13:00-13:30 劇烈下殺", "緊盯 13:00 尾盤震盪與外資新月淨空單"),
            (_last_weekday(yy, mm, 4), "PCE", f"美{prev_m_lbl}PCE", f"美國 {prev_m_lbl} PCE 個人消費支出物價指數", "HIGH",
             "Fed 最重視之通膨指標降溫 ➔ 強化鴿派預期，股債齊揚", "核心PCE反彈 ➔ 壓抑降息空間，美債殖利率上衝", "留意殖利率是否突破 5.0% 觸發利差提款警戒")
        ]
        if mm in [3, 6, 9, 12]:
            auto_specs.append((_nth_weekday(yy, mm, 4, 3), "QW", f"{mm}月美股四巫日", f"美股 {mm} 月四巫日 (期權季度結算)", "MED",
                               "選擇權賣權結算消散，流動性釋放助美股反彈", "CTA 與 Gamma 擠壓放大盤中單日劇烈振幅", "美股結算夜前不輕易滿倉追價"))

        for d_obj, cat_k, s_lbl, ttl, lvl, b_txt, r_txt, act_txt in auto_specs:
            if (yy, mm, cat_k) not in covered_ym_cats:
                all_events.append({"date": d_obj.strftime("%Y-%m-%d"), "cat": cat_k, "short": s_lbl, "title": ttl, "level": lvl, "bull": b_txt, "bear": r_txt, "action": act_txt})

    rows, upcoming_rows = [], []
    for item in all_events:
        try: ev_d = datetime.datetime.strptime(item["date"], "%Y-%m-%d").date()
        except Exception: continue
        delta_days = (ev_d - today_d).days
        if delta_days < -2 or ev_d > end_d: continue
        status = "✅ 已度過" if delta_days < 0 else ("🚨 今日登場！" if delta_days == 0 else (f"🔴 倒數 {delta_days} 天 (高波動警戒)" if delta_days <= 3 else (f"🟡 倒數 {delta_days} 天" if delta_days <= 7 else f"📅 尚餘 {delta_days} 天")))
        row_obj = {
            "日期": item["date"], "倒數狀態": status, "重大風險事件": item["title"],
            "🟢 優於預期／鴿派劇本": item["bull"], "🔴 差於預期／鷹派劇本": item["bear"],
            "🛡️ 實戰因應": item["action"],
            "_short": item["short"], "_days": delta_days, "_level": item["level"]
        }
        rows.append(row_obj)
        if delta_days >= 0: upcoming_rows.append(row_obj)

    rows.sort(key=lambda x: (x["_days"], 0 if x["_level"] == "HIGH" else 1))
    upcoming_rows.sort(key=lambda x: (x["_days"], 0 if x["_level"] == "HIGH" else 1))
    return rows, upcoming_rows, (upcoming_rows[0] if upcoming_rows else None)

# ==========================================
# 模組四：MSCI 每季動態池 ＋ 宏爺穀倉定義
# ==========================================
MSCI_CACHE_FILE = "msci_dynamic_cache.json"
TDCC_PREV_CACHE_FILE = "tdcc_prev_snapshot.json"

MSCI_DEFAULT_2026_11 = {
    "quarter": "2026/11 (半年度調整・11/30 收盤生效)",
    "source": "MSCI 官方公告核實 (2026/11/12 公布)",
    "std_add": [("3189", "景碩", "ABF載板與高頻CCL", "TW"), ("8046", "南電", "ABF載板與高頻CCL", "TW"), ("6274", "台燿", "ABF載板與高頻CCL", "TWO")],
    "std_del": [("5871", "中租-KY", "金融租賃與傳產", "TW"), ("8069", "元太", "光電與電子紙", "TWO"), ("3533", "嘉澤", "高速連接器", "TW"), ("2615", "萬海", "貨櫃航運", "TW")],
    "small_add": [
        ("6683", "雍智科技", "先進封測介面設備", "TWO"), ("7822", "倍利科", "先進封測介面設備", "TW"), ("8021", "尖點", "先進封測介面設備", "TW"),
        ("3163", "波若威", "CPO矽光子與光通訊", "TWO"), ("4991", "環宇-KY", "CPO矽光子與光通訊", "TWO"), ("3016", "嘉晶", "CPO矽光子與化合物半導體", "TW"),
        ("3693", "營邦", "ASIC準系統與高壓被動", "TWO"), ("7711", "永擎", "ASIC準系統與高壓被動", "TW"), ("3026", "禾伸堂", "ASIC準系統與高壓被動", "TW"),
        ("6173", "信昌電", "ASIC準系統與高壓被動", "TW"), ("5351", "鈺創", "記憶體與邊緣AI", "TWO"), ("6672", "騰輝電子-KY", "ABF載板與高頻CCL", "TW"), ("6182", "合晶", "化合物與矽晶圓", "TWO")
    ],
    "small_del": [
        ("1723", "中碳", "傳產材料與內需", "TW"), ("6803", "崑鼎", "傳產環保與內需", "TWO"), ("4506", "崇友", "傳產機電與內需", "TW"),
        ("2543", "皇昌", "營造與內需", "TW"), ("2439", "美律", "消費電子與聲學", "TW"), ("6188", "廣明", "電腦週邊", "TWO"), ("9911", "台灣櫻花", "傳產內需消費", "TW"), ("6719", "力智", "類比IC", "TW")
    ]
}

MSCI_THEME_BELLWETHERS = {
    "ABF載板與高頻CCL": [("3037", "欣興"), ("2383", "台光電"), ("2368", "金像電")],
    "先進封測介面設備": [("3711", "日月光投控"), ("6515", "穎崴"), ("6223", "旺矽"), ("6187", "萬潤")],
    "CPO矽光子與光通訊": [("3363", "上詮"), ("3450", "聯鈞"), ("6442", "光聖"), ("2345", "智邦")],
    "ASIC準系統與高壓被動": [("3661", "世芯-KY"), ("3443", "創意"), ("2327", "國巨"), ("2308", "台達電")],
    "貨櫃航運": [("2603", "長榮"), ("2609", "陽明")],
    "金融租賃與傳產": [("2886", "兆豐金"), ("2891", "中信金")]
}

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_twse_disposition_codes():
    disp_map = {"3189": "⚠️ MSCI 觀察期(11/10~11/16)曾列處置股，本輪恐延後轉入標準指數"}
    try:
        r = requests.get("https://www.twse.com.tw/rwd/zh/announcement/punish?response=json", headers={"User-Agent": "Mozilla/5.0"}, timeout=5, verify=False)
        if r.status_code == 200:
            for row in r.json().get("data", []):
                if len(row) >= 7 and len(str(row[2]).strip()) == 4 and str(row[2]).strip().isdigit():
                    disp_map[str(row[2]).strip()] = f"🚨 現行處置股 ({str(row[6]).strip()})"
    except Exception: pass
    return disp_map

def infer_msci_theme(code: str, name: str, sector_str: str = "") -> str:
    s = f"{name} {sector_str}"
    if code in ["3189", "8046", "6274", "6672", "3037", "2383", "2368"] or any(k in s for k in ["載板", "CCL", "銅箔", "PCB"]): return "ABF載板與高頻CCL"
    if code in ["6683", "7822", "8021", "6515", "6223", "3711", "6239", "3264", "6187", "3131", "3583", "2449"] or (("電腦及週邊" not in s) and any(k in s for k in ["封測", "探針", "檢測設備", "半導體設備", "CoWoS", "先進封裝"])): return "先進封測介面設備"
    if code in ["3163", "4991", "3016", "3363", "3450", "6442"] or any(k in s for k in ["光通訊", "矽光子", "磊晶"]): return "CPO矽光子與光通訊"
    if code in ["3693", "7711", "3026", "6173", "2327", "3661", "3443"] or any(k in s for k in ["被動元件", "準系統", "ASIC"]): return "ASIC準系統與高壓被動"
    if code in ["2615", "2603", "2609"] or "航運" in s: return "貨櫃航運"
    return sector_str or "其他異動族群"

def parse_msci_text_override(raw_text: str, price_dict: dict, quarter_label: str = "使用者自訂最新季度") -> dict:
    res = {"quarter": quarter_label, "source": "指揮官手動覆寫解析", "std_add": [], "std_del": [], "small_add": [], "small_del": []}
    sections = {"std_add": "", "std_del": "", "small_add": "", "small_del": ""}
    cur_sec = "std_add"
    for line in raw_text.splitlines():
        ln = line.strip()
        if not ln: continue
        if ("標準" in ln or "台灣指數" in ln) and ("新增" in ln or "納入" in ln): cur_sec = "std_add"
        elif ("標準" in ln or "台灣指數" in ln) and ("刪除" in ln or "剔除" in ln): cur_sec = "std_del"
        elif "小型" in ln and ("新增" in ln or "納入" in ln): cur_sec = "small_add"
        elif "小型" in ln and ("刪除" in ln or "剔除" in ln): cur_sec = "small_del"
        sections[cur_sec] += " " + ln
    for sec_k, txt in sections.items():
        for c in list(dict.fromkeys(re.findall(r"\b(\d{4})\b", txt))):
            p_inf = price_dict.get(c, {})
            nm, mkt = p_inf.get("name", c), p_inf.get("market", "TW")
            res[sec_k].append((c, nm, infer_msci_theme(c, nm, p_inf.get("sector", "")), mkt))
    return res

@st.cache_data(ttl=21600, show_spinner=False)
def fetch_dynamic_msci_data(sig: int = 1) -> dict:
    data_out = dict(MSCI_DEFAULT_2026_11)
    if os.path.exists(MSCI_CACHE_FILE):
        try:
            with open(MSCI_CACHE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if saved.get("std_add") or saved.get("small_add"): data_out = saved
        except Exception: pass
    data_out["disposition_map"] = fetch_twse_disposition_codes()
    return data_out

GRANARY_1_CSP_COMMON = {"2330", "3711", "3661", "3443", "2454", "6515", "6223", "2383", "2368"}
GRANARY_2_POWER_THERMAL = {"2308", "2301", "3017", "3324", "3653", "3211", "1519", "1503"}
GRANARY_3_EDGE_MEMORY = {"3008", "3406", "2317", "6239", "3034"}
POLICY_DEFENSE_ARMOR = {"1513", "1514", "8936", "9933", "6139", "2886", "2891", "2892", "5880", "2801", "2834"}
OLD_GRANARY_ODM_WATCH = {"2382", "6669", "3231", "2356", "3706"}

ODM_Q3_OFFICIAL_BASE = {
    "3231": {"inv_qoq": 20.4, "ar_qoq": 14.5, "ocf_positive": False, "note": "Q3存貨6,418億(季增+20.4%)且營業現金流為負"},
    "2382": {"inv_qoq": 8.3, "ar_qoq": 21.7, "ocf_positive": False, "note": "Q3存貨8,561億、應收帳款9,318億(季增+21.7%)代客墊料高"},
    "2356": {"inv_qoq": 21.2, "ar_qoq": 16.0, "ocf_positive": False, "note": "伺服器備料存貨季增+21.2%且營業現金流淨流出"},
    "3706": {"inv_qoq": 19.8, "ar_qoq": 15.2, "ocf_positive": False, "note": "機櫃備料存貨季增+19.8%且營業現金流為負"},
    "6669": {"inv_qoq": 11.5, "ar_qoq": 9.8, "ocf_positive": True, "note": "ASIC自研伺服器佔比高、客供料模式現金流穩健"}
}

ELECTRONICS_BLUE_CHIPS = {
    "2330": ("台積電", "🌟新穀倉1・晶圓龍頭", "TW"), "3711": ("日月光投控", "🌟新穀倉1・先進封測", "TW"),
    "3661": ("世芯-KY", "🌟新穀倉1・ASIC晶片", "TW"), "3443": ("創意", "🌟新穀倉1・ASIC矽智財", "TW"),
    "2454": ("聯發科", "🌟新穀倉1・IC設計龍頭", "TW"), "6515": ("穎崴", "🌟新穀倉1・AI測試座龍頭", "TW"),
    "6223": ("旺矽", "🌟新穀倉1・CPC探針卡龍頭", "TWO"), "2383": ("台光電", "🌟新穀倉1・高頻CCL龍頭", "TW"),
    "2368": ("金像電", "🌟新穀倉1・伺服器PCB", "TW"), "2308": ("台達電", "⚡新穀倉2・電源與液冷基建", "TW"),
    "2301": ("光寶科", "⚡新穀倉2・雲端電源與BBU", "TW"), "3017": ("奇鋐", "⚡新穀倉2・AI水冷散熱", "TW"),
    "3324": ("雙鴻", "⚡新穀倉2・水冷散熱", "TWO"), "3653": ("健策", "⚡新穀倉2・均熱片龍頭", "TW"),
    "3211": ("順達", "⚡新穀倉2・BBU電池模組", "TWO"), "1519": ("華城", "⚡新穀倉2・AI變壓器重電", "TW"),
    "1503": ("士電", "⚡新穀倉2・重電與變壓器", "TW"), "3008": ("大立光", "📱新穀倉3・光學鏡頭龍頭", "TW"),
    "3406": ("玉晶光", "📱新穀倉3・邊緣AI光學", "TW"), "2317": ("鴻海", "📱新穀倉3・AI機櫃與iPhone", "TW"),
    "6239": ("力成", "📱新穀倉3・FOPLP與記憶體封測", "TW"), "3034": ("聯詠", "📱新穀倉3・邊緣AI驅動IC", "TW"),
    "2382": ("廣達", "⚠️舊穀倉觀察・AI伺服器ODM", "TW"), "6669": ("緯穎", "🔄雙棲觀察・ASIC與通用伺服器", "TW"),
    "3231": ("緯創", "⚠️舊穀倉觀察・AI伺服器ODM", "TW"), "2356": ("英業達", "⚠️舊穀倉觀察・伺服器ODM", "TW"),
    "3706": ("神達", "⚠️舊穀倉觀察・伺服器機櫃", "TW"), "2379": ("瑞昱", "👑電子權值・網通IC", "TW"),
    "2345": ("智邦", "👑電子權值・高階網通", "TW"), "3533": ("嘉澤", "👑電子權值・高速連接器", "TW"),
    "2059": ("川湖", "👑電子權值・伺服器導軌", "TW"), "2357": ("華碩", "👑電子權值・AI PC龍頭", "TW"),
    "4938": ("和碩", "👑電子權值・通訊與車用", "TW"), "2327": ("國巨", "👑電子權值・被動元件龍頭", "TW"),
    "2395": ("研華", "👑電子權值・工業電腦龍頭", "TW"), "5274": ("信驊", "👑電子權值・BMC晶片王", "TWO"),
    "3529": ("力旺", "👑電子權值・矽智財IP", "TWO"), "2449": ("京元電子", "👑電子權值・AI晶片測試", "TW"),
    "3264": ("欣銓", "👑電子權值・車用與AI測試", "TWO"), "6274": ("台燿", "👑電子權值・高階CCL", "TWO"),
    "8016": ("矽創", "👑電子權值・驅動與感測IC", "TW"), "5269": ("祥碩", "👑電子權值・高速傳輸IC", "TW"),
    "6187": ("萬潤", "👑電子權值・CoWoS設備", "TWO"), "3131": ("弘塑", "👑電子權值・CoWoS設備", "TWO"),
    "3583": ("辛耘", "👑電子權值・先進封裝設備", "TW"), "3558": ("神準", "👑電子權值・企業網通", "TWO"),
    "6285": ("啟碁", "👑電子權值・車用與衛星網通", "TW"), "3037": ("欣興", "👑電子權值・ABF載板龍頭", "TW")
}
POLICY_DEFENSE_CHIPS = {
    "1513": ("中興電", "🛡️政策防彈衣・GIS強韌電網龍頭", "TW"), "1514": ("亞力", "🛡️政策防彈衣・半導體配電與電網", "TW"),
    "8936": ("國統", "🛡️政策防彈衣・海淡廠與水資源龍頭", "TW"), "9933": ("中鼎", "🛡️政策防彈衣・兆元統包工程龍頭", "TW"),
    "6139": ("亞翔", "🛡️政策防彈衣・半導體無塵室與水電工程", "TW")
}
FINANCIAL_BLUE_CHIPS = {
    "2886": ("兆豐金", "🛡️政策防彈衣・官股匯銀龍頭", "TW"), "2891": ("中信金", "🛡️政策防彈衣・消金龍頭金控", "TW"),
    "2881": ("富邦金", "🏦金融權值・獲利王金控", "TW"), "2882": ("國泰金", "🏦金融權值・壽險龍頭金控", "TW"),
    "2884": ("玉山金", "🏦金融權值・高成長民營金控", "TW"), "2885": ("元大金", "🏦金融權值・證券龍頭金控", "TW"),
    "2892": ("第一金", "🛡️政策防彈衣・績優官股金控", "TW"), "2880": ("華南金", "🏦金融權值・穩健官股金控", "TW"),
    "5880": ("合庫金", "🛡️政策防彈衣・官股房貸龍頭", "TW"), "2890": ("永豐金", "🏦金融權值・高ROE民營金控", "TW"),
    "2883": ("凱基金", "🏦金融權值・壽險與創投金控", "TW"), "2887": ("台新新光金", "🏦金融權值・併購成長金控", "TW"),
    "2801": ("彰銀", "🛡️政策防彈衣・官股核心銀行", "TW"), "2834": ("臺企銀", "🛡️政策防彈衣・中小企銀官股", "TW"),
    "2812": ("台中銀", "🏦金融權值・核心績優商銀", "TW")
}
CORE_WATCHLIST = {**ELECTRONICS_BLUE_CHIPS, **POLICY_DEFENSE_CHIPS, **FINANCIAL_BLUE_CHIPS}
CONSTRUCTION_BLACKLIST = {"1808", "2501", "2504", "2505", "2506", "2509", "2511", "2515", "2516", "2520", "2524", "2527", "2528", "2530", "2534", "2535", "2536", "2537", "2538", "2539", "2540", "2542", "2543", "2545", "2546", "2547", "2548", "2597", "5522", "5534", "4113", "5529", "5455", "1439", "9906", "7777", "5523"}
CORE_PORTFOLIO_16 = [
    ("2330", "TW"), ("3711", "TW"), ("6239", "TW"), ("3264", "TWO"),
    ("2884", "TW"), ("2881", "TW"), ("2812", "TW"), ("2887", "TW"), ("2834", "TW"),
    ("0050", "TW"), ("00631L", "TW"), ("00400A", "TW"), ("00985B", "TW"), ("00713", "TW")
]

# ==========================================
# 第一層秒開引擎：極速抓取全市場廣度、大盤三源最新日、真浮動融資曲線、期貨雙備援與美股
# ==========================================
def parse_mis_price(item, is_index=False):
    def to_f(val):
        try:
            s = str(val).replace(",", "").strip()
            return float(s) if s not in ["", "-", "--", "None"] else None
        except Exception: return None
    z_val = to_f(item.get("z"))
    if z_val and z_val > 0: return z_val
    if not is_index:
        b_val, a_val = to_f(str(item.get("b", "")).split("_")[0]), to_f(str(item.get("a", "")).split("_")[0])
        if b_val and a_val and b_val > 0 and a_val > 0: return round((b_val + a_val) / 2.0, 2)
        if b_val and b_val > 0: return b_val
        if a_val and a_val > 0: return a_val
    return None if is_index else to_f(item.get("y"))

def fetch_yahoo_v8_realtime(ticker_symbol, decimals=2):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}?interval=1m&range=1d&includePrePost=true&_={int(time.time() * 1000)}"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"}, timeout=5, verify=False)
        if r.status_code == 200:
            res = r.json().get("chart", {}).get("result", [])
            if res:
                meta = res[0].get("meta", {})
                ind = res[0].get("indicators", {}).get("quote", [{}])[0]
                closes = [x for x in ind.get("close", []) if x is not None]
                opens = [x for x in ind.get("open", []) if x is not None]
                highs = [x for x in ind.get("high", []) if x is not None]
                lows = [x for x in ind.get("low", []) if x is not None]
                p_now = float(meta.get("regularMarketPrice", closes[-1] if closes else 0.0))
                y_prev = float(meta.get("chartPreviousClose", meta.get("previousClose", p_now)))
                o_now = float(meta.get("regularMarketOpen", opens[0] if opens else p_now))
                h_now = float(meta.get("regularMarketDayHigh", max(highs) if highs else p_now))
                l_now = float(meta.get("regularMarketDayLow", min(lows) if lows else p_now))
                mkt_time = meta.get("regularMarketTime", int(time.time()))
                dt_obj = datetime.datetime.fromtimestamp(mkt_time, pytz.timezone("Asia/Taipei"))

                mkt_state = str(meta.get("marketState", "CLOSED")).upper()
                post_p, pre_p = meta.get("postMarketPrice", None), meta.get("preMarketPrice", None)
                ext_chg_pct, ext_label = None, ""
                if mkt_state in ["POST", "POSTPOST", "CLOSED"] and post_p is not None and float(post_p) > 0 and p_now > 0:
                    ext_chg_pct = round(((float(post_p) - p_now) / p_now) * 100.0, 2); ext_label = f"盤後 {ext_chg_pct:+.2f}%"
                elif mkt_state in ["PRE", "PREPRE"] and pre_p is not None and float(pre_p) > 0 and p_now > 0:
                    ext_chg_pct = round(((float(pre_p) - p_now) / p_now) * 100.0, 2); ext_label = f"盤前 {ext_chg_pct:+.2f}%"
                elif len(closes) > 1 and mkt_state != "REGULAR":
                    last_ext_p = float(closes[-1])
                    if abs(last_ext_p - p_now) / max(1.0, p_now) > 0.0005:
                        ext_chg_pct = round(((last_ext_p - p_now) / p_now) * 100.0, 2); ext_label = f"盤後 {ext_chg_pct:+.2f}%"

                if p_now > 0:
                    return {
                        "open": round(o_now, decimals), "close": round(p_now, decimals),
                        "high": round(max(h_now, p_now), decimals), "low": round(min(l_now, p_now), decimals),
                        "prev_close": round(y_prev, decimals),
                        "chg_pct": round(((p_now - y_prev) / y_prev) * 100, 2) if y_prev > 0 else 0.0,
                        "mkt_state": mkt_state, "ext_chg_pct": ext_chg_pct, "ext_label": ext_label,
                        "vol_lots": 0, "time": dt_obj.strftime("%H:%M:%S"),
                        "date": dt_obj.strftime("%Y%m%d"), "date_iso": dt_obj.strftime("%Y-%m-%d"), "ts": mkt_time
                    }
    except Exception: pass
    return None

@st.cache_data(ttl=15, show_spinner=False)
def fetch_mis_realtime_batch(code_market_tuples):
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp", "Cache-Control": "no-cache"}
    rt_dict = {}
    all_pairs = list(dict.fromkeys(list(code_market_tuples) + CORE_PORTFOLIO_16))
    queries = ["tse_t00.tw"] + [f"{'tse' if m == 'TW' else 'otc'}_{c}.tw" for c, m in all_pairs]
    for i in range(0, len(queries), 25):
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={'|'.join(queries[i:i + 25])}&json=1&delay=0&_={int(time.time() * 1000)}"
        try:
            r = requests.get(url, headers=headers, timeout=5, verify=False)
            if r.status_code == 200:
                for item in r.json().get("msgArray", []):
                    c_code = str(item.get("c", "")).strip()
                    p_now = parse_mis_price(item, is_index=(c_code == "t00"))
                    if not c_code or p_now is None: continue
                    def clean_f(k, dv):
                        try: return float(str(item.get(k, dv)).replace(",", ""))
                        except Exception: return dv
                    y_prev, o_now, h_now, l_now, v_lots = clean_f("y", p_now), clean_f("o", p_now), clean_f("h", p_now), clean_f("l", p_now), int(clean_f("v", 0))
                    d_raw = str(item.get("d", "")).strip()
                    d_iso = f"{d_raw[:4]}-{d_raw[4:6]}-{d_raw[6:8]}" if len(d_raw) == 8 else ""
                    rt_dict[c_code] = {
                        "open": round(o_now, 2), "close": round(p_now, 2), "high": round(max(h_now, p_now), 2), "low": round(min(l_now, p_now), 2),
                        "prev_close": round(y_prev, 2), "chg_pct": round(((p_now - y_prev) / y_prev) * 100, 2) if y_prev > 0 else 0.0,
                        "vol_lots": v_lots, "time": str(item.get("t", "")), "date": d_raw, "date_iso": d_iso
                    }
        except Exception: continue
    yv8_t00 = fetch_yahoo_v8_realtime("%5ETWII", decimals=2)
    if yv8_t00 and yv8_t00["close"] > 0:
        if "t00" not in rt_dict or yv8_t00.get("date_iso", "") > rt_dict["t00"].get("date_iso", ""):
            rt_dict["t00"] = yv8_t00
    fx_v8 = fetch_yahoo_v8_realtime("TWD=X", decimals=3)
    if fx_v8 and fx_v8["close"] > 10.0: rt_dict["USDTWD"] = fx_v8
    return rt_dict

@st.cache_data(ttl=15, show_spinner=False)
def fetch_mis_realtime_core16():
    """Layer 1 秒開專用：僅抓取大盤 t00 + 匯率 + 14 檔個人核心資產，耗時 < 0.8 秒"""
    return fetch_mis_realtime_batch(tuple(CORE_PORTFOLIO_16))

@st.cache_data(ttl=300, show_spinner=False)
def fetch_market_prices_fast():
    """
    Layer 1 秒開專用 (僅 3 次快速 JSON 請求，約 1.2 秒)：
    取得全市場 ~1,972 檔上市櫃最新收盤價與漲跌幅、計算真實『漲停/跌停/上漲家數比』廣度，並取得證交所 FMTQIK 最新大盤收盤。
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    price_dict = {}
    twii_fmtqik = {"close": 0.0, "prev_close": 0.0, "chg_pct": 0.0, "date_iso": ""}
    track_etfs = {"0050", "00631L", "00400A", "00985B", "00713"}

    try:
        r_idx = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK", headers=headers, timeout=6, verify=False)
        if r_idx.status_code == 200 and len(r_idx.json()) > 0:
            last_row = r_idx.json()[-1]
            idx_c = float(str(last_row.get("TAIEX", "0")).replace(",", ""))
            idx_chg = float(str(last_row.get("Change", "0")).replace(",", "").replace("+", ""))
            prev_idx = idx_c - idx_chg
            d_raw = str(last_row.get("Date", "")).replace("/", "").replace("-", "").strip()
            d_iso = ""
            if len(d_raw) == 7 and d_raw.isdigit():
                d_iso = f"{int(d_raw[:3]) + 1911}-{d_raw[3:5]}-{d_raw[5:7]}"
            elif len(d_raw) == 8 and d_raw.isdigit():
                d_iso = f"{d_raw[:4]}-{d_raw[4:6]}-{d_raw[6:8]}"
            if idx_c > 0:
                twii_fmtqik = {"close": round(idx_c, 2), "prev_close": round(prev_idx, 2), "chg_pct": round((idx_chg / max(1.0, prev_idx)) * 100.0, 2), "date_iso": d_iso}
    except Exception: pass

    try:
        r_tw = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=8, verify=False)
        if r_tw.status_code == 200:
            for row in r_tw.json():
                code = str(row.get("Code", "")).strip()
                if (len(code) == 4 and code.isdigit()) or code in track_etfs:
                    try:
                        c_str = str(row.get("ClosingPrice", "")).replace(",", "").strip()
                        if not c_str or c_str in ["-", "--"]: continue
                        c_p = float(c_str)
                        chg_raw = str(row.get("Change", "0")).replace(",", "").replace("+", "").replace("X", "").strip()
                        try: chg_v = float(chg_raw)
                        except Exception: chg_v = 0.0
                        if "-" in str(row.get("Change", "")) and chg_v > 0: chg_v = -chg_v
                        prev_c = c_p - chg_v
                        v_str = str(row.get("TradeVolume", "0")).replace(",", "").strip()
                        v_sh = float(v_str) if v_str.replace(".", "").isdigit() else 0.0
                        if c_p > 0:
                            price_dict[code] = {
                                "name": str(row.get("Name", "")).strip(), "close": c_p,
                                "chg_pct": round((chg_v / prev_c) * 100, 2) if prev_c > 0 else 0.0,
                                "vol_lots": int(v_sh // 1000), "vol_shares": v_sh, "market": "TW",
                                "pe": np.nan, "cum_yoy": None, "fini_lots": 0, "sitc_lots": 0, "sector": "",
                                "tdcc_1000": None, "tdcc_400_delta": None, "tdcc_1000_delta": None, "day_trade_str": "N/A (交由淘金客聯網查核)"
                            }
                    except Exception: continue
    except Exception: pass

    try:
        r_otc = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", headers=headers, timeout=8, verify=False)
        if r_otc.status_code == 200:
            for row in r_otc.json():
                code = str(row.get("SecuritiesCompanyCode", row.get("Code", ""))).strip()
                if (len(code) == 4 and code.isdigit()) or code in track_etfs:
                    try:
                        c_str = str(row.get("Close", row.get("ClosingPrice", ""))).replace(",", "").strip()
                        if not c_str or c_str in ["-", "--", "N/A", ""]: continue
                        c_p = float(c_str)
                        chg_raw = str(row.get("Change", "0")).replace(",", "").replace("+", "").replace(" ", "").strip()
                        try: chg_v = float(chg_raw)
                        except Exception: chg_v = 0.0
                        prev_c = c_p - chg_v
                        v_str = str(row.get("TradingShares", row.get("TradeVolume", "0"))).replace(",", "").strip()
                        try: v_sh = float(v_str)
                        except Exception: v_sh = 0.0
                        if c_p > 0:
                            price_dict[code] = {
                                "name": str(row.get("CompanyName", row.get("Name", ""))).strip(), "close": c_p,
                                "chg_pct": round((chg_v / prev_c) * 100, 2) if prev_c > 0 else 0.0,
                                "vol_lots": int(v_sh // 1000), "vol_shares": v_sh, "market": "TWO",
                                "pe": np.nan, "cum_yoy": None, "fini_lots": 0, "sitc_lots": 0, "sector": "",
                                "tdcc_1000": None, "tdcc_400_delta": None, "tdcc_1000_delta": None, "day_trade_str": "N/A (交由淘金客聯網查核)"
                            }
                    except Exception: continue
    except Exception: pass

    # 計算全市場真實廣度（供 2025/04 無量鎖漲停廣度豁免 ＆ 跌停收斂判定）
    lim_up = sum(1 for c, i in price_dict.items() if len(c) == 4 and c.isdigit() and i["chg_pct"] >= 9.4)
    lim_dn = sum(1 for c, i in price_dict.items() if len(c) == 4 and c.isdigit() and i["chg_pct"] <= -9.4)
    up_cnt = sum(1 for c, i in price_dict.items() if len(c) == 4 and c.isdigit() and i["chg_pct"] > 0.0)
    dn_cnt = sum(1 for c, i in price_dict.items() if len(c) == 4 and c.isdigit() and i["chg_pct"] < 0.0)
    adv_ratio = round((up_cnt / max(1, up_cnt + dn_cnt)) * 100.0, 1)
    breadth = {"limit_up": lim_up, "limit_down": lim_dn, "up_cnt": up_cnt, "dn_cnt": dn_cnt, "adv_ratio_pct": adv_ratio}
    return price_dict, twii_fmtqik, breadth

US_SECTOR_DEFINITIONS = [
    ("SOXX", "半導體板塊", [("TSM", "台積電ADR"), ("NVDA", "輝達"), ("AVGO", "博通"), ("AMD", "超微")]),
    ("XLK", "科技硬體雲端", [("AAPL", "蘋果"), ("MSFT", "微軟"), ("ORCL", "甲骨文")]),
    ("XLC", "通訊與CSP", [("META", "Meta"), ("GOOGL", "Google")]),
    ("XLY", "非必需消費/電車", [("AMZN", "亞馬遜"), ("TSLA", "特斯拉")]),
    ("XLI", "工業與電力基建", [("GE", "奇異航太"), ("CAT", "卡特彼勒")]),
    ("XLF", "金融銀行板塊", [("JPM", "摩根大通"), ("GS", "高盛")]),
    ("XLV", "生技醫療板塊", [("LLY", "禮來"), ("UNH", "聯合健康")]),
    ("XLE", "能源與塑化", [("XOM", "埃克森美孚"), ("CVX", "雪佛龍")])
]

@st.cache_data(ttl=120, show_spinner=False)
def fetch_us_sectors_and_leaders():
    all_syms = ["%5EVIX", "%5ESOX", "%5ETNX", "%5EGSPC", "%5EIXIC", "%5EDJI", "NQ=F", "SGOV"]
    for etf_sym, _, leaders in US_SECTOR_DEFINITIONS:
        all_syms.append(etf_sym)
        for l_sym, _ in leaders:
            if l_sym not in all_syms: all_syms.append(l_sym)
    quotes_map = {}
    with ThreadPoolExecutor(max_workers=12) as executor:
        fut_map = {executor.submit(fetch_yahoo_v8_realtime, sym, 3): sym for sym in all_syms}
        for fut in as_completed(fut_map):
            res_q = fut.result()
            if res_q: quotes_map[fut_map[fut]] = res_q
    tnx_q = quotes_map.get("%5ETNX", {})
    tnx_raw = tnx_q.get("close", 4.92)
    tnx_val = round(tnx_raw / 10.0, 2) if tnx_raw > 15.0 else round(tnx_raw, 2)
    tnx_chg = tnx_q.get("chg_pct", 0.0)
    m_state = quotes_map.get("TSM", {}).get("mkt_state", "CLOSED")
    nq_chg = quotes_map.get("NQ=F", {}).get("chg_pct", 0.0)
    us_session_tag = "🟢【美股盤中即時交易中】" if m_state == "REGULAR" else (f"🌅【美股收盤＋盤前電子盤 (NQ {nq_chg:+.2f}%)】" if m_state in ["PRE", "PREPRE"] else f"🏁【美股正規收盤 ＋ 🌙盤後/那指期 (NQ {nq_chg:+.2f}%)】")
    sector_list = []
    for etf_sym, sec_name, leaders in US_SECTOR_DEFINITIONS:
        eq = quotes_map.get(etf_sym, {})
        sec_chg = eq.get("chg_pct", 0.0)
        ext_lbl = f"({eq['ext_label']})" if eq.get("ext_label") else ""
        leader_strs = []
        for l_sym, l_name in leaders:
            lq = quotes_map.get(l_sym, {})
            l_chg = lq.get("chg_pct", 0.0)
            l_ext = f"[{lq['ext_label']}]" if lq.get("ext_label") else ""
            leader_strs.append((l_chg, f"{l_name}({l_sym}) {l_chg:+.2f}%{l_ext}"))
        leader_strs.sort(key=lambda x: x[0], reverse=True)
        sector_list.append({"etf": etf_sym, "name": sec_name, "chg_pct": sec_chg, "summary": f"<b>{sec_name}({etf_sym} {sec_chg:+.2f}%{ext_lbl})</b> ➔ 指標股：{'、'.join([s for _, s in leader_strs[:3]])}"})
    sector_list.sort(key=lambda x: x["chg_pct"], reverse=True)
    return {
        "session_tag": us_session_tag, "quotes": quotes_map, "tnx_val": tnx_val, "tnx_chg": tnx_chg, "nq_chg": nq_chg,
        "top_up_sectors": [x["summary"] for x in sector_list[:3]],
        "bot_dn_sectors": [x["summary"] for x in sorted(sector_list, key=lambda x: x["chg_pct"])[:2]]
    }

@st.cache_data(ttl=120, show_spinner=False)
def fetch_auto_derivatives_and_margin_v62(twii_now: float):
    """
    【v62.0 全浮動總經籌碼引擎】：
    1. 期貨 OI & 連補天數：期交所直連 ＋ FinMind TaiwanFuturesInstitutionalInvestors(TX) 雙備援。
    2. 真實期現貨逆價差：期交所 OpenAPI DailyMarketReportFut ＋ FinMind TaiwanFuturesDaily(TX) 實算。
    3. 大盤融資餘額、近2日增減(億/%)與30日波段融資減幅%：證交所 RWD MI_MARGN(MS總表) ＋ FinMind 45日融資曲線實算。
    4. PCR 與權證避險：期交所 OpenAPI/CSV ＋ 證交所 BFI82U。
    """
    headers = {"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"}
    now_tw = get_tw_now()
    res = {
        "fini_tx_oi": 0, "fini_tx_chg": 0, "sitc_tx_oi": 0, "sitc_tx_chg": 0, "dealer_tx_chg": 0,
        "consec_cover_days": 0, "cover_3d_sum": 0, "fut_src": "自動讀取中", "fut_date": "",
        "tx_close": 0.0, "basis_pts": 0.0, "basis_src": "自動換算",
        "pc_ratio": 80.0, "warrant_hedge_yi": 0.0, "twse_date": "",
        "margin_balance_yi": 0.0, "margin_chg_yi": 0.0, "margin_2d_chg_yi": 0.0, "margin_2d_pct": 0.0,
        "margin_30d_dd_pct": 0.0, "margin_src": "自動讀取中"
    }

    # --- A. 選擇權 PCR ---
    pcr_ok = False
    try:
        r_p = requests.get("https://openapi.taifex.com.tw/v1/PutCallRatio", headers=headers, timeout=5, verify=False)
        if r_p.status_code == 200 and len(r_p.json()) > 0:
            lr = r_p.json()[-1]
            for k in ["PutCallOpenInterestRatio", "PutCallOpenInterestRatio%", "PutCallRatio"]:
                if k in lr and str(lr[k]).strip():
                    v = float(str(lr[k]).replace(",", "").strip())
                    if 10.0 <= v <= 250.0: res["pc_ratio"] = round(v, 1); pcr_ok = True; break
    except Exception: pass
    if not pcr_ok:
        try:
            r_pc = requests.post("https://www.taifex.com.tw/cht/3/pcRatioDown", data={"queryStartDate": (now_tw - datetime.timedelta(days=12)).strftime("%Y/%m/%d"), "queryEndDate": now_tw.strftime("%Y/%m/%d")}, headers=headers, timeout=5, verify=False)
            if r_pc.status_code == 200 and len(r_pc.content) > 50:
                lines = [ln.strip().rstrip(",") for ln in r_pc.content.decode("cp950", errors="ignore").splitlines() if re.match(r"^\d{4}/\d{1,2}/\d{1,2}", ln.strip())]
                if lines:
                    lines.sort(key=lambda s: s.split(",")[0])
                    parts = [p.strip() for p in lines[-1].split(",") if p.strip()]
                    if len(parts) >= 7:
                        v = float(parts[6].replace("%", ""))
                        if 10.0 <= v <= 250.0: res["pc_ratio"] = round(v, 1)
        except Exception: pass

    # --- B. 權證避險 (BFI82U) ---
    for d_back in range(0, 6):
        chk_d = (now_tw - datetime.timedelta(days=d_back)).strftime("%Y%m%d")
        try:
            r_bfi = requests.get(f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate={chk_d}&type=day&response=json", headers=headers, timeout=5, verify=False)
            if r_bfi.status_code == 200 and len(r_bfi.json().get("data", [])) >= 3:
                for row in r_bfi.json()["data"]:
                    if "避險" in str(row[0]):
                        res["warrant_hedge_yi"] = round(float(str(row[3]).replace(",", "").strip()) / 1e8, 2)
                        res["twse_date"] = chk_d; break
                if res["twse_date"]: break
        except Exception: continue

    # --- C. 外資／投信期貨 OI 與連補天數 (通道1: 期交所直連；通道2: FinMind TX 備援) ---
    fut_ok = False
    try:
        r_fut = requests.post("https://www.taifex.com.tw/cht/3/futContractsDateDown", data={"queryStartDate": (now_tw - datetime.timedelta(days=14)).strftime("%Y/%m/%d"), "queryEndDate": now_tw.strftime("%Y/%m/%d")}, headers=headers, timeout=5, verify=False)
        if r_fut.status_code == 200 and len(r_fut.content) > 200:
            df_f = pd.read_csv(io.StringIO(r_fut.content.decode("cp950", errors="ignore")))
            df_f.columns = [str(c).strip() for c in df_f.columns]
            if len(df_f.columns) >= 14:
                dates = sorted(df_f.iloc[:, 0].dropna().astype(str).str.strip().unique())
                if dates:
                    latest_d = dates[-1]
                    oi_net_col = next((c for c in df_f.columns if "未平倉" in c and "口數" in c and "淨額" in c), None)
                    oi_l_col = next((c for c in df_f.columns if "多方" in c and "未平倉" in c and "口數" in c), None)
                    oi_s_col = next((c for c in df_f.columns if "空方" in c and "未平倉" in c and "口數" in c), None)
                    daily_f = []
                    for d_cur in dates:
                        for _, row in df_f[df_f.iloc[:, 0].astype(str).str.strip() == d_cur].iterrows():
                            pn, ident = str(row.iloc[1]).strip(), str(row.iloc[2]).strip()
                            if ("臺股期貨" in pn or "台股期貨" in pn) and ("小型" not in pn) and ("微型" not in pn):
                                tr = int(float(str(row.iloc[7]).replace(",", "").strip() or 0))
                                oi = int(float(str(row[oi_net_col]).replace(",", "").strip() or 0)) if oi_net_col else (int(float(str(row[oi_l_col]).replace(",", "").strip() or 0)) - int(float(str(row[oi_s_col]).replace(",", "").strip() or 0)) if (oi_l_col and oi_s_col) else int(float(str(row.iloc[13]).replace(",", "").strip() or 0)))
                                if "外資" in ident:
                                    daily_f.append(tr)
                                    if d_cur == latest_d: res["fini_tx_chg"], res["fini_tx_oi"] = tr, oi
                                elif "投信" in ident and d_cur == latest_d:
                                    res["sitc_tx_chg"], res["sitc_tx_oi"] = tr, oi
                    if daily_f and res["fini_tx_oi"] != 0:
                        consec = 0
                        for tv in reversed(daily_f):
                            if tv > 0: consec += 1
                            else: break
                        res["consec_cover_days"], res["cover_3d_sum"] = consec, int(sum(daily_f[-3:]))
                        res["fut_date"], res["fut_src"], fut_ok = latest_d, f"期交所直連({latest_d})", True
    except Exception: pass

    if not fut_ok:
        try:
            st_d = (now_tw - datetime.timedelta(days=20)).strftime("%Y-%m-%d")
            r_fm = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanFuturesInstitutionalInvestors&data_id=TX&start_date={st_d}", timeout=5, verify=False)
            if r_fm.status_code == 200 and r_fm.json().get("data"):
                df_fm = pd.DataFrame(r_fm.json()["data"])
                dates = sorted(df_fm["date"].unique())
                if dates:
                    latest_d = dates[-1]
                    df_fini = df_fm[df_fm["institutional_investors"].astype(str).str.contains("外資|Foreign", case=False, na=False)].sort_values("date")
                    df_sitc = df_fm[df_fm["institutional_investors"].astype(str).str.contains("投信|Investment_Trust", case=False, na=False)].sort_values("date")
                    if not df_fini.empty:
                        df_fini["net_tr"] = df_fini["long_deal_volume"].astype(float) - df_fini["short_deal_volume"].astype(float)
                        df_fini["net_oi"] = df_fini["long_open_interest_balance_volume"].astype(float) - df_fini["short_open_interest_balance_volume"].astype(float)
                        res["fini_tx_oi"] = int(round(df_fini["net_oi"].iloc[-1]))
                        res["fini_tx_chg"] = int(round(df_fini["net_tr"].iloc[-1]))
                        daily_f = [int(round(x)) for x in df_fini["net_tr"].values]
                        consec = 0
                        for tv in reversed(daily_f):
                            if tv > 0: consec += 1
                            else: break
                        res["consec_cover_days"], res["cover_3d_sum"] = consec, int(sum(daily_f[-3:]))
                        res["fut_date"], res["fut_src"], fut_ok = str(latest_d), f"FinMind備援({latest_d})", True
                    if not df_sitc.empty:
                        res["sitc_tx_oi"] = int(round(float(df_sitc["long_open_interest_balance_volume"].iloc[-1]) - float(df_sitc["short_open_interest_balance_volume"].iloc[-1])))
        except Exception: pass

    # --- D. 真實台指期收盤與期現貨逆價差 ---
    tx_ok = False
    try:
        r_tx = requests.get("https://openapi.taifex.com.tw/v1/DailyMarketReportFut", headers=headers, timeout=5, verify=False)
        if r_tx.status_code == 200 and isinstance(r_tx.json(), list):
            tx_rows = [row for row in r_tx.json() if str(row.get("Contract", "")).strip() == "TX" and len(str(row.get("ContractMonth(Week)", "")).strip()) == 6 and str(row.get("TradingSession", "")).strip() in ["一般", "Regular", ""]]
            if tx_rows:
                tx_rows.sort(key=lambda x: str(x.get("ContractMonth(Week)", "")))
                c_tx = float(str(tx_rows[0].get("Last", tx_rows[0].get("SettlementPrice", "0"))).replace(",", "").strip())
                if c_tx > 5000:
                    res["tx_close"], res["basis_pts"], res["basis_src"], tx_ok = c_tx, round(c_tx - twii_now, 0), "期交所OpenAPI", True
    except Exception: pass
    if not tx_ok:
        try:
            st_d = (now_tw - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
            r_tx2 = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanFuturesDaily&data_id=TX&start_date={st_d}", timeout=5, verify=False)
            if r_tx2.status_code == 200 and r_tx2.json().get("data"):
                df_t = pd.DataFrame(r_tx2.json()["data"])
                df_t = df_t[(df_t["trading_session"] == "position") & (df_t["contract_date"].astype(str).str.len() == 6)]
                if not df_t.empty:
                    latest_d = df_t["date"].max()
                    sub_t = df_t[df_t["date"] == latest_d].sort_values("contract_date")
                    c_tx = float(sub_t["close"].iloc[0])
                    if c_tx > 5000:
                        res["tx_close"], res["basis_pts"], res["basis_src"], tx_ok = c_tx, round(c_tx - twii_now, 0), f"FinMind({latest_d})", True
        except Exception: pass

    # --- E. 真實大盤融資餘額、2日增減(億/%)與30日波段融資減幅% (FinMind 45日曲線 ＋ 證交所 RWD MS 總表) ---
    mgn_ok = False
    try:
        st_m = (now_tw - datetime.timedelta(days=55)).strftime("%Y-%m-%d")
        r_m = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockTotalMarginPurchaseShortSale&start_date={st_m}", timeout=6, verify=False)
        if r_m.status_code == 200 and r_m.json().get("data"):
            df_m = pd.DataFrame(r_m.json()["data"])
            df_mm = df_m[df_m["name"].astype(str).str.contains("MarginPurchaseMoney", case=False, na=False)].sort_values("date")
            if len(df_mm) >= 3:
                vals = df_mm["TodayBalance"].astype(float).values
                # 自動縮放為億元 (仟元 ÷ 1e5；元 ÷ 1e8)
                scale = 1e8 if np.median(vals) > 1e10 else (1e5 if np.median(vals) > 1e6 else 1.0)
                vals_yi = vals / scale
                cur_yi, prev1_yi, prev2_yi = float(vals_yi[-1]), float(vals_yi[-2]), float(vals_yi[-3])
                hi30_yi = float(np.max(vals_yi[-30:]))
                res["margin_balance_yi"] = round(cur_yi, 1)
                res["margin_chg_yi"] = round(cur_yi - prev1_yi, 1)
                res["margin_2d_chg_yi"] = round(cur_yi - prev2_yi, 1)
                res["margin_2d_pct"] = round(((cur_yi - prev2_yi) / max(1.0, prev2_yi)) * 100.0, 2)
                res["margin_30d_dd_pct"] = round(((hi30_yi - cur_yi) / max(1.0, hi30_yi)) * 100.0, 2)
                res["margin_src"], mgn_ok = f"FinMind信用曲線({df_mm['date'].iloc[-1]})", True
    except Exception: pass

    if not mgn_ok:
        for d_back in range(0, 5):
            chk_dt = now_tw - datetime.timedelta(days=d_back)
            if chk_dt.weekday() >= 5: continue
            try:
                r_ms = requests.get(f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={chk_dt.strftime('%Y%m%d')}&selectType=MS&response=json", headers=headers, timeout=5, verify=False)
                if r_ms.status_code == 200:
                    tables = r_ms.json().get("tables", [])
                    if tables and tables[0].get("data"):
                        for row in tables[0]["data"]:
                            if "融資金額" in str(row[0]):
                                prev_k = float(str(row[4]).replace(",", "").strip())
                                cur_k = float(str(row[5]).replace(",", "").strip())
                                if cur_k > 100000:
                                    res["margin_balance_yi"] = round(cur_k / 1e5, 1)
                                    res["margin_chg_yi"] = round((cur_k - prev_k) / 1e5, 1)
                                    res["margin_2d_chg_yi"] = res["margin_chg_yi"]
                                    res["margin_2d_pct"] = round(((cur_k - prev_k) / max(1.0, prev_k)) * 100.0, 2)
                                    res["margin_src"], mgn_ok = f"證交所RWD({chk_dt.strftime('%Y-%m-%d')})", True
                                    break
                        if mgn_ok: break
            except Exception: continue

    return res

# ==========================================
# 四軌落底偵測器 (對接淘金客 v24.4 模組 0 落底第 2 項：神針／長紅吞噬／2025-04無量鎖漲停豁免／FTD與縮量右腳 ＋ 前哨黃燈)
# ==========================================
def detect_4track_bottom_signals(o_arr, h_arr, l_arr, c_arr, v_arr, limit_up_cnt: int, adv_ratio_pct: float, vol_mult_use: float = 1.0) -> dict:
    out = {"passed": False, "track_hit": "未觸發", "detail": "尚無底部反轉確認型態", "precursor": "", "vol_ratio": 1.0, "sh_ratio": 0.0}
    try:
        if len(c_arr) < 22: return out
        o0, h0, l0, c0 = float(o_arr[-1]), float(h_arr[-1]), float(l_arr[-1]), float(c_arr[-1])
        o1, h1, l1, c1 = float(o_arr[-2]), float(h_arr[-2]), float(l_arr[-2]), float(c_arr[-2])
        o2, c2 = float(o_arr[-3]), float(c_arr[-3])
        v0 = float(v_arr[-1]) * vol_mult_use
        v1 = max(1.0, float(v_arr[-2]))
        vol_20ma = max(1.0, float(np.mean(v_arr[-21:-1])))
        vol_ratio = round(v0 / vol_20ma, 2)
        chg_1d = ((c0 - c1) / max(1.0, c1)) * 100.0
        day_rng = max(1.0, h0 - l0)
        body = max(10.0, abs(c0 - o0))
        lower_sh = max(0.0, min(c0, o0) - l0)
        upper_sh = max(0.0, h0 - max(c0, o0))
        sh_ratio = round(lower_sh / body, 2)
        up_sh_ratio = round(upper_sh / body, 2)
        close_pos = (c0 - l0) / day_rng
        close_above_low_pct = ((c0 - l0) / max(1.0, l0)) * 100.0
        out["vol_ratio"], out["sh_ratio"] = vol_ratio, sh_ratio

        # 2a. 爆量神針探底 (長下影線)
        hit_2a = (vol_ratio >= 1.25) and (sh_ratio >= 1.5) and (close_above_low_pct >= 1.5)
        # 2b. 帶量長紅／多頭吞噬／晨星／底部向上跳空
        is_bull_engulf = (c1 < o1) and (c0 > o0) and (c0 >= o1) and (o0 <= c1 * 1.003)
        is_strong_red = (c0 > o0) and (chg_1d >= 2.5) and (close_pos >= 0.75)
        is_morning_star = (c2 < o2 * 0.985) and (abs(c1 - o1) <= abs(c2 - o2) * 0.45) and (c0 > o0) and (c0 >= (o2 + c2) / 2.0)
        is_gap_up_rev = (l0 > h1 * 1.002) and (c0 > o0)
        hit_2b = (vol_ratio >= 1.20 or v0 > v1) and (is_bull_engulf or is_strong_red or is_morning_star or is_gap_up_rev)
        # 2c. 2025/04 無量鎖漲停／廣度推力豁免
        hit_2c = (chg_1d >= 4.0) and ((limit_up_cnt >= 100) or (adv_ratio_pct >= 85.0))
        # 2d. 歐尼爾 FTD 確認日 (低點後 4~10 日漲 >=1.7% 且量>前日) 或 威科夫縮量右腳
        recent_15_lows = l_arr[-15:]
        low_idx = int(np.argmin(recent_15_lows))
        days_since_low = (len(recent_15_lows) - 1) - low_idx
        min_l15 = float(recent_15_lows[low_idx])
        hit_ftd = (4 <= days_since_low <= 10) and (chg_1d >= 1.7) and (v0 > v1)
        hit_wyckoff_st = (days_since_low >= 3) and (l0 >= min_l15 * 0.992) and (l0 <= min_l15 * 1.035) and (vol_ratio < 0.75) and (c0 > o0)
        hit_2d = hit_ftd or hit_wyckoff_st

        # 底部前哨黃燈偵測（不計分，僅提醒）：急跌後低檔十字線 或 低檔帶量沖天炮(長上影試盤)
        near_20d_low = (l0 <= float(np.min(l_arr[-20:])) * 1.04)
        is_doji = near_20d_low and (abs(c0 - o0) <= day_rng * 0.16)
        is_low_rocket = near_20d_low and (up_sh_ratio >= 1.5) and (vol_ratio >= 1.15)
        if is_doji: out["precursor"] = "🟡 底部前哨：低檔十字線煞車（待隔日紅K確認）"
        elif is_low_rocket: out["precursor"] = "🟡 底部前哨：低檔帶量沖天炮試盤（禁追，待吞噬上影確認）"

        hits = []
        if hit_2c: hits.append(f"2c無量鎖漲停/廣度推力豁免(漲+{chg_1d:.1f}%・漲停{limit_up_cnt}家/上漲比{adv_ratio_pct:.0f}%)")
        if hit_2a: hits.append(f"2a爆量神針探底(量{vol_ratio:.2f}x・下影{sh_ratio:.1f}x)")
        if hit_2b:
            lbl_2b = "多頭吞噬" if is_bull_engulf else ("晨星反轉" if is_morning_star else ("跳空反轉" if is_gap_up_rev else "帶量長紅"))
            hits.append(f"2b{lbl_2b}(量{vol_ratio:.2f}x・漲{chg_1d:+.1f}%)")
        if hit_2d:
            hits.append(f"2d{'歐尼爾FTD確認日' if hit_ftd else '威科夫縮量右腳'}(低點後第{days_since_low}日)")

        if hits:
            out["passed"] = True
            out["track_hit"] = " ＋ ".join(hits)
            out["detail"] = out["track_hit"]
        else:
            out["detail"] = f"量比 {vol_ratio:.2f}x ｜ 下影/實體 {sh_ratio:.2f}x ｜ 漲停 {limit_up_cnt}家/上漲比 {adv_ratio_pct:.0f}%"
    except Exception:
        pass
    return out

def compute_kdj(highs, lows, closes, n=9):
    try:
        k_val, d_val, j_val, k_prev, d_prev, j_prev = 50.0, 50.0, 50.0, 50.0, 50.0, 50.0
        for i in range(len(closes)):
            k_prev, d_prev, j_prev = k_val, d_val, j_val
            start_idx = max(0, i - n + 1)
            hh, ll = np.nanmax(highs[start_idx:i+1]), np.nanmin(lows[start_idx:i+1])
            rsv = ((closes[i] - ll) / (hh - ll) * 100.0) if (pd.notna(hh) and pd.notna(ll) and hh > ll) else 50.0
            k_val = (2.0 / 3.0) * k_val + (1.0 / 3.0) * rsv
            d_val = (2.0 / 3.0) * d_val + (1.0 / 3.0) * k_val
            j_val = 3.0 * k_val - 2.0 * d_val
        return round(k_val, 1), round(d_val, 1), round(j_val, 1), round(j_prev, 1), (k_val >= d_val and k_prev <= d_prev + 3.0)
    except Exception: return 50.0, 50.0, 50.0, 50.0, False

def compute_dmi_and_atr(highs, lows, closes, n=14):
    try:
        if len(closes) < n + 5: return 25.0, 25.0, 20.0, 20.0, False, np.nan
        h, l, c = pd.Series(highs, dtype=float), pd.Series(lows, dtype=float), pd.Series(closes, dtype=float)
        up_move, down_move = h.diff(), -l.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1.0/n, adjust=False).mean()
        plus_di = 100.0 * pd.Series(plus_dm).ewm(alpha=1.0/n, adjust=False).mean() / atr.replace(0, np.nan)
        minus_di = 100.0 * pd.Series(minus_dm).ewm(alpha=1.0/n, adjust=False).mean() / atr.replace(0, np.nan)
        dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.fillna(0.0).ewm(alpha=1.0/n, adjust=False).mean()
        pdi_now, mdi_now = round(float(plus_di.iloc[-1]), 1), round(float(minus_di.iloc[-1]), 1)
        pdi_prev = float(plus_di.iloc[-2]) if len(plus_di) > 1 else pdi_now
        mdi_prev = float(minus_di.iloc[-2]) if len(minus_di) > 1 else mdi_now
        adx_now, adx_prev = round(float(adx.iloc[-1]), 1), round(float(adx.iloc[-2]) if len(adx) > 1 else 20.0, 1)
        dmi_bull = (pdi_now > mdi_now) and (adx_now > adx_prev) and ((pdi_prev <= mdi_prev + 4.0) or (pdi_now - mdi_now > pdi_prev - mdi_prev))
        atr_val, curr_c = float(atr.iloc[-1]), float(c.iloc[-1])
        atr_pct = round((atr_val / curr_c) * 100.0, 2) if (pd.notna(atr_val) and curr_c > 0) else np.nan
        return pdi_now, mdi_now, adx_now, adx_prev, bool(dmi_bull), atr_pct
    except Exception: return np.nan, np.nan, np.nan, np.nan, False, np.nan

def classify_dmi_direction(pdi, mdi, adx, j_val):
    try:
        if any(pd.isna(x) for x in [pdi, mdi, adx]): return "N/A", False
        if adx > 25.0 and pdi > mdi: return ("多頭趨勢確立(強勢鈍化)" if (pd.notna(j_val) and j_val > 100.0) else "多頭趨勢確立"), False
        if adx > 25.0 and mdi > pdi: return "空頭加速（防高ADX假多頭）", True
        if adx < 20.0: return "盤整無趨勢", False
        return ("趨勢萌芽(偏多)" if pdi >= mdi else "趨勢萌芽(偏空)"), False
    except Exception: return "N/A", False

def compute_obv_trend(closes, volumes):
    try:
        if len(closes) < 22 or len(volumes) < 22: return "N/A", False
        diff_c = np.diff(closes, prepend=closes[0])
        obv = np.cumsum(np.where(diff_c > 0, volumes, np.where(diff_c < 0, -volumes, 0.0)))
        obv_5_slope, obv_20_slope = float(obv[-1] - obv[-6]), float(obv[-1] - obv[-21])
        if (float(np.max(closes[-5:])) >= float(np.max(closes[-20:])) * 0.995) and (float(np.max(obv[-5:])) < float(np.max(obv[-20:])) * 0.995 or obv_5_slope <= 0.0):
            return "⚠️OBV背離", True
        if obv_5_slope > 0 and obv_20_slope > 0: return "📈雙斜率向上", False
        if obv_5_slope > 0 and obv_20_slope <= 0: return "🌱短線翻揚", False
        if obv_5_slope <= 0 and obv_20_slope > 0: return "⚖️高檔換手", False
        return "📉斜率向下", False
    except Exception: return "N/A", False

@st.cache_data(ttl=600, show_spinner=False)
def download_fleet_history():
    tickers = ["^TWII", "^GSPC", "^SOX", "^VIX", "TWD=X", "00400A.TW", "00985B.TW", "00713.TW", "00631L.TW", "3711.TW", "AVGO", "NVDA", "SGOV"]
    try: return yf.download(tickers, period="5y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
    except Exception: return pd.DataFrame()

def compute_dynamic_pure_stock_maint(twii_now, ma20, ma60):
    cost_anchor = 0.70 * max(1.0, ma20) + 0.30 * max(1.0, ma60)
    return round(max(115.0, min(195.0, 166.7 * ((twii_now / max(1.0, cost_anchor)) ** 1.95))), 1)

def fetch_and_analyze_fleet_fast():
    """
    【Layer 1 秒開主核心 (約 3～5 秒完成)】：
    三源搶最新交易日(根除盤前卡舊日) ＋ 真實大盤融資與期貨逆價差 ＋ 四軌落底偵測器 ＋ 14 檔個人資產即時報價
    """
    rt_quotes = fetch_mis_realtime_core16()
    fast_prices, twii_fmtqik, breadth = fetch_market_prices_fast()
    data = download_fleet_history()
    now_tw = get_tw_now()
    today_str, today_ymd = now_tw.strftime("%Y-%m-%d"), now_tw.strftime("%Y%m%d")
    is_open_today = (now_tw.weekday() < 5) and ((now_tw.hour * 60 + now_tw.minute) >= 540)
    vol_mult_now, vol_note = get_volume_multiplier()

    twii_now, twii_chg_pct = (twii_fmtqik["close"] or 36033.3), (twii_fmtqik["chg_pct"] if twii_fmtqik["close"] > 0 else 0.0)
    twii_open = twii_high = twii_low = twii_prev_c = twii_now
    ma5 = ma10 = ma20 = ma60 = ma120 = ma240 = m_ma20 = m_ma36 = twii_now
    twii_30d_high = twii_60d_high = twii_now
    twii_30d_dd_pct, twii_consec_above_ma10, twii_20d_ret = 0.0, 0, 0.0
    date_str, quote_src = (twii_fmtqik.get("date_iso") or today_str), "TWSE FMTQIK / Yahoo"
    fx_now, fx_prev, fx_chg_ntd, fx_ma5 = 32.85, 32.85, 0.0, 32.84
    sp500_below_ma200, sox_dd_pct, sox_ma240 = True, 35.0, 10200.0
    bottom_4track = {"passed": False, "track_hit": "未觸發", "detail": "待計算", "precursor": "", "vol_ratio": 1.0, "sh_ratio": 0.0}

    etf_metrics = {
        "00400A": {"p": 10.31, "ma10": 11.40, "ma20": 12.20, "exit_t1": 10.85, "exit_t2": 11.65, "div_latest": 0.06, "div_ttm": 0.60},
        "00985B": {"p": 10.39, "ma20": 10.35, "div_latest": 0.053, "div_ttm": 0.596},
        "00713": {"p": 58.00, "ma20": 59.50, "div_latest": 1.25, "div_ttm": 3.81},
        "00631L": {"p": 25.50, "ma5": 28.00, "ma20": 31.50},
        "3711": {"p": 482.0, "ma5": 485.0, "ma20": 505.0},
        "AVGO": {"p": 313.55}, "NVDA": {"p": 165.06}, "SGOV": {"p": 100.64}
    }

    try:
        if not data.empty and "^TWII" in data:
            df_twii = data["^TWII"].dropna(subset=["Open", "High", "Low", "Close"]).copy()
            if len(df_twii) >= 35:
                o_tw, c_tw, h_tw, l_tw, v_tw = df_twii["Open"].values.astype(float), df_twii["Close"].values.astype(float), df_twii["High"].values.astype(float), df_twii["Low"].values.astype(float), df_twii["Volume"].values.astype(float)
                last_k_date = df_twii.index[-1].strftime("%Y-%m-%d")
                rt_t00 = rt_quotes.get("t00", {})
                rt_iso = rt_t00.get("date_iso", "")
                fmt_iso = twii_fmtqik.get("date_iso", "")

                # 三源搶最新交易日：比較 yf 日K、證交所 FMTQIK 與 MIS/Yahoo v8 即時源，永遠取日期最新那筆！
                best_iso = last_k_date
                used_rt_today = False
                if is_open_today and rt_t00.get("close", 0) > 0 and rt_iso == today_str:
                    if last_k_date == today_str:
                        o_tw[-1] = rt_t00["open"] if rt_t00.get("open", 0) > 0 else o_tw[-1]
                        c_tw[-1], h_tw[-1], l_tw[-1] = rt_t00["close"], max(h_tw[-1], rt_t00["high"]), min(l_tw[-1], rt_t00["low"])
                    else:
                        o_tw, c_tw = np.append(o_tw, rt_t00.get("open") or rt_t00["close"]), np.append(c_tw, rt_t00["close"])
                        h_tw, l_tw, v_tw = np.append(h_tw, rt_t00["high"]), np.append(l_tw, rt_t00["low"]), np.append(v_tw, v_tw[-1])
                    best_iso, used_rt_today = today_str, True
                    date_str, quote_src = f"{today_str} {rt_t00.get('time', '')}", f"🟢 即時對時 ({rt_t00.get('time', '')})"
                else:
                    # 盤前／休市：若 FMTQIK 或 Yahoo v8 日期比 yf 歷史檔更新，立刻補上最新一日收盤！
                    if fmt_iso > last_k_date and twii_fmtqik["close"] > 0:
                        if rt_iso == fmt_iso and rt_t00.get("high", 0) > 0:
                            o_tw, h_tw, l_tw, c_tw = np.append(o_tw, rt_t00["open"]), np.append(h_tw, rt_t00["high"]), np.append(l_tw, rt_t00["low"]), np.append(c_tw, twii_fmtqik["close"])
                        else:
                            o_tw, h_tw, l_tw, c_tw = np.append(o_tw, twii_fmtqik["close"]), np.append(h_tw, twii_fmtqik["close"]), np.append(l_tw, twii_fmtqik["close"]), np.append(c_tw, twii_fmtqik["close"])
                        v_tw = np.append(v_tw, v_tw[-1])
                        best_iso = fmt_iso
                        date_str, quote_src = f"{fmt_iso} 官方收盤", f"📦 證交所 FMTQIK 最新完整收盤 ({fmt_iso})"
                    elif rt_iso > last_k_date and rt_t00.get("close", 0) > 0:
                        o_tw, h_tw, l_tw, c_tw, v_tw = np.append(o_tw, rt_t00["open"]), np.append(h_tw, rt_t00["high"]), np.append(l_tw, rt_t00["low"]), np.append(c_tw, rt_t00["close"]), np.append(v_tw, v_tw[-1])
                        best_iso = rt_iso
                        date_str, quote_src = f"{rt_iso} 收盤", f"📦 Yahoo v8 最新完整收盤 ({rt_iso})"
                    else:
                        date_str, quote_src = f"{last_k_date} 收盤", f"📦 完整收盤 ({last_k_date})"

                twii_open, twii_now, twii_high, twii_low = round(float(o_tw[-1]), 1), round(float(c_tw[-1]), 1), round(float(h_tw[-1]), 1), round(float(l_tw[-1]), 1)
                twii_prev_c = round(float(c_tw[-2]), 1) if len(c_tw) > 1 else twii_now
                twii_chg_pct = round(((twii_now - twii_prev_c) / max(1.0, twii_prev_c)) * 100.0, 2)
                twii_20d_ret = round(((twii_now - float(c_tw[-21])) / max(1.0, float(c_tw[-21]))) * 100.0, 2) if len(c_tw) >= 21 else 0.0

                ma5, ma10, ma20 = round(float(np.mean(c_tw[-5:])), 1), round(float(np.mean(c_tw[-10:])), 1), round(float(np.mean(c_tw[-20:])), 1)
                ma60 = round(float(np.mean(c_tw[-60:])), 1) if len(c_tw) >= 60 else ma20
                ma120 = round(float(np.mean(c_tw[-120:])), 1) if len(c_tw) >= 120 else ma60
                ma240 = round(float(np.mean(c_tw[-240:])), 1) if len(c_tw) >= 240 else ma120

                ma10_ser = pd.Series(c_tw).rolling(10).mean().values
                twii_consec_above_ma10 = 0
                for ii in range(len(c_tw) - 1, max(9, len(c_tw) - 15), -1):
                    if pd.notna(ma10_ser[ii]) and c_tw[ii] >= ma10_ser[ii]: twii_consec_above_ma10 += 1
                    else: break

                twii_30d_high = round(float(np.max(h_tw[-35:])), 1)
                twii_60d_high = round(float(np.max(h_tw[-65:])), 1) if len(h_tw) >= 65 else twii_30d_high
                twii_30d_dd_pct = round(((twii_30d_high - twii_now) / max(1.0, twii_30d_high)) * 100.0, 2)

                m_closes = df_twii["Close"].resample("ME").last().dropna().values.astype(float)
                if len(m_closes) > 0:
                    m_closes[-1] = twii_now
                    m_ma20 = round(float(np.mean(m_closes[-20:])), 1) if len(m_closes) >= 20 else round(ma240 * 0.88, 1)
                    m_ma36 = round(float(np.mean(m_closes[-36:])), 1) if len(m_closes) >= 36 else round(m_ma20 * 0.90, 1)

                bottom_4track = detect_4track_bottom_signals(o_tw, h_tw, l_tw, c_tw, v_tw, breadth["limit_up"], breadth["adv_ratio_pct"], vol_mult_now if used_rt_today else 1.0)

            if "^GSPC" in data:
                c_sp = data["^GSPC"]["Close"].dropna().values.astype(float)
                if len(c_sp) >= 200: sp500_below_ma200 = bool(float(c_sp[-1]) < float(np.mean(c_sp[-200:])))

            if "^SOX" in data:
                df_sx = data["^SOX"].dropna(subset=["High", "Close"])
                if len(df_sx) >= 60:
                    c_sx, h_sx = df_sx["Close"].values.astype(float), df_sx["High"].values.astype(float)
                    sox_hi = float(np.max(h_sx[-150:]))
                    sox_dd_pct = round(((sox_hi - float(c_sx[-1])) / max(1.0, sox_hi)) * 100.0, 2)
                    sox_ma240 = round(float(np.mean(c_sx[-240:])), 2) if len(c_sx) >= 240 else round(float(np.mean(c_sx)), 2)

            if "TWD=X" in data:
                c_fx = data["TWD=X"]["Close"].dropna().values.astype(float)
                if len(c_fx) >= 5:
                    if "USDTWD" in rt_quotes and rt_quotes["USDTWD"]["close"] > 10.0:
                        fx_now, fx_prev = round(float(rt_quotes["USDTWD"]["close"]), 3), round(float(rt_quotes["USDTWD"]["prev_close"]), 3)
                        c_fx[-1] = fx_now
                    else:
                        fx_now, fx_prev = round(float(c_fx[-1]), 3), round(float(c_fx[-2]), 3)
                    fx_chg_ntd, fx_ma5 = round(fx_now - fx_prev, 3), round(float(np.mean(c_fx[-5:])), 3)

            for sym_e, code_e in [("00400A.TW", "00400A"), ("00985B.TW", "00985B"), ("00713.TW", "00713"), ("00631L.TW", "00631L"), ("3711.TW", "3711"), ("AVGO", "AVGO"), ("NVDA", "NVDA"), ("SGOV", "SGOV")]:
                if sym_e in data:
                    df_e = data[sym_e].dropna(subset=["Close", "High", "Low"])
                    if len(df_e) >= 5:
                        c_e = df_e["Close"].values.astype(float)
                        rt_e = rt_quotes.get(code_e, {}).get("close", 0.0) if is_open_today else 0.0
                        if rt_e and rt_e > 0: c_e[-1] = float(rt_e)
                        elif code_e in fast_prices and fast_prices[code_e].get("close", 0) > 0: c_e[-1] = float(fast_prices[code_e]["close"])
                        p_e = round(float(c_e[-1]), 2)
                        etf_metrics[code_e]["p"] = p_e
                        if code_e == "00400A" and len(c_e) >= 20:
                            m10_e, m20_e = round(float(np.mean(c_e[-10:])), 2), round(float(np.mean(c_e[-20:])), 2)
                            t1_val = round(max(p_e * 1.04, min(m10_e, p_e * 1.10)), 2)
                            t2_val = round(max(t1_val + 0.40, min(m20_e, p_e * 1.18)), 2)
                            etf_metrics["00400A"].update({"ma10": m10_e, "ma20": m20_e, "exit_t1": t1_val, "exit_t2": t2_val})
                        elif code_e in ["00985B", "00713", "00631L", "3711"] and len(c_e) >= 20:
                            etf_metrics[code_e]["ma5"] = round(float(np.mean(c_e[-5:])), 2)
                            etf_metrics[code_e]["ma20"] = round(float(np.mean(c_e[-20:])), 2)
    except Exception: pass

    deriv = fetch_auto_derivatives_and_margin_v62(twii_now)
    deriv["stock_only_maint_pct"] = compute_dynamic_pure_stock_maint(twii_now, ma20, ma60)
    deriv["fx_now"], deriv["fx_prev"], deriv["fx_chg_ntd"], deriv["fx_ma5"] = fx_now, fx_prev, fx_chg_ntd, fx_ma5

    return {
        "twii_open": twii_open, "twii_prev_c": twii_prev_c, "twii_now": twii_now, "twii_chg_pct": twii_chg_pct,
        "twii_high": twii_high, "twii_low": twii_low, "twii_20d_ret": twii_20d_ret,
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60, "ma120": ma120, "ma240": ma240,
        "m_ma20": m_ma20, "m_ma36": m_ma36,
        "twii_30d_high": twii_30d_high, "twii_60d_high": twii_60d_high, "twii_30d_dd_pct": twii_30d_dd_pct,
        "twii_consec_above_ma10": twii_consec_above_ma10,
        "sp500_below_ma200": sp500_below_ma200, "sox_dd_pct": sox_dd_pct, "sox_ma240": sox_ma240,
        "breadth": breadth, "bottom_4track": bottom_4track,
        "date_str": date_str, "quote_src": quote_src, "vol_note": vol_note,
        "deriv": deriv, "etf_metrics": etf_metrics, "rt_quotes": rt_quotes, "fast_prices": fast_prices
    }

# ==========================================
# 第二層＆第三層（按需點擊才啟動）：完整法人/集保/當沖/EPS/K線掃描 ＆ 1,972 家月營收快篩
# ==========================================
ACTIVE_CB_BASE_SET = {
    "2301", "3017", "3324", "6187", "3131", "3583", "2383", "2368", "6274", "2345",
    "3533", "2357", "4938", "3231", "2382", "2355", "4976", "3701", "2009", "1527",
    "6112", "3147", "5013", "6446", "3691", "8069", "6751", "6877", "2453", "3264",
    "6285", "3558", "2449", "5269", "8016", "2395", "3008", "2327", "2059", "3653"
}

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_active_cb_codes():
    cb_codes = set(ACTIVE_CB_BASE_SET)
    for url in ["https://www.tpex.org.tw/openapi/v1/tpex_cb_daily_close_quotes", "https://www.tpex.org.tw/web/bond/tradeinfo/cb/CBPrice.php?l=zh-tw&o=json"]:
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5, verify=False)
            if r.status_code == 200:
                rows = r.json() if isinstance(r.json(), list) else r.json().get("aaData", [])
                for item in rows:
                    c_raw = str(item.get("BondCode", item.get("Code", item[0] if isinstance(item, list) and len(item) > 0 else ""))).strip()
                    if len(c_raw) in [5, 6] and c_raw[:4].isdigit(): cb_codes.add(c_raw[:4])
        except Exception: continue
    return cb_codes

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_day_trade_shares_batch() -> tuple[dict, str]:
    headers, dt_shares, now_tw = {"User-Agent": "Mozilla/5.0"}, {}, get_tw_now()
    dt_note = "(前日)" if ((now_tw.weekday() < 5) and (540 <= (now_tw.hour * 60 + now_tw.minute) < 960)) else ""
    try:
        r_tw = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/TWTB4U", headers=headers, timeout=5, verify=False)
        if r_tw.status_code == 200 and isinstance(r_tw.json(), list):
            for row in r_tw.json():
                c, v_raw = str(row.get("Code", row.get("證券代號", ""))).strip(), str(row.get("Volume", row.get("當日沖銷交易成交股數", "0"))).replace(",", "").strip()
                if len(c) == 4 and c.isdigit() and v_raw.replace(".", "").isdigit(): dt_shares[c] = float(v_raw)
    except Exception: pass
    try:
        r_otc = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_day_trading", headers=headers, timeout=5, verify=False)
        if r_otc.status_code == 200 and isinstance(r_otc.json(), list):
            for row in r_otc.json():
                c, v_raw = str(row.get("SecuritiesCompanyCode", row.get("Code", ""))).strip(), str(row.get("DayTradingVolume", row.get("Volume", "0"))).replace(",", "").strip()
                if len(c) == 4 and c.isdigit() and v_raw.replace(".", "").isdigit(): dt_shares[c] = float(v_raw)
    except Exception: pass
    return dt_shares, dt_note

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_official_3rates_and_cum_eps():
    headers, fin_dict = {"User-Agent": "Mozilla/5.0"}, {}
    def clean_num(val):
        try:
            s = str(val).replace(",", "").replace("%", "").replace("+", "").strip()
            return float(s) if s not in ["", "-", "--", "N/A", "None"] else np.nan
        except Exception: return np.nan
    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap17_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap17_O"]:
        try:
            r = requests.get(url, headers=headers, timeout=7, verify=False)
            if r.status_code == 200:
                for row in r.json():
                    c = str(row.get("公司代號", row.get("證券代號", ""))).strip()
                    if len(c) == 4 and c.isdigit():
                        fin_dict[c] = {"gm": clean_num(next((v for k, v in row.items() if "毛利率" in k), np.nan)), "om": clean_num(next((v for k, v in row.items() if "營業利益率" in k or "營益率" in k), np.nan)), "cum_eps": np.nan}
        except Exception: continue
    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci"]:
        try:
            r = requests.get(url, headers=headers, timeout=7, verify=False)
            if r.status_code == 200:
                for row in r.json():
                    c = str(row.get("公司代號", row.get("證券代號", ""))).strip()
                    if len(c) == 4 and c.isdigit():
                        eps_v = clean_num(next((v for k, v in row.items() if "基本每股盈餘" in k or "每股盈餘" in k), np.nan))
                        if c not in fin_dict: fin_dict[c] = {"gm": np.nan, "om": np.nan, "cum_eps": eps_v}
                        else: fin_dict[c]["cum_eps"] = eps_v
        except Exception: continue
    return fin_dict

@st.cache_data(ttl=600, show_spinner=False)
def fetch_full_market_enrichment():
    """僅於 Layer 2 (個股掃描) 或 Layer 3 (月營收) 按鍵啟動時才執行之完整法人、本益比、當沖與集保大戶 enrichment"""
    fast_p, _, _ = fetch_market_prices_fast()
    price_dict = {k: dict(v) for k, v in fast_p.items()}
    tdcc_dict, rev_dict = {}, {}
    headers, now_tw = {"User-Agent": "Mozilla/5.0"}, get_tw_now()

    try:
        dt_map, dt_note = fetch_day_trade_shares_batch()
        for code, dt_sh in dt_map.items():
            if code in price_dict and float(price_dict[code].get("vol_shares", 0)) > 0:
                ratio_dt = round((dt_sh / float(price_dict[code]["vol_shares"])) * 100.0, 1)
                if 0.0 <= ratio_dt <= 99.9: price_dict[code]["day_trade_str"] = f"{ratio_dt:.1f}%{dt_note}"
    except Exception: pass

    for d_back in range(0, 5):
        chk_dt = now_tw - datetime.timedelta(days=d_back)
        if chk_dt.weekday() >= 5: continue
        try:
            r_t86 = requests.get(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={chk_dt.strftime('%Y%m%d')}&selectType=ALLBUT0999&response=json", headers=headers, timeout=7, verify=False)
            if r_t86.status_code == 200 and len(r_t86.json().get("data", [])) > 100:
                for row in r_t86.json()["data"]:
                    c = str(row[0]).strip()
                    if c in price_dict and len(row) >= 12:
                        try:
                            price_dict[c]["fini_lots"] = int(round(float(str(row[4]).replace(",", "").strip()) / 1000.0))
                            price_dict[c]["sitc_lots"] = int(round(float(str(row[10]).replace(",", "").strip()) / 1000.0))
                        except Exception: pass
                break
        except Exception: continue

    for d_back in range(0, 5):
        chk_dt = now_tw - datetime.timedelta(days=d_back)
        if chk_dt.weekday() >= 5: continue
        roc_d = f"{chk_dt.year - 1911}/{chk_dt.month:02d}/{chk_dt.day:02d}"
        try:
            r_o = requests.get(f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d={roc_d}&o=json", headers=headers, timeout=7, verify=False)
            if r_o.status_code == 200 and len(r_o.json().get("aaData", [])) > 100:
                for row in r_o.json()["aaData"]:
                    c = str(row[0]).strip()
                    if c in price_dict and len(row) >= 14:
                        try:
                            f_sh = float(str(row[10]).replace(",", "").strip()) if len(row) >= 20 else float(str(row[4]).replace(",", "").strip())
                            s_sh = float(str(row[13]).replace(",", "").strip()) if len(row) >= 20 else float(str(row[7]).replace(",", "").strip())
                            price_dict[c]["fini_lots"], price_dict[c]["sitc_lots"] = int(round(f_sh / 1000.0)), int(round(s_sh / 1000.0))
                        except Exception: pass
                break
        except Exception: continue

    for pe_url, c_key, p_keys in [("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", "Code", ["PEratio"]), ("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", "SecuritiesCompanyCode", ["PriceEarningRatio", "PEratio"])]:
        try:
            r_pe = requests.get(pe_url, headers=headers, timeout=8, verify=False)
            if r_pe.status_code == 200:
                for row in r_pe.json():
                    c = str(row.get(c_key, row.get("Code", ""))).strip()
                    if c in price_dict:
                        for pk in p_keys:
                            ps = str(row.get(pk, "")).replace(",", "").strip()
                            if ps and ps not in ["-", "--", "N/A", "0", "0.00"]:
                                try: price_dict[c]["pe"] = round(float(ps), 1); break
                                except Exception: pass
        except Exception: pass

    try:
        r_csv = requests.get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", headers=headers, timeout=12, verify=False)
        if r_csv.status_code == 200 and len(r_csv.text) > 500:
            df_tdcc = pd.read_csv(io.StringIO(r_csv.text), dtype=str)
            df_tdcc.columns = [c.strip() for c in df_tdcc.columns]
            col_date, col_code, col_lvl, col_pct = next((c for c in df_tdcc.columns if "日期" in c), None), next((c for c in df_tdcc.columns if "代號" in c), None), next((c for c in df_tdcc.columns if "分級" in c), None), next((c for c in df_tdcc.columns if "比例" in c), None)
            if col_code and col_lvl and col_pct:
                cur_d = str(df_tdcc[col_date].iloc[0]).strip() if col_date else ""
                df_tdcc["code_clean"], df_tdcc["lvl_clean"] = df_tdcc[col_code].str.strip(), df_tdcc[col_lvl].str.strip()
                df_tdcc["pct_num"] = pd.to_numeric(df_tdcc[col_pct].str.replace(",", ""), errors="coerce").fillna(0.0)
                tdcc_dict = {str(k): round(float(v), 2) for k, v in df_tdcc[df_tdcc["lvl_clean"].isin(["12", "13", "14", "15"])].groupby("code_clean")["pct_num"].sum().to_dict().items() if 0.0 < float(v) <= 100.0}
                t1000_map = {str(k): round(float(v), 2) for k, v in df_tdcc[df_tdcc["lvl_clean"] == "15"].groupby("code_clean")["pct_num"].sum().to_dict().items() if 0.0 < float(v) <= 100.0}
                prev_snap = {}
                if os.path.exists(TDCC_PREV_CACHE_FILE):
                    try:
                        with open(TDCC_PREV_CACHE_FILE, "r", encoding="utf-8") as f: prev_snap = json.load(f)
                    except Exception: prev_snap = {}
                if cur_d and prev_snap.get("cur_date") and prev_snap.get("cur_date") != cur_d:
                    prev_snap["prev_date"], prev_snap["prev_400"], prev_snap["prev_1000"] = prev_snap.get("cur_date"), prev_snap.get("cur_400", {}), prev_snap.get("cur_1000", {})
                p400, p1000 = prev_snap.get("prev_400", {}), prev_snap.get("prev_1000", {})
                for ck, v400 in tdcc_dict.items():
                    if ck in price_dict:
                        v1000 = t1000_map.get(ck)
                        price_dict[ck]["tdcc_1000"] = v1000
                        if ck in p400: price_dict[ck]["tdcc_400_delta"] = round(v400 - float(p400[ck]), 2)
                        if v1000 is not None and ck in p1000: price_dict[ck]["tdcc_1000_delta"] = round(v1000 - float(p1000[ck]), 2)
                if cur_d and prev_snap.get("cur_date") != cur_d:
                    try:
                        with open(TDCC_PREV_CACHE_FILE, "w", encoding="utf-8") as f:
                            json.dump({"cur_date": cur_d, "cur_400": tdcc_dict, "cur_1000": t1000_map, "prev_date": prev_snap.get("prev_date", ""), "prev_400": p400, "prev_1000": p1000}, f)
                    except Exception: pass
    except Exception: pass

    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap05_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"]:
        try:
            r_rev = requests.get(url, headers=headers, timeout=10, verify=False)
            if r_rev.status_code == 200:
                for row in r_rev.json():
                    c = str(row.get("公司代號", row.get("證券代號", ""))).strip()
                    sec_s = str(row.get("產業別", "")).strip()
                    yoy_r = next((str(v).replace(",", "").strip() for k, v in row.items() if "同月增減" in k), None)
                    cum_r = next((str(v).replace(",", "").strip() for k, v in row.items() if "前期比較增減" in k), None)
                    if c and yoy_r not in [None, "", "-", "--", "N/A"]:
                        try: rev_dict[c] = round(float(yoy_r), 1)
                        except Exception: pass
                    if c in price_dict:
                        if sec_s and sec_s not in ["", "-", "None"]: price_dict[c]["sector"] = sec_s
                        if cum_r not in [None, "", "-", "--", "N/A"]:
                            try: price_dict[c]["cum_yoy"] = round(float(cum_r), 1)
                            except Exception: pass
        except Exception: continue
    return price_dict, tdcc_dict, rev_dict

MARGIN_DILUTION_SET = {"2382", "3231", "2317", "4938", "2356", "2324", "3706"}
MARGIN_EXPANSION_SET = {"2330", "3017", "3324", "3653", "2059", "2383", "2368", "6274", "2345", "2308", "3711", "6239", "6187", "3131", "3583", "5274", "2301", "3443", "3661", "2454", "6515", "6223", "1519", "1503", "1513", "6683", "7822", "3163"}

def compute_eps_valuation(code, curr_p, sector_str, single_yoy, cum_yoy, pe_official, support_price=None):
    if curr_p is None or pd.isna(curr_p) or curr_p <= 0: return np.nan, np.nan, np.nan, np.nan, "➖ 暫無報價"
    is_fin = ("金融" in sector_str) or (code in ["2881", "2882", "2891", "2886", "2884", "2885", "2892", "2880", "5880", "2890", "2883", "2887", "2801", "2834", "2812"])
    if is_fin: pe_low, pe_high = 12.0, 15.5
    elif any(k in sector_str for k in ["IC設計", "矽智財", "散熱", "BMC", "ASIC", "高速傳輸", "CoWoS", "探針", "重電", "矽光子"]) or code in ["2454", "3034", "2379", "3017", "3653", "2059", "5274", "3529", "3661", "3443", "6669", "3324", "5269", "6187", "3131", "6515", "6223", "1519", "6683", "3163", "4991", "7822"]: pe_low, pe_high = 20.0, 28.0
    elif any(k in sector_str for k in ["晶圓", "封測", "扇形封裝", "CCL", "PCB", "載板", "連接器", "鏡頭", "電源", "機電", "水資源"]) or code in ["2330", "3711", "6239", "2308", "3533", "2383", "2368", "3008", "3406", "2449", "3264", "6274", "3189", "8046", "1503", "1513", "1514", "8936", "6139"]: pe_low, pe_high = 16.0, 22.0
    else: pe_low, pe_high = 14.0, 18.5
    s_yoy = float(single_yoy) if (single_yoy is not None and pd.notna(single_yoy)) else 0.0
    c_yoy = float(cum_yoy) if (cum_yoy is not None and pd.notna(cum_yoy)) else s_yoy
    r6 = 0.60 * c_yoy + 0.40 * s_yoy
    M = 1.20 if r6 >= 35 else (1.14 if r6 >= 20 else (1.08 if r6 >= 10 else (1.02 if r6 >= 0 else (0.94 if r6 >= -10 else 0.88))))
    f_info = fetch_official_3rates_and_cum_eps().get(code, {})
    gm_v, om_v, cum_eps_api = f_info.get("gm", np.nan), f_info.get("om", np.nan), f_info.get("cum_eps", np.nan)
    if code in MARGIN_DILUTION_SET or (r6 > 10 and pd.notna(om_v) and om_v < 4.2 and not is_fin): T, tb = 0.88, "⚠️毛利稀釋T=0.88"
    elif code in MARGIN_EXPANSION_SET or (pd.notna(gm_v) and pd.notna(om_v) and gm_v >= 28 and om_v >= 15 and s_yoy >= c_yoy and s_yoy >= 15): T, tb = 1.12, "🔥三率三升T=1.12"
    else: T, tb = 1.00, "⚖️三率持平T=1.00"
    W = 0.78 if is_fin else 0.74
    cf = (W + (1.0 - W) * M * T) * 0.95
    tech_e = round(float(support_price), 2) if (support_price is not None and pd.notna(support_price) and support_price > 0) else round(curr_p * 0.96, 2)
    if pe_official is not None and pd.notna(pe_official) and 0.5 < float(pe_official) <= 65.0:
        pe_v = round(float(pe_official), 1)
        ttm = curr_p / pe_v
        base_e = (cum_eps_api / W) if (pd.notna(cum_eps_api) and cum_eps_api > 0.1 and (0.35 * ttm <= (cum_eps_api / W) <= 1.8 * ttm)) else ttm
        est_e = round(max(0.1, base_e * cf), 2)
        ep_lo, ep_hi = round(est_e * pe_low, 2), round(est_e * pe_high, 2)
        if curr_p <= ep_lo: vt = f"💎 低估鑽石 (空間+{round((ep_lo - curr_p)/max(1,curr_p)*100,1)}%) [{tb}]"
        elif curr_p <= ep_hi: vt = f"✅ 估值合理 (距目標+{round((ep_hi - curr_p)/max(1,curr_p)*100,1)}%) [{tb}]"
        else: vt = f"⚠️ 超過常態PE (+{round((curr_p - ep_hi)/max(1,ep_hi)*100,1)}%) [{tb}]"
        return est_e, pe_v, ep_lo, ep_hi, vt
    return np.nan, (round(float(pe_official), 1) if pd.notna(pe_official) else np.nan), tech_e, round(curr_p * 1.12, 2), f"🔄 轉機觀察 (守{tech_e}) [{tb}]"

def format_inst_chip_tag(f, s):
    fv, sv = int(f or 0), int(s or 0)
    if fv >= 50 and sv >= 20: return f"🔥土洋同買 (外{fv:+,}/投{sv:+,})"
    if sv >= 30: return f"🏦投信認養 (投{sv:+,}/外{fv:+,})"
    if fv >= 200: return f"🌍外資大買 (外{fv:+,}/投{sv:+,})"
    if fv > 0 or sv > 0: return f"📈法人偏多 (外{fv:+,}/投{sv:+,})"
    if fv == 0 and sv == 0: return "➖中性觀望 (0/0)"
    return f"🛡️法人調節 (外{fv:+,}/投{sv:+,})"

def classify_revenue_note_quality(note_txt, sector_str="", code_str=""):
    s = str(note_txt).strip()
    if code_str in POLICY_DEFENSE_ARMOR: return "🛡️ 政策兆元內需/公用工程護城河"
    if "建材營造" in sector_str or "營造" in sector_str or any(k in s for k in ["建案", "交屋", "房地", "不動產", "過戶", "營建", "售地"]): return "🏗️ 營建交屋認列(排除波段)"
    if not s or s in ["-", "無", "NA", "None", "nan"]: return "✅ 正常本業營運"
    if any(k in s for k in ["評價損益", "投資標的", "公允價值", "處分資產", "處分利益", "賠償", "和解", "補助"]): return "⚠️ 業外/評價認列(非本業)"
    if any(k in s for k in ["合併", "併購", "新納入子公司", "收購"]): return "🔶 併購納入營收(觀察續航)"
    if any(k in s for k in ["專案", "完工比例", "工程", "驗收", "一次性", "授權", "里程碑", "標案", "交貨驗收"]): return "⚠️ 專案/里程碑認列(防隔月回落)"
    if any(k in s for k in ["AI", "伺服器", "液冷", "散熱", "新產品", "產能", "量產", "訂單", "需求", "出貨", "旺季", "雲端", "拉貨"]): return "💎 真本業訂單/擴產爆發！"
    return "✅ 本業出貨成長"

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_single_stock_anti_trap(code: str, mkt: str = "TW") -> dict:
    if code in ODM_Q3_OFFICIAL_BASE:
        b = dict(ODM_Q3_OFFICIAL_BASE[code]); b["ok"] = True; return b
    out = {"ok": False, "inv_qoq": None, "ar_qoq": None, "ocf_positive": None, "note": "N/A (交由淘金客聯網查核)"}
    try:
        tkr = yf.Ticker(f"{code}.{'TW' if mkt == 'TW' else 'TWO'}")
        bs, cf = tkr.quarterly_balance_sheet, tkr.quarterly_cashflow
        inv_q, ar_q, ocf_p = None, None, None
        if bs is not None and not bs.empty and bs.shape[1] >= 2:
            ir = next((i for i in bs.index if "Inventory" in str(i) or "Inventories" in str(i)), None)
            if ir is not None and pd.notna(bs.loc[ir].iloc[0]) and pd.notna(bs.loc[ir].iloc[1]) and float(bs.loc[ir].iloc[1]) > 0:
                inv_q = round(((float(bs.loc[ir].iloc[0]) - float(bs.loc[ir].iloc[1])) / float(bs.loc[ir].iloc[1])) * 100.0, 1)
            ar = next((i for i in bs.index if "Accounts Receivable" in str(i) or "Receivables" in str(i)), None)
            if ar is not None and pd.notna(bs.loc[ar].iloc[0]) and pd.notna(bs.loc[ar].iloc[1]) and float(bs.loc[ar].iloc[1]) > 0:
                ar_q = round(((float(bs.loc[ar].iloc[0]) - float(bs.loc[ar].iloc[1])) / float(bs.loc[ar].iloc[1])) * 100.0, 1)
        if cf is not None and not cf.empty and cf.shape[1] >= 1:
            orw = next((i for i in cf.index if "Operating Cash Flow" in str(i) or "Cash Flow From Continuing Operating" in str(i)), None)
            if orw is not None and pd.notna(cf.loc[orw].iloc[0]): ocf_p = bool(float(cf.loc[orw].iloc[0]) > 0)
        if any(x is not None for x in [inv_q, ar_q, ocf_p]):
            out = {"ok": True, "inv_qoq": inv_q, "ar_qoq": ar_q, "ocf_positive": ocf_p, "note": f"存貨QoQ {inv_q:+.1f}% / 應收QoQ {ar_q if ar_q is not None else 'N/A'} / {'營業現金流為正(✅)' if ocf_p else ('營業現金流為負(⚠️)' if ocf_p is not None else 'OCF N/A')}" if inv_q is not None else f"應收QoQ {ar_q} / OCF {'正' if ocf_p else '負'}"}
    except Exception: pass
    return out

def evaluate_granary_and_anti_trap(code: str, single_yoy, macro_withdrawal_alert: bool = False):
    yoy_pos = (single_yoy is None) or (pd.notna(single_yoy) and float(single_yoy) > 0.0)
    if code in OLD_GRANARY_ODM_WATCH:
        h = ODM_Q3_OFFICIAL_BASE.get(code, {})
        if yoy_pos and (h.get("inv_qoq", 0) >= 19.0 or h.get("ar_qoq", 0) >= 20.0) and (not h.get("ocf_positive", True)):
            return f"🐀 警戒：疑似墊料壓貨 (-20分｜{h.get('note', '')})", -20, "🐀疑似墊料壓貨"
        if code == "6669" and h.get("ocf_positive", True): return f"🔄 雙棲：ASIC自研伺服器 (+8分｜{h.get('note', '')})", +8, "🔄ASIC自研雙棲"
        return f"⚠️ 舊穀倉ODM觀察 (-10分｜{h.get('note', '')})", -10, "⚠️舊穀倉ODM觀察"
    if code in GRANARY_1_CSP_COMMON:
        b = 23 if macro_withdrawal_alert else 15
        return f"🌟 新穀倉1：CSP自研/共通規格 (+{b}分・無墊料風險)", b, "🌟新穀倉1(共通規格)"
    if code in GRANARY_2_POWER_THERMAL:
        b = 14 if macro_withdrawal_alert else 10
        return f"⚡ 新穀倉2：缺電/過熱基建 (+{b}分・剛性升級)", b, "⚡新穀倉2(電力散熱)"
    if code in GRANARY_3_EDGE_MEMORY: return "📱 新穀倉3：邊緣AI與記憶體封測 (+5分・終端轉嫁)", 5, "📱新穀倉3(邊緣封測)"
    if code in POLICY_DEFENSE_ARMOR:
        b = 18 if macro_withdrawal_alert else 8
        return f"🛡️ 政策內需防彈衣 (+{b}分・兆元內需/外資提款避風港)", b, "🛡️政策內需防彈衣"
    return "➖ 一般電子/題材股 (+0分)", 0, "➖一般題材"

@st.cache_data(ttl=43200, show_spinner=False)
def fetch_tdcc_weekly_delta_ondemand(codes_tuple: tuple) -> dict:
    out, start_d = {}, (get_tw_now() - datetime.timedelta(days=28)).strftime("%Y-%m-%d")
    def _one(code):
        try:
            r = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockHoldingSharesPer&data_id={code}&start_date={start_d}", timeout=3.5, verify=False)
            if r.status_code == 200 and r.json().get("data"):
                df_t = pd.DataFrame(r.json()["data"])
                dates = sorted(df_t["date"].unique())
                if len(dates) >= 2:
                    lv400 = {"400,001-600,000", "600,001-800,000", "800,001-1,000,000", "more than 1,000,001", "12", "13", "14", "15"}
                    lv1000 = {"more than 1,000,001", "15"}
                    cs, ps = df_t[df_t["date"] == dates[-1]], df_t[df_t["date"] == dates[-2]]
                    p400_c, p400_p = float(cs[cs["HoldingSharesLevel"].astype(str).isin(lv400)]["percent"].sum()), float(ps[ps["HoldingSharesLevel"].astype(str).isin(lv400)]["percent"].sum())
                    p1000_c, p1000_p = float(cs[cs["HoldingSharesLevel"].astype(str).isin(lv1000)]["percent"].sum()), float(ps[ps["HoldingSharesLevel"].astype(str).isin(lv1000)]["percent"].sum())
                    if p400_c > 0 and p400_p > 0:
                        return code, {"p400": round(p400_c, 2), "d400": round(p400_c - p400_p, 2), "p1000": round(p1000_c, 2), "d1000": round(p1000_c - p1000_p, 2), "date": str(dates[-1])}
        except Exception: pass
        return code, None
    with ThreadPoolExecutor(max_workers=4) as ex:
        for fut in as_completed([ex.submit(_one, c) for c in list(codes_tuple)[:6]]):
            ck, v = fut.result()
            if v is not None: out[ck] = v
    return out

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_pair_swap_history(code_sell: str, mkt_sell: str, code_buy: str, mkt_buy: str, p_sell_live: float, p_buy_live: float) -> dict:
    curr_r = round(p_buy_live / max(0.01, p_sell_live), 3)
    res = {"ok": False, "curr_ratio": curr_r, "median_ratio": curr_r, "min_ratio": curr_r, "max_ratio": curr_r, "discount_pct": 0.0}
    try:
        ts, tb = f"{code_sell}.{'TW' if mkt_sell == 'TW' else 'TWO'}", f"{code_buy}.{'TW' if mkt_buy == 'TW' else 'TWO'}"
        df_p = yf.download([ts, tb], period="1y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
        if not df_p.empty and ts in df_p and tb in df_p:
            al = pd.concat([df_p[ts]["Close"].dropna().rename("s"), df_p[tb]["Close"].dropna().rename("b")], axis=1).dropna()
            if len(al) >= 20:
                rs = (al["b"] / al["s"].replace(0, np.nan)).dropna().values.astype(float)
                med_r = round(float(np.median(rs)), 3)
                res = {"ok": True, "curr_ratio": curr_r, "median_ratio": med_r, "min_ratio": round(float(np.min(rs)), 3), "max_ratio": round(float(np.max(rs)), 3), "discount_pct": round(((med_r - curr_r) / max(0.001, med_r)) * 100.0, 1)}
    except Exception: pass
    return res

@st.cache_data(ttl=600, show_spinner=False)
def download_historical_klines(yf_tickers_tuple):
    try: return yf.download(list(yf_tickers_tuple), period="1y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
    except Exception: return pd.DataFrame()

def scan_all_stocks_on_demand(twii_20d_ret: float, macro_withdrawal_alert: bool):
    """【Layer 2 按需點擊啟動】：完整掃描全台非殭屍強勢股、核心權值、穀倉與 MSCI 動態池之 5 組量化接軌指標"""
    price_dict, tdcc_dict, rev_dict = fetch_full_market_enrichment()
    msci_data = fetch_dynamic_msci_data(len(price_dict))
    cb_set = fetch_active_cb_codes()
    vol_mult, vol_note = get_volume_multiplier()
    now_tw = get_tw_now()
    today_str, today_ymd = now_tw.strftime("%Y-%m-%d"), now_tw.strftime("%Y%m%d")
    is_open = (now_tw.weekday() < 5) and ((now_tw.hour * 60 + now_tw.minute) >= 540)

    candidates = dict(CORE_WATCHLIST)
    msci_code_role = {}
    for role_lbl, lst_k in [("🌟MSCI標準新增", "std_add"), ("📈MSCI小型新增", "small_add"), ("📉MSCI標準刪除", "std_del"), ("⚠️MSCI小型刪除", "small_del")]:
        for c, nm, th, def_mkt in msci_data.get(lst_k, []):
            msci_code_role[c] = (role_lbl, th)
            if c not in candidates: candidates[c] = (price_dict.get(c, {}).get("name", nm), f"{role_lbl}・{th}", price_dict.get(c, {}).get("market", def_mkt))
    for th_name, bl in MSCI_THEME_BELLWETHERS.items():
        for bc, bnm in bl:
            if bc not in candidates: candidates[bc] = (price_dict.get(bc, {}).get("name", bnm), f"👑族群指標・{th_name}", price_dict.get(bc, {}).get("market", "TW"))

    for code, info in sorted([(c, i) for c, i in price_dict.items() if c not in candidates and len(c) == 4 and c.isdigit() and (c not in CONSTRUCTION_BLACKLIST) and ("營造" not in i.get("sector", "")) and (not is_zombie_stock_tiered(c, i["close"], i["vol_lots"] * vol_mult)) and i["chg_pct"] >= 2.0 and i["close"] >= 15.0], key=lambda x: x[1]["chg_pct"], reverse=True)[:70]:
        candidates[code] = (info["name"], info.get("sector") or "⚡短線技術破繭", info["market"])
    for code, info in sorted([(c, i) for c, i in price_dict.items() if c not in candidates and len(c) == 4 and c.isdigit() and (c not in CONSTRUCTION_BLACKLIST) and ("營造" not in i.get("sector", "")) and (not is_zombie_stock_tiered(c, i["close"], i["vol_lots"] * vol_mult)) and i["close"] >= 18.0 and (i.get("fini_lots", 0) >= 100 or i.get("sitc_lots", 0) >= 30) and rev_dict.get(c, -1.0) > 0.0], key=lambda x: x[1].get("sitc_lots", 0) * 3 + x[1].get("fini_lots", 0), reverse=True)[:30]:
        candidates[code] = (info["name"], info.get("sector") or "🏦法人鎖碼成長", info["market"])

    rt_quotes = fetch_mis_realtime_batch(tuple((c, m) for c, (_, _, m) in candidates.items()))
    yf_map = {f"{c}.{'TW' if m == 'TW' else 'TWO'}": c for c, (_, _, m) in candidates.items()}
    raw_df = download_historical_klines(tuple(yf_map.keys()))
    disp_map = msci_data.get("disposition_map", {})

    l_launch, l_pull, l_orbit, l_bc, l_turn, l_gran, l_msci, sec_recs, all_det = [], [], [], [], [], [], [], [], {}
    for yf_tkr, code in yf_map.items():
        try:
            if raw_df.empty or yf_tkr not in raw_df: continue
            df_s = raw_df[yf_tkr].dropna(subset=["Open", "High", "Low", "Close"]).copy()
            if len(df_s) < 35: continue
            o, c, h, l, v = df_s["Open"].values.astype(float), df_s["Close"].values.astype(float), df_s["High"].values.astype(float), df_s["Low"].values.astype(float), df_s["Volume"].values.astype(float) / 1000.0
            last_kd = df_s.index[-1].strftime("%Y-%m-%d")
            name, sector, mkt_code = candidates[code]

            rt = rt_quotes.get(code, {})
            if is_open and rt.get("close", 0) > 0 and rt.get("date") == today_ymd:
                if last_kd == today_str:
                    if rt.get("open", 0) > 0: o[-1] = rt["open"]
                    c[-1], h[-1], l[-1] = rt["close"], max(h[-1], rt["high"]), min(l[-1], rt["low"])
                    if rt["vol_lots"] > 0: v[-1] = float(rt["vol_lots"])
                else:
                    o, c, h, l = np.append(o, rt.get("open") or rt["close"]), np.append(c, rt["close"]), np.append(h, rt["high"]), np.append(l, rt["low"])
                    v = np.append(v, float(rt["vol_lots"]) if rt["vol_lots"] > 0 else v[-1])

            curr_p, prev_p = round(float(c[-1]), 2), float(c[-2])
            chg_1d = round((curr_p - prev_p) / max(0.01, prev_p) * 100, 2)
            if abs(chg_1d) < 0.01 and code in price_dict and abs(price_dict[code].get("chg_pct", 0.0)) > 0.01 and not is_open: chg_1d = price_dict[code]["chg_pct"]
            chg_3d = round((curr_p - float(c[-4])) / max(0.01, float(c[-4])) * 100, 2) if len(c) >= 4 else chg_1d
            est_vol = int(round(v[-1] * (vol_mult if is_open else 1.0)))
            yest_vol, ma5_vol = max(1.0, float(v[-2])), max(1.0, float(np.mean(v[-6:-1])))
            yod_ratio, vol_ratio = round(est_vol / yest_vol, 2), round(est_vol / ma5_vol, 2)
            if (code not in CORE_WATCHLIST) and (code not in msci_code_role) and is_zombie_stock_tiered(code, curr_p, est_vol, ma5_vol): continue

            p_meta = price_dict.get(code, {})
            sec_recs.append({"code": code, "name": name, "sector": sector, "official_sec": p_meta.get("sector") or sector, "close": curr_p, "chg_1d": chg_1d, "est_vol": est_vol, "yest_vol": yest_vol, "yod_ratio": yod_ratio})

            ma5, ma10, ma20 = round(float(np.mean(c[-5:])), 2), round(float(np.mean(c[-10:])), 2), round(float(np.mean(c[-20:])), 2)
            ma60 = round(float(np.mean(c[-60:])), 2) if len(c) >= 60 else ma20
            ma120 = round(float(np.mean(c[-120:])), 2) if len(c) >= 120 else ma60
            ma240 = round(float(np.mean(c[-240:])), 2) if len(c) >= 240 else ma120
            std20 = float(np.std(c[-20:]))
            bb_up, bb_bw = round(ma20 + 2.0 * std20, 2), (round((4.0 * std20 / ma20) * 100, 1) if ma20 > 0 else 99.0)

            sk, sd, sj, sjp, kd_cr = compute_kdj(h[-60:], l[-60:], c[-60:])
            pdi, mdi, adx, adx_p, dmi_b, atr_pct = compute_dmi_and_atr(h[-90:], l[-90:], c[-90:])
            dmi_tag, is_bear_acc = classify_dmi_direction(pdi, mdi, adx, sj)
            dmi_str = f"+DI:{pdi}/-DI:{mdi}/ADX:{adx} [{dmi_tag}]" if pd.notna(adx) else "N/A"
            atr_s = (f"{atr_pct:.1f}% ⚠️高波動(建議收盤確認/倉位減半)" if atr_pct > 4.5 else f"{atr_pct:.1f}%") if pd.notna(atr_pct) else "N/A"
            kd_dmi_res = (sk >= sd) and pd.notna(pdi) and (pdi > mdi) and (adx > adx_p) and (adx >= 18.0) and (kd_cr or dmi_b)

            bias10 = round(((curr_p - ma10) / ma10) * 100.0, 1) if ma10 > 0 else np.nan
            bias20 = round(((curr_p - ma20) / ma20) * 100.0, 1) if ma20 > 0 else np.nan
            b10_s = "N/A" if pd.isna(bias10) else (f"{bias10:+.1f}% 🚨極度過熱" if bias10 > 12.0 else (f"{bias10:+.1f}% ⚠️追高過熱" if bias10 > 10.0 else f"{bias10:+.1f}%"))
            b20_s = f"{bias20:+.1f}%" if pd.notna(bias20) else "N/A"

            drng = float(h[-1] - l[-1])
            up_sh_p = 0.0 if drng < 1e-6 else round(((float(h[-1]) - max(float(o[-1]), float(c[-1]))) / drng) * 100.0, 1)
            cp_p = (100.0 if chg_1d >= 0 else 0.0) if drng < 1e-6 else round(((float(c[-1]) - float(l[-1])) / drng) * 100.0, 1)
            cp_s = f"{cp_p:.0f}% (收破中軸)" if cp_p < 50.0 else f"{cp_p:.0f}%"
            dt_s = p_meta.get("day_trade_str", "N/A (交由淘金客聯網查核)")

            obv_t, _ = compute_obv_trend(c[-60:], v[-60:])
            if len(c) >= 21 and c[-21] > 0:
                s20r = ((curr_p - float(c[-21])) / float(c[-21])) * 100.0
                rs20 = round(s20r - twii_20d_ret, 1)
                rs20_s = f"{rs20:+.1f}% (逆勢抗跌)" if (twii_20d_ret < 0 and rs20 > 0 and (curr_p >= float(np.max(c[-20:])) * 0.985 or s20r > 0)) else f"{rs20:+.1f}%"
            else: rs20_s = "N/A"

            t400, t1000, d400, d1000 = tdcc_dict.get(code), p_meta.get("tdcc_1000"), p_meta.get("tdcc_400_delta"), p_meta.get("tdcc_1000_delta")
            tdcc_s = f"400張:{t400:.1f}%{f'({d400:+.2f}%)' if d400 is not None else ''}/千張:{f'{t1000:.1f}%' + (f'({d1000:+.2f}%)' if d1000 is not None else '') if t1000 is not None else 'N/A'}" if t400 is not None else "N/A"

            raw_ceil = max(bb_up, round(float(np.max(h[-21:-1])), 2))
            ceil_p = round(curr_p * 1.07, 2) if raw_ceil <= curr_p else round(raw_ceil, 2)
            iron_p = round(min(curr_p, max(ma10, float(l[-1])) if curr_p >= ma10 else min(ma20, float(l[-1]))), 2)
            stop_p = round(min(ma60, float(np.min(l[-20:]))), 2)

            yoy_v, cum_v, pe_o = rev_dict.get(code), p_meta.get("cum_yoy"), p_meta.get("pe", np.nan)
            fl, sl = p_meta.get("fini_lots", 0), p_meta.get("sitc_lots", 0)
            yoy_s, cum_s = (f"{yoy_v:+.1f}%" if yoy_v is not None else "待公告"), (f"{cum_v:+.1f}%" if cum_v is not None else "待公告")
            g_tag, g_bon, g_short = evaluate_granary_and_anti_trap(code, yoy_v, macro_withdrawal_alert)
            is_bc, is_fin, is_def = (code in CORE_WATCHLIST), (code in FINANCIAL_BLUE_CHIPS), (code in POLICY_DEFENSE_ARMOR)
            has_fund = (yoy_v is not None and yoy_v > 0) and (cum_v is None or cum_v > 0)
            st_tag = f"🐀疑似墊料壓貨({g_bon}分)" if "疑似墊料" in g_tag else (f"🛡️政策內需防彈衣(+{g_bon}分)" if is_def else (f"🔥有基之彈｜{g_short}" if has_fund else (f"🏦核心權值｜{g_short}" if is_bc else "🎭純技術炒作")))
            est_e, pe_d, ep_in, ep_tg, v_diag = compute_eps_valuation(code, curr_p, sector, yoy_v, cum_v, pe_o, iron_p)

            row_base = {
                "代碼": code, "股名": f"{name}{' 🚨[處置]' if code in disp_map else ''}{' 🔥[CB]' if code in cb_set else ''}{' ⚠️量縮' if is_zombie_stock_tiered(code, curr_p, est_vol, ma5_vol) else ''}",
                "族群": sector, "穀倉賽道與防墊料": g_tag, "現價": curr_p, "今日%": chg_1d, "3日%": chg_3d, "短線屬性": st_tag,
                "DMI方向雙檢": dmi_str, "日J值": sj, "BIAS10": b10_s, "BIAS20": b20_s,
                "5MA量比": vol_ratio, "上影線%": up_sh_p, "收盤位階": cp_s, "當沖率": dt_s,
                "OBV趨勢": obv_t, "RS20D%": rs20_s, "大戶400/千張": tdcc_s, "ATR%波動尺": atr_s,
                "法人動向": format_inst_chip_tag(fl, sl), "外資買賣(張)": fl, "投信買賣(張)": sl,
                "預估EPS(元)": est_e, "本益比(PE)": pe_d, "💎EPS合理切入價": ep_in, "🎯EPS目標價": ep_tg, "估值診斷": v_diag,
                "昨日量比": yod_ratio, "預估量(張)": est_vol, "天花板(反壓)": ceil_p, "月線承接": ma20, "鋼鐵防線": iron_p, "極限停損": stop_p,
                "單月營收YoY": yoy_s, "累計年營收YoY": cum_s
            }
            all_det[code] = {**row_base, "_raw_name": name, "_mkt": mkt_code, "_cb": ("🔥含CB可轉債" if code in cb_set else "➖"), "_ma5": ma5, "_ma10": ma10, "_ma20": ma20, "_ma60": ma60, "_ma120": ma120, "_ma240": ma240, "_pattern_tag": st_tag}

            if code in msci_code_role:
                mr, mt = msci_code_role[code]
                l_msci.append({"MSCI異動別": mr, "影響細分族群": mt, "處置與生效狀態": disp_map.get(code, "✅ 正常交易 (11/30 尾盤生效)"), **row_base})
            if code in (GRANARY_1_CSP_COMMON | GRANARY_2_POWER_THERMAL | GRANARY_3_EDGE_MEMORY | POLICY_DEFENSE_ARMOR | OLD_GRANARY_ODM_WATCH):
                l_gran.append({"穀倉戰略總分": max(30, min(115, 85 + g_bon + (4 if curr_p >= ma20 else -3) + (3 if (pd.notna(pdi) and pdi > mdi) else 0))), **row_base})

            heavy_dn = (chg_1d <= -2.0 and vol_ratio >= 1.25) or (chg_1d <= -4.2)
            above20 = (curr_p >= ma20) and (curr_p >= ma5) and (curr_p >= ma10) and (curr_p >= ma60 or ma20 >= ma60 * 0.94)
            brk20 = (curr_p >= ma20) and (curr_p >= ma5) and (curr_p >= ma10) and (prev_p < ma20) and (chg_1d >= 3.5) and (vol_ratio >= 1.25 or chg_1d >= 8.5) and pd.notna(pdi) and (pdi > mdi)
            if (above20 or brk20) and (not heavy_dn) and (not is_bear_acc) and ("疑似墊料" not in g_tag):
                overh = (chg_3d >= 14.0) or (pd.notna(bias10) and bias10 > 12.0) or (sj >= 93.0 and not kd_dmi_res and chg_1d < 8.5)
                if (brk20 and not overh) or ((chg_1d >= 2.0 and (vol_ratio >= 1.20 or chg_1d >= 7.5) and kd_dmi_res and not overh) or (chg_1d >= 2.8 and (vol_ratio >= 1.25 or chg_1d >= 7.5) and not overh and pd.notna(pdi) and (pdi > mdi) and adx >= 16.0)):
                    pat = f"👑 KD×DMI共振突破 [{g_short}]" if kd_dmi_res else (f"🔥 布林壓縮突破 [{g_short}]" if bb_bw <= 16.0 else f"🚀 站穩月線帶量突破 [{g_short}]")
                    all_det[code]["_pattern_tag"] = pat
                    l_launch.append({"實戰優先分": min(115, 92 + g_bon), "名將戰法診斷": pat, **row_base})
                elif (-3.5 <= chg_1d <= 2.2) and (chg_3d >= -6.5) and (vol_ratio <= 0.95) and (sj <= 68.0) and pd.notna(pdi) and (pdi >= mdi):
                    pat = f"⚡ N字量縮守月線 [{g_short}]"
                    all_det[code]["_pattern_tag"] = pat
                    l_pull.append({"蓄勢評分": min(115, 84 + int((1.0 - vol_ratio) * 15) + g_bon), "名將戰法診斷": pat, **row_base})
                elif curr_p >= ma5 >= ma10 >= ma20 and pd.notna(pdi) and (pdi > mdi) and chg_1d >= -2.0:
                    pat = f"🔥 3日急拉過熱 [{g_short}]" if (chg_3d >= 10.5 or (pd.notna(bias10) and bias10 > 10.0)) else f"🛡️ 沿5/10MA穩步推升 [{g_short}]"
                    all_det[code]["_pattern_tag"] = pat
                    l_orbit.append({"趨勢狀態": pat, **row_base})

            if is_bc and (not heavy_dn):
                bc_ok = has_fund or (yoy_v is not None and yoy_v >= 0) or (cum_v is not None and cum_v >= 0) or is_fin or is_def
                if bc_ok and (curr_p >= ma20) and pd.notna(pdi) and (pdi > mdi) and (not is_bear_acc) and (chg_3d < 9.5) and (sj <= 88.0):
                    l_bc.append({"波段總分": max(40, min(115, 85 + g_bon + (4 if has_fund else 0))), "長線格局定性": f"{g_short}＋{'🔥雙成長' if has_fund else '🏦穩健'}", **row_base})
                elif bc_ok and (curr_p < ma20) and (curr_p >= ma240 * 0.85):
                    dtg = f"🚨【{g_short}・空頭加速警戒】" if is_bear_acc else (f"🟢【{g_short}・月線下築底反彈】" if (curr_p >= ma5 and chg_1d >= 0) else f"⏳【{g_short}・股災超跌回測】")
                    l_turn.append({"波段總分": max(40, min(115, 82 + g_bon - (10 if is_bear_acc else 0))), "類型與轉機標註": dtg, **row_base})
        except Exception: continue

    return {
        "df_launch": pd.DataFrame(l_launch).sort_values(by="實戰優先分", ascending=False) if l_launch else pd.DataFrame(),
        "df_pullback": pd.DataFrame(l_pull).sort_values(by="蓄勢評分", ascending=False) if l_pull else pd.DataFrame(),
        "df_orbit": pd.DataFrame(l_orbit).sort_values(by="3日%", ascending=False) if l_orbit else pd.DataFrame(),
        "df_long_bc": pd.DataFrame(l_bc).sort_values(by="波段總分", ascending=False) if l_bc else pd.DataFrame(),
        "df_long_turn": pd.DataFrame(l_turn).sort_values(by=["波段總分", "今日%"], ascending=[False, False]) if l_turn else pd.DataFrame(),
        "df_granary": pd.DataFrame(l_gran).sort_values(by=["穀倉戰略總分", "今日%"], ascending=[False, False]) if l_gran else pd.DataFrame(),
        "df_msci": pd.DataFrame(l_msci) if l_msci else pd.DataFrame(),
        "sec_recs": sec_recs, "all_details": all_det, "msci_data": msci_data, "price_dict": price_dict, "vol_note": vol_note, "ts": now_tw.strftime("%H:%M:%S")
    }

@st.cache_data(ttl=300, show_spinner=False)
def fetch_all_monthly_revenue_df_on_demand():
    """【Layer 3 按需點擊啟動】：全市場 1,972 家上市櫃月營收動態撈取與三率估值快篩"""
    price_dict, tdcc_dict, _ = fetch_full_market_enrichment()
    headers, records_by_code, cb_set = {"User-Agent": "Mozilla/5.0"}, {}, fetch_active_cb_codes()
    def to_f(v):
        try:
            s = str(v).replace(",", "").replace("+", "").replace("%", "").strip()
            return float(s) if s not in ["", "-", "--", "N/A", "None", "不適用"] else np.nan
        except Exception: return np.nan
    def build_ttag(cr, yoy, mom, cumr, cumy, sec, note="", c=""):
        if (c not in POLICY_DEFENSE_ARMOR) and ("建材營造" in sec or "營造" in sec or any(k in str(note) for k in ["建案", "交屋", "房地", "營建", "售地"])): return "🏗️ 營建入帳認列(排除波段)"
        if pd.isna(yoy) or yoy < 20.0: return "➖ 一般波動"
        py = cumy
        if pd.notna(cr) and pd.notna(cumr) and cumr > cr and (100+yoy) > 0 and pd.notna(cumy) and (100+cumy) > 0:
            pl = (cumr/(1+cumy/100)) - (cr/(1+yoy/100))
            if pl > 0: py = (((cumr - cr) - pl) / pl) * 100.0
        if pd.notna(py) and py <= 0 and yoy >= 20 and (pd.isna(mom) or mom >= 0): return f"🌱 久旱逢甘霖 (前累計{py:+.1f}% ➔ 本月+{yoy:.1f}%)"
        if pd.notna(py) and py <= 8 and yoy >= 25 and (pd.isna(mom) or mom >= 5): return f"🚀 低迷翻揚轉機 (前累計+{py:.1f}% ➔ 本月+{yoy:.1f}%)"
        if yoy >= 20 and pd.notna(cumy) and cumy >= 20: return "🔥 長線強者恆強 (雙高成長)"
        return "📈 單月成長達標"
    for mkt_l, url in [("上市", "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"), ("上櫃", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O")]:
        try:
            r = requests.get(url, headers=headers, timeout=12, verify=False)
            if r.status_code == 200:
                for it in r.json():
                    c = str(it.get("公司代號", it.get("證券代號", ""))).strip()
                    if not (len(c) == 4 and c.isdigit()): continue
                    nm, ym, sec = str(it.get("公司名稱", "")).strip(), str(it.get("資料年月", "")).strip(), str(it.get("產業別", "")).strip()
                    cr, mom, yoy, cumr, cumy = to_f(it.get("營業收入-當月營收")), to_f(it.get("營業收入-上月比較增減(%)")), to_f(it.get("營業收入-去年同月增減(%)")), to_f(it.get("累計營業收入-當月累計營收")), to_f(it.get("累計營業收入-前期比較增減(%)"))
                    nt = str(it.get("備註", "")).strip()
                    if nt in ["-", "無", "NA", "None"]: nt = ""
                    pi = price_dict.get(c, {})
                    cp, vl, fl, sl = pi.get("close", np.nan), pi.get("vol_lots", 0), pi.get("fini_lots", 0), pi.get("sitc_lots", 0)
                    ee, pv, ei, _, vt = compute_eps_valuation(c, cp, sec, yoy, cumy, pi.get("pe", np.nan))
                    records_by_code[c] = {
                        "資料年月": ym, "市場": mkt_l, "代碼": c, "公司名稱": nm or pi.get("name", ""), "產業別": sec or "電子/其他",
                        "轉機與成長型態": build_ttag(cr, yoy, mom, cumr, cumy, sec, nt, c), "備註AI真偽鑑別": classify_revenue_note_quality(nt, sec, c),
                        "CB可轉債": "🔥含CB可轉債" if c in cb_set else "➖", "法人動向": format_inst_chip_tag(fl, sl), "外資買賣(張)": fl, "投信買賣(張)": sl,
                        "現價": cp, "今日%": pi.get("chg_pct", np.nan), "成交量(張)": vl, "預估EPS(元)": ee, "本益比(PE)": pv, "💎EPS合理切入價": ei, "估值診斷": vt,
                        "當月營收(億)": round(cr/1e5, 2) if pd.notna(cr) else np.nan, "月增MoM%": round(mom, 2) if pd.notna(mom) else np.nan,
                        "年增YoY%": round(yoy, 2) if pd.notna(yoy) else np.nan, "累計營收(億)": round(cumr/1e5, 2) if pd.notna(cumr) else np.nan,
                        "累計YoY%": round(cumy, 2) if pd.notna(cumy) else np.nan, "400張大戶%": f"{tdcc_dict[c]:.1f}%" if c in tdcc_dict else "未揭露",
                        "_tdcc_num": float(tdcc_dict.get(c, 0.0)), "財報營收備註說明": nt
                    }
        except Exception: continue
    return pd.DataFrame(list(records_by_code.values())).sort_values(by="年增YoY%", ascending=False, na_position="last").reset_index(drop=True) if records_by_code else pd.DataFrame()

@st.cache_data(ttl=1800, show_spinner=False)
def verify_24m_first_breakout_batch(codes_tuple, threshold_yoy=20.0):
    start_d, verified = (get_tw_now() - datetime.timedelta(days=1120)).strftime("%Y-%m-%d"), {}
    def _chk(code):
        try:
            r = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue&data_id={code}&start_date={start_d}", timeout=5, verify=False)
            if r.status_code == 200 and len(r.json().get("data", [])) >= 15:
                df_h = pd.DataFrame(r.json()["data"])
                df_h["ym"] = df_h["revenue_year"].astype(int) * 100 + df_h["revenue_month"].astype(int)
                df_h = df_h.sort_values("ym").reset_index(drop=True)
                rmap = dict(zip(df_h["ym"], df_h["revenue"].astype(float)))
                ys = [(f"{int(row['revenue_year'])-1911}{int(row['revenue_month']):02d}", round(((float(row["revenue"]) - rmap[(int(row['revenue_year'])-1)*100+int(row['revenue_month'])]) / rmap[(int(row['revenue_year'])-1)*100+int(row['revenue_month'])]) * 100.0, 1)) for _, row in df_h.iterrows() if ((int(row['revenue_year'])-1)*100+int(row['revenue_month'])) in rmap and rmap[(int(row['revenue_year'])-1)*100+int(row['revenue_month'])] > 0]
                if len(ys) >= 6:
                    lym, lyv = ys[-1]
                    prior = ys[:-1][-23:]
                    ms, lhit = 0, "無"
                    for p_ym, v in reversed(prior):
                        if v < threshold_yoy: ms += 1
                        else: lhit = p_ym; break
                    if ms >= len(prior) and len(prior) >= 12: return code, (True, 24, f"👑【2年首見破繭！】{lym}首破+{lyv:.1f}%")
                    if ms >= 6: return code, (True, ms, f"🌟【久違{ms}個月首破20%！】(前次{lhit}，本月+{lyv:.1f}%)")
                    return code, (False, ms, f"❌ 非首次 (僅隔{ms}個月)")
        except Exception: pass
        return code, None
    with ThreadPoolExecutor(max_workers=12) as ex:
        for fut in as_completed([ex.submit(_chk, c) for c in codes_tuple[:150]]):
            ck, vr = fut.result()
            if vr is not None: verified[ck] = vr
    return verified
    # ==========================================
# 【v62.0 下半部完整程式碼：三層按需秒開架構 ＋ 100%真浮動側邊欄 ＋ 四軌落底＆前哨 ＋ 淘金客v24.4雙模式戰情包】
# 直接貼在 verify_24m_first_breakout_batch() 的 return verified 正下方 (上半部維持 v62.0 不動！)
# ==========================================
import base64

def clean_html(raw_html: str) -> str:
    return "\n".join(line.strip() for line in raw_html.splitlines() if line.strip())


def xml_esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def pin_code_name_index(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df_c = df.copy()
    name_col = "股名" if "股名" in df_c.columns else ("公司名稱" if "公司名稱" in df_c.columns else None)
    if "代碼" in df_c.columns and name_col:
        return df_c.set_index(["代碼", name_col])
    return df_c


def prioritize_granary_and_armor_df(df: pd.DataFrame, macro_alert: bool, score_col: str) -> pd.DataFrame:
    if df.empty or "穀倉賽道與防墊料" not in df.columns:
        return df
    df_c = df.copy()
    if macro_alert:
        df_c["_priority_tier"] = df_c["穀倉賽道與防墊料"].apply(
            lambda s: 2 if ("新穀倉1" in str(s) or "政策內需防彈衣" in str(s)) else (1 if ("新穀倉2" in str(s) or "新穀倉3" in str(s)) else (-1 if "疑似墊料壓貨" in str(s) else 0))
        )
        sort_cols = ["_priority_tier"] + ([score_col] if score_col in df_c.columns else []) + (["今日%"] if "今日%" in df_c.columns else [])
        df_c = df_c.sort_values(by=sort_cols, ascending=[False] * len(sort_cols)).drop(columns=["_priority_tier"]).reset_index(drop=True)
    return df_c


def map_tw_theme_cluster(code: str, name: str, sector: str, official_sec: str) -> str:
    s_all = f"{sector} {official_sec} {name}"
    if code in ["1513", "1514", "8936", "9933", "6139", "1519", "1503"] or any(k in s_all for k in ["重電", "電網", "水資源", "無塵室", "統包工程"]):
        return "🛡️ 重電電網與兆元內需基建"
    if code in ["2454", "3034", "3443", "3661", "5274", "3529", "5269", "8016", "6515", "6223"] or any(k in s_all for k in ["IC設計", "矽智財", "ASIC", "BMC", "探針", "測試座"]):
        return "🌟 IC設計與CSP自研晶片測試"
    if code in ["3711", "6239", "2449", "3264", "6187", "3131", "3583", "6683", "7822", "8021"] or (("電腦及週邊" not in s_all) and any(k in s_all for k in ["封測", "扇形封裝", "CoWoS", "封裝", "檢測設備"])):
        return "🔬 先進封測與介面設備"
    if code in ["2330", "2303", "5347"] or "晶圓" in s_all:
        return "🏭 晶圓代工與半導體"
    if code in ["2383", "2368", "6274", "3533", "3189", "8046", "3037"] or any(k in s_all for k in ["CCL", "PCB", "載板", "連接器", "銅箔"]):
        return "⚡ ABF載板與高頻CCL/PCB"
    if code in ["3017", "3324", "3653", "2059"] or any(k in s_all for k in ["散熱", "均熱片", "水冷", "導軌"]):
        return "❄️ AI水冷散熱與伺服器機構"
    if code in ["2308", "2301", "3211"] or any(k in s_all for k in ["電源", "儲能", "BBU"]):
        return "🔋 雲端電源與BBU備援電池"
    if code in ["2317", "2382", "3231", "6669", "2357", "4938", "2356", "3706", "3693", "7711"] or any(k in s_all for k in ["AI伺服器", "AI PC", "電腦及週邊"]):
        return "🖥️ AI伺服器與ODM準系統"
    if code in ["2345", "2379", "3558", "6285", "3163", "4991", "3016", "3363", "3450", "6442"] or any(k in s_all for k in ["網通", "通信網路", "光通訊", "矽光子", "衛星"]):
        return "📡 高階網通與CPO矽光子"
    if any(k in s_all for k in ["金融", "金控", "銀行", "保險", "證券"]):
        return "🏦 金融官股與績優金控"
    if any(k in s_all for k in ["化學", "化工", "材料", "特化", "塑膠", "玻陶", "玻璃"]):
        return "🧪 特用化學與半導體材料"
    if any(k in s_all for k in ["生技", "醫療", "新藥", "製藥"]):
        return "🧬 生技醫療與新藥研發"
    if code in ["3008", "3406"] or any(k in s_all for k in ["光電", "光學", "鏡頭", "面板"]):
        return "📷 光學鏡頭與邊緣AI感測"
    return "🚀 電子零組件與中小型破繭股"


def compute_tw_hot_sectors(records: list[dict]) -> dict:
    if not records:
        return {"hot_html": "", "cold_html": "", "hot_text": "尚未啟動個股掃描", "cold_text": "", "top1_name": "待啟動掃描"}

    df_r = pd.DataFrame(records)
    df_r["theme"] = df_r.apply(lambda r: map_tw_theme_cluster(str(r["code"]), str(r["name"]), str(r["sector"]), str(r["official_sec"])), axis=1)

    theme_rows = []
    for theme_name, grp in df_r.groupby("theme"):
        if len(grp) == 0: continue
        tot_est_v, tot_yest_v = float(grp["est_vol"].sum()), max(1.0, float(grp["yest_vol"].sum()))
        blended_yod = round(0.55 * (tot_est_v / tot_yest_v) + 0.45 * float(grp["yod_ratio"].clip(upper=5.0).mean()), 2)
        avg_chg, max_chg = round(float(grp["chg_1d"].mean()), 2), round(float(grp["chg_1d"].max()), 2)
        heat_score = avg_chg * 2.2 + max_chg * 0.6 + (blended_yod - 1.0) * 6.5

        grp_sorted = grp.sort_values(by=["chg_1d", "yod_ratio"], ascending=[False, False])
        leader_html, leader_txt = [], []
        for _, st_row in grp_sorted.head(3).iterrows():
            c_chg, c_yod = float(st_row["chg_1d"]), float(st_row["yod_ratio"])
            c_col = "#ff8a80" if c_chg >= 0 else "#69f0ae"
            leader_html.append(f"<b style='color:#fff;'>{st_row['name']}({st_row['code']})</b> <b style='color:{c_col};'>{c_chg:+.2f}%</b><span style='color:#ffd54f; font-size:12.5px;'>[昨量{c_yod:.1f}x]</span>")
            leader_txt.append(f"{st_row['name']}({st_row['code']}){c_chg:+.2f}%")
        theme_rows.append({"theme": theme_name, "avg_chg": avg_chg, "blended_yod": blended_yod, "heat_score": heat_score, "leaders_html": "、".join(leader_html), "leaders_txt": "、".join(leader_txt)})

    if not theme_rows:
        return {"hot_html": "", "cold_html": "", "hot_text": "暫無數據", "cold_text": "", "top1_name": "防禦與主流族群"}

    df_th = pd.DataFrame(theme_rows).sort_values(by="heat_score", ascending=False).reset_index(drop=True)
    top_hot, bot_cold = df_th.head(4), df_th.tail(2).sort_values(by="avg_chg", ascending=True)
    hot_divs, hot_txts, cold_divs, cold_txts = [], [], [], []

    for _, r_th in top_hot.iterrows():
        v_col = "#ffd54f" if r_th["blended_yod"] >= 1.15 else "#93c5fd"
        hot_divs.append(
            f"<div style='margin:5px 0; padding:6px 10px; background:rgba(0,0,0,0.3); border-radius:6px; border-left:3px solid #ff8a80; font-size:14.2px; line-height:1.55;'>"
            f"<span style='background:rgba(255,77,79,0.22); color:#ff8a80; padding:1px 6px; border-radius:4px; font-weight:900; margin-right:6px;'>🔥 抗跌/領漲</span>"
            f"<b style='color:#ffd54f;'>{r_th['theme']}</b> (<b style='color:#ff8a80;'>均 {r_th['avg_chg']:+.2f}%</b> ｜ <b style='color:{v_col};'>昨量 {r_th['blended_yod']:.2f}x</b>)<br>"
            f"&nbsp;&nbsp;↳ {r_th['leaders_html']}</div>"
        )
        hot_txts.append(f"{r_th['theme']}(均{r_th['avg_chg']:+.2f}%/量{r_th['blended_yod']:.2f}x:{r_th['leaders_txt']})")

    for _, r_th in bot_cold.iterrows():
        cold_divs.append(
            f"<div style='margin:5px 0; padding:6px 10px; background:rgba(0,0,0,0.3); border-radius:6px; border-left:3px solid #69f0ae; font-size:14.2px; line-height:1.55;'>"
            f"<span style='background:rgba(0,230,118,0.18); color:#69f0ae; padding:1px 6px; border-radius:4px; font-weight:900; margin-right:6px;'>❄️ 重災/調節</span>"
            f"<b style='color:#e2e8f0;'>{r_th['theme']}</b> (<b>均 {r_th['avg_chg']:+.2f}%</b> ｜ 昨量 {r_th['blended_yod']:.2f}x)<br>"
            f"&nbsp;&nbsp;↳ {r_th['leaders_html']}</div>"
        )
        cold_txts.append(f"{r_th['theme']}(均{r_th['avg_chg']:+.2f}%:{r_th['leaders_txt']})")

    return {"hot_html": "".join(hot_divs), "cold_html": "".join(cold_divs), "hot_text": " ； ".join(hot_txts), "cold_text": " ； ".join(cold_txts), "top1_name": "、".join(top_hot["theme"].head(2).tolist())}


def format_sector_bold_html(sec_list: list[str], is_up: bool = True) -> str:
    if not sec_list: return ""
    badge_bg = "rgba(255, 77, 79, 0.22)" if is_up else "rgba(0, 230, 118, 0.18)"
    badge_col = "#ff8a80" if is_up else "#69f0ae"
    icon = "🔥 美股抗跌/領漲" if is_up else "❄️ 美股重挫/領跌"
    out_rows = []
    for s in sec_list:
        s_bold = re.sub(r"([\u4e00-\u9fa5A-Za-z]+\([A-Z]+\))", r"<b style='color:#ffffff;'>\1</b>", s)
        s_bold = s_bold.replace("指標股：", "<b style='color:#ffd54f;'>指標股：</b>")
        out_rows.append(
            f"<div style='margin:5px 0; padding:6px 10px; background:rgba(0,0,0,0.3); border-radius:6px; border-left:3px solid {badge_col}; font-size:14.2px; line-height:1.55;'>"
            f"<span style='background:{badge_bg}; color:{badge_col}; padding:1px 6px; border-radius:4px; font-weight:900; margin-right:6px;'>{icon}</span>{s_bold}</div>"
        )
    return "".join(out_rows)


def build_event_timeline_svg_html(upcoming_rows: list[dict]) -> str:
    evs = upcoming_rows[:6]
    if not evs: return ""
    n = len(evs)
    xs = [100 + i * int(1000 / max(1, n - 1)) for i in range(n)] if n > 1 else [600]
    nodes_svg = []
    for i, ev in enumerate(evs):
        cx, d_left = xs[i], ev["_days"]
        col = "#ff4d4f" if d_left <= 3 else ("#facc15" if d_left <= 7 else "#38bdf8")
        bg_box = "#2d1216" if d_left <= 3 else ("#2e2409" if d_left <= 7 else "#122136")
        d_badge = "今日登場" if d_left == 0 else f"倒數 {d_left} 天"
        short_lbl, date_mmdd = xml_esc(ev["_short"][:11]), xml_esc(ev["日期"][5:])
        box_y, line_y1, line_y2 = (16, 76, 102) if i % 2 == 0 else (132, 114, 132)
        nodes_svg.append(
            f'<line x1="{cx}" y1="{line_y1}" x2="{cx}" y2="{line_y2}" stroke="{col}" stroke-width="2" stroke-dasharray="3,2"/>'
            f'<circle cx="{cx}" cy="108" r="9" fill="{col}" stroke="#0f172a" stroke-width="3"/>'
            f'<rect x="{cx - 86}" y="{box_y}" width="172" height="60" rx="8" fill="{bg_box}" stroke="{col}" stroke-width="1.8"/>'
            f'<text x="{cx}" y="{box_y + 20}" fill="{col}" font-family="sans-serif" font-size="12.5" font-weight="900" text-anchor="middle">{date_mmdd} ｜ {d_badge}</text>'
            f'<text x="{cx}" y="{box_y + 44}" fill="#f8fafc" font-family="sans-serif" font-size="13.5" font-weight="800" text-anchor="middle">{short_lbl}</text>'
        )
    svg_xml = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 208" width="100%" height="auto">
<rect width="1200" height="208" rx="10" fill="#0b1220" stroke="#334155" stroke-width="1.5"/>
<line x1="60" y1="108" x2="1140" y2="108" stroke="#475569" stroke-width="4" stroke-linecap="round"/>
<polygon points="1140,101 1158,108 1140,115" fill="#64748b"/>
{''.join(nodes_svg)}
</svg>"""
    b64_svg = base64.b64encode(svg_xml.encode("utf-8")).decode("utf-8")
    return f'<img src="data:image/svg+xml;base64,{b64_svg}" style="width:100%; height:auto; display:block; border-radius:10px; margin:6px 0 10px 0;" />'


def evaluate_crash_and_bottom_radar_v244(
    twii_now, twii_chg_pct, twii_ma60, twii_ma120, twii_ma240, twii_30d_dd_pct, sp500_below_ma200,
    vix_now, sox_dd_pct, fini_oi, margin_dd_pct, margin_2d_chg_yi, margin_2d_pct, maint_pct,
    bottom_4track: dict, limit_up_cnt: int, adv_ratio_pct: float,
    basis_pts, basis_stabilized, consec_cover_days, cover_3d_sum,
    fx_stopped_dep, limit_down_cnt, had_limit_down_100,
    ban_active, days_since_ban, consec_above_ma10
) -> dict:
    """【模組 0（對齊淘金客 v24.4）：三階燈號 × 四軌落底反轉(含2025/04無量鎖漲停豁免) × 底部前哨 × 政策逃命波】"""
    fini_tag = f"{fini_oi:+,} 口 【🚨極限對沖】" if fini_oi < -80000 else f"{fini_oi:+,} 口"
    w_items = [
        (twii_30d_dd_pct >= 12.0, f"① 30日急殺跌速 ≧ 12%（目前回檔 <b>-{twii_30d_dd_pct:.1f}%</b>）"),
        ((twii_now < twii_ma240) or sp500_below_ma200, f"② 長線生命線失守（加權 {'<' if twii_now < twii_ma240 else '>='} 年線{twii_ma240:,.0f} ｜ 標普 {'< 200MA' if sp500_below_ma200 else '>= 200MA'}）"),
        ((vix_now >= 30.0) or (sox_dd_pct >= 25.0), f"③ 美股恐慌爆表（VIX <b>{vix_now:.2f}</b> ｜ 費半波段跌幅 <b>-{sox_dd_pct:.1f}%</b>）"),
        (fini_oi < -60000, f"④ 外資期貨重空 &gt; 6萬口（目前淨空單 <b>{fini_tag}</b>）"),
        (margin_dd_pct < twii_30d_dd_pct, f"⑤ 融資減幅尚未追上大盤跌幅（融資減 <b>-{margin_dd_pct:.1f}%</b> vs 大盤跌 <b>-{twii_30d_dd_pct:.1f}%</b>）")
    ]
    w_count = sum(1 for flag, _ in w_items if flag)
    crash_warning_on = (w_count >= 3)

    basis_rate_pct = round((abs(basis_pts) / max(1.0, twii_now)) * 100.0, 2) if basis_pts < 0 else 0.0
    b1_ok = (margin_dd_pct >= twii_30d_dd_pct) or (margin_2d_chg_yi <= -500.0) or (margin_2d_pct <= -7.0) or (maint_pct <= 150.0)

    # 落底第 2 項：整合四軌偵測器 ＋ 側邊欄漲停家數覆寫連動 2c 條款
    hit_2c_override = (twii_chg_pct >= 4.0) and ((limit_up_cnt >= 100) or (adv_ratio_pct >= 85.0))
    b2_ok = bool(bottom_4track.get("passed", False) or hit_2c_override)
    b2_detail = bottom_4track.get("detail", "")
    if hit_2c_override and not bottom_4track.get("passed", False):
        b2_detail = f"2c無量鎖漲停/廣度推力豁免(漲+{twii_chg_pct:.1f}%・漲停{limit_up_cnt}家/上漲比{adv_ratio_pct:.0f}%)"
    precursor_txt = bottom_4track.get("precursor", "")

    b3_ok = (basis_pts < 0) and (basis_rate_pct >= 0.8) and basis_stabilized
    b4_ok = (consec_cover_days >= 3) and (cover_3d_sum >= 10000) and fx_stopped_dep

    b_items = [
        (b1_ok, f"① 融資真斷頭（近2日 <b>{margin_2d_chg_yi:+.1f}億 / {margin_2d_pct:+.1f}%</b> ｜ 維持率 <b>{maint_pct:.1f}%</b>）"),
        (b2_ok, f"② 四軌價量／廣度反轉（<b>{b2_detail}</b>）"),
        (b3_ok, f"③ 恐慌逆價差率 ≧ 0.8% 收斂（逆價差 <b>{basis_pts:+.0f}點 / {basis_rate_pct:.2f}%</b> ｜ {'已止穩不擴大' if basis_stabilized else '未確認止穩'}）"),
        (b4_ok, f"④ 外資空單連3日回補&gt;1萬口＋台幣止貶（連補 <b>{consec_cover_days}日 / {cover_3d_sum:+,}口</b> ｜ 台幣{'已止貶✅' if fx_stopped_dep else '續貶⚠️'}）"),
        (had_limit_down_100 and (limit_down_cnt < 20), f"⑤ 恐慌廣度收斂（當日跌停 <b>{limit_down_cnt} 家</b> {'< 20家達標' if (had_limit_down_100 and limit_down_cnt < 20) else '尚未收斂至<20家'}）")
    ]
    b_count = sum(1 for flag, _ in b_items if flag)
    bottom_confirmed = (b_count >= 4)

    is_policy_escape_wave = bool(ban_active and (0 <= days_since_ban <= 14) and (not bottom_confirmed) and (consec_above_ma10 < 3))

    if crash_warning_on and (not bottom_confirmed):
        master_state, lamp_short = "RED_LOCKED", "🔴紅燈鎖死"
        master_badge = f"🔴 紅燈未落底【總開關鎖死】（預警 {w_count}/5・落底 {b_count}/5）"
        master_action = "嚴禁動用新現金追買或開槓桿！僅核准三項豁免：(a) 股轉債轉入00985B/00713；(b) 比價折價>=10~15%換龍頭；(c) 淨降倉換強新穀倉1龍頭(獨立分帳先守平衡價，站上5MA後守5MA)。"
    elif crash_warning_on and bottom_confirmed:
        master_state, lamp_short = "BOTTOM_UNLOCKED", "🟢落底解鎖"
        master_badge = f"🟢 落底確認【總開關解鎖】（預警 {w_count}/5 ＆ 落底共振 {b_count}/5 達標！）"
        master_action = "正式解封新資金！開放水庫四/機動現金分 3 批加碼新穀倉1龍頭權值股作傳家寶存股，同步啟動 MSCI 主流觀察池！"
    elif (twii_now < twii_ma60) or (twii_now < twii_ma120):
        master_state, lamp_short = "YELLOW", "🟡黃燈警戒"
        master_badge = f"🟡 黃燈前哨警戒（觸及季線/半年線・預警 {w_count}/5）"
        master_action = "拉高審核門檻禁止追高！優先收網多頭股息衛星股轉現或轉債，僅限防彈衣／新穀倉1且無3日事件雷者小額試單。"
    else:
        master_state, lamp_short = "GREEN", "🟢綠燈常態"
        master_badge = f"🟢 綠燈多頭常態（站穩季半年線・預警 {w_count}/5）"
        master_action = "正常審核 App 個股訊號，啟動多頭股息衛星循環（嚴守破 10/20MA 或硬停損 -7%~-10% 無條件撤退）。"

    return {
        "w_items": w_items, "w_count": w_count, "crash_warning_on": crash_warning_on,
        "b_items": b_items, "b_count": b_count, "bottom_confirmed": bottom_confirmed,
        "b2_ok": b2_ok, "b2_detail": b2_detail, "precursor_txt": precursor_txt,
        "basis_rate_pct": basis_rate_pct, "is_policy_escape_wave": is_policy_escape_wave,
        "master_state": master_state, "lamp_short": lamp_short,
        "master_badge": master_badge, "master_action": master_action
    }


def build_dynamic_crash_cycle_svg_html(fleet: dict, radar: dict, etf_m: dict) -> str:
    twii_now, hi_60d = fleet["twii_now"], fleet["twii_60d_high"]
    m_ma20, m_ma36, ma20, ma240 = fleet["m_ma20"], fleet["m_ma36"], fleet["ma20"], fleet["ma240"]

    l1_low, l1_high = round(m_ma20 * 0.98, 0), round(m_ma20 * 1.02, 0)
    fib382_reb = round(m_ma20 + (hi_60d - m_ma20) * 0.382, 0)
    r1_low = round(min(ma240, fib382_reb, ma20) * 0.985, 0)
    r1_high = round(max(min(ma240, ma20), fib382_reb), 0)
    l2_low, l2_high = round(m_ma20 * 0.985, 0), round(m_ma20 * 1.035, 0)

    ext_a, ext_b = round(m_ma36, 0), round(m_ma20 * 0.90, 0)
    ext_lo, ext_hi = (round(m_ma36 * 0.98, 0), round(m_ma36 * 1.02, 0)) if abs(ext_a - ext_b) < 250 else (min(ext_a, ext_b), max(ext_a, ext_b))

    if radar["master_state"] == "RED_LOCKED":
        denom = max(100.0, hi_60d - m_ma20)
        prog = float(np.clip((hi_60d - twii_now) / denom, 0.12, 0.92))
        curr_x = int(round(90 + (335 - 90) * prog))
        curr_y = int(round(118 + (268 - 118) * prog))
        badge_x, badge_y = min(430, curr_x + 105), max(128, curr_y - 38)
        dot_col = "#ff4d4f"
        stage_desc = f"📍 目前位於【階段三：恐慌斷頭速殺／築底測試段】（現價 {twii_now:,.0f}點 ｜ 距月20MA {m_ma20:,.0f}點尚餘 {twii_now - m_ma20:+,.0f}點）"
    elif radar["master_state"] == "BOTTOM_UNLOCKED":
        curr_x, curr_y, badge_x, badge_y, dot_col = 695, 258, 695, 200, "#00e676"
        stage_desc = f"📍 目前位於【階段四：落底共振解鎖・W底右腳建倉區】（現價 {twii_now:,.0f}點・開放分 3 批布局）"
    elif radar["master_state"] == "YELLOW":
        curr_x, curr_y, badge_x, badge_y, dot_col = 515, 192, 515, 142, "#fbbf24"
        stage_desc = f"📍 目前位於【🟡 黃燈警戒震盪區】（現價 {twii_now:,.0f}點・季半年線前哨，收網衛星禁追高）"
    else:
        curr_x, curr_y, badge_x, badge_y, dot_col = 960, 172, 960, 224, "#00e676"
        stage_desc = f"📍 目前位於【🟢 綠燈多頭修復主升段】（現價 {twii_now:,.0f}點・常態順勢操作）"

    exit_t1, exit_t2 = etf_m["00400A"]["exit_t1"], etf_m["00400A"]["exit_t2"]
    policy_warn = "<br>• <span style='color:#ff8a80;'>⚠️ <b>政策逃命波警示</b>：限空令/護盤後 10 交易日內，落底未達 4/5 且未連 3 日站穩 10MA，急彈一律定性為「政策逃命波」！</span>" if radar["is_policy_escape_wave"] else ""
    precursor_banner = f"<br>• <span style='color:#fde047;'>{radar['precursor_txt']}</span>" if radar.get("precursor_txt") else ""

    svg_xml = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 448" width="100%" height="auto">
<rect width="1200" height="448" rx="12" fill="#09111a" stroke="#334155" stroke-width="2"/>
<rect x="25" y="102" width="1150" height="206" rx="8" fill="#0f172a" stroke="#1e293b" stroke-width="1"/>
<line x1="25" y1="150" x2="1175" y2="150" stroke="#1e293b" stroke-width="1" stroke-dasharray="4,4"/>
<line x1="25" y1="208" x2="1175" y2="208" stroke="#1e293b" stroke-width="1" stroke-dasharray="4,4"/>
<line x1="25" y1="268" x2="1175" y2="268" stroke="#00e676" stroke-width="2" stroke-dasharray="8,5"/>
<text x="1165" y="262" fill="#00e676" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="end">動態月20MA支撐線：{m_ma20:,.0f}點</text>
<line x1="25" y1="192" x2="1175" y2="192" stroke="#f59e0b" stroke-width="1.8" stroke-dasharray="6,5"/>
<text x="1165" y="186" fill="#fcd34d" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="end">反彈逃命/減碼反壓帶：{r1_low:,.0f}～{r1_high:,.0f}點</text>
<text x="90" y="302" fill="#94a3b8" font-family="sans-serif" font-size="11.5" font-weight="bold" text-anchor="middle">10月初高點</text>
<text x="335" y="302" fill="#ff8a80" font-family="sans-serif" font-size="11.5" font-weight="bold" text-anchor="middle">11月底測第一隻腳</text>
<text x="515" y="302" fill="#fcd34d" font-family="sans-serif" font-size="11.5" font-weight="bold" text-anchor="middle">12月初政策逃命彈</text>
<text x="695" y="302" fill="#38bdf8" font-family="sans-serif" font-size="11.5" font-weight="bold" text-anchor="middle">12月中W底右腳</text>
<text x="960" y="302" fill="#6ee7b7" font-family="sans-serif" font-size="11.5" font-weight="bold" text-anchor="middle">2027 Q1築底修復</text>
<line x1="90" y1="86" x2="90" y2="118" stroke="#f97316" stroke-width="1.5" stroke-dasharray="3,3"/>
<line x1="515" y1="86" x2="515" y2="192" stroke="#f59e0b" stroke-width="1.5" stroke-dasharray="3,3"/>
<line x1="960" y1="86" x2="960" y2="172" stroke="#00e676" stroke-width="1.5" stroke-dasharray="3,3"/>
<line x1="335" y1="268" x2="335" y2="338" stroke="#ff4d4f" stroke-width="1.5" stroke-dasharray="3,3"/>
<line x1="695" y1="260" x2="695" y2="338" stroke="#38bdf8" stroke-width="1.5" stroke-dasharray="3,3"/>
<line x1="1010" y1="288" x2="1010" y2="338" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="3,3"/>
<polyline points="90,118 335,268 515,192 695,260 835,212 960,172 1120,152" fill="none" stroke="#f59e0b" stroke-width="4.5" stroke-linejoin="round"/>
<polyline points="695,260 805,288 925,266" fill="none" stroke="#ef4444" stroke-width="2" stroke-dasharray="5,4"/>
<circle cx="90" cy="118" r="7" fill="#f97316" stroke="#fff" stroke-width="2"/>
<circle cx="335" cy="268" r="8" fill="#ff4d4f" stroke="#fff" stroke-width="2"/>
<circle cx="515" cy="192" r="8" fill="#f59e0b" stroke="#fff" stroke-width="2"/>
<circle cx="695" cy="260" r="8" fill="#38bdf8" stroke="#fff" stroke-width="2"/>
<circle cx="960" cy="172" r="8" fill="#00e676" stroke="#fff" stroke-width="2"/>
<line x1="{curr_x}" y1="{curr_y}" x2="{badge_x}" y2="{badge_y}" stroke="{dot_col}" stroke-width="2.5"/>
<circle cx="{curr_x}" cy="{curr_y}" r="15" fill="{dot_col}" fill-opacity="0.32"/>
<circle cx="{curr_x}" cy="{curr_y}" r="7.5" fill="{dot_col}" stroke="#ffffff" stroke-width="3"/>
<rect x="{badge_x - 88}" y="{badge_y - 15}" width="176" height="28" rx="7" fill="{dot_col}" stroke="#09111a" stroke-width="2"/>
<text x="{badge_x}" y="{badge_y + 4}" fill="#ffffff" font-family="sans-serif" font-size="13.5" font-weight="900" text-anchor="middle">📍現價 {twii_now:,.0f}點</text>
<rect x="20" y="14" width="215" height="70" rx="7" fill="#16222f" stroke="#f97316" stroke-width="1.5"/>
<text x="127" y="36" fill="#e2e8f0" font-family="sans-serif" font-size="13" font-weight="bold" text-anchor="middle">波段起跌高點 (近60日高)</text>
<text x="127" y="58" fill="#fcd34d" font-family="sans-serif" font-size="15" font-weight="900" text-anchor="middle">{hi_60d:,.0f} 點</text>
<text x="127" y="76" fill="#fda4af" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">30日急殺回檔 -{fleet['twii_30d_dd_pct']:.1f}%</text>
<rect x="355" y="14" width="330" height="70" rx="7" fill="#2d1b19" stroke="#f59e0b" stroke-width="2"/>
<text x="520" y="35" fill="#ffffff" font-family="sans-serif" font-size="13.5" font-weight="900" text-anchor="middle">⚠️ 反彈遇壓／減碼區 (年線與0.382反壓)</text>
<text x="520" y="56" fill="#fcd34d" font-family="sans-serif" font-size="14.5" font-weight="900" text-anchor="middle">{r1_low:,.0f} ～ {r1_high:,.0f} 點</text>
<text x="520" y="75" fill="#fda4af" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">00400A 務實滾動退場({exit_t1}~{exit_t2}元)轉00985B</text>
<rect x="780" y="14" width="395" height="70" rx="7" fill="#112a26" stroke="#00e676" stroke-width="1.5"/>
<text x="977" y="36" fill="#a7f3d0" font-family="sans-serif" font-size="13.5" font-weight="900" text-anchor="middle">🚀 2027 Q1 築底修復・外資主流回流段</text>
<text x="977" y="57" fill="#00e676" font-family="sans-serif" font-size="14.5" font-weight="900" text-anchor="middle">待落底雷達 &gt;= 4/5 解除鎖定後分 3 批進場</text>
<text x="977" y="76" fill="#6ee7b7" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">聚焦：新穀倉1共通規格 ＋ MSCI 新增主流 ＋ 傳家寶龍頭</text>
<rect x="20" y="338" width="380" height="88" rx="7" fill="#2b0f14" stroke="#ff4d4f" stroke-width="2"/>
<text x="210" y="360" fill="#ff8a80" font-family="sans-serif" font-size="13.5" font-weight="900" text-anchor="middle">🔥 第一隻腳測試區 (月20MA＋11/23限空令3.0)</text>
<text x="210" y="384" fill="#fcd34d" font-family="sans-serif" font-size="15" font-weight="900" text-anchor="middle">{l1_low:,.0f} ～ {l1_high:,.0f} 點 (月20MA {m_ma20:,.0f})</text>
<text x="210" y="408" fill="#e2e8f0" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">左腳看爆量神針／無量鎖漲停廣度｜紅燈減碼避09:00-09:10</text>
<rect x="425" y="338" width="380" height="88" rx="7" fill="#13283c" stroke="#38bdf8" stroke-width="2"/>
<text x="615" y="360" fill="#93c5fd" font-family="sans-serif" font-size="13.5" font-weight="900" text-anchor="middle">🎯 W底右腳二次測底 (12/8 FOMC・12/15 ANT解禁)</text>
<text x="615" y="384" fill="#6ee7b7" font-family="sans-serif" font-size="15" font-weight="900" text-anchor="middle">{l2_low:,.0f} ～ {l2_high:,.0f} 點 (威科夫窒息縮量)</text>
<text x="615" y="408" fill="#fcd34d" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">落底達 4/5 ➔ 動用水庫四(SGOV/台幣現金)分3批重錘龍頭</text>
<rect x="830" y="338" width="345" height="88" rx="7" fill="#16222f" stroke="#94a3b8" stroke-width="1.5"/>
<text x="1002" y="360" fill="#cbd5e1" font-family="sans-serif" font-size="13.5" font-weight="bold" text-anchor="middle">🛡️ 極端系統性壓力測試底 (月36MA區間)</text>
<text x="1002" y="384" fill="#fcd34d" font-family="sans-serif" font-size="15" font-weight="900" text-anchor="middle">{ext_lo:,.0f} ～ {ext_hi:,.0f} 點</text>
<text x="1002" y="408" fill="#94a3b8" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">僅於美股流動性危機擴大時啟動・死抱傳家寶＋00985B防彈</text>
</svg>"""
    b64_svg = base64.b64encode(svg_xml.encode("utf-8")).decode("utf-8")
    border_col = "#ff4d4f" if radar["master_state"] == "RED_LOCKED" else ("#fbbf24" if radar["master_state"] == "YELLOW" else "#00e676")
    return clean_html(f"""
<div style="background: linear-gradient(180deg, #0d1520 0%, #16222f 100%); border: 2px solid {border_col}; border-radius: 14px; padding: 16px 20px; margin-bottom: 16px; color: #fff;">
<div style="text-align:center; font-size:21px; font-weight:900; color:#fcd34d; border-bottom:1px solid rgba(252,211,77,0.3); padding-bottom:7px; margin-bottom:10px;">
📉 加權指數動態股災四階段週期走勢圖 × 淘金客 v24.4 三階燈號總開關（精準插值校準版）
</div>
<div style="font-size:15px; font-weight:800; color:#f8fafc; background:rgba(0,0,0,0.35); padding:9px 14px; border-radius:8px; margin-bottom:10px; border-left:4px solid {border_col}; line-height:1.65;">
• {stage_desc}<br>
• <b>總開關狀態</b>：{radar['master_badge']} ➔ <span style="color:#ffd54f;">{radar['master_action']}</span>{policy_warn}{precursor_banner}
</div>
<img src="data:image/svg+xml;base64,{b64_svg}" style="width:100%; height:auto; display:block; border-radius:10px;" />
</div>
""")


# ==========================================
# v62.0 淘金客 v24.4 雙模式戰情數據包組裝函式 (對接四軌落底 ＋ 防截斷 ＋ 既有持倉連動)
# ==========================================
def build_market_outpost_line_v244(fleet: dict, radar: dict, deriv: dict, tnx_val: float, fx_now: float, ban_active: bool, next_ev: dict) -> str:
    tw = fleet["twii_now"]
    pos_60 = ">=" if tw >= fleet["ma60"] else "<"
    pos_120 = ">=" if tw >= fleet["ma120"] else "<"
    pos_240 = ">=" if tw >= fleet["ma240"] else "<"
    ban_tag = "限空令生效中(凍結借券賣減看多加分)" if ban_active else "無限空令"
    esc_tag = "/⚠️政策逃命波" if radar["is_policy_escape_wave"] else ""
    b2_tag = f"四軌落底:{'✅' + radar['b2_detail'] if radar['b2_ok'] else '未達標'}"
    pre_tag = f" | {radar['precursor_txt']}" if radar.get("precursor_txt") else ""
    ev_tag = f"近3日大事件:{next_ev['日期']}{next_ev['_short']}(倒數{next_ev['_days']}天)" if (next_ev and next_ev["_days"] <= 3) else (f"下個事件:{next_ev['日期']}{next_ev['_short']}" if next_ev else "無近事件")
    return (
        f"[大盤前哨] 加權={tw:,.1f}({fleet['twii_chg_pct']:+.2f}%・{fleet['date_str']}) | "
        f"位階:{pos_60}60MA({fleet['ma60']:,.0f})/{pos_120}120MA({fleet['ma120']:,.0f})/{pos_240}240MA({fleet['ma240']:,.0f})/月20MA({fleet['m_ma20']:,.0f}) | "
        f"模組0燈號:{radar['lamp_short']}(預警{radar['w_count']}/5・落底{radar['b_count']}/5・{b2_tag}・逆價差率{radar['basis_rate_pct']:.2f}%{esc_tag}){pre_tag} | "
        f"{ban_tag} | PCR={deriv['pc_ratio']:.1f}% | ^TNX={tnx_val:.2f}% | TWD={fx_now:.3f} | {ev_tag}"
    )


def format_tdcc_with_override(d_rec: dict, live_tdcc_map: dict) -> str:
    code = d_rec["代碼"]
    if code in live_tdcc_map:
        w = live_tdcc_map[code]
        return f"400張:{w['p400']:.1f}%({w['d400']:+.2f}%)/千張:{w['p1000']:.1f}%({w['d1000']:+.2f}%)[{w['date']}]"
    return d_rec.get("大戶400/千張", "N/A")


def build_mode_a_guard_package_v244(codes: list[str], source_label: str, batch_label: str, all_details: dict, outpost_line: str, now_str: str, live_tdcc_map: dict) -> str:
    valid_codes = [c for c in codes[:6] if c in all_details]
    lines = [
        f"【淘金客 v24.4｜模式A 守門員快篩批次包（來源：{source_label}｜{batch_label}｜實附 {len(valid_codes)} 檔，核對無漏檔）】({now_str})",
        outpost_line
    ]
    for idx, c in enumerate(valid_codes, 1):
        r = all_details[c]
        tdcc_s = format_tdcc_with_override(r, live_tdcc_map)
        up_sh = f"{r['上影線%']:.1f}%" if pd.notna(r.get("上影線%")) else "N/A"
        lines.append(
            f"{idx}. {c} {r['_raw_name']} | {r['現價']}({r['今日%']:+.2f}%/3日{r['3日%']:+.2f}%) | "
            f"型態:{r.get('_pattern_tag', r['短線屬性'])} | "
            f"DMI:{r['DMI方向雙檢']} J={r['日J值']} | BIAS10={r['BIAS10']} | "
            f"量比{r['5MA量比']}x/上影{up_sh}/位階{r['收盤位階']}/當沖{r['當沖率']} | "
            f"OBV:{r['OBV趨勢']}/RS20D:{r['RS20D%']}/大戶:{tdcc_s} | "
            f"ATR%={r['ATR%波動尺']} | 月營收YoY={r['單月營收YoY']}"
        )
    lines.append(f"[批次結尾核對：實附 {len(valid_codes)} 檔完畢] ➔ 請執行【淘金客 v24.4 模式A 守門員快篩】：集中聯網補查六大死因（處置殭屍／當沖假突破／主動ETF踩踏／墊料壓貨／3日大事件／大戶地雷），以簡表裁決通過／觀察／淘汰並點名進模式B之 Top 1～2！")
    return "\n".join(lines)


def build_mode_b_deep_package_v244(codes: list[str], all_details: dict, price_dict_all: dict, outpost_line: str, now_str: str, live_tdcc_map: dict, holding_notes: dict, is_yellow_red: bool) -> str:
    target_codes = codes[:3]
    pe_note = "【黃/紅燈殺估值期：請嚴審產業P/E天花板】" if is_yellow_red else ""
    lines = [
        f"【淘金客 v24.4｜模式B 決選深檢戰情包（實附 {len(target_codes)} 檔，核對無漏檔）】({now_str})",
        outpost_line
    ]
    for idx, c in enumerate(target_codes, 1):
        r = all_details.get(c)
        hold_s = holding_notes.get(c, "空手新單（無既有部位）")
        if r:
            mkt = r.get("_mkt", "TW")
            anti_trap = fetch_single_stock_anti_trap(c, mkt)
            tdcc_s = format_tdcc_with_override(r, live_tdcc_map)
            up_sh = f"{r['上影線%']:.1f}%" if pd.notna(r.get("上影線%")) else "N/A"
            eps_s = f"{r['預估EPS(元)']:.2f}元" if pd.notna(r.get("預估EPS(元)")) else "N/A"
            pe_s = f"{r['本益比(PE)']:.1f}x" if pd.notna(r.get("本益比(PE)")) else "N/A"
            lines.extend([
                f"=== 決選標的 {idx}：{c} {r['_raw_name']} ({mkt}) ===",
                f"• 持倉帳務狀態：{hold_s}",
                f"• 報價與賽道：現價={r['現價']}({r['今日%']:+.2f}% / 3日{r['3日%']:+.2f}%) | 型態={r.get('_pattern_tag', r['短線屬性'])} | 穀倉={r['穀倉賽道與防墊料']} | CB={r.get('_cb', '➖')}",
                f"• 完整均線與城堡：5MA={r['_ma5']} / 10MA={r['_ma10']} / 20MA={r['_ma20']} / 60MA={r['_ma60']} / 120MA={r['_ma120']} / 240MA={r['_ma240']} | 天花板={r['天花板(反壓)']} / 鋼鐵防線={r['鋼鐵防線']} / 停損={r['極限停損']}",
                f"• DMI、乖離與波動：{r['DMI方向雙檢']} | 日J={r['日J值']} | BIAS10={r['BIAS10']} / BIAS20={r['BIAS20']} | ATR%={r['ATR%波動尺']}",
                f"• 假突破三合一：5MA量比={r['5MA量比']}x (昨量比{r['昨日量比']}x / 預估{r['預估量(張)']:,}張) | 上影線%={up_sh} | 收盤位階={r['收盤位階']} | 當沖率={r['當沖率']}",
                f"• 量能與籌碼動能：OBV={r['OBV趨勢']} | RS20D={r['RS20D%']} | 法人={r['法人動向']} (自營商與Sum-Check請聯網補齊) | 大戶={tdcc_s}",
                f"• 基本面與防墊料：最新月營收YoY={r['單月營收YoY']} / 累計YoY={r['累計年營收YoY']} | 預估EPS={eps_s} / PE={pe_s} ({r['估值診斷']}){pe_note} | 季報防墊料={anti_trap.get('note', 'N/A')}"
            ])
        else:
            p_inf = price_dict_all.get(c, {})
            mkt = p_inf.get("market", "TW")
            anti_trap = fetch_single_stock_anti_trap(c, mkt)
            tdcc_s = format_tdcc_with_override({"代碼": c, "大戶400/千張": "N/A"}, live_tdcc_map)
            lines.extend([
                f"=== 決選標的 {idx}：{c} {p_inf.get('name', c)} ({mkt}・尚未執行Layer2全掃) ===",
                f"• 持倉帳務狀態：{hold_s}",
                f"• 基礎報價：現價={p_inf.get('close', 'N/A')}({p_inf.get('chg_pct', 0):+.2f}%) | 成交量={p_inf.get('vol_lots', 0):,}張 | 當沖率={p_inf.get('day_trade_str', 'N/A')}",
                f"• 籌碼與防墊料：大戶={tdcc_s} | 季報防墊料={anti_trap.get('note', 'N/A')} | 完整技術面=N/A(請先執行Layer2掃描或由淘金客聯網補查)"
            ])
    pk_tail = " ※本次傳入多檔，請於最末段強制附加【決選優先排序表】排出第 1～3 順位！" if len(target_codes) >= 2 else ""
    lines.append(f"[深檢結尾核對：實附 {len(target_codes)} 檔完畢] ➔ 請執行【淘金客 v24.4 模式B 模組 20 雙核深檢】：強制補搜處置新制、自營商Sum-Check、主動ETF踩踏、資券借與法說催化劑，獨立列出 App 訊號裁決、城堡各階具體價位與雙軌行動(新單空手 vs 既有底倉/短單停損)！{pk_tail}")
    return "\n".join(lines)


# ==========================================
# Streamlit 主程式入口 (v62.0 三層按需秒開版 × 淘金客 v24.4)
# ==========================================
now_tw_dt = get_tw_now()
now_str = now_tw_dt.strftime("%Y-%m-%d %H:%M:%S")
st.markdown("## 🚨 台股每日戰情室 v62.0 (三層秒開架構 ＋ 全浮動四軌落底 ＋ 對接淘金客 v24.4)")

# 頂部獨立分層控制列
btn_c1, btn_c2, btn_c3, btn_c4 = st.columns([1.8, 2.0, 1.5, 1.7])
if btn_c1.button("⚡ 秒速刷新大盤與四水庫 (2秒)", use_container_width=True):
    fetch_mis_realtime_core16.clear()
    fetch_market_prices_fast.clear()
    fetch_us_sectors_and_leaders.clear()
    fetch_auto_derivatives_and_margin_v62.clear()
    st.rerun()

trigger_layer2_top = btn_c2.button("🚀 啟動／重掃全台個股池 (Layer 2)", type="primary", use_container_width=True, key="btn_l2_top")

if btn_c3.button("🔄 全域清快取重抓", use_container_width=True):
    st.cache_data.clear()
    st.session_state.pop("v62_scan_data", None)
    st.session_state.pop("v62_rev_df", None)
    st.rerun()

auto_refresh = btn_c4.checkbox("⏱️ 每 20 秒自動刷新大盤", value=False)

# ================= LAYER 1：秒開大盤與個人核心資產 (自動執行，約 3～5 秒) =================
with st.spinner("⚡ Layer 1 秒開載入中：大盤三源最新日、美股/VIX、真浮動融資/期貨與四水庫..."):
    fleet = fetch_and_analyze_fleet_fast()
    us_data = fetch_us_sectors_and_leaders()

deriv = fleet["deriv"]
etf_m = fleet["etf_metrics"]
breadth = fleet["breadth"]
rt_quotes = fleet["rt_quotes"]
fast_prices = fleet["fast_prices"]

# 美股與債匯核心數值
vix_q = us_data["quotes"].get("%5EVIX", {})
vix_now, vix_chg = vix_q.get("close", 26.6), vix_q.get("chg_pct", 0.0)
tnx_val, tnx_chg = us_data.get("tnx_val", 4.92), us_data.get("tnx_chg", 0.0)
sox_chg = us_data["quotes"].get("%5ESOX", {}).get("chg_pct", 0.0)
tsm_q, nvda_q = us_data["quotes"].get("TSM", {}), us_data["quotes"].get("NVDA", {})
tsm_chg, tsm_ext = tsm_q.get("chg_pct", 0.0), (f" ({tsm_q['ext_label']})" if tsm_q.get("ext_label") else "")
nvda_chg, nvda_ext = nvda_q.get("chg_pct", 0.0), (f" ({nvda_q['ext_label']})" if nvda_q.get("ext_label") else "")
fx_now, fx_ma5, fx_chg_ntd = deriv["fx_now"], deriv["fx_ma5"], deriv["fx_chg_ntd"]
fx_stopped_dep = bool(fx_chg_ntd <= 0.0 or fx_now <= fx_ma5)
macro_withdrawal_alert = bool(tnx_val >= 5.0 and fx_now >= 32.50)

def _run_layer2_scan():
    with st.spinner("🔍 Layer 2 執行中：掃描全台 ~170 檔非殭屍股 K 線、5 組接軌指標 (DMI/BIAS/當沖/OBV/RS/ATR%)、MSCI 與宏爺穀倉..."):
        st.session_state["v62_scan_data"] = scan_all_stocks_on_demand(fleet["twii_20d_ret"], macro_withdrawal_alert)

if trigger_layer2_top:
    _run_layer2_scan()

scan_data = st.session_state.get("v62_scan_data", None)
price_dict_active = scan_data["price_dict"] if scan_data else fast_prices
all_scanned_details = scan_data["all_details"] if scan_data else {}
msci_data = scan_data["msci_data"] if scan_data else fetch_dynamic_msci_data(1)

# ================= 左側雙階讀取覆寫控制台 (100% 動態帶入，無寫死常數) =================
with st.sidebar:
    st.markdown("### 🎛️ 雙階讀取覆寫控制台 (全浮動)")
    st.caption(f"📡 期貨源：{deriv['fut_src']} ｜ 融資源：{deriv['margin_src']} ｜ 價差源：{deriv['basis_src']}")
    sb_fini_oi = st.number_input("外資台指期淨未平倉 (口)", value=int(deriv["fini_tx_oi"]), step=1000)
    sb_margin_2d = st.number_input("近2日上市融資增減 (億)", value=float(deriv["margin_2d_chg_yi"]), step=50.0, format="%.1f")
    sb_margin_2d_pct = st.number_input("近2日融資變動幅 (%)", value=float(deriv["margin_2d_pct"]), step=1.0, format="%.1f")
    sb_margin_dd = st.number_input("30日波段融資減幅 (%)", value=float(deriv["margin_30d_dd_pct"]), step=1.0, format="%.1f")
    sb_maint = st.number_input("整戶融資維持率 (%，動態估算可覆寫)", value=float(deriv["stock_only_maint_pct"]), step=0.5, format="%.1f")
    sb_basis = st.number_input("期現貨價差 (點，負=逆價差)", value=float(deriv["basis_pts"]), step=20.0, format="%.0f")
    sb_basis_stable = st.checkbox("恐慌逆價差已達極點且不再擴大", value=False)
    sb_consec_cover = st.number_input("外資期貨連補天數 (日)", value=int(deriv["consec_cover_days"]), min_value=0, max_value=15, step=1)
    sb_cover_3d = st.number_input("外資近3日累計回補 (口)", value=int(deriv["cover_3d_sum"]), step=1000)
    sb_limit_dn = st.number_input("當日上市櫃跌停家數 (家)", value=int(breadth["limit_down"]), min_value=0, step=5)
    sb_limit_up = st.number_input("當日上市櫃漲停家數 (家・2025/04條款)", value=int(breadth["limit_up"]), min_value=0, step=10)
    sb_had_100 = st.checkbox("本波曾見破百檔跌停高峰", value=True)
    sb_ban_active = st.checkbox("限空令 / 國安基金護盤生效中", value=True)
    sb_ban_date = st.date_input("限空令／護盤起始日", value=datetime.date(2026, 11, 23))

    st.markdown("---")
    st.markdown("### 💧 11/23 最新四水庫＆短單帳務")
    sb_lots_00985b = st.number_input("水庫三：00985B 張數 (@10.38)", value=5.0, min_value=0.0, step=1.0, format="%.1f")
    sb_lots_00713 = st.number_input("水庫三：00713 張數", value=0.0, min_value=0.0, step=0.5, format="%.1f")
    sb_shares_3711_short = st.number_input("換強短單：日月光零股 (股 @474)", value=300, min_value=0, step=100)
    sb_short_3711_be = st.number_input("日月光短單損益平衡價 (元)", value=476.79, min_value=0.0, step=1.0, format="%.2f")
    sb_short_3711_above_ma5 = st.checkbox("日月光短單已站穩5MA(升級破5MA停利)", value=False)
    sb_lots_00400a = st.number_input("待退場：00400A 張數 (@14.40)", value=31.0, min_value=0.0, step=1.0, format="%.1f")
    sb_lots_6239 = st.number_input("衛星剩餘：力成(6239) 張數 (@245.75)", value=1.0, min_value=0.0, step=0.1, format="%.1f")
    sb_lots_3264 = st.number_input("衛星剩餘：欣銓(3264) 張數 (已清)", value=0.0, min_value=0.0, step=0.1, format="%.1f")
    sb_twd_cash_wan = st.number_input("水庫四：機動台幣現金 (萬元)", value=17.4, min_value=0.0, step=1.0, format="%.1f")
    sb_usd_cash = st.number_input("水庫四：美金現金 (USD)", value=2300.0, min_value=0.0, step=100.0, format="%.0f")

# ================= 全域頂部：模組二 滾動風險事件警示條 ＋ 時間軸 SVG ＋ 多空劇本 =================
ev_rows, upcoming_ev_rows, next_ev = get_dynamic_rolling_events(horizon_days=90)
if next_ev:
    d_left = next_ev["_days"]
    ev_cls = "event-banner-red" if d_left <= 3 else ("event-banner-yellow" if d_left <= 7 else "event-banner-blue")
    ev_warn_tip = "🚨 【高波動警戒：事件前 3 天內！反彈遇壓減碼、嚴禁開槓桿！】" if d_left <= 3 else "📌 自動滾動追蹤未來 90 天官宣與月度例行事件。"
    st.markdown(clean_html(f"""
    <div class="{ev_cls}">
    <b>📅 下一個重大風險事件倒數：{next_ev['日期']}（{next_ev['倒數狀態']}）➔ {next_ev['重大風險事件']}</b><br>
    • {ev_warn_tip} ｜ 🟢 <b>優於預期</b>：{next_ev['🟢 優於預期／鴿派劇本']} ｜ 🔴 <b>差於預期</b>：{next_ev['🔴 差於預期／鷹派劇本']}
    </div>
    """), unsafe_allow_html=True)

with st.expander("🗓️ 點此展開【自動滾動風險事件視覺化時間軸圖（已去重）＋ 數據公布後多空劇本表 ＋ 自訂事件新增】", expanded=False):
    st.markdown(build_event_timeline_svg_html(upcoming_ev_rows), unsafe_allow_html=True)
    st.dataframe(pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in ev_rows]), use_container_width=True, hide_index=True)

    st.markdown("##### ➕ 新增單次突發風險事件")
    ce_c1, ce_c2, ce_c3, ce_c4 = st.columns([1.2, 1.5, 2.2, 2.2])
    ce_date = ce_c1.date_input("事件日期", value=now_tw_dt.date() + datetime.timedelta(days=5), key="ce_d")
    ce_short = ce_c2.text_input("時間軸簡稱 (8字內)", value="", placeholder="例：台積電法說會", key="ce_s")
    ce_bull = ce_c3.text_input("🟢 優於預期劇本", value="展望優於預期帶動 AI 估值修復", key="ce_b")
    ce_bear = ce_c4.text_input("🔴 差於預期劇本", value="下修資本支出引發供應鏈修正", key="ce_r")
    if st.button("💾 儲存新增事件至滾動時間軸", key="btn_add_ce"):
        if ce_short.strip():
            cur_list = []
            if os.path.exists(CUSTOM_EVENTS_FILE):
                try:
                    with open(CUSTOM_EVENTS_FILE, "r", encoding="utf-8") as f: cur_list = json.load(f)
                except Exception: cur_list = []
            cur_list.append({"date": ce_date.strftime("%Y-%m-%d"), "short": ce_short.strip(), "title": f"【自訂】{ce_short.strip()}", "level": "HIGH", "bull": ce_bull, "bear": ce_bear, "action": "事件前3天控管槓桿與倉位"})
            try:
                with open(CUSTOM_EVENTS_FILE, "w", encoding="utf-8") as f: json.dump(cur_list, f, ensure_ascii=False)
                st.success("✅ 已寫入滾動事件簿！重新整理即可顯示於時間軸。")
            except Exception as e:
                st.error(f"儲存失敗：{e}")

# 計算模組 0 (v24.4 四軌落底版) 結果
days_since_ban = (now_tw_dt.date() - sb_ban_date).days
radar = evaluate_crash_and_bottom_radar_v244(
    fleet["twii_now"], fleet["twii_chg_pct"], fleet["ma60"], fleet["ma120"], fleet["ma240"], fleet["twii_30d_dd_pct"], fleet["sp500_below_ma200"],
    vix_now, fleet["sox_dd_pct"], sb_fini_oi, sb_margin_dd, sb_margin_2d, sb_margin_2d_pct, sb_maint,
    fleet["bottom_4track"], sb_limit_up, breadth["adv_ratio_pct"],
    sb_basis, sb_basis_stable, sb_consec_cover, sb_cover_3d,
    fx_stopped_dep, sb_limit_dn, sb_had_100,
    sb_ban_active, days_since_ban, fleet["twii_consec_above_ma10"]
)

t_min_now = now_tw_dt.hour * 60 + now_tw_dt.minute
is_panic_open_window = (radar["master_state"] == "RED_LOCKED") and (now_tw_dt.weekday() < 5) and (540 <= t_min_now <= 555)

# 四水庫即時價格與市值試算 (Layer 1 秒開直接完成)
def get_live_p(code_k, default_v):
    is_open_t = (now_tw_dt.weekday() < 5) and (t_min_now >= 540)
    rt_v = rt_quotes.get(code_k, {}).get("close", 0.0) if is_open_t else 0.0
    return float(rt_v or price_dict_active.get(code_k, {}).get("close") or default_v)

p_2330, p_3711 = get_live_p("2330", 2030.0), get_live_p("3711", 482.0)
p_6239, p_3264 = get_live_p("6239", 191.0), get_live_p("3264", 141.5)
p_2884, p_2881 = get_live_p("2884", 38.7), get_live_p("2881", 130.5)
p_2812, p_2887, p_2834 = get_live_p("2812", 15.6), get_live_p("2887", 33.0), get_live_p("2834", 16.05)
p_avgo = us_data["quotes"].get("AVGO", {}).get("close") or etf_m["AVGO"]["p"]
p_nvda = us_data["quotes"].get("NVDA", {}).get("close") or etf_m["NVDA"]["p"]
p_sgov = us_data["quotes"].get("SGOV", {}).get("close") or etf_m["SGOV"]["p"]
p_00400a, p_00985b, p_00713 = etf_m["00400A"]["p"], etf_m["00985B"]["p"], etf_m["00713"]["p"]
ma5_3711 = etf_m.get("3711", {}).get("ma5", 485.0)

val_r1 = 400000.0
val_r2_tw_semi = 4000 * p_2330 + 3000 * p_3711
val_r2_tw_fin = 100453 * p_2884 + 11743 * p_2881 + 27148 * p_2812 + 4882 * p_2887 + 158 * 9.57 + 1090 * p_2834
val_r2_us_semi = (35 * p_avgo + 80 * p_nvda) * fx_now
val_r2 = val_r2_tw_semi + val_r2_tw_fin + val_r2_us_semi
val_r3 = sb_lots_00985b * 1000 * p_00985b + sb_lots_00713 * 1000 * p_00713
val_r4 = (30 * p_sgov + sb_usd_cash) * fx_now + sb_twd_cash_wan * 10000.0
val_short_3711 = sb_shares_3711_short * p_3711
val_sat_semi = sb_lots_6239 * 1000 * p_6239 + sb_lots_3264 * 1000 * p_3264
val_sat_400a = sb_lots_00400a * 1000 * p_00400a
total_assets = val_r1 + val_r2 + val_r3 + val_r4 + val_short_3711 + val_sat_semi + val_sat_400a
semi_total = val_r2_tw_semi + val_r2_us_semi + val_short_3711 + val_sat_semi
semi_ratio_pct = round((semi_total / max(1.0, total_assets)) * 100.0, 1)

holding_notes_map = {
    "3711": f"【雙軌分帳】(1)水庫二傳家寶底倉 3張(@172.67)不動；(2)11/23淨降倉換強獨立短單 {sb_shares_3711_short}股(@474.0/平衡價{sb_short_3711_be:.2f}/5MA={ma5_3711:.1f}，買於5MA下方先守平衡價{sb_short_3711_be:.2f}，站上5MA後升級破5MA移動停利！)",
    "6239": f"衛星剩餘 {sb_lots_6239:.1f}張 (@245.75/平衡價247.2，已於11/23減碼1.3張)",
    "2330": "水庫二傳家寶核心底倉 4張 (@229.11，全程死抱不動)",
    "2884": "水庫二傳家寶核心底倉 100.453張 (@19.15)",
    "2881": "水庫二傳家寶核心底倉 11.743張 (@69.50)",
    "2812": "水庫二傳家寶核心底倉 27.148張 (@7.83)",
    "00400A": f"待退場主動ETF {sb_lots_00400a:.0f}張 (@14.40，不加碼、逢彈滾動退場轉00985B)",
    "00985B": f"水庫三低波債券 {sb_lots_00985b:.1f}張 (@10.38)"
}

outpost_line = build_market_outpost_line_v244(fleet, radar, deriv, tnx_val, fx_now, sb_ban_active, next_ev)

# 若 Layer 2 已執行，整理各分頁 DataFrame
if scan_data:
    df_launch = prioritize_granary_and_armor_df(scan_data["df_launch"], macro_withdrawal_alert, "實戰優先分")
    df_pullback = prioritize_granary_and_armor_df(scan_data["df_pullback"], macro_withdrawal_alert, "蓄勢評分")
    df_orbit = scan_data["df_orbit"]
    df_long_bc = prioritize_granary_and_armor_df(scan_data["df_long_bc"], macro_withdrawal_alert, "波段總分")
    df_long_turn = prioritize_granary_and_armor_df(scan_data["df_long_turn"], macro_withdrawal_alert, "波段總分")
    df_granary_audit = scan_data["df_granary"]
    df_msci_audit = scan_data["df_msci"]
    tw_hot_sec_info = compute_tw_hot_sectors(scan_data["sec_recs"])
    if not df_launch.empty and "短線屬性" in df_launch.columns:
        df_launch_fund = df_launch[df_launch["短線屬性"].str.contains("有基之彈|核心權值|政策內需", na=False)].copy()
        df_launch_pure = df_launch[df_launch["短線屬性"].str.contains("純技術炒作", na=False)].copy()
    else:
        df_launch_fund, df_launch_pure = pd.DataFrame(), pd.DataFrame()
else:
    df_launch = df_pullback = df_orbit = df_long_bc = df_long_turn = df_granary_audit = df_msci_audit = df_launch_fund = df_launch_pure = pd.DataFrame()
    tw_hot_sec_info = {"hot_html": "", "cold_html": "", "hot_text": "尚未啟動 Layer 2 掃描", "cold_text": "", "top1_name": "待掃描"}

# ==========================================
# 六大功能分頁架構
# ==========================================
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🚨 雙雷達＆v24.4戰情包",
    "⚖️ 換股＆短單＆四水庫",
    f"⚡ 短線旱地拔蔥榜 {'✅' if scan_data else '💤'}",
    f"🏛️ 長線波段＆MSCI池 {'✅' if scan_data else '💤'}",
    f"🌾 穀倉與防墊料專區 {'✅' if scan_data else '💤'}",
    f"📈 上市櫃月營收快篩 {'✅' if 'v62_rev_df' in st.session_state else '💤'}"
])

# ==================== TAB 1 ====================
with tab1:
    if is_panic_open_window:
        st.error("⏰ 【模組 9 紅燈盤中紀律】目前為 09:00-09:15 恐慌集中賣壓區！減碼弱勢股或比價換股請避開此低點（除非瀕臨鎖跌停），改於 09:15 後流動性穩定或反彈遇壓執行！")

    hero_cls = "war-hero-box-danger" if radar["master_state"] == "RED_LOCKED" else ("war-hero-box-warn" if radar["master_state"] == "YELLOW" else "war-hero-box")
    idx_col = "#ff4d4f" if fleet["twii_chg_pct"] >= 0 else "#00e676"
    sox_c = "#ff4d4f" if sox_chg >= 0 else "#00e676"
    tsm_c = "#ff4d4f" if tsm_chg >= 0 else "#00e676"
    nvda_c = "#ff4d4f" if nvda_chg >= 0 else "#00e676"

    w_html = "<br>".join([f"{'🔴' if f else '⚪'} {txt}" for f, txt in radar["w_items"]])
    b_html = "<br>".join([f"{'🟢' if f else '⚪'} {txt}" for f, txt in radar["b_items"]])
    if radar.get("precursor_txt"):
        b_html += f"<br><span style='color:#fde047; font-weight:800;'>{radar['precursor_txt']}</span>"
    ban_badge = "🛡️ <b>限空令生效中</b>（凍結借券賣減看多加分，改看外資現貨＋期貨連補＋台幣止貶）" if sb_ban_active else "⚪ 目前無限空令"

    st.markdown(clean_html(f"""
    <div class="{hero_cls}">
    <div class="war-score-title">
    📊 台股加權指數：<span style="font-size:34px; color:{idx_col};">{fleet['twii_now']:,.1f} 點 ({fleet['twii_chg_pct']:+.2f}%)</span>
    &nbsp;｜&nbsp; {radar['master_badge']}
    </div>
    <div style="font-size:14.6px; color:#e2e8f0; background:rgba(0,0,0,0.35); padding:8px 14px; border-radius:8px; margin-bottom:10px; line-height:1.68;">
    • 🕒 <b>三源最新對時</b>：{fleet['date_str']}（{fleet['quote_src']}｜開盤 <b>{fleet['twii_open']:,.0f}</b> ／ 低 <b>{fleet['twii_low']:,.0f}</b> ～ 高 <b>{fleet['twii_high']:,.0f}</b> ／ 昨收 <b>{fleet['twii_prev_c']:,.0f}</b>）｜ <b>月20MA：{fleet['m_ma20']:,.0f}</b> ｜ <b>年線：{fleet['ma240']:,.0f}</b><br>
    • ⚖️ <b>模組 0 最高權限指令</b>：<b style="color:#fde047;">{radar['master_action']}</b><br>
    • {ban_badge} ｜ 連站10MA：<b>{fleet['twii_consec_above_ma10']} 日</b> ｜ 全市場廣度：<b>上漲比 {breadth['adv_ratio_pct']:.0f}%（漲停 {sb_limit_up} 家 ／ 跌停 {sb_limit_dn} 家）</b>
    </div>
    <div class="radar-grid-2">
    <div class="radar-panel" style="border-left:5px solid {'#ff4d4f' if radar['crash_warning_on'] else '#64748b'};">
    <span class="war-dim-concl" style="color:#ff8a80;">🔴 一、股災啟動預警雷達（觸發 {radar['w_count']} / 5 項 ｜ ≧3項亮紅燈）</span>
    {w_html}
    </div>
    <div class="radar-panel" style="border-left:5px solid {'#00e676' if radar['bottom_confirmed'] else '#f59e0b'};">
    <span class="war-dim-concl" style="color:#69f0ae;">🟢 二、股災落底確認雷達（達成 {radar['b_count']} / 5 項 ｜ ≧4項共振解鎖）</span>
    {b_html}
    </div>
    </div>
    </div>
    """), unsafe_allow_html=True)

    # 雙模式戰情數據包匯出中心 (對接淘金客 v24.4)
    st.markdown("### 🤖 【淘金客 v24.4 雙腦互補：一鍵複製戰情數據包匯出中心】（防截斷核對 ＋ 右上角一鍵複製）")
    pkg_tab_a, pkg_tab_b = st.tabs(["🛡️ 按鈕 A：守門員快篩批次包（自動切批 5～6 檔）", "🔬 按鈕 B：決選深檢戰情包（1～3 檔＋持倉分帳＋季報防墊料）"])

    with pkg_tab_a:
        if not scan_data:
            st.info("⚡ 目前為【Layer 1 秒開模式】，尚未載入全台 170 檔個股技術掃描池，以節省開盤等待時間。")
            if st.button("🚀 點此啟動全台個股掃描 (Layer 2) 並解鎖模式 A 批次包", type="primary", key="btn_l2_tab1_a"):
                _run_layer2_scan()
                st.rerun()
        else:
            pool_map = {
                f"🔥 短線表1-A：有基之彈旱地拔蔥 ({len(df_launch_fund)}檔)": df_launch_fund["代碼"].tolist() if not df_launch_fund.empty else [],
                f"🎭 短線表1-B：純技術旱地拔蔥 ({len(df_launch_pure)}檔)": df_launch_pure["代碼"].tolist() if not df_launch_pure.empty else [],
                f"🎯 短線表二：N字洗盤低接 ({len(df_pullback)}檔)": df_pullback["代碼"].tolist() if not df_pullback.empty else [],
                f"🛡️ 短線表三：上升軌道推升 ({len(df_orbit)}檔)": df_orbit["代碼"].tolist() if not df_orbit.empty else [],
                f"🏛️ 長線表3-A：核心多頭權值 ({len(df_long_bc)}檔)": df_long_bc["代碼"].tolist() if not df_long_bc.empty else [],
                f"🔄 長線表3-B：權值超跌回測與轉機 ({len(df_long_turn)}檔)": df_long_turn["代碼"].tolist() if not df_long_turn.empty else [],
                f"🌐 本季 MSCI 新增主流股 ({len(df_msci_audit[df_msci_audit['MSCI異動別'].str.contains('新增', na=False)]) if not df_msci_audit.empty else 0}檔)": df_msci_audit[df_msci_audit["MSCI異動別"].str.contains("新增", na=False)]["代碼"].tolist() if not df_msci_audit.empty else [],
                f"🌾 宏爺三大新穀倉＆防彈衣 ({len(df_granary_audit[~df_granary_audit['代碼'].isin(OLD_GRANARY_ODM_WATCH)]) if not df_granary_audit.empty else 0}檔)": df_granary_audit[~df_granary_audit["代碼"].isin(OLD_GRANARY_ODM_WATCH)]["代碼"].tolist() if not df_granary_audit.empty else []
            }
            pool_keys = list(pool_map.keys())
            default_idx = next((i for i, k in enumerate(pool_keys) if len(pool_map[k]) > 0), 0)

            pa_c1, pa_c2, pa_c3, pa_c4 = st.columns([2.6, 1.1, 1.8, 1.8])
            sel_pool_k = pa_c1.selectbox("📂 選擇快篩來源清單", pool_keys, index=default_idx, key="mode_a_pool")
            batch_sz = pa_c2.radio("每批上限", [5, 6], horizontal=True, key="mode_a_bs")
            pool_codes = list(dict.fromkeys([str(x) for x in pool_map[sel_pool_k] if str(x) in all_scanned_details]))

            if pool_codes:
                n_batches = (len(pool_codes) + batch_sz - 1) // batch_sz
                batch_opts = [f"第 {i+1} 批 ({i*batch_sz+1}~{min((i+1)*batch_sz, len(pool_codes))} 檔)" for i in range(n_batches)]
                sel_b_lbl = pa_c3.selectbox("📑 自動切批切換", batch_opts, key="mode_a_batch")
                b_idx = batch_opts.index(sel_b_lbl)
                batch_codes = pool_codes[b_idx * batch_sz : (b_idx + 1) * batch_sz]
                fetch_a_tdcc = pa_c4.checkbox("📡 補抓本批 FinMind 大戶週變動", value=False, key="mode_a_tdcc")

                custom_a = st.multiselect(
                    "✏️ 可微調本批匯出標的（上限 6 檔）：",
                    options=pool_codes, default=batch_codes, max_selections=6,
                    format_func=lambda c: f"{c} {all_scanned_details[c]['_raw_name']}",
                    key=f"mode_a_ms_{sel_pool_k}_{b_idx}"
                )
                final_a_codes = custom_a if custom_a else batch_codes
                live_tdcc_a = fetch_tdcc_weekly_delta_ondemand(tuple(final_a_codes)) if fetch_a_tdcc else {}
                mode_a_txt = build_mode_a_guard_package_v244(final_a_codes, sel_pool_k.split(" (")[0], sel_b_lbl, all_scanned_details, outpost_line, now_str, live_tdcc_a)
                st.code(mode_a_txt, language="text")
            else:
                st.info("目前選擇的清單無符合標的，請切換至『長線表3-B』、『MSCI』或『宏爺穀倉』清單匯出！")

    with pkg_tab_b:
        if all_scanned_details:
            all_opt_codes = sorted(all_scanned_details.keys(), key=lambda c: (0 if c in ["3711", "6239", "2330", "2884", "2881", "2454"] else 1, c))
            default_b = [c for c in ["3711", "6239"] if c in all_opt_codes][:2]
            pb_c1, pb_c2 = st.columns([3.2, 2.0])
            sel_b_codes = pb_c1.multiselect(
                "🎯 勾選決選標的（最多 3 檔；含庫存股會自動帶入成本與獨立停損）：",
                options=all_opt_codes, default=default_b, max_selections=3,
                format_func=lambda c: f"{c} {all_scanned_details[c]['_raw_name']} ({all_scanned_details[c]['現價']})",
                key="mode_b_ms"
            )
            manual_b_str = pb_c2.text_input("⌨️ 或手動輸入 4 碼代號（逗號分隔，上限3檔）", value="", placeholder="例：3711, 2881, 2383", key="mode_b_manual")
        else:
            st.caption("💡 尚未執行 Layer 2 全市場掃描時，可直接於下方輸入 1～3 檔代號生成基礎深檢包（含季報防墊料、大戶週變動與大盤前哨）；若需完整 6 條均線與 5 組量化指標，請先點擊上方【🚀 啟動全台個股池】。")
            sel_b_codes = []
            manual_b_str = st.text_input("⌨️ 直接輸入 1～3 檔 4 碼代號（逗號分隔）", value="3711, 6239", placeholder="例：3711, 2881, 2383", key="mode_b_manual_fast")

        manual_b_codes = [c for c in re.findall(r"\b(\d{4})\b", manual_b_str)][:3]
        target_b_codes = manual_b_codes if manual_b_codes else sel_b_codes

        if st.button("⚡ 生成【淘金客 v24.4 模式 B 深檢戰情包】（即時補抓季報防墊料 ＋ 大戶週變動）", type="primary", key="btn_gen_mode_b"):
            if target_b_codes:
                with st.spinner(f"正在抓取 {', '.join(target_b_codes)} 之季報存貨/應收/現金流與集保大戶週變動..."):
                    live_tdcc_b = fetch_tdcc_weekly_delta_ondemand(tuple(target_b_codes))
                    is_yr = radar["master_state"] in ["YELLOW", "RED_LOCKED"]
                    st.session_state["mode_b_pkg_txt"] = build_mode_b_deep_package_v244(
                        target_b_codes, all_scanned_details, price_dict_active, outpost_line, now_str, live_tdcc_b, holding_notes_map, is_yr
                    )
            else:
                st.warning("請先勾選或輸入 1～3 檔個股代號！")

        if "mode_b_pkg_txt" in st.session_state and st.session_state["mode_b_pkg_txt"]:
            st.code(st.session_state["mode_b_pkg_txt"], language="text")

    # 動態股災週期走勢圖
    st.markdown(build_dynamic_crash_cycle_svg_html(fleet, radar, etf_m), unsafe_allow_html=True)

    # 盤面四大監控卡片 (Card 1 自動依 Layer 2 是否啟動切換呈現)
    up_sec_cards = format_sector_bold_html(us_data["top_up_sectors"], is_up=True)
    dn_sec_cards = format_sector_bold_html(us_data["bot_dn_sectors"], is_up=False)
    if scan_data:
        card1_body = f"{tw_hot_sec_info['hot_html']}{tw_hot_sec_info['cold_html']}"
        card1_title = f"1. 🔥 台股當日抗跌/熱門爆量族群雷達（已於 {scan_data['ts']} 完成掃描）"
    else:
        card1_title = "1. 🔥 台股全市場廣度速覽（熱門爆量族群須啟動 Layer 2）"
        card1_body = (
            f"• <b>全市場漲跌家數</b>：上漲 <b style='color:#ff8a80;'>{breadth['up_cnt']:,} 家</b> ／ 下跌 <b style='color:#69f0ae;'>{breadth['dn_cnt']:,} 家</b>（上漲比 <b>{breadth['adv_ratio_pct']:.1f}%</b>）<br>"
            f"• <b>極端鎖死家數</b>：漲停 <b style='color:#ff8a80;'>{sb_limit_up} 家</b> ／ 跌停 <b style='color:#69f0ae;'>{sb_limit_dn} 家</b><br>"
            f"• 💡 點擊頂部或分頁三～五之<b>【🚀 啟動全台個股池 (Layer 2)】</b>按鈕，即可展開台股 15 大熱門爆量族群領頭羊！"
        )

    st.markdown(clean_html(f"""
    <div class="war-hero-box" style="border-left-color:#38bdf8;">
    <div class="war-dim-grid">
    <div class="war-dim-item" style="border-left:5px solid #ff4d4f;">
    <span class="war-dim-concl">{card1_title}</span>
    {card1_body}
    </div>
    <div class="war-dim-item" style="border-left:5px solid #f59e0b;">
    <span class="war-dim-concl">2. 🌎 美股 VIX / 費半 / ADR 與領漲領跌板塊：{us_data['session_tag']}</span>
    • <b>美股恐慌與債匯</b>：VIX <b>{vix_now:.2f}</b>({vix_chg:+.1f}%) ｜ 美債10Y <b>{tnx_val:.2f}%</b>({tnx_chg:+.2f}%) ｜ 台幣 <b>{fx_now:.3f}</b>({'止貶' if fx_stopped_dep else '續貶'})<br>
    • <b>AI 核心連動</b>：費半 <b style="color:{sox_c};">{sox_chg:+.2f}%</b> ｜ 台積ADR <b style="color:{tsm_c};">{tsm_chg:+.2f}%{tsm_ext}</b> ｜ 輝達 <b style="color:{nvda_c};">{nvda_chg:+.2f}%{nvda_ext}</b>
    {up_sec_cards}
    {dn_sec_cards}
    </div>
    <div class="war-dim-item" style="border-left:5px solid #00e676;">
    <span class="war-dim-concl">3. 🛡️ 真浮動融資曲線與維持率監控（{deriv['margin_src']}）</span>
    • <b>動態維持率</b>：<b style="color:#ffd54f; font-size:16.5px;">{sb_maint:.1f}%</b>（整戶破150% / 純上市破135% 進入斷頭回升檢核）<br>
    • <b>上市融資餘額</b>：最新 <b>{deriv['margin_balance_yi']:,.1f} 億</b>（單日 <b>{deriv['margin_chg_yi']:+.1f}億</b> ｜ 近2日 <b>{sb_margin_2d:+.1f}億 / {sb_margin_2d_pct:+.1f}%</b> ｜ 30日減幅 <b>-{sb_margin_dd:.1f}%</b>）<br>
    • <b>市場極端廣度</b>：漲停 <b>{sb_limit_up} 家</b> ／ 跌停 <b>{sb_limit_dn} 家</b>（跌停 &lt;20 家為收斂達標）
    </div>
    <div class="war-dim-item" style="border-left:5px solid #ffd54f;">
    <span class="war-dim-concl">4. 📊 期權籌碼、逆價差與救市防呆（{deriv['fut_src']}）</span>
    • <b>大台期貨 OI</b>：外資淨未平倉 <b>{sb_fini_oi:+,} 口</b>（連補 {sb_consec_cover}日 / 3日累計 {sb_cover_3d:+,}口）｜ 投信 <b>{deriv['sitc_tx_oi']:+,} 口</b><br>
    • <b>期現價差與期權</b>：逆價差 <b>{sb_basis:+.0f}點 ({radar['basis_rate_pct']:.2f}%)</b> ｜ PCR <b>{deriv['pc_ratio']:.1f}%</b> ｜ 自營商避險 <b>{deriv['warrant_hedge_yi']:+.2f} 億</b><br>
    • <b>救市追蹤</b>：限空令起始日 <b>{sb_ban_date}</b>（已過 {max(0, days_since_ban)} 天｜連站10MA {fleet['twii_consec_above_ma10']} 日）
    </div>
    </div>
    </div>
    """), unsafe_allow_html=True)

    st.markdown("#### 🧭 【大盤各級均線動態城堡導航表】（含月 20MA 與月 36MA 股災鐵板）")
    def ma_row(nm, val, guide):
        diff = round(val - fleet["twii_now"], 0)
        pct = round(diff / max(1.0, fleet["twii_now"]) * 100.0, 2)
        pos = f"上方 +{diff:,.0f}點 (+{pct:.2f}%)" if diff >= 0 else f"✅下方支撐 {diff:,.0f}點 ({pct:.2f}%)"
        return {"均線週期": nm, "動態點位 (距現價)": f"{val:,.0f} 點 ({pos})", "實戰意義": guide}

    st.dataframe(pd.DataFrame([
        ma_row("① 日 20MA (月線・波段反壓)", fleet["ma20"], "下彎蓋頭強壓，反彈至此區宜調節 00400A 與弱勢股"),
        ma_row("② 日 240MA (年線・多空生命線)", fleet["ma240"], "跌破觸發股災警戒；未站回前反彈皆視為空頭修正反彈"),
        ma_row("③ 日 10MA (政策逃命波驗證線)", fleet["ma10"], "限空令後須連 3 日站穩 10MA 且落底達 4/5 才脫離逃命波"),
        ma_row("④ 日 5MA (五日攻擊線)", fleet["ma5"], "短線止跌第一步須重新站上並走平"),
        ma_row("⑤ 月 20MA (第一隻腳核心鐵板)", fleet["m_ma20"], "歷史急殺股災第一道強力支撐區，對接 11/23 限空令 3.0"),
        ma_row("⑥ 月 36MA (極端系統底)", fleet["m_ma36"], "若美股引發二次崩跌之終極防禦底線")
    ]), use_container_width=True, hide_index=True)

# ==================== TAB 2: 換股計算機、修正版短單分帳與四水庫 (Layer 1 秒開直接完整顯示) ====================
with tab2:
    st.markdown("#### ⚡ 【模組三之一：淨降倉換強短單獨立分帳停損監控（修正版：買於5MA下先守平衡價，站上5MA後啟動移動停利）】")
    if sb_shares_3711_short > 0:
        short_pnl = round((p_3711 - sb_short_3711_be) * sb_shares_3711_short, 0)
        short_pnl_pct = round(((p_3711 - 474.0) / 474.0) * 100.0, 2)
        trailing_ma5_active = bool(sb_short_3711_above_ma5 or (p_3711 >= ma5_3711))
        hit_be = (p_3711 < sb_short_3711_be)
        hit_ma5_trail = trailing_ma5_active and (p_3711 < ma5_3711)
        trigger_exit = hit_be or hit_ma5_trail
        sl_cls = "short-ledger-stop" if trigger_exit else "short-ledger-ok"

        if trigger_exit:
            reason_s = f"跌破損益平衡價 ({sb_short_3711_be:.2f})" if hit_be else f"站上後回破 5MA ({ma5_3711:.2f})"
            sl_status = f"🚨 【觸發獨立短單停損撤退！】現價 {p_3711:.1f} 已{reason_s} ➔ 嚴守 v24.4 紀律 5 立即撤退，絕不變長單！"
            active_line_str = f"硬停損底線 = {sb_short_3711_be:.2f} 元"
        elif trailing_ma5_active:
            active_line_str = f"已啟動【破 5MA 移動停利防線 = {ma5_3711:.2f} 元】（硬底線 {sb_short_3711_be:.2f} 元）"
            sl_status = f"✅ 【短單強勢續抱・移動停利中】現價 {p_3711:.1f} 站穩 5MA ({ma5_3711:.2f}) 之上，沿 5MA 抱緊波段！"
        else:
            active_line_str = f"階段一防守：【先守損益平衡價 = {sb_short_3711_be:.2f} 元】（待突破下彎 5MA {ma5_3711:.2f} 元後再切換為破 5MA 停利）"
            sl_status = f"✅ 【短單安全續抱中】因建倉(@474)位於下彎 5MA 下方，目前穩守損益平衡價 {sb_short_3711_be:.2f} 元之上，未觸發停損！"

        st.markdown(clean_html(f"""
        <div class="{sl_cls}">
        <b>🎯 11/23 淨降倉換強短單：日月光投控 (3711) 盤中零股 {sb_shares_3711_short:,} 股（成交均價 474.00 元 ｜ 持有成本 142,402 元）</b><br>
        • <b>即時損益與防線</b>：現價 <b>{p_3711:.2f} 元</b>（帳面預估 <b>{short_pnl:+,.0f} 元 / {short_pnl_pct:+.2f}%</b>）｜ <b>{active_line_str}</b><br>
        • <b>即時紀律裁決</b>：<b style="font-size:16px;">{sl_status}</b><br>
        • <i>（註：此筆 300 股與水庫二傳家寶現股 3,000 股 @172.67 完全分帳管理，絕不混帳攤平）</i>
        </div>
        """), unsafe_allow_html=True)
    else:
        st.info("目前無進行中之換強短單零股部位。")

    st.markdown("#### ⚖️ 【模組三之二：通用雙股比價換龍頭計算機（對接 v24.4 折價 10%～15% 豁免門檻）】")
    sw_p1, sw_p2, sw_p3 = st.columns([2.2, 1.4, 1.4])
    swap_preset = sw_p1.selectbox(
        "🔄 選擇比價換股組合",
        ["🏦 玉山金(2884) 換 富邦金(2881) [金融換龍頭]", "🔬 力成(6239) 換 日月光(3711) [封測剩餘1張換龍頭]", "⚙️ 自訂任意兩檔比價"],
        key="swap_preset_sel"
    )
    c_sell_def, c_buy_def = ("2884", "2881") if "玉山金" in swap_preset else (("6239", "3711") if "力成" in swap_preset else ("2884", "2881"))
    c_sell = sw_p2.text_input("賣出股代號 (弱/次龍頭)", value=c_sell_def, key="sw_cs").strip()
    c_buy = sw_p3.text_input("買入目標代號 (龍頭)", value=c_buy_def, key="sw_cb").strip()

    if len(c_sell) == 4 and len(c_buy) == 4:
        inf_s, inf_b = price_dict_active.get(c_sell, {}), price_dict_active.get(c_buy, {})
        nm_s, mkt_s = inf_s.get("name", c_sell), inf_s.get("market", "TW")
        nm_b, mkt_b = inf_b.get("name", c_buy), inf_b.get("market", "TW")
        ps_live = get_live_p(c_sell, inf_s.get("close", 100.0) or 100.0)
        pb_live = get_live_p(c_buy, inf_b.get("close", 100.0) or 100.0)
        pair_stat = fetch_pair_swap_history(c_sell, mkt_s, c_buy, mkt_b, ps_live, pb_live)

        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric(f"賣出：{nm_s} ({c_sell})", f"{ps_live:.2f} 元")
        sc2.metric(f"買入：{nm_b} ({c_buy})", f"{pb_live:.2f} 元")
        sc3.metric("當下換股比 (賣幾張換1張龍頭)", f"{pair_stat['curr_ratio']:.3f} 張", f"240日中位數 {pair_stat['median_ratio']:.3f} 張")
        sc4.metric("相對常態折價幅度", f"{pair_stat['discount_pct']:+.1f}%", "門檻：>= +10% ~ +15%")

        disc_v = pair_stat["discount_pct"]
        if disc_v >= 15.0:
            pair_verdict = f"💎 【超值換龍頭綠燈！】相對 240 日常態比折價達 +{disc_v:.1f}% (≧15%)，符合 v24.4 紅燈豁免 (b)，確認龍頭無治理/基本面惡化後可同曝險換龍頭！"
        elif disc_v >= 10.0:
            pair_verdict = f"🟢 【達標換龍頭觀察區！】相對常態比折價 +{disc_v:.1f}% (落在 10%~15% 划算區)，可於 09:15 後流動性穩定時分批換手！"
        elif disc_v >= 0.0:
            pair_verdict = f"🟡 【折價尚不足 (+{disc_v:.1f}%)】雖略便宜於常態，尚未達 10% 折價門檻，建議續抱觀望！"
        else:
            pair_verdict = f"🔴 【溢價不划算 ({disc_v:.1f}%)】目前換入龍頭比平常更貴，嚴禁盲目追換！"

        st.markdown(clean_html(f"""
        <div class="swap-card">
        • <b>📐 比價歷史區間（近 240 交易日）</b>：最低 <b>{pair_stat['min_ratio']:.3f} 張</b> ～ 中位數 <b>{pair_stat['median_ratio']:.3f} 張</b> ～ 最高 <b>{pair_stat['max_ratio']:.3f} 張</b> ｜ 目前即時比值：<b>{pair_stat['curr_ratio']:.3f} 張 {nm_s} 換 1 張 {nm_b}</b><br>
        • <b>🚦 v24.4 模組 0/18 比價裁決</b>：<b style="color:#00e676; font-size:15.5px;">{pair_verdict}</b><br>
        • <b>🛡️ 執行鐵律</b>：紅燈期換股避開 <b>09:00-09:10</b> 恐慌低點，改於 <b>09:15 後</b> 同步對敲換手！
        </div>
        """), unsafe_allow_html=True)

    st.markdown("#### 🔄 【模組三之三：00400A 務實滾動退場試算 ＆ 水庫三 (00985B / 00713) 進場殖利率反推】")
    div_985_ttm, div_985_ann = etf_m["00985B"]["div_ttm"], etf_m["00985B"]["div_latest"] * 12.0
    div_713_ttm = etf_m["00713"]["div_ttm"]
    y_985_ttm, y_985_ann = round(div_985_ttm / max(0.1, p_00985b) * 100.0, 2), round(div_985_ann / max(0.1, p_00985b) * 100.0, 2)
    y_713_ttm = round(div_713_ttm / max(0.1, p_00713) * 100.0, 2)
    sell_400a_val = sb_lots_00400a * 1000.0 * p_00400a
    conv_985b_lots = sell_400a_val / max(1.0, p_00985b * 1000.0)
    conv_985b_annual_inc = conv_985b_lots * 1000.0 * div_985_ttm

    st.markdown(clean_html(f"""
    <div class="tab-guide-box" style="border-left-color:#fbbf24;">
    <b>⚠️ 00400A 處置原則（永久停扣・務實滾動退場轉債）</b>：<br>
    • <b>現況與禁買紅線</b>：00400A 現價 <b>{p_00400a:.2f} 元</b>（持 {sb_lots_00400a:.0f} 張 @14.40），12/08 除息已由 0.12 元腰斬至 <b>0.06 元</b>；<b>嚴禁再加碼，亦嚴禁買進 00406A (賣Call陷阱) 與 00953B/00945B (垃圾債)</b>。<br>
    • <b>務實滾動退場區</b>：第一批減碼區 <b style="color:#ffd54f;">{etf_m['00400A']['exit_t1']:.2f} 元</b>（10MA={etf_m['00400A']['ma10']:.2f} 收斂）；第二批主力退場區 <b style="color:#00e676;">{etf_m['00400A']['exit_t2']:.2f} 元</b>（20MA={etf_m['00400A']['ma20']:.2f} 收斂）。<br>
    • <b>即時轉債效益</b>：若將 {sb_lots_00400a:.0f} 張 00400A（約 <b>{sell_400a_val/10000:.1f} 萬元</b>）全數轉入 <b>00985B ({p_00985b:.2f}元)</b>，可換得約 <b>{conv_985b_lots:.1f} 張</b>，每年立刻鎖定約 <b style="color:#00e676;">{conv_985b_annual_inc:,.0f} 元</b>（約 {conv_985b_annual_inc/12:,.0f} 元/月）RR2 低波債息！
    </div>
    """), unsafe_allow_html=True)

    st.dataframe(pd.DataFrame([
        {
            "水庫三核心標的": "00985B 群益ESG投等債0-5 (月配65%)", "即時現價": f"{p_00985b:.2f} 元",
            "近一年/最新年化配息": f"TTM {div_985_ttm:.3f}元 / 年化 {div_985_ann:.3f}元",
            "目前殖利率": f"TTM {y_985_ttm:.2f}% / 年化 {y_985_ann:.2f}%",
            "💎 超值重錘區": f"(≧6.0%) <= {div_985_ttm/0.06:.2f} ~ {div_985_ann/0.06:.2f} 元",
            "✅ 合理建倉區": f"(5.5%~6.0%) {div_985_ann/0.06:.2f} ~ {div_985_ann/0.055:.2f} 元",
            "⚖️ 底線進場價": f"(≧5.0%) <= {div_985_ttm/0.05:.2f} ~ {div_985_ann/0.05:.2f} 元",
            "當前買點判定": "💎 超值建倉區！" if y_985_ann >= 6.0 else ("✅ 合理建倉區" if y_985_ann >= 5.5 else "⚖️ 達5%底線")
        },
        {
            "水庫三核心標的": "00713 元大台灣高息低波 (季配35%)", "即時現價": f"{p_00713:.2f} 元",
            "近一年/最新年化配息": f"TTM {div_713_ttm:.2f}元", "目前殖利率": f"TTM {y_713_ttm:.2f}%",
            "💎 超值重錘區": f"(≧7.0%) <= {div_713_ttm/0.07:.2f} 元",
            "✅ 合理建倉區": f"(6.0%~7.0%) {div_713_ttm/0.07:.2f} ~ {div_713_ttm/0.06:.2f} 元",
            "⚖️ 底線進場價": f"(≧5.5%) <= {div_713_ttm/0.055:.2f} 元",
            "當前買點判定": "💎 超值重錘區" if y_713_ttm >= 7.0 else ("✅ 合理建倉區！" if y_713_ttm >= 6.0 else "⚖️ 觀察區")
        }
    ]), use_container_width=True, hide_index=True)

    st.markdown("#### 💧 【模組三之四：11/23 最新全資產四水庫市值、半導體集中度與被動現金流進度】")
    r_c1, r_c2, r_c3, r_c4 = st.columns(4)
    r_c1.metric("💰 全資產即時估值 (零槓桿)", f"{total_assets/10000:,.1f} 萬元", f"匯率 {fx_now:.3f}")
    r_c2.metric("🔬 半導體總曝險佔比", f"{semi_ratio_pct:.1f}%", "⚠️ >50% 須靠水庫三平衡" if semi_ratio_pct > 50 else "✅ 配置均衡", delta_color="inverse")
    r_c3.metric("🌊 水庫三累積進度 (目標200萬)", f"{val_r3/10000:,.1f} / 200 萬", f"00985B {sb_lots_00985b:.0f}張 ({val_r3/2000000*100:.1f}%)")
    r_c4.metric("🔫 水庫四機動彈藥 (SGOV+現金)", f"{val_r4/10000:,.1f} 萬元", f"含台幣現金 {sb_twd_cash_wan:.1f}萬 + USD")

    st.dataframe(pd.DataFrame([
        {"水庫分類": "水庫一：生活＋急用雙水庫 (永不動用)", "核心組成": "日常 20萬 ＋ 急用定存 20萬 (撐9個月開銷)", "即時市值(萬)": round(val_r1/10000, 1), "佔比(%)": round(val_r1/total_assets*100, 1)},
        {"水庫分類": "水庫二：傳家寶底倉 — 台股半導體", "核心組成": f"台積電4張(@{p_2330:,.0f}) ＋ 日月光3張(@{p_3711:,.0f})", "即時市值(萬)": round(val_r2_tw_semi/10000, 1), "佔比(%)": round(val_r2_tw_semi/total_assets*100, 1)},
        {"水庫分類": "水庫二：傳家寶底倉 — 台股金融股息", "核心組成": "玉山金100.45張、富邦金11.74張、台中銀27.15張、台新新光金/臺企銀", "即時市值(萬)": round(val_r2_tw_fin/10000, 1), "佔比(%)": round(val_r2_tw_fin/total_assets*100, 1)},
        {"水庫分類": "水庫二：傳家寶底倉 — 美股 AI 龍頭", "核心組成": f"AVGO 35股(${p_avgo:.2f}) ＋ NVDA 80股(${p_nvda:.2f})", "即時市值(萬)": round(val_r2_us_semi/10000, 1), "佔比(%)": round(val_r2_us_semi/total_assets*100, 1)},
        {"水庫分類": "水庫三：第二道現金流 (目標200萬)", "核心組成": f"00985B {sb_lots_00985b:.1f}張(@10.38) ＋ 00713 {sb_lots_00713:.1f}張", "即時市值(萬)": round(val_r3/10000, 1), "佔比(%)": round(val_r3/total_assets*100, 1)},
        {"水庫分類": "水庫四：跨市機動彈藥 (含11/23淨收回現金)", "核心組成": f"台幣機動現金 {sb_twd_cash_wan:.1f}萬 ＋ SGOV 30股(${p_sgov:.2f}) ＋ ${sb_usd_cash:,.0f}", "即時市值(萬)": round(val_r4/10000, 1), "佔比(%)": round(val_r4/total_assets*100, 1)},
        {"水庫分類": "獨立分帳：淨降倉換強短單", "核心組成": f"日月光零股 {sb_shares_3711_short}股(@474 / 平衡價{sb_short_3711_be:.2f})", "即時市值(萬)": round(val_short_3711/10000, 1), "佔比(%)": round(val_short_3711/total_assets*100, 1)},
        {"水庫分類": "待汰弱與退場衛星部位", "核心組成": f"力成{sb_lots_6239:.1f}張(@245.75) ＋ 欣銓{sb_lots_3264:.1f}張 ＋ 00400A {sb_lots_00400a:.0f}張", "即時市值(萬)": round((val_sat_semi+val_sat_400a)/10000, 1), "佔比(%)": round((val_sat_semi+val_sat_400a)/total_assets*100, 1)}
    ]), use_container_width=True, hide_index=True)

    with st.expander("📦 次要收合：0050／0050正2 紀錄（目前已全數清空 0 張）", expanded=False):
        st.caption(f"0050正2 (00631L) 現價：{etf_m['00631L']['p']:.2f} 元（5MA={etf_m['00631L'].get('ma5', 0):.2f} / 20MA={etf_m['00631L'].get('ma20', 0):.2f}）｜ 已全數清空，依模組 0 雙雷達控管。")

# ==================== TAB 3: 短線旱地拔蔥榜 (Layer 2 按需啟動) ====================
with tab3:
    if not scan_data:
        st.info("💤 【Layer 2 按需載入待機中】為縮短開啟等待時間，全台 170 檔非殭屍股 K 線與 5 組接軌指標將於您點擊下方按鈕後才開始掃描（耗時約 12～18 秒，掃完後本頁與分頁四、五共用免重跑）。")
        if st.button("🚀 立即啟動全台個股掃描 (Layer 2)", type="primary", key="btn_l2_tab3"):
            _run_layer2_scan()
            st.rerun()
    else:
        if radar["master_state"] == "RED_LOCKED":
            st.error(f"🚨 【淘金客 v24.4 紅燈鎖定（預警 {radar['w_count']}/5・落底 {radar['b_count']}/5）】嚴禁動用新現金追買！已自動排除「ADX>25 且 -DI>+DI」空頭加速股與「BIAS10 > +12%」極度過熱股。")
        elif radar["master_state"] == "YELLOW":
            st.warning("🟡 【黃燈前哨警戒】禁止追高並優先收網多頭衛星股，僅限新穀倉1／防彈衣且無3日事件雷者小額試單。")
        else:
            st.success(f"{radar['master_badge']} ｜ 已於 {scan_data['ts']} 完成全台掃描，請至 Tab 1 匯出模式 A 批次包給淘金客 v24.4 守門！")

        st.markdown("#### 🔥 【表 1-A：短線看技術＋基本面（站穩月線＋DMI多方＋雙成長）】")
        if not df_launch_fund.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_launch_fund)), use_container_width=True)
        else: st.info("今日盤面暫無同時符合站穩月線、DMI多方、非過熱與營收雙成長之真旱地拔蔥標的。")

        st.markdown("#### 🎭 【表 1-B：短線純看技術面（不看基本面・主力籌碼旱地拔蔥榜）】")
        if not df_launch_pure.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_launch_pure)), use_container_width=True)
        else: st.info("今日暫無純技術點火標的。")

        st.markdown("#### 🎯 【表二：N字洗盤蓄勢 ＆ 拉回月線/十日線低接榜】")
        if not df_pullback.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_pullback)), use_container_width=True)
        else: st.info("今日暫無量縮回測月線有守之標的。")

        st.markdown("#### 🛡️ 【表三：上升軌道推升 ＆ 3日急拉過熱警戒榜】")
        if not df_orbit.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_orbit)), use_container_width=True)
        else: st.info("今日暫無多頭軌道推升標的。")

# ==================== TAB 4: 長線波段與 MSCI 動態主流池 (Layer 2 按需啟動) ====================
with tab4:
    st.markdown(f"#### 🌐 【模組四：MSCI 每季動態納入/移除名單 × 受影響族群龍頭與當日熱門股連動】（當前季度：`{msci_data['quarter']}`）")
    with st.expander("🔄 每季公告日一鍵更新：貼上最新 MSCI 季度調整公告文字，3 秒自動解析並永久儲存", expanded=False):
        q_lbl = st.text_input("新季度標籤", value="2027/02 (季度調整)")
        raw_msci_txt = st.text_area("貼上新聞中的『標準指數新增/刪除、小型指數新增/刪除』段落（含 4 碼代號）：", height=110)
        if st.button("💾 解析並更新為最新季度 MSCI 名單"):
            if raw_msci_txt.strip():
                parsed_m = parse_msci_text_override(raw_msci_txt, price_dict_active, q_lbl)
                try:
                    with open(MSCI_CACHE_FILE, "w", encoding="utf-8") as f: json.dump(parsed_m, f, ensure_ascii=False)
                    fetch_dynamic_msci_data.clear()
                    st.session_state.pop("v62_scan_data", None)
                    st.success("✅ 已解析並儲存最新季度 MSCI 名單！請點下方按鈕重新掃描套用。")
                except Exception as e:
                    st.error(f"儲存失敗：{e}")

    if not scan_data:
        st.info("💤 啟動 Layer 2 全台個股掃描後，即可檢視 MSCI 族群龍頭連動表、MSCI 完整 5 組指標表及長線波段表 3-A／3-B。")
        if st.button("🚀 立即啟動全台個股掃描 (Layer 2)", type="primary", key="btn_l2_tab4"):
            _run_layer2_scan()
            st.rerun()
    else:
        if radar["master_state"] == "RED_LOCKED":
            st.warning("🚨 紅燈鎖定中：表 3-B 與 MSCI 清單保留顯示但已強制標註「空頭加速」與「ATR%高波動」，請待落底共振達 4/5 再解封新資金分 3 批存股！")

        scanned_recs = scan_data["sec_recs"]
        theme_link_rows = []
        if not df_msci_audit.empty:
            for th_name, grp in df_msci_audit.groupby("影響細分族群"):
                adds = [f"{r['代碼']}{r['股名'].split(' ')[0]}({r['今日%']:+.1f}%)" for _, r in grp.iterrows() if "新增" in str(r["MSCI異動別"])]
                dels = [f"{r['代碼']}{r['股名'].split(' ')[0]}({r['今日%']:+.1f}%)" for _, r in grp.iterrows() if "刪除" in str(r["MSCI異動別"])]
                bell_list = MSCI_THEME_BELLWETHERS.get(th_name, [])
                bells_str = "、".join([f"{bn}({bc}) {price_dict_active.get(bc,{}).get('chg_pct',0):+.1f}%" for bc, bn in bell_list]) or "依盤面領漲股"
                same_th_recs = [x for x in scanned_recs if (infer_msci_theme(x["code"], x["name"], x["official_sec"]) == th_name)]
                same_th_recs.sort(key=lambda x: (x["chg_1d"], x["yod_ratio"]), reverse=True)
                hot_in_th = "、".join([f"{x['name']}({x['code']}) {x['chg_1d']:+.1f}%[量{x['yod_ratio']:.1f}x]" for x in same_th_recs[:3]]) or "當日量縮整理"
                theme_link_rows.append({
                    "受影響細分族群": th_name, "📈 本季 MSCI 納入受惠": "、".join(adds) if adds else "➖",
                    "📉 本季 MSCI 剔除承壓": "、".join(dels) if dels else "➖",
                    "👑 族群指標龍頭股": bells_str, "🔥 當日族群盤中熱門前3強": hot_in_th
                })
            st.dataframe(pd.DataFrame(theme_link_rows), use_container_width=True, hide_index=True)

        st.markdown("##### 📋 【本季 MSCI 完整納入/移除標的即時掃描表（含 5 組新指標與處置股警示）】")
        if not df_msci_audit.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_msci_audit)), use_container_width=True)

        st.markdown("#### 🏛️ 【表 3-A：核心權值與政策防彈衣 長線多頭波段榜】")
        if not df_long_bc.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_long_bc)), use_container_width=True)
        else: st.info("大盤修正期間暫無同時站上月線與 DMI 多方之權值股，請看下方表 3-B。")

        st.markdown("#### 🔄 【表 3-B：權值股超跌回測支撐買點 ＆ 轉機題材榜】")
        if not df_long_turn.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_long_turn)), use_container_width=True)
        else: st.info("目前暫無符合條件之回測支撐買點。")

# ==================== TAB 5: 穀倉與防墊料專區 (Layer 2 按需啟動) ====================
with tab5:
    st.markdown("#### 🌾 【宏爺講股：三大新穀倉 vs. 🛡️政策內需防彈衣 vs. 🐀最後一隻老鼠（防墊料壓貨）全景體檢專區】")
    st.markdown(clean_html("""
    <div class="tab-guide-box">
    • <b>🌟 新穀倉 1（CSP自研/共通規格 +15~23分）</b>：台積電、日月光投控、世芯-KY、創意、聯發科、穎崴、旺矽、台光電、金像電。<br>
    • <b>⚡ 新穀倉 2（解決缺電/過熱基建 +10~14分）</b>：台達電、光寶科、奇鋐、雙鴻、健策、順達、華城、士電。<br>
    • <b>📱 新穀倉 3（邊緣AI與記憶體封測 +5分）</b>：大立光、玉晶光、鴻海、力成、聯詠。<br>
    • <b>🛡️ 政策內需防彈衣（兆元水電工程/官股金控 +8~18分）</b>：中興電、亞力、國統、中鼎、亞翔、兆豐金、中信金、第一金。<br>
    • <b>🐀 防墊料濾網（扣減 -20分）</b>：硬體組裝 ODM 若月營收 YoY &gt; 0 但季報存貨或應收帳款 QoQ &gt;= 20% 且營業現金流為負，強制標記警示。
    </div>
    """), unsafe_allow_html=True)

    if not scan_data:
        st.info("💤 點擊下方按鈕啟動 Layer 2 掃描，即可載入三大新穀倉戰力排行與舊穀倉 ODM 存貨防墊料體檢表。")
        if st.button("🚀 立即啟動全台個股掃描 (Layer 2)", type="primary", key="btn_l2_tab5"):
            _run_layer2_scan()
            st.rerun()
    elif not df_granary_audit.empty:
        df_g_safe = df_granary_audit[~df_granary_audit["代碼"].isin(OLD_GRANARY_ODM_WATCH)].copy()
        df_g_odm = df_granary_audit[df_granary_audit["代碼"].isin(OLD_GRANARY_ODM_WATCH)].copy()
        st.markdown("##### 🌟 【一、三大新穀倉 ＆ 🛡️ 政策內需防彈衣 即時戰力排行（含 5 組量化接軌指標）】")
        st.dataframe(style_tw_stock_df(pin_code_name_index(df_g_safe)), use_container_width=True)
        st.markdown("##### 🐀 【二、最後一隻老鼠（舊穀倉純伺服器組裝 ODM：存貨/應收帳款 QoQ ＆ 營業現金流防墊料體檢表）】")
        if not df_g_odm.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_g_odm)), use_container_width=True)

# ==================== TAB 6: 上市櫃月營收快篩 (Layer 3 獨立按需啟動) ====================
with tab6:
    target_latest_ym = get_target_revenue_ym_list()[0][2]
    st.markdown(f"#### 📈 【上市櫃每月營收動態撈取 ＆ 三級除殭屍股快篩專區】（最新目標年月：`{target_latest_ym}`）")

    rev_b1, rev_b2 = st.columns([2.5, 4.5])
    if rev_b1.button("📊 啟動／重抓 1,972 家上市櫃月營收與三率估值 (Layer 3)", type="primary", key="btn_l3_rev"):
        with st.spinner("📡 Layer 3 執行中：正在下載全市場 1,972 家最新月營收、法人買賣超與三率 EPS 估值..."):
            st.session_state["v62_rev_df"] = fetch_all_monthly_revenue_df_on_demand()
            st.rerun()

    df_all_rev = st.session_state.get("v62_rev_df", pd.DataFrame())
    if df_all_rev.empty:
        rev_b2.info("💤 【Layer 3 按需載入待機中】點左側按鈕才爬取 1,972 家月營收與財報，避免拖慢每日開盤速度。")
    else:
        rev_b2.success(f"✅ 已載入全市場 {len(df_all_rev):,} 家上市櫃月營收與三大法人估值資料！")
        f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns([1.6, 1.4, 0.9, 0.9, 2.8])
        search_kw = f_col1.text_input("🔍 搜尋代碼/名稱/備註", value="", placeholder="例如：3711、6239、3189...")
        mkt_filter = f_col2.selectbox("🏛️ 市場與籌碼篩選", [f"全部市場 (含{target_latest_ym}與上月)", f"⚡ 僅看已公告最新月 ({target_latest_ym})", "🔥 僅看法人買超 (外資>0 或 投信>0)", "🏦 僅看投信買超認養股", "🔥 僅看含 CB 可轉債標的", "僅看上市", "僅看上櫃"])
        min_yoy = f_col3.number_input("📊 最低單月YoY(%)", value=20.0, step=5.0, format="%.1f")
        min_mom = f_col4.number_input("📈 最低月增MoM(%)", value=0.0, step=5.0, format="%.1f")
        quick_mode = f_col5.selectbox(
            "⚡ 實戰快篩模式",
            [
                "🌱 久旱逢甘霖：前期累計衰退/低迷 ➔ 本月首次爆發 YoY>=20% 轉機股 (排除營建)",
                "👑 玩法1終極獵殺：久旱破繭(YoY>=20%) ＋ 大戶鎖碼(>=50%) ＋ 收紅或法人買超",
                "🔥 玩法3可轉債套利：高成長(YoY>=20%) ＋ 具【🔥含CB可轉債】標的",
                "💎 低估鑽石成長股：單月YoY>=20% 且 現價低於【💎EPS合理切入價】",
                "🚀 有基之彈雙成長：單月YoY>=設定值 且 累計YoY>0% 且 MoM>=設定值",
                "📋 顯示全市場所有公司"
            ]
        )

        z_col1, v_col1, v_col2 = st.columns([2.4, 2.8, 3.8])
        exclude_zombie_rev = z_col1.checkbox("🧹 自動剔除三級制無量殭屍股 (<150元須>=1,000張)", value=True)

        df_filtered = df_all_rev.copy()
        if exclude_zombie_rev and not df_filtered.empty and (not search_kw.strip()):
            vol_mult_now, _ = get_volume_multiplier()
            keep_mask = df_filtered.apply(lambda r: (str(r["代碼"]) in CORE_WATCHLIST) or (not is_zombie_stock_tiered(str(r["代碼"]), r["現價"], float(r.get("成交量(張)", 0) or 0) * vol_mult_now)), axis=1)
            df_filtered = df_filtered[keep_mask].copy()

        if "僅看已公告最新月" in mkt_filter: df_filtered = df_filtered[df_filtered["資料年月"] == target_latest_ym]
        elif "法人買超" in mkt_filter: df_filtered = df_filtered[(df_filtered["外資買賣(張)"] > 0) | (df_filtered["投信買賣(張)"] > 0)]
        elif "投信買超" in mkt_filter: df_filtered = df_filtered[df_filtered["投信買賣(張)"] > 0]
        elif "CB" in mkt_filter: df_filtered = df_filtered[df_filtered["CB可轉債"].str.contains("CB", na=False)]
        elif mkt_filter == "僅看上市": df_filtered = df_filtered[df_filtered["市場"] == "上市"]
        elif mkt_filter == "僅看上櫃": df_filtered = df_filtered[df_filtered["市場"] == "上櫃"]

        if search_kw.strip():
            kw = search_kw.strip()
            df_filtered = df_filtered[df_filtered["代碼"].str.contains(kw, case=False, na=False) | df_filtered["公司名稱"].str.contains(kw, case=False, na=False) | df_filtered["產業別"].str.contains(kw, case=False, na=False) | df_filtered["財報營收備註說明"].str.contains(kw, case=False, na=False)]
        else:
            not_const = ((~df_filtered["產業別"].str.contains("建材營造|營造", na=False)) & (~df_filtered["備註AI真偽鑑別"].str.contains("🏗️", na=False)) & (~df_filtered["代碼"].isin(CONSTRUCTION_BLACKLIST))) | (df_filtered["代碼"].isin(POLICY_DEFENSE_ARMOR))
            if "久旱逢甘霖" in quick_mode: df_filtered = df_filtered[df_filtered["轉機與成長型態"].str.contains("久旱逢甘霖|低迷翻揚", na=False) & (df_filtered["年增YoY%"] >= min_yoy) & (df_filtered["當月營收(億)"].fillna(0) >= 0.20) & not_const]
            elif "玩法1" in quick_mode: df_filtered = df_filtered[df_filtered["轉機與成長型態"].str.contains("久旱逢甘霖|低迷翻揚", na=False) & (df_filtered["年增YoY%"] >= min_yoy) & (df_filtered["_tdcc_num"] >= 50.0) & ((df_filtered["今日%"].fillna(-1.0) >= 0.0) | (df_filtered["外資買賣(張)"] > 0) | (df_filtered["投信買賣(張)"] > 0)) & (~df_filtered["備註AI真偽鑑別"].str.contains("⚠️|🏗️", na=False)) & not_const]
            elif "玩法3" in quick_mode: df_filtered = df_filtered[(df_filtered["年增YoY%"] >= min_yoy) & df_filtered["CB可轉債"].str.contains("CB", na=False) & not_const]
            elif "低估鑽石" in quick_mode: df_filtered = df_filtered[(df_filtered["年增YoY%"] >= min_yoy) & df_filtered["估值診斷"].str.contains("低估鑽石", na=False) & not_const]
            elif "有基之彈" in quick_mode: df_filtered = df_filtered[(df_filtered["年增YoY%"] >= min_yoy) & (df_filtered["累計YoY%"].fillna(-1) > 0.0) & (df_filtered["月增MoM%"] >= min_mom) & not_const]

        if v_col1.button("🔬 執行【24個月首次 YoY>=20%】深度驗證", type="primary"):
            with st.spinner(f"併發調閱 FinMind 歷史月營收 (共 {len(df_filtered)} 檔)..."):
                st.session_state["ver_24m_map"] = verify_24m_first_breakout_batch(tuple(df_filtered["代碼"].tolist()), threshold_yoy=min_yoy)

        if "ver_24m_map" in st.session_state and st.session_state["ver_24m_map"]:
            ver_map = st.session_state["ver_24m_map"]
            if any(c in ver_map for c in df_filtered["代碼"]):
                keep_codes = [c for c, val in ver_map.items() if (isinstance(val, tuple) and val[0])]
                df_filtered = df_filtered[df_filtered["代碼"].isin(keep_codes)].copy()
                df_filtered["轉機與成長型態"] = df_filtered["代碼"].map(lambda c: ver_map[c][2])
                v_col2.success(f"✅ 已完成 24 個月破繭驗證！共 {len(df_filtered)} 檔純破繭轉機股。")

        df_display_rev = df_filtered.drop(columns=["_tdcc_num"], errors="ignore")
        csv_bytes = df_display_rev.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button(label=f"📥 下載當前篩選月營收與法人估值表 ({len(df_display_rev)} 家) CSV", data=csv_bytes, file_name=f"tw_rev_v62_{target_latest_ym}.csv", mime="text/csv")

        def color_rev_val(val):
            try:
                v = float(val)
                return "color: #ff3b30; font-weight: bold;" if v > 0 else ("color: #00b853; font-weight: bold;" if v < 0 else "")
            except Exception: return ""

        df_pinned_rev = pin_code_name_index(df_display_rev)
        rev_color_cols = [c for c in ["今日%", "外資買賣(張)", "投信買賣(張)", "月增MoM%", "年增YoY%", "累計YoY%"] if c in df_pinned_rev.columns]
        rev_fmt = {"現價": "{:.2f}", "預估EPS(元)": "{:.2f}", "本益比(PE)": "{:.1f}", "💎EPS合理切入價": "{:.2f}", "今日%": "{:+.2f}%", "成交量(張)": "{:,.0f}", "外資買賣(張)": "{:+,d}", "投信買賣(張)": "{:+,d}", "當月營收(億)": "{:,.2f}", "月增MoM%": "{:+.2f}%", "年增YoY%": "{:+.2f}%", "累計營收(億)": "{:,.2f}", "累計YoY%": "{:+.2f}%"}
        st.dataframe(df_pinned_rev.style.map(color_rev_val, subset=rev_color_cols).format(rev_fmt, na_rep="-"), use_container_width=True, height=580)

if auto_refresh:
    time.sleep(20)
    fetch_mis_realtime_core16.clear()
    st.rerun()
