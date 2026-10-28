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
    page_title="台股每日戰情室 v36.0",
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

    twii_now = twii_official["close"] if twii_official["close"] > 0 else 46807.0
    twii_chg_pct = twii_official["chg_pct"]
    twii_open = twii_now
    twii_high = twii_now
    twii_low = twii_now
    twii_prev_c = twii_now
    ma5 = round(twii_now, 1)
    ma10 = round(twii_now, 1)
    ma20 = round(twii_now * 1.02, 1)
    ma60 = round(twii_now * 0.99, 1)
    ma240 = round(twii_now * 0.83, 1)
    w_ma10 = round(twii_now * 1.01, 1)
    m_ma5 = round(twii_now * 0.985, 1)
    bb_upper = round(twii_now * 1.06, 1)
    bb_lower = round(twii_now * 0.97, 1)
    k9, d9, j9, j9_prev, kd_cross_d = 19.3, 19.3, 19.3, -1.9, True
    pdi_d, mdi_d, adx_d, adx_d_prev, dmi_bull_d = 18.8, 27.2, 23.5, 24.6, False
    macd_hist = -300.0
    date_str = today_str
    quote_src = "Yahoo Finance / 證交所"

    k60_k, k60_d, k60_j, k60_j_prev, k60_kd_cross = 66.5, 47.2, 105.1, 91.0, True
    k60_pdi, k60_mdi, k60_adx, k60_adx_prev, k60_dmi_bull = 31.5, 20.9, 24.9, 22.0, True
    k60_ma5, k60_ma20, k60_ma60 = twii_now, twii_now, twii_now
    k60_macd_osc = 10.0
    k60_is_red_bar = True

    p_2x = price_dict.get("00631L", {}).get("close", 36.70)
    p_2x_chg = price_dict.get("00631L", {}).get("chg_pct", 0.0)
    p_1x = price_dict.get("0050", {}).get("close", 108.20)
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

    # ==========================================
    # v36.0 雙軌分流候選池：
    # 軌道 A (短線純技術動能池)：不看營收YoY，只要當日帶量強攻即納入
    # 軌道 B (長線基本＋籌碼池)：不管今日漲跌，只要月營收YoY>=15% 且 大戶>=50% 即納入
    # ==========================================
    candidates = dict(CORE_WATCHLIST)

    # 軌道 A：全市場短線純技術帶量突破股 (最多納 35 檔)
    short_added = 0
    for code, info in price_dict.items():
        if code not in candidates and len(code) == 4 and info["vol_lots"] >= 1800 and info["chg_pct"] >= 2.5 and info["close"] >= 20.0:
            candidates[code] = (info["name"], "短線技術破繭", info["market"])
            short_added += 1
            if short_added >= 35:
                break

    # 軌道 B：全市場長線基本面＋大戶鎖碼績優股 (最多納 30 檔)
    long_added = 0
    for code, info in price_dict.items():
        if code not in candidates and len(code) == 4 and info["vol_lots"] >= 800 and info["close"] >= 30.0:
            yoy_v = rev_dict.get(code, 0.0)
            tdcc_v = tdcc_dict.get(code, 0.0)
            if yoy_v >= 15.0 and tdcc_v >= 50.0:
                candidates[code] = (info["name"], "長線績優鎖碼", info["market"])
                long_added += 1
                if long_added >= 30:
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
            # ⚡ 分頁二：短線操作精選區（純粹看技術面與量能，完全不綁定月營收 YoY！）
            # ==========================================
            if curr_p >= ma20 and (curr_p >= ma60 or ma20 >= ma60 * 0.96) and (not is_heavy_dump):
                is_overheated = (chg_3d >= 10.5) or (stk_j9 >= 90.0 and not kd_dmi_resonance)
                fund_badge = "🔥雙渦輪" if (yoy_val and yoy_val > 0) else "⚡純技術題材"

                # 表一：剛起漲點火榜 (純技術突破 + KD×DMI 共振)
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

                # 表二：N字量縮回測低接榜 (純技術量縮守 10/20MA 且 J<=68)
                elif (-3.8 <= chg_1d <= 2.2) and (vol_ratio <= 0.90) and (curr_p >= ma20) and (stk_j9 <= 68.0) and (pdi >= mdi * 0.9):
                    score = 84 + int((1.0 - vol_ratio) * 15)
                    if pdi > mdi:
                        score += 3
                        pattern = f"⚡ 神準/台光電型：N字量縮守月線(DMI多方) [{fund_badge}]"
                    else:
                        pattern = f"⚡ 神準/台光電型：N字量縮守10/20MA(J冷卻) [{fund_badge}]"
                    r2 = {"蓄勢評分": min(99, score), "名將戰法診斷": pattern, **row_base}
                    list_tab2_pullback.append(r2)

                # 表三：上升軌道推升 & KD鈍化續強 / 3日急拉警戒榜
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
            # 🏛️ 分頁三：長期投資波段區（嚴格要求：基本面 YoY>=10% ＋ 大戶>=48% ＋ 技術面 DMI 多方且未過熱）
            # 徹底與短線急拉股分流！
            # ==========================================
            long_rev_ok = (yoy_val is not None) and (yoy_val >= 10.0)
            long_tdcc_ok = (tdcc_val is not None and tdcc_val >= 48.0) or (code in CORE_WATCHLIST and (tdcc_val is None or tdcc_val >= 40.0))
            long_tech_ok = (curr_p >= ma20) and (ma20 >= ma60) and w_bull and (pdi > mdi)
            # 排除短線已經 3日急拉>=9% 或 J>=88 或 月線乖離>16% 的短線噴出股，確保長線買在穩健波段位階
            long_position_safe = (chg_3d < 9.0) and (stk_j9 <= 88.0) and (bias_ma20_pct <= 16.0)

            if long_rev_ok and long_tdcc_ok and long_tech_ok and long_position_safe and (not is_heavy_dump):
                l_score = 85
                if yoy_val >= 25.0:
                    l_score += 5
                elif yoy_val >= 10.0:
                    l_score += 3
                if tdcc_val and tdcc_val >= 65.0:
                    l_score += 5
                elif tdcc_val and tdcc_val >= 50.0:
                    l_score += 3
                if adx >= 20.0 and adx_up:
                    l_score += 4
                if ma60 >= ma120:
                    l_score += 2

                if kd_dmi_resonance:
                    long_status = "👑 基本面高成長＋大戶鎖碼＋KD×DMI共振起漲"
                elif vol_ratio <= 0.90:
                    long_status = "💎 基本面高成長＋大戶鎖碼＋量縮守月線甜美區"
                else:
                    long_status = "✅ 週K守10週線＋DMI多方控盤＋營收/大戶雙優"

                r_long = {
                    "波段總分": min(100, l_score),
                    "長線格局定性": long_status,
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
# 模組 6：Streamlit 戰情室主介面 (v36.0 短長線雙軌分流版)
# ==========================================
now_str = get_tw_now().strftime("%Y-%m-%d %H:%M:%S")
st.markdown("## 🏛️ 台股每日戰情室 v36.0 (短線純技術動能 vs. 長線基本技術雙優・雙軌分流版)")

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

btn_col1, btn_col2, btn_col3 = st.columns([2, 2, 3])
with btn_col1:
    if st.button("⚡ 秒速刷新盤中即時報價 (無快取直連)", use_container_width=True):
        fetch_mis_realtime_batch.clear()
with btn_col2:
    if st.button("🔄 完整重掃全市場與 60分/日K線", use_container_width=True):
        st.cache_data.clear()
with btn_col3:
    auto_refresh = st.checkbox("⏱️ 開啟盤中每 20 秒自動刷新即時報價", value=False)

with st.spinner("📡 正在同步證交所 MIS ＆ Yahoo v8 即時行情、執行【短線純技術 vs 長線基本技術雙優】雙軌掃描..."):
    df_launch, df_pullback, df_orbit, df_long, vol_note, rt_quotes = scan_all_stocks()
    fleet = fetch_and_analyze_fleet(rt_quotes)

st.caption(f"🕒 台灣時間：{now_str} ｜ 報價引擎：{fleet['quote_src']} ｜ 量能狀態：{vol_note}")

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
        f"【台股每日戰情室 v36.0 短長線雙軌分流數據包】({now_str})",
        f"1. 大盤即時報價與位階：現價 {twii_now:,.1f}點 ({fleet['twii_chg_pct']:+.2f}% | 開盤 {twii_open:,.1f} / 最高 {fleet['twii_high']:,.1f} / 最低 {fleet['twii_low']:,.1f} / 昨收 {twii_prev_c:,.1f}) | 日20MA月線 {twii_ma20:,.1f} ({fleet['bias_20']:+.2f}%) | 日60MA季線 {twii_ma60:,.1f} ({fleet['bias_60']:+.2f}%) | 10週線 {twii_w_ma10:,.1f} | 5月線 {twii_m_ma5:,.1f}",
        f"2. 大盤日K與60分K (KDJ + DMI)：『日K』K={fleet['k9']}/D={fleet['d9']}/J={j_now}({j_trend_str}), DMI({dmi_d_str}) ｜ 『60分K』60m_K={k60_k}/60m_D={k60_d}/60m_J={k60_j}({k60_j_trend}), 60m_DMI({dmi_60_str}), 60分5MA={fleet['k60_ma5']:,.1f}",
        f"3. 母子艦隊現況：0050正2(00631L) 現價 {p_2x:.2f}元 ({fleet['p_2x_chg']:+.2f}%), 持有 {in_lots_2x:.2f}張, 市值 {val_2x:,}元(損益 {pnl_2x:+,}元/{pnl_2x_pct:+.2f}%) | 0050 現價 {p_1x:.2f}元 ({fleet['p_1x_chg']:+.2f}%), 持有 {in_lots_1x:.2f}張, 市值 {val_1x:,}元(損益 {pnl_1x:+,}元/{pnl_1x_pct:+.2f}%) | 總市值 {total_fleet_val:,}元 (總損益 {total_pnl:+,}元/{total_pnl_pct:+.2f}%)",
        f"4. 0050正2 對應目標價：高檔收割區={t_harvest:.2f}元 | 日20MA月線={t_ma20:.2f}元 | 10週線反壓={t_w_ma10:.2f}元 | 日60MA季線={t_ma60:.2f}元 | 5月線防線={t_m_ma5:.2f}元"
    ]
    if not df_launch.empty:
        top_l = [f"{r['代碼']}{r['股名']}({r['名將戰法診斷'][:10]}/現價{r['現價']}/今日{r['今日%']:+}%/量比{r['5MA量比']}/J值{r['日J值']}/{r['DMI多空']}/ADX{r['ADX強度']}/防線{r['鋼鐵防線']})" for _, r in df_launch.head(8).iterrows()]
        lines.append("5. 表一(短線純技術：KD×DMI共振與旱地拔蔥榜)：" + "；".join(top_l))
    if not df_pullback.empty:
        top_p = [f"{r['代碼']}{r['股名']}(現價{r['現價']}/今日{r['今日%']:+}%/量比{r['5MA量比']}/J值{r['日J值']}/{r['DMI多空']}/月線{r['月線承接']})" for _, r in df_pullback.head(8).iterrows()]
        lines.append("6. 表二(短線純技術：N字量縮回測低接榜)：" + "；".join(top_p))
    if not df_long.empty:
        top_g = [f"{r['代碼']}{r['股名']}(現價{r['現價']}/今日{r['今日%']:+}%/J值{r['日J值']}/{r['DMI多空']}/ADX{r['ADX強度']}/大戶{r['400張大戶%']}/YoY{r['月營收YoY']})" for _, r in df_long.head(8).iterrows()]
        lines.append("7. 分頁三(長線基本+籌碼+技術雙優旗艦榜)：" + "；".join(top_g))
    return "\n".join(lines)

with st.expander("🤖 點此展開【Gemini AI 專屬戰情數據包】（點擊右上角 📋 圖示即可一鍵複製貼給 Gemini 分析，免截圖！）", expanded=False):
    st.code(build_ai_snapshot_text(), language="markdown")

tab1, tab2, tab3 = st.tabs([
    "📊 0050正2 ＋ 0050 母子艦隊戰情室 (KDJ＋DMI 雙週期共振)",
    "⚡ 短線操作精選區 (純看技術面與量能・KD×DMI 共振)",
    "🏛️ 長期投資波段區 (基本面YoY>=10% ＋ 大戶>=48% ＋ 技術面雙優)"
])

with tab1:
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown("#### 🏛️ 加權指數 (日K＋60分K)")
        st.metric(
            label=f"台股大盤 ({fleet['date_str']})",
            value=f"{twii_now:,.1f} 點",
            delta=f"{fleet['twii_chg_pct']:+.2f}% (開盤 {twii_open:,.0f} | 高低 {fleet['twii_low']:,.0f}~{fleet['twii_high']:,.0f})"
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
            delta=f"損益 {pnl_2x:+,} 元 ({pnl_2x_pct:+.2f}%)"
        )
        st.caption(
            f"• 持有部位：`{in_lots_2x:.2f} 張` (成本 `{in_cost_2x:.2f} 元`)\n\n"
            f"• 部位市值：`{val_2x:,} 元` (佔比 `{ratio_2x_pct}%`)\n\n"
            f"• 反彈目標：10週線 `{t_w_ma10:.2f}元` / 月線 `{t_ma20:.2f}元`"
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
    shares_at_ma60 = int(net_cash_harvest // max(1.0, t_ma60))
    shares_at_m5 = int(net_cash_harvest // max(1.0, t_m_ma5))
    shares_at_ma240 = int(net_cash_harvest // max(1.0, t_ma240))

    half_1x_net_now = int(round(500 * p_1x * (1.0 - 0.002425)))
    buy_2x_shares_now = int(half_1x_net_now // max(1.0, p_2x * 1.001425))
    rebound_to_w10_net = int(round(buy_2x_shares_now * t_w_ma10 * (1.0 - 0.002425))) - int(round(500 * t1x_w_ma10 * 1.001425))
    rebound_to_ma20_net = int(round(buy_2x_shares_now * t_ma20 * (1.0 - 0.002425))) - int(round(500 * t1x_ma20 * 1.001425))

    in_daily_support_zone = (twii_now <= twii_ma60_buffer) and (twii_now >= twii_m_ma5 * 0.99)

    if twii_now >= twii_harvest_zone or (j_now >= 90.0 and k60_overheated_peaking):
        st.warning(
            f"🔥 **【雙週期高檔收割警報】大盤攻抵高檔收割圈 ({twii_harvest_zone:,.1f} 點) 且日J={j_now}、60分J={k60_j} 彎頭轉折！**\n\n"
            f"• **實戰行動**：逢高賣出 **第 1 張機動 0050正2**（約 **{t_harvest:.2f} 元**），淨回收約 **{net_cash_harvest:,} 元** 入池或轉買 `{int(net_cash_harvest // max(1.0, t1x_harvest))} 股 0050`（曝險降至 `134.7%`）。"
        )
    elif in_daily_support_zone and k60_overheated_peaking:
        st.warning(
            f"⚠️ **【日K雖低檔・但 60分K 遇壓彎頭警戒】**\n\n"
            f"• **雙週期診斷**：大盤逼近 **10週線 (`{twii_w_ma10:,.0f} 點`)** 反壓，且 **60分K 的 `J={k60_j}` 自高檔向下彎頭 (`{k60_j_trend}`)**！\n"
            f"• **實戰行動**：短線不追高加碼正2；若前波已低接增持正2者，可於 `{t_w_ma10:.2f} 元` 附近分批收割短差。"
        )
    elif in_daily_support_zone and k60_bottom_turned_up:
        st.success(
            f"🟢 **【日K守季線戰區 ＋ 60分K (KDJ×DMI) 多頭推進：雙週期共振擊球／續抱訊號！】**\n\n"
            f"• **雙週期共振確認**：\n"
            f"  1. **【日K戰區】**：大盤站回 **日60MA季線 (`{twii_ma60:,.0f} 點`)** 與 **5月線 (`{twii_m_ma5:,.0f} 點`)** 之上，日KDJ (`K={fleet['k9']}/D={fleet['d9']}/J={j_now} {j_trend_str}`) 低檔勾頭金叉！\n"
            f"  2. **【60分K動能】**：**60分KDJ (`K={k60_k}/D={k60_d}/J={k60_j} {k60_j_trend}`) 與 60分DMI (`{dmi_60_str}`) 同步多頭向上**，即使 60分J 進入高檔仍屬強勢鈍化推進，非見頂訊號！\n"
            f"• **實戰行動**：母子艦隊安心續抱（或於季線 `{t_ma60:.2f} 元` 附近維持偏多配置）！第一道反彈目標看 **10週線 (`{twii_w_ma10:,.0f}點` / 正2約 `{t_w_ma10:.2f}元`)**，第二道看 **日20MA月線 (`{twii_ma20:,.0f}點` / 正2約 `{t_ma20:.2f}元`)**；直到 60分J 在反壓區正式向下彎頭死叉才收割短差！"
        )
    elif in_daily_support_zone and (not k60_bottom_turned_up):
        st.warning(
            f"🟡 **【日K與60分K雙重整理・等待 60分K 止跌勾頭扳機（切勿盲目摸底接刀！）】**\n\n"
            f"• **實戰行動**：目前 60分J (`{k60_j}`) 仍在向下整理 (`{k60_j_trend}`)，現有 `3張正2 + 1張0050` 先行守住，等待 60分K 收紅勾頭站上 `60分5MA ({fleet['k60_ma5']:,.0f} 點)` 再右側出擊！"
        )
    elif twii_now > twii_ma60_buffer:
        st.info(
            f"☀️ **【多頭巡航／反彈推進區】大盤位於 `{twii_now:,.1f} 點` ｜ 日J={j_now} ({j_trend_str}) ｜ 60分J={k60_j} ({k60_j_trend})**\n\n"
            f"• **實戰行動**：沿 60分5MA 續抱推進！當攻抵 **10週線 (`{twii_w_ma10:,.0f}點` / 正2 `{t_w_ma10:.2f}元`)** 或 **日20MA月線 (`{twii_ma20:,.0f}點` / 正2 `{t_ma20:.2f}元`)** 且 60分J 向下彎頭時，低檔增持之正2 部位再收割短差歸位。"
        )
    else:
        st.error(
            f"❄️ **【長線防線失守警戒】大盤跌破日60MA季線 ({twii_ma60:,.1f} 點) 與 5月均線 ({twii_m_ma5:,.1f} 點)！**\n\n"
            f"• **實戰行動**：若 3 日內無法站回 `{twii_m_ma5:,.0f} 點`，建議趁反彈將 **2 張 `0050正2` 轉換為 `0050` 或現金**（有效曝險降至 `116.7%`）。"
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
            "雙週期實戰錨點": "⚡ 階段2：反彈短差停利區 (10週線 / 日20MA月線)",
            "日K戰區點位": f"{twii_w_ma10:,.0f} ~ {twii_ma20:,.0f} 點",
            "60分K 扣扳機條件": f"觸及 10週線({twii_w_ma10:,.0f}) 或 日20MA({twii_ma20:,.0f}) 且 60分J 向下彎頭",
            "0050正2 目標價": f"{t_w_ma10:,.2f} ~ {t_ma20:,.2f} 元",
            "實戰指令與滾股試算": f"低檔轉攻之正2於此換回500股0050 (淨賺 +{rebound_to_w10_net:,} ~ +{rebound_to_ma20_net:,}元)"
        },
        {
            "雙週期實戰錨點": "🎯 階段3：季線攻防戰區 (日60MA季線 ±1.5%)",
            "日K戰區點位": f"{twii_ma60:,.1f} 點 (前哨 {twii_ma60_buffer:,.0f})",
            "60分K 扣扳機條件": "站穩季線 ＋ 60分KDJ金叉且60分DMI(+DI>-DI)多頭推進",
            "0050正2 目標價": f"{t_ma60:,.2f} ~ {t_ma60_buf:,.2f} 元",
            "實戰指令與滾股試算": f"高檔賣1張於此買回變 {shares_at_ma60:,} 股 (+{shares_at_ma60 - 1000}股)"
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

    st.markdown("#### 🔄 表 B：0050 ⇄ 0050正2「日K × 60分K (KDJ＋DMI) 四段式換檔」實戰精算表")
    df_switch_modes = pd.DataFrame([
        {
            "艦隊攻守檔位": "🔥 檔位1：高檔減壓鎖利檔",
            "日K戰區 ＋ 60分K扳機條件": f"大盤攻抵 {twii_harvest_zone:,.0f} 點 且 60分J>=85 高檔彎頭死叉",
            "雙核轉換動作": f"賣 1張 正2 ({t_harvest:.2f}元) ➔ 轉買 {int(net_cash_harvest // max(1.0, t1x_harvest))}股 0050",
            "換檔後持倉佔比": "正2 (34.7%) ＋ 0050 (65.3%)",
            "有效曝險": "134.7%",
            "實戰目的與防呆效益": "日K與60分K雙過熱鎖利，保留1倍底倉不踏空"
        },
        {
            "艦隊攻守檔位": "☀️ 檔位2：標準巡航 ＆ 60分K遇壓歸位檔",
            "日K戰區 ＋ 60分K扳機條件": f"反彈碰 10週線({twii_w_ma10:,.0f}) 或 日20MA({twii_ma20:,.0f}) 且 60分J向下彎頭",
            "雙核轉換動作": "維持 3張正2+1張0050 (低檔增持正2者於此換回 500股 0050)",
            "換檔後持倉佔比": f"正2 ({ratio_2x_pct}%) ＋ 0050 ({ratio_1x_pct}%)",
            "有效曝險": f"{effective_leverage}%",
            "實戰目的與防呆效益": "若 60分J 鈍化向上且 +DI>-DI 則續抱；直到碰反壓且 J 彎頭才歸位"
        },
        {
            "艦隊攻守檔位": "🎯 檔位3：季線/5月線 雙週期共振轉攻檔",
            "日K戰區 ＋ 60分K扳機條件": f"守季線({twii_ma60:,.0f})~5月線({twii_m_ma5:,.0f}) 且 60分KDJ金叉＋60分DMI多頭",
            "雙核轉換動作": f"賣 0.5張(500股) 0050 ➔ 轉買 {buy_2x_shares_now:,}股 正2 (約 {p_2x:.2f}元)",
            "換檔後持倉佔比": "正2 (75.0%) ＋ 0050 (25.0%)",
            "有效曝險": "175.0%",
            "實戰目的與防呆效益": f"右側轉攻！彈回10週線賺 +{rebound_to_w10_net:,}元、回月線賺 +{rebound_to_ma20_net:,}元"
        },
        {
            "艦隊攻守檔位": "❄️ 檔位4：跌破季線與5月線 避險防守檔",
            "日K戰區 ＋ 60分K扳機條件": f"實體跌破季線({twii_ma60:,.0f})與5月線({twii_m_ma5:,.0f})滿3日 且 DMI -DI>+DI",
            "雙核轉換動作": "賣 2張 正2 ➔ 轉買 0050 或保留現金",
            "換檔後持倉佔比": "正2 (16.7%) ＋ 0050/現金 (83.3%)",
            "有效曝險": "116.7%",
            "實戰目的與防呆效益": "當長線保護傘破裂且 DMI 空方控盤時，切斷 2倍槓桿重傷"
        }
    ])
    st.dataframe(df_switch_modes, use_container_width=True, hide_index=True)

with tab2:
    st.caption("⚡ 【短線純技術動能模式】不限制月營收 YoY！純粹鎖定「KD×DMI 雙金叉共振(ADX↑)、帶量突破、N字量縮回測」之強勢股，嚴守鋼鐵防線停損停利。")
    
    st.markdown("#### 🔥 【表一：👑 KD×DMI 雙金叉共振(ADX↑) ＆ 階段 3(3) 旱地拔蔥純技術點火榜】")
    if not df_launch.empty:
        st.dataframe(df_launch, use_container_width=True, hide_index=True)
    else:
        st.info("今日盤面暫無符合「KD×DMI共振 或 帶量突破未過熱」之短線點火標的。")

    st.markdown("#### 🎯 【表二：N字洗盤蓄勢 ＆ 拉回月線/十日線低接榜 (純技術量縮守支撐・J<=68冷卻)】")
    if not df_pullback.empty:
        st.dataframe(df_pullback, use_container_width=True, hide_index=True)
    else:
        st.info("今日暫無符合「量縮回測月線有守 ＋ J<=68冷卻」之短線洗盤標的。")

    st.markdown("#### 🛡️ 【表三：上升軌道推升 ＆ KD鈍化(DMI+ADX續強) / 3日急拉警戒榜】")
    if not df_orbit.empty:
        st.dataframe(df_orbit, use_container_width=True, hide_index=True)
    else:
        st.info("今日暫無符合多頭軌道推升之標的。")

with tab3:
    st.markdown("#### 🏛️ 【長期投資波段區：月營收 YoY>=10% ＋ 400張大戶>=48% ＋ 週月多頭/DMI多方控盤旗艦榜】")
    st.caption("篩選基準：與短線飆股徹底分流！嚴格要求基本面高成長 (YoY>=10%)、大戶籌碼安定 (>=48%)、週K守10週線、日DMI (+DI>-DI) 且短線未過熱 (J<=88、3日<9%)。")
    if not df_long.empty:
        st.dataframe(df_long, use_container_width=True, hide_index=True)
    else:
        st.info("目前無同時符合「高營收成長 ＋ 大戶鎖碼 ＋ 技術面多頭未過熱」之長線波段標的。")

if auto_refresh:
    time.sleep(20)
    fetch_mis_realtime_batch.clear()
    st.rerun()
