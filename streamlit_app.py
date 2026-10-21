import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import datetime
import pytz
import requests
import urllib3
import io
import time

# 關閉 SSL 憑證警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(
    page_title="台股每日戰情室 v33.0",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ==========================================
# 模組 1：台灣時間定錨與 U 型微笑量能推估引擎 (早盤防暴衝校正版)
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
    # 針對台股早盤 09:00~09:45 成交量集中特性，平滑化推估倍率上限，避免早盤量比失真暴衝
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

# ==========================================
# 模組 2：證交所/櫃買中心「MIS 官方 5 秒即時報價引擎」
# ==========================================
def parse_mis_price(item):
    def to_f(val):
        try:
            s = str(val).replace(",", "").strip()
            return float(s) if s not in ["", "-", "--", "None"] else None
        except Exception:
            return None

    z_val = to_f(item.get("z"))
    if z_val and z_val > 0:
        return z_val

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

    y_val = to_f(item.get("y"))
    return y_val if (y_val and y_val > 0) else None

@st.cache_data(ttl=15, show_spinner=False)
def fetch_mis_realtime_batch(code_market_tuples):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
        "Referer": "https://mis.twse.com.tw/stock/index.jsp"
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
                    p_now = parse_mis_price(item)
                    if not c_code or p_now is None:
                        continue
                    try:
                        y_prev = float(str(item.get("y", p_now)).replace(",", ""))
                    except Exception:
                        y_prev = p_now
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
                        "close": round(p_now, 2),
                        "high": round(max(h_now, p_now), 2),
                        "low": round(min(l_now, p_now), 2),
                        "prev_close": y_prev,
                        "chg_pct": chg_pct,
                        "vol_lots": v_lots,
                        "time": str(item.get("t", "")),
                        "date": str(item.get("d", ""))
                    }
        except Exception:
            continue

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
# 模組 4：核心高動能科技與重電種子資料庫
# ==========================================
CORE_WATCHLIST = {
    "2330": ("台積電", "晶圓與封測", "TW"), "2454": ("聯發科", "IC設計與IP", "TW"),
    "2379": ("瑞昱", "IC設計與IP", "TW"), "3034": ("聯詠", "IC設計與IP", "TW"),
    "8016": ("矽創", "IC設計與IP", "TW"), "3035": ("智原", "IC設計與IP", "TW"),
    "3661": ("世芯-KY", "IC設計與IP", "TW"), "3443": ("創意", "IC設計與IP", "TW"),
    "3529": ("力旺", "IC設計與IP", "TWO"), "5274": ("信驊", "IC設計與IP", "TWO"),
    "5269": ("祥碩", "IC設計與IP", "TW"), "4966": ("譜瑞-KY", "IC設計與IP", "TWO"),
    "3533": ("嘉澤", "高速傳輸與連接器", "TW"), "3653": ("健策", "AI散熱與均熱片", "TW"),
    "3017": ("奇鋐", "AI散熱與均熱片", "TW"), "3324": ("雙鴻", "AI散熱與均熱片", "TWO"),
    "2382": ("廣達", "AI伺服器與組裝", "TW"), "3231": ("緯創", "AI伺服器與組裝", "TW"),
    "6669": ("緯穎", "AI伺服器與組裝", "TW"), "2317": ("鴻海", "AI伺服器與組裝", "TW"),
    "2308": ("台達電", "電源與儲能", "TW"), "3711": ("日月光投控", "晶圓與封測", "TW"),
    "2449": ("京元電子", "晶圓與封測", "TW"), "3264": ("欣銓", "晶圓與封測", "TWO"),
    "6187": ("萬潤", "CoWoS與設備", "TWO"), "3583": ("辛耘", "CoWoS與設備", "TW"),
    "3131": ("弘塑", "CoWoS與設備", "TWO"), "2059": ("川湖", "伺服器導軌與機殼", "TW"),
    "8210": ("勤誠", "伺服器導軌與機殼", "TW"), "2383": ("台光電", "PCB與CCL高頻材料", "TW"),
    "6274": ("台燿", "PCB與CCL高頻材料", "TWO"), "2368": ("金像電", "PCB與CCL高頻材料", "TW"),
    "3081": ("聯亞", "CPO與光通訊", "TWO"), "4979": ("華星光", "CPO與光通訊", "TWO"),
    "3163": ("波若威", "CPO與光通訊", "TWO"), "3363": ("上詮", "CPO與光通訊", "TWO"),
    "1513": ("中興電", "重電與綠能電纜", "TW"), "1519": ("華城", "重電與綠能電纜", "TW"),
    "1503": ("士電", "重電與綠能電纜", "TW"), "1514": ("亞力", "重電與綠能電纜", "TW"),
    "1609": ("大亞", "重電與綠能電纜", "TW"), "3558": ("神準", "網通與光通訊", "TWO"),
    "2345": ("智邦", "網通與光通訊", "TW"), "6285": ("啟碁", "網通與光通訊", "TW")
}

# ==========================================
# 模組 5：技術指標與 KDJ (含 J 值轉折斜率) 演算核心
# ==========================================
def compute_kdj(highs, lows, closes, n=9):
    """計算標準日 KDJ 指標：回傳當日 K9, D9, J9 以及前一日 J9_prev (用於判斷 J 值勾頭轉折)"""
    k_val, d_val = 50.0, 50.0
    j_prev = 50.0
    j_val = 50.0
    for i in range(len(closes)):
        j_prev = j_val
        start_idx = max(0, i - n + 1)
        hh = np.max(highs[start_idx:i+1])
        ll = np.min(lows[start_idx:i+1])
        rsv = ((closes[i] - ll) / (hh - ll) * 100.0) if (hh != ll) else 50.0
        k_val = (2.0 / 3.0) * k_val + (1.0 / 3.0) * rsv
        d_val = (2.0 / 3.0) * d_val + (1.0 / 3.0) * k_val
        j_val = 3.0 * k_val - 2.0 * d_val
    return round(k_val, 1), round(d_val, 1), round(j_val, 1), round(j_prev, 1)

def compute_macd(closes):
    s = pd.Series(closes)
    ema12 = s.ewm(span=12, adjust=False).mean()
    ema26 = s.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    osc = dif - dea
    return dif.values, dea.values, osc.values

@st.cache_data(ttl=900, show_spinner=False)
def download_historical_klines(yf_tickers_tuple):
    try:
        return yf.download(list(yf_tickers_tuple), period="1y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=900, show_spinner=False)
def download_fleet_history():
    tickers = ["^TWII", "00631L.TW", "0050.TW"]
    try:
        return yf.download(tickers, period="2y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
    except Exception:
        return pd.DataFrame()

def fetch_and_analyze_fleet(rt_quotes):
    price_dict, _, _, twii_official = fetch_official_market_data()
    data = download_fleet_history()
    today_str = get_tw_now().strftime("%Y-%m-%d")

    twii_now = twii_official["close"] if twii_official["close"] > 0 else 23000.0
    twii_chg_pct = twii_official["chg_pct"]
    twii_high = twii_now
    twii_low = twii_now
    ma20 = round(twii_now * 0.985, 1)
    ma60 = round(twii_now * 0.96, 1)
    ma240 = round(twii_now * 0.90, 1)
    bb_upper = round(twii_now * 1.03, 1)
    bb_lower = round(twii_now * 0.95, 1)
    k9, d9, j9, j9_prev, macd_hist = 50.0, 50.0, 50.0, 50.0, 0.0
    date_str = today_str
    quote_src = "Yahoo Finance / 證交所"

    p_2x = price_dict.get("00631L", {}).get("close", 37.00)
    p_2x_chg = price_dict.get("00631L", {}).get("chg_pct", 0.0)
    p_1x = price_dict.get("0050", {}).get("close", 108.93)
    p_1x_chg = price_dict.get("0050", {}).get("chg_pct", 0.0)

    try:
        if not data.empty:
            df_twii = data["^TWII"].dropna(subset=["Close"]).copy()
            if len(df_twii) >= 20:
                c_twii = df_twii["Close"].values.astype(float)
                h_twii = df_twii["High"].values.astype(float)
                l_twii = df_twii["Low"].values.astype(float)
                last_k_date = df_twii.index[-1].strftime("%Y-%m-%d")

                if "t00" in rt_quotes and rt_quotes["t00"]["close"] > 0:
                    rt_t00 = rt_quotes["t00"]
                    if last_k_date == today_str:
                        c_twii[-1] = rt_t00["close"]
                        h_twii[-1] = max(h_twii[-1], rt_t00["high"])
                        l_twii[-1] = min(l_twii[-1], rt_t00["low"])
                    else:
                        c_twii = np.append(c_twii, rt_t00["close"])
                        h_twii = np.append(h_twii, rt_t00["high"])
                        l_twii = np.append(l_twii, rt_t00["low"])
                    twii_chg_pct = rt_t00["chg_pct"]
                    date_str = f"{today_str} {rt_t00['time']}"
                    quote_src = f"🟢 證交所 MIS 即時直連 ({rt_t00['time']})"
                else:
                    twii_prev = float(c_twii[-2]) if len(c_twii) > 1 else float(c_twii[-1])
                    twii_chg_pct = round((float(c_twii[-1]) - twii_prev) / twii_prev * 100, 2)
                    date_str = last_k_date

                twii_now = round(float(c_twii[-1]), 1)
                twii_high = round(float(h_twii[-1]), 1)
                twii_low = round(float(l_twii[-1]), 1)
                ma20 = round(float(np.mean(c_twii[-20:])), 1)
                ma60 = round(float(np.mean(c_twii[-60:])), 1) if len(c_twii) >= 60 else ma20
                ma240 = round(float(np.mean(c_twii[-240:])), 1) if len(c_twii) >= 240 else ma60
                std20 = float(np.std(c_twii[-20:]))
                bb_upper = round(ma20 + 2.0 * std20, 1)
                bb_lower = round(ma20 - 2.0 * std20, 1)
                k9, d9, j9, j9_prev = compute_kdj(h_twii[-60:], l_twii[-60:], c_twii[-60:])
                _, _, osc_twii = compute_macd(c_twii)
                macd_hist = round(float(osc_twii[-1]), 1)

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

    bias_20 = round((twii_now - ma20) / max(1.0, ma20) * 100, 2)
    bias_60 = round((twii_now - ma60) / max(1.0, ma60) * 100, 2)

    return {
        "twii_now": twii_now, "twii_chg_pct": twii_chg_pct,
        "twii_high": twii_high, "twii_low": twii_low,
        "ma20": ma20, "ma60": ma60, "ma240": ma240,
        "bb_upper": bb_upper, "bb_lower": bb_lower,
        "bias_20": bias_20, "bias_60": bias_60,
        "k9": k9, "d9": d9, "j9": j9, "j9_prev": j9_prev,
        "macd_hist": macd_hist,
        "p_2x": p_2x, "p_2x_chg": p_2x_chg,
        "p_1x": p_1x, "p_1x_chg": p_1x_chg,
        "date_str": date_str, "quote_src": quote_src
    }

def scan_all_stocks():
    price_dict, tdcc_dict, rev_dict, _ = fetch_official_market_data()
    vol_mult, vol_note = get_volume_multiplier()
    today_str = get_tw_now().strftime("%Y-%m-%d")

    candidates = dict(CORE_WATCHLIST)
    for code, info in price_dict.items():
        if code not in candidates and len(code) == 4 and info["vol_lots"] >= 2500 and info["chg_pct"] >= 3.0 and info["close"] >= 25.0:
            candidates[code] = (info["name"], "全市場動態破繭", info["market"])
            if len(candidates) >= 75:
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
    list_tab3_long = []

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

            if est_vol < 500 and curr_p < 100.0:
                continue
            liq_tag = " ⚠️量縮控盤" if (est_vol < 500 and curr_p >= 100.0) else ""

            ma5 = round(float(np.mean(c[-5:])), 2)
            ma10 = round(float(np.mean(c[-10:])), 2)
            ma20 = round(float(np.mean(c[-20:])), 2)
            ma60 = round(float(np.mean(c[-60:])), 2)
            ma120 = round(float(np.mean(c[-120:])), 2) if len(c) >= 120 else ma60

            std20 = float(np.std(c[-20:]))
            bb_up = round(ma20 + 2.0 * std20, 2)
            bb_bw = round((4.0 * std20 / ma20) * 100, 1) if ma20 > 0 else 99.0

            prev_ma5 = float(np.mean(c[-6:-1]))
            prev_ma10 = float(np.mean(c[-11:-1]))
            prev_ma20 = float(np.mean(c[-21:-1]))
            ma_tangle_pct = round((max(prev_ma5, prev_ma10, prev_ma20) - min(prev_ma5, prev_ma10, prev_ma20)) / prev_ma20 * 100, 2)

            stk_k9, stk_d9, stk_j9, _ = compute_kdj(h[-60:], l[-60:], c[-60:])
            _, _, osc = compute_macd(c)

            df_weekly = df_s["Close"].resample("W").last().dropna()
            w_c = df_weekly.values.astype(float)
            w_ma10 = float(np.mean(w_c[-10:])) if len(w_c) >= 10 else float(np.mean(w_c))
            _, _, w_osc = compute_macd(w_c) if len(w_c) >= 26 else (None, None, np.array([0.0]))
            w_bull = (curr_p >= w_ma10) and (float(w_osc[-1]) >= 0 if len(w_osc) > 0 else True)

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
                "5MA量比": vol_ratio,
                "預估量(張)": est_vol,
                "天花板(反壓)": castle_ceiling,
                "月線承接": castle_month,
                "鋼鐵防線": castle_iron,
                "極限停損": castle_stop,
                "400張大戶%": tdcc_str,
                "月營收YoY": yoy_str
            }

            # 模組 2 與模組 8 雙重濾網：
            # 1. 月營收 YoY 必須 > 0 (排除如祥碩 -30% 等基本面衰退股)
            rev_ok = (yoy_val is None) or (yoy_val > 0)
            # 2. 排除「價跌量增倒貨股」（今日下跌且量比 > 1.5，或單日跌幅 <= -2.2%）
            is_heavy_dump = (chg_1d < 0 and vol_ratio >= 1.5) or (chg_1d <= -2.2)

            if curr_p >= ma20 and ma20 >= ma60 * 0.98 and rev_ok and (not is_heavy_dump):
                if chg_1d >= 2.0 and vol_ratio >= 1.35:
                    score = 85 + int(min(15, vol_ratio * 2.5 + chg_1d))
                    if ma_tangle_pct <= 3.5:
                        pattern = "🌸 瑞昱/矽創型：均線糾結後帶量長紅突破"
                        score += 3
                    elif osc[-1] > osc[-2] and osc[-2] < 0:
                        pattern = "💎 智原型：MACD綠柱收斂翻紅帶量上攻"
                        score += 2
                    elif bb_bw <= 14.0 and curr_p >= ma20:
                        pattern = "🔥 雙鴻/奇鋐型：布林壓縮旱地拔蔥點火"
                        score += 2
                    else:
                        pattern = "🚀 嘉澤/信驊型：多頭帶量突破頸線表態"
                    r1 = {"實戰優先分": min(100, score), "名將戰法診斷": pattern, **row_base}
                    list_tab2_launch.append(r1)

                elif (-2.0 <= chg_1d <= 2.2) and (vol_ratio <= 0.85) and (curr_p >= ma20):
                    score = 82 + int((1.0 - vol_ratio) * 15)
                    pattern = "⚡ 神準/台光電型：N字量縮回測10/20MA有守"
                    r2 = {"蓄勢評分": min(98, score), "名將戰法診斷": pattern, **row_base}
                    list_tab2_pullback.append(r2)

                elif curr_p >= ma5 >= ma10 >= ma20 and chg_1d >= -1.0:
                    if chg_3d >= 8.5 or curr_p >= bb_up or stk_j9 >= 95.0:
                        pattern = "⚠️ 3日急拉/J值過熱：切勿追高・分批鎖利"
                    else:
                        pattern = "🛡️ 台積/台達電型：沿5/10MA上升軌道穩步推升"
                    r3 = {"趨勢狀態": pattern, **row_base}
                    list_tab2_orbit.append(r3)

            if curr_p >= ma20 and ma20 >= ma60 and w_bull and rev_ok and (not is_heavy_dump):
                l_score = 85
                if yoy_val and yoy_val >= 15.0:
                    l_score += 8
                elif yoy_val and yoy_val > 0:
                    l_score += 5
                if tdcc_val and tdcc_val >= 60.0:
                    l_score += 7
                elif tdcc_val and tdcc_val >= 45.0:
                    l_score += 4
                if ma60 >= ma120:
                    l_score += 3
                r_long = {
                    "波段總分": min(100, l_score),
                    "長線格局定性": "✅ 週K守10週線＋站穩月季線＋基本面有基",
                    **row_base
                }
                list_tab3_long.append(r_long)

        except Exception:
            continue

    df_t1 = pd.DataFrame(list_tab2_launch).sort_values(by="實戰優先分", ascending=False) if list_tab2_launch else pd.DataFrame()
    df_t2 = pd.DataFrame(list_tab2_pullback).sort_values(by="蓄勢評分", ascending=False) if list_tab2_pullback else pd.DataFrame()
    df_t3 = pd.DataFrame(list_tab2_orbit).sort_values(by="3日%", ascending=False) if list_tab2_orbit else pd.DataFrame()
    df_long = pd.DataFrame(list_tab3_long).sort_values(by="波段總分", ascending=False) if list_tab3_long else pd.DataFrame()

    return df_t1, df_t2, df_t3, df_long, vol_note, rt_quotes

# ==========================================
# 模組 6：Streamlit 戰情室主介面 (KDJ 短進短出 ＋ AI 一鍵複製數據包)
# ==========================================
now_str = get_tw_now().strftime("%Y-%m-%d %H:%M:%S")
st.markdown("## 🏛️ 台股每日戰情室 v33.0 (KDJ 敏捷運算・0050 ⇄ 正2 雙核換檔・AI 同步版)")

with st.expander("⚙️ 點此展開／調整【0050正2 ＋ 0050 母子艦隊持倉與成本參數】", expanded=False):
    c_in1, c_in2, c_in3, c_in4, c_in5 = st.columns(5)
    with c_in1:
        in_lots_2x = st.number_input("0050正2 持有張數", value=3.0, step=0.1, format="%.2f")
    with c_in2:
        in_cost_2x = st.number_input("0050正2 平均成本(元)", value=34.76, step=0.1, format="%.2f")
    with c_in3:
        in_lots_1x = st.number_input("0050 壓艙石張數", value=1.0, step=0.1, format="%.2f")
    with c_in4:
        in_cost_1x = st.number_input("0050 平均成本(元)", value=101.90, step=0.1, format="%.2f")
    with c_in5:
        in_cash_pool = st.number_input("已收割待買回現金池(元)", value=0.0, step=1000.0, format="%.0f")

btn_col1, btn_col2, btn_col3 = st.columns([2, 2, 4])
with btn_col1:
    if st.button("⚡ 秒速刷新盤中即時報價 (MIS直連)", use_container_width=True):
        fetch_mis_realtime_batch.clear()
with btn_col2:
    if st.button("🔄 完整重掃全市場與歷史K線", use_container_width=True):
        st.cache_data.clear()

with st.spinner("📡 正在同步證交所 MIS 即時行情、大盤 KDJ 與個股技術指標..."):
    df_launch, df_pullback, df_orbit, df_long, vol_note, rt_quotes = scan_all_stocks()
    fleet = fetch_and_analyze_fleet(rt_quotes)

st.caption(f"🕒 台灣時間：{now_str} ｜ 報價引擎：{fleet['quote_src']} ｜ 量能狀態：{vol_note}")

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

# 計算五階與 KDJ 連動點位
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
twii_ma240 = fleet["ma240"]

t_harvest = calc_target_2x(twii_harvest_zone)
t_ma20 = calc_target_2x(twii_ma20)
t_ma60_buf = calc_target_2x(twii_ma60_buffer)
t_ma60 = calc_target_2x(twii_ma60)
t_ma240 = calc_target_2x(twii_ma240)

t1x_harvest = calc_target_1x(twii_harvest_zone)
t1x_ma20 = calc_target_1x(twii_ma20)
t1x_ma60_buf = calc_target_1x(twii_ma60_buffer)

j_now = fleet["j9"]
j_prev = fleet["j9_prev"]
j_delta = round(j_now - j_prev, 1)
j_trend_str = f"↑勾頭轉強 ({j_delta:+.1f})" if j_delta > 0 else f"↓下彎整理 ({j_delta:+.1f})"

# ==========================================
# 🤖 專為 Gemini AI 設計的「一鍵複製完整戰情數據包」
# ==========================================
def build_ai_snapshot_text():
    lines = [
        f"【台股每日戰情室 v33.0 盤中即時數據包】({now_str})",
        f"1. 大盤與KDJ指標：加權指數 {twii_now:,.1f}點 ({fleet['twii_chg_pct']:+.2f}%, 高低 {fleet['twii_low']:,.0f}~{fleet['twii_high']:,.0f}) | 20MA月線 {twii_ma20:,.1f} (乖離 {fleet['bias_20']:+.2f}%) | 60MA季線 {twii_ma60:,.1f} (乖離 {fleet['bias_60']:+.2f}%) | 季線前哨+1.6% {twii_ma60_buffer:,.1f} | 布林上軌前哨 {twii_harvest_zone:,.1f} | 日KDJ: K9={fleet['k9']} / D9={fleet['d9']} / J9={j_now} ({j_trend_str}) | MACD柱={fleet['macd_hist']:+.1f}",
        f"2. 母子艦隊現況：0050正2(00631L) 現價 {p_2x:.2f}元 ({fleet['p_2x_chg']:+.2f}%), 持有 {in_lots_2x:.2f}張(成本{in_cost_2x:.2f}), 市值 {val_2x:,}元(損益 {pnl_2x:+,}元/{pnl_2x_pct:+.2f}%) | 0050 現價 {p_1x:.2f}元 ({fleet['p_1x_chg']:+.2f}%), 持有 {in_lots_1x:.2f}張(成本{in_cost_1x:.2f}), 市值 {val_1x:,}元(損益 {pnl_1x:+,}元/{pnl_1x_pct:+.2f}%) | 總市值 {total_fleet_val:,}元 (總損益 {total_pnl:+,}元/{total_pnl_pct:+.2f}%), 有效曝險 {effective_leverage}%",
        f"3. 0050正2 對應目標價：高檔收割區={t_harvest:.2f}元 | 20MA月線反彈點={t_ma20:.2f}元 | 季線前哨攔截點={t_ma60_buf:.2f}元 | 60MA季線地板={t_ma60:.2f}元"
    ]
    if not df_launch.empty:
        top_l = [f"{r['代碼']}{r['股名']}(現價{r['現價']}/今日{r['今日%']:+}%/量比{r['5MA量比']}/J值{r['日J值']}/防線{r['鋼鐵防線']}/大戶{r['400張大戶%']}/YoY{r['月營收YoY']})" for _, r in df_launch.head(8).iterrows()]
        lines.append("4. 表一(旱地拔蔥點火榜)：" + "；".join(top_l))
    if not df_pullback.empty:
        top_p = [f"{r['代碼']}{r['股名']}(現價{r['現價']}/今日{r['今日%']:+}%/量比{r['5MA量比']}/J值{r['日J值']}/月線{r['月線承接']}/YoY{r['月營收YoY']})" for _, r in df_pullback.head(8).iterrows()]
        lines.append("5. 表二(N字量縮回測榜)：" + "；".join(top_p))
    if not df_long.empty:
        top_g = [f"{r['代碼']}{r['股名']}(現價{r['現價']}/今日{r['今日%']:+}%/量比{r['5MA量比']}/大戶{r['400張大戶%']}/YoY{r['月營收YoY']})" for _, r in df_long.head(8).iterrows()]
        lines.append("6. 分頁三(長線雙守有基榜)：" + "；".join(top_g))
    return "\n".join(lines)

with st.expander("🤖 點此展開【Gemini AI 專屬戰情數據包】（點擊右上角 📋 圖示即可一鍵複製貼給 Gemini 分析，免截圖！）", expanded=False):
    st.code(build_ai_snapshot_text(), language="markdown")

tab1, tab2, tab3 = st.tabs([
    "📊 0050正2 ＋ 0050 母子艦隊滾雪球戰情室 (KDJ版)",
    "⚡ 短線操作精選區 (七大實戰名將)",
    "🏛️ 長期投資波段區 (雙守有基)"
])

with tab1:
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown("#### 🏛️ 加權指數")
        st.metric(
            label=f"台股大盤 ({fleet['date_str']})",
            value=f"{twii_now:,.1f} 點",
            delta=f"{fleet['twii_chg_pct']:+.2f}% (高低 {fleet['twii_low']:,.0f}~{fleet['twii_high']:,.0f})"
        )
        if j_now <= 0:
            kdj_badge = "🎯 J<0 極度超賣擊球區"
        elif j_now <= 20 and j_delta > 0:
            kdj_badge = "⚡ J值低檔勾頭轉強"
        elif j_now >= 90:
            kdj_badge = "🔥 J>90 短線極度過熱"
        elif fleet["k9"] >= fleet["d9"]:
            kdj_badge = "☀️ KDJ 金叉多頭"
        else:
            kdj_badge = "📉 KDJ 死叉整理"

        st.caption(
            f"• 月線(20MA)：`{fleet['ma20']:,.1f}` (`{fleet['bias_20']:+.2f}%`)\n\n"
            f"• 季線(60MA)：`{fleet['ma60']:,.1f}` (`{fleet['bias_60']:+.2f}%`)\n\n"
            f"• 日KDJ：`K={fleet['k9']}` / `D={fleet['d9']}` / **`J={j_now}`** ({kdj_badge})"
        )

    with col2:
        st.markdown("#### 🚀 0050正2")
        st.metric(
            label=f"00631L 子艦隊 (今日 {fleet['p_2x_chg']:+.2f}%)",
            value=f"{p_2x:,.2f} 元",
            delta=f"損益 {pnl_2x:+,} 元 ({pnl_2x_pct:+.2f}%)"
        )
        st.caption(
            f"• 持有部位：`{in_lots_2x:.2f} 張` (成本 `{in_cost_2x:.2f} 元`)\n\n"
            f"• 部位市值：`{val_2x:,} 元` (佔比 `{ratio_2x_pct}%`)\n\n"
            f"• 角色定錨：`2倍槓桿攻擊與滾張數主力`"
        )

    with col3:
        st.markdown("#### 🛡️ 0050")
        st.metric(
            label=f"0050 壓艙石 (今日 {fleet['p_1x_chg']:+.2f}%)",
            value=f"{p_1x:,.2f} 元",
            delta=f"損益 {pnl_1x:+,} 元 ({pnl_1x_pct:+.2f}%)"
        )
        st.caption(
            f"• 持有部位：`{in_lots_1x:.2f} 張` (成本 `{in_cost_1x:.2f} 元`)\n\n"
            f"• 部位市值：`{val_1x:,} 元` (佔比 `{ratio_1x_pct}%`)\n\n"
            f"• 角色定錨：`長線底倉・領息與換檔火種`"
        )

    with col4:
        st.markdown("#### 💰 總市值")
        st.metric(
            label=f"母子艦隊總資產 (總成本 {total_cost_all:,} 元)",
            value=f"{total_fleet_val:,} 元",
            delta=f"總損益 {total_pnl:+,} 元 ({total_pnl_pct:+.2f}%)"
        )
        st.caption(
            f"• 整體有效曝險：`{effective_leverage}%`\n\n"
            f"• 待買回現金池：`{int(in_cash_pool):,} 元`\n\n"
            f"• 艦隊結構：`正2 ({ratio_2x_pct}%) ＋ 0050 ({ratio_1x_pct}%)`"
        )

    st.markdown("---")

    net_cash_harvest = int(round(t_harvest * 1000 * (1.0 - 0.002425)))
    shares_at_ma20 = int(net_cash_harvest // max(1.0, t_ma20))
    shares_at_buf = int(net_cash_harvest // max(1.0, t_ma60_buf))
    shares_at_ma60 = int(net_cash_harvest // max(1.0, t_ma60))
    shares_at_ma240 = int(net_cash_harvest // max(1.0, t_ma240))

    rebound_net_cash = int(round(t_ma20 * 1000 * (1.0 - 0.002425)))
    rebound_gain_shares = int(rebound_net_cash // max(1.0, t_ma60_buf)) - 1000
    rebound_gain_cash = int(round((t_ma20 - t_ma60_buf) * 1000))

    half_1x_net = int(round(500 * t1x_ma60_buf * (1.0 - 0.002425)))
    buy_2x_shares_from_1x = int(half_1x_net // max(1.0, t_ma60_buf * 1.001425))
    rebound_2x_sell_net = int(round(buy_2x_shares_from_1x * t_ma20 * (1.0 - 0.002425)))
    buyback_500_1x_cost = int(round(500 * t1x_ma20 * 1.001425))
    switch_net_profit = rebound_2x_sell_net - buyback_500_1x_cost

    # 精準區分「正在季線前哨圈內」與「已從季線前哨反彈脫離」
    in_buffer_zone = (twii_now <= twii_ma60_buffer) and (twii_now >= twii_ma60 * 0.995)

    if twii_now >= twii_harvest_zone or j_now >= 95.0:
        st.warning(
            f"🔥 **【檔位 1：KDJ 極度過熱高檔收割警報】大盤已進入高檔收割圈 ({twii_harvest_zone:,.1f} 點) 或 J9={j_now} 破百鈍化！**\n\n"
            f"• **現金池滾股方案**：逢高賣出 **第 1 張機動 0050正2**（約 **{t_harvest:.2f} 元**），淨回收約 **{net_cash_harvest:,} 元** 入池待命。\n"
            f"• **0050⇄正2 換檔方案**：將賣出 1 張正2 之資金轉買進 **`{int(net_cash_harvest // max(1.0, t1x_harvest))} 股 0050`**（曝險降至 `134.7%`）！"
        )
    elif twii_now >= twii_ma20:
        st.info(
            f"☀️ **【檔位 2：多頭主升段巡航】大盤穩守月線 ({twii_ma20:,.1f} 點) 之上，日 J9={j_now} ({j_trend_str})！**\n\n"
            f"• **實戰行動**：手頭 `{in_lots_2x:.1f} 張正2 ＋ {in_lots_1x:.1f} 張 0050` 續抱（若先前於季線前哨有將 0.5張0050 轉正2 者，於觸及月線 `{t_ma20:.2f} 元` 或 `J9 >= 80` 時可先換回 500股 0050 收割短差）。"
        )
    elif in_buffer_zone:
        st.success(
            f"🎯 **【檔位 3：季線前哨精準攔截訊號（黃金第一擊球區）】大盤位於季線前 1.6% 防守圈內 ({twii_now:,.1f} <= {twii_ma60_buffer:,.1f} 點) 且日 J9={j_now} ({j_trend_str})！**\n\n"
            f"• **方案 A（現金池買回）**：若現金池有資金，建議於現價 **`{p_2x:.2f} 元`** 動用 **50%~60% 現金池買進正2**！\n"
            f"• **方案 B（0050 ➔ 正2 雙核轉攻）**：可將 **`0.5 張 (500股) 0050`** 賣出轉買進 **`{buy_2x_shares_from_1x:,} 股 0050正2`**（曝險升至 `175.1%`）；待反彈回月線 `{twii_ma20:,.1f} 點` (正2約 `{t_ma20:.2f}元` 或 `J9 > 80`) 換回 `500股 0050`，可淨賺 **`+{switch_net_profit:,} 元`** 價差！"
        )
    elif twii_now > twii_ma60_buffer and twii_now < twii_ma20:
        st.success(
            f"🚀 **【季線前哨攔截成功 ➔ J值勾頭反彈推進中】大盤已自季線前哨 ({twii_ma60_buffer:,.1f} 點) 有守強彈至 `{twii_now:,.1f} 點`，日 J9={j_now} ({j_trend_str})！**\n\n"
            f"• **已於前哨低接/換股者**：恭喜精準攔截！請抱緊增持之正2 部位，上檔短差收割目標鎖定 **20MA 月線 `{twii_ma20:,.1f} 點`（對應正2約 `{t_ma20:.2f} 元`）** 或當 **`日 J9 >= 80`** 時換回 0050 鎖利！\n"
            f"• **維持原標準配置者**：目前位於月季線中段（距離月線 `{fleet['bias_20']:+.2f}%`），維持 `{in_lots_2x:.1f} 張正2 ＋ {in_lots_1x:.1f} 張 0050` 續抱即可，不追高換股；若再次拉回 **`{twii_ma60_buffer:,.1f} 點`（正2約 `{t_ma60_buf:.2f} 元`）且 `J9 < 15`** 再出手。"
        )
    else:
        st.error(
            f"❄️ **【檔位 4：破季線防禦警戒】大盤跌破 60MA 季線 ({twii_ma60:,.1f} 點)，日 J9={j_now}！**\n\n"
            f"• **實戰行動**：若連續 3 日收盤站不回季線，建議將 **2 張 `0050正2` 轉換為 `0050` 壓艙石或現金**（有效曝險降至 `116.7%`），避開主跌段 2 倍傷害。"
        )

    st.markdown("#### 🎯 表 A：大盤五階敏捷指標 ＋ KDJ (J值) ➔ 0050正2 波段收割與前哨攔截精算表")
    df_t50_levels = pd.DataFrame([
        {
            "五階實戰錨點 (含KDJ)": "🔥 階段1：高檔收割區 (布林上軌前哨 / J9>95)",
            "大盤觸發點位": f"{twii_harvest_zone:,.1f} 點",
            "0050正2 目標價": f"{t_harvest:,.2f} 元",
            "敏捷實戰操作指令": "J值破百鈍化彎頭・賣出第 1 張機動正2 (轉現金或轉0050)",
            "滾張數／價差效益試算": f"淨回收約 {net_cash_harvest:,} 元 (或換成 {int(net_cash_harvest // max(1.0, t1x_harvest))} 股 0050)"
        },
        {
            "五階實戰錨點 (含KDJ)": "⚡ 階段2：多空樞紐 ＆ 反彈短差點 (20MA月線 / J9>80)",
            "大盤觸發點位": f"{twii_ma20:,.1f} 點",
            "0050正2 目標價": f"{t_ma20:,.2f} 元",
            "敏捷實戰操作指令": "季線前哨低接者，於觸及月線或 J9>80 時收割短差歸位",
            "滾張數／價差效益試算": f"前哨低接彈回月線賣出 ➔ 每張賺 +{rebound_gain_cash:,} 元 (或 +{rebound_gain_shares} 股)"
        },
        {
            "五階實戰錨點 (含KDJ)": "🎯 階段3：黃金第一擊球區 (季線前哨+1.6% ＆ J9<15)",
            "大盤觸發點位": f"{twii_ma60_buffer:,.1f} 點",
            "0050正2 目標價": f"{t_ma60_buf:,.2f} 元",
            "敏捷實戰操作指令": "J<0極度超賣或J<15勾頭！動用現金池或 0.5張0050 轉正2",
            "滾張數／價差效益試算": f"高檔賣1張於此買回 ➔ 變 {shares_at_buf:,} 股 ({shares_at_buf - 1000:+,}股・不踏空)"
        },
        {
            "五階實戰錨點 (含KDJ)": "🛡️ 階段4：黃金第二擊球區 (60MA季線鐵血地板)",
            "大盤觸發點位": f"{twii_ma60:,.1f} 點",
            "0050正2 目標價": f"{t_ma60:,.2f} 元",
            "敏捷實戰操作指令": "觸及季線本體且J值背離勾頭・剩餘現金打滿",
            "滾張數／價差效益試算": f"高檔賣1張於此買回 ➔ 變 {shares_at_ma60:,} 股 ({shares_at_ma60 - 1000:+,}股)"
        },
        {
            "五階實戰錨點 (含KDJ)": "💎 階段5：長線鐵血大底 (240MA年線國安防線)",
            "大盤觸發點位": f"{twii_ma240:,.1f} 點",
            "0050正2 目標價": f"{t_ma240:,.2f} 元",
            "敏捷實戰操作指令": "歷史級長線大底・0050與現金池全數重裝轉正2",
            "滾張數／價差效益試算": f"高檔賣1張於此買回 ➔ 變 {shares_at_ma240:,} 股 ({shares_at_ma240 - 1000:+,}股)"
        }
    ])
    st.dataframe(df_t50_levels, use_container_width=True, hide_index=True)

    st.markdown("#### 🔄 表 B：0050 ⇄ 0050正2「KDJ 四段式雙核攻守換檔」實戰精算表")
    df_switch_modes = pd.DataFrame([
        {
            "艦隊攻守檔位": "🔥 檔位1：高檔減壓鎖利檔",
            "KDJ 與位階觸發條件": f"攻抵 {twii_harvest_zone:,.1f} 點 或 日 J9 >= 95",
            "雙核轉換動作": f"賣 1張 正2 ({t_harvest:.2f}元) ➔ 轉買 {int(net_cash_harvest // max(1.0, t1x_harvest))}股 0050 ({t1x_harvest:.2f}元)",
            "換檔後持倉佔比": "正2 (34.7%) ＋ 0050 (65.3%)",
            "有效曝險": "134.7%",
            "戰略目的與效益": "J值破百極度過熱時鎖住 2x 獲利、保留 1x 參與權；回測前哨再換回正2多滾 +58 股"
        },
        {
            "艦隊攻守檔位": "☀️ 檔位2：標準巡航 ＆ 反彈歸位檔",
            "KDJ 與位階觸發條件": f"月線 ({twii_ma20:,.1f}點) 之上 或 前哨低接彈至月線/J9>=80",
            "雙核轉換動作": "維持 3張正2+1張0050 (前哨增持正2者於此換回500股0050)",
            "換檔後持倉佔比": f"正2 ({ratio_2x_pct}%) ＋ 0050 ({ratio_1x_pct}%)",
            "有效曝險": f"{effective_leverage}%",
            "戰略目的與效益": f"前哨轉攻部位於月線 ({t_ma20:.2f}元) 歸位，落袋 +{switch_net_profit:,}元 並避免盤整摩擦損耗"
        },
        {
            "艦隊攻守檔位": "🎯 檔位3：季線前哨 J 值超賣轉攻檔",
            "KDJ 與位階觸發條件": f"回測季線前哨 {twii_ma60_buffer:,.1f} 點 且 J9 < 0 (或 J9<15 勾頭)",
            "雙核轉換動作": f"賣 0.5張(500股) 0050 ➔ 轉買 {buy_2x_shares_from_1x:,}股 正2 ({t_ma60_buf:.2f}元)",
            "換檔後持倉佔比": "正2 (75.1%) ＋ 0050 (24.9%)",
            "有效曝險": "175.1%",
            "戰略目的與效益": f"利用 J<0 極端超賣比 KD 提早 1~2 天攔截 V 轉；彈回月線淨賺 +{switch_net_profit:,}元"
        },
        {
            "艦隊攻守檔位": "❄️ 檔位4：破季線避險防守檔",
            "KDJ 與位階觸發條件": f"實體跌破季線 {twii_ma60:,.1f} 點滿 3 日 且 KDJ 死叉向下",
            "雙核轉換動作": f"賣 2張 正2 ({t_ma60:.2f}元) ➔ 轉買 0050 或保留現金",
            "換檔後持倉佔比": "正2 (16.7%) ＋ 0050/現金 (83.3%)",
            "有效曝險": "116.7%",
            "戰略目的與效益": "切斷空頭主跌段 2倍槓桿重傷，保留元氣待年線或重返季線再戰"
        }
    ])
    st.dataframe(df_switch_modes, use_container_width=True, hide_index=True)

with tab2:
    st.caption(f"⚡ 盤中即時引擎已開啟（已加入月營收 YoY>0 濾網與日 J 值欄位）｜ 城堡點位：天花板(20日高/布林上軌/突破延伸+7%)、月線承接(20MA)、鋼鐵防線(10MA/紅K低)、極限停損(60MA)")
    
    st.markdown("#### 🔥 【表一：階段 3(3) 旱地拔蔥 ＆ 七大實戰名將點火榜】")
    if not df_launch.empty:
        st.dataframe(df_launch, use_container_width=True, hide_index=True)
    else:
        st.info("今日盤面暫無符合「帶量 > 1.35倍 ＋ 突破均線糾結 ＋ 營收正成長」之點火標的，請保持耐心切勿追高（紀律 6：永遠只做看得懂的部分）。")

    st.markdown("#### 🎯 【表二：N字洗盤蓄勢 ＆ 拉回月線/十日線低接榜】")
    if not df_pullback.empty:
        st.dataframe(df_pullback, use_container_width=True, hide_index=True)
    else:
        st.info("今日暫無符合「量縮回測月線有守 ＋ 營收正成長」之洗盤標的。")

    st.markdown("#### 🛡️ 【表三：上升軌道推升 ＆ 3日急拉/J值過熱警戒榜】")
    if not df_orbit.empty:
        st.dataframe(df_orbit, use_container_width=True, hide_index=True)
    else:
        st.info("今日暫無符合多頭軌道推升之標的。")

with tab3:
    st.markdown("#### 🏛️ 【長期投資波段區：週K守10週線 ＋ 日線站穩月季線 ＋ 月營收正成長旗艦榜】")
    st.caption("篩選基準：嚴守模組 2 與模組 3「長線保護短線」原則（站穩 10 週均線、現價 >= 20MA >= 60MA、月營收 YoY > 0、排除當日價跌量增股）")
    if not df_long.empty:
        st.dataframe(df_long, use_container_width=True, hide_index=True)
    else:
        st.info("目前無符合長線波段條件之標的。")
