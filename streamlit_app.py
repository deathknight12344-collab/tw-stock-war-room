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

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(
    page_title="台股每日戰情室 v42.0",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ==========================================
# 模組 0：母子艦隊「真實資金流三維帳本」記憶引擎
# ==========================================
CONFIG_FILE = "fleet_portfolio_v42.json"
DEFAULT_PORTFOLIO = {
    "init_lots_2x": 3.00,
    "init_cost_2x": 34.68,
    "init_lots_1x": 1.00,
    "init_cost_1x": 101.90,
    "extra_cash_in": 6850.0,
    "realized_pnl_1x": 2670.0,
    "lots_2x": 5.00,
    "cost_2x": 35.04,
    "lots_1x": 0.40,
    "cost_1x": 101.90,
    "cash_pool": 0.0
}

def load_portfolio_config():
    cfg = dict(DEFAULT_PORTFOLIO)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                for k in cfg:
                    if k in saved:
                        cfg[k] = float(saved[k])
        except Exception:
            pass
    qp = st.query_params
    for k in cfg:
        if k in qp:
            try:
                cfg[k] = float(qp[k])
            except Exception:
                pass
    return cfg

def save_portfolio_config(cfg_dict):
    clean_cfg = {k: round(float(v), 2) for k, v in cfg_dict.items()}
    for k_int in ["extra_cash_in", "realized_pnl_1x", "cash_pool"]:
        clean_cfg[k_int] = round(float(cfg_dict.get(k_int, 0.0)), 0)
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(clean_cfg, f)
    except Exception:
        pass
    try:
        for k, v in clean_cfg.items():
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
    if df.empty:
        return df
    def color_tw_val(val):
        try:
            if isinstance(val, (int, float)):
                v = float(val)
            else:
                s = str(val).replace("%", "").replace(",", "").replace("+", "").replace("元", "").strip()
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
        return df.style.map(color_tw_val, subset=target_cols).format(fmt_dict, na_rep="-")
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
        "Cache-Control": "no-cache, no-store, must-revalidate"
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
                        "open": round(o_now, 2), "close": round(p_now, 2),
                        "high": round(max(h_now, p_now), 2), "low": round(min(l_now, p_now), 2),
                        "prev_close": round(y_prev, 2), "chg_pct": chg_pct, "vol_lots": 0,
                        "time": dt_obj.strftime("%H:%M:%S"), "date": dt_obj.strftime("%Y%m%d")
                    }
    except Exception:
        pass
    return None

@st.cache_data(ttl=10, show_spinner=False)
def fetch_mis_realtime_batch(code_market_tuples):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://mis.twse.com.tw/stock/index.jsp",
        "Cache-Control": "no-cache"
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
                for item in r.json().get("msgArray", []):
                    c_code = str(item.get("c", "")).strip()
                    p_now = parse_mis_price(item, is_index=(c_code == "t00"))
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
                        "open": round(o_now, 2), "close": round(p_now, 2),
                        "high": round(max(h_now, p_now), 2), "low": round(min(l_now, p_now), 2),
                        "prev_close": round(y_prev, 2), "chg_pct": chg_pct, "vol_lots": v_lots,
                        "time": str(item.get("t", "")), "date": str(item.get("d", ""))
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
# 模組 3-A：證交所/櫃買中心/集保中心 OpenAPI
# ==========================================
@st.cache_data(ttl=600, show_spinner=False)
def fetch_official_market_data():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    price_dict, tdcc_dict, rev_dict = {}, {}, {}
    twii_official = {"close": 0.0, "chg_pct": 0.0, "date": ""}

    try:
        r_idx = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK", headers=headers, timeout=8, verify=False)
        if r_idx.status_code == 200:
            idx_list = r_idx.json()
            if len(idx_list) > 0:
                last_row = idx_list[-1]
                idx_c = float(str(last_row.get("TAIEX", "0")).replace(",", ""))
                idx_chg = float(str(last_row.get("Change", "0")).replace(",", "").replace("+", ""))
                prev_idx = idx_c - idx_chg
                idx_pct = round((idx_chg / prev_idx) * 100, 2) if prev_idx > 0 else 0.0
                twii_official = {"close": round(idx_c, 1), "chg_pct": idx_pct, "date": str(last_row.get("Date", ""))}
    except Exception:
        pass

    try:
        r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=8, verify=False)
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
                            price_dict[code] = {"name": str(row.get("Name", "")).strip(), "close": c_price, "chg_pct": chg_pct, "vol_lots": vol_lots, "market": "TW"}
                    except Exception:
                        continue
    except Exception:
        pass

    try:
        r_tpex = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", headers=headers, timeout=8, verify=False)
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
                            price_dict[code] = {"name": str(row.get("CompanyName", "")).strip(), "close": c_price, "chg_pct": chg_pct, "vol_lots": vol_lots, "market": "TWO"}
                    except Exception:
                        continue
    except Exception:
        pass

    try:
        r_csv = requests.get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", headers=headers, timeout=12, verify=False)
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
                for k, v in df_sub.groupby(col_code)["pct_num"].sum().items():
