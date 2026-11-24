import datetime, calendar, pytz, os, json, re
import pandas as pd
import numpy as np

# ==========================================
# 基礎時間、格式與排版共用工具
# ==========================================
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
    if now.weekday() >= 5:
        return 1.0, "休市/週末 (完整收盤量)"
    t_val = now.hour * 60 + now.minute
    if t_val < 540:
        return 1.0, "開盤前 (前一交易日完整量)"
    if t_val >= 810:
        return 1.0, "已收盤 (當日完整收盤量)"
    elapsed = max(1, t_val - 540)
    if elapsed <= 15:
        mult = min(4.2, (270.0 / elapsed) * 0.32)
    elif elapsed <= 45:
        mult = min(3.8, (270.0 / elapsed) * 0.48)
    elif elapsed <= 90:
        mult = (270.0 / elapsed) * 0.65
    elif elapsed <= 180:
        mult = (270.0 / elapsed) * 0.82
    else:
        mult = 270.0 / elapsed
    mc = round(max(1.0, min(mult, 4.5)), 2)
    return mc, f"盤中即時推估 (U型倍率 {mc:.2f}x)"

def parse_roc_or_iso_date(d_raw: str) -> str:
    s = str(d_raw).replace("/", "").replace("-", "").strip()
    if len(s) == 7 and s.isdigit():
        return f"{int(s[:3]) + 1911}-{s[3:5]}-{s[5:7]}"
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return ""

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

def is_zombie_stock_tiered(code: str, curr_p: float, est_vol: float, ma5_vol: float = 0.0) -> bool:
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
    if df.empty:
        return df
    def color_tw_val(val):
        try:
            s = str(val).replace("%", "").replace(",", "").replace("+", "").replace("元", "").replace("倍", "").replace("張", "").split(" ")[0].strip()
            if s in ["", "-", "--", "待公告", "未揭露", "虧損/無PE", "N/A"]:
                return ""
            v = float(s)
            return "color: #ff3b30; font-weight: bold;" if v > 0 else ("color: #00b853; font-weight: bold;" if v < 0 else "")
        except Exception:
            return ""
    target_cols = [c for c in ["今日%", "3日%", "外資買賣(張)", "投信買賣(張)", "單月營收YoY", "累計年營收YoY", "RS20D%"] if c in df.columns]
    fmt_dict = {k: "{:.2f}" for k in ["現價", "💎EPS合理切入價", "🎯EPS目標價", "預估EPS(元)", "昨日量比", "5MA量比", "天花板(反壓)", "月線承接", "鋼鐵防線", "極限停損"] if k in df.columns}
    for k_int in ["外資買賣(張)", "投信買賣(張)", "預估量(張)", "成交量(張)"]:
        if k_int in df.columns:
            fmt_dict[k_int] = "{:,.0f}" if "量" in k_int else "{:+,d}"
    if "本益比(PE)" in df.columns: fmt_dict["本益比(PE)"] = "{:.1f}"
    if "今日%" in df.columns: fmt_dict["今日%"] = "{:+.2f}%"
    if "3日%" in df.columns: fmt_dict["3日%"] = "{:+.2f}%"
    if "日J值" in df.columns: fmt_dict["日J值"] = "{:.1f}"
    if "上影線%" in df.columns: fmt_dict["上影線%"] = "{:.1f}%"
    try:
        return df.style.map(color_tw_val, subset=target_cols).format(fmt_dict, na_rep="-")
    except Exception:
        return df

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

# ==========================================
# 全域靜態清單：宏爺三大新穀倉、MSCI 動態池、核心資產池與美股板塊
# ==========================================
CUSTOM_EVENTS_FILE = "custom_risk_events.json"
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
ACTIVE_CB_BASE_SET = {
    "2301", "3017", "3324", "6187", "3131", "3583", "2383", "2368", "6274", "2345",
    "3533", "2357", "4938", "3231", "2382", "2355", "4976", "3701", "2009", "1527",
    "6112", "3147", "5013", "6446", "3691", "8069", "6751", "6877", "2453", "3264",
    "6285", "3558", "2449", "5269", "8016", "2395", "3008", "2327", "2059", "3653"
}
MARGIN_DILUTION_SET = {"2382", "3231", "2317", "4938", "2356", "2324", "3706"}
MARGIN_EXPANSION_SET = {"2330", "3017", "3324", "3653", "2059", "2383", "2368", "6274", "2345", "2308", "3711", "6239", "6187", "3131", "3583", "5274", "2301", "3443", "3661", "2454", "6515", "6223", "1519", "1503", "1513", "6683", "7822", "3163"}

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

# ==========================================
# 分類與評級共用函式
# ==========================================
def infer_msci_theme(code: str, name: str, sector_str: str = "") -> str:
    s = f"{name} {sector_str}"
    if code in ["3189", "8046", "6274", "6672", "3037", "2383", "2368"] or any(k in s for k in ["載板", "CCL", "銅箔", "PCB"]):
        return "ABF載板與高頻CCL"
    if code in ["6683", "7822", "8021", "6515", "6223", "3711", "6239", "3264", "6187", "3131", "3583", "2449"] or (("電腦及週邊" not in s) and any(k in s for k in ["封測", "探針", "檢測設備", "半導體設備", "CoWoS", "先進封裝"])):
        return "先進封測介面設備"
    if code in ["3163", "4991", "3016", "3363", "3450", "6442"] or any(k in s for k in ["光通訊", "矽光子", "磊晶"]):
        return "CPO矽光子與光通訊"
    if code in ["3693", "7711", "3026", "6173", "2327", "3661", "3443"] or any(k in s for k in ["被動元件", "準系統", "ASIC"]):
        return "ASIC準系統與高壓被動"
    if code in ["2615", "2603", "2609"] or "航運" in s:
        return "貨櫃航運"
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

def evaluate_granary_and_anti_trap(code: str, single_yoy, macro_withdrawal_alert: bool = False):
    yoy_pos = (single_yoy is None) or (pd.notna(single_yoy) and float(single_yoy) > 0.0)
    if code in OLD_GRANARY_ODM_WATCH:
        h = ODM_Q3_OFFICIAL_BASE.get(code, {})
        if yoy_pos and (h.get("inv_qoq", 0) >= 19.0 or h.get("ar_qoq", 0) >= 20.0) and (not h.get("ocf_positive", True)):
            return f"🐀 警戒：疑似墊料壓貨 (-20分｜{h.get('note', '')})", -20, "🐀疑似墊料壓貨"
        if code == "6669" and h.get("ocf_positive", True):
            return f"🔄 雙棲：ASIC自研伺服器 (+8分｜{h.get('note', '')})", +8, "🔄ASIC自研雙棲"
        return f"⚠️ 舊穀倉ODM觀察 (-10分｜{h.get('note', '')})", -10, "⚠️舊穀倉ODM觀察"
    if code in GRANARY_1_CSP_COMMON:
        b = 23 if macro_withdrawal_alert else 15
        return f"🌟 新穀倉1：CSP自研/共通規格 (+{b}分・無墊料風險)", b, "🌟新穀倉1(共通規格)"
    if code in GRANARY_2_POWER_THERMAL:
        b = 14 if macro_withdrawal_alert else 10
        return f"⚡ 新穀倉2：缺電/過熱基建 (+{b}分・剛性升級)", b, "⚡新穀倉2(電力散熱)"
    if code in GRANARY_3_EDGE_MEMORY:
        return "📱 新穀倉3：邊緣AI與記憶體封測 (+5分・終端轉嫁)", 5, "📱新穀倉3(邊緣封測)"
    if code in POLICY_DEFENSE_ARMOR:
        b = 18 if macro_withdrawal_alert else 8
        return f"🛡️ 政策內需防彈衣 (+{b}分・兆元內需/外資提款避風港)", b, "🛡️政策內需防彈衣"
    return "➖ 一般電子/題材股 (+0分)", 0, "➖一般題材"

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

def map_tw_theme_cluster(code: str, name: str, sector: str, official_sec: str) -> str:
    s_all = f"{sector} {official_sec} {name}"
    if code in ["1513", "1514", "8936", "9933", "6139", "1519", "1503"] or any(k in s_all for k in ["重電", "電網", "水資源", "無塵室", "統包工程"]): return "🛡️ 重電電網與兆元內需基建"
    if code in ["2454", "3034", "3443", "3661", "5274", "3529", "5269", "8016", "6515", "6223"] or any(k in s_all for k in ["IC設計", "矽智財", "ASIC", "BMC", "探針", "測試座"]): return "🌟 IC設計與CSP自研晶片測試"
    if code in ["3711", "6239", "2449", "3264", "6187", "3131", "3583", "6683", "7822", "8021"] or (("電腦及週邊" not in s_all) and any(k in s_all for k in ["封測", "扇形封裝", "CoWoS", "封裝", "檢測設備"])): return "🔬 先進封測與介面設備"
    if code in ["2330", "2303", "5347"] or "晶圓" in s_all: return "🏭 晶圓代工與半導體"
    if code in ["2383", "2368", "6274", "3533", "3189", "8046", "3037"] or any(k in s_all for k in ["CCL", "PCB", "載板", "連接器", "銅箔"]): return "⚡ ABF載板與高頻CCL/PCB"
    if code in ["3017", "3324", "3653", "2059"] or any(k in s_all for k in ["散熱", "均熱片", "水冷", "導軌"]): return "❄️ AI水冷散熱與伺服器機構"
    if code in ["2308", "2301", "3211"] or any(k in s_all for k in ["電源", "儲能", "BBU"]): return "🔋 雲端電源與BBU備援電池"
    if code in ["2317", "2382", "3231", "6669", "2357", "4938", "2356", "3706", "3693", "7711"] or any(k in s_all for k in ["AI伺服器", "AI PC", "電腦及週邊"]): return "🖥️ AI伺服器與ODM準系統"
    if code in ["2345", "2379", "3558", "6285", "3163", "4991", "3016", "3363", "3450", "6442"] or any(k in s_all for k in ["網通", "通信網路", "光通訊", "矽光子", "衛星"]): return "📡 高階網通與CPO矽光子"
    if any(k in s_all for k in ["金融", "金控", "銀行", "保險", "證券"]): return "🏦 金融官股與績優金控"
    if any(k in s_all for k in ["化學", "化工", "材料", "特化", "塑膠", "玻陶", "玻璃"]): return "🧪 特用化學與半導體材料"
    if any(k in s_all for k in ["生技", "醫療", "新藥", "製藥"]): return "🧬 生技醫療與新藥研發"
    if code in ["3008", "3406"] or any(k in s_all for k in ["光電", "光學", "鏡頭", "面板"]): return "📷 光學鏡頭與邊緣AI感測"
    return "🚀 電子零組件與中小型破繭股"

# ==========================================
# 模組二：雙軌自動滾動風險事件引擎（以 (年, 月, 類別) 精準去重）
# ==========================================
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
    today_d = get_tw_now().date()
    end_d = today_d + datetime.timedelta(days=horizon_days)
    covered_ym_cats, all_events = set(), []

    for ev in OFFICIAL_ANCHOR_EVENTS:
        ev_d = datetime.datetime.strptime(ev["date"], "%Y-%m-%d").date()
        for ov_cat in ev.get("overrides", [ev["cat"]]):
            covered_ym_cats.add((ev_d.year, ev_d.month, ov_cat))
        all_events.append(dict(ev))

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
        try:
            ev_d = datetime.datetime.strptime(item["date"], "%Y-%m-%d").date()
        except Exception:
            continue
        delta_days = (ev_d - today_d).days
        if delta_days < -2 or ev_d > end_d:
            continue
        status = "✅ 已度過" if delta_days < 0 else ("🚨 今日登場！" if delta_days == 0 else (f"🔴 倒數 {delta_days} 天 (高波動警戒)" if delta_days <= 3 else (f"🟡 倒數 {delta_days} 天" if delta_days <= 7 else f"📅 尚餘 {delta_days} 天")))
        row_obj = {
            "日期": item["date"], "倒數狀態": status, "重大風險事件": item["title"],
            "🟢 優於預期／鴿派劇本": item["bull"], "🔴 差於預期／鷹派劇本": item["bear"],
            "🛡️ 實戰因應": item["action"],
            "_short": item["short"], "_days": delta_days, "_level": item["level"]
        }
        rows.append(row_obj)
        if delta_days >= 0:
            upcoming_rows.append(row_obj)

    rows.sort(key=lambda x: (x["_days"], 0 if x["_level"] == "HIGH" else 1))
    upcoming_rows.sort(key=lambda x: (x["_days"], 0 if x["_level"] == "HIGH" else 1))
    return rows, upcoming_rows, (upcoming_rows[0] if upcoming_rows else None)
