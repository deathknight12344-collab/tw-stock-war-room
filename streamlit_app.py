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
    page_title="台股每日戰情室 v34.0",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ==========================================
# 模組 1：台灣時間定錨與 U 型微笑量能推估引擎
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
# 模組 5：多週期 (日K ＋ 60分K ＋ 週月K) KDJ 與技術指標演算核心
# ==========================================
def compute_kdj(highs, lows, closes, n=9):
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

@st.cache_data(ttl=180, show_spinner=False)
def download_fleet_60m():
    """下載大盤與 0050正2 近 2 個月之 60分K線，用於精準判斷微觀落底與起漲"""
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

    twii_now = twii_official["close"] if twii_official["close"] > 0 else 46041.4
    twii_chg_pct = twii_official["chg_pct"]
    twii_high = twii_now
    twii_low = twii_now
    ma5 = round(twii_now, 1)
    ma10 = round(twii_now, 1)
    ma20 = round(twii_now * 1.04, 1)
    ma60 = round(twii_now * 1.003, 1)
    ma240 = round(twii_now * 0.83, 1)
    w_ma10 = round(twii_now * 1.02, 1)
    m_ma5 = round(twii_now * 0.996, 1)
    bb_upper = round(twii_now * 1.08, 1)
    bb_lower = round(twii_now * 0.98, 1)
    k9, d9, j9, j9_prev, macd_hist = 12.2, 22.9, -8.9, -1.0, -378.0
    date_str = today_str
    quote_src = "Yahoo Finance / 證交所"

    # 60分K 指標預設值
    k60_k, k60_d, k60_j, k60_j_prev = 15.0, 25.0, -5.0, 5.0
    k60_ma5, k60_ma20, k60_ma60 = twii_now, twii_now, twii_now
    k60_macd_osc = -50.0
    k60_is_red_bar = False

    p_2x = price_dict.get("00631L", {}).get("close", 35.61)
    p_2x_chg = price_dict.get("00631L", {}).get("chg_pct", 0.0)
    p_1x = price_dict.get("0050", {}).get("close", 106.90)
    p_1x_chg = price_dict.get("0050", {}).get("chg_pct", 0.0)

    # 1. 解析日 K、週 K (10週線)、月 K (5月線)
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
                ma5 = round(float(np.mean(c_twii[-5:])), 1)
                ma10 = round(float(np.mean(c_twii[-10:])), 1)
                ma20 = round(float(np.mean(c_twii[-20:])), 1)
                ma60 = round(float(np.mean(c_twii[-60:])), 1) if len(c_twii) >= 60 else ma20
                ma240 = round(float(np.mean(c_twii[-240:])), 1) if len(c_twii) >= 240 else ma60
                std20 = float(np.std(c_twii[-20:]))
                bb_upper = round(ma20 + 2.0 * std20, 1)
                bb_lower = round(ma20 - 2.0 * std20, 1)
                k9, d9, j9, j9_prev = compute_kdj(h_twii[-60:], l_twii[-60:], c_twii[-60:])
                _, _, osc_twii = compute_macd(c_twii)
                macd_hist = round(float(osc_twii[-1]), 1)

                # 計算模組 3「面」：10週均線 與 5月均線
                s_close = pd.Series(c_twii, index=pd.date_range(end=datetime.datetime.now(), periods=len(c_twii), freq="B"))
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

    # 2. 解析 60分K線 (60m KDJ、60m 均線、60m 止跌紅K判定)
    try:
        if not data_60m.empty and "^TWII" in data_60m:
            df_60 = data_60m["^TWII"].dropna(subset=["Close"]).copy()
            if len(df_60) >= 25:
                c_60 = df_60["Close"].values.astype(float)
                o_60 = df_60["Open"].values.astype(float)
                h_60 = df_60["High"].values.astype(float)
                l_60 = df_60["Low"].values.astype(float)

                # 將 MIS 最新即時點位注入最新一根 60分K棒
                if abs(c_60[-1] - twii_now) > 1.0:
                    c_60[-1] = twii_now
                    h_60[-1] = max(h_60[-1], twii_now)
                    l_60[-1] = min(l_60[-1], twii_now)

                k60_ma5 = round(float(np.mean(c_60[-5:])), 1)
                k60_ma20 = round(float(np.mean(c_60[-20:])), 1)
                k60_ma60 = round(float(np.mean(c_60[-60:])), 1) if len(c_60) >= 60 else k60_ma20
                k60_k, k60_d, k60_j, k60_j_prev = compute_kdj(h_60[-60:], l_60[-60:], c_60[-60:])
                _, _, osc_60 = compute_macd(c_60)
                k60_macd_osc = round(float(osc_60[-1]), 1)
                k60_is_red_bar = bool(c_60[-1] >= o_60[-1] and c_60[-1] >= c_60[-2])
    except Exception:
        pass

    bias_20 = round((twii_now - ma20) / max(1.0, ma20) * 100, 2)
    bias_60 = round((twii_now - ma60) / max(1.0, ma60) * 100, 2)

    return {
        "twii_now": twii_now, "twii_chg_pct": twii_chg_pct,
        "twii_high": twii_high, "twii_low": twii_low,
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60, "ma240": ma240,
        "w_ma10": w_ma10, "m_ma5": m_ma5,
        "bb_upper": bb_upper, "bb_lower": bb_lower,
        "bias_20": bias_20, "bias_60": bias_60,
        "k9": k9, "d9": d9, "j9": j9, "j9_prev": j9_prev,
        "macd_hist": macd_hist,
        "k60_k": k60_k, "k60_d": k60_d, "k60_j": k60_j, "k60_j_prev": k60_j_prev,
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

            rev_ok = (yoy_val is None) or (yoy_val > 0)
            is_heavy_dump = (chg_1d < 0 and vol_ratio >= 1.5) or (chg_1d <= -2.2)

            if curr_p >= ma20 and ma20 >= ma60 * 0.98 and rev_ok and (not is_heavy_dump):
                is_overheated = (chg_3d >= 10.0) or (stk_j9 >= 90.0)

                if chg_1d >= 2.0 and vol_ratio >= 1.35 and (not is_overheated):
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

                # 表二：N字量縮回測低接榜（嚴格要求日J值 <= 65，排除 J>90 高檔剛轉折股）
                elif (-2.0 <= chg_1d <= 2.2) and (vol_ratio <= 0.85) and (curr_p >= ma20) and (stk_j9 <= 65.0):
                    score = 82 + int((1.0 - vol_ratio) * 15)
                    pattern = "⚡ 神準/台光電型：N字量縮回測10/20MA有守(J值已冷卻)"
                    r2 = {"蓄勢評分": min(98, score), "名將戰法診斷": pattern, **row_base}
                    list_tab2_pullback.append(r2)

                elif curr_p >= ma5 >= ma10 >= ma20 and chg_1d >= -1.5:
                    if is_overheated or curr_p >= bb_up:
                        pattern = "⚠️ 3日急拉/J值>=90過熱：切勿追高・分批鎖利"
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
# 模組 6：Streamlit 戰情室主介面 (日K定戰區 ＋ 60分K扣扳機 雙週期共振版)
# ==========================================
now_str = get_tw_now().strftime("%Y-%m-%d %H:%M:%S")
st.markdown("## 🏛️ 台股每日戰情室 v34.0 (日K定戰區 ＋ 60分K扣扳機 雙週期共振版)")

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
    if st.button("🔄 完整重掃全市場與 60分/日K線", use_container_width=True):
        st.cache_data.clear()

with st.spinner("📡 正在同步證交所 MIS 即時行情、大盤 60分K ＋ 日/週/月K 多週期指標..."):
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
t_ma60_buf = calc_target_2x(twii_ma60_buffer)
t_ma60 = calc_target_2x(twii_ma60)
t_m_ma5 = calc_target_2x(twii_m_ma5)
t_ma240 = calc_target_2x(twii_ma240)
t_60m_ma20 = calc_target_2x(fleet["k60_ma20"])
t_60m_ma60 = calc_target_2x(fleet["k60_ma60"])

t1x_harvest = calc_target_1x(twii_harvest_zone)
t1x_ma20 = calc_target_1x(twii_ma20)
t1x_ma60 = calc_target_1x(twii_ma60)

# 日K 與 60分K 的 J 值與轉折方向
j_now = fleet["j9"]
j_prev = fleet["j9_prev"]
j_delta = round(j_now - j_prev, 1)
j_trend_str = f"↑勾頭 ({j_delta:+.1f})" if j_delta > 0 else f"↓下彎 ({j_delta:+.1f})"

k60_k = fleet["k60_k"]
k60_d = fleet["k60_d"]
k60_j = fleet["k60_j"]
k60_j_prev = fleet["k60_j_prev"]
k60_j_delta = round(k60_j - k60_j_prev, 1)
k60_j_trend = f"↑勾頭 ({k60_j_delta:+.1f})" if k60_j_delta > 0 else f"↓下探 ({k60_j_delta:+.1f})"

# 判斷 60分K 是否已經確認止跌起漲 (60分J勾頭向上 且 站上60分5MA 或 60分KD金叉)
k60_bottom_turned_up = (k60_j_delta > 0 and (twii_now >= fleet["k60_ma5"] or fleet["k60_is_red_bar"])) or (k60_k >= k60_d and k60_j <= 50.0)
k60_overheated_peaking = (k60_j >= 68.0 and k60_j_delta <= 0) or (k60_j >= 80.0)

def build_ai_snapshot_text():
    lines = [
        f"【台股每日戰情室 v34.0 雙週期共振數據包】({now_str})",
        f"1. 大盤多週期位階：現價 {twii_now:,.1f}點 ({fleet['twii_chg_pct']:+.2f}%, 高低 {fleet['twii_low']:,.0f}~{fleet['twii_high']:,.0f}) | 日20MA月線 {twii_ma20:,.1f} ({fleet['bias_20']:+.2f}%) | 日60MA季線 {twii_ma60:,.1f} ({fleet['bias_60']:+.2f}%) | 週10MA {twii_w_ma10:,.1f} | 月5MA最後防線 {twii_m_ma5:,.1f}",
        f"2. 日K vs 60分K 雙週期KDJ：『日KDJ』K={fleet['k9']}/D={fleet['d9']}/J={j_now}({j_trend_str}), 日MACD柱={fleet['macd_hist']:+.1f} ｜ 『60分KDJ』60m_K={k60_k}/60m_D={k60_d}/60m_J={k60_j}({k60_j_trend}), 60分5MA={fleet['k60_ma5']:,.1f} / 60分20MA={fleet['k60_ma20']:,.1f} / 60分60MA={fleet['k60_ma60']:,.1f}",
        f"3. 母子艦隊現況：0050正2(00631L) 現價 {p_2x:.2f}元 ({fleet['p_2x_chg']:+.2f}%), 持有 {in_lots_2x:.2f}張(成本{in_cost_2x:.2f}), 市值 {val_2x:,}元(損益 {pnl_2x:+,}元/{pnl_2x_pct:+.2f}%) | 0050 現價 {p_1x:.2f}元 ({fleet['p_1x_chg']:+.2f}%), 持有 {in_lots_1x:.2f}張(成本{in_cost_1x:.2f}), 市值 {val_1x:,}元(損益 {pnl_1x:+,}元/{pnl_1x_pct:+.2f}%) | 總市值 {total_fleet_val:,}元 (總損益 {total_pnl:+,}元/{total_pnl_pct:+.2f}%), 有效曝險 {effective_leverage}%",
        f"4. 0050正2 對應目標價：高檔收割區={t_harvest:.2f}元 | 日20MA月線={t_ma20:.2f}元 | 60分60MA反壓={t_60m_ma60:.2f}元 | 日60MA季線={t_ma60:.2f}元 | 月5MA防線={t_m_ma5:.2f}元"
    ]
    if not df_launch.empty:
        top_l = [f"{r['代碼']}{r['股名']}(現價{r['現價']}/今日{r['今日%']:+}%/3日{r['3日%']:+}%/量比{r['5MA量比']}/J值{r['日J值']}/防線{r['鋼鐵防線']}/大戶{r['400張大戶%']}/YoY{r['月營收YoY']})" for _, r in df_launch.head(8).iterrows()]
        lines.append("5. 表一(旱地拔蔥剛起漲點火榜)：" + "；".join(top_l))
    if not df_pullback.empty:
        top_p = [f"{r['代碼']}{r['股名']}(現價{r['現價']}/今日{r['今日%']:+}%/3日{r['3日%']:+}%/量比{r['5MA量比']}/J值{r['日J值']}/月線{r['月線承接']}/大戶{r['400張大戶%']}/YoY{r['月營收YoY']})" for _, r in df_pullback.head(8).iterrows()]
        lines.append("6. 表二(N字量縮回測低接榜)：" + "；".join(top_p))
    if not df_orbit.empty:
        top_o = [f"{r['代碼']}{r['股名']}(現價{r['現價']}/今日{r['今日%']:+}%/3日{r['3日%']:+}%/量比{r['5MA量比']}/J值{r['日J值']}/防線{r['鋼鐵防線']}/YoY{r['月營收YoY']})" for _, r in df_orbit.head(8).iterrows()]
        lines.append("7. 表三(軌道推升與J值過熱警戒榜)：" + "；".join(top_o))
    if not df_long.empty:
        top_g = [f"{r['代碼']}{r['股名']}(現價{r['現價']}/今日{r['今日%']:+}%/量比{r['5MA量比']}/J值{r['日J值']}/大戶{r['400張大戶%']}/YoY{r['月營收YoY']})" for _, r in df_long.head(8).iterrows()]
        lines.append("8. 分頁三(長線雙守有基榜)：" + "；".join(top_g))
    return "\n".join(lines)

with st.expander("🤖 點此展開【Gemini AI 專屬戰情數據包】（點擊右上角 📋 圖示即可一鍵複製貼給 Gemini 分析，免截圖！）", expanded=False):
    st.code(build_ai_snapshot_text(), language="markdown")

tab1, tab2, tab3 = st.tabs([
    "📊 0050正2 ＋ 0050 母子艦隊戰情室 (日K＋60分K雙週期共振)",
    "⚡ 短線操作精選區 (七大實戰名將)",
    "🏛️ 長期投資波段區 (雙守有基)"
])

with tab1:
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown("#### 🏛️ 加權指數 (日K＋60分K)")
        st.metric(
            label=f"台股大盤 ({fleet['date_str']})",
            value=f"{twii_now:,.1f} 點",
            delta=f"{fleet['twii_chg_pct']:+.2f}% (高低 {fleet['twii_low']:,.0f}~{fleet['twii_high']:,.0f})"
        )
        w_status = "✅守穩" if twii_now >= twii_w_ma10 else "⚠️跌破"
        m_status = "✅有守" if twii_now >= twii_m_ma5 else "❌失守"
        st.caption(
            f"• 日均線：月線 `{twii_ma20:,.0f}` (`{fleet['bias_20']:+.1f}%`) ｜ 季線 `{twii_ma60:,.0f}` (`{fleet['bias_60']:+.2f}%`)\n\n"
            f"• 長線傘：10週線 `{twii_w_ma10:,.0f}` ({w_status}) ｜ **5月線 `{twii_m_ma5:,.0f}` ({m_status})**\n\n"
            f"• **日KDJ**：`K={fleet['k9']}` / `D={fleet['d9']}` / **`J={j_now}`** ({j_trend_str})\n\n"
            f"• **60分KDJ**：`K={k60_k}` / `D={k60_d}` / **`J={k60_j}`** ({k60_j_trend} | 60分5MA `{fleet['k60_ma5']:,.0f}`)"
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
            f"• 60分K壓力：60分20MA對應 `{t_60m_ma20:.2f}元` / 60分60MA `{t_60m_ma60:.2f}元`"
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
    shares_at_ma60 = int(net_cash_harvest // max(1.0, t_ma60))
    shares_at_m5 = int(net_cash_harvest // max(1.0, t_m_ma5))
    shares_at_ma240 = int(net_cash_harvest // max(1.0, t_ma240))

    # 以現價（季線/5月線戰區）換股 ➔ 彈回 60分K 60MA 或 日K 20MA 的試算
    half_1x_net_now = int(round(500 * p_1x * (1.0 - 0.002425)))
    buy_2x_shares_now = int(half_1x_net_now // max(1.0, p_2x * 1.001425))
    rebound_to_ma20_net = int(round(buy_2x_shares_now * t_ma20 * (1.0 - 0.002425))) - int(round(500 * t1x_ma20 * 1.001425))

    # ==========================================
    # 🏆 日K定戰區 ＋ 60分K扣扳機 雙週期共振智慧決策引擎
    # ==========================================
    in_daily_support_zone = (twii_now <= twii_ma60_buffer) and (twii_now >= twii_m_ma5 * 0.99)

    if twii_now >= twii_harvest_zone or (j_now >= 90.0 and k60_overheated_peaking):
        st.warning(
            f"🔥 **【雙週期高檔收割警報】大盤攻抵高檔收割圈 ({twii_harvest_zone:,.1f} 點) 且日J={j_now}、60分J={k60_j} 雙雙過熱轉折！**\n\n"
            f"• **實戰行動**：逢高賣出 **第 1 張機動 0050正2**（約 **{t_harvest:.2f} 元**），淨回收約 **{net_cash_harvest:,} 元** 入池或轉買 `{int(net_cash_harvest // max(1.0, t1x_harvest))} 股 0050`（曝險降至 `134.7%`）。"
        )
    elif in_daily_support_zone and k60_overheated_peaking:
        st.warning(
            f"⚠️ **【日K雖低檔・但 60分K 反彈見頂警戒（防範 10/21 假落底陷阱）】**\n\n"
            f"• **雙週期診斷**：雖然日K位於低檔（`日J={j_now}`），但 **60分K 的 `J={k60_j}` 已衝入高檔過熱區且遭遇 60分60MA (`{fleet['k60_ma60']:,.0f} 點`) 反壓**！\n"
            f"• **實戰行動**：**嚴禁在此追高買進正2！** 若前波已低接增持正2者，建議趁 60分K 高檔先行收割短差歸位，提防 60分K 死叉引發二次下殺。"
        )
    elif in_daily_support_zone and k60_bottom_turned_up:
        st.success(
            f"🟢 **【日K戰區 ＋ 60分K落底勾頭：雙週期共振精準擊球訊號！】**\n\n"
            f"• **雙週期確認**：\n"
            f"  1. **【日K戰區】**：大盤進入季線 (`{twii_ma60:,.0f}`) 與 5月線 (`{twii_m_ma5:,.0f}`) 核心支撐區，日KDJ 極度超賣 (`日J={j_now}`)。\n"
            f"  2. **【60分K扳機】**：**60分KDJ (`K={k60_k} / D={k60_d} / J={k60_j}`) 已完成落底並向上勾頭 (`{k60_j_trend}`)，且站上 60分5MA (`{fleet['k60_ma5']:,.0f} 點`)**！\n"
            f"• **實戰行動**：可於現價 **`{p_2x:.2f} 元`** 動用現金池買回正2，或將 **`0.5 張 (500股) 0050` 賣出轉買進 `{buy_2x_shares_now:,} 股 0050正2`**！第一道短差停利點看 **60分60MA (`{fleet['k60_ma60']:,.0f}點` / 正2約 `{t_60m_ma60:.2f}元`)**，第二道看 **日20MA月線 (`{twii_ma20:,.0f}點` / 正2約 `{t_ma20:.2f}元`，預計淨賺 `+{rebound_to_ma20_net:,}元`)**！"
        )
    elif in_daily_support_zone and (not k60_bottom_turned_up):
        st.warning(
            f"🟡 **【日K與60分K雙重超賣・等待 60分K 止跌勾頭扳機（切勿盲目摸底接刀！）】**\n\n"
            f"• **雙週期診斷**：\n"
            f"  1. **【日/週/月位階】**：今日大盤收在 `{twii_now:,.1f} 點`，微幅跌破 **日60MA季線 (`{twii_ma60:,.1f} 點`)** 與 **10週線 (`{twii_w_ma10:,.1f} 點`)**，正測試最後長線防線 **5月均線 (`{twii_m_ma5:,.1f} 點`)**；日KDJ 極度超賣 (`K={fleet['k9']} / D={fleet['d9']} / J={j_now}`)。\n"
            f"  2. **【60分K微觀狀態】**：60分KDJ 也已殺入低檔 (`K={k60_k} / D={k60_d} / J={k60_j}`)，**但目前 60分J 仍在向下探底 (`{k60_j_trend}`)，且指數尚未站回 60分5MA (`{fleet['k60_ma5']:,.1f} 點`)**！\n\n"
            f"• **淘金客鐵血行動指令**：\n"
            f"  - **先忍住不接下墜刀**：今日尾盤收最低，代表 60分K 短線賣壓尚未宣洩完畢，現有 `3張正2 + 1張0050` 先行守住，**暫勿將 0050 換成正2**。\n"
            f"  - **明日盤中扣扳機條件**：緊盯下方 **`5月均線 {twii_m_ma5:,.1f} 點`（正2約 `{t_m_ma5:.2f} 元`）** 與 **`季線 {twii_ma60:,.1f} 點`**，只要盤中 **60分K 收出止跌紅K棒、60分J值由負翻正勾頭並站上 60分5MA (`{fleet['k60_ma5']:,.0f} 點`)**，本燈號會自動轉為 **🟢 綠燈共振買訊**，屆時再右側安全出擊！"
        )
    elif twii_now > twii_ma60_buffer:
        st.info(
            f"☀️ **【多頭巡航／反彈推進區】大盤位於 `{twii_now:,.1f} 點` ｜ 日J={j_now} ({j_trend_str}) ｜ 60分J={k60_j} ({k60_j_trend})**\n\n"
            f"• **實戰行動**：若 60分J 衝高至 `75~85` 且遇 **60分60MA (`{fleet['k60_ma60']:,.0f}點` / 正2 `{t_60m_ma60:.2f}元`)** 或 **日20MA月線 (`{twii_ma20:,.0f}點` / 正2 `{t_ma20:.2f}元`)** 出現 60分K 死叉時，低檔增持之正2 部位可先收割短差歸位！"
        )
    else:
        st.error(
            f"❄️ **【長線防線失守警戒】大盤跌破日60MA季線 ({twii_ma60:,.1f} 點) 與 5月均線 ({twii_m_ma5:,.1f} 點)！**\n\n"
            f"• **實戰行動**：長線保護傘（10週線與5月線）雙雙失守，若 3 日內無法站回 `{twii_m_ma5:,.0f} 點`，建議趁 60分K 反彈時將 **2 張 `0050正2` 轉換為 `0050` 壓艙石或現金**（有效曝險降至 `116.7%`），嚴格保護本金！"
        )

    st.markdown("#### 🎯 表 A：【日K定戰區 ＋ 60分K找買賣點】五階雙週期共振精算表")
    df_t50_levels = pd.DataFrame([
        {
            "雙週期實戰錨點": "🔥 階段1：高檔波段收割區 (日布林上軌前哨)",
            "日K戰區點位": f"{twii_harvest_zone:,.1f} 點",
            "60分K 扣扳機條件": "日J>90 且 60分KDJ 高檔死叉跌破 60分5MA",
            "0050正2 目標價": f"{t_harvest:,.2f} 元",
            "實戰指令與滾股試算": f"賣出第1張正2 ➔ 淨回收 {net_cash_harvest:,} 元 (或換 {int(net_cash_harvest // max(1.0, t1x_harvest))}股 0050)"
        },
        {
            "雙週期實戰錨點": "⚡ 階段2：反彈短差停利區 (日20MA月線 / 60分60MA)",
            "日K戰區點位": f"{fleet['k60_ma60']:,.0f} ~ {twii_ma20:,.0f} 點",
            "60分K 扣扳機條件": f"60分J>=75 遇 60分60MA({fleet['k60_ma60']:,.0f}) 或日20MA 彎頭死叉",
            "0050正2 目標價": f"{t_60m_ma60:,.2f} ~ {t_ma20:,.2f} 元",
            "實戰指令與滾股試算": f"低檔轉攻之正2於此換回500股0050 (避免重演10/21反彈見頂未跑)"
        },
        {
            "雙週期實戰錨點": "🎯 階段3：季線攻防戰區 (日60MA季線 ±1.5%)",
            "日K戰區點位": f"{twii_ma60:,.1f} 點 (前哨 {twii_ma60_buffer:,.0f})",
            "60分K 扣扳機條件": f"日J<15 且 60分J({k60_j})落底翻正勾頭＋站上60分5MA({fleet['k60_ma5']:,.0f})",
            "0050正2 目標價": f"{t_ma60:,.2f} ~ {t_ma60_buf:,.2f} 元",
            "實戰指令與滾股試算": f"60分K未勾頭前忍住不接刀！確認勾頭後買回變 {shares_at_ma60:,} 股 (+{shares_at_ma60 - 1000}股)"
        },
        {
            "雙週期實戰錨點": "🛡️ 階段4：長線多頭最後防線 (月K 5月均線)",
            "日K戰區點位": f"{twii_m_ma5:,.1f} 點 (距今 {twii_now - twii_m_ma5:+.0f}點)",
            "60分K 扣扳機條件": "回測 5月線不破 ＋ 60分K 出現神針探底紅K與底背離金叉",
            "0050正2 目標價": f"{t_m_ma5:,.2f} 元",
            "實戰指令與滾股試算": f"模組3長線保護傘底線！共振有守時 0.5張0050 轉正2 (買回變 {shares_at_m5:,}股)"
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

    st.markdown("#### 🔄 表 B：0050 ⇄ 0050正2「日K × 60分K 雙週期四段式換檔」實戰精算表")
    df_switch_modes = pd.DataFrame([
        {
            "艦隊攻守檔位": "🔥 檔位1：高檔減壓鎖利檔",
            "日K戰區 ＋ 60分K扳機條件": f"大盤攻抵 {twii_harvest_zone:,.0f} 點 且 60分J>=85 高檔死叉",
            "雙核轉換動作": f"賣 1張 正2 ({t_harvest:.2f}元) ➔ 轉買 {int(net_cash_harvest // max(1.0, t1x_harvest))}股 0050",
            "換檔後持倉佔比": "正2 (34.7%) ＋ 0050 (65.3%)",
            "有效曝險": "134.7%",
            "實戰目的與防呆效益": "日K與60分K雙過熱鎖利，保留1倍底倉不踏空"
        },
        {
            "艦隊攻守檔位": "☀️ 檔位2：標準巡航 ＆ 60分K遇壓歸位檔",
            "日K戰區 ＋ 60分K扳機條件": f"反彈碰 60分60MA ({fleet['k60_ma60']:,.0f}點) 或 日20MA ({twii_ma20:,.0f}點) 且 60分J>=70 彎頭",
            "雙核轉換動作": "維持 3張正2+1張0050 (低檔增持正2者於此換回 500股 0050)",
            "換檔後持倉佔比": f"正2 ({ratio_2x_pct}%) ＋ 0050 ({ratio_1x_pct}%)",
            "有效曝險": f"{effective_leverage}%",
            "實戰目的與防呆效益": "解決 10/21「日K看似低檔、60分K已在高檔見頂」卻沒跑的痛點！"
        },
        {
            "艦隊攻守檔位": "🎯 檔位3：季線/5月線 雙週期落底轉攻檔",
            "日K戰區 ＋ 60分K扳機條件": f"位於季線({twii_ma60:,.0f})~5月線({twii_m_ma5:,.0f}) 且 60分J<15 翻正勾頭站上60分5MA",
            "雙核轉換動作": f"賣 0.5張(500股) 0050 ➔ 轉買 {buy_2x_shares_now:,}股 正2 (約 {p_2x:.2f}元)",
            "換檔後持倉佔比": "正2 (75.0%) ＋ 0050 (25.0%)",
            "有效曝險": "175.0%",
            "實戰目的與防呆效益": f"60分K未止跌前絕不摸底！確認 60分K 勾頭右側轉攻，彈回月線淨賺 +{rebound_to_ma20_net:,}元"
        },
        {
            "艦隊攻守檔位": "❄️ 檔位4：跌破季線與5月線 避險防守檔",
            "日K戰區 ＋ 60分K扳機條件": f"實體跌破季線({twii_ma60:,.0f})與5月線({twii_m_ma5:,.0f})滿3日 且 60分K反彈不過20MA",
            "雙核轉換動作": f"賣 2張 正2 ➔ 轉買 0050 或保留現金",
            "換檔後持倉佔比": "正2 (16.7%) ＋ 0050/現金 (83.3%)",
            "有效曝險": "116.7%",
            "實戰目的與防呆效益": "當 10週線與 5月線長線保護傘破裂時，果斷切斷 2倍槓桿重傷"
        }
    ])
    st.dataframe(df_switch_modes, use_container_width=True, hide_index=True)

with tab2:
    st.caption(f"⚡ 盤中即時引擎已開啟（表一排除J>=90過熱股；表二嚴格要求 J<=65 洗盤冷卻）｜ 城堡點位：天花板、月線承接、鋼鐵防線、極限停損")
    
    st.markdown("#### 🔥 【表一：階段 3(3) 旱地拔蔥 ＆ 七大實戰名將剛起漲點火榜 (未過熱)】")
    if not df_launch.empty:
        st.dataframe(df_launch, use_container_width=True, hide_index=True)
    else:
        st.info("今日盤面暫無符合「帶量突破 ＋ 未過熱(J<90且3日<10%) ＋ 營收正成長」之剛起漲標的，請保持耐心切勿追高。")

    st.markdown("#### 🎯 【表二：N字洗盤蓄勢 ＆ 拉回月線/十日線低接榜 (J<=65 洗盤冷卻)】")
    if not df_pullback.empty:
        st.dataframe(df_pullback, use_container_width=True, hide_index=True)
    else:
        st.info("今日暫無符合「量縮回測月線有守 ＋ J<=65冷卻 ＋ 營收正成長」之洗盤標的。")

    st.markdown("#### 🛡️ 【表三：上升軌道推升 ＆ 3日急拉/J值>=90過熱警戒榜】")
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
