import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import datetime, pytz, requests, urllib3, io, os, json, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="台股每日戰情室 v60.0", page_icon="🚨", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
.war-hero-box { background: linear-gradient(135deg, #0f2027 0%, #203a43 50%, #2c5364 100%); color: #fff; padding: 20px 24px; border-radius: 14px; border-left: 8px solid #00e676; margin-bottom: 16px; box-shadow: 0 6px 18px rgba(0,0,0,0.25); }
.war-hero-box-danger { background: linear-gradient(135deg, #2b0f14 0%, #451219 50%, #5c1620 100%); color: #fff; padding: 20px 24px; border-radius: 14px; border-left: 8px solid #ff4d4f; margin-bottom: 16px; box-shadow: 0 6px 20px rgba(255,77,79,0.28); }
.war-hero-box-warn { background: linear-gradient(135deg, #2b1d1d 0%, #3d251e 50%, #4a2c2a 100%); color: #fff; padding: 20px 24px; border-radius: 14px; border-left: 8px solid #ff9800; margin-bottom: 16px; box-shadow: 0 6px 18px rgba(0,0,0,0.25); }
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
.swap-card { background: #0f172a; color: #f8fafc; border-radius: 12px; padding: 16px 20px; border: 2px solid #38bdf8; margin-bottom: 14px; line-height: 1.7; }
.pkg-box { background: #0f172a; border: 2px solid #f59e0b; border-radius: 12px; padding: 14px 18px; margin-bottom: 16px; color: #f8fafc; }
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
    """三級制『張數 × 成交金額(億)』除殭屍股濾網：<150元須>=1000張；150~500元須>=500張或1.5億；>=500元須>=200張或2.0億"""
    if curr_p is None or pd.isna(curr_p) or curr_p <= 0:
        return True
    eff_vol = max(float(est_vol or 0), float(ma5_vol or 0) * 0.85)
    turnover_yi = (eff_vol * float(curr_p)) / 100000.0
    if curr_p < 150.0:
        return eff_vol < 1000.0
    elif curr_p < 500.0:
        return (eff_vol < 500.0) and (turnover_yi < 1.5)
    else:
        return (eff_vol < 200.0) and (turnover_yi < 2.0)

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
# 模組二：2026/11 ～ 2027/01 關鍵風險事件行事曆
# ==========================================
EVENT_CALENDAR_2026_2027 = [
    {"date": "2026-11-23", "title": "金管會「限空令 3.0」正式上路（全市場禁平盤下放空/融券150%/禁發認售）", "level": "HIGH", "action": "觀察開盤回測月20MA(33,746)與T+2融資斷頭餘波，反彈不追高"},
    {"date": "2026-11-25", "title": "美國 10 月 PCE 物價指數 ＆ Q3 GDP 修正值", "level": "HIGH", "action": "牽動 12 月 Fed 升息定價與美債殖利率，嚴禁開槓桿"},
    {"date": "2026-11-30", "title": "MSCI 11 月半年度調整收盤生效（台股新興市場權重升至 29.01%）", "level": "MED", "action": "警戒 13:25~13:30 尾盤爆量甩尾，觀察新增主流股買盤"},
    {"date": "2026-12-01", "title": "黃仁勳與輝達法務長赴美國參眾兩院聽證會（12/01~12/03，12/3 參院公開作證）", "level": "HIGH", "action": "AI 供應鏈情緒高波動期，反彈遇壓減碼、嚴守現金水庫"},
    {"date": "2026-12-04", "title": "美國 11 月非農就業報告", "level": "HIGH", "action": "FOMC 前最後關鍵就業數據，慎防美股大幅震盪"},
    {"date": "2026-12-08", "title": "聯準會 FOMC 利率決議與點陣圖（12/08~12/09）", "level": "HIGH", "action": "市場已提前反映 3~4 碼升息，緊盯是否出現利空出盡與二次測底買點"},
    {"date": "2026-12-10", "title": "台積電 11 月營收、美國 11 月 CPI、美商務部長盧特尼克參院作證", "level": "HIGH", "action": "基本面與晶片出口政策雙重檢驗，確認 AI 訂單真實續航力"},
    {"date": "2026-12-15", "title": "Anthropic (ANT) 首波早期鎖定期解禁（3.03 億股）", "level": "HIGH", "action": "提防美股科技流動性抽血效應，關注台股 W 底右腳支撐"},
    {"date": "2026-12-16", "title": "台指期與選擇權 12 月月結算日", "level": "MED", "action": "外資近 10 萬口空單換倉/結算表態，觀察期現貨逆價差收斂"},
    {"date": "2026-12-21", "title": "ANT 快速納入那斯達克 100 生效 ＆ 美 BIS 晶片新規舊約運送截止日", "level": "HIGH", "action": "防範重演 SpaceX 納入日倒貨潮與出口管制搶運潮退潮"},
    {"date": "2026-12-23", "title": "美國 11 月 PCE 物價指數", "level": "MED", "action": "年底外資休假前最後通膨數據"},
    {"date": "2027-01-19", "title": "美國 BIS 晶片新規評論截止日", "level": "HIGH", "action": "2027 Q1 半導體出口規範定案關鍵時點"}
]

def get_upcoming_risk_events():
    today_d = get_tw_now().date()
    rows, next_ev = [], None
    for ev in EVENT_CALENDAR_2026_2027:
        ev_d = datetime.datetime.strptime(ev["date"], "%Y-%m-%d").date()
        delta_days = (ev_d - today_d).days
        status = "✅ 已度過" if delta_days < 0 else (f"🚨 今日登場！" if delta_days == 0 else (f"🔴 倒數 {delta_days} 天 (高波動警戒)" if delta_days <= 3 else (f"🟡 倒數 {delta_days} 天" if delta_days <= 7 else f"📅 尚餘 {delta_days} 天")))
        item = {"日期": ev["date"], "倒數狀態": status, "重大風險事件": ev["title"], "實戰防禦提醒": ev["action"], "_days": delta_days, "_level": ev["level"]}
        rows.append(item)
        if delta_days >= 0 and next_ev is None:
            next_ev = item
    return rows, next_ev

# ==========================================
# 模組四：MSCI 每季動態自動更新引擎 ＋ 證交所處置股查核
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
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get("https://www.twse.com.tw/rwd/zh/announcement/punish?response=json", headers=headers, timeout=6, verify=False)
        if r.status_code == 200:
            for row in r.json().get("data", []):
                if len(row) >= 7:
                    c_code = str(row[2]).strip()
                    if len(c_code) == 4 and c_code.isdigit():
                        disp_map[c_code] = f"🚨 現行處置股 ({str(row[6]).strip()})"
    except Exception:
        pass
    return disp_map

def infer_msci_theme(code: str, name: str, sector_str: str = "") -> str:
    s = f"{name} {sector_str}"
    if code in ["3189", "8046", "6274", "6672", "3037", "2383", "2368"] or any(k in s for k in ["載板", "CCL", "銅箔", "PCB"]): return "ABF載板與高頻CCL"
    if code in ["6683", "7822", "8021", "6515", "6223", "3711", "6239", "3264"] or any(k in s for k in ["封測", "探針", "檢測", "設備"]): return "先進封測介面設備"
    if code in ["3163", "4991", "3016", "3363", "3450", "6442"] or any(k in s for k in ["光通訊", "矽光子", "網通", "磊晶"]): return "CPO矽光子與光通訊"
    if code in ["3693", "7711", "3026", "6173", "2327", "3661", "3443"] or any(k in s for k in ["伺服器", "被動", "準系統", "ASIC"]): return "ASIC準系統與高壓被動"
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
def fetch_dynamic_msci_data(price_dict_keys_sig: int) -> dict:
    data_out = dict(MSCI_DEFAULT_2026_11)
    if os.path.exists(MSCI_CACHE_FILE):
        try:
            with open(MSCI_CACHE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if saved.get("std_add") or saved.get("small_add"):
                    data_out = saved
        except Exception: pass
    data_out["disposition_map"] = fetch_twse_disposition_codes()
    return data_out

# ==========================================
# 模組零：宏爺三大新穀倉 vs. 政策防彈衣 vs. 舊穀倉墊料觀察
# ==========================================
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

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_odm_inventory_ocf_health(codes_tuple):
    health_map = {k: dict(v) for k, v in ODM_Q3_OFFICIAL_BASE.items()}
    for code in codes_tuple:
        if code not in OLD_GRANARY_ODM_WATCH or code in ODM_Q3_OFFICIAL_BASE: continue
        res_s = fetch_single_stock_anti_trap(code, "TW")
        if res_s.get("ok"):
            health_map[code] = res_s
    return health_map

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_single_stock_anti_trap(code: str, mkt: str = "TW") -> dict:
    """Mode B 決選深檢包專用：按需查詢單檔個股季報之存貨QoQ、應收帳款QoQ 與營業現金流 (防逾時 24h 快取)"""
    if code in ODM_Q3_OFFICIAL_BASE:
        b = dict(ODM_Q3_OFFICIAL_BASE[code])
        b["ok"] = True
        return b
    out = {"ok": False, "inv_qoq": None, "ar_qoq": None, "ocf_positive": None, "note": "N/A (交由淘金客聯網查核)"}
    try:
        tkr = yf.Ticker(f"{code}.{'TW' if mkt == 'TW' else 'TWO'}")
        bs, cf = tkr.quarterly_balance_sheet, tkr.quarterly_cashflow
        inv_qoq_val, ar_qoq_val, ocf_pos_val = None, None, None
        if bs is not None and not bs.empty and bs.shape[1] >= 2:
            inv_row = next((idx for idx in bs.index if "Inventory" in str(idx) or "Inventories" in str(idx)), None)
            if inv_row is not None:
                v0, v1 = float(bs.loc[inv_row].iloc[0]), float(bs.loc[inv_row].iloc[1])
                if pd.notna(v0) and pd.notna(v1) and v1 > 0:
                    inv_qoq_val = round(((v0 - v1) / v1) * 100.0, 1)
            ar_row = next((idx for idx in bs.index if "Accounts Receivable" in str(idx) or "Receivables" in str(idx)), None)
            if ar_row is not None:
                a0, a1 = float(bs.loc[ar_row].iloc[0]), float(bs.loc[ar_row].iloc[1])
                if pd.notna(a0) and pd.notna(a1) and a1 > 0:
                    ar_qoq_val = round(((a0 - a1) / a1) * 100.0, 1)
        if cf is not None and not cf.empty and cf.shape[1] >= 1:
            ocf_row = next((idx for idx in cf.index if "Operating Cash Flow" in str(idx) or "Cash Flow From Continuing Operating" in str(idx)), None)
            if ocf_row is not None:
                ocf_v = float(cf.loc[ocf_row].iloc[0])
                if pd.notna(ocf_v): ocf_pos_val = bool(ocf_v > 0)
        if any(x is not None for x in [inv_qoq_val, ar_qoq_val, ocf_pos_val]):
            i_s = f"存貨QoQ {inv_qoq_val:+.1f}%" if inv_qoq_val is not None else "存貨QoQ N/A"
            a_s = f"應收QoQ {ar_qoq_val:+.1f}%" if ar_qoq_val is not None else "應收QoQ N/A"
            o_s = ("營業現金流為正(✅)" if ocf_pos_val else "營業現金流為負(⚠️)") if ocf_pos_val is not None else "OCF N/A"
            out = {"ok": True, "inv_qoq": inv_qoq_val, "ar_qoq": ar_qoq_val, "ocf_positive": ocf_pos_val, "note": f"{i_s} / {a_s} / {o_s}"}
    except Exception:
        pass
    return out

def evaluate_granary_and_anti_trap(code: str, single_yoy, odm_health_map: dict, macro_withdrawal_alert: bool = False) -> tuple[str, int, str]:
    yoy_pos = (single_yoy is None) or (pd.notna(single_yoy) and float(single_yoy) > 0.0)
    if code in OLD_GRANARY_ODM_WATCH:
        h_info = odm_health_map.get(code, ODM_Q3_OFFICIAL_BASE.get(code, {}))
        inv_qoq, ar_qoq, ocf_pos = h_info.get("inv_qoq", 0.0) or 0.0, h_info.get("ar_qoq", 0.0) or 0.0, h_info.get("ocf_positive", True)
        note_s = h_info.get("note", "純伺服器組裝ODM")
        if yoy_pos and (inv_qoq >= 19.0 or ar_qoq >= 20.0) and (not ocf_pos):
            return f"🐀 警戒：疑似墊料壓貨 (-20分｜{note_s})", -20, "🐀疑似墊料壓貨"
        elif code == "6669" and ocf_pos:
            return f"🔄 雙棲：ASIC自研伺服器 (+8分｜{note_s})", +8, "🔄ASIC自研雙棲"
        return f"⚠️ 舊穀倉ODM觀察 (-10分｜{note_s})", -10, "⚠️舊穀倉ODM觀察"
    if code in GRANARY_1_CSP_COMMON:
        bonus = 23 if macro_withdrawal_alert else 15
        return f"🌟 新穀倉1：CSP自研/共通規格 (+{bonus}分・無墊料風險)", bonus, "🌟新穀倉1(共通規格)"
    if code in GRANARY_2_POWER_THERMAL:
        bonus = 14 if macro_withdrawal_alert else 10
        return f"⚡ 新穀倉2：缺電/過熱基建 (+{bonus}分・剛性升級)", bonus, "⚡新穀倉2(電力散熱)"
    if code in GRANARY_3_EDGE_MEMORY:
        return "📱 新穀倉3：邊緣AI與記憶體封測 (+5分・終端轉嫁)", 5, "📱新穀倉3(邊緣封測)"
    if code in POLICY_DEFENSE_ARMOR:
        bonus = 18 if macro_withdrawal_alert else 8
        return f"🛡️ 政策內需防彈衣 (+{bonus}分・兆元內需/外資提款避風港)", bonus, "🛡️政策內需防彈衣"
    return "➖ 一般電子/題材股 (+0分)", 0, "➖一般題材"

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

def format_inst_chip_tag(fini_lots, sitc_lots):
    f_v = int(fini_lots) if pd.notna(fini_lots) else 0
    s_v = int(sitc_lots) if pd.notna(sitc_lots) else 0
    if f_v >= 50 and s_v >= 20: return f"🔥土洋同買 (外{f_v:+,}/投{s_v:+,})"
    elif s_v >= 30: return f"🏦投信認養 (投{s_v:+,}/外{f_v:+,})"
    elif f_v >= 200: return f"🌍外資大買 (外{f_v:+,}/投{s_v:+,})"
    elif f_v > 0 or s_v > 0: return f"📈法人偏多 (外{f_v:+,}/投{s_v:+,})"
    elif f_v == 0 and s_v == 0: return "➖中性觀望 (0/0)"
    else: return f"🛡️法人調節 (外{f_v:+,}/投{s_v:+,})"

ACTIVE_CB_BASE_SET = {
    "2301", "3017", "3324", "6187", "3131", "3583", "2383", "2368", "6274", "2345",
    "3533", "2357", "4938", "3231", "2382", "2355", "4976", "3701", "2009", "1527",
    "6112", "3147", "5013", "6446", "3691", "8069", "6751", "6877", "2453", "3264",
    "6285", "3558", "2449", "5269", "8016", "2395", "3008", "2327", "2059", "3653"
}

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_active_cb_codes():
    cb_codes = set(ACTIVE_CB_BASE_SET)
    headers = {"User-Agent": "Mozilla/5.0"}
    for url in ["https://www.tpex.org.tw/openapi/v1/tpex_cb_daily_close_quotes", "https://www.tpex.org.tw/web/bond/tradeinfo/cb/CBPrice.php?l=zh-tw&o=json"]:
        try:
            r = requests.get(url, headers=headers, timeout=6, verify=False)
            if r.status_code == 200:
                data = r.json()
                rows = data if isinstance(data, list) else data.get("aaData", [])
                for item in rows:
                    c_raw = str(item.get("BondCode", item.get("Code", item[0] if isinstance(item, list) and len(item) > 0 else ""))).strip()
                    if len(c_raw) in [5, 6] and c_raw[:4].isdigit(): cb_codes.add(c_raw[:4])
        except Exception: continue
    return cb_codes

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_official_3rates_and_cum_eps():
    headers = {"User-Agent": "Mozilla/5.0"}
    fin_dict = {}
    def clean_num(val):
        try:
            s = str(val).replace(",", "").replace("%", "").replace("+", "").strip()
            return float(s) if s not in ["", "-", "--", "N/A", "None"] else np.nan
        except Exception: return np.nan
    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap17_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap17_O"]:
        try:
            r = requests.get(url, headers=headers, timeout=8, verify=False)
            if r.status_code == 200:
                for row in r.json():
                    c = str(row.get("公司代號", row.get("證券代號", ""))).strip()
                    if len(c) == 4 and c.isdigit():
                        gm = clean_num(next((v for k, v in row.items() if "毛利率" in k), np.nan))
                        om = clean_num(next((v for k, v in row.items() if "營業利益率" in k or "營益率" in k), np.nan))
                        nm = clean_num(next((v for k, v in row.items() if "純益率" in k or "淨利率" in k), np.nan))
                        fin_dict[c] = {"gm": gm, "om": om, "nm": nm, "cum_eps": np.nan}
        except Exception: continue
    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci"]:
        try:
            r = requests.get(url, headers=headers, timeout=8, verify=False)
            if r.status_code == 200:
                for row in r.json():
                    c = str(row.get("公司代號", row.get("證券代號", ""))).strip()
                    if len(c) == 4 and c.isdigit():
                        eps_v = clean_num(next((v for k, v in row.items() if "基本每股盈餘" in k or "每股盈餘" in k), np.nan))
                        if c not in fin_dict: fin_dict[c] = {"gm": np.nan, "om": np.nan, "nm": np.nan, "cum_eps": eps_v}
                        else: fin_dict[c]["cum_eps"] = eps_v
        except Exception: continue
    return fin_dict

MARGIN_DILUTION_SET = {"2382", "3231", "2317", "4938", "2356", "2324", "3706"}
MARGIN_EXPANSION_SET = {
    "2330", "3017", "3324", "3653", "2059", "2383", "2368", "6274", "2345",
    "2308", "3711", "6239", "6187", "3131", "3583", "5274", "2301", "3443",
    "3661", "2454", "6515", "6223", "1519", "1503", "1513", "6683", "7822", "3163"
}

def compute_eps_valuation(code, curr_p, sector_str, single_yoy, cum_yoy, pe_official, support_price=None):
    if curr_p is None or pd.isna(curr_p) or curr_p <= 0:
        return np.nan, np.nan, np.nan, np.nan, "➖ 暫無報價"

    is_fin = ("金融" in sector_str) or (code in ["2881", "2882", "2891", "2886", "2884", "2885", "2892", "2880", "5880", "2890", "2883", "2887", "2801", "2834"])
    if is_fin: pe_low, pe_high = 12.0, 15.5
    elif any(k in sector_str for k in ["IC設計", "矽智財", "散熱", "BMC", "ASIC", "高速傳輸", "CoWoS", "探針", "重電", "矽光子"]) or code in ["2454", "3034", "2379", "3017", "3653", "2059", "5274", "3529", "3661", "3443", "6669", "3324", "5269", "6187", "3131", "6515", "6223", "1519", "6683", "3163", "4991", "7822"]:
        pe_low, pe_high = 20.0, 28.0
    elif any(k in sector_str for k in ["晶圓", "封測", "扇形封裝", "CCL", "PCB", "載板", "連接器", "鏡頭", "電源", "機電", "水資源"]) or code in ["2330", "3711", "6239", "2308", "3533", "2383", "2368", "3008", "3406", "2449", "3264", "6274", "3189", "8046", "1503", "1513", "1514", "8936", "6139"]:
        pe_low, pe_high = 16.0, 22.0
    else: pe_low, pe_high = 14.0, 18.5

    s_yoy = float(single_yoy) if (single_yoy is not None and pd.notna(single_yoy)) else 0.0
    c_yoy = float(cum_yoy) if (cum_yoy is not None and pd.notna(cum_yoy)) else s_yoy
    r_6m_yoy = 0.60 * c_yoy + 0.40 * s_yoy

    if r_6m_yoy >= 35.0: M = 1.20
    elif r_6m_yoy >= 20.0: M = 1.14
    elif r_6m_yoy >= 10.0: M = 1.08
    elif r_6m_yoy >= 0.0: M = 1.02
    elif r_6m_yoy >= -10.0: M = 0.94
    else: M = 0.88

    fin_map = fetch_official_3rates_and_cum_eps()
    f_info = fin_map.get(code, {})
    gm_v, om_v, cum_eps_api = f_info.get("gm", np.nan), f_info.get("om", np.nan), f_info.get("cum_eps", np.nan)

    if code in MARGIN_DILUTION_SET or (r_6m_yoy > 10.0 and pd.notna(om_v) and om_v < 4.2 and not is_fin):
        T, t_badge = 0.88, "⚠️毛利稀釋T=0.88"
    elif code in MARGIN_EXPANSION_SET or (pd.notna(gm_v) and pd.notna(om_v) and gm_v >= 28.0 and om_v >= 15.0 and s_yoy >= c_yoy and s_yoy >= 15.0):
        T, t_badge = 1.12, "🔥三率三升T=1.12"
    elif r_6m_yoy > 0.0 and s_yoy < c_yoy - 15.0 and pd.notna(om_v) and om_v < 8.0 and not is_fin:
        T, t_badge = 0.88, "⚠️毛利稀釋T=0.88"
    else:
        T, t_badge = 1.00, "⚖️三率持平T=1.00"

    W = float(np.clip(0.78 if is_fin else 0.74, 0.15, 0.85))
    conv_factor = (W + (1.0 - W) * M * T) * 0.95
    tech_entry = round(float(support_price), 2) if (support_price is not None and pd.notna(support_price) and support_price > 0) else round(curr_p * 0.96, 2)

    if pe_official is not None and pd.notna(pe_official) and 0.5 < float(pe_official) <= 65.0:
        pe_val = round(float(pe_official), 1)
        ttm_eps = curr_p / pe_val
        base_annual_eps = (cum_eps_api / W) if (pd.notna(cum_eps_api) and cum_eps_api > 0.1 and (0.35 * ttm_eps <= (cum_eps_api / W) <= 1.8 * ttm_eps)) else ttm_eps
        est_eps = round(max(0.1, base_annual_eps * conv_factor), 2)
        eps_entry_price = round(est_eps * pe_low, 2)
        eps_target_price = round(est_eps * pe_high, 2)

        if curr_p <= eps_entry_price:
            upside_to_entry = round(((eps_entry_price - curr_p) / max(1.0, curr_p)) * 100.0, 1)
            val_tag = f"💎 低估鑽石 (空間+{upside_to_entry}%) [{t_badge}]"
        elif curr_p <= eps_target_price:
            upside_pct = round(((eps_target_price - curr_p) / max(1.0, curr_p)) * 100.0, 1)
            val_tag = f"✅ 估值合理 (距目標+{upside_pct}%) [{t_badge}]"
        else:
            over_pct = round(((curr_p - eps_target_price) / max(1.0, eps_target_price)) * 100.0, 1)
            val_tag = f"⚠️ 超過常態PE (+{over_pct}%) [{t_badge}]"
        return est_eps, pe_val, eps_entry_price, eps_target_price, val_tag
    elif pe_official is not None and pd.notna(pe_official) and float(pe_official) > 65.0:
        pe_val = round(float(pe_official), 1)
        ttm_eps = curr_p / pe_val
        base_annual_eps = (cum_eps_api / W) if (pd.notna(cum_eps_api) and cum_eps_api > 0.05) else ttm_eps
        est_eps = round(max(0.05, base_annual_eps * conv_factor), 2)
        return est_eps, pe_val, tech_entry, round(curr_p * 1.12, 2), f"🌱 初轉盈高PE ({pe_val:.0f}倍・守{tech_entry}) [{t_badge}]"
    else:
        if pd.notna(cum_eps_api) and cum_eps_api > 0.2:
            est_eps = round((cum_eps_api / W) * conv_factor, 2)
            pe_calc = round(curr_p / max(0.1, est_eps), 1)
            if pe_calc <= 65.0:
                return est_eps, pe_calc, round(est_eps * pe_low, 2), round(est_eps * pe_high, 2), f"🌱 轉盈新星 (PE {pe_calc:.1f}x) [{t_badge}]"
        return np.nan, np.nan, tech_entry, round(curr_p * 1.12, 2), f"🔄 轉機觀察 (無PE・守{tech_entry}) [{t_badge}]"

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
                post_p = meta.get("postMarketPrice", None)
                pre_p = meta.get("preMarketPrice", None)
                ext_chg_pct, ext_label = None, ""
                if mkt_state in ["POST", "POSTPOST", "CLOSED"] and post_p is not None and float(post_p) > 0 and p_now > 0:
                    ext_chg_pct = round(((float(post_p) - p_now) / p_now) * 100.0, 2)
                    ext_label = f"盤後 {ext_chg_pct:+.2f}%"
                elif mkt_state in ["PRE", "PREPRE"] and pre_p is not None and float(pre_p) > 0 and p_now > 0:
                    ext_chg_pct = round(((float(pre_p) - p_now) / p_now) * 100.0, 2)
                    ext_label = f"盤前 {ext_chg_pct:+.2f}%"
                elif len(closes) > 1 and mkt_state != "REGULAR":
                    last_ext_p = float(closes[-1])
                    if abs(last_ext_p - p_now) / max(1.0, p_now) > 0.0005:
                        ext_chg_pct = round(((last_ext_p - p_now) / p_now) * 100.0, 2)
                        ext_label = f"盤後 {ext_chg_pct:+.2f}%"

                if p_now > 0:
                    return {
                        "open": round(o_now, decimals), "close": round(p_now, decimals),
                        "high": round(max(h_now, p_now), decimals), "low": round(min(l_now, p_now), decimals),
                        "prev_close": round(y_prev, decimals),
                        "chg_pct": round(((p_now - y_prev) / y_prev) * 100, 2) if y_prev > 0 else 0.0,
                        "mkt_state": mkt_state, "ext_chg_pct": ext_chg_pct, "ext_label": ext_label,
                        "vol_lots": 0, "time": dt_obj.strftime("%H:%M:%S"), "date": dt_obj.strftime("%Y%m%d"), "ts": mkt_time
                    }
    except Exception: pass
    return None

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
            if l_sym not in all_syms:
                all_syms.append(l_sym)

    quotes_map = {}
    with ThreadPoolExecutor(max_workers=12) as executor:
        fut_map = {executor.submit(fetch_yahoo_v8_realtime, sym, 3): sym for sym in all_syms}
        for fut in as_completed(fut_map):
            sym_k = fut_map[fut]
            res_q = fut.result()
            if res_q: quotes_map[sym_k] = res_q

    tnx_q = quotes_map.get("%5ETNX", {})
    tnx_raw = tnx_q.get("close", 4.94)
    tnx_val = round(tnx_raw / 10.0, 2) if tnx_raw > 15.0 else round(tnx_raw, 2)
    tnx_chg = tnx_q.get("chg_pct", -1.20)

    tsm_q = quotes_map.get("TSM", {})
    m_state = tsm_q.get("mkt_state", "CLOSED")
    nq_chg = quotes_map.get("NQ=F", {}).get("chg_pct", 0.0)
    if m_state == "REGULAR": us_session_tag = "🟢【美股盤中即時交易中】"
    elif m_state in ["PRE", "PREPRE"]: us_session_tag = f"🌅【美股收盤＋盤前電子盤 (那指期 NQ {nq_chg:+.2f}%)】"
    else: us_session_tag = f"🏁【美股正規收盤 ＋ 🌙盤後/那指期 (NQ {nq_chg:+.2f}%)】"

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
        "session_tag": us_session_tag, "quotes": quotes_map,
        "tnx_val": tnx_val, "tnx_chg": tnx_chg, "nq_chg": nq_chg,
        "top_up_sectors": [x["summary"] for x in sector_list[:3]],
        "bot_dn_sectors": [x["summary"] for x in sorted(sector_list, key=lambda x: x["chg_pct"])[:2]]
    }

@st.cache_data(ttl=10, show_spinner=False)
def fetch_mis_realtime_batch(code_market_tuples):
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp", "Cache-Control": "no-cache"}
    rt_dict = {}
    extra_etfs = [("0050", "TW"), ("00631L", "TW"), ("00400A", "TW"), ("00985B", "TW"), ("00713", "TW"), ("2812", "TW")]
    all_pairs = list(dict.fromkeys(list(code_market_tuples) + extra_etfs))
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
                    rt_dict[c_code] = {
                        "open": round(o_now, 2), "close": round(p_now, 2), "high": round(max(h_now, p_now), 2), "low": round(min(l_now, p_now), 2),
                        "prev_close": round(y_prev, 2), "chg_pct": round(((p_now - y_prev) / y_prev) * 100, 2) if y_prev > 0 else 0.0,
                        "vol_lots": v_lots, "time": str(item.get("t", "")), "date": str(item.get("d", ""))
                    }
        except Exception: continue
    yv8_t00 = fetch_yahoo_v8_realtime("%5ETWII", decimals=2)
    if yv8_t00 and yv8_t00["close"] > 0:
        if "t00" not in rt_dict or (abs(rt_dict["t00"]["close"] - rt_dict["t00"]["prev_close"]) < 0.01 and abs(yv8_t00["close"] - yv8_t00["prev_close"]) > 0.01):
            rt_dict["t00"] = yv8_t00
    fx_v8 = fetch_yahoo_v8_realtime("TWD=X", decimals=3)
    if fx_v8 and fx_v8["close"] > 10.0: rt_dict["USDTWD"] = fx_v8
    return rt_dict

# ==========================================
# v60.0 新增：全市場當沖率整批抓取 (TWSE TWTB4U ＋ TPEx) & 按需集保大戶週變動
# ==========================================
@st.cache_data(ttl=1800, show_spinner=False)
def fetch_day_trade_shares_batch() -> tuple[dict, str]:
    """整批 2 次請求取得全上市 (TWTB4U) 與全上櫃當沖成交股數，嚴格 5 秒逾時防護與 try-except"""
    headers = {"User-Agent": "Mozilla/5.0"}
    dt_shares = {}
    dt_note = ""
    now_tw = get_tw_now()
    is_intraday = (now_tw.weekday() < 5) and (540 <= (now_tw.hour * 60 + now_tw.minute) < 960)
    if is_intraday:
        dt_note = "(前日)"

    # 1. 上市 TWSE TWTB4U (OpenAPI 優先，失敗則回溯 3 個交易日抓 RWD JSON)
    twse_ok = False
    try:
        r_tw = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/TWTB4U", headers=headers, timeout=5, verify=False)
        if r_tw.status_code == 200 and isinstance(r_tw.json(), list) and len(r_tw.json()) > 50:
            for row in r_tw.json():
                c = str(row.get("Code", row.get("證券代號", ""))).strip()
                v_raw = str(row.get("Volume", row.get("當日沖銷交易成交股數", "0"))).replace(",", "").strip()
                if len(c) == 4 and c.isdigit() and v_raw.replace(".", "").isdigit():
                    dt_shares[c] = float(v_raw)
            twse_ok = True
    except Exception:
        pass

    if not twse_ok:
        for d_back in range(0, 4):
            chk_dt = now_tw - datetime.timedelta(days=d_back)
            if chk_dt.weekday() >= 5: continue
            try:
                r_rw = requests.get(f"https://www.twse.com.tw/rwd/zh/dayTrading/TWTB4U?date={chk_dt.strftime('%Y%m%d')}&response=json", headers=headers, timeout=5, verify=False)
                if r_rw.status_code == 200:
                    tables = r_rw.json().get("tables", [])
                    rows = tables[1].get("data", []) if len(tables) >= 2 else r_rw.json().get("data", [])
                    if rows and len(rows) > 50:
                        for row in rows:
                            c = str(row[0]).strip()
                            v_raw = str(row[3]).replace(",", "").strip()
                            if len(c) == 4 and c.isdigit() and v_raw.replace(".", "").isdigit():
                                dt_shares[c] = float(v_raw)
                        break
            except Exception:
                continue

    # 2. 上櫃 TPEx 當沖統計 (OpenAPI 優先，失敗則抓 intraday_trading_stat_result.php)
    tpex_ok = False
    try:
        r_otc = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_day_trading", headers=headers, timeout=5, verify=False)
        if r_otc.status_code == 200 and isinstance(r_otc.json(), list) and len(r_otc.json()) > 50:
            for row in r_otc.json():
                c = str(row.get("SecuritiesCompanyCode", row.get("Code", ""))).strip()
                v_raw = str(row.get("DayTradingVolume", row.get("Volume", "0"))).replace(",", "").strip()
                if len(c) == 4 and c.isdigit() and v_raw.replace(".", "").isdigit():
                    dt_shares[c] = float(v_raw)
            tpex_ok = True
    except Exception:
        pass

    if not tpex_ok:
        for d_back in range(0, 4):
            chk_dt = now_tw - datetime.timedelta(days=d_back)
            if chk_dt.weekday() >= 5: continue
            roc_d = f"{chk_dt.year - 1911}/{chk_dt.month:02d}/{chk_dt.day:02d}"
            try:
                r_o2 = requests.get(f"https://www.tpex.org.tw/web/stock/trading/intraday_stat/intraday_trading_stat_result.php?l=zh-tw&d={roc_d}&o=json", headers=headers, timeout=5, verify=False)
                if r_o2.status_code == 200:
                    rows = r_o2.json().get("aaData", [])
                    if rows and len(rows) > 50:
                        for row in rows:
                            c = str(row[0]).strip()
                            v_raw = str(row[3]).replace(",", "").strip()
                            if len(c) == 4 and c.isdigit() and v_raw.replace(".", "").isdigit():
                                dt_shares[c] = float(v_raw)
                        break
            except Exception:
                continue

    return dt_shares, dt_note

@st.cache_data(ttl=43200, show_spinner=False)
def fetch_tdcc_weekly_delta_ondemand(codes_tuple: tuple) -> dict:
    """
    針對 Mode A (最多6檔) / Mode B (最多3檔) 匯出時按需抓取最近兩週 400張/千張大戶單週變動%，
    單次 timeout=3.5s、最多4執行緒、快取12小時，失敗安全回傳 None 絕不卡死！
    """
    out = {}
    start_d = (get_tw_now() - datetime.timedelta(days=28)).strftime("%Y-%m-%d")

    def _one(code):
        try:
            r = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockHoldingSharesPer&data_id={code}&start_date={start_d}", timeout=3.5, verify=False)
            if r.status_code == 200:
                data = r.json().get("data", [])
                if data:
                    df_t = pd.DataFrame(data)
                    if "date" in df_t.columns and "HoldingSharesLevel" in df_t.columns and "percent" in df_t.columns:
                        dates = sorted(df_t["date"].unique())
                        if len(dates) >= 2:
                            d_cur, d_prv = dates[-1], dates[-2]
                            lv400 = {"400,001-600,000", "600,001-800,000", "800,001-1,000,000", "more than 1,000,001", "12", "13", "14", "15"}
                            lv1000 = {"more than 1,000,001", "15"}
                            cur_sub, prv_sub = df_t[df_t["date"] == d_cur], df_t[df_t["date"] == d_prv]
                            p400_c = float(cur_sub[cur_sub["HoldingSharesLevel"].astype(str).isin(lv400)]["percent"].sum())
                            p400_p = float(prv_sub[prv_sub["HoldingSharesLevel"].astype(str).isin(lv400)]["percent"].sum())
                            p1000_c = float(cur_sub[cur_sub["HoldingSharesLevel"].astype(str).isin(lv1000)]["percent"].sum())
                            p1000_p = float(prv_sub[prv_sub["HoldingSharesLevel"].astype(str).isin(lv1000)]["percent"].sum())
                            if p400_c > 0 and p400_p > 0:
                                return code, {
                                    "p400": round(p400_c, 2), "d400": round(p400_c - p400_p, 2),
                                    "p1000": round(p1000_c, 2), "d1000": round(p1000_c - p1000_p, 2),
                                    "date": str(d_cur)
                                }
        except Exception:
            pass
        return code, None

    with ThreadPoolExecutor(max_workers=4) as ex:
        for fut in as_completed([ex.submit(_one, c) for c in list(codes_tuple)[:6]]):
            c_k, val = fut.result()
            if val is not None: out[c_k] = val
    return out

@st.cache_data(ttl=600, show_spinner=False)
def fetch_official_market_data():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    price_dict, tdcc_dict, rev_dict = {}, {}, {}
    twii_official = {"close": 0.0, "chg_pct": 0.0, "date": ""}
    now_tw = get_tw_now()
    track_etf_codes = {"0050", "00631L", "00400A", "00985B", "00713"}

    try:
        r_idx = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK", headers=headers, timeout=10, verify=False)
        if r_idx.status_code == 200 and len(r_idx.json()) > 0:
            last_row = r_idx.json()[-1]
            idx_c = float(str(last_row.get("TAIEX", "0")).replace(",", ""))
            idx_chg = float(str(last_row.get("Change", "0")).replace(",", "").replace("+", ""))
            prev_idx = idx_c - idx_chg
            twii_official = {"close": round(idx_c, 1), "chg_pct": round((idx_chg / prev_idx) * 100, 2) if prev_idx > 0 else 0.0, "date": str(last_row.get("Date", ""))}
    except Exception: pass

    try:
        r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=10, verify=False)
        if r.status_code == 200:
            for row in r.json():
                code = str(row.get("Code", "")).strip()
                if (len(code) == 4 and code.isdigit()) or code in track_etf_codes:
                    try:
                        c_str = str(row.get("ClosingPrice", "")).replace(",", "").strip()
                        if not c_str or c_str in ["-", "--"]: continue
                        c_price = float(c_str)
                        chg_raw = str(row.get("Change", "0")).replace(",", "").replace("+", "").replace("X", "").strip()
                        try: chg_val = float(chg_raw)
                        except Exception: chg_val = 0.0
                        if "-" in str(row.get("Change", "")) and chg_val > 0: chg_val = -chg_val
                        prev_c = c_price - chg_val
                        v_str = str(row.get("TradeVolume", "0")).replace(",", "").strip()
                        vol_shares = float(v_str) if v_str.replace(".", "").isdigit() else 0.0
                        if c_price > 0:
                            price_dict[code] = {
                                "name": str(row.get("Name", "")).strip(), "close": c_price,
                                "chg_pct": round((chg_val / prev_c) * 100, 2) if prev_c > 0 else 0.0,
                                "vol_lots": int(vol_shares // 1000), "vol_shares": vol_shares,
                                "market": "TW", "pe": np.nan, "pb": np.nan,
                                "cum_yoy": None, "fini_lots": 0, "sitc_lots": 0, "sector": "",
                                "tdcc_1000": None, "tdcc_400_delta": None, "tdcc_1000_delta": None, "day_trade_str": "N/A (交由淘金客聯網查核)"
                            }
                    except Exception: continue
    except Exception: pass

    try:
        r_tpex = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", headers=headers, timeout=10, verify=False)
        if r_tpex.status_code == 200:
            for row in r_tpex.json():
                code = str(row.get("SecuritiesCompanyCode", row.get("Code", ""))).strip()
                if (len(code) == 4 and code.isdigit()) or code in track_etf_codes:
                    try:
                        c_str = str(row.get("Close", row.get("ClosingPrice", ""))).replace(",", "").strip()
                        if not c_str or c_str in ["-", "--", "N/A", ""]: continue
                        c_price = float(c_str)
                        chg_raw = str(row.get("Change", "0")).replace(",", "").replace("+", "").replace(" ", "").strip()
                        try: chg_val = float(chg_raw)
                        except Exception: chg_val = 0.0
                        prev_c = c_price - chg_val
                        v_str = str(row.get("TradingShares", row.get("TradeVolume", "0"))).replace(",", "").strip()
                        try: vol_shares = float(v_str)
                        except Exception: vol_shares = 0.0
                        if c_price > 0:
                            price_dict[code] = {
                                "name": str(row.get("CompanyName", row.get("Name", ""))).strip(), "close": c_price,
                                "chg_pct": round((chg_val / prev_c) * 100, 2) if prev_c > 0 else 0.0,
                                "vol_lots": int(vol_shares // 1000), "vol_shares": vol_shares,
                                "market": "TWO", "pe": np.nan, "pb": np.nan,
                                "cum_yoy": None, "fini_lots": 0, "sitc_lots": 0, "sector": "",
                                "tdcc_1000": None, "tdcc_400_delta": None, "tdcc_1000_delta": None, "day_trade_str": "N/A (交由淘金客聯網查核)"
                            }
                    except Exception: continue
    except Exception: pass

    if sum(1 for v in price_dict.values() if v.get("market") == "TWO") < 100:
        try:
            r_otc2 = requests.get("https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&o=json", headers=headers, timeout=8, verify=False)
            if r_otc2.status_code == 200:
                for row in r_otc2.json().get("aaData", []):
                    if len(row) >= 9 and len(str(row[0]).strip()) == 4 and str(row[0]).strip().isdigit():
                        code = str(row[0]).strip()
                        try:
                            c_price = float(str(row[2]).replace(",", "").strip())
                            try: chg_val = float(str(row[3]).replace(",", "").replace("+", "").strip())
                            except Exception: chg_val = 0.0
                            prev_c = c_price - chg_val
                            try: vol_shares = float(str(row[8]).replace(",", "").strip())
                            except Exception: vol_shares = 0.0
                            if c_price > 0:
                                price_dict[code] = {
                                    "name": str(row[1]).strip(), "close": c_price,
                                    "chg_pct": round((chg_val / prev_c) * 100, 2) if prev_c > 0 else 0.0,
                                    "vol_lots": int(vol_shares // 1000), "vol_shares": vol_shares,
                                    "market": "TWO", "pe": np.nan, "pb": np.nan,
                                    "cum_yoy": None, "fini_lots": 0, "sitc_lots": 0, "sector": "",
                                    "tdcc_1000": None, "tdcc_400_delta": None, "tdcc_1000_delta": None, "day_trade_str": "N/A (交由淘金客聯網查核)"
                                }
                        except Exception: continue
        except Exception: pass

    # 注入批次當沖率
    try:
        dt_shares_map, dt_note = fetch_day_trade_shares_batch()
        for code, dt_sh in dt_shares_map.items():
            if code in price_dict:
                tot_sh = float(price_dict[code].get("vol_shares", 0.0))
                if tot_sh > 0 and dt_sh >= 0:
                    ratio_dt = round((dt_sh / tot_sh) * 100.0, 1)
                    if 0.0 <= ratio_dt <= 99.9:
                        price_dict[code]["day_trade_str"] = f"{ratio_dt:.1f}%{dt_note}"
                        price_dict[code]["day_trade_num"] = ratio_dt
    except Exception:
        pass

    # 三大法人買賣超 (上市 T86 / 上櫃 3itrade)
    for d_back in range(0, 5):
        chk_dt = now_tw - datetime.timedelta(days=d_back)
        if chk_dt.weekday() >= 5: continue
        try:
            r_t86 = requests.get(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={chk_dt.strftime('%Y%m%d')}&selectType=ALLBUT0999&response=json", headers=headers, timeout=7, verify=False)
            if r_t86.status_code == 200:
                t86_data = r_t86.json().get("data", [])
                if t86_data and len(t86_data) > 100:
                    for row in t86_data:
                        c_code = str(row[0]).strip()
                        if c_code in price_dict and len(row) >= 12:
                            try:
                                price_dict[c_code]["fini_lots"] = int(round(float(str(row[4]).replace(",", "").strip()) / 1000.0))
                                price_dict[c_code]["sitc_lots"] = int(round(float(str(row[10]).replace(",", "").strip()) / 1000.0))
                            except Exception: pass
                    break
        except Exception: continue

    for d_back in range(0, 5):
        chk_dt = now_tw - datetime.timedelta(days=d_back)
        if chk_dt.weekday() >= 5: continue
        roc_d_str = f"{chk_dt.year - 1911}/{chk_dt.month:02d}/{chk_dt.day:02d}"
        try:
            r_otc_inst = requests.get(f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d={roc_d_str}&o=json", headers=headers, timeout=7, verify=False)
            if r_otc_inst.status_code == 200:
                otc_rows = r_otc_inst.json().get("aaData", [])
                if otc_rows and len(otc_rows) > 100:
                    for row in otc_rows:
                        c_code = str(row[0]).strip()
                        if c_code in price_dict and len(row) >= 14:
                            try:
                                f_sh = float(str(row[10]).replace(",", "").strip()) if len(row) >= 20 else float(str(row[4]).replace(",", "").strip())
                                s_sh = float(str(row[13]).replace(",", "").strip()) if len(row) >= 20 else float(str(row[7]).replace(",", "").strip())
                                price_dict[c_code]["fini_lots"] = int(round(f_sh / 1000.0))
                                price_dict[c_code]["sitc_lots"] = int(round(s_sh / 1000.0))
                            except Exception: pass
                    break
        except Exception: continue

    for pe_url, c_key, p_keys in [
        ("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", "Code", ["PEratio"]),
        ("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", "SecuritiesCompanyCode", ["PriceEarningRatio", "PEratio"])
    ]:
        try:
            r_pe = requests.get(pe_url, headers=headers, timeout=10, verify=False)
            if r_pe.status_code == 200:
                for row in r_pe.json():
                    code = str(row.get(c_key, row.get("Code", ""))).strip()
                    if code in price_dict:
                        for pk in p_keys:
                            pe_s = str(row.get(pk, "")).replace(",", "").strip()
                            if pe_s and pe_s not in ["-", "--", "N/A", "0", "0.00"]:
                                try: price_dict[code]["pe"] = round(float(pe_s), 1); break
                                except Exception: pass
        except Exception: pass

    # 集保中心 400張以上 (12~15級) ＋ 1000張以上 (15級) ＆ 本地前週快照比對
    try:
        r_csv = requests.get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", headers=headers, timeout=15, verify=False)
        if r_csv.status_code == 200 and len(r_csv.text) > 500:
            df_tdcc = pd.read_csv(io.StringIO(r_csv.text), dtype=str)
            df_tdcc.columns = [c.strip() for c in df_tdcc.columns]
            col_date = next((c for c in df_tdcc.columns if "日期" in c), None)
            col_code = next((c for c in df_tdcc.columns if "代號" in c), None)
            col_lvl = next((c for c in df_tdcc.columns if "分級" in c), None)
            col_pct = next((c for c in df_tdcc.columns if "比例" in c), None)
            if col_code and col_lvl and col_pct:
                cur_tdcc_date = str(df_tdcc[col_date].iloc[0]).strip() if col_date else ""
                df_tdcc["code_clean"] = df_tdcc[col_code].str.strip()
                df_tdcc["lvl_clean"] = df_tdcc[col_lvl].str.strip()
                df_tdcc["pct_num"] = pd.to_numeric(df_tdcc[col_pct].str.replace(",", ""), errors="coerce").fillna(0.0)

                df_400 = df_tdcc[df_tdcc["lvl_clean"].isin(["12", "13", "14", "15"])]
                g400 = df_400.groupby("code_clean")["pct_num"].sum().to_dict()
                tdcc_dict = {str(k): round(float(v), 2) for k, v in g400.items() if 0.0 < float(v) <= 100.0}

                df_1000 = df_tdcc[df_tdcc["lvl_clean"] == "15"]
                g1000 = df_1000.groupby("code_clean")["pct_num"].sum().to_dict()
                tdcc_1000_map = {str(k): round(float(v), 2) for k, v in g1000.items() if 0.0 < float(v) <= 100.0}

                prev_snap = {}
                if os.path.exists(TDCC_PREV_CACHE_FILE):
                    try:
                        with open(TDCC_PREV_CACHE_FILE, "r", encoding="utf-8") as f:
                            prev_snap = json.load(f)
                    except Exception: prev_snap = {}

                if cur_tdcc_date and prev_snap.get("cur_date") and prev_snap.get("cur_date") != cur_tdcc_date:
                    prev_snap["prev_date"] = prev_snap.get("cur_date")
                    prev_snap["prev_400"] = prev_snap.get("cur_400", {})
                    prev_snap["prev_1000"] = prev_snap.get("cur_1000", {})

                prev_400 = prev_snap.get("prev_400", {})
                prev_1000 = prev_snap.get("prev_1000", {})

                for c_k, v400 in tdcc_dict.items():
                    if c_k in price_dict:
                        v1000 = tdcc_1000_map.get(c_k, None)
                        price_dict[c_k]["tdcc_1000"] = v1000
                        if c_k in prev_400:
                            price_dict[c_k]["tdcc_400_delta"] = round(v400 - float(prev_400[c_k]), 2)
                        if v1000 is not None and c_k in prev_1000:
                            price_dict[c_k]["tdcc_1000_delta"] = round(v1000 - float(prev_1000[c_k]), 2)

                if cur_tdcc_date and prev_snap.get("cur_date") != cur_tdcc_date:
                    try:
                        save_obj = {
                            "cur_date": cur_tdcc_date, "cur_400": tdcc_dict, "cur_1000": tdcc_1000_map,
                            "prev_date": prev_snap.get("prev_date", ""), "prev_400": prev_400, "prev_1000": prev_1000
                        }
                        with open(TDCC_PREV_CACHE_FILE, "w", encoding="utf-8") as f:
                            json.dump(save_obj, f)
                    except Exception: pass
    except Exception: pass

    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap05_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"]:
        try:
            r_rev = requests.get(url, headers=headers, timeout=12, verify=False)
            if r_rev.status_code == 200:
                for row in r_rev.json():
                    code = str(row.get("公司代號", row.get("證券代號", ""))).strip()
                    sec_str = str(row.get("產業別", "")).strip()
                    yoy_raw = next((str(v).replace(",", "").strip() for k, v in row.items() if "同月增減" in k), None)
                    cum_raw = next((str(v).replace(",", "").strip() for k, v in row.items() if "前期比較增減" in k), None)
                    if code and yoy_raw not in [None, "", "-", "--", "N/A"]:
                        try: rev_dict[code] = round(float(yoy_raw), 1)
                        except Exception: pass
                    if code in price_dict:
                        if sec_str and sec_str not in ["", "-", "None"]: price_dict[code]["sector"] = sec_str
                        if cum_raw not in [None, "", "-", "--", "N/A"]:
                            try: price_dict[code]["cum_yoy"] = round(float(cum_raw), 1)
                            except Exception: pass
        except Exception: continue
    return price_dict, tdcc_dict, rev_dict, twii_official

@st.cache_data(ttl=300, show_spinner=False)
def fetch_all_monthly_revenue_df(price_dict):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    records_by_code = {}
    cb_set = fetch_active_cb_codes()

    def to_float(val):
        try:
            s = str(val).replace(",", "").replace("+", "").replace("%", "").strip()
            return float(s) if s not in ["", "-", "--", "N/A", "None", "不適用"] else np.nan
        except Exception: return np.nan

    def build_turnaround_tag(curr_rev_k, yoy_pct, mom_pct, cum_rev_k, cum_yoy_pct, sector_str, note_str="", code_str=""):
        if (code_str not in POLICY_DEFENSE_ARMOR) and ("建材營造" in sector_str or "營造" in sector_str or any(k in str(note_str) for k in ["建案", "交屋", "房地", "不動產", "過戶", "營建", "售地"])):
            return "🏗️ 營建入帳認列(排除波段)"
        if "評價損益" in note_str or "投資標的" in note_str:
            return "💼 投資評價變動(非本業實業)"
        if pd.isna(yoy_pct) or yoy_pct < 20.0: return "➖ 一般波動"
        prev_cum_yoy = cum_yoy_pct
        if pd.notna(curr_rev_k) and pd.notna(cum_rev_k) and cum_rev_k > curr_rev_k and (100.0 + yoy_pct) > 0 and pd.notna(cum_yoy_pct) and (100.0 + cum_yoy_pct) > 0:
            last_yr_curr = curr_rev_k / (1.0 + yoy_pct / 100.0)
            last_yr_cum = cum_rev_k / (1.0 + cum_yoy_pct / 100.0)
            prev_cum_last = last_yr_cum - last_yr_curr
            if prev_cum_last > 0:
                prev_cum_yoy = (((cum_rev_k - curr_rev_k) - prev_cum_last) / prev_cum_last) * 100.0
        if pd.notna(prev_cum_yoy) and prev_cum_yoy <= 0.0 and yoy_pct >= 20.0 and (pd.isna(mom_pct) or mom_pct >= 0.0):
            return f"🌱 久旱逢甘霖 (前累計{prev_cum_yoy:+.1f}%衰退 ➔ 本月暴衝+{yoy_pct:.1f}%)"
        elif pd.notna(prev_cum_yoy) and prev_cum_yoy <= 8.0 and yoy_pct >= 25.0 and (pd.isna(mom_pct) or mom_pct >= 5.0):
            return f"🚀 低迷翻揚轉機 (前累計+{prev_cum_yoy:.1f}%平淡 ➔ 本月暴衝+{yoy_pct:.1f}%)"
        elif yoy_pct >= 20.0 and pd.notna(cum_yoy_pct) and cum_yoy_pct >= 20.0:
            return "🔥 長線強者恆強 (單月與累計雙高成長)"
        return "📈 單月成長達標"

    for mkt_label, url in [("上市", "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"), ("上櫃", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O")]:
        try:
            r = requests.get(url, headers=headers, timeout=12, verify=False)
            if r.status_code == 200:
                for item in r.json():
                    code = str(item.get("公司代號", item.get("證券代號", ""))).strip()
                    if not (len(code) == 4 and code.isdigit()): continue
                    name, ym_raw, sector = str(item.get("公司名稱", "")).strip(), str(item.get("資料年月", "")).strip(), str(item.get("產業別", "")).strip()
                    rev_curr_k, mom_pct, yoy_pct = to_float(item.get("營業收入-當月營收")), to_float(item.get("營業收入-上月比較增減(%)")), to_float(item.get("營業收入-去年同月增減(%)"))
                    cum_rev_k, cum_yoy_pct = to_float(item.get("累計營業收入-當月累計營收")), to_float(item.get("累計營業收入-前期比較增減(%)"))
                    note_txt = str(item.get("備註", "")).strip()
                    if note_txt in ["-", "無", "NA", "None"]: note_txt = ""
                    p_info = price_dict.get(code, {})
                    if not name and p_info.get("name"): name = p_info.get("name")
                    curr_p, v_lots = p_info.get("close", np.nan), p_info.get("vol_lots", 0)
                    f_lots, s_lots = p_info.get("fini_lots", 0), p_info.get("sitc_lots", 0)
                    est_eps, pe_v, eps_entry, _, val_tag = compute_eps_valuation(code, curr_p, sector, yoy_pct, cum_yoy_pct, p_info.get("pe", np.nan))
                    records_by_code[code] = {
                        "資料年月": ym_raw, "市場": mkt_label, "代碼": code, "公司名稱": name, "產業別": sector or "電子/其他",
                        "轉機與成長型態": build_turnaround_tag(rev_curr_k, yoy_pct, mom_pct, cum_rev_k, cum_yoy_pct, sector, note_txt, code),
                        "備註AI真偽鑑別": classify_revenue_note_quality(note_txt, sector, code),
                        "CB可轉債": "🔥含CB可轉債" if code in cb_set else "➖",
                        "法人動向": format_inst_chip_tag(f_lots, s_lots), "外資買賣(張)": f_lots, "投信買賣(張)": s_lots,
                        "現價": curr_p, "今日%": p_info.get("chg_pct", np.nan), "成交量(張)": v_lots,
                        "預估EPS(元)": est_eps, "本益比(PE)": pe_v, "💎EPS合理切入價": eps_entry, "估值診斷": val_tag,
                        "當月營收(億)": round(rev_curr_k / 100000.0, 2) if pd.notna(rev_curr_k) else np.nan,
                        "月增MoM%": round(mom_pct, 2) if pd.notna(mom_pct) else np.nan,
                        "年增YoY%": round(yoy_pct, 2) if pd.notna(yoy_pct) else np.nan,
                        "累計營收(億)": round(cum_rev_k / 100000.0, 2) if pd.notna(cum_rev_k) else np.nan,
                        "累計YoY%": round(cum_yoy_pct, 2) if pd.notna(cum_yoy_pct) else np.nan,
                        "財報營收備註說明": note_txt
                    }
        except Exception: continue

    latest_roc_y, latest_m, latest_ym_str = get_target_revenue_ym_list()[0]
    for mkt_label, m_url in [
        ("上市", f"https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_{latest_roc_y}_{latest_m}_0.html"),
        ("上櫃", f"https://mopsov.twse.com.tw/nas/t21/otc/t21sc03_{latest_roc_y}_{latest_m}_0.html")
    ]:
        try:
            r_mops = requests.get(m_url, headers=headers, timeout=6, verify=False)
            if r_mops.status_code == 200 and len(r_mops.content) > 2000:
                html_txt = r_mops.content.decode("cp950", errors="ignore")
                current_mops_sector = ""
                for block in re.split(r"(<th[^>]*>.*?產業別.*?</th>)", html_txt, flags=re.S | re.I):
                    sec_m = re.search(r"產業別[：:]\s*([^<\s]+)", block)
                    if sec_m: current_mops_sector = sec_m.group(1).strip(); continue
                    for tr_match in re.findall(r"<tr[^>]*>(.*?)</tr>", block, flags=re.S | re.I):
                        tds = [re.sub(r"<[^>]+>", "", td).replace("&nbsp;", "").strip() for td in re.findall(r"<td[^>]*>(.*?)</td>", tr_match, flags=re.S | re.I)]
                        if len(tds) >= 10 and len(tds[0]) == 4 and tds[0].isdigit():
                            code, rev_curr_k = tds[0], to_float(tds[2])
                            if pd.isna(rev_curr_k) or rev_curr_k <= 0: continue
                            mom_pct, yoy_pct, cum_rev_k, cum_yoy_pct = to_float(tds[5]), to_float(tds[6]), to_float(tds[7]), to_float(tds[9])
                            note_txt = tds[10] if len(tds) > 10 and tds[10] not in ["-", "無", "NA", "None"] else ""
                            existing, p_info = records_by_code.get(code, {}), price_dict.get(code, {})
                            clean_name = existing.get("公司名稱") or p_info.get("name") or tds[1]
                            sector = existing.get("產業別") or p_info.get("sector") or current_mops_sector or "電子/轉機"
                            final_note = note_txt or existing.get("財報營收備註說明", "")
                            curr_p, v_lots = p_info.get("close", existing.get("現價", np.nan)), p_info.get("vol_lots", existing.get("成交量(張)", 0))
                            f_lots, s_lots = p_info.get("fini_lots", 0), p_info.get("sitc_lots", 0)
                            est_eps, pe_v, eps_entry, _, val_tag = compute_eps_valuation(code, curr_p, sector, yoy_pct, cum_yoy_pct, p_info.get("pe", np.nan))
                            records_by_code[code] = {
                                "資料年月": latest_ym_str, "市場": mkt_label, "代碼": code, "公司名稱": clean_name, "產業別": sector,
                                "轉機與成長型態": build_turnaround_tag(rev_curr_k, yoy_pct, mom_pct, cum_rev_k, cum_yoy_pct, sector, final_note, code),
                                "備註AI真偽鑑別": classify_revenue_note_quality(final_note, sector, code),
                                "CB可轉債": "🔥含CB可轉債" if code in cb_set else "➖",
                                "法人動向": format_inst_chip_tag(f_lots, s_lots), "外資買賣(張)": f_lots, "投信買賣(張)": s_lots,
                                "現價": curr_p, "今日%": p_info.get("chg_pct", existing.get("今日%", np.nan)), "成交量(張)": v_lots,
                                "預估EPS(元)": est_eps, "本益比(PE)": pe_v, "💎EPS合理切入價": eps_entry, "估值診斷": val_tag,
                                "當月營收(億)": round(rev_curr_k / 100000.0, 2),
                                "月增MoM%": round(mom_pct, 2) if pd.notna(mom_pct) else np.nan,
                                "年增YoY%": round(yoy_pct, 2) if pd.notna(yoy_pct) else np.nan,
                                "累計營收(億)": round(cum_rev_k / 100000.0, 2) if pd.notna(cum_rev_k) else np.nan,
                                "累計YoY%": round(cum_yoy_pct, 2) if pd.notna(cum_yoy_pct) else np.nan,
                                "財報營收備註說明": final_note
                            }
        except Exception: continue
    if not records_by_code: return pd.DataFrame()
    return pd.DataFrame(list(records_by_code.values())).sort_values(by="年增YoY%", ascending=False, na_position="last").reset_index(drop=True)

@st.cache_data(ttl=1800, show_spinner=False)
def verify_24m_first_breakout_batch(codes_tuple, threshold_yoy=20.0):
    start_d = (get_tw_now() - datetime.timedelta(days=1120)).strftime("%Y-%m-%d")
    verified_results = {}
    def check_single_stock(code):
        try:
            r = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue&data_id={code}&start_date={start_d}", timeout=5, verify=False)
            if r.status_code == 200:
                data = r.json().get("data", [])
                if len(data) >= 15:
                    df_h = pd.DataFrame(data)
                    df_h["ym"] = df_h["revenue_year"].astype(int) * 100 + df_h["revenue_month"].astype(int)
                    df_h = df_h.sort_values("ym").reset_index(drop=True)
                    rev_map = dict(zip(df_h["ym"], df_h["revenue"].astype(float)))
                    yoy_series = []
                    for _, row in df_h.iterrows():
                        y_int, m_int = int(row["revenue_year"]), int(row["revenue_month"])
                        prev_ym = (y_int - 1) * 100 + m_int
                        if prev_ym in rev_map and rev_map[prev_ym] > 0:
                            yoy_series.append((f"{y_int - 1911}{m_int:02d}", round(((float(row["revenue"]) - rev_map[prev_ym]) / rev_map[prev_ym]) * 100.0, 1)))
                    if len(yoy_series) >= 6:
                        latest_ym_s, latest_yoy_v = yoy_series[-1]
                        prior_list = yoy_series[:-1][-23:]
                        months_since, last_hit_ym = 0, "無"
                        for ym_p, v in reversed(prior_list):
                            if v < threshold_yoy: months_since += 1
                            else: last_hit_ym = ym_p; break
                        max_prior = max([v for _, v in prior_list]) if prior_list else 0.0
                        if months_since >= len(prior_list) and len(prior_list) >= 12:
                            return code, (True, 24, f"👑【2年(24個月)首見破繭！】前{len(prior_list)}月最高僅{max_prior:+.1f}% ➔ {latest_ym_s}首破+{latest_yoy_v:.1f}%")
                        elif months_since >= 6:
                            return code, (True, months_since, f"🌟【久違 {months_since} 個月首破20%！】(前次為{last_hit_ym}，本月+{latest_yoy_v:.1f}%)")
                        else:
                            return code, (False, months_since, f"❌ 非首次 (前次達標於 {last_hit_ym}，僅隔 {months_since} 個月)")
        except Exception: pass
        return code, None
    with ThreadPoolExecutor(max_workers=12) as executor:
        for fut in as_completed([executor.submit(check_single_stock, c) for c in codes_tuple[:150]]):
            c_res, val_res = fut.result()
            if val_res is not None: verified_results[c_res] = val_res
    return verified_results

def estimate_foreign_short_cost_and_target(highs, lows, closes, volumes):
    if len(closes) < 20:
        curr = float(closes[-1]) if len(closes) > 0 else 36033.3
        return round(curr * 1.08, 0), round(curr * 1.04, 0), round(curr * 1.00, 0)
    h20, l20, c20, v20 = np.array(highs[-20:], dtype=float), np.array(lows[-20:], dtype=float), np.array(closes[-20:], dtype=float), np.array(volumes[-20:], dtype=float)
    if np.sum(v20) <= 0: v20 = np.ones_like(c20)
    typ_p = (h20 + l20 + c20) / 3.0
    med_p = np.median(typ_p)
    weights = v20 * (1.0 + np.maximum(0.0, (typ_p - med_p) / max(1.0, med_p)) * 8.0 + ((h20 - c20) / np.maximum(1.0, c20)) * 15.0)
    short_cost_idx = float(np.sum(typ_p * weights) / max(1.0, np.sum(weights)))
    ma20_val = float(np.mean(c20))
    ma60_val = float(np.mean(closes[-60:])) if len(closes) >= 60 else ma20_val
    est_short_cost = round(0.55 * short_cost_idx + 0.25 * ma20_val + 0.20 * ma60_val, 0)
    return est_short_cost, round(est_short_cost * 0.960, 0), round(est_short_cost * 0.925, 0)

def compute_dynamic_pure_stock_maint(twii_now, ma20, ma60):
    cost_anchor = 0.70 * max(1.0, ma20) + 0.30 * max(1.0, ma60)
    return round(max(115.0, min(195.0, 166.7 * ((twii_now / max(1.0, cost_anchor)) ** 1.95))), 1)

@st.cache_data(ttl=60, show_spinner=False)
def fetch_auto_derivatives_base(price_dict):
    headers = {"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"}
    res = {
        "fini_tx_oi": -97863, "fini_tx_chg": 650, "sitc_tx_oi": 96500, "sitc_tx_chg": 450, "dealer_tx_chg": -1100,
        "fini_mtx_oi": 4200, "fini_mtx_chg": 320, "sitc_mtx_chg": 0, "dealer_mtx_chg": -500, "retail_mtx_chg": 180,
        "consec_cover_days": 1, "cover_3d_sum": 1800,
        "pc_ratio": 78.5, "warrant_hedge_yi": -290.16, "official_stock_maint": None, "official_maint_date": "",
        "margin_balance_yi": 4362.0, "margin_chg_yi": -491.4, "margin_2d_chg_yi": -971.7,
        "fut_date": "2026/11/20", "twse_date": ""
    }
    now_tw = get_tw_now()
    pcr_fetched = False

    try:
        r_pcr = requests.get("https://openapi.taifex.com.tw/v1/PutCallRatio", headers=headers, timeout=5, verify=False)
        if r_pcr.status_code == 200 and len(r_pcr.json()) > 0:
            last_row = r_pcr.json()[-1]
            for k_p in ["PutCallOpenInterestRatio", "PutCallOpenInterestRatio%", "PutCallRatio"]:
                if k_p in last_row and str(last_row[k_p]).strip():
                    val = float(str(last_row[k_p]).replace(",", "").strip())
                    if 30.0 <= val <= 250.0:
                        res["pc_ratio"] = round(val, 1); pcr_fetched = True; break
    except Exception: pass

    if not pcr_fetched:
        try:
            q_end, q_start = now_tw.strftime("%Y/%m/%d"), (now_tw - datetime.timedelta(days=12)).strftime("%Y/%m/%d")
            r_pcr_csv = requests.post("https://www.taifex.com.tw/cht/3/pcRatioDown", data={"queryStartDate": q_start, "queryEndDate": q_end}, headers=headers, timeout=6, verify=False)
            if r_pcr_csv.status_code == 200 and len(r_pcr_csv.content) > 50:
                txt_csv = r_pcr_csv.content.decode("cp950", errors="ignore")
                valid_lines = [ln.strip().rstrip(",") for ln in txt_csv.splitlines() if re.match(r"^\d{4}/\d{1,2}/\d{1,2}", ln.strip())]
                if valid_lines:
                    valid_lines.sort(key=lambda s: s.split(",")[0])
                    last_parts = [p.strip() for p in valid_lines[-1].split(",") if p.strip()]
                    if len(last_parts) >= 7:
                        val_csv = float(last_parts[6].replace("%", ""))
                        if 30.0 <= val_csv <= 250.0: res["pc_ratio"] = round(val_csv, 1); pcr_fetched = True
        except Exception: pass

    try:
        q_end, q_start = now_tw.strftime("%Y/%m/%d"), (now_tw - datetime.timedelta(days=14)).strftime("%Y/%m/%d")
        r_fut = requests.post("https://www.taifex.com.tw/cht/3/futContractsDateDown", data={"queryStartDate": q_start, "queryEndDate": q_end}, headers=headers, timeout=8, verify=False)
        if r_fut.status_code == 200 and len(r_fut.content) > 200:
            df_f = pd.read_csv(io.StringIO(r_fut.content.decode("cp950", errors="ignore")))
            df_f.columns = [str(c).strip() for c in df_f.columns]
            if len(df_f.columns) >= 14:
                dates = sorted(df_f.iloc[:, 0].dropna().astype(str).str.strip().unique())
                if len(dates) > 0:
                    latest_d = dates[-1]
                    res["fut_date"] = latest_d
                    oi_net_col = next((c for c in df_f.columns if "未平倉" in c and "口數" in c and "淨額" in c), None)
                    oi_long_col = next((c for c in df_f.columns if "多方" in c and "未平倉" in c and "口數" in c), None)
                    oi_short_col = next((c for c in df_f.columns if "空方" in c and "未平倉" in c and "口數" in c), None)

                    daily_fini_trades = []
                    for d_cur in dates:
                        sub_d = df_f[df_f.iloc[:, 0].astype(str).str.strip() == d_cur]
                        for _, row in sub_d.iterrows():
                            prod_name, ident = str(row.iloc[1]).strip(), str(row.iloc[2]).strip()
                            net_trade = int(float(str(row.iloc[7]).replace(",", "").strip() or 0))
                            if oi_net_col: oi_net = int(float(str(row[oi_net_col]).replace(",", "").strip() or 0))
                            elif oi_long_col and oi_short_col: oi_net = int(float(str(row[oi_long_col]).replace(",", "").strip() or 0)) - int(float(str(row[oi_short_col]).replace(",", "").strip() or 0))
                            else: oi_net = int(float(str(row.iloc[13]).replace(",", "").strip() or 0))

                            if ("臺股期貨" in prod_name or "台股期貨" in prod_name) and ("小型" not in prod_name) and ("微型" not in prod_name):
                                if "外資" in ident:
                                    daily_fini_trades.append(net_trade)
                                    if d_cur == latest_d:
                                        res["fini_tx_chg"] = net_trade
                                        if oi_net != 0: res["fini_tx_oi"] = oi_net
                                elif "投信" in ident and d_cur == latest_d:
                                    res["sitc_tx_chg"] = net_trade
                                    if oi_net != 0: res["sitc_tx_oi"] = oi_net
                                elif "自營" in ident and d_cur == latest_d:
                                    res["dealer_tx_chg"] = net_trade
                            elif ("小型臺指" in prod_name or "小型台指" in prod_name) and d_cur == latest_d:
                                if "外資" in ident:
                                    res["fini_mtx_chg"] = net_trade
                                    if oi_net != 0: res["fini_mtx_oi"] = oi_net
                                elif "投信" in ident: res["sitc_mtx_chg"] = net_trade
                                elif "自營" in ident: res["dealer_mtx_chg"] = net_trade
                    res["retail_mtx_chg"] = -(res["fini_mtx_chg"] + res["sitc_mtx_chg"] + res["dealer_mtx_chg"])
                    if daily_fini_trades:
                        consec = 0
                        for t_v in reversed(daily_fini_trades):
                            if t_v > 0: consec += 1
                            else: break
                        res["consec_cover_days"] = consec
                        res["cover_3d_sum"] = int(sum(daily_fini_trades[-3:]))
    except Exception: pass

    for d_back in range(0, 6):
        chk_d = (now_tw - datetime.timedelta(days=d_back)).strftime("%Y%m%d")
        try:
            r_bfi = requests.get(f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate={chk_d}&type=day&response=json", headers=headers, timeout=6, verify=False)
            if r_bfi.status_code == 200:
                b_data = r_bfi.json().get("data", [])
                if b_data and len(b_data) >= 3:
                    for row in b_data:
                        if "避險" in str(row[0]):
                            res["warrant_hedge_yi"] = round(float(str(row[3]).replace(",", "").strip()) / 100000000.0, 2)
                            res["twse_date"] = chk_d; break
                    if res["twse_date"]: break
        except Exception: continue

    try:
        r_mgn = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN", headers=headers, timeout=6, verify=False)
        if r_mgn.status_code == 200 and len(r_mgn.json()) > 0:
            last_m = r_mgn.json()[-1]
            for k_m, v_m in last_m.items():
                if "融資金額" in str(k_m) and "今日餘額" in str(k_m):
                    val_k = float(str(v_m).replace(",", "").strip())
                    if val_k > 1000000: res["margin_balance_yi"] = round(val_k / 100000.0, 1)
                elif "融資金額" in str(k_m) and "前日餘額" in str(k_m):
                    prev_k = float(str(v_m).replace(",", "").strip())
                    if prev_k > 1000000 and res.get("margin_balance_yi"):
                        res["margin_chg_yi"] = round(res["margin_balance_yi"] - (prev_k / 100000.0), 1)
    except Exception: pass
    return res

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
    "1513": ("中興電", "🛡️政策防彈衣・GIS強韌電網龍頭", "TW"),
    "1514": ("亞力", "🛡️政策防彈衣・半導體配電與電網", "TW"),
    "8936": ("國統", "🛡️政策防彈衣・海淡廠與水資源龍頭", "TW"),
    "9933": ("中鼎", "🛡️政策防彈衣・兆元統包工程龍頭", "TW"),
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

# ==========================================
# v60.0 升級核心技術指標函式 (全數加裝 try-except 容錯)
# ==========================================
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
    except Exception:
        return 50.0, 50.0, 50.0, 50.0, False

def compute_dmi_and_atr(highs, lows, closes, n=14):
    """同步計算 Wilder 平滑之 +DI / -DI / ADX(14) 與 ATR%(14)，任何異常回傳安全預設值"""
    try:
        if len(closes) < n + 5:
            return 25.0, 25.0, 20.0, 20.0, False, np.nan
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
        pdi_now = round(float(plus_di.iloc[-1]), 1)
        mdi_now = round(float(minus_di.iloc[-1]), 1)
        pdi_prev = float(plus_di.iloc[-2]) if len(plus_di) > 1 else pdi_now
        mdi_prev = float(minus_di.iloc[-2]) if len(minus_di) > 1 else mdi_now
        adx_now = round(float(adx.iloc[-1]), 1)
        adx_prev = round(float(adx.iloc[-2]) if len(adx) > 1 else 20.0, 1)
        dmi_bull = (pdi_now > mdi_now) and (adx_now > adx_prev) and ((pdi_prev <= mdi_prev + 4.0) or (pdi_now - mdi_now > pdi_prev - mdi_prev))
        atr_val = float(atr.iloc[-1])
        curr_c = float(c.iloc[-1])
        atr_pct = round((atr_val / curr_c) * 100.0, 2) if (pd.notna(atr_val) and curr_c > 0) else np.nan
        return pdi_now, mdi_now, adx_now, adx_prev, bool(dmi_bull), atr_pct
    except Exception:
        return np.nan, np.nan, np.nan, np.nan, False, np.nan

def classify_dmi_direction(pdi, mdi, adx, j_val):
    """Wilder DMI 方向雙檢標籤判定"""
    try:
        if any(pd.isna(x) for x in [pdi, mdi, adx]):
            return "N/A", False
        if adx > 25.0 and pdi > mdi:
            return ("多頭趨勢確立(強勢鈍化)" if (pd.notna(j_val) and j_val > 100.0) else "多頭趨勢確立"), False
        if adx > 25.0 and mdi > pdi:
            return "空頭加速（防高ADX假多頭）", True
        if adx < 20.0:
            return "盤整無趨勢", False
        return ("趨勢萌芽(偏多)" if pdi >= mdi else "趨勢萌芽(偏空)"), False
    except Exception:
        return "N/A", False

def compute_obv_trend(closes, volumes):
    """計算累積 OBV 及其 5日/20日斜率，偵測價創新高但 OBV 未創高之背離"""
    try:
        if len(closes) < 22 or len(volumes) < 22:
            return "N/A", False
        diff_c = np.diff(closes, prepend=closes[0])
        dir_v = np.where(diff_c > 0, volumes, np.where(diff_c < 0, -volumes, 0.0))
        obv = np.cumsum(dir_v)
        obv_5_slope = float(obv[-1] - obv[-6])
        obv_20_slope = float(obv[-1] - obv[-21])
        p_5_hi, p_20_hi = float(np.max(closes[-5:])), float(np.max(closes[-20:]))
        o_5_hi, o_20_hi = float(np.max(obv[-5:])), float(np.max(obv[-20:]))
        if (p_5_hi >= p_20_hi * 0.995) and (o_5_hi < o_20_hi * 0.995 or obv_5_slope <= 0.0):
            return "⚠️OBV背離", True
        if obv_5_slope > 0 and obv_20_slope > 0: return "📈雙斜率向上", False
        if obv_5_slope > 0 and obv_20_slope <= 0: return "🌱短線翻揚", False
        if obv_5_slope <= 0 and obv_20_slope > 0: return "⚖️高檔換手", False
        return "📉斜率向下", False
    except Exception:
        return "N/A", False

def compute_macd(closes):
    s = pd.Series(closes)
    dif = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    return dif.values, dea.values, (dif - dea).values

@st.cache_data(ttl=600, show_spinner=False)
def download_historical_klines(yf_tickers_tuple):
    try: return yf.download(list(yf_tickers_tuple), period="1y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=600, show_spinner=False)
def download_fleet_history():
    tickers = ["^TWII", "^GSPC", "^SOX", "^VIX", "TWD=X", "00400A.TW", "00985B.TW", "00713.TW", "00631L.TW", "AVGO", "NVDA", "SGOV"]
    try: return yf.download(tickers, period="5y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=120, show_spinner=False)
def download_fleet_60m():
    try: return yf.download(["^TWII"], period="2mo", interval="60m", progress=False, group_by="ticker", auto_adjust=False)
    except Exception: return pd.DataFrame()

def get_twii_20d_return(rt_quotes: dict) -> float:
    """從共用快取取得加權指數近 20 交易日報酬率%，供 scan_all_stocks() 計算 RS_20D"""
    try:
        data = download_fleet_history()
        if not data.empty and "^TWII" in data:
            c_arr = data["^TWII"]["Close"].dropna().values.astype(float)
            if len(c_arr) >= 21:
                rt_c = rt_quotes.get("t00", {}).get("close", 0.0)
                curr_tw = float(rt_c) if (rt_c and rt_c > 0) else float(c_arr[-1])
                base_20 = float(c_arr[-21])
                if base_20 > 0:
                    return round(((curr_tw - base_20) / base_20) * 100.0, 2)
    except Exception:
        pass
    return 0.0

def fetch_and_analyze_fleet(rt_quotes, price_dict_all=None):
    if price_dict_all is None:
        price_dict, _, _, twii_official = fetch_official_market_data()
    else:
        price_dict = price_dict_all
        twii_official = {"close": 0.0, "chg_pct": 0.0, "date": ""}
    data, data_60m = download_fleet_history(), download_fleet_60m()
    now_tw = get_tw_now()
    today_str = now_tw.strftime("%Y-%m-%d")
    is_market_open_or_after = (now_tw.hour * 60 + now_tw.minute) >= 540

    limit_down_cnt = sum(1 for c, inf in price_dict.items() if len(c) == 4 and c.isdigit() and float(inf.get("chg_pct", 0.0)) <= -9.4)

    twii_now = twii_official["close"] if twii_official["close"] > 0 else 36033.3
    twii_chg_pct = twii_official["chg_pct"] if twii_official["close"] > 0 else -6.23
    twii_open = twii_high = twii_low = twii_prev_c = twii_now
    ma5, ma10, ma20, ma60, ma120, ma240 = 38500.0, 40500.0, 42800.0, 44900.0, 45000.0, 39150.0
    w_ma10, m_ma5, m_ma20, m_ma36 = 43500.0, 42800.0, 33746.0, 30370.0
    twii_30d_high, twii_60d_high, twii_30d_dd_pct = 49822.0, 49822.0, 27.68
    twii_vol_20ma_ratio, twii_lower_shadow_ratio, twii_close_above_low_pct, is_climax_hammer = 1.15, 0.4, 0.5, False
    ma5_prev, ma10_prev, ma20_prev, ma60_prev, ma120_prev, ma240_prev = ma5, ma10, ma20, ma60, ma120, ma240
    deduct_5d, deduct_10d, deduct_20d, deduct_60d, deduct_120d = ma5, ma10, ma20, ma60, ma120
    bb_upper, bb_lower = 47500.0, 34500.0
    k9, d9, j9, j9_prev, kd_cross_d = 18.0, 28.0, -2.0, 12.0, False
    pdi_d, mdi_d, adx_d, adx_d_prev, dmi_bull_d = 12.0, 48.0, 42.0, 38.0, False
    date_str, quote_src = today_str, "Yahoo Finance / 證交所"
    est_short_cost, target_cover_1, target_cover_2 = 42500.0, 40800.0, 39300.0
    k60_k, k60_d, k60_j, k60_j_prev, k60_kd_cross = 15.0, 22.0, 1.0, 10.0, False
    k60_ma5, k60_ma20, k60_ma60 = 37200.0, 39500.0, 42000.0
    fx_now, fx_prev, fx_chg_ntd, fx_ma5, fx_ma20 = 32.850, 32.860, -0.010, 32.840, 32.650

    sp500_now, sp500_ma200, sp500_below_ma200 = 6910.4, 7050.0, True
    sox_now, sox_150d_high, sox_dd_pct, sox_ma240 = 8408.6, 14655.0, 42.6, 10200.0

    etf_metrics = {
        "00400A": {"p": 9.80, "ma10": 11.20, "ma20": 12.10, "exit_t1": 10.80, "exit_t2": 11.50, "div_latest": 0.06, "div_ttm": 0.60},
        "00985B": {"p": 10.36, "ma20": 10.35, "div_latest": 0.053, "div_ttm": 0.596},
        "00713": {"p": 58.00, "ma20": 59.50, "div_latest": 1.25, "div_ttm": 3.81},
        "00631L": {"p": 25.50, "ma5": 28.00, "ma20": 31.50},
        "AVGO": {"p": 304.41}, "NVDA": {"p": 125.53}, "SGOV": {"p": 100.72}
    }

    try:
        if not data.empty and "^TWII" in data:
            df_twii = data["^TWII"].dropna(subset=["Open", "High", "Low", "Close"]).copy()
            if len(df_twii) >= 35:
                o_twii, c_twii, h_twii, l_twii, v_twii = df_twii["Open"].values.astype(float), df_twii["Close"].values.astype(float), df_twii["High"].values.astype(float), df_twii["Low"].values.astype(float), df_twii["Volume"].values.astype(float)
                last_k_date = df_twii.index[-1].strftime("%Y-%m-%d")
                if "t00" in rt_quotes and rt_quotes["t00"]["close"] > 0 and (is_market_open_or_after or abs(rt_quotes["t00"]["chg_pct"]) > 0.01):
                    rt_t00 = rt_quotes["t00"]
                    if last_k_date == today_str or (not is_market_open_or_after):
                        o_twii[-1] = rt_t00["open"] if rt_t00.get("open", 0) > 0 else o_twii[-1]
                        c_twii[-1], h_twii[-1], l_twii[-1] = rt_t00["close"], max(h_twii[-1], rt_t00["high"]), min(l_twii[-1], rt_t00["low"])
                    else:
                        o_twii = np.append(o_twii, rt_t00["open"] if rt_t00.get("open", 0) > 0 else rt_t00["close"])
                        c_twii, h_twii, l_twii, v_twii = np.append(c_twii, rt_t00["close"]), np.append(h_twii, rt_t00["high"]), np.append(l_twii, rt_t00["low"]), np.append(v_twii, v_twii[-1])
                    twii_chg_pct, twii_prev_c = rt_t00["chg_pct"], rt_t00.get("prev_close", float(c_twii[-2]))
                    date_str, quote_src = f"{today_str} {rt_t00['time']}", f"🟢 證交所 MIS ＆ Yahoo v8 雙源即時對時 ({rt_t00['time']})"
                else:
                    twii_prev_c = float(c_twii[-2]) if len(c_twii) > 1 else float(c_twii[-1])
                    twii_chg_pct = round((float(c_twii[-1]) - twii_prev_c) / twii_prev_c * 100, 2)
                    date_str = last_k_date

                twii_open, twii_now, twii_high, twii_low = round(float(o_twii[-1]), 1), round(float(c_twii[-1]), 1), round(float(h_twii[-1]), 1), round(float(l_twii[-1]), 1)
                ma5, ma10, ma20 = round(float(np.mean(c_twii[-5:])), 1), round(float(np.mean(c_twii[-10:])), 1), round(float(np.mean(c_twii[-20:])), 1)
                ma60 = round(float(np.mean(c_twii[-60:])), 1) if len(c_twii) >= 60 else ma20
                ma120 = round(float(np.mean(c_twii[-120:])), 1) if len(c_twii) >= 120 else ma60
                ma240 = round(float(np.mean(c_twii[-240:])), 1) if len(c_twii) >= 240 else ma120
                ma5_prev, ma10_prev, ma20_prev = round(float(np.mean(c_twii[-6:-1])), 1), round(float(np.mean(c_twii[-11:-1])), 1), round(float(np.mean(c_twii[-21:-1])), 1)
                ma60_prev = round(float(np.mean(c_twii[-61:-1])), 1) if len(c_twii) >= 61 else ma60
                ma120_prev = round(float(np.mean(c_twii[-121:-1])), 1) if len(c_twii) >= 121 else ma120
                ma240_prev = round(float(np.mean(c_twii[-241:-1])), 1) if len(c_twii) >= 241 else ma240
                deduct_5d, deduct_10d, deduct_20d = round(float(c_twii[-5]), 1), round(float(c_twii[-10]), 1), round(float(c_twii[-20]), 1)
                deduct_60d = round(float(c_twii[-60]), 1) if len(c_twii) >= 60 else ma60
                deduct_120d = round(float(c_twii[-120]), 1) if len(c_twii) >= 120 else ma120
                std20 = float(np.std(c_twii[-20:]))
                bb_upper, bb_lower = round(ma20 + 2.0 * std20, 1), round(ma20 - 2.0 * std20, 1)

                twii_30d_high = round(float(np.max(h_twii[-35:])), 1)
                twii_60d_high = round(float(np.max(h_twii[-65:])), 1) if len(h_twii) >= 65 else twii_30d_high
                twii_30d_dd_pct = round(((twii_30d_high - twii_now) / max(1.0, twii_30d_high)) * 100.0, 2)

                vol_20ma = float(np.mean(v_twii[-21:-1])) if len(v_twii) >= 21 else max(1.0, float(np.mean(v_twii)))
                vol_mult_now, _ = get_volume_multiplier()
                twii_vol_20ma_ratio = round((float(v_twii[-1]) * vol_mult_now) / max(1.0, vol_20ma), 2)
                body_len = max(10.0, abs(twii_now - twii_open))
                lower_sh = max(0.0, min(twii_now, twii_open) - twii_low)
                twii_lower_shadow_ratio = round(lower_sh / body_len, 2)
                twii_close_above_low_pct = round(((twii_now - twii_low) / max(1.0, twii_low)) * 100.0, 2)
                is_climax_hammer = bool(twii_vol_20ma_ratio >= 1.25 and twii_lower_shadow_ratio >= 1.5 and twii_close_above_low_pct >= 1.5)

                est_short_cost, target_cover_1, target_cover_2 = estimate_foreign_short_cost_and_target(h_twii, l_twii, c_twii, v_twii)
                k9, d9, j9, j9_prev, kd_cross_d = compute_kdj(h_twii[-60:], l_twii[-60:], c_twii[-60:])
                pdi_d, mdi_d, adx_d, adx_d_prev, dmi_bull_d, _ = compute_dmi_and_atr(h_twii[-90:], l_twii[-90:], c_twii[-90:])

                w_closes = df_twii["Close"].resample("W").last().dropna().values.astype(float)
                if len(w_closes) > 0:
                    w_closes[-1] = twii_now
                    w_ma10 = round(float(np.mean(w_closes[-10:])), 1) if len(w_closes) >= 10 else ma60
                m_closes = df_twii["Close"].resample("ME").last().dropna().values.astype(float)
                if len(m_closes) > 0:
                    m_closes[-1] = twii_now
                    m_ma5 = round(float(np.mean(m_closes[-5:])), 1) if len(m_closes) >= 5 else ma60
                    m_ma20 = round(float(np.mean(m_closes[-20:])), 1) if len(m_closes) >= 20 else round(ma240 * 0.88, 1)
                    m_ma36 = round(float(np.mean(m_closes[-36:])), 1) if len(m_closes) >= 36 else round(m_ma20 * 0.90, 1)

            if "^GSPC" in data:
                c_sp = data["^GSPC"]["Close"].dropna().values.astype(float)
                if len(c_sp) >= 200:
                    sp500_now = round(float(c_sp[-1]), 2)
                    sp500_ma200 = round(float(np.mean(c_sp[-200:])), 2)
                    sp500_below_ma200 = bool(sp500_now < sp500_ma200)

            if "^SOX" in data:
                df_sx = data["^SOX"].dropna(subset=["High", "Close"])
                if len(df_sx) >= 60:
                    c_sx, h_sx = df_sx["Close"].values.astype(float), df_sx["High"].values.astype(float)
                    sox_now = round(float(c_sx[-1]), 2)
                    sox_150d_high = round(float(np.max(h_sx[-150:])), 2)
                    sox_dd_pct = round(((sox_150d_high - sox_now) / max(1.0, sox_150d_high)) * 100.0, 2)
                    sox_ma240 = round(float(np.mean(c_sx[-240:])), 2) if len(c_sx) >= 240 else round(float(np.mean(c_sx)), 2)

            if "TWD=X" in data:
                df_fx = data["TWD=X"].dropna(subset=["Close"]).copy()
                if len(df_fx) >= 20:
                    c_fx = df_fx["Close"].values.astype(float)
                    if "USDTWD" in rt_quotes and rt_quotes["USDTWD"]["close"] > 10.0:
                        fx_now, fx_prev = round(float(rt_quotes["USDTWD"]["close"]), 3), round(float(rt_quotes["USDTWD"]["prev_close"]), 3)
                        c_fx[-1] = fx_now
                    else:
                        fx_now, fx_prev = round(float(c_fx[-1]), 3), round(float(c_fx[-2]), 3) if len(c_fx) >= 2 else round(float(c_fx[-1]), 3)
                    fx_chg_ntd, fx_ma5, fx_ma20 = round(fx_now - fx_prev, 3), round(float(np.mean(c_fx[-5:])), 3), round(float(np.mean(c_fx[-20:])), 3)

            for sym_e, code_e in [("00400A.TW", "00400A"), ("00985B.TW", "00985B"), ("00713.TW", "00713"), ("00631L.TW", "00631L"), ("AVGO", "AVGO"), ("NVDA", "NVDA"), ("SGOV", "SGOV")]:
                if sym_e in data:
                    df_e = data[sym_e].dropna(subset=["Close", "High", "Low"])
                    if len(df_e) >= 5:
                        c_e, h_e, l_e = df_e["Close"].values.astype(float), df_e["High"].values.astype(float), df_e["Low"].values.astype(float)
                        rt_e = rt_quotes.get(code_e, {}).get("close", 0.0)
                        if rt_e and rt_e > 0: c_e[-1] = float(rt_e)
                        elif code_e in price_dict and price_dict[code_e].get("close", 0) > 0: c_e[-1] = float(price_dict[code_e]["close"])
                        p_e = round(float(c_e[-1]), 2)
                        etf_metrics[code_e]["p"] = p_e
                        if code_e == "00400A" and len(c_e) >= 20:
                            m10_e, m20_e = round(float(np.mean(c_e[-10:])), 2), round(float(np.mean(c_e[-20:])), 2)
                            hi20, lo20 = float(np.max(h_e[-20:])), float(np.min(l_e[-20:]))
                            fib382, fib500 = round(lo20 + (hi20 - lo20) * 0.382, 2), round(lo20 + (hi20 - lo20) * 0.500, 2)
                            etf_metrics["00400A"].update({
                                "ma10": m10_e, "ma20": m20_e,
                                "exit_t1": round(max(p_e * 1.05, min(m10_e, fib382)), 2),
                                "exit_t2": round(max(p_e * 1.10, max(m20_e, fib500)), 2)
                            })
                        elif code_e in ["00985B", "00713", "00631L"] and len(c_e) >= 20:
                            etf_metrics[code_e]["ma5"] = round(float(np.mean(c_e[-5:])), 2)
                            etf_metrics[code_e]["ma20"] = round(float(np.mean(c_e[-20:])), 2)
    except Exception: pass

    try:
        if not data_60m.empty and "^TWII" in data_60m:
            df_60 = data_60m["^TWII"].dropna(subset=["Open", "High", "Low", "Close"]).copy()
            if len(df_60) >= 30:
                c_60, h_60, l_60 = df_60["Close"].values.astype(float), df_60["High"].values.astype(float), df_60["Low"].values.astype(float)
                if abs(c_60[-1] - twii_now) > 1.0: c_60[-1], h_60[-1], l_60[-1] = twii_now, max(h_60[-1], twii_now), min(l_60[-1], twii_now)
                k60_ma5, k60_ma20 = round(float(np.mean(c_60[-5:])), 1), round(float(np.mean(c_60[-20:])), 1)
                k60_ma60 = round(float(np.mean(c_60[-60:])), 1) if len(c_60) >= 60 else k60_ma20
                k60_k, k60_d, k60_j, k60_j_prev, k60_kd_cross = compute_kdj(h_60[-60:], l_60[-60:], c_60[-60:])
    except Exception: pass

    raw_deriv = fetch_auto_derivatives_base(price_dict)
    live_maint = compute_dynamic_pure_stock_maint(twii_now, ma20, ma60)
    deriv_info = {
        **raw_deriv,
        "real_fini_oi": raw_deriv["fini_tx_oi"] + raw_deriv["sitc_tx_oi"],
        "est_short_cost": est_short_cost, "target_cover_1": target_cover_1, "target_cover_2": target_cover_2,
        "short_profit_pts": round(est_short_cost - twii_now, 0),
        "short_profit_pct": round((est_short_cost - twii_now) / max(1.0, est_short_cost) * 100.0, 2),
        "stock_only_maint_pct": live_maint,
        "fx_now": fx_now, "fx_prev": fx_prev, "fx_chg_ntd": fx_chg_ntd, "fx_ma5": fx_ma5, "fx_ma20": fx_ma20
    }

    return {
        "twii_open": twii_open, "twii_prev_c": round(twii_prev_c, 1), "twii_now": twii_now, "twii_chg_pct": twii_chg_pct,
        "twii_high": twii_high, "twii_low": twii_low,
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60, "ma120": ma120, "ma240": ma240,
        "ma5_prev": ma5_prev, "ma10_prev": ma10_prev, "ma20_prev": ma20_prev, "ma60_prev": ma60_prev, "ma120_prev": ma120_prev, "ma240_prev": ma240_prev,
        "deduct_5d": deduct_5d, "deduct_10d": deduct_10d, "deduct_20d": deduct_20d, "deduct_60d": deduct_60d, "deduct_120d": deduct_120d,
        "w_ma10": w_ma10, "m_ma5": m_ma5, "m_ma20": m_ma20, "m_ma36": m_ma36,
        "bb_upper": bb_upper, "bb_lower": bb_lower,
        "twii_30d_high": twii_30d_high, "twii_60d_high": twii_60d_high, "twii_30d_dd_pct": twii_30d_dd_pct,
        "twii_vol_20ma_ratio": twii_vol_20ma_ratio, "twii_lower_shadow_ratio": twii_lower_shadow_ratio,
        "twii_close_above_low_pct": twii_close_above_low_pct, "is_climax_hammer": is_climax_hammer,
        "sp500_now": sp500_now, "sp500_ma200": sp500_ma200, "sp500_below_ma200": sp500_below_ma200,
        "sox_now": sox_now, "sox_150d_high": sox_150d_high, "sox_dd_pct": sox_dd_pct, "sox_ma240": sox_ma240,
        "limit_down_cnt": limit_down_cnt,
        "bias_5": round((twii_now - ma5) / max(1.0, ma5) * 100, 2),
        "bias_10": round((twii_now - ma10) / max(1.0, ma10) * 100, 2),
        "bias_20": round((twii_now - ma20) / max(1.0, ma20) * 100, 2),
        "k9": k9, "d9": d9, "j9": j9, "j9_prev": j9_prev, "pdi_d": pdi_d, "mdi_d": mdi_d, "adx_d": adx_d,
        "k60_k": k60_k, "k60_d": k60_d, "k60_j": k60_j, "k60_j_prev": k60_j_prev,
        "k60_ma5": k60_ma5, "k60_ma20": k60_ma20, "k60_ma60": k60_ma60,
        "date_str": date_str, "quote_src": quote_src, "deriv": deriv_info, "etf_metrics": etf_metrics
    }

def scan_all_stocks():
    price_dict, tdcc_dict, rev_dict, _ = fetch_official_market_data()
    msci_data = fetch_dynamic_msci_data(len(price_dict))
    cb_set = fetch_active_cb_codes()
    vol_mult, vol_note = get_volume_multiplier()
    now_tw = get_tw_now()
    today_str = now_tw.strftime("%Y-%m-%d")
    is_market_open = (now_tw.weekday() < 5) and ((now_tw.hour * 60 + now_tw.minute) >= 540)

    candidates = dict(CORE_WATCHLIST)

    msci_code_role = {}
    for role_lbl, lst_k in [("🌟MSCI標準新增", "std_add"), ("📈MSCI小型新增", "small_add"), ("📉MSCI標準刪除", "std_del"), ("⚠️MSCI小型刪除", "small_del")]:
        for c, nm, th, def_mkt in msci_data.get(lst_k, []):
            real_mkt = price_dict.get(c, {}).get("market", def_mkt)
            msci_code_role[c] = (role_lbl, th)
            if c not in candidates:
                candidates[c] = (price_dict.get(c, {}).get("name", nm), f"{role_lbl}・{th}", real_mkt)

    for th_name, bell_list in MSCI_THEME_BELLWETHERS.items():
        for bc, bnm in bell_list:
            if bc not in candidates:
                real_mkt = price_dict.get(bc, {}).get("market", "TW")
                candidates[bc] = (price_dict.get(bc, {}).get("name", bnm), f"👑族群指標・{th_name}", real_mkt)

    sorted_short_pool = sorted(
        [(c, info) for c, info in price_dict.items() if c not in candidates and len(c) == 4 and c.isdigit() and (c not in CONSTRUCTION_BLACKLIST) and ("營造" not in info.get("sector", "")) and (not is_zombie_stock_tiered(c, info["close"], info["vol_lots"] * vol_mult)) and info["chg_pct"] >= 2.0 and info["close"] >= 15.0],
        key=lambda x: (x[1]["chg_pct"] * 0.7 + min(5.0, (x[1]["vol_lots"] / 2000.0)) * 0.3), reverse=True
    )
    for code, info in sorted_short_pool[:70]:
        candidates[code] = (info["name"], info.get("sector") or "⚡短線技術破繭", info["market"])

    sorted_inst_pool = sorted(
        [(c, info) for c, info in price_dict.items() if c not in candidates and len(c) == 4 and c.isdigit() and (c not in CONSTRUCTION_BLACKLIST) and ("營造" not in info.get("sector", "")) and (not is_zombie_stock_tiered(c, info["close"], info["vol_lots"] * vol_mult)) and info["close"] >= 18.0 and (info.get("fini_lots", 0) >= 100 or info.get("sitc_lots", 0) >= 30) and rev_dict.get(c, -1.0) > 0.0],
        key=lambda x: (x[1].get("sitc_lots", 0) * 3 + x[1].get("fini_lots", 0)), reverse=True
    )
    for code, info in sorted_inst_pool[:30]:
        candidates[code] = (info["name"], info.get("sector") or "🏦法人鎖碼成長", info["market"])

    mis_targets = [(c, m) for c, (_, _, m) in candidates.items()]
    rt_quotes = fetch_mis_realtime_batch(tuple(mis_targets))
    twii_20d_ret = get_twii_20d_return(rt_quotes)
    yf_map = {f"{c}.{'TW' if m == 'TW' else 'TWO'}": c for c, (_, _, m) in candidates.items()}
    raw_df = download_historical_klines(tuple(yf_map.keys()))

    us_sec_info = fetch_us_sectors_and_leaders()
    tnx_now = us_sec_info.get("tnx_val", 4.94)
    fx_rt_val = rt_quotes.get("USDTWD", {}).get("close", 32.85)
    macro_withdrawal_alert = bool(tnx_now >= 5.0 and fx_rt_val >= 32.50)
    odm_health_map = fetch_odm_inventory_ocf_health(tuple(OLD_GRANARY_ODM_WATCH))
    disp_map = msci_data.get("disposition_map", {})

    list_tab2_launch, list_tab2_pullback, list_tab2_orbit, list_tab3_core_bluechip, list_tab3_turnaround_or_dip = [], [], [], [], []
    list_granary_audit, list_msci_audit, scanned_sector_records = [], [], []
    all_scanned_details = {}

    for yf_tkr, code in yf_map.items():
        try:
            if raw_df.empty or yf_tkr not in raw_df: continue
            df_s = raw_df[yf_tkr].dropna(subset=["Open", "High", "Low", "Close"]).copy()
            if len(df_s) < 35: continue
            o = df_s["Open"].values.astype(float)
            c = df_s["Close"].values.astype(float)
            h = df_s["High"].values.astype(float)
            l = df_s["Low"].values.astype(float)
            v = df_s["Volume"].values.astype(float) / 1000.0
            last_k_date = df_s.index[-1].strftime("%Y-%m-%d")
            name, sector, mkt_code = candidates[code]

            if code in rt_quotes and rt_quotes[code]["close"] > 0:
                rt = rt_quotes[code]
                if last_k_date == today_str:
                    if rt.get("open", 0) > 0: o[-1] = rt["open"]
                    c[-1], h[-1], l[-1] = rt["close"], max(h[-1], rt["high"]), min(l[-1], rt["low"])
                    if rt["vol_lots"] > 0: v[-1] = float(rt["vol_lots"])
                elif is_market_open and abs(rt["close"] - rt["prev_close"]) > 0.001:
                    o = np.append(o, rt["open"] if rt.get("open", 0) > 0 else rt["close"])
                    c, h, l = np.append(c, rt["close"]), np.append(h, rt["high"]), np.append(l, rt["low"])
                    v = np.append(v, float(rt["vol_lots"]) if rt["vol_lots"] > 0 else v[-1])

            curr_p, prev_p = round(float(c[-1]), 2), float(c[-2])
            chg_1d = round((curr_p - prev_p) / prev_p * 100, 2) if prev_p > 0 else 0.0
            if abs(chg_1d) < 0.01 and code in price_dict and abs(price_dict[code].get("chg_pct", 0.0)) > 0.01 and not is_market_open:
                chg_1d = price_dict[code]["chg_pct"]
            chg_3d = round((curr_p - float(c[-4])) / float(c[-4]) * 100, 2) if len(c) >= 4 else chg_1d
            est_vol = int(round(v[-1] * vol_mult))
            yest_vol = max(1.0, float(v[-2])) if len(v) >= 2 else max(1.0, float(v[-1]))
            yod_vol_ratio = round(est_vol / yest_vol, 2)
            ma5_vol = max(1.0, float(np.mean(v[-6:-1])))
            vol_ratio = round(est_vol / ma5_vol, 2)

            is_protected = (code in CORE_WATCHLIST) or (code in msci_code_role)
            if (not is_protected) and is_zombie_stock_tiered(code, curr_p, est_vol, ma5_vol):
                continue

            p_meta = price_dict.get(code, {})
            official_sec = p_meta.get("sector") or sector
            scanned_sector_records.append({
                "code": code, "name": name, "sector": sector, "official_sec": official_sec,
                "close": curr_p, "chg_1d": chg_1d, "est_vol": est_vol, "yest_vol": yest_vol,
                "yod_ratio": yod_vol_ratio, "ma5_ratio": vol_ratio, "turnover_yi": round(est_vol * curr_p / 100000.0, 2)
            })

            disp_badge = " 🚨[處置注意]" if code in disp_map else ""
            liq_tag = " ⚠️量縮控盤" if is_zombie_stock_tiered(code, curr_p, est_vol, ma5_vol) else ""
            cb_tag = " 🔥[CB可轉債]" if code in cb_set else ""

            ma5 = round(float(np.mean(c[-5:])), 2)
            ma10 = round(float(np.mean(c[-10:])), 2)
            ma20 = round(float(np.mean(c[-20:])), 2)
            ma60 = round(float(np.mean(c[-60:])), 2) if len(c) >= 60 else ma20
            ma120 = round(float(np.mean(c[-120:])), 2) if len(c) >= 120 else ma60
            ma240 = round(float(np.mean(c[-240:])), 2) if len(c) >= 240 else ma120
            std20 = float(np.std(c[-20:]))
            bb_up = round(ma20 + 2.0 * std20, 2)
            bb_bw = round((4.0 * std20 / ma20) * 100, 1) if ma20 > 0 else 99.0
            prev_ma5, prev_ma10, prev_ma20 = float(np.mean(c[-6:-1])), float(np.mean(c[-11:-1])), float(np.mean(c[-21:-1]))
            ma_tangle_pct = round((max(prev_ma5, prev_ma10, prev_ma20) - min(prev_ma5, prev_ma10, prev_ma20)) / max(0.1, prev_ma20) * 100, 2)

            # [新指標 1 & 5] Wilder DMI 方向雙檢 ＋ ATR% 波動尺
            stk_k9, stk_d9, stk_j9, stk_j_prev, kd_cross_recent = compute_kdj(h[-60:], l[-60:], c[-60:])
            stk_j_up = stk_j9 > stk_j_prev
            pdi, mdi, adx, adx_prev, dmi_bull_active, atr_pct = compute_dmi_and_atr(h[-90:], l[-90:], c[-90:])
            dmi_dir_tag, is_bear_accel = classify_dmi_direction(pdi, mdi, adx, stk_j9)
            dmi_full_str = f"+DI:{pdi}/-DI:{mdi}/ADX:{adx} [{dmi_dir_tag}]" if pd.notna(adx) else "N/A"
            atr_str = (f"{atr_pct:.1f}% ⚠️高波動(建議收盤確認/倉位減半)" if atr_pct > 4.5 else f"{atr_pct:.1f}%") if pd.notna(atr_pct) else "N/A"
            adx_delta, adx_up = (round(adx - adx_prev, 1), adx > adx_prev) if pd.notna(adx) else (0.0, False)
            kd_dmi_resonance = (stk_k9 >= stk_d9) and pd.notna(pdi) and (pdi > mdi) and adx_up and (adx >= 18.0) and (kd_cross_recent or dmi_bull_active)

            # [新指標 2] BIAS 乖離率追高煞車 (固定兩段式：> +10% 黃燈⚠️追高過熱 / > +12% 紅燈🚨極度過熱)
            try:
                bias_10 = round(((curr_p - ma10) / ma10) * 100.0, 1) if ma10 > 0 else np.nan
                bias_20 = round(((curr_p - ma20) / ma20) * 100.0, 1) if ma20 > 0 else np.nan
                if pd.isna(bias_10): bias10_str = "N/A"
                elif bias_10 > 12.0: bias10_str = f"{bias_10:+.1f}% 🚨極度過熱"
                elif bias_10 > 10.0: bias10_str = f"{bias_10:+.1f}% ⚠️追高過熱"
                else: bias10_str = f"{bias_10:+.1f}%"
                bias20_str = f"{bias_20:+.1f}%" if pd.notna(bias_20) else "N/A"
            except Exception:
                bias_10, bias_20, bias10_str, bias20_str = np.nan, np.nan, "N/A", "N/A"

            # [新指標 3] 上影線% × 收盤位階% × 當沖率% (假突破三合一防呆)
            try:
                day_range = float(h[-1] - l[-1])
                if day_range < 1e-6:
                    upper_sh_pct = 0.0
                    close_pos_pct = 100.0 if chg_1d >= 0 else 0.0
                else:
                    upper_sh_pct = round(((float(h[-1]) - max(float(o[-1]), float(c[-1]))) / day_range) * 100.0, 1)
                    close_pos_pct = round(((float(c[-1]) - float(l[-1])) / day_range) * 100.0, 1)
                close_pos_str = f"{close_pos_pct:.0f}% (收破中軸)" if close_pos_pct < 50.0 else f"{close_pos_pct:.0f}%"
            except Exception:
                upper_sh_pct, close_pos_pct, close_pos_str = np.nan, np.nan, "N/A"
            day_trade_str = p_meta.get("day_trade_str", "N/A (交由淘金客聯網查核)")

            # [新指標 4] OBV 趨勢 × RS_20D 相對大盤強度 × 400/千張大戶週變動
            obv_tag, is_obv_div = compute_obv_trend(c[-60:], v[-60:])
            try:
                if len(c) >= 21 and c[-21] > 0:
                    stk_20d_ret = ((curr_p - float(c[-21])) / float(c[-21])) * 100.0
                    rs_20d = round(stk_20d_ret - twii_20d_ret, 1)
                    is_counter_strong = (twii_20d_ret < 0.0) and (rs_20d > 0.0) and (curr_p >= float(np.max(c[-20:])) * 0.985 or stk_20d_ret > 0.0)
                    rs_20d_str = f"{rs_20d:+.1f}% (逆勢抗跌)" if is_counter_strong else f"{rs_20d:+.1f}%"
                else:
                    rs_20d, rs_20d_str = np.nan, "N/A"
            except Exception:
                rs_20d, rs_20d_str = np.nan, "N/A"

            tdcc_400 = tdcc_dict.get(code, None)
            tdcc_1000 = p_meta.get("tdcc_1000", None)
            d400 = p_meta.get("tdcc_400_delta", None)
            d1000 = p_meta.get("tdcc_1000_delta", None)
            if tdcc_400 is not None:
                s400 = f"{tdcc_400:.1f}%" + (f"({d400:+.2f}%)" if d400 is not None else "")
                s1000 = (f"{tdcc_1000:.1f}%" + (f"({d1000:+.2f}%)" if d1000 is not None else "")) if tdcc_1000 is not None else "N/A"
                tdcc_full_str = f"400張:{s400}/千張:{s1000}"
            else:
                tdcc_full_str = "N/A"

            raw_ceiling = max(bb_up, round(float(np.max(h[-21:-1])), 2))
            castle_ceiling = round(curr_p * 1.07, 2) if raw_ceiling <= curr_p else round(raw_ceiling, 2)
            castle_iron = round(min(curr_p, max(ma10, float(l[-1])) if curr_p >= ma10 else min(ma20, float(l[-1]))), 2)
            castle_stop = round(min(ma60, float(np.min(l[-20:]))), 2)

            yoy_val, cum_yoy_val, pe_official = rev_dict.get(code, None), p_meta.get("cum_yoy", None), p_meta.get("pe", np.nan)
            f_lots, s_lots = p_meta.get("fini_lots", 0), p_meta.get("sitc_lots", 0)
            inst_tag = format_inst_chip_tag(f_lots, s_lots)
            yoy_str = f"{yoy_val:+.1f}%" if yoy_val is not None else "待公告"
            cum_yoy_str = f"{cum_yoy_val:+.1f}%" if cum_yoy_val is not None else "待公告"
            granary_full_tag, granary_bonus, granary_short_badge = evaluate_granary_and_anti_trap(code, yoy_val, odm_health_map, macro_withdrawal_alert)

            is_blue_chip, is_fin, is_defense = (code in CORE_WATCHLIST), (code in FINANCIAL_BLUE_CHIPS), (code in POLICY_DEFENSE_ARMOR)
            has_solid_fund = (yoy_val is not None and yoy_val > 0.0) and (cum_yoy_val is None or cum_yoy_val > 0.0)
            if "疑似墊料壓貨" in granary_full_tag: short_track_tag = f"🐀疑似墊料壓貨({granary_bonus}分)"
            elif is_defense: short_track_tag = f"🛡️政策內需防彈衣(+{granary_bonus}分)"
            elif has_solid_fund: short_track_tag = f"🔥有基之彈｜{granary_short_badge}"
            elif is_blue_chip and (cum_yoy_val is None or cum_yoy_val > 0.0): short_track_tag = f"🏦核心權值｜{granary_short_badge}"
            else: short_track_tag = "🎭純技術炒作(不看基本面)"

            est_eps, pe_disp, eps_entry_p, eps_target_p, val_diag = compute_eps_valuation(code, curr_p, sector, yoy_val, cum_yoy_val, pe_official, support_price=castle_iron)

            row_base = {
                "代碼": code, "股名": f"{name}{disp_badge}{cb_tag}{liq_tag}", "族群": sector,
                "穀倉賽道與防墊料": granary_full_tag,
                "現價": curr_p, "今日%": chg_1d, "3日%": chg_3d, "短線屬性": short_track_tag,
                "DMI方向雙檢": dmi_full_str, "日J值": stk_j9,
                "BIAS10": bias10_str, "BIAS20": bias20_str,
                "5MA量比": vol_ratio, "上影線%": upper_sh_pct, "收盤位階": close_pos_str, "當沖率": day_trade_str,
                "OBV趨勢": obv_tag, "RS20D%": rs_20d_str, "大戶400/千張": tdcc_full_str, "ATR%波動尺": atr_str,
                "法人動向": inst_tag, "外資買賣(張)": f_lots, "投信買賣(張)": s_lots,
                "預估EPS(元)": est_eps, "本益比(PE)": pe_disp, "💎EPS合理切入價": eps_entry_p, "🎯EPS目標價": eps_target_p, "估值診斷": val_diag,
                "昨日量比": yod_vol_ratio, "預估量(張)": est_vol,
                "天花板(反壓)": castle_ceiling, "月線承接": ma20, "鋼鐵防線": castle_iron, "極限停損": castle_stop,
                "單月營收YoY": yoy_str, "累計年營收YoY": cum_yoy_str
            }

            # 完整明細入庫供 Mode A / Mode B 戰情數據包直接提取
            all_scanned_details[code] = {
                **row_base,
                "_raw_name": name, "_mkt": mkt_code, "_cb": ("🔥含CB可轉債" if code in cb_set else "➖"),
                "_ma5": ma5, "_ma10": ma10, "_ma20": ma20, "_ma60": ma60, "_ma120": ma120, "_ma240": ma240,
                "_pdi": pdi, "_mdi": mdi, "_adx": adx, "_dmi_tag": dmi_dir_tag, "_atr_pct": atr_pct,
                "_tdcc_400": tdcc_400, "_tdcc_1000": tdcc_1000, "_d400": d400, "_d1000": d1000,
                "_pattern_tag": short_track_tag
            }

            if code in msci_code_role:
                m_role, m_theme = msci_code_role[code]
                disp_msg = disp_map.get(code, "✅ 正常交易 (11/30 尾盤生效)")
                list_msci_audit.append({"MSCI異動別": m_role, "影響細分族群": m_theme, "處置與生效狀態": disp_msg, **row_base})

            if code in (GRANARY_1_CSP_COMMON | GRANARY_2_POWER_THERMAL | GRANARY_3_EDGE_MEMORY | POLICY_DEFENSE_ARMOR | OLD_GRANARY_ODM_WATCH):
                base_g_score = 85 + granary_bonus + (4 if curr_p >= ma20 else -3) + (3 if (pd.notna(pdi) and pdi > mdi) else 0) + (3 if (f_lots > 0 or s_lots > 0) else 0)
                list_granary_audit.append({"穀倉戰略總分": max(30, min(115, base_g_score)), **row_base})

            # 右側多方進場榜：強制排除 is_bear_accel (ADX>25 且 -DI>+DI) 與疑似墊料壓貨股！
            is_heavy_dump = (chg_1d <= -2.0 and vol_ratio >= 1.25) or (chg_1d <= -4.2)
            is_above_ma20_trend = (curr_p >= ma20) and (curr_p >= ma5) and (curr_p >= ma10) and (curr_p >= ma60 or ma20 >= ma60 * 0.94)
            is_true_ma20_breakout = (curr_p >= ma20) and (curr_p >= ma5) and (curr_p >= ma10) and (prev_p < ma20) and (chg_1d >= 3.5) and (vol_ratio >= 1.25 or chg_1d >= 8.5) and pd.notna(pdi) and (pdi > mdi)

            if (is_above_ma20_trend or is_true_ma20_breakout) and (not is_heavy_dump) and (not is_bear_accel) and ("疑似墊料壓貨" not in granary_full_tag):
                is_overheated = (chg_3d >= 14.0) or (pd.notna(bias_10) and bias_10 > 12.0) or (stk_j9 >= 93.0 and not kd_dmi_resonance and chg_1d < 8.5)
                if (is_true_ma20_breakout and not is_overheated) or ((chg_1d >= 2.0 and (vol_ratio >= 1.20 or chg_1d >= 7.5) and kd_dmi_resonance and not is_overheated) or (chg_1d >= 2.8 and (vol_ratio >= 1.25 or chg_1d >= 7.5) and not is_overheated and pd.notna(pdi) and (pdi > mdi) and adx >= 16.0)):
                    if kd_dmi_resonance: raw_sc, pattern = 95, f"👑 KD×DMI雙金叉共振旱地拔蔥 [{granary_short_badge}]"
                    elif bb_bw <= 16.0: raw_sc, pattern = 93, f"🔥 布林壓縮旱地拔蔥 [{granary_short_badge}]"
                    elif ma_tangle_pct <= 4.0: raw_sc, pattern = 92, f"🌸 均線糾結旱地拔蔥 [{granary_short_badge}]"
                    elif is_true_ma20_breakout: raw_sc, pattern = 91, f"🌱 帶量長紅突破月線20MA [{granary_short_badge}]"
                    else: raw_sc, pattern = min(90, 82 + int(min(8, vol_ratio * 2.0 + chg_1d))), f"🚀 站穩月線帶量突破頸線 [{granary_short_badge}]"
                    all_scanned_details[code]["_pattern_tag"] = pattern
                    list_tab2_launch.append({"實戰優先分": min(115, raw_sc + granary_bonus), "名將戰法診斷": pattern, **row_base})
                elif (-3.5 <= chg_1d <= 2.2) and (chg_3d >= -6.5) and (vol_ratio <= 0.95) and (stk_j9 <= 68.0) and pd.notna(pdi) and (pdi >= mdi):
                    pattern = f"⚡ N字量縮守月線 [{granary_short_badge}]"
                    all_scanned_details[code]["_pattern_tag"] = pattern
                    list_tab2_pullback.append({"蓄勢評分": min(115, 84 + int((1.0 - vol_ratio) * 15) + granary_bonus), "名將戰法診斷": pattern, **row_base})
                elif curr_p >= ma5 >= ma10 >= ma20 and pd.notna(pdi) and (pdi > mdi) and chg_1d >= -2.0:
                    pattern = f"🔥 3日急拉過熱 [{granary_short_badge}]" if (chg_3d >= 10.5 or (pd.notna(bias_10) and bias_10 > 10.0)) else f"🛡️ 沿5/10MA穩步推升 [{granary_short_badge}]"
                    all_scanned_details[code]["_pattern_tag"] = pattern
                    list_tab2_orbit.append({"趨勢狀態": pattern, **row_base})

            if is_blue_chip and (not is_heavy_dump):
                bc_rev_ok = has_solid_fund or (yoy_val is not None and yoy_val >= 0.0) or (cum_yoy_val is not None and cum_yoy_val >= 0.0) or is_fin or is_defense
                fund_qual = "🔥單月+累計雙成長" if has_solid_fund else "🏦累計年增穩健"
                if bc_rev_ok and (curr_p >= ma20) and pd.notna(pdi) and (pdi > mdi) and (not is_bear_accel) and (chg_3d < 9.5) and (stk_j9 <= 88.0):
                    l_score = max(40, min(115, 85 + granary_bonus + (3 if is_fin else 0) + (4 if has_solid_fund else 0) + (3 if "低估鑽石" in val_diag else 0)))
                    list_tab3_core_bluechip.append({"波段總分": l_score, "長線格局定性": f"{granary_short_badge}＋{fund_qual}", **row_base})
                elif bc_rev_ok and (curr_p < ma20) and (curr_p >= ma240 * 0.85):
                    dip_score = max(40, min(115, 82 + granary_bonus + (4 if chg_1d >= 0.0 else 0) + (3 if stk_j_up else 0) + (3 if "低估鑽石" in val_diag else 0) - (10 if is_bear_accel else 0)))
                    dip_tag = f"🚨【{granary_short_badge}・空頭加速警戒】" if is_bear_accel else (f"🟢【{granary_short_badge}・月線下築底反彈】(上檔月線{ma20})" if (curr_p >= ma5 and chg_1d >= 0.0) else f"⏳【{granary_short_badge}・股災超跌回測支撐】")
                    list_tab3_turnaround_or_dip.append({"波段總分": dip_score, "類型與轉機標註": dip_tag, **row_base})
            elif (not is_blue_chip) and (code not in CONSTRUCTION_BLACKLIST) and (not is_heavy_dump) and (not is_bear_accel):
                if (yoy_val is not None and 25.0 <= yoy_val <= 400.0) and (tdcc_400 is not None and tdcc_400 >= 48.0) and (curr_p >= ma20 >= ma60) and pd.notna(pdi) and (pdi > mdi) and (stk_j9 <= 85.0):
                    t_row = dict(row_base)
                    t_row["股名"] = f"🔄【轉機股】{name}{cb_tag}{liq_tag}"
                    list_tab3_turnaround_or_dip.append({"波段總分": 92 + granary_bonus, "類型與轉機標註": f"🔄【轉機爆發】單月YoY{yoy_str}/累計{cum_yoy_str}＋DMI翻揚", **t_row})
        except Exception: continue

    st.session_state["scanned_sector_records"] = scanned_sector_records
    st.session_state["all_scanned_details"] = all_scanned_details
    st.session_state["granary_audit_df"] = pd.DataFrame(list_granary_audit).sort_values(by=["穀倉戰略總分", "今日%"], ascending=[False, False]) if list_granary_audit else pd.DataFrame()
    st.session_state["msci_audit_df"] = pd.DataFrame(list_msci_audit) if list_msci_audit else pd.DataFrame()
    st.session_state["macro_withdrawal_alert"] = macro_withdrawal_alert
    st.session_state["tnx_now"] = tnx_now

    df_t1 = pd.DataFrame(list_tab2_launch).sort_values(by="實戰優先分", ascending=False) if list_tab2_launch else pd.DataFrame()
    df_t2 = pd.DataFrame(list_tab2_pullback).sort_values(by="蓄勢評分", ascending=False) if list_tab2_pullback else pd.DataFrame()
    df_t3 = pd.DataFrame(list_tab2_orbit).sort_values(by="3日%", ascending=False) if list_tab2_orbit else pd.DataFrame()
    df_long_bc = pd.DataFrame(list_tab3_core_bluechip).sort_values(by="波段總分", ascending=False) if list_tab3_core_bluechip else pd.DataFrame()
    df_long_turn = pd.DataFrame(list_tab3_turnaround_or_dip).sort_values(by=["波段總分", "今日%"], ascending=[False, False]) if list_tab3_turnaround_or_dip else pd.DataFrame()
    return df_t1, df_t2, df_t3, df_long_bc, df_long_turn, vol_note, rt_quotes, msci_data, price_dict
    # ==========================================
# 【v60.0 下半部完整程式碼：淘金客 v24.1 雙腦互補架構（5組接軌指標展示 ＋ 雙模式一鍵複製戰情包）】
# 直接貼在 scan_all_stocks() 的 return 那一行正下方 (上半部維持 v60.0 不動！)
# ==========================================
import base64

def clean_html(raw_html: str) -> str:
    """移除多行 HTML 字串之行首縮排與空行，防止 Streamlit Markdown 誤判為程式碼區塊"""
    return "\n".join(line.strip() for line in raw_html.splitlines() if line.strip())


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
    if code in ["3711", "6239", "2449", "3264", "6187", "3131", "3583", "6683", "7822", "8021"] or any(k in s_all for k in ["封測", "扇形封裝", "CoWoS", "封裝", "測試"]):
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
        return {"hot_html": "", "cold_html": "", "hot_text": "暫無數據", "cold_text": "暫無數據", "top1_name": "防禦與主流族群"}

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
        return {"hot_html": "", "cold_html": "", "hot_text": "暫無數據", "cold_text": "暫無數據", "top1_name": "防禦與主流族群"}

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


def evaluate_crash_and_bottom_radar(
    twii_now, twii_ma240, twii_30d_dd_pct, sp500_below_ma200, vix_now, sox_dd_pct,
    fini_oi, margin_dd_pct, margin_2d_chg_yi, maint_pct,
    is_climax_hammer, twii_vol_ratio, twii_sh_ratio,
    basis_pts, basis_stabilized, consec_cover_days, cover_3d_sum,
    limit_down_cnt, had_limit_down_100
) -> dict:
    """【淘金客 v24.1 股災預警與落底判定雙雷達（全動態浮動計算 ＋ 側邊欄覆寫）】"""
    w_items = [
        (twii_30d_dd_pct >= 12.0, f"① 30日急殺跌速 >= 12%（目前回檔 <b>-{twii_30d_dd_pct:.1f}%</b>）"),
        ((twii_now < twii_ma240) or sp500_below_ma200, f"② 長線生命線失守（加權 {'<' if twii_now < twii_ma240 else '>='} 年線{twii_ma240:,.0f} ｜ 標普 {'< 200MA' if sp500_below_ma200 else '>= 200MA'}）"),
        ((vix_now >= 30.0) or (sox_dd_pct >= 25.0), f"③ 美股恐慌爆表（VIX <b>{vix_now:.2f}</b> ｜ 費半高點跌幅 <b>-{sox_dd_pct:.1f}%</b>）"),
        (fini_oi < -60000, f"④ 外資期貨重空 > 6萬口（目前淨空單 <b>{fini_oi:+,} 口</b>）"),
        (margin_dd_pct < twii_30d_dd_pct, f"⑤ 融資減幅尚未追上大盤跌幅（融資減 <b>-{margin_dd_pct:.1f}%</b> vs 大盤跌 <b>-{twii_30d_dd_pct:.1f}%</b>）")
    ]
    w_count = sum(1 for flag, _ in w_items if flag)
    crash_warning_on = (w_count >= 3)

    b_items = [
        ((margin_dd_pct >= twii_30d_dd_pct) or (margin_2d_chg_yi <= -500.0) or (maint_pct <= 155.0), f"① 融資真斷頭清洗（近2日融資 <b>{margin_2d_chg_yi:+.1f}億</b> ｜ 維持率 <b>{maint_pct:.2f}%</b>）"),
        (is_climax_hammer, f"② 爆量長下影線（量比 <b>{twii_vol_ratio:.2f}x</b>>=1.25x ｜ 下影/實體 <b>{twii_sh_ratio:.2f}x</b>>=1.5x）"),
        ((basis_pts <= -100.0) and basis_stabilized, f"③ 期現貨恐慌逆價差收斂（逆價差 <b>{basis_pts:+.0f}點</b> ｜ {'已止穩不擴大' if basis_stabilized else '仍擴大恐慌中'}）"),
        ((consec_cover_days >= 3) and (cover_3d_sum >= 10000), f"④ 外資空單連3日回補 > 1萬口（連補 <b>{consec_cover_days}日</b> ｜ 3日累計 <b>{cover_3d_sum:+,}口</b>）"),
        (had_limit_down_100 and (limit_down_cnt < 20), f"⑤ 恐慌廣度收斂（當日跌停 <b>{limit_down_cnt} 家</b> {'< 20家達標' if (had_limit_down_100 and limit_down_cnt < 20) else '尚未收斂至<20家'}）")
    ]
    b_count = sum(1 for flag, _ in b_items if flag)
    bottom_confirmed = (b_count >= 4)

    if crash_warning_on and (not bottom_confirmed):
        master_state = "LOCKED"
        master_badge = f"🔴 總開關【紅燈鎖死】（預警 {w_count}/5 觸發・落底僅 {b_count}/5 未達標）"
        master_action = "嚴禁追買個股與開槓桿！死守水庫二傳家寶底倉、靜待月 20MA 支撐與落底 4/5 共振，僅執行比價換股與逢彈調節 00400A。"
    elif crash_warning_on and bottom_confirmed:
        master_state = "UNLOCKED_DIP"
        master_badge = f"🟢 總開關【落底解鎖】（預警 {w_count}/5 ＆ 落底共振 {b_count}/5 達標！）"
        master_action = "解除買進鎖定！開放機動彈藥分 3 批布局新穀倉1共通規格股、2027 MSCI 外資主流股與水庫三防禦部位！"
    else:
        master_state = "NORMAL"
        master_badge = f"☀️ 總開關【常態開放】（預警 {w_count}/5 安全・順勢篩選操作）"
        master_action = "非暴力股災環境，依 5 組量化接軌指標與淘金客 v24.1 雙腦守門員機制正常操作。"

    return {
        "w_items": w_items, "w_count": w_count, "crash_warning_on": crash_warning_on,
        "b_items": b_items, "b_count": b_count, "bottom_confirmed": bottom_confirmed,
        "master_state": master_state, "master_badge": master_badge, "master_action": master_action
    }


def build_dynamic_crash_cycle_svg_html(fleet: dict, radar: dict, etf_m: dict) -> str:
    """【動態股災四階段週期走勢圖 × 淘金客 v24.1 雙雷達總開關（全浮動均線推算）】"""
    twii_now, hi_60d = fleet["twii_now"], fleet["twii_60d_high"]
    m_ma20, m_ma36, ma20, ma240 = fleet["m_ma20"], fleet["m_ma36"], fleet["ma20"], fleet["ma240"]

    l1_low, l1_high = round(m_ma20 * 0.98, 0), round(m_ma20 * 1.02, 0)
    fib382_reb = round(m_ma20 + (hi_60d - m_ma20) * 0.382, 0)
    r1_low = round(min(ma240, fib382_reb, ma20) * 0.985, 0)
    r1_high = round(max(min(ma240, ma20), fib382_reb), 0)
    l2_low, l2_high = round(m_ma20 * 0.985, 0), round(m_ma20 * 1.035, 0)
    ext_low = round(min(m_ma36, m_ma20 * 0.90), 0)

    if radar["master_state"] == "LOCKED":
        if twii_now <= m_ma20 * 1.08:
            curr_x, curr_y, badge_x, badge_y = 335, 255, 335, 195
            stage_desc = f"📍 目前位於【階段三：恐慌斷頭最高潮尾聲】（現價 {twii_now:,.0f}點 直測月20MA {m_ma20:,.0f}點第一隻腳・11/23 限空令3.0上路）"
        else:
            curr_x, curr_y, badge_x, badge_y = 235, 190, 235, 140
            stage_desc = f"📍 目前位於【階段二：高利率估值重算速殺段】（現價 {twii_now:,.0f}點 跌破年線 {ma240:,.0f}點）"
        dot_col = "#ff4d4f"
    elif radar["master_state"] == "UNLOCKED_DIP":
        curr_x, curr_y, badge_x, badge_y = 695, 252, 695, 192
        stage_desc = f"📍 目前位於【階段四：落底共振解鎖・W底右腳建倉區】（現價 {twii_now:,.0f}點・開放分 3 批布局）"
        dot_col = "#00e676"
    else:
        curr_x, curr_y, badge_x, badge_y = 955, 175, 955, 235
        stage_desc = f"📍 目前位於【多頭修復主升推進段】（現價 {twii_now:,.0f}點・常態順勢操作）"
        dot_col = "#00e676"

    exit_t1, exit_t2 = etf_m["00400A"]["exit_t1"], etf_m["00400A"]["exit_t2"]

    svg_xml = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 430" width="100%" height="auto">
<rect width="1200" height="430" rx="12" fill="#09111a" stroke="#334155" stroke-width="2"/>
<rect x="25" y="102" width="1150" height="210" rx="8" fill="#0f172a" stroke="#1e293b" stroke-width="1"/>
<line x1="25" y1="150" x2="1175" y2="150" stroke="#1e293b" stroke-width="1" stroke-dasharray="4,4"/>
<line x1="25" y1="210" x2="1175" y2="210" stroke="#1e293b" stroke-width="1" stroke-dasharray="4,4"/>
<line x1="25" y1="270" x2="1175" y2="270" stroke="#1e293b" stroke-width="1" stroke-dasharray="4,4"/>
<line x1="25" y1="268" x2="1175" y2="268" stroke="#00e676" stroke-width="2" stroke-dasharray="8,5"/>
<text x="1165" y="262" fill="#00e676" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="end">動態月20MA支撐線：{m_ma20:,.0f}點</text>
<line x1="25" y1="188" x2="1175" y2="188" stroke="#f59e0b" stroke-width="1.8" stroke-dasharray="6,5"/>
<text x="1165" y="182" fill="#fcd34d" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="end">反彈逃命/減碼反壓帶：{r1_low:,.0f}～{r1_high:,.0f}點</text>
<line x1="90" y1="86" x2="90" y2="118" stroke="#f97316" stroke-width="1.5" stroke-dasharray="3,3"/>
<line x1="515" y1="86" x2="515" y2="188" stroke="#f59e0b" stroke-width="1.5" stroke-dasharray="3,3"/>
<line x1="960" y1="86" x2="960" y2="145" stroke="#00e676" stroke-width="1.5" stroke-dasharray="3,3"/>
<line x1="335" y1="268" x2="335" y2="326" stroke="#ff4d4f" stroke-width="1.5" stroke-dasharray="3,3"/>
<line x1="695" y1="262" x2="695" y2="326" stroke="#38bdf8" stroke-width="1.5" stroke-dasharray="3,3"/>
<line x1="1010" y1="295" x2="1010" y2="326" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="3,3"/>
<polyline points="90,118 220,185 335,268 515,188 695,262 840,210 960,145 1120,122" fill="none" stroke="#f59e0b" stroke-width="4.5" stroke-linejoin="round"/>
<polyline points="695,262 800,295 920,268" fill="none" stroke="#ef4444" stroke-width="2" stroke-dasharray="5,4"/>
<circle cx="90" cy="118" r="7" fill="#f97316" stroke="#fff" stroke-width="2"/>
<circle cx="335" cy="268" r="8" fill="#ff4d4f" stroke="#fff" stroke-width="2"/>
<circle cx="515" cy="188" r="8" fill="#f59e0b" stroke="#fff" stroke-width="2"/>
<circle cx="695" cy="262" r="8" fill="#38bdf8" stroke="#fff" stroke-width="2"/>
<circle cx="960" cy="145" r="8" fill="#00e676" stroke="#fff" stroke-width="2"/>
<line x1="{curr_x}" y1="{curr_y}" x2="{badge_x}" y2="{badge_y}" stroke="{dot_col}" stroke-width="2.5"/>
<circle cx="{curr_x}" cy="{curr_y}" r="16" fill="{dot_col}" fill-opacity="0.32"/>
<circle cx="{curr_x}" cy="{curr_y}" r="8" fill="{dot_col}" stroke="#ffffff" stroke-width="3"/>
<rect x="{badge_x - 95}" y="{badge_y - 16}" width="190" height="30" rx="7" fill="{dot_col}" stroke="#09111a" stroke-width="2.5"/>
<text x="{badge_x}" y="{badge_y + 5}" fill="#ffffff" font-family="sans-serif" font-size="14" font-weight="900" text-anchor="middle">📍現價 {twii_now:,.0f}點</text>
<rect x="20" y="14" width="215" height="70" rx="7" fill="#16222f" stroke="#f97316" stroke-width="1.5"/>
<text x="127" y="36" fill="#e2e8f0" font-family="sans-serif" font-size="13" font-weight="bold" text-anchor="middle">波段起跌高點 (近60日高)</text>
<text x="127" y="58" fill="#fcd34d" font-family="sans-serif" font-size="15" font-weight="900" text-anchor="middle">{hi_60d:,.0f} 點</text>
<text x="127" y="76" fill="#fda4af" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">30日急殺回檔 -{fleet['twii_30d_dd_pct']:.1f}%</text>
<rect x="355" y="14" width="330" height="70" rx="7" fill="#2d1b19" stroke="#f59e0b" stroke-width="2"/>
<text x="520" y="35" fill="#ffffff" font-family="sans-serif" font-size="13.5" font-weight="900" text-anchor="middle">⚠️ 反彈遇壓／減碼區 (年線與0.382反壓)</text>
<text x="520" y="56" fill="#fcd34d" font-family="sans-serif" font-size="14.5" font-weight="900" text-anchor="middle">{r1_low:,.0f} ～ {r1_high:,.0f} 點</text>
<text x="520" y="75" fill="#fda4af" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">00400A 滾動退場({exit_t1}~{exit_t2}元)轉00985B・禁追高</text>
<rect x="780" y="14" width="395" height="70" rx="7" fill="#112a26" stroke="#00e676" stroke-width="1.5"/>
<text x="977" y="36" fill="#a7f3d0" font-family="sans-serif" font-size="13.5" font-weight="900" text-anchor="middle">🚀 2027 Q1~Q2 築底完成・外資主流回流段</text>
<text x="977" y="57" fill="#00e676" font-family="sans-serif" font-size="14.5" font-weight="900" text-anchor="middle">待落底雷達 &gt;= 4/5 解除鎖定後分 3 批進場</text>
<text x="977" y="76" fill="#6ee7b7" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">聚焦：新穀倉1共通規格 ＋ MSCI 新增主流 ＋ 換股標的</text>
<rect x="20" y="326" width="380" height="86" rx="7" fill="#2b0f14" stroke="#ff4d4f" stroke-width="2"/>
<text x="210" y="348" fill="#ff8a80" font-family="sans-serif" font-size="13.5" font-weight="900" text-anchor="middle">🔥 第一隻腳測試區 (月20MA＋11/23限空令3.0)</text>
<text x="210" y="372" fill="#fcd34d" font-family="sans-serif" font-size="15" font-weight="900" text-anchor="middle">{l1_low:,.0f} ～ {l1_high:,.0f} 點 (月20MA {m_ma20:,.0f})</text>
<text x="210" y="396" fill="#e2e8f0" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">T+2 斷頭餘波消化｜開板比價換買日月光(比值&gt;=1.18)</text>
<rect x="425" y="326" width="380" height="86" rx="7" fill="#13283c" stroke="#38bdf8" stroke-width="2"/>
<text x="615" y="348" fill="#93c5fd" font-family="sans-serif" font-size="13.5" font-weight="900" text-anchor="middle">🎯 W底右腳二次測底 (12/8 FOMC・12/15 ANT解禁)</text>
<text x="615" y="372" fill="#6ee7b7" font-family="sans-serif" font-size="15" font-weight="900" text-anchor="middle">{l2_low:,.0f} ～ {l2_high:,.0f} 點 (量縮不破前低)</text>
<text x="615" y="396" fill="#fcd34d" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">雙雷達落底達 4/5 ➔ 動用水庫四(SGOV/現金)與抱現資金重錘</text>
<rect x="830" y="326" width="345" height="86" rx="7" fill="#16222f" stroke="#94a3b8" stroke-width="1.5"/>
<text x="1002" y="348" fill="#cbd5e1" font-family="sans-serif" font-size="13.5" font-weight="bold" text-anchor="middle">🛡️ 極端系統性壓力測試底 (動態月36MA)</text>
<text x="1002" y="372" fill="#fcd34d" font-family="sans-serif" font-size="15" font-weight="900" text-anchor="middle">{ext_low:,.0f} ～ {m_ma36:,.0f} 點</text>
<text x="1002" y="396" fill="#94a3b8" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">僅於美股流動性危機擴大時啟動・死抱傳家寶＋00985B防彈</text>
</svg>"""
    b64_svg = base64.b64encode(svg_xml.encode("utf-8")).decode("utf-8")
    border_col = "#ff4d4f" if radar["master_state"] == "LOCKED" else "#00e676"
    raw_card = f"""
<div style="background: linear-gradient(180deg, #0d1520 0%, #16222f 100%); border: 2px solid {border_col}; border-radius: 14px; padding: 16px 20px; margin-bottom: 16px; color: #fff;">
<div style="text-align:center; font-size:21px; font-weight:900; color:#fcd34d; border-bottom:1px solid rgba(252,211,77,0.3); padding-bottom:7px; margin-bottom:10px;">
📉 加權指數動態股災四階段週期走勢圖 × 淘金客 v24.1 雙雷達總開關（全浮動均線自動推算）
</div>
<div style="font-size:15px; font-weight:800; color:#f8fafc; background:rgba(0,0,0,0.35); padding:9px 14px; border-radius:8px; margin-bottom:10px; border-left:4px solid {border_col}; line-height:1.65;">
• {stage_desc}<br>
• <b>總開關狀態</b>：{radar['master_badge']} ➔ <span style="color:#ffd54f;">{radar['master_action']}</span>
</div>
<img src="data:image/svg+xml;base64,{b64_svg}" style="width:100%; height:auto; display:block; border-radius:10px;" />
</div>
"""
    return clean_html(raw_card)


# ==========================================
# v60.0 核心：淘金客 v24.1 雙模式戰情數據包組裝函式 (模式A 守門員批次 / 模式B 決選深檢)
# ==========================================
def build_market_outpost_line(fleet: dict, radar: dict, deriv: dict, tnx_val: float, fx_now: float) -> str:
    tw = fleet["twii_now"]
    pos_60 = ">=" if tw >= fleet["ma60"] else "<"
    pos_120 = ">=" if tw >= fleet["ma120"] else "<"
    pos_240 = ">=" if tw >= fleet["ma240"] else "<"
    return (
        f"[大盤前哨] 加權={tw:,.1f}({fleet['twii_chg_pct']:+.2f}%) | "
        f"位階:{pos_60}60MA({fleet['ma60']:,.0f})/{pos_120}120MA({fleet['ma120']:,.0f})/{pos_240}240MA({fleet['ma240']:,.0f})/月20MA({fleet['m_ma20']:,.0f}) | "
        f"股災雙雷達:預警{radar['w_count']}/5・落底{radar['b_count']}/5({radar['master_state']}) | "
        f"PCR={deriv['pc_ratio']:.1f}% | ^TNX={tnx_val:.2f}% | USD/TWD={fx_now:.3f}"
    )


def format_tdcc_with_override(d_rec: dict, live_tdcc_map: dict) -> str:
    code = d_rec["代碼"]
    if code in live_tdcc_map:
        w = live_tdcc_map[code]
        return f"400張:{w['p400']:.1f}%({w['d400']:+.2f}%)/千張:{w['p1000']:.1f}%({w['d1000']:+.2f}%)[{w['date']}]"
    return d_rec.get("大戶400/千張", "N/A")


def build_mode_a_guard_package(codes: list[str], source_label: str, batch_label: str, all_details: dict, outpost_line: str, now_str: str, live_tdcc_map: dict) -> str:
    lines = [
        f"【淘金客 v24.1｜模式A 守門員快篩批次包（來源：{source_label}｜{batch_label}：共{len(codes)}檔）】({now_str})",
        outpost_line
    ]
    for idx, c in enumerate(codes[:6], 1):
        r = all_details.get(c)
        if not r: continue
        tdcc_s = format_tdcc_with_override(r, live_tdcc_map)
        up_sh = f"{r['上影線%']:.1f}%" if pd.notna(r.get("上影線%")) else "N/A"
        lines.append(
            f"{idx}. {c} {r['_raw_name']} | {r['現價']}({r['今日%']:+.2f}%/3日{r['3日%']:+.2f}%) | "
            f"型態:{r.get('_pattern_tag', r['短線屬性'])} | "
            f"DMI:{r['DMI方向雙檢']} J={r['日J值']} | "
            f"BIAS10={r['BIAS10']} | "
            f"量比{r['5MA量比']}x/上影{up_sh}/位階{r['收盤位階']}/當沖{r['當沖率']} | "
            f"OBV:{r['OBV趨勢']}/RS20D:{r['RS20D%']}/大戶:{tdcc_s} | "
            f"ATR%={r['ATR%波動尺']} | 月營收YoY={r['單月營收YoY']}"
        )
    lines.append("➔ 請執行【淘金客 v24.1 模式A 守門員快篩】：直接剔除地雷／假突破／墊料風險股，篩出值得進入模式B深檢的 Top 1~3 檔！")
    return "\n".join(lines)


def build_mode_b_deep_package(codes: list[str], all_details: dict, price_dict_all: dict, outpost_line: str, now_str: str, live_tdcc_map: dict) -> str:
    lines = [
        f"【淘金客 v24.1｜模式B 決選深檢戰情包（共{len(codes)}檔）】({now_str})",
        outpost_line
    ]
    for idx, c in enumerate(codes[:3], 1):
        r = all_details.get(c)
        if r:
            mkt = r.get("_mkt", "TW")
            anti_trap = fetch_single_stock_anti_trap(c, mkt)
            tdcc_s = format_tdcc_with_override(r, live_tdcc_map)
            up_sh = f"{r['上影線%']:.1f}%" if pd.notna(r.get("上影線%")) else "N/A"
            eps_s = f"{r['預估EPS(元)']:.2f}元" if pd.notna(r.get("預估EPS(元)")) else "N/A"
            pe_s = f"{r['本益比(PE)']:.1f}x" if pd.notna(r.get("本益比(PE)")) else "N/A"
            lines.extend([
                f"=== 決選標的 {idx}：{c} {r['_raw_name']} ({mkt}) ===",
                f"• 報價與賽道：現價={r['現價']}({r['今日%']:+.2f}% / 3日{r['3日%']:+.2f}%) | 型態={r.get('_pattern_tag', r['短線屬性'])} | 穀倉={r['穀倉賽道與防墊料']} | CB={r.get('_cb', '➖')}",
                f"• 完整均線與城堡：5MA={r['_ma5']} / 10MA={r['_ma10']} / 20MA={r['_ma20']} / 60MA={r['_ma60']} / 120MA={r['_ma120']} / 240MA={r['_ma240']} | 天花板={r['天花板(反壓)']} / 鋼鐵防線={r['鋼鐵防線']} / 停損={r['極限停損']}",
                f"• DMI、乖離與波動：{r['DMI方向雙檢']} | 日J={r['日J值']} | BIAS10={r['BIAS10']} / BIAS20={r['BIAS20']} | ATR%={r['ATR%波動尺']}",
                f"• 假突破三合一：5MA量比={r['5MA量比']}x (昨量比{r['昨日量比']}x / 預估{r['預估量(張)']:,}張) | 上影線%={up_sh} | 收盤位階={r['收盤位階']} | 當沖率={r['當沖率']}",
                f"• 量能與籌碼動能：OBV={r['OBV趨勢']} | RS20D={r['RS20D%']} | 法人={r['法人動向']} | 大戶={tdcc_s}",
                f"• 基本面與防墊料：最新月營收YoY={r['單月營收YoY']} / 累計YoY={r['累計年營收YoY']} | 預估EPS={eps_s} / PE={pe_s} ({r['估值診斷']}) | 季報防墊料={anti_trap.get('note', 'N/A')}"
            ])
        else:
            p_inf = price_dict_all.get(c, {})
            mkt = p_inf.get("market", "TW")
            anti_trap = fetch_single_stock_anti_trap(c, mkt)
            nm = p_inf.get("name", c)
            tdcc_s = format_tdcc_with_override({"代碼": c, "大戶400/千張": "N/A"}, live_tdcc_map)
            lines.extend([
                f"=== 決選標的 {idx}：{c} {nm} ({mkt}・非主掃描池) ===",
                f"• 基礎報價：現價={p_inf.get('close', 'N/A')}({p_inf.get('chg_pct', 0):+.2f}%) | 成交量={p_inf.get('vol_lots', 0):,}張 | 當沖率={p_inf.get('day_trade_str', 'N/A')}",
                f"• 籌碼與防墊料：外資={p_inf.get('fini_lots', 0):+,}張 / 投信={p_inf.get('sitc_lots', 0):+,}張 | 大戶={tdcc_s} | 季報防墊料={anti_trap.get('note', 'N/A')} | 完整技術面=N/A (交由淘金客聯網查核)"
            ])
    lines.append("➔ 請執行【淘金客 v24.1 模組 20 雙核全維度深檢】：優先聯網補查法說催化劑、主力分點與資券借，輸出具體進出場城堡點位、一票否決裁決與鐵血紀律！")
    return "\n".join(lines)


# ==========================================
# Streamlit 主程式入口 (v60.0 淘金客 v24.1 雙腦互補戰情室)
# ==========================================
now_str = get_tw_now().strftime("%Y-%m-%d %H:%M:%S")
st.markdown("## 🚨 台股每日戰情室 v60.0 (對接淘金客 v24.1：5組量化接軌指標 ＋ 雙模式戰情包 ＋ 股災雙雷達)")

btn_c1, btn_c2, btn_c3 = st.columns([2, 2, 3])
if btn_c1.button("⚡ 秒速刷新即時報價、5組指標與雙雷達", use_container_width=True):
    fetch_mis_realtime_batch.clear()
    fetch_us_sectors_and_leaders.clear()
if btn_c2.button("🔄 重抓期交所、三大法人、當沖率與最新月營收", use_container_width=True):
    st.cache_data.clear()
auto_refresh = btn_c3.checkbox("⏱️ 開啟盤中每 20 秒自動刷新", value=False)

with st.spinner("📡 正在同步 5 組量化接軌指標 (DMI/BIAS/當沖/OBV/RS/ATR%)、股災雙雷達與 MSCI 動態池..."):
    df_launch, df_pullback, df_orbit, df_long_bc, df_long_turn, vol_note, rt_quotes, msci_data, price_dict_all = scan_all_stocks()
    fleet = fetch_and_analyze_fleet(rt_quotes, price_dict_all=price_dict_all)
    us_data = fetch_us_sectors_and_leaders()
    df_all_rev = fetch_all_monthly_revenue_df(price_dict_all)
    _, tdcc_dict_all, _, _ = fetch_official_market_data()
    if not df_all_rev.empty:
        df_all_rev["400張大戶%"] = df_all_rev["代碼"].map(lambda c: f"{tdcc_dict_all[c]:.1f}%" if c in tdcc_dict_all else "未揭露")
        df_all_rev["_tdcc_num"] = df_all_rev["代碼"].map(lambda c: float(tdcc_dict_all.get(c, 0.0)))

deriv = fleet["deriv"]
etf_m = fleet["etf_metrics"]
tw_hot_sec_info = compute_tw_hot_sectors(st.session_state.get("scanned_sector_records", []))
all_scanned_details = st.session_state.get("all_scanned_details", {})
df_granary_audit = st.session_state.get("granary_audit_df", pd.DataFrame())
df_msci_audit = st.session_state.get("msci_audit_df", pd.DataFrame())

# ==========================================
# 左側雙階讀取覆寫控制台 (Sidebar)
# ==========================================
with st.sidebar:
    st.markdown("### 🎛️ 雙階讀取覆寫控制台")
    st.caption("盤中或特殊日若有無法即時爬取之籌碼，可直接覆寫（第一優先覆寫權）：")
    sb_fini_oi = st.number_input("外資台指期淨未平倉 (口)", value=int(deriv["fini_tx_oi"]), step=1000)
    sb_margin_2d = st.number_input("近2日上市櫃融資合計增減 (億)", value=float(deriv.get("margin_2d_chg_yi", -971.7)), step=50.0, format="%.1f")
    sb_margin_dd = st.number_input("波段融資總減幅 (%)", value=18.5, step=1.0, format="%.1f")
    sb_maint = st.number_input("全市場融資維持率 (%)", value=151.78 if fleet["twii_now"] < 37000 else float(deriv["stock_only_maint_pct"]), step=0.5, format="%.2f")
    sb_basis = st.number_input("期現貨價差 (點，負=逆價差)", value=-150.0 if fleet["twii_now"] < 37000 else 0.0, step=20.0, format="%.0f")
    sb_basis_stable = st.checkbox("期現貨逆價差已達極點且不再擴大", value=False)
    sb_consec_cover = st.number_input("外資期貨連補天數 (日)", value=int(deriv.get("consec_cover_days", 1)), min_value=0, max_value=15, step=1)
    sb_cover_3d = st.number_input("外資近3日累計回補 (口)", value=int(deriv.get("cover_3d_sum", 1800)), step=1000)
    sb_limit_dn = st.number_input("當日上市櫃跌停家數 (家)", value=int(fleet["limit_down_cnt"]), min_value=0, step=5)
    sb_had_100 = st.checkbox("本波曾見破百檔跌停高峰", value=True)
    st.markdown("---")
    st.markdown("### 💧 四水庫持股張數調整")
    sb_lots_00985b = st.number_input("水庫三：00985B 持有張數", value=0.0, min_value=0.0, step=1.0, format="%.1f")
    sb_lots_00713 = st.number_input("水庫三：00713 持有張數", value=0.0, min_value=0.0, step=0.5, format="%.1f")
    sb_lots_00400a = st.number_input("待退場：00400A 持有張數", value=31.0, min_value=0.0, step=1.0, format="%.1f")
    sb_lots_6239 = st.number_input("衛星換股：力成(6239) 張數", value=2.3, min_value=0.0, step=0.1, format="%.1f")
    sb_lots_3264 = st.number_input("衛星換股：欣銓(3264) 張數", value=1.0, min_value=0.0, step=0.1, format="%.1f")
    sb_usd_cash = st.number_input("水庫四：美金現金 (USD)", value=2300.0, min_value=0.0, step=100.0, format="%.0f")

# ==========================================
# 全域頂部：模組二 關鍵風險事件倒數警示條
# ==========================================
ev_rows, next_ev = get_upcoming_risk_events()
if next_ev:
    d_left = next_ev["_days"]
    ev_cls = "event-banner-red" if d_left <= 3 else ("event-banner-yellow" if d_left <= 7 else "event-banner-blue")
    ev_warn_tip = "🚨 【高波動警戒：事件前 3 天內！反彈遇壓減碼、嚴禁開槓桿！】" if d_left <= 3 else "📌 提早規劃資金水位與防禦避險。"
    st.markdown(clean_html(f"""
    <div class="{ev_cls}">
    <b>📅 下一個重大風險事件倒數：{next_ev['日期']}（{next_ev['倒數狀態']}）➔ {next_ev['重大風險事件']}</b><br>
    • {ev_warn_tip} 實戰因應：<b>{next_ev['實戰防禦提醒']}</b>
    </div>
    """), unsafe_allow_html=True)

with st.expander("🗓️ 點此展開【2026/11 ～ 2027/01 完整關鍵風險事件行事曆 (12 檔)】", expanded=False):
    st.dataframe(pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in ev_rows]), use_container_width=True, hide_index=True)

# 計算核心美股/匯率與股災雙雷達結果
vix_q = us_data["quotes"].get("%5EVIX", {})
vix_now, vix_chg = vix_q.get("close", 37.66), vix_q.get("chg_pct", +12.5)
tnx_val, tnx_chg = us_data.get("tnx_val", 4.94), us_data.get("tnx_chg", -1.20)
sox_chg = us_data["quotes"].get("%5ESOX", {}).get("chg_pct", -10.73)
tsm_q, nvda_q = us_data["quotes"].get("TSM", {}), us_data["quotes"].get("NVDA", {})
tsm_chg, tsm_ext = tsm_q.get("chg_pct", -9.48), (f" ({tsm_q['ext_label']})" if tsm_q.get("ext_label") else "")
nvda_chg, nvda_ext = nvda_q.get("chg_pct", -14.01), (f" ({nvda_q['ext_label']})" if nvda_q.get("ext_label") else "")
fx_now, fx_ma5, fx_chg_ntd = deriv["fx_now"], deriv["fx_ma5"], deriv["fx_chg_ntd"]
macro_withdrawal_alert = bool(tnx_val >= 5.0 and fx_now >= 32.50)

radar = evaluate_crash_and_bottom_radar(
    fleet["twii_now"], fleet["ma240"], fleet["twii_30d_dd_pct"], fleet["sp500_below_ma200"],
    vix_now, fleet["sox_dd_pct"], sb_fini_oi, sb_margin_dd, sb_margin_2d, sb_maint,
    fleet["is_climax_hammer"], fleet["twii_vol_20ma_ratio"], fleet["twii_lower_shadow_ratio"],
    sb_basis, sb_basis_stable, sb_consec_cover, sb_cover_3d, sb_limit_dn, sb_had_100
)

# 比價換股即時報價
p_6239 = rt_quotes.get("6239", {}).get("close") or price_dict_all.get("6239", {}).get("close", 181.5)
p_3264 = rt_quotes.get("3264", {}).get("close") or price_dict_all.get("3264", {}).get("close", 136.0)
p_3711 = rt_quotes.get("3711", {}).get("close") or price_dict_all.get("3711", {}).get("close", 457.0)
swap_sell_twd = 2000.0 * p_6239 + 1000.0 * p_3264
swap_ratio = round((2.0 * p_6239 + 1.0 * p_3264) / max(1.0, p_3711), 3)
swap_net_cash = round(swap_sell_twd - 1000.0 * p_3711 - 3000.0, 0)

if p_3711 >= 455.0:
    ase_zone, swap_signal = "🔴 高風險區 (>=455元)", "🔴 嚴禁追換！開板有量先賣出(2張力成+1張欣銓)抱台幣現金，等 12 月中二次測底！"
elif p_3711 >= 420.0:
    ase_zone = "🟡 過渡觀察區 (420~454元)"
    swap_signal = "🟡 比值達標惟日月光仍在過渡區，優先先賣抱現或等回測 415 以下！" if swap_ratio >= 1.18 else "🟠 比值未達 1.18，先賣抱現等二次測底！"
elif p_3711 >= 360.0:
    ase_zone = "🟢 合理黃金區 (360~415元)"
    swap_signal = f"🟢 【觸發換股綠燈！】比值 {swap_ratio:.3f} >= 1.18，立刻換買 1 張日月光並保留淨現金 {swap_net_cash:+,.0f} 元！" if swap_ratio >= 1.18 else f"🟠 日月光已入合理區，但比值 {swap_ratio:.3f} < 1.18，建議先賣抱現或視盤況擇機承接！"
else:
    ase_zone = "💎 鐵板鑽石區 (<=359元)"
    swap_signal = f"💎 【鑽石重錘換股訊號！】比值 {swap_ratio:.3f}，立刻換買 1 張日月光並留淨現金 {swap_net_cash:+,.0f} 元！" if swap_ratio >= 1.18 else f"🟢 日月光落入鐵板區，雖比值 {swap_ratio:.3f} < 1.18，可逢低換入 1 張日月光！"

# 四水庫即時市值試算
def get_live_p(code_k, default_v):
    return float(rt_quotes.get(code_k, {}).get("close") or price_dict_all.get(code_k, {}).get("close") or default_v)

p_2330, p_2884, p_2881 = get_live_p("2330", 2000.0), get_live_p("2884", 39.6), get_live_p("2881", 136.5)
p_2812, p_2887, p_2834 = get_live_p("2812", 15.75), get_live_p("2887", 36.0), get_live_p("2834", 16.5)
p_avgo = us_data["quotes"].get("AVGO", {}).get("close") or etf_m["AVGO"]["p"]
p_nvda = us_data["quotes"].get("NVDA", {}).get("close") or etf_m["NVDA"]["p"]
p_sgov = us_data["quotes"].get("SGOV", {}).get("close") or etf_m["SGOV"]["p"]
p_00400a, p_00985b, p_00713 = etf_m["00400A"]["p"], etf_m["00985B"]["p"], etf_m["00713"]["p"]

val_r1 = 400000.0
val_r2_tw_semi = 4000 * p_2330 + 3000 * p_3711
val_r2_tw_fin = 100453 * p_2884 + 11743 * p_2881 + 27148 * p_2812 + 4882 * p_2887 + 158 * 9.55 + 1090 * p_2834
val_r2_us_semi = (35 * p_avgo + 80 * p_nvda) * fx_now
val_r2 = val_r2_tw_semi + val_r2_tw_fin + val_r2_us_semi
val_r3 = sb_lots_00985b * 1000 * p_00985b + sb_lots_00713 * 1000 * p_00713
val_r4 = (30 * p_sgov + sb_usd_cash) * fx_now
val_sat_semi = sb_lots_6239 * 1000 * p_6239 + sb_lots_3264 * 1000 * p_3264
val_sat_400a = sb_lots_00400a * 1000 * p_00400a
total_assets = val_r1 + val_r2 + val_r3 + val_r4 + val_sat_semi + val_sat_400a
semi_total = val_r2_tw_semi + val_r2_us_semi + val_sat_semi
semi_ratio_pct = round((semi_total / max(1.0, total_assets)) * 100.0, 1)

# 整理優先排序表
df_launch = prioritize_granary_and_armor_df(df_launch, macro_withdrawal_alert, "實戰優先分")
df_pullback = prioritize_granary_and_armor_df(df_pullback, macro_withdrawal_alert, "蓄勢評分")
df_long_bc = prioritize_granary_and_armor_df(df_long_bc, macro_withdrawal_alert, "波段總分")
df_long_turn = prioritize_granary_and_armor_df(df_long_turn, macro_withdrawal_alert, "波段總分")

if not df_launch.empty and "短線屬性" in df_launch.columns:
    df_launch_fund = df_launch[df_launch["短線屬性"].str.contains("有基之彈|核心權值|政策內需", na=False)].copy()
    df_launch_pure = df_launch[df_launch["短線屬性"].str.contains("純技術炒作", na=False)].copy()
else:
    df_launch_fund, df_launch_pure = pd.DataFrame(), pd.DataFrame()

outpost_line = build_market_outpost_line(fleet, radar, deriv, tnx_val, fx_now)

# ==========================================
# 六大功能分頁架構
# ==========================================
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🚨 雙雷達＆v24.1戰情包",
    "⚖️ 比價換股＆四水庫監控",
    "⚡ 短線旱地拔蔥榜",
    "🏛️ 長線波段＆MSCI主流池",
    "🌾 穀倉與防墊料專區",
    "📈 上市櫃月營收快篩"
])

# ==================== TAB 1 ====================
with tab1:
    hero_cls = "war-hero-box-danger" if radar["master_state"] == "LOCKED" else ("war-hero-box" if radar["master_state"] == "UNLOCKED_DIP" else "war-hero-box-warn")
    idx_col = "#ff4d4f" if fleet["twii_chg_pct"] >= 0 else "#00e676"
    sox_c = "#ff4d4f" if sox_chg >= 0 else "#00e676"
    tsm_c = "#ff4d4f" if tsm_chg >= 0 else "#00e676"
    nvda_c = "#ff4d4f" if nvda_chg >= 0 else "#00e676"

    w_html = "<br>".join([f"{'🔴' if f else '⚪'} {txt}" for f, txt in radar["w_items"]])
    b_html = "<br>".join([f"{'🟢' if f else '⚪'} {txt}" for f, txt in radar["b_items"]])

    st.markdown(clean_html(f"""
    <div class="{hero_cls}">
    <div class="war-score-title">
    📊 台股加權指數：<span style="font-size:34px; color:{idx_col};">{fleet['twii_now']:,.1f} 點 ({fleet['twii_chg_pct']:+.2f}%)</span>
    &nbsp;｜&nbsp; {radar['master_badge']}
    </div>
    <div style="font-size:14.8px; color:#e2e8f0; background:rgba(0,0,0,0.35); padding:8px 14px; border-radius:8px; margin-bottom:10px; line-height:1.65;">
    • 🕒 <b>對時與區間</b>：{fleet['date_str']}（開盤 <b>{fleet['twii_open']:,.0f}</b> ／ 低 <b>{fleet['twii_low']:,.0f}</b> ～ 高 <b>{fleet['twii_high']:,.0f}</b> ／ 昨收 <b>{fleet['twii_prev_c']:,.0f}</b>）｜ <b>月20MA鐵板：{fleet['m_ma20']:,.0f}點</b> ｜ <b>年線：{fleet['ma240']:,.0f}點</b><br>
    • ⚖️ <b>總開關最高紀律指令</b>：<b style="color:#fde047;">{radar['master_action']}</b>
    </div>
    <div class="radar-grid-2">
    <div class="radar-panel" style="border-left:5px solid {'#ff4d4f' if radar['crash_warning_on'] else '#64748b'};">
    <span class="war-dim-concl" style="color:#ff8a80;">🔴 一、股災啟動預警雷達（目前觸發 {radar['w_count']} / 5 項 ｜ >=3 項紅燈鎖死）</span>
    {w_html}
    </div>
    <div class="radar-panel" style="border-left:5px solid {'#00e676' if radar['bottom_confirmed'] else '#f59e0b'};">
    <span class="war-dim-concl" style="color:#69f0ae;">🟢 二、股災落底確認雷達（目前達成 {radar['b_count']} / 5 項 ｜ >=4 項共振解鎖）</span>
    {b_html}
    </div>
    </div>
    </div>
    """), unsafe_allow_html=True)

    # ==========================================
    # v60.0 核心亮點：淘金客 v24.1 雙模式一鍵複製戰情數據包匯出中心
    # ==========================================
    st.markdown("### 🤖 【淘金客 v24.1 雙腦互補：一鍵複製戰情數據包匯出中心】（右上角圖示直接一鍵複製）")
    pkg_tab_a, pkg_tab_b = st.tabs(["🛡️ 按鈕 A：守門員快篩批次包（自動切批，每批 5～6 檔）", "🔬 按鈕 B：單檔／決選深檢戰情包（精選 1～3 檔＋季報防墊料）"])

    with pkg_tab_a:
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
                "✏️ 可微調本批匯出標的（上限 6 檔，預設為上方自動切批結果）：",
                options=pool_codes,
                default=batch_codes,
                max_selections=6,
                format_func=lambda c: f"{c} {all_scanned_details[c]['_raw_name']}",
                key=f"mode_a_ms_{sel_pool_k}_{b_idx}"
            )
            final_a_codes = custom_a if custom_a else batch_codes
            live_tdcc_a = fetch_tdcc_weekly_delta_ondemand(tuple(final_a_codes)) if fetch_a_tdcc else {}
            mode_a_txt = build_mode_a_guard_package(final_a_codes, sel_pool_k.split(" (")[0], sel_b_lbl, all_scanned_details, outpost_line, now_str, live_tdcc_a)
            st.code(mode_a_txt, language="text")
        else:
            st.info("目前選擇的清單無符合標的（股災期間右側表 1-A 可能為空），請切換至『長線表3-B』、『MSCI』或『宏爺穀倉』清單匯出！")

    with pkg_tab_b:
        all_opt_codes = sorted(all_scanned_details.keys(), key=lambda c: (0 if c in ["3711", "2330", "6239", "3264", "2301", "2454"] else 1, c))
        default_b = [c for c in ["3711", "6239"] if c in all_opt_codes][:2]
        pb_c1, pb_c2 = st.columns([3.2, 2.0])
        sel_b_codes = pb_c1.multiselect(
            "🎯 從已掃描個股中勾選決選標的（最多 3 檔）：",
            options=all_opt_codes,
            default=default_b,
            max_selections=3,
            format_func=lambda c: f"{c} {all_scanned_details[c]['_raw_name']} ({all_scanned_details[c]['現價']})",
            key="mode_b_ms"
        )
        manual_b_str = pb_c2.text_input("⌨️ 或手動輸入 4 碼代號（逗號分隔，覆寫上限3檔）", value="", placeholder="例：3711, 2383, 3189", key="mode_b_manual")
        manual_b_codes = [c for c in re.findall(r"\b(\d{4})\b", manual_b_str)][:3]
        target_b_codes = manual_b_codes if manual_b_codes else sel_b_codes

        if st.button("⚡ 生成【模式 B 決選深檢戰情包】（即時補抓季報防墊料 ＋ 400/千張大戶週變動）", type="primary", key="btn_gen_mode_b"):
            if target_b_codes:
                with st.spinner(f"正在抓取 {', '.join(target_b_codes)} 之季報存貨/應收/現金流與集保大戶週變動..."):
                    live_tdcc_b = fetch_tdcc_weekly_delta_ondemand(tuple(target_b_codes))
                    st.session_state["mode_b_pkg_txt"] = build_mode_b_deep_package(target_b_codes, all_scanned_details, price_dict_all, outpost_line, now_str, live_tdcc_b)
            else:
                st.warning("請先勾選或輸入 1～3 檔個股代號！")

        if "mode_b_pkg_txt" in st.session_state and st.session_state["mode_b_pkg_txt"]:
            st.code(st.session_state["mode_b_pkg_txt"], language="text")
        else:
            st.caption("💡 經過模式 A 守門員快篩留下 Top 1～3 檔後，於上方選擇標的並點擊紅色按鈕，即可一鍵生成完整深檢數據包（含 6 條均線、季報防墊料與 CB 狀態）。")

    # 動態股災四階段週期走勢圖
    st.markdown(build_dynamic_crash_cycle_svg_html(fleet, radar, etf_m), unsafe_allow_html=True)

    # 盤面熱門族群與美股/籌碼四大監控卡片
    up_sec_cards = format_sector_bold_html(us_data["top_up_sectors"], is_up=True)
    dn_sec_cards = format_sector_bold_html(us_data["bot_dn_sectors"], is_up=False)
    st.markdown(clean_html(f"""
    <div class="war-hero-box" style="border-left-color:#38bdf8;">
    <div class="war-dim-grid">
    <div class="war-dim-item" style="border-left:5px solid #ff4d4f;">
    <span class="war-dim-concl">1. 🔥 台股當日抗跌/熱門爆量族群雷達（{vol_note}）</span>
    {tw_hot_sec_info['hot_html']}
    {tw_hot_sec_info['cold_html']}
    </div>
    <div class="war-dim-item" style="border-left:5px solid #f59e0b;">
    <span class="war-dim-concl">2. 🌎 美股 VIX / 費半 / ADR 與領漲領跌板塊：{us_data['session_tag']}</span>
    • <b>美股恐慌與債匯</b>：VIX <b>{vix_now:.2f}</b>({vix_chg:+.1f}%) ｜ 美債10Y <b>{tnx_val:.2f}%</b>({tnx_chg:+.2f}%) ｜ 台幣 <b>{fx_now:.3f}</b><br>
    • <b>AI 核心連動</b>：費半 <b style="color:{sox_c};">{sox_chg:+.2f}%</b> ｜ 台積ADR <b style="color:{tsm_c};">{tsm_chg:+.2f}%{tsm_ext}</b> ｜ 輝達 <b style="color:{nvda_c};">{nvda_chg:+.2f}%{nvda_ext}</b>
    {up_sec_cards}
    {dn_sec_cards}
    </div>
    <div class="war-dim-item" style="border-left:5px solid #00e676;">
    <span class="war-dim-concl">3. 🛡️ 融資清洗與維持率監控（側邊欄可覆寫）</span>
    • <b>全市場維持率</b>：<b style="color:#ffd54f; font-size:16.5px;">{sb_maint:.2f}%</b>（低於 155% 進入真斷頭區）<br>
    • <b>融資增減</b>：最新上市餘額 <b>{deriv['margin_balance_yi']:,.1f} 億</b>（近2日上市櫃合計 <b>{sb_margin_2d:+.1f} 億</b>）<br>
    • <b>當日跌停廣度</b>：全市場跌停約 <b>{sb_limit_dn} 家</b>（收斂至 &lt;20 家為落底條件之一）
    </div>
    <div class="war-dim-item" style="border-left:5px solid #ffd54f;">
    <span class="war-dim-concl">4. 📊 期權籌碼、權證避險與救市政策</span>
    • <b>大小台期貨</b>：外資淨未平倉 <b>{sb_fini_oi:+,} 口</b>（連補 {sb_consec_cover}日 / 3日累計 {sb_cover_3d:+,}口）｜ 投信 <b>{deriv['sitc_tx_oi']:+,} 口</b><br>
    • <b>選擇權與權證</b>：PCR <b>{deriv['pc_ratio']:.1f}%</b> ｜ 自營商避險 <b>{deriv['warrant_hedge_yi']:+.2f} 億</b><br>
    • <b>政策護盤</b>：<b>11/23 限空令 3.0 上路</b>（歷次 20日勝率 80% / 60日勝率 100%）
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
        ma_row("① 日 20MA (月線・波段反壓)", fleet["ma20"], "下彎蓋頭強壓，反彈至此區宜減碼 00400A 與弱勢股"),
        ma_row("② 日 240MA (年線・多空生命線)", fleet["ma240"], "跌破觸發股災警戒；未站回前反彈皆視為空頭修正反彈"),
        ma_row("③ 日 5MA (五日攻擊線)", fleet["ma5"], "短線止跌第一步須重新站上並走平"),
        ma_row("④ 月 20MA (第一隻腳核心鐵板)", fleet["m_ma20"], "歷史急殺股災第一道強力支撐區，對接 11/23 限空令 3.0"),
        ma_row("⑤ 月 36MA (極端系統底)", fleet["m_ma36"], "若美股引發二次崩跌之終極防禦底線")
    ]), use_container_width=True, hide_index=True)

# ==================== TAB 2: 比價換股與四水庫監控 ====================
with tab2:
    st.markdown("#### ⚖️ 【模組三之一：盤中比價換股試算器（2張力成 6239 ＋ 1張欣銓 3264 ➔ 換買 1張日月光 3711）】")
    sw_c1, sw_c2, sw_c3, sw_c4 = st.columns(4)
    sw_c1.metric("力成 (6239) 現價", f"{p_6239:.2f} 元", f"持有 {sb_lots_6239:.1f} 張 (@250.17)")
    sw_c2.metric("欣銓 (3264) 現價", f"{p_3264:.2f} 元", f"持有 {sb_lots_3264:.1f} 張 (@168.00)")
    sw_c3.metric("日月光 (3711) 現價", f"{p_3711:.2f} 元", ase_zone)
    sw_c4.metric("即時換股比值 (門檻 >= 1.18)", f"{swap_ratio:.3f}", f"換股後淨現金 {swap_net_cash:+,.0f} 元")

    st.markdown(clean_html(f"""
    <div class="swap-card">
    • <b>📐 換股公式即時演算</b>：賣出 (2張力成 {2000*p_6239:,.0f} ＋ 1張欣銓 {1000*p_3264:,.0f} ＝ <b>{swap_sell_twd:,.0f} 元</b>) － 買進 1張日月光 (<b>{1000*p_3711:,.0f} 元</b>) － 預估稅費 3,000 元 ＝ <b style="color:#ffd54f; font-size:17px;">淨剩餘現金 {swap_net_cash:+,.0f} 元</b><br>
    • <b>🚦 即時換股決策燈號</b>：<b style="font-size:16.5px; color:#00e676;">{swap_signal}</b><br>
    • <b>📏 日月光四區間基準</b>：🔴 高風險 `&gt;= 455` ｜ 🟡 過渡 `420～454` ｜ 🟢 合理 `360～415` ｜ 💎 鐵板 `300～345`（開板有量才執行，不殺跌停市價）
    </div>
    """), unsafe_allow_html=True)

    st.markdown("#### 🔄 【模組三之二：00400A 滾動合理退場試算 ＆ 月配低波債券 (00985B / 00713) 進場殖利率反推】")
    div_985_ttm, div_985_ann = etf_m["00985B"]["div_ttm"], etf_m["00985B"]["div_latest"] * 12.0
    div_713_ttm = etf_m["00713"]["div_ttm"]
    y_985_ttm = round(div_985_ttm / max(0.1, p_00985b) * 100.0, 2)
    y_985_ann = round(div_985_ann / max(0.1, p_00985b) * 100.0, 2)
    y_713_ttm = round(div_713_ttm / max(0.1, p_00713) * 100.0, 2)
    sell_400a_val = sb_lots_00400a * 1000.0 * p_00400a
    conv_985b_lots = sell_400a_val / max(1.0, p_00985b * 1000.0)
    conv_985b_annual_inc = conv_985b_lots * 1000.0 * div_985_ttm

    st.markdown(clean_html(f"""
    <div class="tab-guide-box" style="border-left-color:#fbbf24;">
    <b>⚠️ 00400A 處置原則（不建議再加碼・動態滾動退場轉債）</b>：<br>
    • <b>最新配息警訊</b>：00400A 現價 <b>{p_00400a:.2f} 元</b>（持 {sb_lots_00400a:.0f} 張 @14.40），12/08 除息已由 0.12 元腰斬至 <b>0.06 元</b>；<b>不建議再加碼，亦嚴禁買進 00406A (賣Call陷阱) 與 00953B/00945B (垃圾債)</b>。<br>
    • <b>動態滾動退場價區</b>：第一批減碼區 <b style="color:#ffd54f;">{etf_m['00400A']['exit_t1']:.2f} 元</b>（10MA={etf_m['00400A']['ma10']:.2f} / 0.382反彈位）；第二批主力退場區 <b style="color:#00e676;">{etf_m['00400A']['exit_t2']:.2f} 元</b>（20MA={etf_m['00400A']['ma20']:.2f} / 0.50反彈位）。<br>
    • <b>即時轉債試算</b>：若將 {sb_lots_00400a:.0f} 張 00400A（約 <b>{sell_400a_val/10000:.1f} 萬元</b>）全數轉入 <b>00985B ({p_00985b:.2f}元)</b>，可換得約 <b>{conv_985b_lots:.1f} 張</b>，每年立刻鎖定約 <b style="color:#00e676;">{conv_985b_annual_inc:,.0f} 元</b>（約 {conv_985b_annual_inc/12:,.0f} 元/月）RR2 低波債息！
    </div>
    """), unsafe_allow_html=True)

    df_bond_yield = pd.DataFrame([
        {
            "水庫三核心標的": "00985B 群益ESG投等債0-5 (月配65%)", "即時現價": f"{p_00985b:.2f} 元",
            "近一年/最新年化配息": f"TTM {div_985_ttm:.3f}元 / 年化 {div_985_ann:.3f}元",
            "目前殖利率": f"TTM {y_985_ttm:.2f}% / 年化 {y_985_ann:.2f}%",
            "💎 超值重錘區 (>=6.0%)": f"<= {div_985_ttm/0.06:.2f} ~ {div_985_ann/0.06:.2f} 元",
            "✅ 合理建倉區 (5.5%~6.0%)": f"{div_985_ann/0.06:.2f} ~ {div_985_ann/0.055:.2f} 元",
            "⚖️ 底線進場價 (>=5.0%)": f"<= {div_985_ttm/0.05:.2f} ~ {div_985_ann/0.05:.2f} 元",
            "當前買點判定": "💎 超值建倉區！" if y_985_ann >= 6.0 else ("✅ 合理建倉區" if y_985_ann >= 5.5 else "⚖️ 達5%底線")
        },
        {
            "水庫三核心標的": "00713 元大台灣高息低波 (季配35%)", "即時現價": f"{p_00713:.2f} 元",
            "近一年/最新年化配息": f"TTM {div_713_ttm:.2f}元", "目前殖利率": f"TTM {y_713_ttm:.2f}%",
            "💎 超值重錘區 (>=7.0%)": f"<= {div_713_ttm/0.07:.2f} 元",
            "✅ 合理建倉區 (6.0%~7.0%)": f"{div_713_ttm/0.07:.2f} ~ {div_713_ttm/0.06:.2f} 元",
            "⚖️ 底線進場價 (>=5.5%)": f"<= {div_713_ttm/0.055:.2f} 元",
            "當前買點判定": "💎 超值重錘區" if y_713_ttm >= 7.0 else ("✅ 合理建倉區！" if y_713_ttm >= 6.0 else "⚖️ 觀察區")
        }
    ])
    st.dataframe(df_bond_yield, use_container_width=True, hide_index=True)

    st.markdown("#### 💧 【模組三之三：全資產四水庫即時市值、半導體集中度與被動現金流進度】")
    r_c1, r_c2, r_c3, r_c4 = st.columns(4)
    r_c1.metric("💰 全資產即時估值 (零槓桿)", f"{total_assets/10000:,.1f} 萬元", f"匯率 {fx_now:.3f}")
    r_c2.metric("🔬 半導體總曝險佔比", f"{semi_ratio_pct:.1f}%", "⚠️ >50% 須靠水庫三平衡" if semi_ratio_pct > 50 else "✅ 配置均衡", delta_color="inverse")
    r_c3.metric("🌊 水庫三累積進度 (目標200萬)", f"{val_r3/10000:,.1f} / 200 萬", f"達成率 {min(100.0, val_r3/2000000*100):.1f}%")
    r_c4.metric("🔫 水庫四機動彈藥 (SGOV+USD)", f"{val_r4/10000:,.1f} 萬元", f"SGOV 30股 + ${sb_usd_cash:,.0f}")

    df_res_table = pd.DataFrame([
        {"水庫分類": "水庫一：生活＋急用雙水庫 (永不動用)", "核心組成": "日常 20萬 ＋ 急用定存 20萬 (撐9個月開銷)", "即時市值(萬)": round(val_r1/10000, 1), "佔比(%)": round(val_r1/total_assets*100, 1)},
        {"水庫分類": "水庫二：傳家寶底倉 — 台股半導體", "核心組成": f"台積電4張(@{p_2330:,.0f}) ＋ 日月光3張(@{p_3711:,.0f})", "即時市值(萬)": round(val_r2_tw_semi/10000, 1), "佔比(%)": round(val_r2_tw_semi/total_assets*100, 1)},
        {"水庫分類": "水庫二：傳家寶底倉 — 台股金融股息", "核心組成": "玉山金100.45張、富邦金11.74張、台中銀27.15張、台新新光金/臺企銀", "即時市值(萬)": round(val_r2_tw_fin/10000, 1), "佔比(%)": round(val_r2_tw_fin/total_assets*100, 1)},
        {"水庫分類": "水庫二：傳家寶底倉 — 美股 AI 龍頭", "核心組成": f"AVGO 35股(${p_avgo:.2f}) ＋ NVDA 80股(${p_nvda:.2f})", "即時市值(萬)": round(val_r2_us_semi/10000, 1), "佔比(%)": round(val_r2_us_semi/total_assets*100, 1)},
        {"水庫分類": "水庫三：第二道現金流 (目標200萬)", "核心組成": f"00985B {sb_lots_00985b:.1f}張 (65%) ＋ 00713 {sb_lots_00713:.1f}張 (35%)", "即時市值(萬)": round(val_r3/10000, 1), "佔比(%)": round(val_r3/total_assets*100, 1)},
        {"水庫分類": "水庫四：跨市機動抄底彈藥", "核心組成": f"SGOV 30股(${p_sgov:.2f}) ＋ ${sb_usd_cash:,.0f} 美元現金", "即時市值(萬)": round(val_r4/10000, 1), "佔比(%)": round(val_r4/total_assets*100, 1)},
        {"水庫分類": "待汰弱與比價換股衛星部位", "核心組成": f"力成{sb_lots_6239:.1f}張 ＋ 欣銓{sb_lots_3264:.1f}張 ＋ 00400A {sb_lots_00400a:.0f}張", "即時市值(萬)": round((val_sat_semi+val_sat_400a)/10000, 1), "佔比(%)": round((val_sat_semi+val_sat_400a)/total_assets*100, 1)}
    ])
    st.dataframe(df_res_table, use_container_width=True, hide_index=True)

    with st.expander("📦 次要收合：0050／0050正2 母子艦隊紀錄（目前已全數清空 0 張）", expanded=False):
        st.caption(f"0050正2 (00631L) 現價：{etf_m['00631L']['p']:.2f} 元（5MA={etf_m['00631L'].get('ma5', 0):.2f} / 20MA={etf_m['00631L'].get('ma20', 0):.2f}）｜ 目前已全數清空，依股災雙雷達總開關控管，落底達 4/5 前嚴禁開啟槓桿。")

# ==================== TAB 3: 短線旱地拔蔥榜 ====================
with tab3:
    if radar["master_state"] == "LOCKED":
        st.error(f"🚨 【淘金客 v24.1 股災總開關紅燈鎖定中（預警 {radar['w_count']}/5・落底 {radar['b_count']}/5）】目前嚴禁追買短線突破股！已自動排除「ADX>25 且 -DI>+DI」空頭加速股與「BIAS10 > +12%」極度過熱股。")
    else:
        st.success(f"{radar['master_badge']} ｜ 已套用 5 組量化接軌指標（DMI雙檢 / BIAS兩段式煞車 / 假突破三合一 / OBV+RS20D+大戶 / ATR%波動尺），請至 Tab 1 匯出模式 A 批次包給淘金客守門！")

    st.markdown("#### 🔥 【表 1-A：短線看技術＋基本面（站穩月線＋DMI多方＋雙成長・已排除高ADX空頭加速與極度過熱）】")
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

# ==================== TAB 4: 長線波段與 MSCI 動態主流池 ====================
with tab4:
    if radar["master_state"] == "LOCKED":
        st.warning("🚨 股災總開關紅燈鎖定中：表 3-B 與 MSCI 清單保留顯示但已強制標註「空頭加速（防高ADX假多頭）」與「ATR%高波動」，請待落底共振達 4/5 再分批進場！")

    st.markdown(f"#### 🌐 【模組四：MSCI 每季動態納入/移除名單 × 受影響族群龍頭與當日熱門股連動】（當前季度：`{msci_data['quarter']}`）")
    with st.expander("🔄 每季公告日一鍵更新：貼上最新 MSCI 季度調整公告文字，3 秒自動解析並永久儲存", expanded=False):
        q_lbl = st.text_input("新季度標籤", value="2027/02 (季度調整)")
        raw_msci_txt = st.text_area("貼上新聞中的『標準指數新增/刪除、小型指數新增/刪除』段落（含 4 碼代號）：", height=110)
        if st.button("💾 解析並更新為最新季度 MSCI 名單"):
            if raw_msci_txt.strip():
                parsed_m = parse_msci_text_override(raw_msci_txt, price_dict_all, q_lbl)
                try:
                    with open(MSCI_CACHE_FILE, "w", encoding="utf-8") as f:
                        json.dump(parsed_m, f, ensure_ascii=False)
                    fetch_dynamic_msci_data.clear()
                    st.success("✅ 已解析並儲存最新季度 MSCI 名單！請點上方「重抓」按鈕套用。")
                except Exception as e:
                    st.error(f"儲存失敗：{e}")

    scanned_recs = st.session_state.get("scanned_sector_records", [])
    theme_link_rows = []
    if not df_msci_audit.empty:
        for th_name, grp in df_msci_audit.groupby("影響細分族群"):
            adds = [f"{r['代碼']}{r['股名'].split(' ')[0]}({r['今日%']:+.1f}%)" for _, r in grp.iterrows() if "新增" in str(r["MSCI異動別"])]
            dels = [f"{r['代碼']}{r['股名'].split(' ')[0]}({r['今日%']:+.1f}%)" for _, r in grp.iterrows() if "刪除" in str(r["MSCI異動別"])]
            bell_list = MSCI_THEME_BELLWETHERS.get(th_name, [])
            bells_str = "、".join([f"{bn}({bc}) {price_dict_all.get(bc,{}).get('chg_pct',0):+.1f}%" for bc, bn in bell_list]) or "依盤面領漲股"
            same_th_recs = [x for x in scanned_recs if (infer_msci_theme(x["code"], x["name"], x["official_sec"]) == th_name)]
            same_th_recs.sort(key=lambda x: (x["chg_1d"], x["yod_ratio"]), reverse=True)
            hot_in_th = "、".join([f"{x['name']}({x['code']}) {x['chg_1d']:+.1f}%[量{x['yod_ratio']:.1f}x]" for x in same_th_recs[:3]]) or "當日量縮整理"
            theme_link_rows.append({
                "受影響細分族群": th_name,
                "📈 本季 MSCI 納入受惠": "、".join(adds) if adds else "➖",
                "📉 本季 MSCI 剔除承壓": "、".join(dels) if dels else "➖",
                "👑 族群指標龍頭股": bells_str,
                "🔥 當日族群盤中熱門前3強": hot_in_th
            })
        st.dataframe(pd.DataFrame(theme_link_rows), use_container_width=True, hide_index=True)

    st.markdown("##### 📋 【本季 MSCI 完整納入/移除標的即時掃描表（含 5 組新指標與處置股警示）】")
    if not df_msci_audit.empty:
        st.dataframe(style_tw_stock_df(pin_code_name_index(df_msci_audit)), use_container_width=True)
    else:
        st.info("正在同步 MSCI 觀察池 K 線資料...")

    st.markdown("#### 🏛️ 【表 3-A：核心權值與政策防彈衣 長線多頭波段榜】")
    if not df_long_bc.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_long_bc)), use_container_width=True)
    else: st.info("大盤急殺修正期間，暫無同時站上月線與 DMI 多方之權值股，請看下方表 3-B 追蹤回測支撐買點。")

    st.markdown("#### 🔄 【表 3-B：權值股超跌回測支撐買點 ＆ 轉機題材榜】")
    if not df_long_turn.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_long_turn)), use_container_width=True)
    else: st.info("目前暫無符合條件之回測支撐買點。")

# ==================== TAB 5: 穀倉與防墊料專區 ====================
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

    if not df_granary_audit.empty:
        df_g_safe = df_granary_audit[~df_granary_audit["代碼"].isin(OLD_GRANARY_ODM_WATCH)].copy()
        df_g_odm = df_granary_audit[df_granary_audit["代碼"].isin(OLD_GRANARY_ODM_WATCH)].copy()
        st.markdown("##### 🌟 【一、三大新穀倉 ＆ 🛡️ 政策內需防彈衣 即時戰力排行（含 5 組量化接軌指標）】")
        st.dataframe(style_tw_stock_df(pin_code_name_index(df_g_safe)), use_container_width=True)
        st.markdown("##### 🐀 【二、最後一隻老鼠（舊穀倉純伺服器組裝 ODM：存貨/應收帳款 QoQ ＆ 營業現金流防墊料體檢表）】")
        if not df_g_odm.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_g_odm)), use_container_width=True)
    else:
        st.info("正在載入穀倉賽道資料...")

# ==================== TAB 6: 上市櫃月營收快篩 ====================
with tab6:
    target_latest_ym = get_target_revenue_ym_list()[0][2]
    st.markdown(f"#### 📈 【上市櫃每月營收動態撈取 ＆ 三級除殭屍股快篩專區】（最新目標年月：`{target_latest_ym}`）")
    if df_all_rev.empty:
        st.warning("⚠️ 目前證交所/公開資訊觀測站月營收暫無回應，請點選上方「重抓」按鈕再試。")
    else:
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
        st.download_button(label=f"📥 下載當前篩選月營收與法人估值表 ({len(df_display_rev)} 家) CSV", data=csv_bytes, file_name=f"tw_rev_v60_{target_latest_ym}.csv", mime="text/csv")

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
    fetch_mis_realtime_batch.clear()
    st.rerun()
