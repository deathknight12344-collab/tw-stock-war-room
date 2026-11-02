import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import datetime
import pytz
import requests
import urllib3
import io
import os
import json
import time

# 關閉 SSL 憑證警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(
    page_title="台股每日戰情室 v37.0",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ==========================================
# 模組 0：母子艦隊持倉參數「雙重記憶引擎」(JSON 檔案 + URL Query Params)
# 確保按 F5 重新整理網頁或重開分頁時，設定的張數與成本永遠不會遺失！
# ==========================================
CONFIG_FILE = "fleet_portfolio_config.json"
DEFAULT_PORTFOLIO = {
    "lots_2x": 4.00,
    "cost_2x": 35.27,
    "lots_1x": 0.70,
    "cost_1x": 101.90,
    "cash_pool": 0.0
}

def load_portfolio_config():
    cfg = dict(DEFAULT_PORTFOLIO)
    # 1. 優先讀取伺服器本地記憶檔
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                for k in cfg:
                    if k in saved:
                        cfg[k] = float(saved[k])
        except Exception:
            pass
    # 2. 若網址列帶有記憶參數 (st.query_params)，則覆寫確保 F5 刷新 100% 不掉資料
    qp = st.query_params
    for k in cfg:
        if k in qp:
            try:
                cfg[k] = float(qp[k])
            except Exception:
                pass
    return cfg

def save_portfolio_config(lots_2x, cost_2x, lots_1x, cost_1x, cash_pool):
    cfg = {
        "lots_2x": round(float(lots_2x), 2),
        "cost_2x": round(float(cost_2x), 2),
        "lots_1x": round(float(lots_1x), 2),
        "cost_1x": round(float(cost_1x), 2),
        "cash_pool": round(float(cash_pool), 0)
    }
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except Exception:
        pass
    try:
        for k, v in cfg.items():
            st.query_params[k] = str(v)
    except Exception:
        pass

# ==========================================
# 模組 1：台灣時間定錨、台股紅漲綠跌表格染色引擎、U 型量能推估
# ==========================================
def get_tw_now():
    tz = pytz.timezone("Asia/Taipei")
    return datetime.datetime.now(tz)

def get_volume_multiplier():
    now = get_tw_now()
    if now.weekday() >= 5:
        return 1.0, "休市/週末 (完整收盤量)"
    t_val = now.hour * 60 + now.minute
    open_t = 9 * 60
    close_t = 13 * 60 + 30
    if t_val < open_t:
        return 1.0, "開盤前 (前一交易日完整量)"
    if t_val >= close_t:
        return 1.0, "已收盤 (當日完整收盤量)"
    
    elapsed = max(1, t_val - open_t)
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
    mult_clean = round(max(1.0, min(mult, 4.5)), 2)
    return mult_clean, f"盤中即時推估 (U型倍率 {mult_clean:.2f}x)"

def style_tw_stock_df(df):
    """台股專屬配色：正數（漲/成長）顯示鮮紅色，負數（跌/衰退）顯示翠綠色"""
    if df.empty:
        return df

    def color_tw_val(val):
        try:
            if isinstance(val, (int, float)):
                v = float(val)
            else:
                s = str(val).replace("%", "").replace(",", "").replace("+", "").strip()
                if s in ["", "-", "--", "待公告", "未揭露"]:
                    return ""
                v = float(s)
            if v > 0:
                return "color: #ff3b30; font-weight: bold;"
            elif v < 0:
                return "color: #00b853; font-weight: bold;"
            return ""
        except Exception:
            return ""

    target_cols = [c for c in ["今日%", "3日%", "月營收YoY"] if c in df.columns]
    fmt_dict = {}
    if "現價" in df.columns:
        fmt_dict["現價"] = "{:.2f}"
    if "今日%" in df.columns:
        fmt_dict["今日%"] = "{:+.2f}%"
    if "3日%" in df.columns:
        fmt_dict["3日%"] = "{:+.2f}%"
    if "日J值" in df.columns:
        fmt_dict["日J值"] = "{:.1f}"
    if "5MA量比" in df.columns:
        fmt_dict["5MA量比"] = "{:.2f}"
    for c_name in ["天花板(反壓)", "月線承接", "鋼鐵防線", "極限停損"]:
        if c_name in df.columns:
            fmt_dict[c_name] = "{:.2f}"

    try:
        styler = df.style.map(color_tw_val, subset=target_cols).format(fmt_dict, na_rep="-")
        return styler
    except Exception:
        return df

# ==========================================
# 模組 2：證交所 MIS 無快取直連 ＋ Yahoo v8 1分K 雙源即時報價引擎
# ==========================================
def parse_mis_price(item, is_index=False):
    def to_f(val):
        try:
            s = str(val).replace(",", "").strip()
            return float(s) if s not in ["", "-", "--", "None"] else None
        except Exception:
            return None

    z_val = to_f(item.get("z"))
    if z_val and z_val > 0:
        return z_val

    if not is_index:
        b_str = str(item.get("b", "")).split("_")[0]
        a_str = str(item.get("a", "")).split("_")[0]
        b_val = to_f(b_str)
        a_val = to_f(a_str)
        if b_val and a_val and b_val > 0 and a_val > 0:
            return round((b_val + a_val) / 2.0, 2)
        if b_val and b_val > 0:
            return b_val
        if a_val and a_val > 0:
            return a_val

    if is_index:
        return None

    y_val = to_f(item.get("y"))
    return y_val if (y_val and y_val > 0) else None

def fetch_yahoo_v8_realtime(ticker_symbol):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache"
    }
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}?interval=1m&range=1d&_={int(time.time() * 1000)}"
    try:
        r = requests.get(url, headers=headers, timeout=5, verify=False)
        if r.status_code == 200:
            res = r.json().get("chart", {}).get("result", [])
            if res:
                meta = res[0].get("meta", {})
                indicators = res[0].get("indicators", {}).get("quote", [{}])[0]
                closes = [x for x in indicators.get("close", []) if x is not None]
                opens = [x for x in indicators.get("open", []) if x is not None]
                highs = [x for x in indicators.get("high", []) if x is not None]
                lows = [x for x in indicators.get("low", []) if x is not None]

                p_now = float(meta.get("regularMarketPrice", closes[-1] if closes else 0.0))
                y_prev = float(meta.get("chartPreviousClose", meta.get("previousClose", p_now)))
                o_now = float(meta.get("regularMarketOpen", opens[0] if opens else p_now))
                h_now = float(meta.get("regularMarketDayHigh", max(highs) if highs else p_now))
                l_now = float(meta.get("regularMarketDayLow", min(lows) if lows else p_now))
                mkt_time = meta.get("regularMarketTime", int(time.time()))
                tz = pytz.timezone("Asia/Taipei")
                dt_obj = datetime.datetime.fromtimestamp(mkt_time, tz)

                if p_now > 0:
                    chg_pct = round(((p_now - y_prev) / y_prev) * 100, 2) if y_prev > 0 else 0.0
                    return {
                        "open": round(o_now, 2),
                        "close": round(p_now, 2),
                        "high": round(max(h_now, p_now), 2),
                        "low": round(min(l_now, p_now), 2),
                        "prev_close": round(y_prev, 2),
                        "chg_pct": chg_pct,
                        "vol_lots": 0,
                        "time": dt_obj.strftime("%H:%M:%S"),
                        "date": dt_obj.strftime("%Y%m%d"),
                        "ts": mkt_time
                    }
    except Exception:
        pass
    return None

@st.cache_data(ttl=10, show_spinner=False)
def fetch_mis_realtime_batch(code_market_tuples):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
        "Referer": "https://mis.twse.com.tw/stock/index.jsp",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache"
    }
    rt_dict = {}
    queries = ["tse_t00.tw"]
    for code, mkt in code_market_tuples:
        prefix = "tse" if mkt == "TW" else "otc"
        queries.append(f"{prefix}_{code}.tw")

    batch_size = 25
    for i in range(0, len(queries), batch_size):
        chunk = queries[i:i + batch_size]
        ex_ch = "|".join(chunk)
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={ex_ch}&json=1&delay=0&_={int(time.time() * 1000)}"
        try:
            r = requests.get(url, headers=headers, timeout=5, verify=False)
            if r.status_code == 200:
                data = r.json()
                for item in data.get("msgArray", []):
                    c_code = str(item.get("c", "")).strip()
                    is_idx = (c_code == "t00")
                    p_now = parse_mis_price(item, is_index=is_idx)
                    if not c_code or p_now is None:
                        continue
                    try:
                        y_prev = float(str(item.get("y", p_now)).replace(",", ""))
                    except Exception:
                        y_prev = p_now
                    try:
                        o_now = float(str(item.get("o", p_now)).replace(",", ""))
                    except Exception:
                        o_now = p_now
                    try:
                        h_now = float(str(item.get("h", p_now)).replace(",", ""))
                    except Exception:
                        h_now = p_now
                    try:
                        l_now = float(str(item.get("l", p_now)).replace(",", ""))
                    except Exception:
                        l_now = p_now
                    try:
                        v_lots = int(float(str(item.get("v", "0")).replace(",", "")))
                    except Exception:
                        v_lots = 0

                    chg_pct = round(((p_now - y_prev) / y_prev) * 100, 2) if y_prev > 0 else 0.0
                    rt_dict[c_code] = {
                        "open": round(o_now, 2),
                        "close": round(p_now, 2),
                        "high": round(max(h_now, p_now), 2),
                        "low": round(min(l_now, p_now), 2),
                        "prev_close": round(y_prev, 2),
                        "chg_pct": chg_pct,
                        "vol_lots": v_lots,
                        "time": str(item.get("t", "")),
                        "date": str(item.get("d", ""))
                    }
        except Exception:
            continue

    for code_key, yf_sym in [("t00", "%5ETWII"), ("00631L", "00631L.TW"), ("0050", "0050.TW")]:
        yv8 = fetch_yahoo_v8_realtime(yf_sym)
        if yv8 and yv8["close"] > 0:
            if code_key not in rt_dict:
                rt_dict[code_key] = yv8
            else:
                if rt_dict[code_key].get("open", 0) <= 0:
                    rt_dict[code_key]["open"] = yv8["open"]
                if abs(rt_dict[code_key]["close"] - rt_dict[code_key]["prev_close"]) < 0.01 and abs(yv8["close"] - yv8["prev_close"]) > 0.01:
                    rt_dict[code_key] = yv8

    return rt_dict

# ==========================================
# 模組 3：證交所/櫃買中心/集保中心 OpenAPI 基本面與盤後總表
# ==========================================
@st.cache_data(ttl=600, show_spinner=False)
def fetch_official_market_data():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    price_dict = {}
    tdcc_dict = {}
    rev_dict = {}
    twii_official = {"close": 0.0, "chg_pct": 0.0, "date": ""}

    try:
        r_idx = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK", headers=headers, timeout=10, verify=False)
        if r_idx.status_code == 200:
            idx_list = r_idx.json()
            if len(idx_list) > 0:
                last_row = idx_list[-1]
                idx_c = float(str(last_row.get("TAIEX", "0")).replace(",", ""))
                idx_chg = float(str(last_row.get("Change", "0")).replace(",", "").replace("+", ""))
                prev_idx = idx_c - idx_chg
                idx_pct = round((idx_chg / prev_idx) * 100, 2) if prev_idx > 0 else 0.0
                twii_official = {
                    "close": round(idx_c, 1),
                    "chg_pct": idx_pct,
                    "date": str(last_row.get("Date", ""))
                }
    except Exception:
        pass

    try:
        r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=10, verify=False)
        if r.status_code == 200:
            for row in r.json():
                code = str(row.get("Code", "")).strip()
                if (len(code) == 4 and code.isdigit()) or code in ["0050", "00631L"]:
                    try:
                        c_price = float(str(row.get("ClosingPrice", "0")).replace(",", ""))
                        chg_raw = str(row.get("Change", "0")).replace(",", "").replace("+", "").replace("X", "").strip()
                        chg_val = float(chg_raw) if chg_raw not in ["", "-", "--"] else 0.0
                        if "-" in str(row.get("Change", "")) and chg_val > 0:
                            chg_val = -chg_val
                        prev_c = c_price - chg_val
                        chg_pct = round((chg_val / prev_c) * 100, 2) if prev_c > 0 else 0.0
                        vol_lots = int(float(str(row.get("TradeVolume", "0")).replace(",", "")) // 1000)
                        if c_price > 0:
                            price_dict[code] = {
                                "name": str(row.get("Name", "")).strip(),
                                "close": c_price,
                                "chg_pct": chg_pct,
                                "vol_lots": vol_lots,
                                "market": "TW"
                            }
                    except Exception:
                        continue
    except Exception:
        pass

    try:
        r_tpex = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", headers=headers, timeout=10, verify=False)
        if r_tpex.status_code == 200:
            for row in r_tpex.json():
                code = str(row.get("SecuritiesCompanyCode", "")).strip()
                if len(code) == 4 and code.isdigit():
                    try:
                        c_price = float(str(row.get("Close", "0")).replace(",", ""))
                        chg_raw = str(row.get("Change", "0")).replace(",", "").replace("+", "").strip()
                        chg_val = float(chg_raw) if chg_raw not in ["", "-", "--", "除息", "除權"] else 0.0
                        prev_c = c_price - chg_val
                        chg_pct = round((chg_val / prev_c) * 100, 2) if prev_c > 0 else 0.0
                        vol_lots = int(float(str(row.get("TradingShares", "0")).replace(",", "")) // 1000)
                        if c_price > 0:
                            price_dict[code] = {
                                "name": str(row.get("CompanyName", "")).strip(),
                                "close": c_price,
                                "chg_pct": chg_pct,
                                "vol_lots": vol_lots,
                                "market": "TWO"
                            }
                    except Exception:
                        continue
    except Exception:
        pass

    try:
        r_csv = requests.get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", headers=headers, timeout=15, verify=False)
        if r_csv.status_code == 200 and len(r_csv.text) > 500:
            df_tdcc = pd.read_csv(io.StringIO(r_csv.text), dtype=str)
            df_tdcc.columns = [c.strip() for c in df_tdcc.columns]
            col_code = next((c for c in df_tdcc.columns if "代號" in c), None)
            col_lvl = next((c for c in df_tdcc.columns if "分級" in c), None)
            col_pct = next((c for c in df_tdcc.columns if "比例" in c), None)
            if col_code and col_lvl and col_pct:
                df_tdcc[col_code] = df_tdcc[col_code].str.strip()
                df_tdcc[col_lvl] = df_tdcc[col_lvl].str.strip()
                df_sub = df_tdcc[df_tdcc[col_lvl].isin(["12", "13", "14", "15"])].copy()
                df_sub["pct_num"] = pd.to_numeric(df_sub[col_pct].str.replace(",", ""), errors="coerce").fillna(0.0)
                grouped = df_sub.groupby(col_code)["pct_num"].sum()
                for k, v in grouped.items():
                    if 0 < v <= 100.0:
                        tdcc_dict[str(k)] = round(float(v), 1)
    except Exception:
        pass

    for url in [
        "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
        "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"
    ]:
        try:
            r_rev = requests.get(url, headers=headers, timeout=12, verify=False)
            if r_rev.status_code == 200:
                for row in r_rev.json():
                    code = str(row.get("公司代號", row.get("證券代號", ""))).strip()
                    yoy_raw = None
                    for k_name, v_val in row.items():
                        if "去年同月增減" in k_name or "同月增減" in k_name:
                            yoy_raw = str(v_val).replace(",", "").strip()
                            break
                    if code and yoy_raw not in [None, "", "-", "--", "N/A"]:
                        try:
                            rev_dict[code] = round(float(yoy_raw), 1)
                        except Exception:
                            pass
        except Exception:
            pass

    return price_dict, tdcc_dict, rev_dict, twii_official

# ==========================================
# 模組 4：長線「電子權值股」＋「金融官股/龍頭金控」專屬白名單資料庫
# (徹底排除一般沒落傳產與營建一次性入帳失真股！)
# ==========================================
ELECTRONICS_BLUE_CHIPS = {
    "2330": ("台積電", "👑電子權值・晶圓龍頭", "TW"),
    "2317": ("鴻海", "👑電子權值・AI伺服器", "TW"),
    "2454": ("聯發科", "👑電子權值・IC設計龍頭", "TW"),
    "2308": ("台達電", "👑電子權值・電源與儲能", "TW"),
    "2382": ("廣達", "👑電子權值・AI伺服器", "TW"),
    "3711": ("日月光投控", "👑電子權值・先進封測", "TW"),
    "3034": ("聯詠", "👑電子權值・IC設計", "TW"),
    "2379": ("瑞昱", "👑電子權值・網通IC", "TW"),
    "6669": ("緯穎", "👑電子權值・AI伺服器", "TW"),
    "3231": ("緯創", "👑電子權值・AI伺服器", "TW"),
    "2345": ("智邦", "👑電子權值・高階網通", "TW"),
    "3017": ("奇鋐", "👑電子權值・AI散熱", "TW"),
    "3533": ("嘉澤", "👑電子權值・高速連接器", "TW"),
    "3653": ("健策", "👑電子權值・均熱片龍頭", "TW"),
    "2059": ("川湖", "👑電子權值・伺服器導軌", "TW"),
    "2383": ("台光電", "👑電子權值・高頻CCL", "TW"),
    "2368": ("金像電", "👑電子權值・伺服器PCB", "TW"),
    "2357": ("華碩", "👑電子權值・AI PC龍頭", "TW"),
    "2301": ("光寶科", "👑電子權值・雲端電源", "TW"),
    "4938": ("和碩", "👑電子權值・通訊與車用", "TW"),
    "2327": ("國巨", "👑電子權值・被動元件龍頭", "TW"),
    "3008": ("大立光", "👑電子權值・光學鏡頭龍頭", "TW"),
    "2395": ("研華", "👑電子權值・工業電腦龍頭", "TW"),
    "5274": ("信驊", "👑電子權值・BMC晶片王", "TWO"),
    "3529": ("力旺", "👑電子權值・矽智財IP", "TWO"),
    "3661": ("世芯-KY", "👑電子權值・ASIC晶片", "TW"),
    "3443": ("創意", "👑電子權值・ASIC矽智財", "TW"),
    "2449": ("京元電子", "👑電子權值・AI晶片測試", "TW"),
    "3264": ("欣銓", "👑電子權值・車用與AI測試", "TWO"),
    "3324": ("雙鴻", "👑電子權值・水冷散熱", "TWO"),
    "6274": ("台燿", "👑電子權值・高階CCL", "TWO"),
    "8016": ("矽創", "👑電子權值・驅動與感測IC", "TW"),
    "5269": ("祥碩", "👑電子權值・高速傳輸IC", "TW"),
    "6187": ("萬潤", "👑電子權值・CoWoS設備", "TWO"),
    "3131": ("弘塑", "👑電子權值・CoWoS設備", "TWO"),
    "3583": ("辛耘", "👑電子權值・先進封裝設備", "TW"),
    "3558": ("神準", "👑電子權值・企業網通", "TWO"),
    "6285": ("啟碁", "👑電子權值・車用與衛星網通", "TW")
}

FINANCIAL_BLUE_CHIPS = {
    "2881": ("富邦金", "🏦金融權值・獲利王金控", "TW"),
    "2882": ("國泰金", "🏦金融權值・壽險龍頭金控", "TW"),
    "2891": ("中信金", "🏦金融權值・消金龍頭金控", "TW"),
    "2886": ("兆豐金", "🏦金融權值・官股匯銀龍頭", "TW"),
    "2884": ("玉山金", "🏦金融權值・高成長民營金控", "TW"),
    "2885": ("元大金", "🏦金融權值・證券龍頭金控", "TW"),
    "2892": ("第一金", "🏦金融權值・績優官股金控", "TW"),
    "2880": ("華南金", "🏦金融權值・穩健官股金控", "TW"),
    "5880": ("合庫金", "🏦金融權值・官股房貸龍頭", "TW"),
    "2890": ("永豐金", "🏦金融權值・高ROE民營金控", "TW"),
    "2883": ("凱基金", "🏦金融權值・壽險與創投金控", "TW"),
    "2887": ("台新金", "🏦金融權值・併購成長金控", "TW"),
    "2801": ("彰銀", "🏦金融權值・官股核心銀行", "TW"),
    "2834": ("臺企銀", "🏦金融權值・中小企銀官股", "TW")
}

CORE_WATCHLIST = {**ELECTRONICS_BLUE_CHIPS, **FINANCIAL_BLUE_CHIPS}

# 排除營建股（25xx 及特定建商如 1808 潤隆），避免交屋一次性入帳 YoY +60000% 污染長線榜
CONSTRUCTION_BLACKLIST = {"1808", "2501", "2504", "2505", "2506", "2509", "2511", "2515", "2516", "2520", "2524", "2527", "2528", "2530", "2534", "2535", "2536", "2537", "2538", "2539", "2540", "2542", "2543", "2545", "2546", "2547", "2548", "2597", "5522", "5534"}

def is_traditional_or_construction(code):
    if code in CONSTRUCTION_BLACKLIST:
        return True
    # 台股代碼前兩碼屬於傳統產業（水泥11、食品12、塑膠13、紡織14、造紙19、鋼鐵20、橡膠21、玻璃陶瓷18、營建25、航運26、觀光27、百貨29）
    if len(code) == 4 and code[:2] in ["11", "12", "13", "14", "18", "19", "20", "21", "25", "26", "27", "29"]:
        return True
    return False

# ==========================================
# 模組 5：多週期 KDJ ＋ 威爾德 DMI(14) 雙指標共振演算核心
# ==========================================
def compute_kdj(highs, lows, closes, n=9):
    k_val, d_val = 50.0, 50.0
    k_prev, d_prev, j_prev = 50.0, 50.0, 50.0
    j_val = 50.0
    for i in range(len(closes)):
        k_prev, d_prev, j_prev = k_val, d_val, j_val
        start_idx = max(0, i - n + 1)
        hh = np.max(highs[start_idx:i+1])
        ll = np.min(lows[start_idx:i+1])
        rsv = ((closes[i] - ll) / (hh - ll) * 100.0) if (hh != ll) else 50.0
        k_val = (2.0 / 3.0) * k_val + (1.0 / 3.0) * rsv
        d_val = (2.0 / 3.0) * d_val + (1.0 / 3.0) * k_val
        j_val = 3.0 * k_val - 2.0 * d_val
    kd_cross_recent = (k_val >= d_val) and (k_prev <= d_prev + 3.0)
    return round(k_val, 1), round(d_val, 1), round(j_val, 1), round(j_prev, 1), kd_cross_recent

def compute_dmi(highs, lows, closes, n=14):
    if len(closes) < n + 5:
        return 25.0, 25.0, 20.0, 20.0, False

    h = pd.Series(highs, dtype=float)
    l = pd.Series(lows, dtype=float)
    c = pd.Series(closes, dtype=float)

    up_move = h.diff()
    down_move = -l.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr1 = h - l
    tr2 = (h - c.shift(1)).abs()
    tr3 = (l - c.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

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
    adx_prev = round(float(adx.iloc[-2]), 1) if len(adx) > 1 else adx_now

    dmi_bull_active = (pdi_now > mdi_now) and (adx_now > adx_prev) and ((pdi_prev <= mdi_prev + 4.0) or (pdi_now - mdi_now > pdi_prev - mdi_prev))
    return pdi_now, mdi_now, adx_now, adx_prev, bool(dmi_bull_active)

def compute_macd(closes):
    s = pd.Series(closes)
    ema12 = s.ewm(span=12, adjust=False).mean()
    ema26 = s.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    osc = dif - dea
    return dif.values, dea.values, osc.values

@st.cache_data(ttl=600, show_spinner=False)
def download_historical_klines(yf_tickers_tuple):
    try:
        return yf.download(list(yf_tickers_tuple), period="1y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=600, show_spinner=False)
def download_fleet_history():
    tickers = ["^TWII", "00631L.TW", "0050.TW"]
    try:
        return yf.download(tickers, period="2y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=120, show_spinner=False)
def download_fleet_60m():
    tickers = ["^TWII", "00631L.TW", "0050.TW"]
    try:
        return yf.download(tickers, period="2mo", interval="60m", progress=False, group_by="ticker", auto_adjust=False)
    except Exception:
        return pd.DataFrame()

def fetch_and_analyze_fleet(rt_quotes):
    price_dict, _, _, twii_official = fetch_official_market_data()
    data = download_fleet_history()
    data_60m = download_fleet_60m()
    today_str = get_tw_now().strftime("%Y-%m-%d")

    twii_now = twii_official["close"] if twii_official["close"] > 0 else 45082.0
    twii_chg_pct = twii_official["chg_pct"]
    twii_open = twii_now
    twii_high = twii_now
    twii_low = twii_now
    twii_prev_c = twii_now
    ma5 = round(twii_now, 1)
    ma10 = round(twii_now, 1)
    ma20 = round(twii_now * 1.05, 1)
    ma60 = round(twii_now * 1.03, 1)
    ma240 = round(twii_now * 0.85, 1)
    w_ma10 = round(twii_now * 1.04, 1)
    m_ma5 = round(twii_now * 1.01, 1)
    bb_upper = round(twii_now * 1.09, 1)
    bb_lower = round(twii_now * 0.98, 1)
    k9, d9, j9, j9_prev, kd_cross_d = 21.8, 21.3, 22.8, 21.1, True
    pdi_d, mdi_d, adx_d, adx_d_prev, dmi_bull_d = 14.6, 36.4, 28.9, 27.6, False
    macd_hist = -350.0
    date_str = today_str
    quote_src = "Yahoo Finance / 證交所"

    k60_k, k60_d, k60_j, k60_j_prev, k60_kd_cross = 25.5, 24.1, 28.3, 8.4, True
    k60_pdi, k60_mdi, k60_adx, k60_adx_prev, k60_dmi_bull = 17.1, 48.6, 28.7, 27.0, False
    k60_ma5, k60_ma20, k60_ma60 = twii_now, twii_now, twii_now
    k60_macd_osc = -20.0
    k60_is_red_bar = True

    p_2x = price_dict.get("00631L", {}).get("close", 33.91)
    p_2x_chg = price_dict.get("00631L", {}).get("chg_pct", 0.0)
    p_1x = price_dict.get("0050", {}).get("close", 104.18)
    p_1x_chg = price_dict.get("0050", {}).get("chg_pct", 0.0)

    try:
        if not data.empty:
            df_twii = data["^TWII"].dropna(subset=["Close"]).copy()
            if len(df_twii) >= 25:
                o_twii = df_twii["Open"].values.astype(float)
                c_twii = df_twii["Close"].values.astype(float)
                h_twii = df_twii["High"].values.astype(float)
                l_twii = df_twii["Low"].values.astype(float)
                last_k_date = df_twii.index[-1].strftime("%Y-%m-%d")

                if "t00" in rt_quotes and rt_quotes["t00"]["close"] > 0:
                    rt_t00 = rt_quotes["t00"]
                    if last_k_date == today_str:
                        o_twii[-1] = rt_t00["open"] if rt_t00.get("open", 0) > 0 else o_twii[-1]
                        c_twii[-1] = rt_t00["close"]
                        h_twii[-1] = max(h_twii[-1], rt_t00["high"])
                        l_twii[-1] = min(l_twii[-1], rt_t00["low"])
                    else:
                        o_twii = np.append(o_twii, rt_t00["open"] if rt_t00.get("open", 0) > 0 else rt_t00["close"])
                        c_twii = np.append(c_twii, rt_t00["close"])
                        h_twii = np.append(h_twii, rt_t00["high"])
                        l_twii = np.append(l_twii, rt_t00["low"])
                    twii_chg_pct = rt_t00["chg_pct"]
                    twii_prev_c = rt_t00.get("prev_close", float(c_twii[-2]))
                    date_str = f"{today_str} {rt_t00['time']}"
                    quote_src = f"🟢 證交所 MIS ＆ Yahoo v8 雙源即時對時 ({rt_t00['time']})"
                else:
                    twii_prev_c = float(c_twii[-2]) if len(c_twii) > 1 else float(c_twii[-1])
                    twii_chg_pct = round((float(c_twii[-1]) - twii_prev_c) / twii_prev_c * 100, 2)
                    date_str = last_k_date

                twii_open = round(float(o_twii[-1]), 1)
                twii_now = round(float(c_twii[-1]), 1)
                twii_high = round(float(h_twii[-1]), 1)
                twii_low = round(float(l_twii[-1]), 1)
                ma5 = round(float(np.mean(c_twii[-5:])), 1)
                ma10 = round(float(np.mean(c_twii[-10:])), 1)
                ma20 = round(float(np.mean(c_twii[-20:])), 1)
                ma60 = round(float(np.mean(c_twii[-60:])), 1) if len(c_twii) >= 60 else ma20
                ma240 = round(float(np.mean(c_twii[-240:])), 1) if len(c_twii) >= 240 else ma60
                std20 = float(np.std(c_twii[-20:]))
                bb_upper = round(ma20 + 2.0 * std20, 1)
                bb_lower = round(ma20 - 2.0 * std20, 1)

                k9, d9, j9, j9_prev, kd_cross_d = compute_kdj(h_twii[-60:], l_twii[-60:], c_twii[-60:])
                pdi_d, mdi_d, adx_d, adx_d_prev, dmi_bull_d = compute_dmi(h_twii[-90:], l_twii[-90:], c_twii[-90:])
                _, _, osc_twii = compute_macd(c_twii)
                macd_hist = round(float(osc_twii[-1]), 1)

                w_closes = df_twii["Close"].resample("W").last().dropna().values.astype(float)
                if len(w_closes) > 0:
                    w_closes[-1] = twii_now
                    w_ma10 = round(float(np.mean(w_closes[-10:])), 1) if len(w_closes) >= 10 else ma60
                m_closes = df_twii["Close"].resample("ME").last().dropna().values.astype(float)
                if len(m_closes) > 0:
                    m_closes[-1] = twii_now
                    m_ma5 = round(float(np.mean(m_closes[-5:])), 1) if len(m_closes) >= 5 else ma60

            df_2x = data["00631L.TW"].dropna(subset=["Close"])
            if "00631L" in rt_quotes and rt_quotes["00631L"]["close"] > 0:
                p_2x = rt_quotes["00631L"]["close"]
                p_2x_chg = rt_quotes["00631L"]["chg_pct"]
            elif len(df_2x) >= 2:
                p_2x = round(float(df_2x["Close"].values[-1]), 2)
                p_2x_prev = float(df_2x["Close"].values[-2])
                p_2x_chg = round((p_2x - p_2x_prev) / p_2x_prev * 100, 2)

            df_1x = data["0050.TW"].dropna(subset=["Close"])
            if "0050" in rt_quotes and rt_quotes["0050"]["close"] > 0:
                p_1x = rt_quotes["0050"]["close"]
                p_1x_chg = rt_quotes["0050"]["chg_pct"]
            elif len(df_1x) >= 2:
                p_1x = round(float(df_1x["Close"].values[-1]), 2)
                p_1x_prev = float(df_1x["Close"].values[-2])
                p_1x_chg = round((p_1x - p_1x_prev) / p_1x_prev * 100, 2)
    except Exception:
        pass

    try:
        if not data_60m.empty and "^TWII" in data_60m:
            df_60 = data_60m["^TWII"].dropna(subset=["Close"]).copy()
            if len(df_60) >= 30:
                c_60 = df_60["Close"].values.astype(float)
                o_60 = df_60["Open"].values.astype(float)
                h_60 = df_60["High"].values.astype(float)
                l_60 = df_60["Low"].values.astype(float)

                if abs(c_60[-1] - twii_now) > 1.0:
                    c_60[-1] = twii_now
                    h_60[-1] = max(h_60[-1], twii_now)
                    l_60[-1] = min(l_60[-1], twii_now)

                k60_ma5 = round(float(np.mean(c_60[-5:])), 1)
                k60_ma20 = round(float(np.mean(c_60[-20:])), 1)
                k60_ma60 = round(float(np.mean(c_60[-60:])), 1) if len(c_60) >= 60 else k60_ma20
                k60_k, k60_d, k60_j, k60_j_prev, k60_kd_cross = compute_kdj(h_60[-60:], l_60[-60:], c_60[-60:])
                k60_pdi, k60_mdi, k60_adx, k60_adx_prev, k60_dmi_bull = compute_dmi(h_60[-80:], l_60[-80:], c_60[-80:])
                _, _, osc_60 = compute_macd(c_60)
                k60_macd_osc = round(float(osc_60[-1]), 1)
                k60_is_red_bar = bool(c_60[-1] >= o_60[-1] and c_60[-1] >= c_60[-2])
    except Exception:
        pass

    bias_20 = round((twii_now - ma20) / max(1.0, ma20) * 100, 2)
    bias_60 = round((twii_now - ma60) / max(1.0, ma60) * 100, 2)

    return {
        "twii_open": twii_open, "twii_prev_c": round(twii_prev_c, 1),
        "twii_now": twii_now, "twii_chg_pct": twii_chg_pct,
        "twii_high": twii_high, "twii_low": twii_low,
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60, "ma240": ma240,
        "w_ma10": w_ma10, "m_ma5": m_ma5,
        "bb_upper": bb_upper, "bb_lower": bb_lower,
        "bias_20": bias_20, "bias_60": bias_60,
        "k9": k9, "d9": d9, "j9": j9, "j9_prev": j9_prev, "kd_cross_d": kd_cross_d,
        "pdi_d": pdi_d, "mdi_d": mdi_d, "adx_d": adx_d, "adx_d_prev": adx_d_prev, "dmi_bull_d": dmi_bull_d,
        "macd_hist": macd_hist,
        "k60_k": k60_k, "k60_d": k60_d, "k60_j": k60_j, "k60_j_prev": k60_j_prev,
        "k60_pdi": k60_pdi, "k60_mdi": k60_mdi, "k60_adx": k60_adx, "k60_adx_prev": k60_adx_prev,
        "k60_ma5": k60_ma5, "k60_ma20": k60_ma20, "k60_ma60": k60_ma60,
        "k60_macd_osc": k60_macd_osc, "k60_is_red_bar": k60_is_red_bar,
        "p_2x": p_2x, "p_2x_chg": p_2x_chg,
        "p_1x": p_1x, "p_1x_chg": p_1x_chg,
        "date_str": date_str, "quote_src": quote_src
    }

def scan_all_stocks():
    price_dict, tdcc_dict, rev_dict, _ = fetch_official_market_data()
    vol_mult, vol_note = get_volume_multiplier()
    today_str = get_tw_now().strftime("%Y-%m-%d")

    candidates = dict(CORE_WATCHLIST)

    # 軌道 A：全市場短線純技術帶量突破股 (不限產業、不看營收YoY，最多納 35 檔)
    short_added = 0
    for code, info in price_dict.items():
        if code not in candidates and len(code) == 4 and info["vol_lots"] >= 1800 and info["chg_pct"] >= 2.2 and info["close"] >= 20.0:
            candidates[code] = (info["name"], "⚡短線技術破繭", info["market"])
            short_added += 1
            if short_added >= 35:
                break

    # 軌道 B：全市場潛在「🔄 轉機題材股」或電子績優股 (排除營建一次性入帳失真股 YoY > 400%)
    turnaround_added = 0
    for code, info in price_dict.items():
        if code not in candidates and len(code) == 4 and (code not in CONSTRUCTION_BLACKLIST) and info["vol_lots"] >= 1000 and info["close"] >= 25.0:
            yoy_v = rev_dict.get(code, 0.0)
            tdcc_v = tdcc_dict.get(code, 0.0)
            if 25.0 <= yoy_v <= 400.0 and tdcc_v >= 45.0:
                if is_traditional_or_construction(code):
                    candidates[code] = (info["name"], "🔄傳產轉機題材", info["market"])
                else:
                    candidates[code] = (info["name"], "🔄電子轉機成長", info["market"])
                turnaround_added += 1
                if turnaround_added >= 25:
                    break

    mis_targets = [("00631L", "TW"), ("0050", "TW")] + [(c, m) for c, (_, _, m) in candidates.items()]
    rt_quotes = fetch_mis_realtime_batch(tuple(mis_targets))

    yf_map = {}
    for c, (_, _, m) in candidates.items():
        suffix = "TW" if m == "TW" else "TWO"
        yf_map[f"{c}.{suffix}"] = c

    raw_df = download_historical_klines(tuple(yf_map.keys()))

    list_tab2_launch = []
    list_tab2_pullback = []
    list_tab2_orbit = []
    list_tab3_core_bluechip = []
    list_tab3_turnaround_or_dip = []

    for yf_tkr, code in yf_map.items():
        try:
            if raw_df.empty or yf_tkr not in raw_df:
                continue
            df_s = raw_df[yf_tkr].dropna(subset=["Close"]).copy()
            if len(df_s) < 65:
                continue
            c = df_s["Close"].values.astype(float)
            h = df_s["High"].values.astype(float)
            l = df_s["Low"].values.astype(float)
            v = (df_s["Volume"].values.astype(float) / 1000.0)
            last_k_date = df_s.index[-1].strftime("%Y-%m-%d")

            name, sector, _ = candidates[code]

            if code in rt_quotes and rt_quotes[code]["close"] > 0:
                rt = rt_quotes[code]
                if last_k_date == today_str:
                    c[-1] = rt["close"]
                    h[-1] = max(h[-1], rt["high"])
                    l[-1] = min(l[-1], rt["low"])
                    if rt["vol_lots"] > 0:
                        v[-1] = float(rt["vol_lots"])
                else:
                    c = np.append(c, rt["close"])
                    h = np.append(h, rt["high"])
                    l = np.append(l, rt["low"])
                    v = np.append(v, float(rt["vol_lots"]) if rt["vol_lots"] > 0 else v[-1])

            curr_p = round(float(c[-1]), 2)
            prev_p = float(c[-2])
            chg_1d = round((curr_p - prev_p) / prev_p * 100, 2) if prev_p > 0 else 0.0
            chg_3d = round((curr_p - float(c[-4])) / float(c[-4]) * 100, 2) if len(c) >= 4 else chg_1d

            est_vol = int(round(v[-1] * vol_mult))
            ma5_vol = max(1.0, float(np.mean(v[-6:-1])))
            vol_ratio = round(est_vol / ma5_vol, 2)

            if est_vol < 500 and curr_p < 100.0 and (code not in CORE_WATCHLIST):
                continue
            liq_tag = " ⚠️量縮控盤" if (est_vol < 500 and curr_p >= 100.0) else ""

            ma5 = round(float(np.mean(c[-5:])), 2)
            ma10 = round(float(np.mean(c[-10:])), 2)
            ma20 = round(float(np.mean(c[-20:])), 2)
            ma60 = round(float(np.mean(c[-60:])), 2)
            ma120 = round(float(np.mean(c[-120:])), 2) if len(c) >= 120 else ma60
            ma240 = round(float(np.mean(c[-240:])), 2) if len(c) >= 240 else ma120

            std20 = float(np.std(c[-20:]))
            bb_up = round(ma20 + 2.0 * std20, 2)
            bb_bw = round((4.0 * std20 / ma20) * 100, 1) if ma20 > 0 else 99.0
            bias_ma20_pct = round((curr_p - ma20) / max(0.01, ma20) * 100, 1)

            prev_ma5 = float(np.mean(c[-6:-1]))
            prev_ma10 = float(np.mean(c[-11:-1]))
            prev_ma20 = float(np.mean(c[-21:-1]))
            ma_tangle_pct = round((max(prev_ma5, prev_ma10, prev_ma20) - min(prev_ma5, prev_ma10, prev_ma20)) / prev_ma20 * 100, 2)

            stk_k9, stk_d9, stk_j9, stk_j_prev, kd_cross_recent = compute_kdj(h[-60:], l[-60:], c[-60:])
            pdi, mdi, adx, adx_prev, dmi_bull_active = compute_dmi(h[-90:], l[-90:], c[-90:])
            adx_delta = round(adx - adx_prev, 1)
            adx_up = adx > adx_prev
            dmi_str = f"+DI:{pdi} > -DI:{mdi}" if pdi > mdi else f"+DI:{pdi} < -DI:{mdi}"
            adx_str = f"{adx} ({'↑轉強' if adx_up else '↓整理'}{adx_delta:+.1f})"

            kd_dmi_resonance = (stk_k9 >= stk_d9) and (pdi > mdi) and adx_up and (adx >= 18.0) and (kd_cross_recent or dmi_bull_active)

            _, _, osc = compute_macd(c)

            df_weekly = df_s["Close"].resample("W").last().dropna()
            w_c = df_weekly.values.astype(float)
            if len(w_c) > 0:
                w_c[-1] = curr_p
            w_ma10 = float(np.mean(w_c[-10:])) if len(w_c) >= 10 else float(np.mean(w_c))
            _, _, w_osc = compute_macd(w_c) if len(w_c) >= 26 else (None, None, np.array([0.0]))
            w_bull = (curr_p >= w_ma10) and (float(w_osc[-1]) >= -0.5 if len(w_osc) > 0 else True)

            prev_high_20d = round(float(np.max(h[-21:-1])), 2)
            raw_ceiling = max(bb_up, prev_high_20d)
            castle_ceiling = round(curr_p * 1.07, 2) if raw_ceiling <= curr_p else round(raw_ceiling, 2)
            castle_month = ma20
            support_cand = max(ma10, float(l[-1])) if curr_p >= ma10 else min(ma20, float(l[-1]))
            castle_iron = round(min(curr_p, support_cand), 2)
            castle_stop = round(min(ma60, float(np.min(l[-20:]))), 2)

            tdcc_val = tdcc_dict.get(code, None)
            tdcc_str = f"{tdcc_val:.1f}%" if tdcc_val is not None else "未揭露"
            yoy_val = rev_dict.get(code, None)
            yoy_str = f"{yoy_val:+.1f}%" if yoy_val is not None else "待公告"

            row_base = {
                "代碼": code,
                "股名": f"{name}{liq_tag}",
                "族群": sector,
                "現價": curr_p,
                "今日%": chg_1d,
                "3日%": chg_3d,
                "日J值": stk_j9,
                "DMI多空": dmi_str,
                "ADX強度": adx_str,
                "5MA量比": vol_ratio,
                "預估量(張)": est_vol,
                "天花板(反壓)": castle_ceiling,
                "月線承接": castle_month,
                "鋼鐵防線": castle_iron,
                "極限停損": castle_stop,
                "400張大戶%": tdcc_str,
                "月營收YoY": yoy_str
            }

            is_heavy_dump = (chg_1d <= -2.0 and vol_ratio >= 1.25) or (chg_1d <= -4.2)

            # ==========================================
            # ⚡ 分頁二：短線操作精選區（純看技術面與量能，不限營收 YoY）
            # ==========================================
            if curr_p >= ma20 and (curr_p >= ma60 or ma20 >= ma60 * 0.96) and (not is_heavy_dump):
                is_overheated = (chg_3d >= 10.5) or (stk_j9 >= 90.0 and not kd_dmi_resonance)
                fund_badge = "🔥技術+營收雙渦輪" if (yoy_val and 0 < yoy_val <= 400.0) else "⚡純技術動能"

                if (chg_1d >= 1.5 and vol_ratio >= 1.20 and kd_dmi_resonance and not is_overheated) or (chg_1d >= 2.0 and vol_ratio >= 1.35 and not is_overheated and pdi > mdi and adx >= 16.0):
                    score = 85 + int(min(12, vol_ratio * 2.5 + chg_1d))
                    if kd_dmi_resonance:
                        pattern = f"👑 KD×DMI雙金叉共振(ADX↑) [{fund_badge}]"
                        score = 100
                    elif ma_tangle_pct <= 3.5:
                        pattern = f"🌸 瑞昱/矽創型：均線糾結帶量突破 [{fund_badge}]"
                        score += 3
                    elif osc[-1] > osc[-2] and osc[-2] < 0:
                        pattern = f"💎 智原型：MACD綠柱收斂翻紅上攻 [{fund_badge}]"
                        score += 2
                    elif bb_bw <= 14.0 and curr_p >= ma20:
                        pattern = f"🔥 雙鴻/奇鋐型：布林壓縮旱地拔蔥 [{fund_badge}]"
                        score += 2
                    else:
                        pattern = f"🚀 嘉澤/信驊型：多頭帶量突破頸線 [{fund_badge}]"
                    r1 = {"實戰優先分": min(100, score), "名將戰法診斷": pattern, **row_base}
                    list_tab2_launch.append(r1)

                elif (-3.8 <= chg_1d <= 2.2) and (vol_ratio <= 0.90) and (curr_p >= ma20) and (stk_j9 <= 68.0) and (pdi >= mdi * 0.9):
                    score = 84 + int((1.0 - vol_ratio) * 15)
                    if pdi > mdi:
                        score += 3
                        pattern = f"⚡ 神準/台光電型：N字量縮守月線(DMI多方) [{fund_badge}]"
                    else:
                        pattern = f"⚡ 神準/台光電型：N字量縮守10/20MA(J冷卻) [{fund_badge}]"
                    r2 = {"蓄勢評分": min(99, score), "名將戰法診斷": pattern, **row_base}
                    list_tab2_pullback.append(r2)

                elif curr_p >= ma5 >= ma10 >= ma20 and chg_1d >= -2.0:
                    if chg_3d >= 10.5:
                        pattern = "🔥 3日急拉過熱(3日>=10.5%)：切勿追高・分批鎖利"
                    elif stk_j9 >= 90.0 and pdi > mdi and adx_up:
                        pattern = "🚀 KD鈍化惟DMI+ADX向上：主升段續抱・勿過早下車"
                    elif stk_j9 >= 90.0 or curr_p >= bb_up:
                        pattern = "⚠️ KDJ高檔鈍化警戒(J>=90)：動能高檔・宜分批鎖利"
                    else:
                        pattern = "🛡️ 台積/台達電型：沿5/10MA上升軌道穩步推升"
                    r3 = {"趨勢狀態": pattern, **row_base}
                    list_tab2_orbit.append(r3)

            # ==========================================
            # 🏛️ 分頁三：長期投資波段區
            # 表 3-A：純【👑 電子權值股 ＆ 🏦 金融官股/龍頭金控】（徹底排除一般傳產與營建！）
            # 表 3-B：【🔄 轉機題材特選股（明確標註🔄轉機股）】＆【💎 權值股回測長線支撐逢低佈局榜】
            # ==========================================
            is_blue_chip = (code in ELECTRONICS_BLUE_CHIPS) or (code in FINANCIAL_BLUE_CHIPS)
            is_fin = (code in FINANCIAL_BLUE_CHIPS)

            if is_blue_chip and (not is_heavy_dump):
                # 電子權值要求月營收 YoY > 0；金融金控重配息與獲利穩健（YoY >= -5% 即可）
                bc_rev_ok = (yoy_val is None) or (yoy_val >= 0.0) or (is_fin and yoy_val >= -5.0)
                bc_tdcc_ok = (tdcc_val is None) or (tdcc_val >= 42.0)

                # 1. 站穩月線或季線且 DMI 多方控盤之強勢領航權值股 -> 進入 表 3-A
                if bc_rev_ok and bc_tdcc_ok and (curr_p >= ma20 or (curr_p >= ma60 and pdi >= mdi * 0.95)) and (chg_3d < 9.5) and (stk_j9 <= 88.0):
                    l_score = 88
                    if is_fin:
                        l_score += 3
                    if yoy_val and yoy_val >= 15.0:
                        l_score += 4
                    if tdcc_val and tdcc_val >= 65.0:
                        l_score += 4
                    if pdi > mdi and adx_up:
                        l_score += 3

                    if kd_dmi_resonance:
                        bc_status = "👑 權值龍頭＋KD×DMI雙金叉共振起漲"
                    elif is_fin and curr_p >= ma20:
                        bc_status = "🏦 績優金控＋站穩月季線＋長線護城河"
                    elif vol_ratio <= 0.90 and curr_p >= ma20:
                        bc_status = "💎 權值龍頭＋大戶鎖碼＋量縮守月線甜美區"
                    else:
                        bc_status = "✅ 權值龍頭＋長線多頭趨勢穩健"

                    r_bc = {
                        "波段總分": min(100, l_score),
                        "長線格局定性": bc_status,
                        **row_base
                    }
                    list_tab3_core_bluechip.append(r_bc)

                # 2. 當大盤重挫時，隨大盤回測季線/半年線/年線且日J值超賣勾頭之優質權值股 -> 進入 表 3-B 逢低佈局區
                elif bc_rev_ok and (curr_p >= ma240 * 0.96) and (stk_j9 <= 35.0) and (stk_j9 >= stk_j_prev - 2.0):
                    dip_row = dict(row_base)
                    dip_row["長線支撐(季/年線)"] = f"季{ma60:.1f} / 年{ma240:.1f}"
                    r_dip = {
                        "波段總分": 90,
                        "類型與轉機標註": "💎【權值回測買點】隨大盤回測長線支撐＋J值低檔止穩",
                        **row_base
                    }
                    list_tab3_turnaround_or_dip.append(r_dip)

            # 3. 非權值股（或傳產股）若具備「低檔起漲＋營收YoY>=25%＋DMI多方控盤＋大戶>=48%」-> 明確標註為【🔄 轉機股】進入 表 3-B
            elif (not is_blue_chip) and (code not in CONSTRUCTION_BLACKLIST) and (not is_heavy_dump):
                is_turnaround = (
                    (yoy_val is not None and 25.0 <= yoy_val <= 400.0)
                    and (tdcc_val is not None and tdcc_val >= 48.0)
                    and (curr_p >= ma20 and ma20 >= ma60)
                    and (pdi > mdi)
                    and (stk_j9 <= 85.0)
                )
                if is_turnaround:
                    t_row = dict(row_base)
                    t_row["股名"] = f"🔄【轉機股】{name}{liq_tag}"
                    ind_type = "傳產景氣轉機" if is_traditional_or_construction(code) else "中小型成長轉機"
                    r_turn = {
                        "波段總分": 89 + (4 if kd_dmi_resonance else 0),
                        "類型與轉機標註": f"🔄【{ind_type}】營收爆發(YoY{yoy_str})＋DMI多頭翻揚",
                        **t_row
                    }
                    list_tab3_turnaround_or_dip.append(r_turn)

        except Exception:
            continue

    df_t1 = pd.DataFrame(list_tab2_launch).sort_values(by="實戰優先分", ascending=False) if list_tab2_launch else pd.DataFrame()
    df_t2 = pd.DataFrame(list_tab2_pullback).sort_values(by="蓄勢評分", ascending=False) if list_tab2_pullback else pd.DataFrame()
    df_t3 = pd.DataFrame(list_tab2_orbit).sort_values(by="3日%", ascending=False) if list_tab2_orbit else pd.DataFrame()
    df_long_bc = pd.DataFrame(list_tab3_core_bluechip).sort_values(by="波段總分", ascending=False) if list_tab3_core_bluechip else pd.DataFrame()
    df_long_turn = pd.DataFrame(list_tab3_turnaround_or_dip).sort_values(by="波段總分", ascending=False) if list_tab3_turnaround_or_dip else pd.DataFrame()

    return df_t1, df_t2, df_t3, df_long_bc, df_long_turn, vol_note, rt_quotes

# ==========================================
# 模組 6：Streamlit 戰情室主介面 (v37.0 台股紅漲綠跌 ＋ 持倉記憶鎖定 ＋ 權值/轉機分流版)
# ==========================================
now_str = get_tw_now().strftime("%Y-%m-%d %H:%M:%S")
st.markdown("## 🏛️ 台股每日戰情室 v37.0 (台股紅漲綠跌・持倉記憶鎖定・電子金融權值＆轉機股分流版)")

# 載入已記憶的母子艦隊持倉設定
saved_cfg = load_portfolio_config()

with st.expander("⚙️ 點此展開／調整【0050正2 ＋ 0050 母子艦隊持倉與成本參數】（支援記憶鎖定防 F5 刷新遺失）", expanded=False):
    c_in1, c_in2, c_in3, c_in4, c_in5 = st.columns(5)
    with c_in1:
        in_lots_2x = st.number_input("0050正2 持有張數", value=float(saved_cfg["lots_2x"]), step=0.1, format="%.2f")
    with c_in2:
        in_cost_2x = st.number_input("0050正2 平均成本(元)", value=float(saved_cfg["cost_2x"]), step=0.01, format="%.2f")
    with c_in3:
        in_lots_1x = st.number_input("0050 壓艙石張數", value=float(saved_cfg["lots_1x"]), step=0.1, format="%.2f")
    with c_in4:
        in_cost_1x = st.number_input("0050 平均成本(元)", value=float(saved_cfg["cost_1x"]), step=0.01, format="%.2f")
    with c_in5:
        in_cash_pool = st.number_input("已收割待買回現金池(元)", value=float(saved_cfg["cash_pool"]), step=1000.0, format="%.0f")

    save_col1, save_col2 = st.columns([2, 5])
    with save_col1:
        if st.button("💾 儲存並鎖定持倉設定 (防 F5 刷新遺失)", use_container_width=True, type="primary"):
            save_portfolio_config(in_lots_2x, in_cost_2x, in_lots_1x, in_cost_1x, in_cash_pool)
            st.success(f"✅ 已成功記憶持倉：{in_lots_2x:.2f}張正2(@{in_cost_2x:.2f}) ＋ {in_lots_1x:.2f}張0050(@{in_cost_1x:.2f})！現在按 F5 刷新也不會遺失。")
    with save_col2:
        st.caption("💡 提示：修改上方數字後，請點一下左側「💾 儲存並鎖定持倉設定」，系統會將參數寫入雲端設定檔與網址記憶中，按 F5 也不會重置。")

# 若使用者在輸入框調整了數值，自動同步更新記憶檔
if (
    abs(in_lots_2x - saved_cfg["lots_2x"]) > 0.001
    or abs(in_cost_2x - saved_cfg["cost_2x"]) > 0.001
    or abs(in_lots_1x - saved_cfg["lots_1x"]) > 0.001
    or abs(in_cost_1x - saved_cfg["cost_1x"]) > 0.001
    or abs(in_cash_pool - saved_cfg["cash_pool"]) > 0.5
):
    save_portfolio_config(in_lots_2x, in_cost_2x, in_lots_1x, in_cost_1x, in_cash_pool)

btn_col1, btn_col2, btn_col3 = st.columns([2, 2, 3])
with btn_col1:
    if st.button("⚡ 秒速刷新盤中即時報價 (無快取直連)", use_container_width=True):
        fetch_mis_realtime_batch.clear()
with btn_col2:
    if st.button("🔄 完整重掃全市場與 60分/日K線", use_container_width=True):
        st.cache_data.clear()
with btn_col3:
    auto_refresh = st.checkbox("⏱️ 開啟盤中每 20 秒自動刷新即時報價", value=False)

with st.spinner("📡 正在同步證交所 MIS ＆ Yahoo v8 即時行情、執行【短線純技術 vs 長線電子金融權值/轉機股】掃描..."):
    df_launch, df_pullback, df_orbit, df_long_bc, df_long_turn, vol_note, rt_quotes = scan_all_stocks()
    fleet = fetch_and_analyze_fleet(rt_quotes)

st.caption(f"🕒 台灣時間：{now_str} ｜ 報價引擎：{fleet['quote_src']} ｜ 量能狀態：{vol_note} ｜ 🎨 配色：台股紅漲綠跌")

twii_open = fleet["twii_open"]
twii_prev_c = fleet["twii_prev_c"]
twii_now = fleet["twii_now"]
p_2x = fleet["p_2x"]
p_1x = fleet["p_1x"]

val_2x = int(round(in_lots_2x * 1000 * p_2x))
cost_total_2x = int(round(in_lots_2x * 1000 * in_cost_2x))
pnl_2x = val_2x - cost_total_2x
pnl_2x_pct = round(((p_2x - in_cost_2x) / max(0.01, in_cost_2x)) * 100, 2)

val_1x = int(round(in_lots_1x * 1000 * p_1x))
cost_total_1x = int(round(in_lots_1x * 1000 * in_cost_1x))
pnl_1x = val_1x - cost_total_1x
pnl_1x_pct = round(((p_1x - in_cost_1x) / max(0.01, in_cost_1x)) * 100, 2)

total_cost_all = cost_total_2x + cost_total_1x
total_fleet_val = val_2x + val_1x + int(in_cash_pool)
total_pnl = pnl_2x + pnl_1x
total_pnl_pct = round((total_pnl / max(1, total_cost_all)) * 100, 2)
ratio_2x_pct = round((val_2x / max(1, total_fleet_val)) * 100, 1)
ratio_1x_pct = round((val_1x / max(1, total_fleet_val)) * 100, 1)
effective_leverage = round(((val_1x * 1.0 + val_2x * 2.0) / max(1, total_fleet_val)) * 100, 1)

def calc_target_2x(target_twii):
    pct_diff = (target_twii - twii_now) / max(1.0, twii_now)
    return round(p_2x * (1.0 + pct_diff * 2.0), 2)

def calc_target_1x(target_twii):
    pct_diff = (target_twii - twii_now) / max(1.0, twii_now)
    return round(p_1x * (1.0 + pct_diff * 1.0), 2)

twii_harvest_zone = round(min(fleet["bb_upper"], fleet["ma20"] * 1.035), 1)
twii_ma20 = fleet["ma20"]
twii_ma60_buffer = round(fleet["ma60"] * 1.016, 1)
twii_ma60 = fleet["ma60"]
twii_m_ma5 = fleet["m_ma5"]
twii_w_ma10 = fleet["w_ma10"]
twii_ma240 = fleet["ma240"]

t_harvest = calc_target_2x(twii_harvest_zone)
t_ma20 = calc_target_2x(twii_ma20)
t_w_ma10 = calc_target_2x(twii_w_ma10)
t_ma60_buf = calc_target_2x(twii_ma60_buffer)
t_ma60 = calc_target_2x(twii_ma60)
t_m_ma5 = calc_target_2x(twii_m_ma5)
t_ma240 = calc_target_2x(twii_ma240)

t1x_harvest = calc_target_1x(twii_harvest_zone)
t1x_ma20 = calc_target_1x(twii_ma20)
t1x_w_ma10 = calc_target_1x(twii_w_ma10)

j_now = fleet["j9"]
j_prev = fleet["j9_prev"]
j_delta = round(j_now - j_prev, 1)
j_trend_str = f"↑勾頭 ({j_delta:+.1f})" if j_delta > 0 else f"↓下彎 ({j_delta:+.1f})"

pdi_d = fleet["pdi_d"]
mdi_d = fleet["mdi_d"]
adx_d = fleet["adx_d"]
adx_d_delta = round(adx_d - fleet["adx_d_prev"], 1)
dmi_d_str = f"+DI={pdi_d} / -DI={mdi_d} / ADX={adx_d}({'↑' if adx_d_delta > 0 else '↓'}{adx_d_delta:+.1f})"

k60_k = fleet["k60_k"]
k60_d = fleet["k60_d"]
k60_j = fleet["k60_j"]
k60_j_prev = fleet["k60_j_prev"]
k60_j_delta = round(k60_j - k60_j_prev, 1)
k60_j_trend = f"↑勾頭 ({k60_j_delta:+.1f})" if k60_j_delta > 0 else f"↓下彎 ({k60_j_delta:+.1f})"

k60_pdi = fleet["k60_pdi"]
k60_mdi = fleet["k60_mdi"]
k60_adx = fleet["k60_adx"]
k60_adx_delta = round(k60_adx - fleet["k60_adx_prev"], 1)
dmi_60_str = f"+DI={k60_pdi} / -DI={k60_mdi} / ADX={k60_adx}({'↑' if k60_adx_delta > 0 else '↓'})"

k60_bottom_turned_up = (k60_j_delta > 0 and (twii_now >= fleet["k60_ma5"] or fleet["k60_is_red_bar"])) or (k60_k >= k60_d and k60_j_delta >= 0)
k60_overheated_peaking = (k60_j >= 75.0 and k60_j_delta < 0) and (twii_now >= twii_w_ma10 * 0.993)

def build_ai_snapshot_text():
    lines = [
        f"【台股每日戰情室 v37.0 雙軌分流數據包】({now_str})",
        f"1. 大盤即時報價與位階：現價 {twii_now:,.1f}點 ({fleet['twii_chg_pct']:+.2f}% | 開盤 {twii_open:,.1f} / 最高 {fleet['twii_high']:,.1f} / 最低 {fleet['twii_low']:,.1f} / 昨收 {twii_prev_c:,.1f}) | 日20MA月線 {twii_ma20:,.1f} ({fleet['bias_20']:+.2f}%) | 日60MA季線 {twii_ma60:,.1f} ({fleet['bias_60']:+.2f}%) | 10週線 {twii_w_ma10:,.1f} | 5月線 {twii_m_ma5:,.1f}",
        f"2. 大盤日K與60分K (KDJ + DMI)：『日K』K={fleet['k9']}/D={fleet['d9']}/J={j_now}({j_trend_str}), DMI({dmi_d_str}) ｜ 『60分K』60m_K={k60_k}/60m_D={k60_d}/60m_J={k60_j}({k60_j_trend}), 60m_DMI({dmi_60_str}), 60分5MA={fleet['k60_ma5']:,.1f}",
        f"3. 母子艦隊現況(已記憶鎖定)：0050正2(00631L) 現價 {p_2x:.2f}元 ({fleet['p_2x_chg']:+.2f}%), 持有 {in_lots_2x:.2f}張(成本{in_cost_2x:.2f}), 市值 {val_2x:,}元(損益 {pnl_2x:+,}元/{pnl_2x_pct:+.2f}%) | 0050 現價 {p_1x:.2f}元 ({fleet['p_1x_chg']:+.2f}%), 持有 {in_lots_1x:.2f}張(成本{in_cost_1x:.2f}), 市值 {val_1x:,}元(損益 {pnl_1x:+,}元/{pnl_1x_pct:+.2f}%) | 總市值 {total_fleet_val:,}元 (總損益 {total_pnl:+,}元/{total_pnl_pct:+.2f}%), 有效曝險 {effective_leverage}%",
        f"4. 0050正2 對應目標價：高檔收割區={t_harvest:.2f}元 | 日20MA月線={t_ma20:.2f}元 | 10週線={t_w_ma10:.2f}元 | 日60MA季線={t_ma60:.2f}元 | 5月線={t_m_ma5:.2f}元"
    ]
    if not df_launch.empty:
        top_l = [f"{r['代碼']}{r['股名']}(現價{r['現價']}/今日{r['今日%']:+}%/量比{r['5MA量比']}/J值{r['日J值']}/{r['DMI多空']}/ADX{r['ADX強度']})" for _, r in df_launch.head(8).iterrows()]
        lines.append("5. 表一(短線純技術點火榜)：" + "；".join(top_l))
    if not df_pullback.empty:
        top_p = [f"{r['代碼']}{r['股名']}(現價{r['現價']}/今日{r['今日%']:+}%/量比{r['5MA量比']}/J值{r['日J值']}/{r['DMI多空']}/月線{r['月線承接']})" for _, r in df_pullback.head(8).iterrows()]
        lines.append("6. 表二(短線純技術N字回測榜)：" + "；".join(top_p))
    if not df_long_bc.empty:
        top_g = [f"{r['代碼']}{r['股名']}({r['族群']}/現價{r['現價']}/今日{r['今日%']:+}%/{r['DMI多空']}/大戶{r['400張大戶%']}/YoY{r['月營收YoY']})" for _, r in df_long_bc.head(8).iterrows()]
        lines.append("7. 分頁三表A(電子與金融權值核心波段榜)：" + "；".join(top_g))
    if not df_long_turn.empty:
        top_t = [f"{r['代碼']}{r['股名']}({r['類型與轉機標註']}/現價{r['現價']}/今日{r['今日%']:+}%/YoY{r['月營收YoY']})" for _, r in df_long_turn.head(6).iterrows()]
        lines.append("8. 分頁三表B(轉機股與權值回測買點榜)：" + "；".join(top_t))
    return "\n".join(lines)

with st.expander("🤖 點此展開【Gemini AI 專屬戰情數據包】（點擊右上角 📋 圖示即可一鍵複製貼給 Gemini 分析，免截圖！）", expanded=False):
    st.code(build_ai_snapshot_text(), language="markdown")

tab1, tab2, tab3 = st.tabs([
    "📊 0050正2 ＋ 0050 母子艦隊戰情室 (紅漲綠跌・KDJ＋DMI)",
    "⚡ 短線操作精選區 (純看技術面與量能・KD×DMI 共振)",
    "🏛️ 長期投資波段區 (👑電子權值 ＋ 🏦金融金控 ＋ 🔄轉機股標註)"
])

with tab1:
    col1, col2, col3, col4 = st.columns(4)

    # 全面採用 delta_color="inverse" 且 delta 以純 + / - 開頭，確保台股「紅漲(↑) / 綠跌(↓)」100% 精準呈現！
    with col1:
        st.markdown("#### 🏛️ 加權指數 (日K＋60分K)")
        st.metric(
            label=f"台股大盤 ({fleet['date_str']})",
            value=f"{twii_now:,.1f} 點",
            delta=f"{fleet['twii_chg_pct']:+.2f}% (開盤 {twii_open:,.0f} | 高低 {fleet['twii_low']:,.0f}~{fleet['twii_high']:,.0f})",
            delta_color="inverse"
        )
        w_status = "✅守穩" if twii_now >= twii_w_ma10 else "⚠️跌破"
        m_status = "✅有守" if twii_now >= twii_m_ma5 else "❌失守"
        st.caption(
            f"• 開盤/昨收：**開盤 `{twii_open:,.1f}`** ｜ 昨收 `{twii_prev_c:,.1f}`\n\n"
            f"• 日均線：月線 `{twii_ma20:,.0f}` (`{fleet['bias_20']:+.1f}%`) ｜ 季線 `{twii_ma60:,.0f}` (`{fleet['bias_60']:+.2f}%`)\n\n"
            f"• 長線傘：10週線 `{twii_w_ma10:,.0f}` ({w_status}) ｜ **5月線 `{twii_m_ma5:,.0f}` ({m_status})**\n\n"
            f"• **日KDJ ＆ DMI**：`K={fleet['k9']}/D={fleet['d9']}/J={j_now}` ({j_trend_str}) ｜ `{dmi_d_str}`\n\n"
            f"• **60分KDJ ＆ DMI**：`K={k60_k}/D={k60_d}/J={k60_j}` ({k60_j_trend}) ｜ `{dmi_60_str}`"
        )

    with col2:
        st.markdown("#### 🚀 0050正2")
        st.metric(
            label=f"00631L 子艦隊 (今日 {fleet['p_2x_chg']:+.2f}%)",
            value=f"{p_2x:,.2f} 元",
            delta=f"{pnl_2x:+,} 元 ({pnl_2x_pct:+.2f}%)",
            delta_color="inverse"
        )
        st.caption(
            f"• 持有部位：**`{in_lots_2x:.2f} 張`** (成本 **`{in_cost_2x:.2f} 元`**)\n\n"
            f"• 部位市值：`{val_2x:,} 元` (佔比 `{ratio_2x_pct}%`)\n\n"
            f"• 反彈目標：5月線 `{t_m_ma5:.2f}元` / 季線 `{t_ma60:.2f}元` / 月線 `{t_ma20:.2f}元`"
        )

    with col3:
        st.markdown("#### 🛡️ 0050")
        st.metric(
            label=f"0050 壓艙石 (今日 {fleet['p_1x_chg']:+.2f}%)",
            value=f"{p_1x:,.2f} 元",
            delta=f"{pnl_1x:+,} 元 ({pnl_1x_pct:+.2f}%)",
            delta_color="inverse"
        )
        st.caption(
            f"• 持有部位：**`{in_lots_1x:.2f} 張`** (成本 **`{in_cost_1x:.2f} 元`**)\n\n"
            f"• 部位市值：`{val_1x:,} 元` (佔比 `{ratio_1x_pct}%`)\n\n"
            f"• 角色定錨：`長線底倉・領息與換檔火種`"
        )

    with col4:
        st.markdown("#### 💰 總市值")
        st.metric(
            label=f"母子艦隊總資產 (總成本 {total_cost_all:,} 元)",
            value=f"{total_fleet_val:,} 元",
            delta=f"{total_pnl:+,} 元 ({total_pnl_pct:+.2f}%)",
            delta_color="inverse"
        )
        st.caption(
            f"• 整體有效曝險：**`{effective_leverage}%`**\n\n"
            f"• 待買回現金池：`{int(in_cash_pool):,} 元`\n\n"
            f"• 艦隊結構：`正2 ({ratio_2x_pct}%) ＋ 0050 ({ratio_1x_pct}%)`"
        )

    st.markdown("---")

    net_cash_harvest = int(round(t_harvest * 1000 * (1.0 - 0.002425)))
    shares_at_ma60 = int(net_cash_harvest // max(1.0, t_ma60))
    shares_at_m5 = int(net_cash_harvest // max(1.0, t_m_ma5))
    shares_at_ma240 = int(net_cash_harvest // max(1.0, t_ma240))

    half_1x_lots = round(min(0.5, in_lots_1x), 2)
    half_1x_shares = int(round(half_1x_lots * 1000))
    half_1x_net_now = int(round(half_1x_shares * p_1x * (1.0 - 0.002425)))
    buy_2x_shares_now = int(half_1x_net_now // max(1.0, p_2x * 1.001425))
    rebound_to_ma60_net = int(round(buy_2x_shares_now * t_ma60 * (1.0 - 0.002425))) - int(round(half_1x_shares * calc_target_1x(twii_ma60) * 1.001425))
    rebound_to_ma20_net = int(round(buy_2x_shares_now * t_ma20 * (1.0 - 0.002425))) - int(round(half_1x_shares * t1x_ma20 * 1.001425))

    # ==========================================
    # 嚴格校正後之「日K位階 × 60分K (KDJ+DMI)」實戰決策燈號
    # 徹底修正「跌破季線與5月線卻誤判為站上季線」之 Bug！
    # ==========================================
    above_ma60 = twii_now >= twii_ma60
    above_m5 = twii_now >= twii_m_ma5
    k60_dmi_is_bull = k60_pdi > k60_mdi

    if twii_now >= twii_harvest_zone or (j_now >= 90.0 and k60_overheated_peaking):
        st.warning(
            f"🔥 **【雙週期高檔收割警報】大盤攻抵高檔收割圈 ({twii_harvest_zone:,.1f} 點) 且日J={j_now}、60分J={k60_j} 彎頭轉折！**\n\n"
            f"• **實戰行動**：逢高賣出 **第 1 張機動 0050正2**（約 **{t_harvest:.2f} 元**），淨回收約 **{net_cash_harvest:,} 元** 入池或轉買 `{int(net_cash_harvest // max(1.0, t1x_harvest))} 股 0050`。"
        )
    elif above_ma60 and k60_bottom_turned_up and k60_dmi_is_bull:
        st.success(
            f"🟢 **【日K守穩季線 (`{twii_ma60:,.0f}`) ＋ 60分K (KDJ×DMI) 真多頭共振推進！】**\n\n"
            f"• **雙週期確認**：大盤穩守季線 (`{twii_ma60:,.0f}`) 與 5月線 (`{twii_m_ma5:,.0f}`) 之上，且 60分KDJ (`J={k60_j}`) 與 60分DMI (`+DI={k60_pdi} > -DI={k60_mdi}`) 同步多頭控盤！\n"
            f"• **實戰行動**：手頭 `{in_lots_2x:.2f}張正2 ＋ {in_lots_1x:.2f}張0050` 安心續抱！反彈目標依序看 **10週線 (`{twii_w_ma10:,.0f}點` / 正2 `{t_w_ma10:.2f}元`)** 與 **日20MA月線 (`{twii_ma20:,.0f}點` / 正2 `{t_ma20:.2f}元`)**。"
        )
    elif (not above_ma60) and above_m5:
        st.warning(
            f"🟡 **【日K跌破季線 (`{twii_ma60:,.0f}`)・退守最後長線防線 5月均線 (`{twii_m_ma5:,.0f}`) 決戰區！】**\n\n"
            f"• **雙週期診斷**：大盤目前已跌破季線 (`{fleet['bias_60']:+.2f}%`)，正守在 **5月均線 (`{twii_m_ma5:,.0f} 點`)** 之上；60分KDJ `J={k60_j} ({k60_j_trend})` ｜ 60分DMI `{dmi_60_str}`。\n"
            f"• **實戰行動**：若 60分KDJ 金叉勾頭且守穩 5月線，反彈第一道壓力先看 **季線反壓 (`{twii_ma60:,.0f}點` / 正2約 `{t_ma60:.2f}元`)**；在未重新站回季線且 60分DMI `+DI` 未超越 `-DI` 前，切勿過度擴大槓桿！"
        )
    else:
        # 大盤同時跌破季線 (46,569) 與 5月均線 (45,496)，如 11/02 現價 45,082 點之實況
        if k60_bottom_turned_up:
            st.warning(
                f"🟠 **【破線後之 60分K 跌深反彈警報】大盤目前位於季線 (`{twii_ma60:,.0f}`) 與 5月線 (`{twii_m_ma5:,.0f}`) 之下 (`{twii_now:,.1f} 點`)！**\n\n"
                f"• **真實位階診斷（重要修正）**：\n"
                f"  1. **【日/月K位階】**：大盤目前已跌破 **日60MA季線 (`{twii_ma60:,.0f} 點`，乖離 `{fleet['bias_60']:+.2f}%`)** 與 **5月均線 (`{twii_m_ma5:,.0f} 點`，跌破 `{twii_now - twii_m_ma5:+.0f} 點`)**；日DMI 為 **`+DI={pdi_d} < -DI={mdi_d}`（空方控盤）**。\n"
                f"  2. **【60分K微觀動能】**：雖然日KDJ (`J={j_now} {j_trend_str}`) 與 **60分KDJ (`K={k60_k}/D={k60_d}/J={k60_j} {k60_j_trend}`) 在低檔出現止跌金叉反彈**，但 **60分DMI (`+DI={k60_pdi} < -DI={k60_mdi}` / `ADX={k60_adx}`)** 顯示中線空方壓力仍重，屬於**「跌破支撐後之跌深技術性反彈」**！\n\n"
                f"• **母子艦隊 (`{in_lots_2x:.2f}張正2 ＋ {in_lots_1x:.2f}張0050`) 實戰指令**：\n"
                f"  - **反彈壓力觀察**：短線隨 60分K 金叉反彈時，第一道蓋頭反壓在 **5月均線 `{twii_m_ma5:,.0f} 點`（正2約 `{t_m_ma5:.2f} 元`）**，第二道重壓在 **日60MA季線 `{twii_ma60:,.0f} 點`（正2約 `{t_ma60:.2f} 元`，接近您的正2成本 `{in_cost_2x:.2f} 元`）**！\n"
                f"  - **減壓防守原則**：您目前正2 持有 `{in_lots_2x:.2f} 張`（整體曝險 `{effective_leverage}%` 偏高），當大盤反彈測試 **5月線~季線 (`{t_m_ma5:.2f} ~ {t_ma60:.2f} 元`)** 若遇壓導致 60分J 再度彎頭死叉，建議逢反彈將 **1~1.5 張正2 減碼換回 0050 或現金**，把曝險降回 `135%~150%` 安全水位！"
            )
        else:
            st.error(
                f"❄️ **【長線防線失守・空方控盤警戒】大盤跌破季線 (`{twii_ma60:,.0f}`) 與 5月均線 (`{twii_m_ma5:,.0f}`)，且 60分K 仍在下探 (`60分J={k60_j}`)！**\n\n"
                f"• **實戰行動**：日DMI (`+DI={pdi_d} < -DI={mdi_d}`) 與 60分DMI (`+DI={k60_pdi} < -DI={k60_mdi}`) 雙雙空方控盤，嚴禁在 60分K 止跌前摸底加碼正2！若連續 3 日站不回 5月線 `{twii_m_ma5:,.0f} 點`，應趁盤中反彈將正2 降檔轉入 0050 防守。"
            )

    st.markdown("#### 🎯 表 A：【日K定戰區 ＋ 60分K (KDJ×DMI) 找買賣點】五階雙週期共振精算表")
    df_t50_levels = pd.DataFrame([
        {
            "雙週期實戰錨點": "🔥 階段1：高檔波段收割區 (日布林上軌前哨)",
            "日K戰區點位": f"{twii_harvest_zone:,.1f} 點",
            "60分K 扣扳機條件": "日J>90 且 60分KDJ 高檔死叉跌破 60分5MA",
            "0050正2 目標價": f"{t_harvest:,.2f} 元",
            "實戰指令與滾股試算": f"賣出第1張正2 ➔ 淨回收 {net_cash_harvest:,} 元 (或換 {int(net_cash_harvest // max(1.0, t1x_harvest))}股 0050)"
        },
        {
            "雙週期實戰錨點": "⚡ 階段2：波段反彈壓力區 (10週線 / 日20MA月線)",
            "日K戰區點位": f"{twii_w_ma10:,.0f} ~ {twii_ma20:,.0f} 點",
            "60分K 扣扳機條件": f"觸及 10週線({twii_w_ma10:,.0f}) 或 日20MA({twii_ma20:,.0f}) 且 60分J 向下彎頭",
            "0050正2 目標價": f"{t_w_ma10:,.2f} ~ {t_ma20:,.2f} 元",
            "實戰指令與滾股試算": f"低檔轉攻之正2於此換回0050 (淨賺 +{rebound_to_ma20_net:,}元)"
        },
        {
            "雙週期實戰錨點": "🎯 階段3：季線多空分水嶺 (日60MA季線)",
            "日K戰區點位": f"{twii_ma60:,.1f} 點 (目前乖離 {fleet['bias_60']:+.2f}%)",
            "60分K 扣扳機條件": "跌破時反彈遇壓先減碼 ｜ 重新站回且 60分DMI(+DI>-DI) 才轉攻",
            "0050正2 目標價": f"{t_ma60:,.2f} 元 (近您成本 {in_cost_2x:.2f}元)",
            "實戰指令與滾股試算": f"目前季線轉為反壓！反彈至 {t_ma60:.2f}元 若不過宜先減碼降槓桿"
        },
        {
            "雙週期實戰錨點": "🛡️ 階段4：長線多頭最後防線 (月K 5月均線)",
            "日K戰區點位": f"{twii_m_ma5:,.1f} 點 (距今 {twii_now - twii_m_ma5:+.0f}點)",
            "60分K 扣扳機條件": "觀察3日內能否憑 60分K 金叉迅速站回 5月線之上",
            "0050正2 目標價": f"{t_m_ma5:,.2f} 元",
            "實戰指令與滾股試算": f"模組3長線保護傘底線！站回有守續抱，3日站不回則降檔防守"
        },
        {
            "雙週期實戰錨點": "💎 階段5：歷史級國安大底 (日240MA年線)",
            "日K戰區點位": f"{twii_ma240:,.1f} 點",
            "60分K 扣扳機條件": "若跌破5月線滿3日先降載；待年線日K與60分K雙底背離重裝",
            "0050正2 目標價": f"{t_ma240:,.2f} 元",
            "實戰指令與滾股試算": f"高檔賣1張於此買回 ➔ 變 {shares_at_ma240:,} 股 (+{shares_at_ma240 - 1000}股)"
        }
    ])
    st.dataframe(df_t50_levels, use_container_width=True, hide_index=True)

    st.markdown(f"#### 🔄 表 B：0050 ⇄ 0050正2「日K × 60分K (KDJ＋DMI) 四段式換檔」實戰精算表（依您目前 `{in_lots_2x:.2f}張正2 ＋ {in_lots_1x:.2f}張0050` 動態精算）")
    df_switch_modes = pd.DataFrame([
        {
            "艦隊攻守檔位": "🔥 檔位1：高檔減壓鎖利檔",
            "日K戰區 ＋ 60分K扳機條件": f"大盤攻抵 {twii_harvest_zone:,.0f} 點 且 60分J>=85 高檔彎頭死叉",
            "雙核轉換動作": f"賣 1張 正2 ({t_harvest:.2f}元) ➔ 轉買 {int(net_cash_harvest // max(1.0, t1x_harvest))}股 0050",
            "換檔後持倉佔比": "正2 降載 ＋ 0050 增持",
            "有效曝險": "約 135%",
            "實戰目的與防呆效益": "日K與60分K雙過熱鎖利，保留1倍底倉不踏空"
        },
        {
            "艦隊攻守檔位": "☀️ 檔位2：目前持倉巡航 ＆ 反彈遇壓歸位檔",
            "日K戰區 ＋ 60分K扳機條件": f"反彈碰 季線({twii_ma60:,.0f}) / 10週線({twii_w_ma10:,.0f}) 且 60分J向下彎頭",
            "雙核轉換動作": f"目前持有 {in_lots_2x:.2f}張正2＋{in_lots_1x:.2f}張0050 (反彈遇壓可將 1張正2 換回 0050 降壓)",
            "換檔後持倉佔比": f"正2 ({ratio_2x_pct}%) ＋ 0050 ({ratio_1x_pct}%)",
            "有效曝險": f"{effective_leverage}%",
            "實戰目的與防呆效益": f"目前曝險 {effective_leverage}% 較積極，反彈至季線 {t_ma60:.2f}元 遇壓可先減壓"
        },
        {
            "艦隊攻守檔位": "🎯 檔位3：重返5月線/季線 雙週期共振轉攻檔",
            "日K戰區 ＋ 60分K扳機條件": f"重新站回 5月線({twii_m_ma5:,.0f})~季線({twii_ma60:,.0f}) 且 60分KDJ金叉＋60分DMI(+DI>-DI)",
            "雙核轉換動作": f"賣 {half_1x_lots:.2f}張({half_1x_shares}股) 0050 ➔ 轉買 {buy_2x_shares_now:,}股 正2 (約 {p_2x:.2f}元)",
            "換檔後持倉佔比": "正2 攻擊極大化",
            "有效曝險": "約 180%",
            "實戰目的與防呆效益": f"必須確認站回防線且 60分DMI 多方奪權才啟動！彈回季線賺 +{rebound_to_ma60_net:,}元"
        },
        {
            "艦隊攻守檔位": "❄️ 檔位4：跌破季線與5月線 避險防守檔 (目前警戒區)",
            "日K戰區 ＋ 60分K扳機條件": f"跌破季線({twii_ma60:,.0f})與5月線({twii_m_ma5:,.0f})滿3日 且 60分K反彈遇壓死叉",
            "雙核轉換動作": "趁 60分K 反彈賣出 1.5~2張 正2 ➔ 轉買 0050 或保留現金",
            "換檔後持倉佔比": "正2 (約30%) ＋ 0050/現金 (約70%)",
            "有效曝險": "約 125%",
            "實戰目的與防呆效益": "當長線保護傘破裂且 DMI (-DI>+DI) 空方控盤時，趁反彈降槓桿保護本金"
        }
    ])
    st.dataframe(df_switch_modes, use_container_width=True, hide_index=True)

with tab2:
    st.caption("⚡ 【短線純技術動能模式（紅漲綠跌染色）】完全不看月營收 YoY！純粹鎖定「KD×DMI 雙金叉共振(ADX↑)、帶量突破、N字量縮回測」之強勢股。")
    
    st.markdown("#### 🔥 【表一：👑 KD×DMI 雙金叉共振(ADX↑) ＆ 階段 3(3) 旱地拔蔥純技術點火榜】")
    if not df_launch.empty:
        st.dataframe(style_tw_stock_df(df_launch), use_container_width=True, hide_index=True)
    else:
        st.info("今日盤面暫無符合「KD×DMI共振 或 帶量突破未過熱」之短線點火標的。")

    st.markdown("#### 🎯 【表二：N字洗盤蓄勢 ＆ 拉回月線/十日線低接榜 (純技術量縮守支撐・J<=68冷卻)】")
    if not df_pullback.empty:
        st.dataframe(style_tw_stock_df(df_pullback), use_container_width=True, hide_index=True)
    else:
        st.info("今日暫無符合「量縮回測月線有守 ＋ J<=68冷卻」之短線洗盤標的。")

    st.markdown("#### 🛡️ 【表三：上升軌道推升 ＆ KD鈍化(DMI+ADX續強) / 3日急拉警戒榜】")
    if not df_orbit.empty:
        st.dataframe(style_tw_stock_df(df_orbit), use_container_width=True, hide_index=True)
    else:
        st.info("今日暫無符合多頭軌道推升之標的。")

with tab3:
    st.markdown("#### 🏛️ 【表 3-A：👑 電子權值股 ＆ 🏦 金融官股/龍頭金控 長線多頭核心波段榜】")
    st.caption("篩選基準：100% 鎖定「電子權值龍頭」與「績優金融金控」（徹底剔除一般傳產與營建股）！要求站穩月/季線支撐、基本面穩健且短線未過熱。")
    if not df_long_bc.empty:
        st.dataframe(style_tw_stock_df(df_long_bc), use_container_width=True, hide_index=True)
    else:
        st.info("今日大盤回檔測試季線之下，暫無站上月線之電子/金融權值股；請參考下方【表 3-B】之權值股逢低佈局買點與轉機股！")

    st.markdown("#### 🔄 【表 3-B：🔄 轉機題材特選股（明確標註轉機股）＆ 💎 電子/金融權值股回測長線支撐買點榜】")
    st.caption("篩選基準：(1) 明確標註【🔄 轉機股】：具備營收爆發(YoY>=25%)＋大戶鎖碼(>=48%)＋DMI多頭翻揚之轉機題材股；(2) 【💎 權值回測買點】：隨大盤回測季線/年線且日J值低檔止穩之電子金融權值股。")
    if not df_long_turn.empty:
        st.dataframe(style_tw_stock_df(df_long_turn), use_container_width=True, hide_index=True)
    else:
        st.info("目前暫無符合條件之轉機題材股或權值回測買點。")

if auto_refresh:
    time.sleep(20)
    fetch_mis_realtime_batch.clear()
    st.rerun()
