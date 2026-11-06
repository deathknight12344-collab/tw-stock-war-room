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
    page_title="台股每日戰情室 v46.0",
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
# 模組 0：母子艦隊「真實資金流三維帳本」記憶引擎
# ==========================================
CONFIG_FILE = "fleet_portfolio_v46.json"
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
    for fname in [CONFIG_FILE, "fleet_portfolio_v45.json", "fleet_portfolio_v44.json", "fleet_portfolio_v43.json"]:
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
# 模組 2：證交所 MIS 無快取直連 ＋ Yahoo v8 1分K 雙源即時報價引擎 (含 USD/TWD 台幣匯率即時流)
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
        yv8 = fetch_yahoo_v8_realtime(yf_sym, decimals=2)
        if yv8 and yv8["close"] > 0:
            if code_key not in rt_dict:
                rt_dict[code_key] = yv8
            else:
                if rt_dict[code_key].get("open", 0) <= 0:
                    rt_dict[code_key]["open"] = yv8["open"]
                if abs(rt_dict[code_key]["close"] - rt_dict[code_key]["prev_close"]) < 0.01 and abs(yv8["close"] - yv8["prev_close"]) > 0.01:
                    rt_dict[code_key] = yv8

    # 同步抓取 USD/TWD 新台幣即時匯率 (取小數點後 3 位)
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
        curr = float(closes[-1]) if len(closes) > 0 else 41575.0
        return round(curr * 1.08, 0), round(curr * 1.04, 0), round(curr * 1.00, 0)

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
# 模組 4：長線「電子權值股」＋「金融官股/龍頭金控」專屬白名單資料庫
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

CONSTRUCTION_BLACKLIST = {"1808", "2501", "2504", "2505", "2506", "2509", "2511", "2515", "2516", "2520", "2524", "2527", "2528", "2530", "2534", "2535", "2536", "2537", "2538", "2539", "2540", "2542", "2543", "2545", "2546", "2547", "2548", "2597", "5522", "5534"}

def is_traditional_or_construction(code):
    if code in CONSTRUCTION_BLACKLIST:
        return True
    if len(code) == 4 and code[:2] in ["11", "12", "13", "14", "18", "19", "20", "21", "25", "26", "27", "29"]:
        return True
    return False

# ==========================================
# 模組 5：多週期 KDJ ＋ 威爾德 DMI(14) ＋ 新台幣匯率 (TWD=X) 演算核心
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
    tickers = ["^TWII", "00631L.TW", "0050.TW", "TWD=X"]
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

    twii_now = twii_official["close"] if twii_official["close"] > 0 else 41574.7
    twii_chg_pct = twii_official["chg_pct"]
    twii_open = twii_now
    twii_high = twii_now
    twii_low = twii_now
    twii_prev_c = twii_now
    ma5 = round(twii_now * 1.045, 1)
    ma10 = round(twii_now * 1.078, 1)
    ma20 = round(twii_now * 1.12, 1)
    ma60 = round(twii_now * 1.11, 1)
    ma120 = round(twii_now * 1.09, 1)
    ma240 = round(twii_now * 0.93, 1)
    w_ma10 = round(twii_now * 1.12, 1)
    m_ma5 = round(twii_now * 1.077, 1)

    # 均線前一日值與扣抵值預設
    ma5_prev, ma10_prev, ma20_prev, ma60_prev, ma120_prev, ma240_prev = ma5, ma10, ma20, ma60, ma120, ma240
    deduct_5d, deduct_10d, deduct_20d, deduct_60d, deduct_120d = ma5, ma10, ma20, ma60, ma120

    bb_upper = round(twii_now * 1.16, 1)
    bb_lower = round(twii_now * 0.98, 1)
    k9, d9, j9, j9_prev, kd_cross_d = 12.0, 15.2, 2.3, 13.0, False
    pdi_d, mdi_d, adx_d, adx_d_prev, dmi_bull_d = 9.8, 48.2, 33.5, 30.1, False
    macd_hist = -450.0
    date_str = today_str
    quote_src = "Yahoo Finance / 證交所"

    est_short_cost = round(twii_now * 1.128, 0)
    target_cover_1 = round(est_short_cost * 0.96, 0)
    target_cover_2 = round(est_short_cost * 0.925, 0)

    k60_k, k60_d, k60_j, k60_j_prev, k60_kd_cross = 14.2, 21.4, -0.3, 4.6, False
    k60_pdi, k60_mdi, k60_adx, k60_adx_prev, k60_dmi_bull = 11.5, 45.0, 35.0, 32.0, False
    k60_ma5, k60_ma20, k60_ma60 = round(twii_now * 1.01, 1), round(twii_now * 1.04, 1), round(twii_now * 1.08, 1)
    k60_macd_osc = -35.0
    k60_is_red_bar = False

    # 新台幣匯率 (USD/TWD) 預設值
    fx_now = 32.180
    fx_prev = 32.120
    fx_chg_ntd = +0.060  # 正數代表美元漲=台幣貶值；負數代表美元跌=台幣升值
    fx_ma5 = 32.080
    fx_ma20 = 31.950

    p_2x = price_dict.get("00631L", {}).get("close", 29.21)
    p_2x_chg = price_dict.get("00631L", {}).get("chg_pct", 0.0)
    p_1x = price_dict.get("0050", {}).get("close", 97.15)
    p_1x_chg = price_dict.get("0050", {}).get("chg_pct", 0.0)

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

                # 計算各級均線前一日值（用於判斷均線斜率是下彎蓋頭壓還是上揚支撐）與扣抵值
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

            # 解析 USD/TWD 新台幣匯率走勢
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
                    dip_score = 86
                    if chg_1d >= 0.0:
                        dip_score += 4
                    if stk_j_up:
                        dip_score += 3
                    if tdcc_val and tdcc_val >= 70.0:
                        dip_score += 2
                    if vol_ratio <= 1.0:
                        dip_score += 1

                    if chg_1d >= 0.0 or stk_j_up:
                        dip_tag = "🟢【權值右側止穩】回測長線支撐＋今日抗跌/J值勾頭"
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

    return df_t1, df_t2, df_t3, df_long_bc, df_long_turn, vol_note, rt_quotes

# ==========================================
# 模組 6：v46.0 中期破線與籌碼/台幣匯率熔斷機制 (Hard Cap & Penalty)
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

    # 3. 規則 D (v46.0 新增)：新台幣匯率 (USD/TWD) 升貶加減分
    if fx_now <= fx_ma5 and fx_chg_ntd < 0:
        score += 5
        penalties.append(f"台幣止貶轉升破5MA (+5分)")
    elif fx_now > fx_ma5 and fx_chg_ntd >= 0.04:
        score -= 5
        penalties.append(f"台幣續貶熱錢匯出 (-5分)")

    # 4. 規則 C：雙重破線硬上限熔斷機制 (Hard Cap)
    if taiex_price < ma120 and (pcr_ratio < 70.0 or margin_ratio < 143.0):
        if score > 60:
            is_capped = True
            penalties.append("觸發破半年線硬上限熔斷 (鎖定60分)")
        else:
            penalties.append("處於破半年線熔斷警戒區 (上限60分)")
        score = min(score, 60)
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
# 模組 7：Streamlit 戰情室主介面 (v46.0 台幣匯率整合 ＋ 空方勢均線壓力/反轉導航版)
# ==========================================
now_str = get_tw_now().strftime("%Y-%m-%d %H:%M:%S")
st.markdown("## 🏛️ 台股每日戰情室 v46.0 (台幣匯率熱錢監控 ＋ 空方勢均線「蓋頭壓力 vs. 反轉突破」導航版)")

saved_cfg = load_portfolio_config()

with st.expander("⚙️ 點此展開／調整【母子艦隊：⚓ 原始建倉 ＋ 💵 換檔補貼新資金 ＋ 🚀 當前實際持倉】（支援記憶鎖定防 F5 遺失）", expanded=False):
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

    st.markdown("##### 🚀 第二排：【當前實際持倉】")
    c_in1, c_in2, c_in3, c_in4, c_in5 = st.columns(5)
    with c_in1:
        in_lots_2x = st.number_input("當前 0050正2 持有張數", value=float(saved_cfg["lots_2x"]), step=0.1, format="%.2f")
    with c_in2:
        in_cost_2x = st.number_input("當前 0050正2 券商均價(元)", value=float(saved_cfg["cost_2x"]), step=0.01, format="%.2f")
    with c_in3:
        in_lots_1x = st.number_input("當前 0050 持有張數", value=float(saved_cfg["lots_1x"]), step=0.1, format="%.2f")
    with c_in4:
        in_cost_1x = st.number_input("當前 0050 券商均價(元)", value=float(saved_cfg["cost_1x"]), step=0.01, format="%.2f")
    with c_in5:
        in_cash_pool = st.number_input("當前待買回現金池(元)", value=float(saved_cfg["cash_pool"]), step=1000.0, format="%.0f")

    current_cfg_dict = {
        "init_lots_2x": init_lots_2x, "init_cost_2x": init_cost_2x,
        "init_lots_1x": init_lots_1x, "init_cost_1x": init_cost_1x,
        "extra_cash_in": extra_cash_in, "realized_pnl_1x": realized_pnl_1x,
        "lots_2x": in_lots_2x, "cost_2x": in_cost_2x,
        "lots_1x": in_lots_1x, "cost_1x": in_cost_1x, "cash_pool": in_cash_pool
    }

    save_col1, save_col2 = st.columns([2, 5])
    with save_col1:
        if st.button("💾 儲存並鎖定真實資金流帳本 (防 F5 遺失)", use_container_width=True, type="primary"):
            save_portfolio_config(current_cfg_dict)
            st.success("✅ 已成功記憶【原始建倉 ＋ 補貼新資金 ＋ 當前實際持倉】！")
    with save_col2:
        st.caption("💡 系統已將您補貼的新資金計入累計總投入本金，確保真實總損益與換檔 Alpha 100% 精準！")

if any(abs(current_cfg_dict[k] - saved_cfg[k]) > 0.001 for k in current_cfg_dict):
    save_portfolio_config(current_cfg_dict)

btn_col1, btn_col2, btn_col3 = st.columns([2, 2, 3])
with btn_col1:
    if st.button("⚡ 秒速刷新盤中即時報價、台幣匯率與維持率", use_container_width=True):
        fetch_mis_realtime_batch.clear()
with btn_col2:
    if st.button("🔄 立即重抓 14:00 後期交所與全市場籌碼", use_container_width=True):
        st.cache_data.clear()
with btn_col3:
    auto_refresh = st.checkbox("⏱️ 開啟盤中每 20 秒自動刷新即時報價", value=False)

with st.spinner("📡 正在自動連線期交所/證交所/Yahoo匯率抓取期權、台幣匯率(USD/TWD)與空方勢均線反壓矩陣..."):
    df_launch, df_pullback, df_orbit, df_long_bc, df_long_turn, vol_note, rt_quotes = scan_all_stocks()
    fleet = fetch_and_analyze_fleet(rt_quotes)

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
fx_cents_chg = round(fx_chg_ntd * 100.0, 1)  # 換算為「分」

st.caption(f"🕒 台灣時間：{now_str} ｜ 報價：{fleet['quote_src']} ｜ 台幣匯率：{fx_now:.3f} ({'貶值' if fx_chg_ntd > 0 else '升值'}{abs(fx_cents_chg):.1f}分) ｜ 期權源：{deriv['fut_src']}")

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
# 🏆 五維度原始評分計算 ＋ 台幣匯率狀態定錨
# ==========================================
if in_maint_pct < 130.0:
    s1_maint = 25
    s1_desc = f"💎 歷史級全面斷頭大底 ({in_maint_pct:.1f}% < 130%)"
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
    s2_desc = f"🟢 外資單日大舉回補空單 +{in_fini_chg:,} 口 (佈空成本約 {est_short_cost:,.0f}點)"
elif twii_now <= target_cover_2 * 1.005:
    if in_fini_chg <= -1500:
        s2_fut = 12
        s2_desc = f"⚠️ 點位雖達外資滿足區 ({target_cover_2:,.0f}點，每口已賺 +{short_profit_pts:,.0f}點)，惟外資今日仍逆勢追空 {in_fini_chg:+,}口 壓低結算，待轉正回補！"
    else:
        s2_fut = 20
        s2_desc = f"🎯 已達外資空單【終極獲利滿足區 {target_cover_2:,.0f}點】(成本 {est_short_cost:,.0f}點，每口已賺 +{short_profit_pts:,.0f}點/+{short_profit_pct:.1f}%，隨時大回補！)"
elif twii_now <= target_cover_1:
    s2_fut = 14
    s2_desc = f"🟡 已入外資初步獲利區 {target_cover_1:,.0f}點 (外資成本 {est_short_cost:,.0f}點，終極滿足 {target_cover_2:,.0f}點)"
elif in_fini_chg <= -2500:
    s2_fut = 4
    s2_desc = f"⚠️ 外資單日加空 {in_fini_chg:+,} 口 (佈空成本 {est_short_cost:,.0f}點)"
else:
    s2_fut = 10
    s2_desc = f"🟡 外資實質空單 {real_directional_fini_oi:+,} 口 (佈空成本 {est_short_cost:,.0f}點)"

if in_pcr <= 75.0 or in_pcr >= 105.0:
    s3_pcr = 12
    pcr_note = f"PCR {in_pcr:.1f}% ({'極端超跌醞釀軋空' if in_pcr <= 75.0 else '莊家多方支撐'})"
elif in_pcr <= 85.0:
    s3_pcr = 8
    pcr_note = f"PCR {in_pcr:.1f}% (進入低檔超跌前哨區)"
else:
    s3_pcr = 5
    pcr_note = f"PCR {in_pcr:.1f}% (莊家短線偏空壓制)"

if in_warrant_yi >= 3.0:
    s3_war = 8
    war_note = f"權證避險買超 {in_warrant_yi:+.2f}億 (認購點火)"
elif in_warrant_yi <= -35.0:
    s3_war = 7
    war_note = f"權證避險大賣 {in_warrant_yi:+.2f}億 (認購恐慌停損宣洩)"
else:
    s3_war = 3
    war_note = f"權證避險調節 {in_warrant_yi:+.2f}億"

s3_opt_war = s3_pcr + s3_war
s3_desc = f"🟢 {pcr_note} ＋ {war_note}" if s3_opt_war >= 14 else f"🟡 {pcr_note} ＋ {war_note}"

if (k60_k >= k60_d) and (twii_now >= fleet["k60_ma5"]) and (k60_pdi > k60_mdi):
    s4_k60 = 15
    s4_desc = f"🟢 60分KDJ金叉＋站穩60分5MA({fleet['k60_ma5']:,.0f})＋60分DMI多方 (右側扳機確立)"
elif (k60_j_delta > 0 or k60_k >= k60_d) and (twii_now >= fleet["k60_ma5"]):
    s4_k60 = 13
    s4_desc = f"🟢 60分KDJ金叉 (J={k60_j} {k60_j_trend}) 並站上 60分5MA({fleet['k60_ma5']:,.0f})"
elif k60_j_delta > 0 or k60_k >= k60_d:
    s4_k60 = 10
    s4_desc = f"🟢 60分KDJ (K={k60_k}/D={k60_d}/J={k60_j} {k60_j_trend}) 低檔金叉勾頭，挑戰 60分5MA({fleet['k60_ma5']:,.0f})"
else:
    s4_k60 = 0
    s4_desc = f"⚠️ 60分J={k60_j} ({k60_j_trend}) 仍在向下探底，尚未站上 60分5MA({fleet['k60_ma5']:,.0f})"

if twii_now >= twii_ma60 and fleet["k9"] >= fleet["d9"]:
    s5_space = 15
    s5_desc = f"🟢 穩守季線({twii_ma60:,.0f})之上且日KDJ多頭"
elif twii_now >= twii_ma120 and j_now <= 25.0:
    s5_space = 12
    s5_desc = f"🟢 守穩半年線({twii_ma120:,.0f})支撐且日J={j_now}低檔超賣"
elif fleet["bias_20"] <= -7.0 and j_now <= 18.0:
    s5_space = 10
    j_dir_word = "低檔勾頭" if j_delta > 0 else "極度超賣探底"
    s5_desc = f"🎯 月線負乖離高達 {fleet['bias_20']:+.2f}% 且日J={j_now}({j_trend_str}) {j_dir_word} (具備極端乖離彈力)"
else:
    s5_space = 5
    s5_desc = f"⚠️ 位於季線({twii_ma60:,.0f})與半年線({twii_ma120:,.0f})之下整理 (月線乖離 {fleet['bias_20']:+.2f}%)"

raw_score_5d = s1_maint + s2_fut + s3_opt_war + s4_k60 + s5_space

score_5d, grade_str, circuit_trace_str, is_circuit_capped = apply_macro_circuit_breaker(
    raw_score=raw_score_5d,
    taiex_price=twii_now,
    ma60=twii_ma60,
    ma120=twii_ma120,
    pcr_ratio=in_pcr,
    margin_ratio=in_maint_pct,
    fx_now=fx_now,
    fx_ma5=fx_ma5,
    fx_chg_ntd=fx_chg_ntd
)

# 新台幣匯率狀態標籤
if fx_now <= fx_ma5 and fx_chg_ntd < 0:
    fx_status_str = f"🟢 台幣由貶轉升破5MA ({fx_now:.3f} <= {fx_ma5:.3f}，熱錢回流止跌訊號！)"
elif fx_chg_ntd <= 0:
    fx_status_str = f"🟡 台幣今日止貶回穩 ({fx_now:.3f}，升值 {abs(fx_cents_chg):.1f}分，待跌破5MA {fx_ma5:.3f} 確認)"
else:
    fx_status_str = f"🚨 台幣續貶 ({fx_now:.3f}，貶值 {abs(fx_cents_chg):.1f}分 > 5MA {fx_ma5:.3f}，外資熱錢提款未止)"

def build_ai_snapshot_text():
    lines = [
        f"【台股每日戰情室 v46.0 台幣匯率＋空方勢均線反壓導航數據包】({now_str})",
        f"1. 五維度總評分(含熔斷與匯率)：最終 {score_5d} / 100 分 ({grade_str} | {circuit_trace_str}) | 大盤現價 {twii_now:,.1f}點 ({fleet['twii_chg_pct']:+.2f}%) | 日5MA {twii_ma5:,.0f} ({fleet['bias_5']:+.2f}%) | 日10MA {twii_ma10:,.0f} ({fleet['bias_10']:+.2f}%) | 日20MA月線 {twii_ma20:,.0f} ({fleet['bias_20']:+.2f}%) | 日60MA季線 {twii_ma60:,.0f} ({fleet['bias_60']:+.2f}%) | 日120MA半年線 {twii_ma120:,.0f} ({fleet['bias_120']:+.2f}%) | 日240MA年線 {twii_ma240:,.0f}",
        f"2. 五大籌碼與台幣匯率雷達：『台幣匯率USD/TWD』={fx_now:.3f} ({fx_status_str}) | 『外資平均佈空成本』={est_short_cost:,.0f}點 (每口已賺 +{short_profit_pts:,.0f}點/+{short_profit_pct:.1f}%，終極滿足回補區={target_cover_2:,.0f}點) | 『外資實質空單』={real_directional_fini_oi:+,}口 (單日 {in_fini_chg:+,}口) | 『純個股融資維持率』={in_maint_pct:.1f}% | 『選擇權PCR』={in_pcr:.1f}% | 『權證避險』={in_warrant_yi:+.2f}億元",
        f"3. 母子艦隊真實資金流帳本：累計總投入本金={total_invested_capital:,}元 | 當前總市值={total_fleet_val:,}元 (5.0張正2@{p_2x:.2f} + 0.4張0050@{p_1x:.2f}) ➔ 【真實累計總損益={true_total_pnl:+,}元 ({true_total_pnl_pct:+.2f}%)】、【換檔純價差Alpha={switching_alpha:+,}元】、【總資產翻紅兩平點：{twii_breakeven:,.0f}點 / 正2={p2x_breakeven:.2f}元】、【純換檔兩平點：{twii_switch_be:,.0f}點 / 正2={p2x_switch_be:.2f}元】"
    ]
    if not df_long_bc.empty:
        top_g = [f"{r['代碼']}{r['股名']}(現價{r['現價']}/今日{r['今日%']:+}%/{r['DMI多空']}/大戶{r['400張大戶%']}/YoY{r['月營收YoY']})" for _, r in df_long_bc.head(8).iterrows()]
        lines.append("4. 分頁三表A(電子與金融權值核心波段榜)：" + "；".join(top_g))
    if not df_long_turn.empty:
        top_t = [f"{r['代碼']}{r['股名']}(總分{r['波段總分']}/{r['類型與轉機標註']}/現價{r['現價']}/今日{r['今日%']:+}%/J值{r['日J值']})" for _, r in df_long_turn.head(8).iterrows()]
        lines.append("5. 分頁三表B(轉機股與權值回測買點榜)：" + "；".join(top_t))
    return "\n".join(lines)

with st.expander("🤖 點此展開【Gemini AI 專屬戰情數據包】（點擊右上角 📋 圖示即可一鍵複製貼給 Gemini 分析，免截圖！）", expanded=False):
    st.code(build_ai_snapshot_text(), language="markdown")

tab1, tab2, tab3 = st.tabs([
    "📊 0050正2 ＋ 0050 母子艦隊戰情室 (v46.0 台幣匯率監控 ＋ 空方勢均線壓力/反轉導航)",
    "⚡ 短線操作精選區 (純看技術面與量能・KD×DMI 共振)",
    "🏛️ 長期投資波段區 (👑電子權值 ＋ 🏦金融金控 ＋ 🔄轉機股標註)"
])

with tab1:
    # ==========================================
    # 🏆 第一順位（最頂端置頂）：【五維度＋台幣匯率量化診斷 ➔ 0050 ＆ 0050正2 空方勢實戰操作方案】
    # ==========================================
    if twii_now < twii_ma120 or is_circuit_capped:
        hero_box_class = "war-hero-box-warn"
        score_color = "#ffab40"
        banner_bg = "rgba(255, 152, 0, 0.22)"
        banner_color = "#ffd54f"
    else:
        hero_box_class = "war-hero-box"
        score_color = "#00e676"
        banner_bg = "rgba(0, 230, 118, 0.14)"
        banner_color = "#00e676"

    if s4_k60 >= 10:
        k60_action_txt = f"且 <b>60分KDJ 已出現低檔金叉勾頭 (60分J={k60_j})</b>"
    else:
        k60_action_txt = f"惟 <b>目前 60分J ({k60_j} {k60_j_trend}) 與 日J ({j_now} {j_trend_str}) 雙雙仍在向下探底、尚未站回 60分5MA ({fleet['k60_ma5']:,.0f}點)</b>"

    st.markdown(f"""
    <div class="{hero_box_class}">
        <div class="war-score-title">📋 【五維度＋台幣匯率量化診斷 ➔ 0050 ＆ 0050正2 空方勢實戰操作方案】（熔斷後總評分：<span style="font-size:36px; color:{score_color};">{score_5d}</span> / 100 分）</div>
        <div class="war-circuit-trace">{circuit_trace_str}</div><br>
        <div class="war-grade-banner" style="background:{banner_bg}; color:{banner_color}; border:1px solid {banner_color};">🧭 戰情室狀態定性：{grade_str}</div>
        <div class="war-dim-grid">
            <div class="war-dim-item"><b>1. 純個股維持率 ({s1_maint}/25分)</b><br>{s1_desc}</div>
            <div class="war-dim-item"><b>2. 外資空單與滿足度 ({s2_fut}/25分)</b><br>{s2_desc}</div>
            <div class="war-dim-item"><b>3. 選擇權PCR＋權證 ({s3_opt_war}/20分)</b><br>{s3_desc}</div>
            <div class="war-dim-item"><b>4. 60分K微觀扳機 ({s4_k60}/15分)</b><br>{s4_desc}</div>
            <div class="war-dim-item"><b>5. 負乖離彈力 ({s5_space}/15分)</b><br>{s5_desc}</div>
            <div class="war-dim-item"><b>6. 台幣匯率熱錢監控</b><br>{fx_status_str}</div>
        </div>
        <div class="war-action-box">
            <div style="font-size:19px; font-weight:900; color:#ffd54f; margin-bottom:6px;">
                🎯 母子艦隊（目前持有 {in_lots_2x:.2f}張正2 ＋ {in_lots_1x:.2f}張0050 ｜ 累計總本金 {total_invested_capital:,}元 ｜ 當前總市值 {total_fleet_val:,}元 ｜ 真實總損益 {true_total_pnl:+,}元）空方勢三大鐵律：
            </div>
            <b>①【止跌三大訊號確認前・嚴禁再賣 0050 加碼正2】</b>：目前純個股維持率已跌破 135% 追繳線至 <b style="color:#00e676;">{in_maint_pct:.1f}%</b>（進入 T+2 早盤斷頭宣洩底區），但 {k60_action_txt}。在確認 <b>(A) 新台幣匯率止貶轉升破 5MA（{fx_ma5:.3f}）</b>、<b>(B) 大盤站回 60分5MA（{fleet['k60_ma5']:,.0f}點）與日5MA（{twii_ma5:,.0f}點）</b>、<b>(C) 外資期貨轉為正回補</b> 之前，現有 5.0 張正2 不必在斷頭末端砍在阿呆谷，但<b>絕對禁止再動用最後 0.4 張（400股）0050 擴大槓桿！</b><br>
            <b>②【空方勢「突破哪條均線是反轉 vs. 哪條是壓力」核心準則】</b>：目前大盤上方 <b>5MA（{twii_ma5:,.0f}）、10MA（{twii_ma10:,.0f}）、半年線（{twii_ma120:,.0f}）、季線（{twii_ma60:,.0f}）、月線（{twii_ma20:,.0f}）</b> 全部陡峭下彎！<b>「凡是陡峭下彎的均線，第一次反彈觸碰 90% 都是蓋頭反壓（逃命減碼點）！」</b><br>
            &nbsp;&nbsp;• <b>🔑 止跌確認線</b>：收盤突破 <b>60分20MA（{fleet['k60_ma20']:,.0f}點 / 正2約 {calc_target_2x(fleet['k60_ma20']):.2f}元）与 日5MA（{twii_ma5:,.0f}點 / 正2約 {t_ma5:.2f}元）</b> 才算單邊殺盤止跌。<br>
            &nbsp;&nbsp;• <b>🚨 三道蓋頭反壓減碼線</b>：反彈初碰下彎的 <b>① 日10MA/5月線（{twii_m_ma5:,.0f}~{twii_ma10:,.0f}點 / 正2約 {t_m_ma5:.2f}元）</b>、<b>② 120MA半年線（{twii_ma120:,.0f}點 / 正2約 {t_ma120:.2f}元，此時總資產已翻紅！）</b>、<b>③ 60MA季線与20MA月線（{twii_ma60:,.0f}~{twii_ma20:,.0f}點 / 正2約 {t_ma60:.2f}~{t_ma20:.2f}元）</b> 皆為強大蓋頭反壓，請依下方【表 D】分批將增持的 2 張正2 減碼換回 0050！<br>
            &nbsp;&nbsp;• <b>🏆 真正的波段反轉確認線</b>：唯有反彈遇壓拉回打出 <b>W 底第二隻腳（不破前低）</b>，待 <b>日20MA 月線扣抵值走低、月線由下彎轉為走平後，第二次帶量突破 20MA 月線</b>，才是真正的「空翻多波段大反轉」！
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ==========================================
    # 🧭 第二順位：新增【表 D：空方勢專武——各級均線「蓋頭反壓減碼 vs. 突破反轉確認」實戰導航矩陣】
    # 直接解答「空方勢到底突破哪一條均線才是反轉、哪一條均線是蓋頭壓力」！
    # ==========================================
    st.markdown("#### 🧭 表 D：【空方勢專武】大盤各級均線「蓋頭反壓減碼點 vs. 止跌反轉確認線」實戰導航矩陣")
    st.caption("💡 操盤鐵律：均線「陡峭下彎且扣抵高檔」時，第一次觸碰一律視為【🚨 蓋頭反壓（逢高減碼點）】；唯有站上短期均線使均線走平、或打出第二隻腳突破走平之月線，才視為【🔑 止跌 / 🏆 真反轉】！")

    def build_ma_guide_row(ma_name, ma_val, ma_prev_val, deduct_val, role_tag, action_guide):
        dist_pts = round(ma_val - twii_now, 0)
        dist_pct = round((ma_val - twii_now) / max(1.0, twii_now) * 100.0, 2)
        p2x_at_ma = calc_target_2x(ma_val)
        p1x_at_ma = calc_target_1x(ma_val)
        slope_str = "📉 下彎蓋頭" if ma_val < ma_prev_val else "📈 上揚支撐"
        deduct_str = f"扣高 {deduct_val:,.0f} (助跌)" if deduct_val > twii_now * 1.01 else f"扣低 {deduct_val:,.0f} (助漲)"
        pos_str = f"上方 +{dist_pts:,.0f}點 (+{dist_pct:.2f}%)" if dist_pts >= 0 else f"下方 {dist_pts:,.0f}點 ({dist_pct:.2f}%)"
        return {
            "均線名稱與週期": ma_name,
            "當前動態點位 (距現價)": f"{ma_val:,.0f} 點 ({pos_str})",
            "均線方向 ＆ 扣抵狀態": f"{slope_str} ｜ {deduct_str}",
            "空方勢實戰定性 (壓力 vs 反轉)": role_tag,
            "對應正2 / 0050 預估價": f"正2: {p2x_at_ma:.2f}元 ｜ 0050: {p1x_at_ma:.2f}元",
            "母子艦隊具體操作指令": action_guide
        }

    df_ma_matrix = pd.DataFrame([
        build_ma_guide_row(
            "① 60分K 5MA (極短線)", fleet["k60_ma5"], fleet["k60_ma5"] + 50, fleet["k60_ma5"] + 150,
            "🔑 【微觀止跌第一關】",
            "未站上此線前代表盤中急殺未止，嚴禁左側摸底；站上才確認當日止跌。"
        ),
        build_ma_guide_row(
            "② 日K 5MA (五日攻擊線)", twii_ma5, fleet["ma5_prev"], fleet["deduct_5d"],
            "🔑 【短線止跌確認線】(破單邊殺盤)",
            f"重疊外資滿足區({target_cover_2:,.0f}點)！收盤實體紅K突破日5MA，確認展開日線級跌深反彈。"
        ),
        build_ma_guide_row(
            "③ 日K 10MA ＆ 月K 5月線", twii_ma10, fleet["ma10_prev"], fleet["deduct_10d"],
            "🚨 【第一道蓋頭反壓】(空方慣性線)",
            f"空方勢沿10MA下殺！首次反彈觸及下彎10MA ({t_ma10:.2f}元) 必遇壓，短差者注意震盪。"
        ),
        build_ma_guide_row(
            "④ 日K 120MA (半年線)", twii_ma120, fleet["ma120_prev"], fleet["deduct_120d"],
            "🚨 【第二道重蓋頭反壓】(首波減碼點)",
            f"已越過總資產翻紅點({twii_breakeven:,.0f}點)！反彈初碰半年線 ({t_ma120:.2f}元) 若60分J彎頭，先減碼 1張正2 降槓桿！"
        ),
        build_ma_guide_row(
            "⑤ 日K 60MA (季線・外資成本區)", twii_ma60, fleet["ma60_prev"], fleet["deduct_60d"],
            "🚨 【第三道終極蓋頭反壓】(全數歸位點)",
            f"重疊純換檔兩平點({twii_switch_be:,.0f}點)與外資成本！反彈至此 ({t_ma60:.2f}元) 賣出增持之 2張正2 買回600股0050！"
        ),
        build_ma_guide_row(
            "⑥ 日K 20MA (月線・多空生命線)", twii_ma20, fleet["ma20_prev"], fleet["deduct_20d"],
            "🏆 【初碰大反壓 ➔ 二測突破真反轉】",
            "V轉初碰下彎月線必拉回！須等拉回打W底第二隻腳、月線走平後第2次突破，才是波段『空翻多真反轉』！"
        ),
        build_ma_guide_row(
            "⑦ 日K 240MA (年線・牛熊防線)", twii_ma240, fleet["ma240_prev"], twii_ma240 * 0.92,
            "💎 【長線唯一上揚鐵底】(終極支撐)",
            f"目前唯一保持上揚之長線均線 ({t_ma240:.2f}元)，若極端黑天鵝殺至此處為歷史級重裝買點。"
        )
    ])
    st.dataframe(df_ma_matrix, use_container_width=True, hide_index=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ==========================================
    # 🧭 第三順位：大字體高對比強化版【五大衍生品、純個股維持率與台幣匯率雷達】（升級為 5 欄大字卡！）
    # ==========================================
    st.markdown("#### 🧭 大盤五大籌碼與台幣匯率雷達（外資空單成本 × 純個股維持率 × 台幣匯率 × 選擇權PCR × 權證避險）")
    d_col1, d_col2, d_col3, d_col4, d_col5 = st.columns(5)

    cover_reached = twii_now <= target_cover_2 * 1.008
    cover_badge_color = "#00e676" if (cover_reached and in_fini_chg >= 0) else "#fbbf24"
    if cover_reached and in_fini_chg <= -1500:
        cover_badge_text = f"⚠️ 達滿足點惟外資今仍追空 {in_fini_chg:+,}口"
    elif cover_reached:
        cover_badge_text = f"🎯 已達終極回補滿足區 ({target_cover_2:,.0f}點)！"
    else:
        cover_badge_text = f"⏳ 接近終極滿足區 ({target_cover_2:,.0f}點)"

    with d_col1:
        fini_chg_color = "#ff4d4f" if in_fini_chg > 0 else "#00e676"
        st.markdown(f"""
        <div class="radar-card-highlight">
            <div class="radar-title">1. 外資實質空單 (扣投信正2)</div>
            <div class="radar-big-val" style="color:#ffd54f;">{real_directional_fini_oi:+,} 口</div>
            <div class="radar-sub-highlight" style="background:rgba(245,158,11,0.18); color:#fcd34d; border:1px solid #f59e0b;">
                🎯 外資佈空成本：<span style="font-size:20px; color:#ffffff;">{est_short_cost:,.0f} 點</span><br>
                💰 每口已賺：<span style="color:#00e676;">+{short_profit_pts:,.0f} 點 (+{short_profit_pct:.1f}%)</span>
            </div>
            <div class="radar-sub-highlight" style="background:rgba(0,230,118,0.15); color:{cover_badge_color};">
                🏁 滿足點：<span style="font-size:20px;">{target_cover_2:,.0f} 點</span><br>
                ({cover_badge_text})
            </div>
            <div class="radar-detail-row">• 表面外資：<b>{in_fini_oi:+,} 口</b></div>
            <div class="radar-detail-row">• 投信正2：<b style="color:#ff4d4f;">+{in_sitc_oi:,} 口</b></div>
            <div class="radar-detail-row">• 單日增減：<b style="color:{fini_chg_color};">{in_fini_chg:+,} 口</b></div>
            <div class="radar-score-pill">得分：{s2_fut} / 25 分</div>
        </div>
        """, unsafe_allow_html=True)

    with d_col2:
        if in_maint_pct < 130.0:
            maint_color = "#ff4d4f"
            maint_zone_label = "💎 <130% 歷史級全面斷頭底！"
        elif in_maint_pct <= 135.0:
            maint_color = "#00e676"
            maint_zone_label = f"🚨 {in_maint_pct:.1f}% 破135%！T+2斷頭倒數底！"
        elif in_maint_pct <= 145.0:
            maint_color = "#00e676"
            maint_zone_label = "🟢 135%~145% 常態落底甜美區！"
        else:
            maint_color = "#fbbf24"
            maint_zone_label = "🟡 >145% 散戶浮額洗盤中"

        st.markdown(f"""
        <div class="radar-card-highlight">
            <div class="radar-title">2. 純個股融資維持率 (排除ETF)</div>
            <div class="radar-big-val" style="color:{maint_color};">{in_maint_pct:.1f} %</div>
            <div class="radar-sub-highlight" style="background:rgba(0,230,118,0.16); color:{maint_color}; border:1px solid {maint_color};">
                {maint_zone_label}
            </div>
            <div class="radar-detail-row">• 常態落底區：<b style="color:#ffd54f;">135% ～ 145%</b></div>
            <div class="radar-detail-row">• 斷頭法規：<b>&lt;135% ➔ T+2 早盤斷頭</b></div>
            <div class="radar-detail-row">• 極端參考：<b>&lt; 130%</b> (2025/4關稅戰)</div>
            <div class="radar-detail-row">• 範圍：<b>剔除 00xx ETF</b></div>
            <div class="radar-score-pill">得分：{s1_maint} / 25 分</div>
        </div>
        """, unsafe_allow_html=True)

    with d_col3:
        # 台幣匯率卡片：升值(負值)代表熱錢流入用紅色/亮綠標示好消息；貶值(正值)代表外資提款用橘黃色警戒
        fx_is_appreciating = (fx_chg_ntd < 0) and (fx_now <= fx_ma5)
        fx_card_color = "#00e676" if fx_is_appreciating else ("#fbbf24" if fx_chg_ntd <= 0.02 else "#ff4d4f")
        fx_dir_str = f"升值 {abs(fx_cents_chg):.1f} 分" if fx_chg_ntd < 0 else (f"貶值 {abs(fx_cents_chg):.1f} 分" if fx_chg_ntd > 0 else "持平")
        fx_badge = "🟢 台幣升破5MA・熱錢回流止跌！" if fx_is_appreciating else ("🟡 台幣高檔震盪・觀察5MA轉折" if fx_chg_ntd <= 0.02 else "🚨 台幣續貶・外資提款匯出中")
        fx_score_adj = "+5 分 (止貶獎勵)" if fx_is_appreciating else ("-5 分 (續貶警戒)" if (fx_now > fx_ma5 and fx_chg_ntd >= 0.04) else "0 分 (中性觀察)")

        st.markdown(f"""
        <div class="radar-card-highlight">
            <div class="radar-title">3. 新台幣匯率 (USD/TWD 熱錢)</div>
            <div class="radar-big-val" style="color:{fx_card_color};">{fx_now:.3f} 元</div>
            <div class="radar-sub-highlight" style="background:rgba(255,255,255,0.08); color:{fx_card_color}; border:1px solid {fx_card_color};">
                今日：{fx_dir_str}<br>{fx_badge}
            </div>
            <div class="radar-detail-row">• 匯率 5MA 轉折線：<b style="color:#ffd54f;">{fx_ma5:.3f} 元</b></div>
            <div class="radar-detail-row">• 匯率 20MA 月線：<b>{fx_ma20:.3f} 元</b></div>
            <div class="radar-detail-row">• 止跌關鍵：<b>匯價跌破 {fx_ma5:.3f} = 台幣轉升</b></div>
            <div class="radar-score-pill">匯率加減分：{fx_score_adj}</div>
        </div>
        """, unsafe_allow_html=True)

    with d_col4:
        pcr_color = "#00e676" if in_pcr <= 75.0 else ("#ff4d4f" if in_pcr >= 100.0 else "#fbbf24")
        pcr_penalty_note = "⚠️ PCR<70% 觸發熔斷扣10分" if in_pcr < 70.0 else ("⚠️ PCR<80% 莊家偏空扣5分" if in_pcr < 80.0 else "✅ PCR>=80% 無扣分")
        st.markdown(f"""
        <div class="radar-card">
            <div class="radar-title">4. 選擇權全月份 P/C Ratio</div>
            <div class="radar-big-val" style="color:{pcr_color};">{in_pcr:.1f} %</div>
            <div class="radar-sub-highlight" style="background:rgba(255,255,255,0.08); color:{pcr_color};">
                {pcr_penalty_note}
            </div>
            <div class="radar-detail-row">• 距平衡(100%)：<b>{in_pcr - 100.0:+.1f}%</b></div>
            <div class="radar-detail-row">• 軋空臨界：<b style="color:#ffd54f;">&lt;60%~70% 勾頭即軋空</b></div>
            <div class="radar-detail-row">• 來源：<b>期交所 14:00 直連</b></div>
            <div class="radar-score-pill">得分：{s3_pcr} / 12 分</div>
        </div>
        """, unsafe_allow_html=True)

    with d_col5:
        war_color = "#ff4d4f" if in_warrant_yi > 0 else ("#00e676" if in_warrant_yi <= -35.0 else "#fbbf24")
        war_tag = "🔥 認購權證大戶點火" if in_warrant_yi > 0 else ("🎯 認購恐慌停損極端宣洩！" if in_warrant_yi <= -35.0 else "📉 權證短線偏空調節")
        st.markdown(f"""
        <div class="radar-card">
            <div class="radar-title">5. 權證：自營商(避險)</div>
            <div class="radar-big-val" style="color:{war_color};">{in_warrant_yi:+.2f} 億</div>
            <div class="radar-sub-highlight" style="background:rgba(255,255,255,0.08); color:{war_color};">
                {war_tag}
            </div>
            <div class="radar-detail-row">• 意義：<b>權證真實 Delta 避險</b></div>
            <div class="radar-detail-row">• 特徵：<b style="color:#ffd54f;">大賣逾35億為停損宣洩底</b></div>
            <div class="radar-detail-row">• 來源：<b>證交所 BFI82U</b></div>
            <div class="radar-score-pill">得分：{s3_war} / 8 分</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ==========================================
    # 🏛️ 第四順位：加權指數與母子艦隊四欄資產司令塔
    # ==========================================
    st.markdown("#### 🏛️ 加權指數多週期技術位階 ＆ 母子艦隊雙帳本真實損益司令塔")
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown("##### 📊 加權指數 (日K＋60分K)")
        st.metric(
            label=f"台股大盤 ({fleet['date_str']})",
            value=f"{twii_now:,.1f} 點",
            delta=f"{fleet['twii_chg_pct']:+.2f}% (開盤 {twii_open:,.0f} | 高低 {fleet['twii_low']:,.0f}~{fleet['twii_high']:,.0f})",
            delta_color="inverse"
        )
        w_status = "✅守穩" if twii_now >= twii_w_ma10 else "⚠️跌破"
        m_status = "✅有守" if twii_now >= twii_m_ma5 else "❌失守"
        ma120_status = "✅守穩" if twii_now >= twii_ma120 else "🚨跌破(扣15分)"
        st.caption(
            f"• 短期反壓：5MA `{twii_ma5:,.0f}` (`{fleet['bias_5']:+.1f}%`) ｜ 10MA `{twii_ma10:,.0f}` (`{fleet['bias_10']:+.1f}%`)\n\n"
            f"• 中期反壓：月線 `{twii_ma20:,.0f}` (`{fleet['bias_20']:+.1f}%`) ｜ 季線 `{twii_ma60:,.0f}` (`{fleet['bias_60']:+.1f}%`)\n\n"
            f"• 長線均線：**半年線 `{twii_ma120:,.0f}`** ({ma120_status}) ｜ **年線 `{twii_ma240:,.0f}`**\n\n"
            f"• 週月保護傘：10週線 `{twii_w_ma10:,.0f}` ({w_status}) ｜ **5月線 `{twii_m_ma5:,.0f}` ({m_status})**\n\n"
            f"• **日KDJ / 60分KDJ**：日 `J={j_now}` ({j_trend_str}) ｜ 60分 `J={k60_j}` ({k60_j_trend})"
        )

    with col2:
        st.markdown("##### 🚀 0050正2 (子艦隊)")
        st.metric(
            label=f"00631L 現價 (今日 {fleet['p_2x_chg']:+.2f}%)",
            value=f"{p_2x:,.2f} 元",
            delta=f"{pnl_2x:+,} 元 (券商帳面 {pnl_2x_pct:+.2f}%)",
            delta_color="inverse"
        )
        st.caption(
            f"• 當前持有：**`{in_lots_2x:.2f} 張`** (券商均價 `{in_cost_2x:.2f}` / 裸價 `34.94`)\n\n"
            f"• 換檔買進：`10/28 買1張@36.68` ＋ `11/2 買1張@33.98`\n\n"
            f"• 換檔增減：**`{diff_shares_2x:+,} 股`** ｜ 市值：`{val_2x:,} 元` (`{ratio_2x_pct}%`)\n\n"
            f"• 均線反壓對應：5MA `{t_ma5:.2f}` / 半年線 `{t_ma120:.2f}` / 季線 `{t_ma60:.2f}`"
        )

    with col3:
        st.markdown("##### 🛡️ 0050 (壓艙石)")
        st.metric(
            label=f"0050 現價 (今日 {fleet['p_1x_chg']:+.2f}%)",
            value=f"{p_1x:,.2f} 元",
            delta=f"{pnl_1x:+,} 元 (券商帳面 {pnl_1x_pct:+.2f}%)",
            delta_color="inverse"
        )
        st.caption(
            f"• 當前持有：**`{in_lots_1x:.2f} 張`** (原始 `1.0張` @成本 `{in_cost_1x:.2f}`)\n\n"
            f"• 換檔賣出：`10/28 賣300股@108.3` ＋ `11/2 賣300股@104.4`\n\n"
            f"• 已實現獲利：**`+{realized_1x_gain:,} 元`** (`+1,920` + `+750`)\n\n"
            f"• 換檔增減：**`{diff_shares_1x:+,} 股`** ｜ 市值：`{val_1x:,} 元` (`{ratio_1x_pct}%`)"
        )

    with col4:
        st.markdown("##### 💰 總市值 (扣除補貼新資金)")
        st.metric(
            label=f"當前總市值 (累計總投入本金 {total_invested_capital:,} 元)",
            value=f"{total_fleet_val:,} 元",
            delta=f"{true_total_pnl:+,} 元 (真實總報酬 {true_total_pnl_pct:+.2f}%)",
            delta_color="inverse"
        )
        st.caption(
            f"• ⚓ **本金結構**：原始 `{initial_base_capital:,}` ＋ **自貼新資金 `{extra_cash_injected:,}`** ＝ `{total_invested_capital:,}元`\n\n"
            f"• ⚡ **換檔純價差 (Alpha)**：**`{switching_alpha:+,} 元`** *(10/28 `{alpha_1028:+,}` + 11/2 `{alpha_1102:+,}`)*\n\n"
            f"• 📋 **動態損益驗算**：已實現 `+{realized_1x_gain:,}` + 0050未實現 `{pnl_1x:+,}` + 正2裸價未實現 `{bare_unrealized_pnl_2x:+,}` = **`{true_total_pnl:+,}元`**\n\n"
            f"• 🚀 **有效曝險**：**`{effective_leverage}%`** *(資產翻紅：`{twii_breakeven:,.0f}點` / 純換檔翻紅：`{twii_switch_be:,.0f}點`)*"
        )

    st.markdown("---")

    # ==========================================
    # 🔄 第五順位：表 C 與 表 A
    # ==========================================
    st.markdown(f"#### 🔄 表 C：【賣 600股0050 ＋ 自貼 `{extra_cash_injected:,}元` 買 2張正2 ➔ 反彈賣 2張正2 買回 600股0050】真實閉環損益精算表（已按點位由低至高自動排序）")

    def calc_roundtrip_row(stage_name, target_idx, target_p2x, target_p1x):
        val_curr_at_t = int(round(in_lots_2x * 1000 * target_p2x)) + int(round(in_lots_1x * 1000 * target_p1x)) + int(round(in_cash_pool))
        true_profit_at_t = val_curr_at_t - total_invested_capital
        true_profit_pct = round((true_profit_at_t / max(1, total_invested_capital)) * 100, 2)
        sell_extra_2x_net = int(round(extra_2x_shares * target_p2x * (1.0 - 0.002425)))
        buyback_1x_cost = int(round(short_1x_shares * target_p1x * 1.001425))
        pure_switch_net = sell_extra_2x_net - extra_cash_injected - buyback_1x_cost
        free_2x_shares = int(round(pure_switch_net / max(1.0, target_p2x)))
        return {
            "_sort_idx": float(target_idx),
            "動態均線與反彈錨點": stage_name,
            "大盤動態點位": f"{target_idx:,.0f} 點",
            "正2 / 0050 預估價": f"正2: {target_p2x:.2f}元 ｜ 0050: {target_p1x:.2f}元",
            "艦隊預計總市值": f"{val_curr_at_t:,} 元",
            "🏆 真實累計總損益 (已扣新資金)": f"{true_profit_at_t:+,} 元 ({true_profit_pct:+.2f}%)",
            "⚡ 歸還自貼6,850元並買回600股0050後之【純換檔淨利】": f"純換檔損益 {pure_switch_net:+,} 元 (相當於 {free_2x_shares:+,} 股正2)"
        }

    raw_rt_rows = [
        calc_roundtrip_row("📍 目前現況 (最新收盤/即時價)", twii_now, p_2x, p_1x),
        calc_roundtrip_row("🔑 短線止跌確認線：日K 5MA (五日線)", twii_ma5, t_ma5, t1x_ma5),
        calc_roundtrip_row("🏁 外資終極獲利滿足錨點 (-7.5%成本線)", target_cover_2, calc_target_2x(target_cover_2), calc_target_1x(target_cover_2)),
        calc_roundtrip_row("🚨 第一道蓋頭反壓：日K 10MA / 月K 5月線", twii_m_ma5, t_m_ma5, t1x_m5),
        calc_roundtrip_row("🏁 外資初步回補目標區 (-4.0%成本線)", target_cover_1, calc_target_2x(target_cover_1), calc_target_1x(target_cover_1)),
        calc_roundtrip_row("⚖️ 總資產翻紅損益兩平點 (總市值=212,790元)", twii_breakeven, p2x_breakeven, p1x_breakeven),
        calc_roundtrip_row("🚨 第二道蓋頭反壓：日K 120MA 半年線 (首道減碼點)", twii_ma120, t_ma120, t1x_ma120),
        calc_roundtrip_row("🚨 第三道蓋頭反壓：日K 60MA 季線", twii_ma60, t_ma60, t1x_ma60),
        calc_roundtrip_row("🔄 純換檔損益兩平點 (歸還6,850元+買回600股0050不賠錢)", twii_switch_be, p2x_switch_be, p1x_switch_be),
        calc_roundtrip_row("🏆 波段反轉分水嶺：日K 20MA 月線 (初碰大壓・二測突破真反轉)", twii_ma20, t_ma20, t1x_ma20),
        calc_roundtrip_row("👑 動態日布林上軌收割區", twii_harvest_zone, t_harvest, t1x_harvest)
    ]
    df_roundtrip = pd.DataFrame(raw_rt_rows).sort_values(by="_sort_idx", ascending=True).drop(columns=["_sort_idx"])
    st.dataframe(df_roundtrip, use_container_width=True, hide_index=True)

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
    st.caption("篩選基準：100% 鎖定「電子權值龍頭」與「績優金融金控」（徹底剔除一般傳產與營建股）！嚴格要求站穩月線支撐、DMI (+DI>-DI) 多方控盤且未過熱。")
    if not df_long_bc.empty:
        st.dataframe(style_tw_stock_df(df_long_bc), use_container_width=True, hide_index=True)
    else:
        st.info("今日大盤回檔，暫無同時站上月線且 DMI 多方之權值股；請參考下方【表 3-B】之權值股逢低佈局買點與轉機股！")

    st.markdown("#### 🔄 【表 3-B：🔄 轉機題材特選股（明確標註轉機股）＆ 💎 電子/金融權值股回測長線支撐買點榜（已按右側止穩優先排序）】")
    st.caption("篩選基準：(1) 【🔄 轉機股】：具備營收爆發(YoY>=25%)＋大戶鎖碼(>=48%)＋DMI多頭翻揚；(2) 【🟢 權值右側止穩 / ⏳ 權值左側超跌】：隨大盤回測長線支撐且日J值入低檔之權值龍頭，自動優先排序今日抗跌收紅或J值勾頭股。")
    if not df_long_turn.empty:
        st.dataframe(style_tw_stock_df(df_long_turn), use_container_width=True, hide_index=True)
    else:
        st.info("目前暫無符合條件之轉機題材股或權值回測買點。")

if auto_refresh:
    time.sleep(20)
    fetch_mis_realtime_batch.clear()
    st.rerun()
