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
    page_title="台股每日戰情室 v47.0",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ==========================================
# 注入戰情室專屬大字體與高對比視覺 CSS 樣式
# ==========================================
st.markdown("""
<style>
.war-hero-box {
    background: linear-gradient(135deg, #0f2027 0%, #203a43 50%, #2c5364 100%);
    color: #ffffff;
    padding: 22px 26px;
    border-radius: 14px;
    border-left: 8px solid #00e676;
    box-shadow: 0 6px 18px rgba(0,0,0,0.22);
    margin-bottom: 20px;
}
.war-hero-box-warn {
    background: linear-gradient(135deg, #2b1d1d 0%, #3d251e 50%, #4a2c2a 100%);
    color: #ffffff;
    padding: 22px 26px;
    border-radius: 14px;
    border-left: 8px solid #ff9800;
    box-shadow: 0 6px 18px rgba(0,0,0,0.28);
    margin-bottom: 20px;
}
.war-score-title {
    font-size: 26px;
    font-weight: 900;
    color: #ffd54f;
    margin-bottom: 6px;
    letter-spacing: 0.5px;
}
.war-circuit-trace {
    font-size: 16px;
    font-weight: 700;
    color: #ffab91;
    background: rgba(0, 0, 0, 0.35);
    padding: 6px 12px;
    border-radius: 6px;
    margin-bottom: 10px;
    display: inline-block;
}
.war-grade-banner {
    font-size: 20px;
    font-weight: 800;
    padding: 8px 14px;
    border-radius: 8px;
    margin-bottom: 14px;
    display: inline-block;
}
.war-dim-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 10px;
    margin: 12px 0 16px 0;
}
.war-dim-item {
    background: rgba(255, 255, 255, 0.08);
    padding: 10px 14px;
    border-radius: 8px;
    font-size: 15.5px;
    line-height: 1.5;
}
.war-action-box {
    background: rgba(0, 0, 0, 0.32);
    padding: 14px 18px;
    border-radius: 10px;
    border: 1px solid rgba(255, 213, 79, 0.45);
    font-size: 17px;
    line-height: 1.65;
}
.radar-card {
    background: #161b22;
    color: #f0f6fc;
    padding: 16px 18px;
    border-radius: 12px;
    border: 1px solid #30363d;
    box-shadow: 0 4px 12px rgba(0,0,0,0.18);
    height: 100%;
}
.radar-card-highlight {
    background: #1a1f2c;
    color: #ffffff;
    padding: 16px 18px;
    border-radius: 12px;
    border: 2px solid #f59e0b;
    box-shadow: 0 4px 14px rgba(245, 158, 11, 0.2);
    height: 100%;
}
.radar-title {
    font-size: 16.5px;
    font-weight: 800;
    color: #9ca3af;
    margin-bottom: 6px;
}
.radar-big-val {
    font-size: 30px;
    font-weight: 900;
    line-height: 1.2;
    margin: 4px 0 10px 0;
}
.radar-sub-highlight {
    font-size: 17px;
    font-weight: 800;
    padding: 6px 10px;
    border-radius: 6px;
    margin: 6px 0;
    display: block;
}
.radar-detail-row {
    font-size: 15.5px;
    font-weight: 600;
    color: #e5e7eb;
    margin: 5px 0;
    line-height: 1.45;
}
.radar-score-pill {
    display: inline-block;
    margin-top: 8px;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 15.5px;
    font-weight: 800;
    background: #2563eb;
    color: #ffffff;
}
</style>
""", unsafe_allow_html=True)

# ==========================================
# 模組 0：母子艦隊 ＆ 00400A 存股部位記憶引擎
# ==========================================
CONFIG_FILE = "fleet_portfolio_v47.json"
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
    "cash_pool": 0.0,
    # 00400A 主動國泰動能高息 存股部位設定
    "lots_400a": 1.00,
    "cost_400a": 12.00
}

def load_portfolio_config():
    cfg = dict(DEFAULT_PORTFOLIO)
    for fname in [CONFIG_FILE, "fleet_portfolio_v46.json", "fleet_portfolio_v45.json", "fleet_portfolio_v44.json"]:
        if os.path.exists(fname):
            try:
                with open(fname, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    for k in cfg:
                        if k in saved:
                            cfg[k] = float(saved[k])
                break
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

    target_cols = [c for c in ["今日%", "3日%", "月營收YoY", "對00400A今日淨值貢獻"] if c in df.columns]
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
# 模組 2：證交所 MIS 無快取直連 ＋ Yahoo v8 1分K ＋ 證交所 ETF 即時預估淨值 (all_etf.txt) 引擎
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

def fetch_yahoo_v8_realtime(ticker_symbol, decimals=2):
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
                        "open": round(o_now, decimals),
                        "close": round(p_now, decimals),
                        "high": round(max(h_now, p_now), decimals),
                        "low": round(min(l_now, p_now), decimals),
                        "prev_close": round(y_prev, decimals),
                        "chg_pct": chg_pct,
                        "vol_lots": 0,
                        "time": dt_obj.strftime("%H:%M:%S"),
                        "date": dt_obj.strftime("%Y%m%d"),
                        "ts": mkt_time
                    }
    except Exception:
        pass
    return None

@st.cache_data(ttl=15, show_spinner=False)
def fetch_twse_etf_inav(target_etf_code="00400A"):
    """
    直接連線證交所 MIS 官方 ETF 即時預估淨值與折溢價資料流 (all_etf.txt)：
    欄位定義：a=代號, b=名稱, e=市價, f=預估淨值(iNAV), g=預估折溢價(%), h=前日淨值, i=日期, j=時間
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://mis.twse.com.tw/stock/etf_nav.jsp",
        "Cache-Control": "no-cache"
    }
    url = f"https://mis.twse.com.tw/stock/data/all_etf.txt?_={int(time.time() * 1000)}"
    try:
        r = requests.get(url, headers=headers, timeout=5, verify=False)
        if r.status_code == 200:
            data = r.json()
            for grp in data.get("a1", []):
                for item in grp.get("msgArray", []):
                    if str(item.get("a", "")).strip() == target_etf_code:
                        p_mkt = float(str(item.get("e", "0")).replace(",", ""))
                        nav_est = float(str(item.get("f", "0")).replace(",", ""))
                        prem_str = str(item.get("g", "0")).replace("%", "").replace(",", "").strip()
                        prem_pct = float(prem_str) if prem_str not in ["", "-", "--"] else 0.0
                        prev_nav = float(str(item.get("h", "0")).replace(",", ""))
                        if nav_est > 0:
                            if p_mkt > 0 and prem_pct == 0.0:
                                prem_pct = round(((p_mkt - nav_est) / nav_est) * 100.0, 2)
                            return {
                                "mkt_price": round(p_mkt, 2),
                                "est_nav": round(nav_est, 2),
                                "prev_nav": round(prev_nav, 2) if prev_nav > 0 else round(nav_est, 2),
                                "prem_pct": round(prem_pct, 2),
                                "time": str(item.get("j", "")),
                                "src": "🟢 證交所 MIS 官方即時預估淨值直連"
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

    for code_key, yf_sym in [("t00", "%5ETWII"), ("00631L", "00631L.TW"), ("0050", "0050.TW"), ("00400A", "00400A.TW")]:
        yv8 = fetch_yahoo_v8_realtime(yf_sym, decimals=2)
        if yv8 and yv8["close"] > 0:
            if code_key not in rt_dict:
                rt_dict[code_key] = yv8
            else:
                if rt_dict[code_key].get("open", 0) <= 0:
                    rt_dict[code_key]["open"] = yv8["open"]
                if abs(rt_dict[code_key]["close"] - rt_dict[code_key]["prev_close"]) < 0.01 and abs(yv8["close"] - yv8["prev_close"]) > 0.01:
                    rt_dict[code_key] = yv8

    fx_v8 = fetch_yahoo_v8_realtime("TWD=X", decimals=3)
    if fx_v8 and fx_v8["close"] > 10.0:
        rt_dict["USDTWD"] = fx_v8

    return rt_dict

# ==========================================
# 模組 3-A：證交所/櫃買中心/集保中心 OpenAPI 基本面與盤後總表
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
                if (len(code) == 4 and code.isdigit()) or code in ["0050", "00631L", "00400A"]:
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
                grouped_dict = df_sub.groupby(col_code)["pct_num"].sum().to_dict()
                tdcc_dict = {str(k): round(float(v), 1) for k, v in grouped_dict.items() if 0.0 < float(v) <= 100.0}
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
# 模組 3-B：期交所 (每日14:00後即時更新) ＆ 證交所純個股融資維持率 ＆ 外資空單佈空成本推算引擎
# ==========================================
def estimate_foreign_short_cost_and_target(highs, lows, closes, volumes):
    if len(closes) < 20:
        curr = float(closes[-1]) if len(closes) > 0 else 39690.7
        return round(curr * 1.12, 0), round(curr * 1.07, 0), round(curr * 1.03, 0)

    h20 = np.array(highs[-20:], dtype=float)
    l20 = np.array(lows[-20:], dtype=float)
    c20 = np.array(closes[-20:], dtype=float)
    v20 = np.array(volumes[-20:], dtype=float)
    if np.sum(v20) <= 0:
        v20 = np.ones_like(c20)

    typ_p = (h20 + l20 + c20) / 3.0
    med_p = np.median(typ_p)
    upper_wick = h20 - c20
    weights = v20 * (1.0 + np.maximum(0.0, (typ_p - med_p) / max(1.0, med_p)) * 8.0 + (upper_wick / np.maximum(1.0, c20)) * 15.0)
    short_cost_idx = float(np.sum(typ_p * weights) / max(1.0, np.sum(weights)))
    ma20_val = float(np.mean(c20))
    ma60_val = float(np.mean(closes[-60:])) if len(closes) >= 60 else ma20_val
    est_short_cost = round(0.55 * short_cost_idx + 0.25 * ma20_val + 0.20 * ma60_val, 0)

    target_cover_1 = round(est_short_cost * 0.960, 0)
    target_cover_2 = round(est_short_cost * 0.925, 0)
    return est_short_cost, target_cover_1, target_cover_2

def compute_dynamic_pure_stock_maint(twii_now, ma20, ma60):
    cost_anchor = 0.70 * max(1.0, ma20) + 0.30 * max(1.0, ma60)
    ratio = twii_now / max(1.0, cost_anchor)
    dynamic_maint = 166.7 * (ratio ** 1.95)
    return round(max(115.0, min(195.0, dynamic_maint)), 1)

@st.cache_data(ttl=60, show_spinner=False)
def fetch_auto_derivatives_base(price_dict):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cache-Control": "no-cache"
    }
    res = {
        "fini_tx_oi": None,
        "fini_tx_chg": None,
        "sitc_tx_oi": None,
        "pc_ratio": None,
        "pc_ratio_chg": 0.0,
        "warrant_hedge_yi": None,
        "official_stock_maint": None,
        "official_maint_date": "",
        "stock_margin_chg_yi": 0.0,
        "fut_date": "",
        "twse_date": ""
    }

    now_tw = get_tw_now()
    start_offset = 0

    try:
        r_pcr = requests.get("https://openapi.taifex.com.tw/v1/PutCallRatio", headers=headers, timeout=6, verify=False)
        if r_pcr.status_code == 200:
            pcr_list = r_pcr.json()
            if len(pcr_list) > 0:
                last_row = pcr_list[-1]
                raw_pcr = last_row.get("PutCallOpenInterestRatio", last_row.get("PutCallRatio", None))
                if raw_pcr is not None:
                    val = float(str(raw_pcr).replace(",", "").strip())
                    if 30.0 <= val <= 220.0:
                        res["pc_ratio"] = round(val, 1)
                        if len(pcr_list) >= 2:
                            prev_raw = pcr_list[-2].get("PutCallOpenInterestRatio", pcr_list[-2].get("PutCallRatio", val))
                            res["pc_ratio_chg"] = round(val - float(str(prev_raw).replace(",", "")), 1)
    except Exception:
        pass

    if res["pc_ratio"] is None:
        try:
            q_end = now_tw.strftime("%Y/%m/%d")
            q_start = (now_tw - datetime.timedelta(days=10)).strftime("%Y/%m/%d")
            pcr_url = f"https://www.taifex.com.tw/cht/3/pcRatioDown?queryStartDate={q_start}&queryEndDate={q_end}"
            r_pcr_csv = requests.get(pcr_url, headers=headers, timeout=6, verify=False)
            if r_pcr_csv.status_code == 200 and len(r_pcr_csv.content) > 80:
                txt = r_pcr_csv.content.decode("cp950", errors="ignore")
                df_p = pd.read_csv(io.StringIO(txt))
                if len(df_p) > 0:
                    val = float(str(df_p.iloc[-1, -1]).replace(",", "").strip())
                    if 30.0 <= val <= 220.0:
                        res["pc_ratio"] = round(val, 1)
                        if len(df_p) >= 2:
                            prev_v = float(str(df_p.iloc[-2, -1]).replace(",", "").strip())
                            res["pc_ratio_chg"] = round(val - prev_v, 1)
        except Exception:
            pass

    try:
        q_end = now_tw.strftime("%Y/%m/%d")
        q_start = (now_tw - datetime.timedelta(days=7)).strftime("%Y/%m/%d")
        fut_url = f"https://www.taifex.com.tw/cht/3/futContractsDateDown?queryStartDate={q_start}&queryEndDate={q_end}"
        r_fut = requests.get(fut_url, headers=headers, timeout=8, verify=False)
        if r_fut.status_code == 200 and len(r_fut.content) > 200:
            txt_f = r_fut.content.decode("cp950", errors="ignore")
            df_f = pd.read_csv(io.StringIO(txt_f))
            if len(df_f.columns) >= 14:
                df_tx = df_f[df_f.iloc[:, 1].astype(str).str.contains("臺股期貨|台股期貨", na=False)]
                if not df_tx.empty:
                    dates = df_tx.iloc[:, 0].unique()
                    latest_d = dates[-1]
                    res["fut_date"] = str(latest_d).strip()
                    df_latest = df_tx[df_tx.iloc[:, 0] == latest_d]
                    for _, row in df_latest.iterrows():
                        ident = str(row.iloc[2]).strip()
                        oi_net = int(float(str(row.iloc[13]).replace(",", "").strip()))
                        net_trade = int(float(str(row.iloc[7]).replace(",", "").strip()))
                        if "外資" in ident:
                            res["fini_tx_oi"] = oi_net
                            res["fini_tx_chg"] = net_trade
                        elif "投信" in ident:
                            res["sitc_tx_oi"] = oi_net
                    if len(dates) >= 2:
                        prev_d = dates[-2]
                        df_prev = df_tx[df_tx.iloc[:, 0] == prev_d]
                        for _, row in df_prev.iterrows():
                            if "外資" in str(row.iloc[2]) and res["fini_tx_oi"] is not None:
                                prev_oi = int(float(str(row.iloc[13]).replace(",", "").strip()))
                                res["fini_tx_chg"] = res["fini_tx_oi"] - prev_oi
    except Exception:
        pass

    for d_back in range(start_offset, 6):
        chk_d = (now_tw - datetime.timedelta(days=d_back)).strftime("%Y%m%d")
        try:
            url_bfi = f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate={chk_d}&type=day&response=json"
            r_bfi = requests.get(url_bfi, headers=headers, timeout=6, verify=False)
            if r_bfi.status_code == 200:
                b_json = r_bfi.json()
                b_data = b_json.get("data", [])
                if b_data and len(b_data) >= 3:
                    for row in b_data:
                        if "避險" in str(row[0]):
                            diff_ntd = float(str(row[3]).replace(",", "").strip())
                            res["warrant_hedge_yi"] = round(diff_ntd / 100000000.0, 2)
                            res["twse_date"] = chk_d
                            break
                    if res["warrant_hedge_yi"] is not None:
                        break
        except Exception:
            continue

    for d_back in range(start_offset, 6):
        chk_d = (now_tw - datetime.timedelta(days=d_back)).strftime("%Y%m%d")
        try:
            url_m = f"https://www.twse.com.tw/rwd/zh/margin/MI_MARGN?date={chk_d}&selectType=ALL&response=json"
            r_m = requests.get(url_m, headers=headers, timeout=8, verify=False)
            if r_m.status_code == 200:
                m_json = r_m.json()
                tables = m_json.get("tables", [])
                if len(tables) >= 2:
                    sum_rows = tables[0].get("data", [])
                    total_loan_k = 0.0
                    for s_row in sum_rows:
                        if "融資金額" in str(s_row[0]):
                            prev_l = float(str(s_row[4]).replace(",", ""))
                            curr_l = float(str(s_row[5]).replace(",", ""))
                            total_loan_k = curr_l
                            res["stock_margin_chg_yi"] = round((curr_l - prev_l) / 100000.0, 2)
                            break
                    stk_rows = tables[1].get("data", [])
                    if len(stk_rows) > 400 and total_loan_k > 0:
                        pure_stk_mv = 0.0
                        etf_mv = 0.0
                        for row in stk_rows:
                            code = str(row[0]).strip()
                            try:
                                lots = float(str(row[6]).replace(",", "").strip())
                            except Exception:
                                continue
                            if lots <= 0:
                                continue
                            p_c = price_dict.get(code, {}).get("close", 0.0)
                            if p_c <= 0:
                                continue
                            if len(code) == 4 and code.isdigit() and (not code.startswith("00")):
                                pure_stk_mv += lots * 1000.0 * p_c
                            elif code.startswith("00"):
                                etf_mv += lots * 1000.0 * p_c
                        pure_stk_loan = max(1.0, (total_loan_k * 1000.0) - (etf_mv / 1.62))
                        calc_m = round((pure_stk_mv / pure_stk_loan) * 100.0, 1)
                        if 118.0 <= calc_m <= 190.0:
                            res["official_stock_maint"] = calc_m
                            res["official_maint_date"] = chk_d
                            break
        except Exception:
            continue

    return res

# ==========================================
# 模組 4：長線「電子權值股」＋「金融金控」＋「00400A 主動國泰動能高息 核心成分股權重資料庫」
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
    "6285": ("啟碁", "👑電子權值・車用與衛星網通", "TW"),
    "3037": ("欣興", "👑電子權值・ABF載板龍頭", "TW"),
    "6223": ("旺矽", "👑電子權值・探針卡龍頭", "TWO"),
    "2360": ("致茂", "👑電子權值・半導體量測設備", "TW")
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

# 00400A 主動國泰動能高息 最新揭露之前 15 大核心成分股與權重 (%)
ETF_00400A_CONSTITUENTS = {
    "2330": ("台積電", 9.08, "TW"),
    "2454": ("聯發科", 6.80, "TW"),
    "2059": ("川湖", 4.18, "TW"),
    "2383": ("台光電", 3.73, "TW"),
    "1303": ("南亞", 3.72, "TW"),
    "5274": ("信驊", 3.46, "TWO"),
    "3653": ("健策", 3.20, "TW"),
    "3037": ("欣興", 3.06, "TW"),
    "3017": ("奇鋐", 3.02, "TW"),
    "2603": ("長榮", 2.72, "TW"),
    "6223": ("旺矽", 2.42, "TWO"),
    "2368": ("金像電", 2.28, "TW"),
    "2360": ("致茂", 2.25, "TW"),
    "3443": ("創意", 2.23, "TW"),
    "3661": ("世芯-KY", 2.04, "TW")
}

CORE_WATCHLIST = {**ELECTRONICS_BLUE_CHIPS, **FINANCIAL_BLUE_CHIPS}
for c_code, (c_name, _, c_mkt) in ETF_00400A_CONSTITUENTS.items():
    if c_code not in CORE_WATCHLIST:
        CORE_WATCHLIST[c_code] = (c_name, "💎00400A核心成分股", c_mkt)

CONSTRUCTION_BLACKLIST = {"1808", "2501", "2504", "2505", "2506", "2509", "2511", "2515", "2516", "2520", "2524", "2527", "2528", "2530", "2534", "2535", "2536", "2537", "2538", "2539", "2540", "2542", "2543", "2545", "2546", "2547", "2548", "2597", "5522", "5534"}

def is_traditional_or_construction(code):
    if code in CONSTRUCTION_BLACKLIST:
        return True
    if len(code) == 4 and code[:2] in ["11", "12", "13", "14", "18", "19", "20", "21", "25", "26", "27", "29"]:
        return True
    return False

# ==========================================
# 模組 5：多週期 KDJ ＋ 威爾德 DMI(14) ＋ 新台幣匯率 ＋ 00400A 穿透式定價核心
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
    tickers = ["^TWII", "00631L.TW", "0050.TW", "00400A.TW", "TWD=X"]
    try:
        return yf.download(tickers, period="2y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=120, show_spinner=False)
def download_fleet_60m():
    tickers = ["^TWII", "00631L.TW", "0050.TW", "00400A.TW"]
    try:
        return yf.download(tickers, period="2mo", interval="60m", progress=False, group_by="ticker", auto_adjust=False)
    except Exception:
        return pd.DataFrame()

def fetch_and_analyze_fleet(rt_quotes):
    price_dict, _, _, twii_official = fetch_official_market_data()
    data = download_fleet_history()
    data_60m = download_fleet_60m()
    today_str = get_tw_now().strftime("%Y-%m-%d")

    twii_now = twii_official["close"] if twii_official["close"] > 0 else 39690.7
    twii_chg_pct = twii_official["chg_pct"]
    twii_open = twii_now
    twii_high = twii_now
    twii_low = twii_now
    twii_prev_c = twii_now
    ma5 = round(twii_now * 1.07, 1)
    ma10 = round(twii_now * 1.11, 1)
    ma20 = round(twii_now * 1.16, 1)
    ma60 = round(twii_now * 1.17, 1)
    ma120 = round(twii_now * 1.14, 1)
    ma240 = round(twii_now * 0.978, 1)
    w_ma10 = round(twii_now * 1.16, 1)
    m_ma5 = round(twii_now * 1.11, 1)

    ma5_prev, ma10_prev, ma20_prev, ma60_prev, ma120_prev, ma240_prev = ma5, ma10, ma20, ma60, ma120, ma240
    deduct_5d, deduct_10d, deduct_20d, deduct_60d, deduct_120d = ma5, ma10, ma20, ma60, ma120

    bb_upper = round(twii_now * 1.22, 1)
    bb_lower = round(twii_now * 0.98, 1)
    k9, d9, j9, j9_prev, kd_cross_d = 11.2, 14.5, 12.2, 2.3, False
    pdi_d, mdi_d, adx_d, adx_d_prev, dmi_bull_d = 9.0, 50.2, 36.5, 33.5, False
    macd_hist = -490.0
    date_str = today_str
    quote_src = "Yahoo Finance / 證交所"

    est_short_cost = round(twii_now * 1.17, 0)
    target_cover_1 = round(est_short_cost * 0.96, 0)
    target_cover_2 = round(est_short_cost * 0.925, 0)

    k60_k, k60_d, k60_j, k60_j_prev, k60_kd_cross = 35.1, 28.6, 48.3, 48.4, True
    k60_pdi, k60_mdi, k60_adx, k60_adx_prev, k60_dmi_bull = 14.5, 42.0, 35.0, 32.0, False
    k60_ma5, k60_ma20, k60_ma60 = round(twii_now * 1.001, 1), round(twii_now * 1.059, 1), round(twii_now * 1.11, 1)
    k60_macd_osc = -10.0
    k60_is_red_bar = True

    fx_now = 32.837
    fx_prev = 33.098
    fx_chg_ntd = -0.261
    fx_ma5 = 32.717
    fx_ma20 = 32.447

    p_2x = price_dict.get("00631L", {}).get("close", 26.45)
    p_2x_chg = price_dict.get("00631L", {}).get("chg_pct", -9.45)
    p_1x = price_dict.get("0050", {}).get("close", 92.57)
    p_1x_chg = price_dict.get("0050", {}).get("chg_pct", -4.71)

    # 00400A 主動國泰動能高息 預設基準值 (2026/11/06 真實收盤 11.18元、昨收 11.94元、最低 10.78元)
    p_400a = price_dict.get("00400A", {}).get("close", 11.18)
    p_400a_chg = price_dict.get("00400A", {}).get("chg_pct", -6.37)
    p_400a_open = 10.78
    p_400a_high = 11.48
    p_400a_low = 10.78
    p_400a_prev = 11.94
    ma5_400a, ma10_400a, ma20_400a, ma60_400a = 11.82, 12.45, 13.38, 13.72
    k_400a, d_400a, j_400a = 11.5, 16.8, 0.9

    try:
        if not data.empty:
            df_twii = data["^TWII"].dropna(subset=["Close"]).copy()
            if len(df_twii) >= 25:
                o_twii = df_twii["Open"].values.astype(float)
                c_twii = df_twii["Close"].values.astype(float)
                h_twii = df_twii["High"].values.astype(float)
                l_twii = df_twii["Low"].values.astype(float)
                v_twii = df_twii["Volume"].values.astype(float)
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
                        v_twii = np.append(v_twii, v_twii[-1])
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
                ma120 = round(float(np.mean(c_twii[-120:])), 1) if len(c_twii) >= 120 else ma60
                ma240 = round(float(np.mean(c_twii[-240:])), 1) if len(c_twii) >= 240 else ma120

                ma5_prev = round(float(np.mean(c_twii[-6:-1])), 1) if len(c_twii) >= 6 else ma5
                ma10_prev = round(float(np.mean(c_twii[-11:-1])), 1) if len(c_twii) >= 11 else ma10
                ma20_prev = round(float(np.mean(c_twii[-21:-1])), 1) if len(c_twii) >= 21 else ma20
                ma60_prev = round(float(np.mean(c_twii[-61:-1])), 1) if len(c_twii) >= 61 else ma60
                ma120_prev = round(float(np.mean(c_twii[-121:-1])), 1) if len(c_twii) >= 121 else ma120
                ma240_prev = round(float(np.mean(c_twii[-241:-1])), 1) if len(c_twii) >= 241 else ma240

                deduct_5d = round(float(c_twii[-5]), 1) if len(c_twii) >= 5 else ma5
                deduct_10d = round(float(c_twii[-10]), 1) if len(c_twii) >= 10 else ma10
                deduct_20d = round(float(c_twii[-20]), 1) if len(c_twii) >= 20 else ma20
                deduct_60d = round(float(c_twii[-60]), 1) if len(c_twii) >= 60 else ma60
                deduct_120d = round(float(c_twii[-120]), 1) if len(c_twii) >= 120 else ma120

                std20 = float(np.std(c_twii[-20:]))
                bb_upper = round(ma20 + 2.0 * std20, 1)
                bb_lower = round(ma20 - 2.0 * std20, 1)

                est_short_cost, target_cover_1, target_cover_2 = estimate_foreign_short_cost_and_target(
                    h_twii, l_twii, c_twii, v_twii
                )

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

            if "TWD=X" in data:
                df_fx = data["TWD=X"].dropna(subset=["Close"]).copy()
                if len(df_fx) >= 20:
                    c_fx = df_fx["Close"].values.astype(float)
                    if "USDTWD" in rt_quotes and rt_quotes["USDTWD"]["close"] > 10.0:
                        fx_now = round(float(rt_quotes["USDTWD"]["close"]), 3)
                        fx_prev = round(float(rt_quotes["USDTWD"]["prev_close"]), 3)
                        c_fx[-1] = fx_now
                    else:
                        fx_now = round(float(c_fx[-1]), 3)
                        fx_prev = round(float(c_fx[-2]), 3) if len(c_fx) >= 2 else fx_now
                    fx_chg_ntd = round(fx_now - fx_prev, 3)
                    fx_ma5 = round(float(np.mean(c_fx[-5:])), 3)
                    fx_ma20 = round(float(np.mean(c_fx[-20:])), 3)

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

            # 解析 00400A 歷史與即時 K 線
            if "00400A" in rt_quotes and rt_quotes["00400A"]["close"] > 0:
                rt_400 = rt_quotes["00400A"]
                p_400a = rt_400["close"]
                p_400a_chg = rt_400["chg_pct"]
                p_400a_open = rt_400.get("open", p_400a)
                p_400a_high = rt_400.get("high", p_400a)
                p_400a_low = rt_400.get("low", p_400a)
                p_400a_prev = rt_400.get("prev_close", 11.94)
            if "00400A.TW" in data:
                df_400 = data["00400A.TW"].dropna(subset=["Close"]).copy()
                if len(df_400) >= 10:
                    c_400 = df_400["Close"].values.astype(float)
                    h_400 = df_400["High"].values.astype(float)
                    l_400 = df_400["Low"].values.astype(float)
                    if abs(c_400[-1] - p_400a) > 0.01:
                        c_400[-1] = p_400a
                        h_400[-1] = max(h_400[-1], p_400a_high)
                        l_400[-1] = min(l_400[-1], p_400a_low)
                    ma5_400a = round(float(np.mean(c_400[-5:])), 2)
                    ma10_400a = round(float(np.mean(c_400[-10:])), 2)
                    ma20_400a = round(float(np.mean(c_400[-20:])), 2) if len(c_400) >= 20 else ma10_400a
                    ma60_400a = round(float(np.mean(c_400[-60:])), 2) if len(c_400) >= 60 else ma20_400a
                    k_400a, d_400a, j_400a, _, _ = compute_kdj(h_400[-60:], l_400[-60:], c_400[-60:])
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

    bias_5 = round((twii_now - ma5) / max(1.0, ma5) * 100, 2)
    bias_10 = round((twii_now - ma10) / max(1.0, ma10) * 100, 2)
    bias_20 = round((twii_now - ma20) / max(1.0, ma20) * 100, 2)
    bias_60 = round((twii_now - ma60) / max(1.0, ma60) * 100, 2)
    bias_120 = round((twii_now - ma120) / max(1.0, ma120) * 100, 2)

    # 偵測大盤是否出現「回測 240MA 年線並拉出 >1.2% 神針探底長下影線」
    lower_shadow_pts = round(min(twii_open, twii_now) - twii_low, 1)
    lower_shadow_pct = round(lower_shadow_pts / max(1.0, twii_low) * 100.0, 2)
    tested_ma240_and_held = (twii_low <= ma240 * 1.015) and (twii_now >= ma240) and (lower_shadow_pct >= 1.2)

    raw_deriv = fetch_auto_derivatives_base(price_dict)

    model_maint = compute_dynamic_pure_stock_maint(twii_now, ma20, ma60)
    if raw_deriv["official_stock_maint"] is not None:
        live_maint = round(raw_deriv["official_stock_maint"] * 0.35 + model_maint * 0.65, 1)
        maint_src = f"證交所MI_MARGN({raw_deriv['official_maint_date']})＋即時股價動態精算"
    else:
        live_maint = model_maint
        maint_src = "純個股融資成本動態浮動模型"

    if raw_deriv["fini_tx_oi"] is not None:
        fini_oi = raw_deriv["fini_tx_oi"]
        fini_chg = raw_deriv["fini_tx_chg"] if raw_deriv["fini_tx_chg"] is not None else 0
        sitc_oi = raw_deriv["sitc_tx_oi"] if raw_deriv["sitc_tx_oi"] is not None else 54200
        fut_src = f"期交所14:00後官方直連 ({raw_deriv['fut_date']})"
    else:
        fini_oi = int(round(-78000 + bias_60 * 650))
        fini_chg = int(round(twii_chg_pct * 1800))
        sitc_oi = int(round(54500 - bias_60 * 350))
        fut_src = "期交所結構模型＋盤中即時價差動態浮動"

    if raw_deriv["pc_ratio"] is not None:
        live_pcr = raw_deriv["pc_ratio"]
    else:
        live_pcr = round(max(55.0, min(135.0, 92.0 + bias_20 * 2.2 + twii_chg_pct * 3.0)), 1)

    if raw_deriv["warrant_hedge_yi"] is not None:
        live_warrant_yi = raw_deriv["warrant_hedge_yi"]
    else:
        live_warrant_yi = round(twii_chg_pct * 12.5 + bias_20 * 1.8, 2)

    deriv_info = {
        "fini_tx_oi": fini_oi,
        "fini_tx_chg": fini_chg,
        "sitc_tx_oi": sitc_oi,
        "real_fini_oi": fini_oi + sitc_oi,
        "est_short_cost": est_short_cost,
        "target_cover_1": target_cover_1,
        "target_cover_2": target_cover_2,
        "short_profit_pts": round(est_short_cost - twii_now, 0),
        "short_profit_pct": round((est_short_cost - twii_now) / max(1.0, est_short_cost) * 100.0, 2),
        "pc_ratio": live_pcr,
        "warrant_hedge_yi": live_warrant_yi,
        "stock_only_maint_pct": live_maint,
        "stock_margin_chg_yi": raw_deriv["stock_margin_chg_yi"],
        "maint_src": maint_src,
        "fut_src": fut_src,
        "fx_now": fx_now,
        "fx_prev": fx_prev,
        "fx_chg_ntd": fx_chg_ntd,
        "fx_ma5": fx_ma5,
        "fx_ma20": fx_ma20
    }

    return {
        "twii_open": twii_open, "twii_prev_c": round(twii_prev_c, 1),
        "twii_now": twii_now, "twii_chg_pct": twii_chg_pct,
        "twii_high": twii_high, "twii_low": twii_low,
        "lower_shadow_pts": lower_shadow_pts, "lower_shadow_pct": lower_shadow_pct,
        "tested_ma240_and_held": tested_ma240_and_held,
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60, "ma120": ma120, "ma240": ma240,
        "ma5_prev": ma5_prev, "ma10_prev": ma10_prev, "ma20_prev": ma20_prev,
        "ma60_prev": ma60_prev, "ma120_prev": ma120_prev, "ma240_prev": ma240_prev,
        "deduct_5d": deduct_5d, "deduct_10d": deduct_10d, "deduct_20d": deduct_20d,
        "deduct_60d": deduct_60d, "deduct_120d": deduct_120d,
        "w_ma10": w_ma10, "m_ma5": m_ma5,
        "bb_upper": bb_upper, "bb_lower": bb_lower,
        "bias_5": bias_5, "bias_10": bias_10, "bias_20": bias_20, "bias_60": bias_60, "bias_120": bias_120,
        "k9": k9, "d9": d9, "j9": j9, "j9_prev": j9_prev, "kd_cross_d": kd_cross_d,
        "pdi_d": pdi_d, "mdi_d": mdi_d, "adx_d": adx_d, "adx_d_prev": adx_d_prev, "dmi_bull_d": dmi_bull_d,
        "macd_hist": macd_hist,
        "k60_k": k60_k, "k60_d": k60_d, "k60_j": k60_j, "k60_j_prev": k60_j_prev,
        "k60_pdi": k60_pdi, "k60_mdi": k60_mdi, "k60_adx": k60_adx, "k60_adx_prev": k60_adx_prev,
        "k60_ma5": k60_ma5, "k60_ma20": k60_ma20, "k60_ma60": k60_ma60,
        "k60_macd_osc": k60_macd_osc, "k60_is_red_bar": k60_is_red_bar,
        "p_2x": p_2x, "p_2x_chg": p_2x_chg,
        "p_1x": p_1x, "p_1x_chg": p_1x_chg,
        "p_400a": p_400a, "p_400a_chg": p_400a_chg,
        "p_400a_open": p_400a_open, "p_400a_high": p_400a_high, "p_400a_low": p_400a_low, "p_400a_prev": p_400a_prev,
        "ma5_400a": ma5_400a, "ma10_400a": ma10_400a, "ma20_400a": ma20_400a, "ma60_400a": ma60_400a,
        "k_400a": k_400a, "d_400a": d_400a, "j_400a": j_400a,
        "date_str": date_str, "quote_src": quote_src,
        "deriv": deriv_info
    }

def scan_all_stocks():
    price_dict, tdcc_dict, rev_dict, _ = fetch_official_market_data()
    vol_mult, vol_note = get_volume_multiplier()
    today_str = get_tw_now().strftime("%Y-%m-%d")

    candidates = dict(CORE_WATCHLIST)

    short_added = 0
    for code, info in price_dict.items():
        if code not in candidates and len(code) == 4 and info["vol_lots"] >= 1800 and info["chg_pct"] >= 2.2 and info["close"] >= 20.0:
            candidates[code] = (info["name"], "⚡短線技術破繭", info["market"])
            short_added += 1
            if short_added >= 35:
                break

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

    mis_targets = [("00631L", "TW"), ("0050", "TW"), ("00400A", "TW")] + [(c, m) for c, (_, _, m) in candidates.items()]
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
    dict_400a_constituents = {}

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
                curr_p = round(float(rt["close"]), 2)
                chg_1d = float(rt["chg_pct"])
            else:
                curr_p = round(float(c[-1]), 2)
                prev_p = float(c[-2])
                chg_1d = round((curr_p - prev_p) / prev_p * 100, 2) if prev_p > 0 else 0.0
                # 修復 Yahoo K線平盤墊檔導致 0.00% 之問題：若 chg_1d 為 0 但證交所/櫃買官方收盤表有漲跌幅，優先採用官方漲跌幅！
                if abs(chg_1d) < 0.001 and code in price_dict:
                    curr_p = price_dict[code]["close"]
                    chg_1d = price_dict[code]["chg_pct"]
                    c[-1] = curr_p

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

            prev_ma5 = float(np.mean(c[-6:-1]))
            prev_ma10 = float(np.mean(c[-11:-1]))
            prev_ma20 = float(np.mean(c[-21:-1]))
            ma_tangle_pct = round((max(prev_ma5, prev_ma10, prev_ma20) - min(prev_ma5, prev_ma10, prev_ma20)) / prev_ma20 * 100, 2)

            stk_k9, stk_d9, stk_j9, stk_j_prev, kd_cross_recent = compute_kdj(h[-60:], l[-60:], c[-60:])
            stk_j_up = stk_j9 > stk_j_prev
            pdi, mdi, adx, adx_prev, dmi_bull_active = compute_dmi(h[-90:], l[-90:], c[-90:])
            adx_delta = round(adx - adx_prev, 1)
            adx_up = adx > adx_prev
            dmi_str = f"+DI:{pdi} > -DI:{mdi}" if pdi > mdi else f"+DI:{pdi} < -DI:{mdi}"
            adx_str = f"{adx} ({'↑轉強' if adx_up else '↓整理'}{adx_delta:+.1f})"

            kd_dmi_resonance = (stk_k9 >= stk_d9) and (pdi > mdi) and adx_up and (adx >= 18.0) and (kd_cross_recent or dmi_bull_active)

            _, _, osc = compute_macd(c)

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

            # 若屬於 00400A 前 15 大核心成分股，記錄其穿透式位階與淨值貢獻數據
            if code in ETF_00400A_CONSTITUENTS:
                c_name_400, w_pct, _ = ETF_00400A_CONSTITUENTS[code]
                dict_400a_constituents[code] = {
                    "代碼": code,
                    "成分股名": c_name_400,
                    "持股權重": f"{w_pct:.2f}%",
                    "_w": w_pct,
                    "現價": curr_p,
                    "今日%": chg_1d,
                    "3日%": chg_3d,
                    "日J值": stk_j9,
                    "_j_up": stk_j_up,
                    "DMI多空": dmi_str,
                    "月線承接": ma20,
                    "季線支撐": ma60,
                    "年線鐵底": ma240,
                    "_ma5": ma5,
                    "_ma20": ma20,
                    "_ma60": ma60,
                    "_ma240": ma240,
                    "鋼鐵防線": castle_iron,
                    "400張大戶%": tdcc_str,
                    "月營收YoY": yoy_str,
                    "對00400A今日淨值貢獻": f"{round(chg_1d * (w_pct / 100.0), 3):+.3f}%"
                }

            is_heavy_dump = (chg_1d <= -2.0 and vol_ratio >= 1.25) or (chg_1d <= -4.2)

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

                elif (-3.5 <= chg_1d <= 2.2) and (chg_3d >= -6.5) and (vol_ratio <= 0.90) and (curr_p >= ma20) and (stk_j9 <= 68.0) and (pdi >= mdi * 0.9):
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

            is_blue_chip = (code in ELECTRONICS_BLUE_CHIPS) or (code in FINANCIAL_BLUE_CHIPS)
            is_fin = (code in FINANCIAL_BLUE_CHIPS)

            if is_blue_chip and (not is_heavy_dump):
                bc_rev_ok = (yoy_val is None) or (yoy_val >= 0.0) or (is_fin and yoy_val >= -5.0)
                bc_tdcc_ok = (tdcc_val is None) or (tdcc_val >= 42.0)

                if bc_rev_ok and bc_tdcc_ok and (curr_p >= ma20) and (pdi > mdi) and (chg_3d < 9.5) and (stk_j9 <= 88.0):
                    l_score = 88
                    if is_fin:
                        l_score += 3
                    if yoy_val and yoy_val >= 15.0:
                        l_score += 4
                    if tdcc_val and tdcc_val >= 65.0:
                        l_score += 4
                    if adx_up:
                        l_score += 3

                    if kd_dmi_resonance:
                        bc_status = "👑 權值龍頭＋KD×DMI雙金叉共振起漲"
                    elif is_fin:
                        bc_status = "🏦 績優金控＋站穩月季線＋長線護城河"
                    elif vol_ratio <= 0.90:
                        bc_status = "💎 權值龍頭＋大戶鎖碼＋量縮守月線甜美區"
                    else:
                        bc_status = "✅ 權值龍頭＋長線多頭趨勢穩健"

                    r_bc = {
                        "波段總分": min(100, l_score),
                        "長線格局定性": bc_status,
                        **row_base
                    }
                    list_tab3_core_bluechip.append(r_bc)

                elif bc_rev_ok and (curr_p < ma20) and (curr_p >= ma240 * 0.95) and (stk_j9 <= 40.0):
                    # 嚴格要求今日實質收紅 (chg_1d > 0.05%) 才算今日抗跌翻紅，修復 +0.00% 平盤誤加分問題
                    dip_score = 86
                    if chg_1d > 0.05:
                        dip_score += 4
                    if stk_j_up:
                        dip_score += 3
                    if tdcc_val and tdcc_val >= 70.0:
                        dip_score += 2
                    if vol_ratio <= 1.0:
                        dip_score += 1

                    if chg_1d > 0.05:
                        dip_tag = "🟢【權值右側翻紅】回測長線支撐＋今日逆勢收紅止跌"
                    elif stk_j_up:
                        dip_tag = "🟢【權值J值勾頭】回測長線支撐＋日J值低檔勾頭轉折"
                    else:
                        dip_tag = "⏳【權值左側超跌】J值入低檔惟仍在下探・待止跌紅K"

                    r_dip = {
                        "波段總分": min(96, dip_score),
                        "類型與轉機標註": dip_tag,
                        **row_base
                    }
                    list_tab3_turnaround_or_dip.append(r_dip)

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
                        "波段總分": 92 + (4 if kd_dmi_resonance else 0),
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
    df_long_turn = pd.DataFrame(list_tab3_turnaround_or_dip).sort_values(by=["波段總分", "今日%"], ascending=[False, False]) if list_tab3_turnaround_or_dip else pd.DataFrame()

    return df_t1, df_t2, df_t3, df_long_bc, df_long_turn, dict_400a_constituents, vol_note, rt_quotes

# ==========================================
# 模組 6：v47.0 中期破線與籌碼/台幣匯率/年線神針探底熔斷機制
# ==========================================
def apply_macro_circuit_breaker(
    raw_score: int,
    taiex_price: float,
    ma60: float,
    ma120: float,
    pcr_ratio: float,
    margin_ratio: float,
    fx_now: float,
    fx_ma5: float,
    fx_chg_ntd: float,
    tested_ma240_and_held: bool,
    lower_shadow_pts: float,
) -> tuple[int, str, str, bool]:
    score = raw_score
    status_tag = "🚀 多頭趨勢推進（順勢偏多操作）"
    penalties = []
    is_capped = False

    # 1. 規則 A：均線位階扣分
    if taiex_price < ma120:
        score -= 15
        penalties.append("跌破120MA半年線 (-15分)")
    elif taiex_price < ma60:
        score -= 8
        penalties.append("跌破60MA季線 (-8分)")

    # 2. 規則 B：選擇權莊家偏空扣分
    if pcr_ratio < 70.0:
        score -= 10
        penalties.append(f"選擇權PCR {pcr_ratio:.1f}%<70% (-10分)")
    elif pcr_ratio < 80.0:
        score -= 5
        penalties.append(f"選擇權PCR {pcr_ratio:.1f}%<80% (-5分)")

    # 3. 規則 D：新台幣匯率升貶加減分
    if fx_now <= fx_ma5 and fx_chg_ntd < 0:
        score += 5
        penalties.append("台幣止貶轉升破5MA (+5分)")
    elif fx_chg_ntd <= -0.15:
        score += 3
        penalties.append(f"台幣單日強升 {abs(fx_chg_ntd)*100:.1f}分 (+3分)")
    elif fx_now > fx_ma5 and fx_chg_ntd >= 0.04:
        score -= 5
        penalties.append("台幣續貶熱錢匯出 (-5分)")

    # 4. 規則 E (v47.0 新增)：回測 240MA 年線有守且拉出神針探底長下影線加分
    if tested_ma240_and_held:
        score += 6
        penalties.append(f"回測240MA年線有守＋拉出 {lower_shadow_pts:,.0f}點神針下影線 (+6分)")

    # 5. 規則 C：雙重破線硬上限熔斷機制 (Hard Cap)
    if taiex_price < ma120 and (pcr_ratio < 70.0 or margin_ratio < 143.0):
        if score > 60:
            is_capped = True
            penalties.append("觸發破半年線硬上限熔斷 (鎖定60分)")
        else:
            penalties.append("處於破半年線熔斷警戒區 (上限60分)")
        score = min(score, 60)
        if tested_ma240_and_held and margin_ratio < 130.0:
            status_tag = "💎 年線神針探底＋維持率<130%斷頭底浮現（中期仍破半年線，搶反彈逢高遇壓分批減碼）"
        else:
            status_tag = "⚠️ 極限超跌搶反彈格局（空方勢蓋頭反壓，嚴控槓桿、逢彈遇壓減碼）"
    elif taiex_price < ma60:
        if score > 70:
            is_capped = True
            penalties.append("觸發破季線硬上限熔斷 (鎖定70分)")
        score = min(score, 70)
        status_tag = "🛡️ 季線下震盪整理（保守防禦、分批調節）"
    else:
        if score >= 75:
            status_tag = "🟢 右側強烈轉攻格局（均線多頭＋籌碼共振，積極偏多）"
        else:
            status_tag = "☀️ 多頭巡航整理格局（守穩季線，標準配置續抱）"

    score = max(0, score)
    if penalties:
        trace_str = f"⚡ 熔斷演算軌跡：原始動能 {raw_score} 分 ➔ {' ＋ '.join(penalties)} ➔ 最終裁決 {score} / 100 分"
    else:
        trace_str = f"✅ 均線與籌碼無扣分項：原始動能 {raw_score} 分 ➔ 最終裁決 {score} / 100 分"

    return score, status_tag, trace_str, is_capped

# ==========================================
# 模組 7：Streamlit 戰情室主介面 (v47.0 新增 00400A 主動國泰動能高息 智慧存股戰情室)
# ==========================================
now_str = get_tw_now().strftime("%Y-%m-%d %H:%M:%S")
st.markdown("## 🏛️ 台股每日戰情室 v47.0 (新增 `00400A 主動國泰動能高息` 淨值折溢價＆成分股穿透存股雷達)")

saved_cfg = load_portfolio_config()

with st.expander("⚙️ 點此展開／調整【母子艦隊 (0050/正2) ＆ 💎 00400A 存股持倉與成本參數】（支援記憶鎖定防 F5 遺失）", expanded=False):
    st.markdown("##### ⚓ 第一排：【原始建倉基準】＆【換檔過程補貼新資金／已實現獲利】")
    ic1, ic2, ic3, ic4, ic5, ic6 = st.columns(6)
    with ic1:
        init_lots_2x = st.number_input("原始 0050正2 張數", value=float(saved_cfg["init_lots_2x"]), step=0.1, format="%.2f")
    with ic2:
        init_cost_2x = st.number_input("原始 0050正2 成本(元)", value=float(saved_cfg["init_cost_2x"]), step=0.01, format="%.2f")
    with ic3:
        init_lots_1x = st.number_input("原始 0050 張數", value=float(saved_cfg["init_lots_1x"]), step=0.1, format="%.2f")
    with ic4:
        init_cost_1x = st.number_input("原始 0050 成本(元)", value=float(saved_cfg["init_cost_1x"]), step=0.01, format="%.2f")
    with ic5:
        extra_cash_in = st.number_input("💵 換檔補貼新資金(元)", value=float(saved_cfg["extra_cash_in"]), step=100.0, format="%.0f")
    with ic6:
        realized_pnl_1x = st.number_input("💰 賣0050已實現獲利(元)", value=float(saved_cfg["realized_pnl_1x"]), step=100.0, format="%.0f")

    st.markdown("##### 🚀 第二排：【母子艦隊當前持倉】＆【💎 00400A 主動國泰動能高息 存股部位】")
    c_in1, c_in2, c_in3, c_in4, c_in5, c_in6, c_in7 = st.columns(7)
    with c_in1:
        in_lots_2x = st.number_input("當前 0050正2 張數", value=float(saved_cfg["lots_2x"]), step=0.1, format="%.2f")
    with c_in2:
        in_cost_2x = st.number_input("當前 正2 均價(元)", value=float(saved_cfg["cost_2x"]), step=0.01, format="%.2f")
    with c_in3:
        in_lots_1x = st.number_input("當前 0050 張數", value=float(saved_cfg["lots_1x"]), step=0.1, format="%.2f")
    with c_in4:
        in_cost_1x = st.number_input("當前 0050 均價(元)", value=float(saved_cfg["cost_1x"]), step=0.01, format="%.2f")
    with c_in5:
        in_cash_pool = st.number_input("待買回現金池(元)", value=float(saved_cfg["cash_pool"]), step=1000.0, format="%.0f")
    with c_in6:
        in_lots_400a = st.number_input("💎 00400A 持有張數", value=float(saved_cfg.get("lots_400a", 1.0)), step=0.5, format="%.2f")
    with c_in7:
        in_cost_400a = st.number_input("💎 00400A 成本(元)", value=float(saved_cfg.get("cost_400a", 12.00)), step=0.05, format="%.2f")

    current_cfg_dict = {
        "init_lots_2x": init_lots_2x, "init_cost_2x": init_cost_2x,
        "init_lots_1x": init_lots_1x, "init_cost_1x": init_cost_1x,
        "extra_cash_in": extra_cash_in, "realized_pnl_1x": realized_pnl_1x,
        "lots_2x": in_lots_2x, "cost_2x": in_cost_2x,
        "lots_1x": in_lots_1x, "cost_1x": in_cost_1x, "cash_pool": in_cash_pool,
        "lots_400a": in_lots_400a, "cost_400a": in_cost_400a
    }

    save_col1, save_col2 = st.columns([2, 5])
    with save_col1:
        if st.button("💾 儲存並鎖定全部持倉帳本 (防 F5 遺失)", use_container_width=True, type="primary"):
            save_portfolio_config(current_cfg_dict)
            st.success("✅ 已成功記憶【母子艦隊 ＋ 00400A 存股帳本】！")
    with save_col2:
        st.caption("💡 系統已同步記憶您的母子艦隊與 00400A 存股參數，按 F5 重新整理網頁也不會遺失！")

if any(abs(current_cfg_dict[k] - saved_cfg.get(k, 0.0)) > 0.001 for k in current_cfg_dict):
    save_portfolio_config(current_cfg_dict)

btn_col1, btn_col2, btn_col3 = st.columns([2, 2, 3])
with btn_col1:
    if st.button("⚡ 秒速刷新盤中即時報價、00400A淨值與維持率", use_container_width=True):
        fetch_mis_realtime_batch.clear()
        fetch_twse_etf_inav.clear()
with btn_col2:
    if st.button("🔄 立即重抓 14:00 後期交所與全市場籌碼", use_container_width=True):
        st.cache_data.clear()
with btn_col3:
    auto_refresh = st.checkbox("⏱️ 開啟盤中每 20 秒自動刷新即時報價", value=False)

with st.spinner("📡 正在自動連線證交所/期交所抓取大盤、00400A 即時預估淨值與前 15 大成分股穿透位階..."):
    df_launch, df_pullback, df_orbit, df_long_bc, df_long_turn, dict_400a_constituents, vol_note, rt_quotes = scan_all_stocks()
    fleet = fetch_and_analyze_fleet(rt_quotes)
    etf_inav_400a = fetch_twse_etf_inav("00400A")

deriv = fleet["deriv"]
in_fini_oi = deriv["fini_tx_oi"]
in_fini_chg = deriv["fini_tx_chg"]
in_sitc_oi = deriv["sitc_tx_oi"]
real_directional_fini_oi = deriv["real_fini_oi"]
est_short_cost = deriv["est_short_cost"]
target_cover_1 = deriv["target_cover_1"]
target_cover_2 = deriv["target_cover_2"]
short_profit_pts = deriv["short_profit_pts"]
short_profit_pct = deriv["short_profit_pct"]
in_pcr = deriv["pc_ratio"]
in_warrant_yi = deriv["warrant_hedge_yi"]
in_maint_pct = deriv["stock_only_maint_pct"]

fx_now = deriv["fx_now"]
fx_prev = deriv["fx_prev"]
fx_chg_ntd = deriv["fx_chg_ntd"]
fx_ma5 = deriv["fx_ma5"]
fx_ma20 = deriv["fx_ma20"]
fx_cents_chg = round(fx_chg_ntd * 100.0, 1)

# ==========================================
# 💎 計算 00400A 即時預估淨值 (iNAV)、折溢價率與成分股穿透合成位階
# ==========================================
p_400a = fleet["p_400a"]
p_400a_chg = fleet["p_400a_chg"]
p_400a_prev = fleet["p_400a_prev"]

# 計算前 15 大成分股之加權漲跌幅、加權日 J 值、以及各成分股距離自身 5MA/20MA/60MA/240MA 之合成淨值錨點
total_w_tracked = 0.0
weighted_chg_sum = 0.0
weighted_j_sum = 0.0
weighted_ma5_ratio = 0.0
weighted_ma20_ratio = 0.0
weighted_ma60_ratio = 0.0
weighted_ma240_ratio = 0.0
j_up_count = 0

for c_code, c_info in dict_400a_constituents.items():
    w = c_info["_w"]
    cp = max(0.01, c_info["現價"])
    total_w_tracked += w
    weighted_chg_sum += w * c_info["今日%"]
    weighted_j_sum += w * c_info["日J值"]
    weighted_ma5_ratio += w * (c_info["_ma5"] / cp)
    weighted_ma20_ratio += w * (c_info["_ma20"] / cp)
    weighted_ma60_ratio += w * (c_info["_ma60"] / cp)
    weighted_ma240_ratio += w * (c_info["_ma240"] / cp)
    if c_info["_j_up"] or c_info["今日%"] > 0.05:
        j_up_count += 1

if total_w_tracked > 0:
    synth_constituent_chg_pct = round(weighted_chg_sum / total_w_tracked, 2)
    synth_constituent_j = round(weighted_j_sum / total_w_tracked, 1)
    mult_to_ma5 = weighted_ma5_ratio / total_w_tracked
    mult_to_ma20 = weighted_ma20_ratio / total_w_tracked
    mult_to_ma60 = weighted_ma60_ratio / total_w_tracked
    mult_to_ma240 = weighted_ma240_ratio / total_w_tracked
else:
    synth_constituent_chg_pct = p_400a_chg
    synth_constituent_j = fleet["j_400a"]
    mult_to_ma5, mult_to_ma20, mult_to_ma60, mult_to_ma240 = 1.05, 1.15, 1.18, 0.96

# 決定 00400A 最準確之即時預估淨值 (優先取證交所 all_etf.txt 官方 iNAV，若休市或未回傳則以昨收淨值 × 成分股加權漲跌幅合成)
if etf_inav_400a and etf_inav_400a["est_nav"] > 0:
    nav_400a_now = etf_inav_400a["est_nav"]
    if etf_inav_400a["mkt_price"] > 0:
        p_400a = etf_inav_400a["mkt_price"]
    prem_400a_pct = round(((p_400a - nav_400a_now) / nav_400a_now) * 100.0, 2)
    nav_400a_src = etf_inav_400a["src"]
else:
    # 以昨收淨值 (約等於昨收市價 × 0.9992) 結合今日成分股即時加權漲跌幅合成預估淨值
    est_prev_nav = round(p_400a_prev * 0.9992, 2)
    nav_400a_now = round(est_prev_nav * (1.0 + (synth_constituent_chg_pct * 0.65 + p_400a_chg * 0.35) / 100.0), 2)
    prem_400a_pct = round(((p_400a - nav_400a_now) / max(0.01, nav_400a_now)) * 100.0, 2)
    nav_400a_src = "🧮 前15大成分股即時股價穿透合成預估淨值"

# 穿透式計算 00400A 對應成分股各級均線之合成目標價
synth_400a_ma5 = round(nav_400a_now * mult_to_ma5, 2)
synth_400a_ma20 = round(nav_400a_now * mult_to_ma20, 2)
synth_400a_ma60 = round(nav_400a_now * mult_to_ma60, 2)
synth_400a_ma240 = round(nav_400a_now * mult_to_ma240, 2)

st.caption(f"🕒 台灣時間：{now_str} ｜ 報價：{fleet['quote_src']} ｜ 台幣：{fx_now:.3f} ({'貶值' if fx_chg_ntd > 0 else '升值'}{abs(fx_cents_chg):.1f}分) ｜ 00400A現價：{p_400a:.2f}元 (預估淨值 {nav_400a_now:.2f}元 / 折溢價 {prem_400a_pct:+.2f}%)")

twii_open = fleet["twii_open"]
twii_prev_c = fleet["twii_prev_c"]
twii_now = fleet["twii_now"]
p_2x = fleet["p_2x"]
p_1x = fleet["p_1x"]

# 真實資金流核心精算
init_cap_2x = int(round(init_lots_2x * 1000 * init_cost_2x))
init_cap_1x = int(round(init_lots_1x * 1000 * init_cost_1x))
initial_base_capital = init_cap_2x + init_cap_1x
extra_cash_injected = int(round(extra_cash_in))
realized_1x_gain = int(round(realized_pnl_1x))
total_invested_capital = initial_base_capital + extra_cash_injected

hold_initial_only_val = int(round(init_lots_2x * 1000 * p_2x)) + int(round(init_lots_1x * 1000 * p_1x))
hold_initial_plus_cash_val = hold_initial_only_val + extra_cash_injected

val_2x = int(round(in_lots_2x * 1000 * p_2x))
cost_total_2x = int(round(in_lots_2x * 1000 * in_cost_2x))
pnl_2x = val_2x - cost_total_2x
pnl_2x_pct = round(((p_2x - in_cost_2x) / max(0.01, in_cost_2x)) * 100, 2)

bare_cost_total_2x = total_invested_capital + realized_1x_gain - int(round(in_lots_1x * 1000 * in_cost_1x))
bare_unrealized_pnl_2x = val_2x - bare_cost_total_2x

val_1x = int(round(in_lots_1x * 1000 * p_1x))
cost_total_1x = int(round(in_lots_1x * 1000 * in_cost_1x))
pnl_1x = val_1x - cost_total_1x
pnl_1x_pct = round(((p_1x - in_cost_1x) / max(0.01, in_cost_1x)) * 100, 2)

broker_cost_all = cost_total_2x + cost_total_1x
total_fleet_val = val_2x + val_1x + int(round(in_cash_pool))

true_total_pnl = total_fleet_val - total_invested_capital
true_total_pnl_pct = round((true_total_pnl / max(1, total_invested_capital)) * 100, 2)
switching_alpha = total_fleet_val - hold_initial_plus_cash_val

alpha_1028 = int(round(1000 * p_2x - (300 * p_1x + 4190)))
alpha_1102 = int(round(1000 * p_2x - (300 * p_1x + 2660)))

req_idx_gain_ratio = (total_invested_capital - total_fleet_val) / max(1.0, (2.0 * val_2x + 1.0 * val_1x))
twii_breakeven = round(twii_now * (1.0 + req_idx_gain_ratio), 0)
p2x_breakeven = round(p_2x * (1.0 + 2.0 * req_idx_gain_ratio), 2)
p1x_breakeven = round(p_1x * (1.0 + 1.0 * req_idx_gain_ratio), 2)

diff_shares_2x = int(round((in_lots_2x - init_lots_2x) * 1000))
diff_shares_1x = int(round((in_lots_1x - init_lots_1x) * 1000))
extra_2x_shares = max(0, diff_shares_2x)
short_1x_shares = max(0, -diff_shares_1x)

curr_extra_2x_net = extra_2x_shares * p_2x * (1.0 - 0.002425)
curr_buyback_1x_cost = short_1x_shares * p_1x * 1.001425
denom_switch = max(1.0, 2.0 * curr_extra_2x_net - 1.0 * curr_buyback_1x_cost)
req_switch_gain_ratio = (extra_cash_injected + curr_buyback_1x_cost - curr_extra_2x_net) / denom_switch
twii_switch_be = round(twii_now * (1.0 + req_switch_gain_ratio), 0)
p2x_switch_be = round(p_2x * (1.0 + 2.0 * req_switch_gain_ratio), 2)
p1x_switch_be = round(p_1x * (1.0 + 1.0 * req_switch_gain_ratio), 2)

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
twii_ma5 = fleet["ma5"]
twii_ma10 = fleet["ma10"]
twii_ma20 = fleet["ma20"]
twii_ma60_buffer = round(fleet["ma60"] * 1.016, 1)
twii_ma60 = fleet["ma60"]
twii_ma120 = fleet["ma120"]
twii_ma240 = fleet["ma240"]
twii_m_ma5 = fleet["m_ma5"]
twii_w_ma10 = fleet["w_ma10"]

t_harvest = calc_target_2x(twii_harvest_zone)
t_ma5 = calc_target_2x(twii_ma5)
t_ma10 = calc_target_2x(twii_ma10)
t_ma20 = calc_target_2x(twii_ma20)
t_w_ma10 = calc_target_2x(twii_w_ma10)
t_ma60_buf = calc_target_2x(twii_ma60_buffer)
t_ma60 = calc_target_2x(twii_ma60)
t_m_ma5 = calc_target_2x(twii_m_ma5)
t_ma120 = calc_target_2x(twii_ma120)
t_ma240 = calc_target_2x(twii_ma240)
t_cover_2 = calc_target_2x(target_cover_2)

t1x_harvest = calc_target_1x(twii_harvest_zone)
t1x_ma5 = calc_target_1x(twii_ma5)
t1x_ma10 = calc_target_1x(twii_ma10)
t1x_ma20 = calc_target_1x(twii_ma20)
t1x_w_ma10 = calc_target_1x(twii_w_ma10)
t1x_ma60 = calc_target_1x(twii_ma60)
t1x_m5 = calc_target_1x(twii_m_ma5)
t1x_ma120 = calc_target_1x(twii_ma120)

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

# ==========================================
# 🏆 五維度原始評分計算 ＋ 台幣匯率與年線神針探底狀態定錨
# ==========================================
if in_maint_pct < 130.0:
    s1_maint = 25
    s1_desc = f"💎 歷史級全面斷頭大底 ({in_maint_pct:.1f}% < 130%・重現2025/4關稅戰級別極限清洗！)"
elif in_maint_pct <= 135.0:
    s1_maint = 25
    s1_desc = f"🚨 已破 135% 追繳臨界 ({in_maint_pct:.1f}%)・進入 T+2 早盤強制斷頭宣洩倒數底區！"
elif in_maint_pct <= 145.0:
    s1_maint = 22
    s1_desc = f"🟢 已入常態波段落底甜美區 ({in_maint_pct:.1f}% 位於 135%~145%)"
elif in_maint_pct <= 152.0:
    s1_maint = 14
    s1_desc = f"🟡 散戶浮額降溫洗盤中段 ({in_maint_pct:.1f}% 接近 145%)"
elif in_maint_pct <= 165.0:
    s1_maint = 10
    s1_desc = f"☀️ 信用維持率常態區 ({in_maint_pct:.1f}%)"
else:
    s1_maint = 3
    s1_desc = f"⚠️ 融資維持率過熱警戒 ({in_maint_pct:.1f}% > 165%)"

if in_fini_chg >= 4500:
    s2_fut = 25
    s2_desc = f"🟢 外資單日大舉回補空單 +{in_fini_chg:,} 口 (佈空成本約 {est_short_cost
