import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import datetime, pytz, requests, urllib3, io, os, json, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="台股每日戰情室 v54.0", page_icon="🧭", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
.war-hero-box { background: linear-gradient(135deg, #0f2027 0%, #203a43 50%, #2c5364 100%); color: #fff; padding: 20px 24px; border-radius: 14px; border-left: 8px solid #00e676; margin-bottom: 18px; }
.war-hero-box-warn { background: linear-gradient(135deg, #2b1d1d 0%, #3d251e 50%, #4a2c2a 100%); color: #fff; padding: 20px 24px; border-radius: 14px; border-left: 8px solid #ff9800; margin-bottom: 18px; }
.war-score-title { font-size: 25px; font-weight: 900; color: #ffd54f; margin-bottom: 6px; }
.war-circuit-trace { font-size: 15.5px; font-weight: 700; color: #ffab91; background: rgba(0,0,0,0.35); padding: 5px 12px; border-radius: 6px; margin-bottom: 10px; display: inline-block; }
.war-grade-banner { font-size: 19px; font-weight: 800; padding: 7px 14px; border-radius: 8px; margin-bottom: 12px; display: inline-block; }
.war-dim-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; margin: 10px 0 14px 0; }
.war-dim-item { background: rgba(255,255,255,0.08); padding: 9px 13px; border-radius: 8px; font-size: 15px; line-height: 1.45; }
.war-action-box { background: rgba(0,0,0,0.32); padding: 13px 16px; border-radius: 10px; border: 1px solid rgba(255,213,79,0.45); font-size: 16.5px; line-height: 1.6; }
.radar-card { background: #161b22; color: #f0f6fc; padding: 15px 16px; border-radius: 12px; border: 1px solid #30363d; height: 100%; }
.radar-card-highlight { background: #1a1f2c; color: #fff; padding: 15px 16px; border-radius: 12px; border: 2px solid #f59e0b; height: 100%; }
.radar-title { font-size: 16px; font-weight: 800; color: #9ca3af; margin-bottom: 4px; }
.radar-big-val { font-size: 29px; font-weight: 900; line-height: 1.2; margin: 4px 0 8px 0; }
.radar-sub-highlight { font-size: 16px; font-weight: 800; padding: 5px 9px; border-radius: 6px; margin: 5px 0; display: block; }
.radar-detail-row { font-size: 15px; font-weight: 600; color: #e5e7eb; margin: 4px 0; line-height: 1.4; }
.radar-score-pill { display: inline-block; margin-top: 6px; padding: 3px 10px; border-radius: 20px; font-size: 15px; font-weight: 800; background: #2563eb; color: #fff; }
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

def style_tw_stock_df(df):
    if df.empty: return df
    def color_tw_val(val):
        try:
            s = str(val).replace("%", "").replace(",", "").replace("+", "").replace("元", "").replace("倍", "").replace("張", "").strip()
            if s in ["", "-", "--", "待公告", "未揭露", "虧損/無PE"]: return ""
            v = float(s)
            return "color: #ff3b30; font-weight: bold;" if v > 0 else ("color: #00b853; font-weight: bold;" if v < 0 else "")
        except Exception: return ""
    target_cols = [c for c in ["今日%", "3日%", "外資買賣(張)", "投信買賣(張)", "單月營收YoY", "累計年營收YoY", "月營收YoY"] if c in df.columns]
    fmt_dict = {k: "{:.2f}" for k in ["現價", "💎EPS合理切入價", "🎯EPS目標價", "預估EPS(元)", "5MA量比", "天花板(反壓)", "月線承接", "鋼鐵防線", "極限停損"] if k in df.columns}
    for k_int in ["外資買賣(張)", "投信買賣(張)"]:
        if k_int in df.columns: fmt_dict[k_int] = "{:+,d}"
    if "本益比(PE)" in df.columns: fmt_dict["本益比(PE)"] = "{:.1f}"
    if "今日%" in df.columns: fmt_dict["今日%"] = "{:+.2f}%"
    if "3日%" in df.columns: fmt_dict["3日%"] = "{:+.2f}%"
    if "日J值" in df.columns: fmt_dict["日J值"] = "{:.1f}"
    try: return df.style.map(color_tw_val, subset=target_cols).format(fmt_dict, na_rep="-")
    except Exception: return df

def classify_revenue_note_quality(note_txt, sector_str="", code_str=""):
    s = str(note_txt).strip()
    if "建材營造" in sector_str or "營造" in sector_str or any(k in s for k in ["建案", "交屋", "房地", "不動產", "過戶", "營建", "售地"]):
        return "🏗️ 營建交屋認列(排除波段)"
    if not s or s in ["-", "無", "NA", "None", "nan"]:
        return "✅ 正常本業營運"
    if any(k in s for k in ["評價損益", "投資標的", "公允價值", "處分資產", "處分利益", "賠償", "和解", "補助"]):
        return "⚠️ 業外/評價認列(非本業)"
    if any(k in s for k in ["合併", "併購", "新納入子公司", "收購"]):
        return "🔶 併購納入營收(觀察續航)"
    if any(k in s for k in ["專案", "完工比例", "工程", "驗收", "一次性", "授權", "里程碑", "標案", "交貨驗收"]):
        return "⚠️ 專案/里程碑認列(防隔月回落)"
    if any(k in s for k in ["AI", "伺服器", "液冷", "散熱", "新產品", "產能", "量產", "訂單", "需求", "出貨", "旺季", "雲端", "拉貨"]):
        return "💎 真本業訂單/擴產爆發！"
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

# ==========================================
# 模組 2：淘金客 EPS 預估 SOP v2.0（時序收斂 ＋ 三率體質乘數 T）
# ==========================================
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

MARGIN_DILUTION_SET = {"2382", "3231", "2317", "4938", "2356", "2324"}
MARGIN_EXPANSION_SET = {"2330", "3017", "3324", "3653", "2059", "2383", "2368", "6274", "2345", "2308", "3711", "6187", "3131", "3583", "5274", "2301", "3443", "3661", "2454"}

def compute_eps_valuation(code, curr_p, sector_str, single_yoy, cum_yoy, pe_official, support_price=None):
    if curr_p is None or pd.isna(curr_p) or curr_p <= 0:
        return np.nan, np.nan, np.nan, np.nan, "➖ 暫無報價"

    is_fin = ("金融" in sector_str) or (code in ["2881", "2882", "2891", "2886", "2884", "2885", "2892", "2880", "5880", "2890", "2883", "2887", "2801", "2834"])
    if is_fin: pe_low, pe_high = 12.0, 15.5
    elif any(k in sector_str for k in ["IC設計", "矽智財", "散熱", "BMC", "ASIC", "高速傳輸", "CoWoS"]) or code in ["2454", "3034", "2379", "3017", "3653", "2059", "5274", "3529", "3661", "3443", "6669", "3324", "5269", "6187", "3131"]:
        pe_low, pe_high = 20.0, 28.0
    elif any(k in sector_str for k in ["晶圓", "封測", "CCL", "PCB", "連接器", "鏡頭", "電源"]) or code in ["2330", "3711", "2308", "3533", "2383", "2368", "3008", "2449", "3264", "6274"]:
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
        tech_target = round(curr_p * 1.12, 2)
        return est_eps, pe_val, tech_entry, tech_target, f"🌱 初轉盈高PE ({pe_val:.0f}倍・守{tech_entry}) [{t_badge}]"
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
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}?interval=1m&range=1d&_={int(time.time() * 1000)}"
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
                if p_now > 0:
                    return {
                        "open": round(o_now, decimals), "close": round(p_now, decimals),
                        "high": round(max(h_now, p_now), decimals), "low": round(min(l_now, p_now), decimals),
                        "prev_close": round(y_prev, decimals), "chg_pct": round(((p_now - y_prev) / y_prev) * 100, 2) if y_prev > 0 else 0.0,
                        "vol_lots": 0, "time": dt_obj.strftime("%H:%M:%S"), "date": dt_obj.strftime("%Y%m%d"), "ts": mkt_time
                    }
    except Exception: pass
    return None

@st.cache_data(ttl=10, show_spinner=False)
def fetch_mis_realtime_batch(code_market_tuples):
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp", "Cache-Control": "no-cache"}
    rt_dict = {}
    queries = ["tse_t00.tw"] + [f"{'tse' if m == 'TW' else 'otc'}_{c}.tw" for c, m in code_market_tuples]
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

@st.cache_data(ttl=600, show_spinner=False)
def fetch_official_market_data():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    price_dict, tdcc_dict, rev_dict = {}, {}, {}
    twii_official = {"close": 0.0, "chg_pct": 0.0, "date": ""}
    now_tw = get_tw_now()

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
                if len(code) == 4 and code.isdigit():
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
                        vol_lots = int(float(v_str) // 1000) if v_str.replace(".", "").isdigit() else 0
                        if c_price > 0:
                            price_dict[code] = {
                                "name": str(row.get("Name", "")).strip(), "close": c_price,
                                "chg_pct": round((chg_val / prev_c) * 100, 2) if prev_c > 0 else 0.0,
                                "vol_lots": vol_lots, "market": "TW", "pe": np.nan, "pb": np.nan,
                                "cum_yoy": None, "fini_lots": 0, "sitc_lots": 0, "sector": ""
                            }
                    except Exception: continue
    except Exception: pass

    try:
        r_tpex = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", headers=headers, timeout=10, verify=False)
        if r_tpex.status_code == 200:
            for row in r_tpex.json():
                code = str(row.get("SecuritiesCompanyCode", row.get("Code", ""))).strip()
                if len(code) == 4 and code.isdigit():
                    try:
                        c_str = str(row.get("Close", row.get("ClosingPrice", ""))).replace(",", "").strip()
                        if not c_str or c_str in ["-", "--", "N/A", ""]: continue
                        c_price = float(c_str)
                        chg_raw = str(row.get("Change", "0")).replace(",", "").replace("+", "").replace(" ", "").strip()
                        try: chg_val = float(chg_raw)
                        except Exception: chg_val = 0.0
                        prev_c = c_price - chg_val
                        v_str = str(row.get("TradingShares", row.get("TradeVolume", "0"))).replace(",", "").strip()
                        try: vol_lots = int(float(v_str) // 1000)
                        except Exception: vol_lots = 0
                        if c_price > 0:
                            price_dict[code] = {
                                "name": str(row.get("CompanyName", row.get("Name", ""))).strip(), "close": c_price,
                                "chg_pct": round((chg_val / prev_c) * 100, 2) if prev_c > 0 else 0.0,
                                "vol_lots": vol_lots, "market": "TWO", "pe": np.nan, "pb": np.nan,
                                "cum_yoy": None, "fini_lots": 0, "sitc_lots": 0, "sector": ""
                            }
                    except Exception: continue
    except Exception: pass

    otc_cnt = sum(1 for v in price_dict.values() if v.get("market") == "TWO")
    if otc_cnt < 100:
        try:
            r_otc2 = requests.get("https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&o=json", headers=headers, timeout=8, verify=False)
            if r_otc2.status_code == 200:
                for row in r_otc2.json().get("aaData", []):
                    if len(row) >= 9:
                        code = str(row[0]).strip()
                        if len(code) == 4 and code.isdigit():
                            try:
                                c_price = float(str(row[2]).replace(",", "").strip())
                                try: chg_val = float(str(row[3]).replace(",", "").replace("+", "").strip())
                                except Exception: chg_val = 0.0
                                prev_c = c_price - chg_val
                                try: vol_lots = int(float(str(row[8]).replace(",", "").strip()) // 1000)
                                except Exception: vol_lots = 0
                                if c_price > 0:
                                    price_dict[code] = {
                                        "name": str(row[1]).strip(), "close": c_price,
                                        "chg_pct": round((chg_val / prev_c) * 100, 2) if prev_c > 0 else 0.0,
                                        "vol_lots": vol_lots, "market": "TWO", "pe": np.nan, "pb": np.nan,
                                        "cum_yoy": None, "fini_lots": 0, "sitc_lots": 0, "sector": ""
                                    }
                            except Exception: continue
        except Exception: pass

    for d_back in range(0, 5):
        chk_dt = now_tw - datetime.timedelta(days=d_back)
        if chk_dt.weekday() >= 5: continue
        d_str_twse = chk_dt.strftime("%Y%m%d")
        try:
            r_t86 = requests.get(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={d_str_twse}&selectType=ALLBUT0999&response=json", headers=headers, timeout=7, verify=False)
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

    try:
        r_pe_tw = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", headers=headers, timeout=10, verify=False)
        if r_pe_tw.status_code == 200:
            for row in r_pe_tw.json():
                code = str(row.get("Code", "")).strip()
                if code in price_dict:
                    try:
                        pe_s = str(row.get("PEratio", "")).replace(",", "").strip()
                        if pe_s and pe_s not in ["-", "--", "N/A", "0", "0.00"]: price_dict[code]["pe"] = round(float(pe_s), 1)
                    except Exception: pass
    except Exception: pass

    try:
        r_pe_otc = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", headers=headers, timeout=10, verify=False)
        if r_pe_otc.status_code == 200:
            for row in r_pe_otc.json():
                code = str(row.get("SecuritiesCompanyCode", row.get("Code", ""))).strip()
                if code in price_dict:
                    try:
                        pe_s = str(row.get("PriceEarningRatio", row.get("PEratio", ""))).replace(",", "").strip()
                        if pe_s and pe_s not in ["-", "--", "N/A", "0", "0.00"]: price_dict[code]["pe"] = round(float(pe_s), 1)
                    except Exception: pass
    except Exception: pass

    try:
        r_csv = requests.get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", headers=headers, timeout=15, verify=False)
        if r_csv.status_code == 200 and len(r_csv.text) > 500:
            df_tdcc = pd.read_csv(io.StringIO(r_csv.text), dtype=str)
            df_tdcc.columns = [c.strip() for c in df_tdcc.columns]
            col_code = next((c for c in df_tdcc.columns if "代號" in c), None)
            col_lvl = next((c for c in df_tdcc.columns if "分級" in c), None)
            col_pct = next((c for c in df_tdcc.columns if "比例" in c), None)
            if col_code and col_lvl and col_pct:
                df_sub = df_tdcc[df_tdcc[col_lvl].str.strip().isin(["12", "13", "14", "15"])].copy()
                df_sub["code_clean"] = df_sub[col_code].str.strip()
                df_sub["pct_num"] = pd.to_numeric(df_sub[col_pct].str.replace(",", ""), errors="coerce").fillna(0.0)
                grouped_dict = df_sub.groupby("code_clean")["pct_num"].sum().to_dict()
                tdcc_dict = {str(k): round(float(v), 1) for k, v in grouped_dict.items() if 0.0 < float(v) <= 100.0}
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

    def build_turnaround_tag(curr_rev_k, yoy_pct, mom_pct, cum_rev_k, cum_yoy_pct, sector_str, note_str=""):
        if "建材營造" in sector_str or "營造" in sector_str or any(k in str(note_str) for k in ["建案", "交屋", "房地", "不動產", "過戶", "營建", "售地"]):
            return "🏗️ 營建入帳認列(排除波段)"
        if "評價損益" in note_str or "投資標的" in note_str:
            return "💼 投資評價變動(非本業實業)"
        if pd.isna(yoy_pct) or yoy_pct < 20.0:
            return "➖ 一般波動"
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
                    curr_p = p_info.get("close", np.nan)
                    f_lots, s_lots = p_info.get("fini_lots", 0), p_info.get("sitc_lots", 0)
                    inst_tag = format_inst_chip_tag(f_lots, s_lots)
                    est_eps, pe_v, eps_entry, eps_target, val_tag = compute_eps_valuation(code, curr_p, sector, yoy_pct, cum_yoy_pct, p_info.get("pe", np.nan))
                    cb_badge = "🔥含CB可轉債" if code in cb_set else "➖"
                    note_qual = classify_revenue_note_quality(note_txt, sector, code)
                    records_by_code[code] = {
                        "資料年月": ym_raw, "市場": mkt_label, "代碼": code, "公司名稱": name, "產業別": sector or "電子/其他",
                        "轉機與成長型態": build_turnaround_tag(rev_curr_k, yoy_pct, mom_pct, cum_rev_k, cum_yoy_pct, sector, note_txt),
                        "備註AI真偽鑑別": note_qual, "CB可轉債": cb_badge,
                        "法人動向": inst_tag, "外資買賣(張)": f_lots, "投信買賣(張)": s_lots,
                        "現價": curr_p, "今日%": p_info.get("chg_pct", np.nan),
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
    mops_urls = [
        ("上市", f"https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_{latest_roc_y}_{latest_m}_0.html"),
        ("上櫃", f"https://mopsov.twse.com.tw/nas/t21/otc/t21sc03_{latest_roc_y}_{latest_m}_0.html"),
        ("上市", f"https://mops.twse.com.tw/nas/t21/sii/t21sc03_{latest_roc_y}_{latest_m}_0.html"),
        ("上櫃", f"https://mops.twse.com.tw/nas/t21/otc/t21sc03_{latest_roc_y}_{latest_m}_0.html")
    ]
    for mkt_label, m_url in mops_urls:
        try:
            r_mops = requests.get(m_url, headers=headers, timeout=6, verify=False)
            if r_mops.status_code == 200 and len(r_mops.content) > 2000:
                html_txt = r_mops.content.decode("cp950", errors="ignore")
                current_mops_sector = ""
                for block in re.split(r"(<th[^>]*>.*?產業別.*?</th>)", html_txt, flags=re.S | re.I):
                    sec_m = re.search(r"產業別[：:]\s*([^<\s]+)", block)
                    if sec_m:
                        current_mops_sector = sec_m.group(1).strip()
                        continue
                    for tr_match in re.findall(r"<tr[^>]*>(.*?)</tr>", block, flags=re.S | re.I):
                        tds = [re.sub(r"<[^>]+>", "", td).replace("&nbsp;", "").strip() for td in re.findall(r"<td[^>]*>(.*?)</td>", tr_match, flags=re.S | re.I)]
                        if len(tds) >= 10:
                            code = tds[0]
                            if len(code) == 4 and code.isdigit():
                                rev_curr_k = to_float(tds[2])
                                if pd.isna(rev_curr_k) or rev_curr_k <= 0: continue
                                mom_pct, yoy_pct, cum_rev_k, cum_yoy_pct = to_float(tds[5]), to_float(tds[6]), to_float(tds[7]), to_float(tds[9])
                                note_txt = tds[10] if len(tds) > 10 else ""
                                if note_txt in ["-", "無", "NA", "None"]: note_txt = ""
                                existing = records_by_code.get(code, {})
                                p_info = price_dict.get(code, {})
                                clean_name = existing.get("公司名稱") or p_info.get("name") or tds[1]
                                sector = existing.get("產業別") or p_info.get("sector") or current_mops_sector or "電子/轉機"
                                if sector in ["上市", "上櫃", ""]: sector = current_mops_sector or "電子/其他"
                                final_note = note_txt or existing.get("財報營收備註說明", "")
                                curr_p = p_info.get("close", existing.get("現價", np.nan))
                                f_lots, s_lots = p_info.get("fini_lots", 0), p_info.get("sitc_lots", 0)
                                inst_tag = format_inst_chip_tag(f_lots, s_lots)
                                if code in price_dict and pd.notna(cum_yoy_pct): price_dict[code]["cum_yoy"] = round(cum_yoy_pct, 1)
                                est_eps, pe_v, eps_entry, eps_target, val_tag = compute_eps_valuation(code, curr_p, sector, yoy_pct, cum_yoy_pct, p_info.get("pe", np.nan))
                                cb_badge = "🔥含CB可轉債" if code in cb_set else "➖"
                                note_qual = classify_revenue_note_quality(final_note, sector, code)
                                records_by_code[code] = {
                                    "資料年月": latest_ym_str, "市場": mkt_label, "代碼": code, "公司名稱": clean_name, "產業別": sector,
                                    "轉機與成長型態": build_turnaround_tag(rev_curr_k, yoy_pct, mom_pct, cum_rev_k, cum_yoy_pct, sector, final_note),
                                    "備註AI真偽鑑別": note_qual, "CB可轉債": cb_badge,
                                    "法人動向": inst_tag, "外資買賣(張)": f_lots, "投信買賣(張)": s_lots,
                                    "現價": curr_p, "今日%": p_info.get("chg_pct", existing.get("今日%", np.nan)),
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
        futures = [executor.submit(check_single_stock, c) for c in codes_tuple[:150]]
        for fut in as_completed(futures):
            c_res, val_res = fut.result()
            if val_res is not None: verified_results[c_res] = val_res
    return verified_results

def estimate_foreign_short_cost_and_target(highs, lows, closes, volumes):
    if len(closes) < 20:
        curr = float(closes[-1]) if len(closes) > 0 else 44257.2
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
        "fini_tx_oi": -104271, "fini_tx_chg": -779, "sitc_tx_oi": 101870, "sitc_tx_chg": 3351, "dealer_tx_chg": 210,
        "fini_mtx_oi": 4339, "fini_mtx_chg": -1679, "sitc_mtx_chg": 0, "dealer_mtx_chg": 2550, "retail_mtx_chg": -871,
        "pc_ratio": 85.6, "warrant_hedge_yi": 34.56, "official_stock_maint": None, "official_maint_date": "",
        "stock_margin_chg_yi": 0.0, "fut_date": "2026/11/11", "twse_date": ""
    }
    now_tw = get_tw_now()
    pcr_fetched = False
    try:
        r_pcr = requests.get("https://openapi.taifex.com.tw/v1/PutCallRatio", headers=headers, timeout=5, verify=False)
        if r_pcr.status_code == 200 and len(r_pcr.json()) > 0:
            last_row = r_pcr.json()[-1]
            raw_pcr = last_row.get("PutCallOpenInterestRatio", last_row.get("PutCallRatio", last_row.get("PutCallOpenInterestRatio%", None)))
            if raw_pcr is not None:
                val = float(str(raw_pcr).replace(",", "").strip())
                if 30.0 <= val <= 220.0:
                    res["pc_ratio"] = round(val, 1)
                    pcr_fetched = True
    except Exception: pass

    if not pcr_fetched:
        try:
            q_end, q_start = now_tw.strftime("%Y/%m/%d"), (now_tw - datetime.timedelta(days=10)).strftime("%Y/%m/%d")
            r_pcr_csv = requests.post("https://www.taifex.com.tw/cht/3/pcRatioDown", data={"queryStartDate": q_start, "queryEndDate": q_end}, headers=headers, timeout=6, verify=False)
            if r_pcr_csv.status_code == 200 and len(r_pcr_csv.content) > 50:
                df_pcr = pd.read_csv(io.StringIO(r_pcr_csv.content.decode("cp950", errors="ignore")))
                if not df_pcr.empty and df_pcr.shape[1] >= 7:
                    val_csv = float(str(df_pcr.iloc[-1, 6]).replace(",", "").strip())
                    if 30.0 <= val_csv <= 220.0: res["pc_ratio"] = round(val_csv, 1)
        except Exception: pass

    try:
        q_end, q_start = now_tw.strftime("%Y/%m/%d"), (now_tw - datetime.timedelta(days=7)).strftime("%Y/%m/%d")
        r_fut = requests.post("https://www.taifex.com.tw/cht/3/futContractsDateDown", data={"queryStartDate": q_start, "queryEndDate": q_end}, headers=headers, timeout=8, verify=False)
        if r_fut.status_code == 200 and len(r_fut.content) > 200:
            df_f = pd.read_csv(io.StringIO(r_fut.content.decode("cp950", errors="ignore")))
            if len(df_f.columns) >= 14:
                dates = df_f.iloc[:, 0].dropna().unique()
                if len(dates) > 0:
                    latest_d = dates[-1]
                    res["fut_date"] = str(latest_d).strip()
                    for _, row in df_f[df_f.iloc[:, 0] == latest_d].iterrows():
                        prod_name, ident = str(row.iloc[1]).strip(), str(row.iloc[2]).strip()
                        net_trade = int(float(str(row.iloc[7]).replace(",", "").strip() or 0))
                        oi_net = int(float(str(row.iloc[13]).replace(",", "").strip() or 0))
                        if ("臺股期貨" in prod_name or "台股期貨" in prod_name) and ("小型" not in prod_name) and ("微型" not in prod_name):
                            if "外資" in ident:
                                res["fini_tx_chg"] = net_trade
                                if oi_net != 0: res["fini_tx_oi"] = oi_net
                            elif "投信" in ident:
                                res["sitc_tx_chg"] = net_trade
                                if oi_net != 0: res["sitc_tx_oi"] = oi_net
                            elif "自營" in ident: res["dealer_tx_chg"] = net_trade
                        elif "小型臺指" in prod_name or "小型台指" in prod_name:
                            if "外資" in ident:
                                res["fini_mtx_chg"] = net_trade
                                if oi_net != 0: res["fini_mtx_oi"] = oi_net
                            elif "投信" in ident: res["sitc_mtx_chg"] = net_trade
                            elif "自營" in ident: res["dealer_mtx_chg"] = net_trade
                    res["retail_mtx_chg"] = -(res["fini_mtx_chg"] + res["sitc_mtx_chg"] + res["dealer_mtx_chg"])
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
                            res["twse_date"] = chk_d
                            break
                    if res["twse_date"]: break
        except Exception: continue
    return res

ELECTRONICS_BLUE_CHIPS = {
    "2330": ("台積電", "👑電子權值・晶圓龍頭", "TW"), "2317": ("鴻海", "👑電子權值・AI伺服器", "TW"),
    "2454": ("聯發科", "👑電子權值・IC設計龍頭", "TW"), "2308": ("台達電", "👑電子權值・電源與儲能", "TW"),
    "2382": ("廣達", "👑電子權值・AI伺服器", "TW"), "3711": ("日月光投控", "👑電子權值・先進封測", "TW"),
    "3034": ("聯詠", "👑電子權值・IC設計", "TW"), "2379": ("瑞昱", "👑電子權值・網通IC", "TW"),
    "6669": ("緯穎", "👑電子權值・AI伺服器", "TW"), "3231": ("緯創", "👑電子權值・AI伺服器", "TW"),
    "2345": ("智邦", "👑電子權值・高階網通", "TW"), "3017": ("奇鋐", "👑電子權值・AI散熱", "TW"),
    "3533": ("嘉澤", "👑電子權值・高速連接器", "TW"), "3653": ("健策", "👑電子權值・均熱片龍頭", "TW"),
    "2059": ("川湖", "👑電子權值・伺服器導軌", "TW"), "2383": ("台光電", "👑電子權值・高頻CCL", "TW"),
    "2368": ("金像電", "👑電子權值・伺服器PCB", "TW"), "2357": ("華碩", "👑電子權值・AI PC龍頭", "TW"),
    "2301": ("光寶科", "👑電子權值・雲端電源", "TW"), "4938": ("和碩", "👑電子權值・通訊與車用", "TW"),
    "2327": ("國巨", "👑電子權值・被動元件龍頭", "TW"), "3008": ("大立光", "👑電子權值・光學鏡頭龍頭", "TW"),
    "2395": ("研華", "👑電子權值・工業電腦龍頭", "TW"), "5274": ("信驊", "👑電子權值・BMC晶片王", "TWO"),
    "3529": ("力旺", "👑電子權值・矽智財IP", "TWO"), "3661": ("世芯-KY", "👑電子權值・ASIC晶片", "TW"),
    "3443": ("創意", "👑電子權值・ASIC矽智財", "TW"), "2449": ("京元電子", "👑電子權值・AI晶片測試", "TW"),
    "3264": ("欣銓", "👑電子權值・車用與AI測試", "TWO"), "3324": ("雙鴻", "👑電子權值・水冷散熱", "TWO"),
    "6274": ("台燿", "👑電子權值・高階CCL", "TWO"), "8016": ("矽創", "👑電子權值・驅動與感測IC", "TW"),
    "5269": ("祥碩", "👑電子權值・高速傳輸IC", "TW"), "6187": ("萬潤", "👑電子權值・CoWoS設備", "TWO"),
    "3131": ("弘塑", "👑電子權值・CoWoS設備", "TWO"), "3583": ("辛耘", "👑電子權值・先進封裝設備", "TW"),
    "3558": ("神準", "👑電子權值・企業網通", "TWO"), "6285": ("啟碁", "👑電子權值・車用與衛星網通", "TW")
}

FINANCIAL_BLUE_CHIPS = {
    "2881": ("富邦金", "🏦金融權值・獲利王金控", "TW"), "2882": ("國泰金", "🏦金融權值・壽險龍頭金控", "TW"),
    "2891": ("中信金", "🏦金融權值・消金龍頭金控", "TW"), "2886": ("兆豐金", "🏦金融權值・官股匯銀龍頭", "TW"),
    "2884": ("玉山金", "🏦金融權值・高成長民營金控", "TW"), "2885": ("元大金", "🏦金融權值・證券龍頭金控", "TW"),
    "2892": ("第一金", "🏦金融權值・績優官股金控", "TW"), "2880": ("華南金", "🏦金融權值・穩健官股金控", "TW"),
    "5880": ("合庫金", "🏦金融權值・官股房貸龍頭", "TW"), "2890": ("永豐金", "🏦金融權值・高ROE民營金控", "TW"),
    "2883": ("凱基金", "🏦金融權值・壽險與創投金控", "TW"), "2887": ("台新金", "🏦金融權值・併購成長金控", "TW"),
    "2801": ("彰銀", "🏦金融權值・官股核心銀行", "TW"), "2834": ("臺企銀", "🏦金融權值・中小企銀官股", "TW")
}

CORE_WATCHLIST = {**ELECTRONICS_BLUE_CHIPS, **FINANCIAL_BLUE_CHIPS}
CONSTRUCTION_BLACKLIST = {"1808", "2501", "2504", "2505", "2506", "2509", "2511", "2515", "2516", "2520", "2524", "2527", "2528", "2530", "2534", "2535", "2536", "2537", "2538", "2539", "2540", "2542", "2543", "2545", "2546", "2547", "2548", "2597", "5522", "5534", "4113", "5529", "5455", "1439", "9906", "7777", "5523"}

def is_traditional_or_construction(code):
    return (code in CONSTRUCTION_BLACKLIST) or (len(code) == 4 and code[:2] in ["11", "12", "13", "14", "18", "19", "20", "21", "25", "26", "27", "29"])

def compute_kdj(highs, lows, closes, n=9):
    k_val, d_val, j_val, k_prev, d_prev, j_prev = 50.0, 50.0, 50.0, 50.0, 50.0, 50.0
    for i in range(len(closes)):
        k_prev, d_prev, j_prev = k_val, d_val, j_val
        start_idx = max(0, i - n + 1)
        hh, ll = np.max(highs[start_idx:i+1]), np.min(lows[start_idx:i+1])
        rsv = ((closes[i] - ll) / (hh - ll) * 100.0) if (hh != ll) else 50.0
        k_val = (2.0 / 3.0) * k_val + (1.0 / 3.0) * rsv
        d_val = (2.0 / 3.0) * d_val + (1.0 / 3.0) * k_val
        j_val = 3.0 * k_val - 2.0 * d_val
    return round(k_val, 1), round(d_val, 1), round(j_val, 1), round(j_prev, 1), (k_val >= d_val and k_prev <= d_prev + 3.0)

def compute_dmi(highs, lows, closes, n=14):
    if len(closes) < n + 5: return 25.0, 25.0, 20.0, 20.0, False
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
    pdi_prev, mdi_prev = float(plus_di.iloc[-2]) if len(plus_di) > 1 else pdi_now, float(minus_di.iloc[-2]) if len(minus_di) > 1 else mdi_now
    adx_now, adx_prev = round(float(adx.iloc[-1]), 1), round(float(adx.iloc[-2]), 1) if len(adx) > 1 else 20.0
    dmi_bull = (pdi_now > mdi_now) and (adx_now > adx_prev) and ((pdi_prev <= mdi_prev + 4.0) or (pdi_now - mdi_now > pdi_prev - mdi_prev))
    return pdi_now, mdi_now, adx_now, adx_prev, bool(dmi_bull)

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
    try: return yf.download(["^TWII", "TWD=X"], period="2y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=120, show_spinner=False)
def download_fleet_60m():
    try: return yf.download(["^TWII"], period="2mo", interval="60m", progress=False, group_by="ticker", auto_adjust=False)
    except Exception: return pd.DataFrame()

def fetch_and_analyze_fleet(rt_quotes):
    price_dict, _, _, twii_official = fetch_official_market_data()
    data, data_60m = download_fleet_history(), download_fleet_60m()
    now_tw = get_tw_now()
    today_str = now_tw.strftime("%Y-%m-%d")
    is_market_open_or_after = (now_tw.hour * 60 + now_tw.minute) >= 540

    twii_now = twii_official["close"] if twii_official["close"] > 0 else 44257.2
    twii_chg_pct = twii_official["chg_pct"]
    twii_open = twii_high = twii_low = twii_prev_c = twii_now
    ma5, ma10, ma20, ma60, ma120, ma240 = 41981.0, 43266.0, 45144.0, 46300.0, 45387.0, 39022.0
    w_ma10, m_ma5 = 46691.0, 45332.0
    ma5_prev, ma10_prev, ma20_prev, ma60_prev, ma120_prev, ma240_prev = ma5, ma10, ma20, ma60, ma120, ma240
    deduct_5d, deduct_10d, deduct_20d, deduct_60d, deduct_120d = ma5, ma10, ma20, ma60, ma120
    bb_upper, bb_lower = 46724.0, 40500.0
    k9, d9, j9, j9_prev, kd_cross_d = 38.0, 23.6, 81.2, 53.8, True
    pdi_d, mdi_d, adx_d, adx_d_prev, dmi_bull_d = 22.0, 38.0, 34.0, 35.9, False
    date_str, quote_src = today_str, "Yahoo Finance / 證交所"
    est_short_cost, target_cover_1, target_cover_2 = 45474.0, 43655.0, 42063.0
    k60_k, k60_d, k60_j, k60_j_prev, k60_kd_cross = 86.4, 71.5, 116.4, 111.0, True
    k60_pdi, k60_mdi, k60_adx, k60_adx_prev, k60_dmi_bull = 31.0, 19.0, 33.0, 31.0, True
    k60_ma5, k60_ma20, k60_ma60 = 43669.0, 42614.0, 44100.0
    fx_now, fx_prev, fx_chg_ntd, fx_ma5, fx_ma20 = 32.841, 32.847, -0.006, 32.864, 32.583

    try:
        if not data.empty and "^TWII" in data:
            df_twii = data["^TWII"].dropna(subset=["Close"]).copy()
            if len(df_twii) >= 25:
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
                est_short_cost, target_cover_1, target_cover_2 = estimate_foreign_short_cost_and_target(h_twii, l_twii, c_twii, v_twii)
                k9, d9, j9, j9_prev, kd_cross_d = compute_kdj(h_twii[-60:], l_twii[-60:], c_twii[-60:])
                pdi_d, mdi_d, adx_d, adx_d_prev, dmi_bull_d = compute_dmi(h_twii[-90:], l_twii[-90:], c_twii[-90:])
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
                        fx_now, fx_prev = round(float(rt_quotes["USDTWD"]["close"]), 3), round(float(rt_quotes["USDTWD"]["prev_close"]), 3)
                        c_fx[-1] = fx_now
                    else:
                        fx_now, fx_prev = round(float(c_fx[-1]), 3), round(float(c_fx[-2]), 3) if len(c_fx) >= 2 else round(float(c_fx[-1]), 3)
                    fx_chg_ntd, fx_ma5, fx_ma20 = round(fx_now - fx_prev, 3), round(float(np.mean(c_fx[-5:])), 3), round(float(np.mean(c_fx[-20:])), 3)
    except Exception: pass

    try:
        if not data_60m.empty and "^TWII" in data_60m:
            df_60 = data_60m["^TWII"].dropna(subset=["Close"]).copy()
            if len(df_60) >= 30:
                c_60, h_60, l_60 = df_60["Close"].values.astype(float), df_60["High"].values.astype(float), df_60["Low"].values.astype(float)
                if abs(c_60[-1] - twii_now) > 1.0: c_60[-1], h_60[-1], l_60[-1] = twii_now, max(h_60[-1], twii_now), min(l_60[-1], twii_now)
                k60_ma5, k60_ma20 = round(float(np.mean(c_60[-5:])), 1), round(float(np.mean(c_60[-20:])), 1)
                k60_ma60 = round(float(np.mean(c_60[-60:])), 1) if len(c_60) >= 60 else k60_ma20
                k60_k, k60_d, k60_j, k60_j_prev, k60_kd_cross = compute_kdj(h_60[-60:], l_60[-60:], c_60[-60:])
                k60_pdi, k60_mdi, k60_adx, k60_adx_prev, k60_dmi_bull = compute_dmi(h_60[-80:], l_60[-80:], c_60[-80:])
    except Exception: pass

    bias_5, bias_10, bias_20 = round((twii_now - ma5) / max(1.0, ma5) * 100, 2), round((twii_now - ma10) / max(1.0, ma10) * 100, 2), round((twii_now - ma20) / max(1.0, ma20) * 100, 2)
    bias_60, bias_120 = round((twii_now - ma60) / max(1.0, ma60) * 100, 2), round((twii_now - ma120) / max(1.0, ma120) * 100, 2)

    raw_deriv = fetch_auto_derivatives_base(price_dict)
    live_maint = compute_dynamic_pure_stock_maint(twii_now, ma20, ma60)
    fini_oi, fini_chg, sitc_oi, sitc_tx_chg = raw_deriv["fini_tx_oi"], raw_deriv["fini_tx_chg"], raw_deriv["sitc_tx_oi"], raw_deriv["sitc_tx_chg"]
    fini_mtx_chg, retail_mtx_chg = raw_deriv["fini_mtx_chg"], raw_deriv["retail_mtx_chg"]

    deriv_info = {
        "fini_tx_oi": fini_oi, "fini_tx_chg": fini_chg, "sitc_tx_oi": sitc_oi, "sitc_tx_chg": sitc_tx_chg,
        "fini_mtx_chg": fini_mtx_chg, "retail_mtx_chg": retail_mtx_chg,
        "real_fini_oi": fini_oi + sitc_oi, "est_short_cost": est_short_cost, "target_cover_1": target_cover_1, "target_cover_2": target_cover_2,
        "short_profit_pts": round(est_short_cost - twii_now, 0), "short_profit_pct": round((est_short_cost - twii_now) / max(1.0, est_short_cost) * 100.0, 2),
        "pc_ratio": raw_deriv["pc_ratio"], "warrant_hedge_yi": raw_deriv["warrant_hedge_yi"],
        "stock_only_maint_pct": live_maint, "fut_src": f"期交所官方直連 ({raw_deriv['fut_date']} 區分大台/小台)",
        "fx_now": fx_now, "fx_prev": fx_prev, "fx_chg_ntd": fx_chg_ntd, "fx_ma5": fx_ma5, "fx_ma20": fx_ma20
    }
    return {
        "twii_open": twii_open, "twii_prev_c": round(twii_prev_c, 1), "twii_now": twii_now, "twii_chg_pct": twii_chg_pct,
        "twii_high": twii_high, "twii_low": twii_low, "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60, "ma120": ma120, "ma240": ma240,
        "ma5_prev": ma5_prev, "ma10_prev": ma10_prev, "ma20_prev": ma20_prev, "ma60_prev": ma60_prev, "ma120_prev": ma120_prev, "ma240_prev": ma240_prev,
        "deduct_5d": deduct_5d, "deduct_10d": deduct_10d, "deduct_20d": deduct_20d, "deduct_60d": deduct_60d, "deduct_120d": deduct_120d,
        "w_ma10": w_ma10, "m_ma5": m_ma5, "bb_upper": bb_upper, "bb_lower": bb_lower,
        "bias_5": bias_5, "bias_10": bias_10, "bias_20": bias_20, "bias_60": bias_60, "bias_120": bias_120,
        "k9": k9, "d9": d9, "j9": j9, "j9_prev": j9_prev, "kd_cross_d": kd_cross_d,
        "pdi_d": pdi_d, "mdi_d": mdi_d, "adx_d": adx_d, "adx_d_prev": adx_d_prev, "dmi_bull_d": dmi_bull_d,
        "k60_k": k60_k, "k60_d": k60_d, "k60_j": k60_j, "k60_j_prev": k60_j_prev,
        "k60_pdi": k60_pdi, "k60_mdi": k60_mdi, "k60_adx": k60_adx, "k60_adx_prev": k60_adx_prev,
        "k60_ma5": k60_ma5, "k60_ma20": k60_ma20, "k60_ma60": k60_ma60,
        "date_str": date_str, "quote_src": quote_src, "deriv": deriv_info
    }

def scan_all_stocks():
    price_dict, tdcc_dict, rev_dict, _ = fetch_official_market_data()
    cb_set = fetch_active_cb_codes()
    vol_mult, vol_note = get_volume_multiplier()
    now_tw = get_tw_now()
    today_str = now_tw.strftime("%Y-%m-%d")
    is_market_open = (now_tw.weekday() < 5) and ((now_tw.hour * 60 + now_tw.minute) >= 540)

    candidates = dict(CORE_WATCHLIST)

    sorted_short_pool = sorted(
        [(c, info) for c, info in price_dict.items() if c not in candidates and len(c) == 4 and (c not in CONSTRUCTION_BLACKLIST) and ("營造" not in info.get("sector", "")) and info["vol_lots"] >= 1000 and info["chg_pct"] >= 3.0 and info["close"] >= 18.0],
        key=lambda x: x[1]["chg_pct"],
        reverse=True
    )
    for code, info in sorted_short_pool[:60]:
        candidates[code] = (info["name"], info.get("sector") or "⚡短線技術破繭", info["market"])

    sorted_turn_pool = sorted(
        [(c, info) for c, info in price_dict.items() if c not in candidates and len(c) == 4 and (c not in CONSTRUCTION_BLACKLIST) and ("營造" not in info.get("sector", "")) and info["vol_lots"] >= 800 and info["close"] >= 20.0 and 25.0 <= rev_dict.get(c, 0.0) <= 400.0 and tdcc_dict.get(c, 0.0) >= 45.0],
        key=lambda x: rev_dict.get(x[0], 0.0),
        reverse=True
    )
    for code, info in sorted_turn_pool[:25]:
        candidates[code] = (info["name"], info.get("sector") or ("🔄傳產轉機題材" if is_traditional_or_construction(code) else "🔄電子轉機成長"), info["market"])

    mis_targets = [(c, m) for c, (_, _, m) in candidates.items()]
    rt_quotes = fetch_mis_realtime_batch(tuple(mis_targets))
    yf_map = {f"{c}.{'TW' if m == 'TW' else 'TWO'}": c for c, (_, _, m) in candidates.items()}
    raw_df = download_historical_klines(tuple(yf_map.keys()))
    list_tab2_launch, list_tab2_pullback, list_tab2_orbit, list_tab3_core_bluechip, list_tab3_turnaround_or_dip = [], [], [], [], []

    for yf_tkr, code in yf_map.items():
        try:
            if raw_df.empty or yf_tkr not in raw_df: continue
            df_s = raw_df[yf_tkr].dropna(subset=["Close"]).copy()
            if len(df_s) < 65: continue
            c, h, l, v = df_s["Close"].values.astype(float), df_s["High"].values.astype(float), df_s["Low"].values.astype(float), (df_s["Volume"].values.astype(float) / 1000.0)
            last_k_date = df_s.index[-1].strftime("%Y-%m-%d")
            name, sector, _ = candidates[code]

            if code in rt_quotes and rt_quotes[code]["close"] > 0:
                rt = rt_quotes[code]
                if last_k_date == today_str:
                    c[-1], h[-1], l[-1] = rt["close"], max(h[-1], rt["high"]), min(l[-1], rt["low"])
                    if rt["vol_lots"] > 0: v[-1] = float(rt["vol_lots"])
                elif is_market_open and abs(rt["close"] - rt["prev_close"]) > 0.001:
                    c, h, l = np.append(c, rt["close"]), np.append(h, rt["high"]), np.append(l, rt["low"])
                    v = np.append(v, float(rt["vol_lots"]) if rt["vol_lots"] > 0 else v[-1])

            curr_p, prev_p = round(float(c[-1]), 2), float(c[-2])
            chg_1d = round((curr_p - prev_p) / prev_p * 100, 2) if prev_p > 0 else 0.0
            if abs(chg_1d) < 0.01 and code in price_dict and abs(price_dict[code].get("chg_pct", 0.0)) > 0.01 and not is_market_open:
                chg_1d = price_dict[code]["chg_pct"]
            chg_3d = round((curr_p - float(c[-4])) / float(c[-4]) * 100, 2) if len(c) >= 4 else chg_1d
            est_vol = int(round(v[-1] * vol_mult))
            vol_ratio = round(est_vol / max(1.0, float(np.mean(v[-6:-1]))), 2)
            if est_vol < 500 and curr_p < 100.0 and (code not in CORE_WATCHLIST): continue
            liq_tag = " ⚠️量縮控盤" if (est_vol < 500 and curr_p >= 100.0) else ""
            cb_tag = " 🔥[CB可轉債]" if code in cb_set else ""

            ma5, ma10, ma20, ma60 = round(float(np.mean(c[-5:])), 2), round(float(np.mean(c[-10:])), 2), round(float(np.mean(c[-20:])), 2), round(float(np.mean(c[-60:])), 2)
            ma240 = round(float(np.mean(c[-240:])), 2) if len(c) >= 240 else ma60
            std20 = float(np.std(c[-20:]))
            bb_up = round(ma20 + 2.0 * std20, 2)
            bb_bw = round((4.0 * std20 / ma20) * 100, 1) if ma20 > 0 else 99.0
            prev_ma5, prev_ma10, prev_ma20 = float(np.mean(c[-6:-1])), float(np.mean(c[-11:-1])), float(np.mean(c[-21:-1]))
            ma_tangle_pct = round((max(prev_ma5, prev_ma10, prev_ma20) - min(prev_ma5, prev_ma10, prev_ma20)) / max(0.1, prev_ma20) * 100, 2)

            stk_k9, stk_d9, stk_j9, stk_j_prev, kd_cross_recent = compute_kdj(h[-60:], l[-60:], c[-60:])
            stk_j_up = stk_j9 > stk_j_prev
            pdi, mdi, adx, adx_prev, dmi_bull_active = compute_dmi(h[-90:], l[-90:], c[-90:])
            adx_delta, adx_up = round(adx - adx_prev, 1), (adx > adx_prev)
            dmi_str = f"+DI:{pdi} > -DI:{mdi}" if pdi > mdi else f"+DI:{pdi} < -DI:{mdi}"
            adx_str = f"{adx} ({'↑轉強' if adx_up else '↓整理'}{adx_delta:+.1f})"
            kd_dmi_resonance = (stk_k9 >= stk_d9) and (pdi > mdi) and adx_up and (adx >= 18.0) and (kd_cross_recent or dmi_bull_active)
            _, _, osc = compute_macd(c)

            raw_ceiling = max(bb_up, round(float(np.max(h[-21:-1])), 2))
            castle_ceiling = round(curr_p * 1.07, 2) if raw_ceiling <= curr_p else round(raw_ceiling, 2)
            castle_iron = round(min(curr_p, max(ma10, float(l[-1])) if curr_p >= ma10 else min(ma20, float(l[-1]))), 2)
            castle_stop = round(min(ma60, float(np.min(l[-20:]))), 2)

            p_meta = price_dict.get(code, {})
            tdcc_val = tdcc_dict.get(code, None)
            yoy_val = rev_dict.get(code, None)
            cum_yoy_val = p_meta.get("cum_yoy", None)
            pe_official = p_meta.get("pe", np.nan)
            f_lots, s_lots = p_meta.get("fini_lots", 0), p_meta.get("sitc_lots", 0)
            inst_tag = format_inst_chip_tag(f_lots, s_lots)

            yoy_str = f"{yoy_val:+.1f}%" if yoy_val is not None else "待公告"
            cum_yoy_str = f"{cum_yoy_val:+.1f}%" if cum_yoy_val is not None else "待公告"

            is_blue_chip, is_fin = (code in ELECTRONICS_BLUE_CHIPS) or (code in FINANCIAL_BLUE_CHIPS), (code in FINANCIAL_BLUE_CHIPS)
            has_solid_fund = (yoy_val is not None and yoy_val > 0.0) and (cum_yoy_val is None or cum_yoy_val > 0.0)
            if has_solid_fund:
                short_track_tag = "🔥有基之彈(技+基雙渦輪)"
            elif is_blue_chip and (cum_yoy_val is not None and cum_yoy_val > 0.0):
                short_track_tag = "🏦有基之彈(累計年增穩健)"
            else:
                short_track_tag = "🎭純技術炒作(不看基本面)"

            est_eps, pe_disp, eps_entry_p, eps_target_p, val_diag = compute_eps_valuation(code, curr_p, sector, yoy_val, cum_yoy_val, pe_official, support_price=castle_iron)

            row_base = {
                "代碼": code, "股名": f"{name}{cb_tag}{liq_tag}", "族群": sector, "現價": curr_p, "今日%": chg_1d, "3日%": chg_3d,
                "短線屬性": short_track_tag,
                "法人動向": inst_tag, "外資買賣(張)": f_lots, "投信買賣(張)": s_lots,
                "預估EPS(元)": est_eps, "本益比(PE)": pe_disp, "💎EPS合理切入價": eps_entry_p, "🎯EPS目標價": eps_target_p, "估值診斷": val_diag,
                "日J值": stk_j9, "DMI多空": dmi_str, "ADX強度": adx_str, "5MA量比": vol_ratio, "預估量(張)": est_vol,
                "天花板(反壓)": castle_ceiling, "月線承接": ma20, "鋼鐵防線": castle_iron, "極限停損": castle_stop,
                "400張大戶%": f"{tdcc_val:.1f}%" if tdcc_val is not None else "未揭露",
                "單月營收YoY": yoy_str, "累計年營收YoY": cum_yoy_str
            }
            is_heavy_dump = (chg_1d <= -2.0 and vol_ratio >= 1.25) or (chg_1d <= -4.2)

            is_above_ma20_trend = (curr_p >= ma20) and (curr_p >= ma60 or ma20 >= ma60 * 0.94)
            is_bottom_sprout = (curr_p >= ma5 and curr_p >= ma10) and (prev_p < ma10 or prev_p < ma20) and (chg_1d >= 3.5) and (vol_ratio >= 1.15 or chg_1d >= 8.5) and (stk_k9 >= stk_d9 or osc[-1] > osc[-2] or stk_j_up)
            is_limit_up_breakout = (chg_1d >= 8.5) and (curr_p >= ma5) and (curr_p >= ma10 * 0.98)

            if (is_above_ma20_trend or is_bottom_sprout or is_limit_up_breakout) and (not is_heavy_dump):
                is_overheated = (chg_3d >= 14.0) or (stk_j9 >= 93.0 and not kd_dmi_resonance and not is_limit_up_breakout)
                if (is_bottom_sprout and not is_overheated) or (is_limit_up_breakout and not is_overheated) or ((chg_1d >= 1.8 and (vol_ratio >= 1.15 or chg_1d >= 7.0) and kd_dmi_resonance and not is_overheated) or (chg_1d >= 2.5 and (vol_ratio >= 1.20 or chg_1d >= 7.0) and not is_overheated and pdi > mdi and adx >= 15.0)):
                    if kd_dmi_resonance:
                        score, pattern = 100, f"👑 KD×DMI雙金叉共振旱地拔蔥 [{short_track_tag}]"
                    elif bb_bw <= 16.0 and curr_p >= ma20:
                        score, pattern = 98, f"🔥 雙鴻/奇鋐型：布林壓縮旱地拔蔥 [{short_track_tag}]"
                    elif ma_tangle_pct <= 4.0 and curr_p >= ma20:
                        score, pattern = 97, f"🌸 瑞昱/矽創型：均線糾結旱地拔蔥 [{short_track_tag}]"
                    elif is_bottom_sprout:
                        score, pattern = 96, f"🌱 階段3(3)底部旱地拔蔥：首根長紅帶量穿雙線 [{short_track_tag}]"
                    else:
                        score, pattern = min(95, 86 + int(min(9, vol_ratio * 2.0 + chg_1d))), f"🚀 帶量突破頸線旱地拔蔥 [{short_track_tag}]"
                    list_tab2_launch.append({"實戰優先分": score, "名將戰法診斷": pattern, **row_base})
                elif is_above_ma20_trend and (-3.5 <= chg_1d <= 2.2) and (chg_3d >= -6.5) and (vol_ratio <= 0.95) and (stk_j9 <= 68.0) and (pdi >= mdi * 0.9):
                    list_tab2_pullback.append({"蓄勢評分": min(99, 84 + int((1.0 - vol_ratio) * 15) + (3 if pdi > mdi else 0)), "名將戰法診斷": f"⚡ N字量縮守月線 [{short_track_tag}]", **row_base})
                elif is_above_ma20_trend and curr_p >= ma5 >= ma10 >= ma20 and chg_1d >= -2.0:
                    pattern = f"🔥 3日急拉過熱 [{short_track_tag}]" if chg_3d >= 10.5 else (f"🚀 KD鈍化惟DMI續強 [{short_track_tag}]" if (stk_j9 >= 90.0 and pdi > mdi and adx_up) else f"🛡️ 沿5/10MA穩步推升 [{short_track_tag}]")
                    list_tab2_orbit.append({"趨勢狀態": pattern, **row_base})

            if is_blue_chip and (not is_heavy_dump):
                bc_rev_ok = has_solid_fund or (yoy_val is not None and yoy_val >= 0.0) or (cum_yoy_val is not None and cum_yoy_val >= 0.0) or (is_fin and (yoy_val is None or yoy_val >= -5.0))
                bc_tdcc_ok = (tdcc_val is None) or (tdcc_val >= 42.0)
                fund_qual = "🔥單月+累計雙成長" if has_solid_fund else "🏦累計年增穩健"
                if bc_rev_ok and bc_tdcc_ok and (curr_p >= ma20) and (pdi > mdi) and (chg_3d < 9.5) and (stk_j9 <= 88.0):
                    l_score = min(100, 88 + (3 if is_fin else 0) + (4 if has_solid_fund else 0) + (4 if (tdcc_val and tdcc_val >= 65.0) else 0) + (3 if "低估鑽石" in val_diag else 0) + (2 if (f_lots > 0 and s_lots > 0) else 0))
                    list_tab3_core_bluechip.append({"波段總分": l_score, "長線格局定性": f"👑 權值龍頭＋{fund_qual}" if kd_dmi_resonance else (f"🏦 績優金控＋{fund_qual}" if is_fin else f"✅ 長線多頭＋{fund_qual}"), **row_base})
                elif bc_rev_ok and (curr_p < ma20) and (curr_p >= ma240 * 0.95) and (stk_j9 <= 48.0):
                    dip_score = min(98, 86 + (4 if chg_1d >= 0.0 else 0) + (3 if stk_j_up else 0) + (3 if has_solid_fund else 0) + (3 if "低估鑽石" in val_diag else 0) + (2 if (f_lots > 0 or s_lots > 0) else 0))
                    dip_tag = f"🟢【權值右側止穩・{fund_qual}】" if (chg_1d >= 0.0 or stk_j_up) else f"⏳【權值左側超跌・{fund_qual}】"
                    list_tab3_turnaround_or_dip.append({"波段總分": dip_score, "類型與轉機標註": dip_tag, **row_base})
            elif (not is_blue_chip) and (code not in CONSTRUCTION_BLACKLIST) and (not is_heavy_dump):
                if (yoy_val is not None and 25.0 <= yoy_val <= 400.0) and (tdcc_val is not None and tdcc_val >= 48.0) and (curr_p >= ma20 >= ma60) and (pdi > mdi) and (stk_j9 <= 85.0):
                    t_row = dict(row_base)
                    t_row["股名"] = f"🔄【轉機股】{name}{cb_tag}{liq_tag}"
                    rev_type = "雙成長爆發" if (cum_yoy_val and cum_yoy_val > 0) else "久旱破繭轉機"
                    list_tab3_turnaround_or_dip.append({"波段總分": 92 + (4 if kd_dmi_resonance else 0) + (2 if (f_lots > 0 or s_lots > 0) else 0), "類型與轉機標註": f"🔄【{rev_type}】單月YoY{yoy_str}/累計{cum_yoy_str}＋DMI翻揚", **t_row})
        except Exception: continue

    df_t1 = pd.DataFrame(list_tab2_launch).sort_values(by="實戰優先分", ascending=False) if list_tab2_launch else pd.DataFrame()
    df_t2 = pd.DataFrame(list_tab2_pullback).sort_values(by="蓄勢評分", ascending=False) if list_tab2_pullback else pd.DataFrame()
    df_t3 = pd.DataFrame(list_tab2_orbit).sort_values(by="3日%", ascending=False) if list_tab2_orbit else pd.DataFrame()
    df_long_bc = pd.DataFrame(list_tab3_core_bluechip).sort_values(by="波段總分", ascending=False) if list_tab3_core_bluechip else pd.DataFrame()
    df_long_turn = pd.DataFrame(list_tab3_turnaround_or_dip).sort_values(by=["波段總分", "今日%"], ascending=[False, False]) if list_tab3_turnaround_or_dip else pd.DataFrame()
    return df_t1, df_t2, df_t3, df_long_bc, df_long_turn, vol_note, rt_quotes
    # ==========================================
# 【v54.0 下半部程式碼：純加權指數方向儀表板 ＋ 股市水庫水位建議 ＋ 淘金客 EPS SOP v2.0】
# ==========================================
def apply_macro_circuit_breaker(
    raw_score: int,
    taiex_price: float,
    ma60: float,
    ma120: float,
    ma240: float,
    ma120_rising: bool,
    pcr_ratio: float,
    margin_ratio: float,
    fx_now: float,
    fx_ma5: float,
    fx_chg_ntd: float,
    above_ma5: bool = False
) -> tuple[int, str, str, bool]:
    score, penalties, is_capped = raw_score, [], False
    status_tag = "🚀 多頭趨勢推進（順勢偏多操作）"

    if taiex_price < ma240:
        score -= 18
        penalties.append("🚨跌破240MA年線長線支撐 (-18分)")
    elif taiex_price < ma120:
        if ma120_rising:
            score -= 8
            penalties.append("跌破60MA季線(-5分)＋刺穿上揚120MA半年線輕度警戒(-3分)")
        else:
            score -= 10
            penalties.append("跌破60MA季線(-5分)＋跌破下彎120MA半年線警戒(-5分)")
    elif taiex_price < ma60:
        score -= 5
        penalties.append("跌破60MA季線 (-5分)")

    if pcr_ratio < 70.0:
        score -= 10
        penalties.append(f"選擇權PCR {pcr_ratio:.1f}%<70% (-10分)")
    elif pcr_ratio < 80.0:
        score -= 5
        penalties.append(f"選擇權PCR {pcr_ratio:.1f}%<80% (-5分)")

    if fx_now <= fx_ma5 and fx_chg_ntd < 0:
        score += 5
        penalties.append("台幣止貶轉升破5MA (+5分)")
    elif fx_now > fx_ma5 and fx_chg_ntd >= 0.04:
        score -= 5
        penalties.append("台幣續貶破5MA熱錢匯出 (-5分)")

    if taiex_price < ma240:
        if score > 60: is_capped = True
        score = min(score, 60)
        penalties.append("觸發破年線硬上限熔斷 (鎖定60分)")
        status_tag = "🚨 長線破年線空方警戒（嚴控資金水位、反彈調節防守）"
    elif taiex_price < ma120:
        if above_ma5:
            score = min(score, 82)
            status_tag = "🟢 短線站上5MA/10MA確認止跌・B波反彈推進中（挑戰上檔月線與半年線反壓）"
        else:
            if score > 65: is_capped = True
            score = min(score, 65)
            status_tag = "⚠️ 半年線下築底測試格局（半年線僅作警戒，靜待站穩日5MA轉攻）"
    elif taiex_price < ma60:
        score = min(score, 85)
        status_tag = "🛡️ 站回半年線、季線下震盪收復格局（選股不選市、鎖定有基之彈）"
    else:
        status_tag = "🚀 站穩季線多頭推進格局（順勢偏多操作）"

    score = max(0, score)
    trace_str = f"⚡ 評分演算軌跡：原始動能 {raw_score} 分 ➔ {' ＋ '.join(penalties)} ➔ 最終裁決 {score} / 100 分" if penalties else f"✅ 原始動能 {raw_score} 分 ➔ 最終裁決 {score} / 100 分"
    return score, status_tag, trace_str, is_capped


@st.cache_data(ttl=120, show_spinner=False)
def fetch_vix_and_panic_forecast(twii_now, ma5, ma10, ma20, ma60, ma120, ma240, m_ma5, bb_upper, twii_low, ma120_rising, ma240_rising, j_now, j_delta, k60_ma5, in_maint_pct, in_pcr, retail_mtx_chg, in_warrant_yi, est_short_cost, target_cover_1, target_cover_2):
    vix_now, vix_chg_pct, vix_ma5, vix_ma20 = 18.81, -3.34, 22.40, 19.37
    sox_chg, tsm_chg, nvda_chg = -0.35, +2.02, -5.47
    try:
        v8_vix = fetch_yahoo_v8_realtime("%5EVIX", decimals=2)
        if v8_vix and v8_vix["close"] > 5.0:
            vix_now, vix_chg_pct = v8_vix["close"], v8_vix["chg_pct"]
        df_vix = yf.download("^VIX", period="2mo", interval="1d", progress=False, auto_adjust=False)
        if not df_vix.empty and "Close" in df_vix:
            c_vix = df_vix["Close"].dropna().values.astype(float).flatten()
            if len(c_vix) >= 5:
                if not (v8_vix and v8_vix["close"] > 5.0):
                    vix_now = round(float(c_vix[-1]), 2)
                    vix_prev = float(c_vix[-2]) if len(c_vix) >= 2 else vix_now
                    vix_chg_pct = round(((vix_now - vix_prev) / max(1.0, vix_prev)) * 100.0, 2)
                else:
                    c_vix[-1] = vix_now
                vix_ma5 = round(float(np.mean(c_vix[-5:])), 2)
                vix_ma20 = round(float(np.mean(c_vix[-20:])), 2) if len(c_vix) >= 20 else vix_ma5
    except Exception:
        pass

    for sym_k, dec_v in [("%5ESOX", "sox"), ("TSM", "tsm"), ("NVDA", "nvda")]:
        try:
            q_us = fetch_yahoo_v8_realtime(sym_k, decimals=2)
            if q_us and q_us["close"] > 0:
                if dec_v == "sox": sox_chg = q_us["chg_pct"]
                elif dec_v == "tsm": tsm_chg = q_us["chg_pct"]
                elif dec_v == "nvda": nvda_chg = q_us["chg_pct"]
        except Exception:
            pass

    if vix_now >= 28.0:
        vix_status, vix_color = f"🚨 極度恐慌警戒 ({vix_now:.2f} >= 28)", "#ff4d4f"
    elif vix_now >= 20.0:
        if vix_now < vix_ma5 or vix_chg_pct < 0:
            vix_status, vix_color = f"🟢 恐慌高檔回落 ({vix_now:.2f} < 5MA {vix_ma5:.2f}，美股止穩訊號！)", "#00e676"
        else:
            vix_status, vix_color = f"🟡 避險情緒偏高 ({vix_now:.2f} > 20，留意美股波動)", "#fbbf24"
    else:
        vix_status, vix_color = f"☀️ 全球風險偏好穩定 ({vix_now:.2f} < 20 常態安全區)", "#00e676"

    p_maint = np.clip((170.0 - in_maint_pct) / 40.0 * 100.0, 0.0, 100.0)
    p_pcr = np.clip((120.0 - in_pcr) / 60.0 * 100.0, 0.0, 100.0)
    p_retail = np.clip(50.0 + (-retail_mtx_chg / 100.0) + (-in_warrant_yi / 8.0), 0.0, 100.0)
    p_vix = np.clip((vix_now - 12.0) / 20.0 * 100.0, 0.0, 100.0)

    tw_panic_idx = int(round(0.35 * p_maint + 0.25 * p_pcr + 0.20 * p_retail + 0.20 * p_vix))
    if tw_panic_idx >= 70:
        panic_label, panic_color = f"😨 極度恐慌清洗區 ({tw_panic_idx}/100) ➔ 散戶斷頭認輸，強烈反彈訊號！", "#00e676"
    elif tw_panic_idx >= 55:
        panic_label, panic_color = f"🟡 恐慌釋放轉折區 ({tw_panic_idx}/100) ➔ 浮額已大幅降溫，有利多方反攻", "#ffd54f"
    elif tw_panic_idx >= 35:
        panic_label, panic_color = f"☀️ 情緒中性常態區 ({tw_panic_idx}/100) ➔ 籌碼多空平衡，選股操作", "#e5e7eb"
    else:
        panic_label, panic_color = f"🔥 散戶極度貪婪過熱 ({tw_panic_idx}/100) ➔ 居高思危、留意追高風險", "#ff4d4f"

    long_trend = "📈 【長線多頭保護傘未破】(240MA年線與120MA半年線雙雙上揚助漲)" if (twii_now >= ma240 and ma240_rising) else "🚨 【長線跌破年線轉空】"
    mid_trend = "📈 【中線多頭排列】" if twii_now >= ma60 else ("🧭 【中線收復半年線・挑戰季線】" if twii_now >= ma120 else "📉 【中線處於月/季線下修正波】(半年線僅為洗盤警戒，非真長空)")
    if twii_now >= ma10 and twii_now >= ma5:
        short_trend = "🔥 【短線連破5MA與10MA強彈確立】(十日線已轉為下檔支撐，正挑戰月線與半年線！)"
    elif twii_now >= ma5 and twii_now >= k60_ma5:
        short_trend = "🔥 【短線右側止跌確立・展開日線級B波強彈】(已突破日5MA與60分5MA，日KDJ金叉向上！)"
    elif twii_now >= ma5 or twii_now >= k60_ma5 or j_delta > 0:
        short_trend = "🌱 【短線初步止穩勾頭・站上短期均線轉強】"
    else:
        short_trend = "⚠️ 【短線仍在5MA下尋底測試】"

    if twii_now >= ma10:
        up_t1_low, up_t1_high = round(min(ma20, m_ma5), 0), round(max(ma20, m_ma5), 0)
        up_t2_low, up_t2_high = round(min(m_ma5, ma120), 0), round(max(m_ma5, ma120), 0)
        up_t3_low, up_t3_high = round(min(ma60, bb_upper), 0), round(max(ma60, bb_upper), 0)
        dn_s1_low, dn_s1_high = round(min(ma10, k60_ma5), 0), round(max(ma10, k60_ma5), 0)
        dn_s2_low, dn_s2_high = round(min(ma5, target_cover_2), 0), round(max(ma5, target_cover_2), 0)
    else:
        up_t1_low, up_t1_high = round(min(ma10, target_cover_1), 0), round(max(ma10, target_cover_1), 0)
        mid_resist = sorted([ma20, m_ma5, ma120])
        up_t2_low, up_t2_high = round(mid_resist[0], 0), round(mid_resist[-1], 0)
        up_t3_low, up_t3_high = round(min(ma60, bb_upper), 0), round(max(ma60, bb_upper), 0)
        dn_s1_low, dn_s1_high = round(min(ma5, k60_ma5), 0), round(max(ma5, k60_ma5), 0)
        dn_s2_low, dn_s2_high = round(min(40350.0, twii_low * 0.965), 0), round(min(41200.0, dn_s1_low - 450.0), 0)

    dn_s3_val = round(ma240, 0)

    return {
        "vix_now": vix_now, "vix_chg_pct": vix_chg_pct, "vix_ma5": vix_ma5, "vix_ma20": vix_ma20,
        "vix_status": vix_status, "vix_color": vix_color,
        "sox_chg": sox_chg, "tsm_chg": tsm_chg, "nvda_chg": nvda_chg,
        "tw_panic_idx": tw_panic_idx, "panic_label": panic_label, "panic_color": panic_color,
        "long_trend": long_trend, "mid_trend": mid_trend, "short_trend": short_trend,
        "up_t1": (up_t1_low, up_t1_high), "up_t2": (up_t2_low, up_t2_high), "up_t3": (up_t3_low, up_t3_high),
        "dn_s1": (dn_s1_low, dn_s1_high), "dn_s2": (dn_s2_low, dn_s2_high), "dn_s3": dn_s3_val
    }


def pin_code_name_index(df):
    if df.empty:
        return df
    name_col = "股名" if "股名" in df.columns else ("公司名稱" if "公司名稱" in df.columns else None)
    if "代碼" in df.columns and name_col:
        return df.set_index(["代碼", name_col])
    return df


# ==========================================
# Streamlit 戰情室主介面 (v54.0 純加權指數方向儀表板 ＋ 淘金客 EPS SOP v2.0)
# ==========================================
now_str = get_tw_now().strftime("%Y-%m-%d %H:%M:%S")
st.markdown("## 🧭 台股每日戰情室 v54.0 (加權指數多空方向儀表板 ＋ 淘金客 EPS 預估 SOP v2.0 ＋ 三大法人T86籌碼版)")

btn_col1, btn_col2, btn_col3 = st.columns([2, 2, 3])
if btn_col1.button("⚡ 秒速刷新盤中即時報價、VIX恐慌指數與維持率", use_container_width=True):
    fetch_mis_realtime_batch.clear()
    fetch_vix_and_panic_forecast.clear()
if btn_col2.button("🔄 立即重抓 14:00 後期交所、三大法人T86與最新月營收", use_container_width=True):
    st.cache_data.clear()
auto_refresh = btn_col3.checkbox("⏱️ 開啟盤中每 20 秒自動刷新即時報價", value=False)

with st.spinner("📡 正在同步加權指數多空方向儀表板、淘金客 EPS SOP v2.0 (時序收斂+三率乘數T) 與三大法人買賣超..."):
    df_launch, df_pullback, df_orbit, df_long_bc, df_long_turn, vol_note, rt_quotes = scan_all_stocks()
    fleet = fetch_and_analyze_fleet(rt_quotes)
    price_dict_all, tdcc_dict_all, _, _ = fetch_official_market_data()
    df_all_rev = fetch_all_monthly_revenue_df(price_dict_all)
    if not df_all_rev.empty:
        df_all_rev["400張大戶%"] = df_all_rev["代碼"].map(lambda c: f"{tdcc_dict_all[c]:.1f}%" if c in tdcc_dict_all else "未揭露")
        df_all_rev["_tdcc_num"] = df_all_rev["代碼"].map(lambda c: float(tdcc_dict_all.get(c, 0.0)))

deriv = fleet["deriv"]
in_fini_oi, in_fini_chg, in_sitc_oi = deriv["fini_tx_oi"], deriv["fini_tx_chg"], deriv["sitc_tx_oi"]
sitc_tx_chg_v, fini_mtx_chg_v, retail_mtx_chg_v = deriv.get("sitc_tx_chg", 3351), deriv.get("fini_mtx_chg", -1679), deriv.get("retail_mtx_chg", -871)
real_directional_fini_oi, est_short_cost, target_cover_1, target_cover_2 = deriv["real_fini_oi"], deriv["est_short_cost"], deriv["target_cover_1"], deriv["target_cover_2"]
short_profit_pts, short_profit_pct, in_pcr, in_warrant_yi, in_maint_pct = deriv["short_profit_pts"], deriv["short_profit_pct"], deriv["pc_ratio"], deriv["warrant_hedge_yi"], deriv["stock_only_maint_pct"]
fx_now, fx_prev, fx_chg_ntd, fx_ma5, fx_ma20 = deriv["fx_now"], deriv["fx_prev"], deriv["fx_chg_ntd"], deriv["fx_ma5"], deriv["fx_ma20"]
fx_cents_chg = round(fx_chg_ntd * 100.0, 1)

twii_open, twii_prev_c, twii_now = fleet["twii_open"], fleet["twii_prev_c"], fleet["twii_now"]
twii_ma5, twii_ma10, twii_ma20, twii_ma60, twii_ma120, twii_ma240 = fleet["ma5"], fleet["ma10"], fleet["ma20"], fleet["ma60"], fleet["ma120"], fleet["ma240"]
twii_m_ma5, twii_w_ma10 = fleet["m_ma5"], fleet["w_ma10"]
ma120_is_rising = bool(twii_ma120 >= fleet["ma120_prev"])
ma240_is_rising = bool(twii_ma240 >= fleet["ma240_prev"])
is_above_ma5 = bool(twii_now >= twii_ma5)
is_above_ma10 = bool(twii_now >= twii_ma10)

j_now, j_delta = fleet["j9"], round(fleet["j9"] - fleet["j9_prev"], 1)
j_trend_str = f"↑勾頭 ({j_delta:+.1f})" if j_delta > 0 else f"↓下彎 ({j_delta:+.1f})"
k60_k, k60_d, k60_j, k60_j_delta = fleet["k60_k"], fleet["k60_d"], fleet["k60_j"], round(fleet["k60_j"] - fleet["k60_j_prev"], 1)
k60_j_trend = f"↑勾頭 ({k60_j_delta:+.1f})" if k60_j_delta > 0 else f"↓下彎 ({k60_j_delta:+.1f})"

tf = fetch_vix_and_panic_forecast(
    twii_now, twii_ma5, twii_ma10, twii_ma20, twii_ma60, twii_ma120, twii_ma240, twii_m_ma5, fleet["bb_upper"],
    fleet["twii_low"], ma120_is_rising, ma240_is_rising, j_now, j_delta, fleet["k60_ma5"],
    in_maint_pct, in_pcr, retail_mtx_chg_v, in_warrant_yi, est_short_cost, target_cover_1, target_cover_2
)

st.caption(f"🕒 台灣時間：{now_str} ｜ 報價：{fleet['quote_src']} ｜ 美股VIX：{tf['vix_now']:.2f} ({tf['vix_chg_pct']:+.2f}%) ｜ 費半：{tf['sox_chg']:+.2f}% ｜ 台積ADR：{tf['tsm_chg']:+.2f}% ｜ 台股恐慌：{tf['tw_panic_idx']}/100")

s1_maint = 25 if in_maint_pct <= 135.0 else (22 if in_maint_pct <= 145.0 else (16 if in_maint_pct <= 152.0 else (12 if in_maint_pct <= 165.0 else 5)))
s1_desc = f"🚨 已破135%追繳線 ({in_maint_pct:.1f}%)・T+2斷頭底！" if in_maint_pct <= 135.0 else (f"🟢 底部反彈回升中 ({in_maint_pct:.1f}%)" if in_maint_pct <= 160.0 else f"🟡 維持率 {in_maint_pct:.1f}%")

if in_fini_chg >= 4500 or fini_mtx_chg_v >= 4500:
    s2_fut = 22 if in_fini_chg < 0 else 25
    s2_desc = f"🟢 小台外資大回補 {fini_mtx_chg_v:+,}口(散戶洗出 {retail_mtx_chg_v:+,}口)｜大台外資 {in_fini_chg:+,}口(投信 {sitc_tx_chg_v:+,}口)"
elif sitc_tx_chg_v >= 2500 and retail_mtx_chg_v < 0:
    s2_fut = 18
    s2_desc = f"🟢 投信大台大買 {sitc_tx_chg_v:+,}口撐盤＋散戶小台續砍 {retail_mtx_chg_v:+,}口｜大台外資 {in_fini_chg:+,}口"
elif twii_now <= target_cover_2 * 1.005:
    s2_fut = 15 if in_fini_chg <= -1500 else 20
    s2_desc = f"🎯 已達外資空單滿足區 ({target_cover_2:,.0f}點)｜大台 {in_fini_chg:+,}口 / 小台 {fini_mtx_chg_v:+,}口"
else:
    s2_fut = 14
    s2_desc = f"🟡 大台外資 {in_fini_chg:+,}口(投信 {sitc_tx_chg_v:+,}口)｜小台外資 {fini_mtx_chg_v:+,}口(散戶 {retail_mtx_chg_v:+,}口)"

s3_pcr = 12 if (in_pcr <= 75.0 or in_pcr >= 100.0) else (10 if in_pcr >= 82.0 else 8)
s3_war = 8 if in_warrant_yi >= 3.0 else (7 if in_warrant_yi <= -35.0 else 4)
s3_opt_war = s3_pcr + s3_war
s3_desc = f"PCR {in_pcr:.1f}% ＋ 權證避險 {in_warrant_yi:+.2f}億"

if (k60_k >= k60_d) and (twii_now >= fleet["k60_ma5"]):
    s4_k60 = 15 if is_above_ma5 else 13
    s4_desc = f"🟢 已突破60分5MA({fleet['k60_ma5']:,.0f})與日5MA({twii_ma5:,.0f})！右側轉攻確立"
elif twii_now >= fleet["k60_ma5"] and is_above_ma5:
    s4_k60 = 13
    s4_desc = f"🟢 已站穩60分5MA({fleet['k60_ma5']:,.0f})與日5MA({twii_ma5:,.0f})之上 (60分J={k60_j}高檔良性換手)"
elif is_above_ma5 or k60_j_delta > 0 or k60_k >= k60_d:
    s4_k60 = 10
    s4_desc = f"🟢 已站上日5MA({twii_ma5:,.0f})，60分K挑戰 60分5MA({fleet['k60_ma5']:,.0f})"
else:
    s4_k60 = 0
    s4_desc = f"⚠️ 60分J={k60_j} ({k60_j_trend}) 尚未站上 60分5MA({fleet['k60_ma5']:,.0f})"

s5_space = 15 if (twii_now >= twii_ma10 and is_above_ma5) else (12 if (is_above_ma5 and j_delta > 0) else (10 if (fleet["bias_20"] <= -6.0 and j_delta > 0) else 5))
s5_desc = f"🟢 已連破5MA與10MA({twii_ma10:,.0f})｜日J={j_now}({j_trend_str})" if is_above_ma10 else (f"🟢 已站上日5MA({twii_ma5:,.0f})｜日J={j_now}({j_trend_str})" if is_above_ma5 else f"🎯 月線負乖離 {fleet['bias_20']:+.2f}%｜日J={j_now}({j_trend_str})")

raw_score_5d = s1_maint + s2_fut + s3_opt_war + s4_k60 + s5_space
score_5d, grade_str, circuit_trace_str, is_circuit_capped = apply_macro_circuit_breaker(
    raw_score_5d, twii_now, twii_ma60, twii_ma120, twii_ma240, ma120_is_rising,
    in_pcr, in_maint_pct, fx_now, fx_ma5, fx_chg_ntd, above_ma5=is_above_ma5
)

fx_cmp_sym = "<=" if fx_now <= fx_ma5 else ">"
if fx_now <= fx_ma5 and fx_chg_ntd < 0:
    fx_status_str = f"🟢 台幣轉升破5MA ({fx_now:.3f} <= 5MA {fx_ma5:.3f})"
elif fx_chg_ntd <= 0:
    fx_status_str = f"🟡 台幣止貶回穩 ({fx_now:.3f} {fx_cmp_sym} 5MA {fx_ma5:.3f})"
elif fx_now <= fx_ma5:
    fx_status_str = f"🟡 台幣微貶 {abs(fx_cents_chg):.1f}分 惟仍守5MA內 ({fx_now:.3f} <= 5MA {fx_ma5:.3f})"
else:
    fx_status_str = f"🚨 台幣續貶破5MA ({fx_now:.3f} > 5MA {fx_ma5:.3f})"

# 根據大盤位階與五維度評分，自動計算「股市水庫理論」建議資金水位
if twii_now >= twii_ma20 and score_5d >= 70:
    water_level_str = "💧 70% ～ 80% (月線上多頭推進・積極順勢操作)"
elif is_above_ma10 and score_5d >= 60:
    water_level_str = "💧 50% ～ 60% (B波強彈挑戰月線/半年線反壓・選股操作、遇壓不追高)"
elif is_above_ma5:
    water_level_str = "💧 40% ～ 50% (短線止跌反彈・鎖定有基之彈與低估鑽石股)"
else:
    water_level_str = "💧 20% ～ 30% (5MA下尋底防禦・保留高現金水庫等右側訊號)"

if not df_launch.empty and "短線屬性" in df_launch.columns:
    df_launch_fund = df_launch[df_launch["短線屬性"].str.contains("有基之彈", na=False)].copy()
    df_launch_pure = df_launch[df_launch["短線屬性"].str.contains("純技術炒作", na=False)].copy()
else:
    df_launch_fund, df_launch_pure = pd.DataFrame(), pd.DataFrame()

def fmt_stock_full_detail(r):
    pe_s = f"{r['本益比(PE)']:.1f}x" if pd.notna(r.get("本益比(PE)")) else "無PE"
    eps_s = f"{r['預估EPS(元)']:.2f}元" if pd.notna(r.get("預估EPS(元)")) else "待轉盈"
    ent_s = f"{r['💎EPS合理切入價']:.2f}" if pd.notna(r.get("💎EPS合理切入價")) else "-"
    tgt_s = f"{r['🎯EPS目標價']:.2f}" if pd.notna(r.get("🎯EPS目標價")) else "-"
    return (
        f"【{r['代碼']} {r.get('股名', r.get('公司名稱', ''))}】"
        f"現價={r['現價']}({r['今日%']:+.2f}%/3日{r.get('3日%', 0):+.2f}%) | "
        f"法人={r.get('法人動向', '-')}[外資{r.get('外資買賣(張)', 0):+,}張/投信{r.get('投信買賣(張)', 0):+,}張] | "
        f"量比={r.get('5MA量比', '-')}(預估{r.get('預估量(張)', '-')}張) | "
        f"技術:J={r.get('日J值', '-')}/{r.get('DMI多空', '-')}/ADX={r.get('ADX強度', '-')} | "
        f"城堡點位:[天花板{r.get('天花板(反壓)', '-')}/月線{r.get('月線承接', '-')}/鋼鐵防線{r.get('鋼鐵防線', '-')}/停損{r.get('極限停損', '-')}] | "
        f"400張大戶={r.get('400張大戶%', '-')} | "
        f"營收:[單月YoY={r.get('單月營收YoY', '-')}/累計年YoY={r.get('累計年營收YoY', '-')}] | "
        f"EPS估值(SOPv2.0):[預估EPS={eps_s}/PE={pe_s}/💎合理切入={ent_s}/🎯目標={tgt_s}/{r.get('估值診斷', '-')}]"
    )

def build_complete_ai_snapshot():
    lines = [
        f"【⚡ 淘金客 v23.0 雙核 20 模組強制聯網深度健檢指令】（{now_str}）：請針對以下戰情室數據包，優先自主聯網查證美股/台指夜盤、重點個股之主力分點籌碼、資券借變化、CB可轉債轉換價與最新法說會催化劑，並嚴格依『模組20：雙核全維度輸出格式』與『淘金客 EPS 預估 SOP v2.0（時序收斂＋三率乘數T）』給出具體買賣點位與實戰行動指令！",
        f"1. [大盤方向儀表板與多週期指標]：總評分={score_5d}/100分 ({grade_str} | {circuit_trace_str}) | 建議水庫水位={water_level_str} | 大盤現價={twii_now:,.1f}點 ({fleet['twii_chg_pct']:+.2f}% | 開盤{twii_open:,.0f}/高{fleet['twii_high']:,.0f}/低{fleet['twii_low']:,.0f}/昨收{twii_prev_c:,.0f}) | 趨勢定性：{tf['long_trend']} / {tf['mid_trend']} / {tf['short_trend']} | 上檔預計目標：第一站{tf['up_t1'][0]:,.0f}~{tf['up_t1'][1]:,.0f}點、第二站{tf['up_t2'][0]:,.0f}~{tf['up_t2'][1]:,.0f}點、第三站{tf['up_t3'][0]:,.0f}~{tf['up_t3'][1]:,.0f}點 | 下檔預計支撐：第一道{tf['dn_s1'][0]:,.0f}~{tf['dn_s1'][1]:,.0f}點、第二道{tf['dn_s2'][0]:,.0f}~{tf['dn_s2'][1]:,.0f}點、終極鐵底(240MA年線){tf['dn_s3']:,.0f}點",
        f"2. [模組16 雙恐慌指數＋美股夜盤連動＋期權匯率]：美股VIX={tf['vix_now']:.2f}({tf['vix_chg_pct']:+.2f}% | {tf['vix_status']}) | 費半SOX={tf['sox_chg']:+.2f}% | 台積電ADR={tf['tsm_chg']:+.2f}% | 輝達NVDA={tf['nvda_chg']:+.2f}% | 台股散戶恐慌指數={tf['tw_panic_idx']}/100 ({tf['panic_label']}) | 大台外資={in_fini_chg:+,}口(未平倉={in_fini_oi:+,}) / 大台投信={sitc_tx_chg_v:+,}口(未平倉={in_sitc_oi:+,}) | 小台外資={fini_mtx_chg_v:+,}口 / 小台散戶={retail_mtx_chg_v:+,}口 | 外資佈空成本={est_short_cost:,.0f}點(滿足點={target_cover_2:,.0f}) | 純個股維持率={in_maint_pct:.1f}% | PCR={in_pcr:.1f}% | 權證避險={in_warrant_yi:+.2f}億 | 台幣={fx_now:.3f}({fx_status_str})"
    ]
    lines.append("3. [短線表1-A：看技術+基本面(有基之彈旱地拔蔥榜)]：" + (" ； ".join([fmt_stock_full_detail(r) for _, r in df_launch_fund.head(8).iterrows()]) if not df_launch_fund.empty else "今日暫無符合標的"))
    lines.append("4. [短線表1-B：純看技術面(主力純籌碼炒作旱地拔蔥榜)]：" + (" ； ".join([fmt_stock_full_detail(r) for _, r in df_launch_pure.head(8).iterrows()]) if not df_launch_pure.empty else "今日暫無符合標的"))
    lines.append("5. [短線表2：N字洗盤量縮守月線榜]：" + (" ； ".join([fmt_stock_full_detail(r) for _, r in df_pullback.head(8).iterrows()]) if not df_pullback.empty else "今日暫無符合標的"))
    lines.append("6. [短線表3：上升軌道推升與急拉警戒榜]：" + (" ； ".join([fmt_stock_full_detail(r) for _, r in df_orbit.head(8).iterrows()]) if not df_orbit.empty else "今日暫無符合標的"))
    lines.append("7. [長線表3-A：電子與金融權值核心多頭榜]：" + (" ； ".join([fmt_stock_full_detail(r) for _, r in df_long_bc.head(10).iterrows()]) if not df_long_bc.empty else "今日大盤回檔暫無站上月線之權值股"))
    lines.append("8. [長線表3-B：權值股回測支撐買點＆轉機股榜(含EPS SOP v2.0鑽石切入價)]：" + (" ； ".join([f"{r.get('類型與轉機標註', '')} " + fmt_stock_full_detail(r) for _, r in df_long_turn.head(12).iterrows()]) if not df_long_turn.empty else "今日暫無符合標的"))
    if not df_all_rev.empty:
        df_turn_top = df_all_rev[
            df_all_rev["轉機與成長型態"].str.contains("久旱逢甘霖|低迷翻揚", na=False)
            & (~df_all_rev["備註AI真偽鑑別"].str.contains("🏗️|⚠️", na=False))
            & (df_all_rev["當月營收(億)"].fillna(0) >= 0.20)
        ].head(15)
        if not df_turn_top.empty:
            t_list = []
            for _, r in df_turn_top.iterrows():
                eps_u = f"{r['預估EPS(元)']:.2f}元" if pd.notna(r.get("預估EPS(元)")) else "待轉盈"
                pe_u = f"{r['本益比(PE)']:.1f}x" if pd.notna(r.get("本益比(PE)")) else "無PE"
                t_list.append(
                    f"【{r['代碼']}{r['公司名稱']}({r['資料年月']})】現價={r['現價']}({r['今日%']:+}%) | {r['轉機與成長型態']} | "
                    f"法人={r.get('法人動向', '-')} | 備註真偽:{r.get('備註AI真偽鑑別', '-')} | CB:{r.get('CB可轉債', '-')} | "
                    f"單月YoY={r['年增YoY%']:+}%/累計年YoY={r['累計YoY%']:+}% | "
                    f"EPS估值(SOPv2.0):預估EPS={eps_u}/PE={pe_u}/💎切入價={r['💎EPS合理切入價']}({r['估值診斷']}) | "
                    f"大戶={r.get('400張大戶%', '-')} | 備註原文:{r['財報營收備註說明'][:45]}"
                )
            lines.append("9. [分頁四：久旱逢甘霖(已排除營建與一次性認列＋含三大法人買賣超＋EPS SOP v2.0)真轉機前15強]：" + " ； ".join(t_list))
    return "\n".join(lines)

with st.expander("🤖 點此展開【Gemini 淘金客 v23.0 專屬全維度戰情數據包】（已整合大盤方向儀表板＋EPS預估SOP v2.0＋三大法人T86籌碼！）", expanded=False):
    st.text_area("📋 手機/電腦可直接全選複製貼給 Gemini 淘金客進行雙核 20 模組深度健檢：", value=build_complete_ai_snapshot(), height=220)

tab1, tab2, tab3, tab4 = st.tabs([
    "🧭 台股加權指數方向儀表板 (五維度多空評分 ＋ VIX/散戶恐慌指數 ＋ 漲跌目標與均線導航)",
    "⚡ 短線操作精選區 (🔥技+基有基之彈 vs 🎭純技術炒作 ＋ 三大法人買賣超)",
    "🏛️ 長期投資波段區 (💎EPS SOP v2.0 鑽石切入價 ＋ 三率乘數T ＋ 三大法人買賣超)",
    "📈 上市櫃每月營收自動撈取專區 (2年破繭 ＋ EPS SOP v2.0 ＋ 三大法人 ＋ 備註AI真偽)"
])

with tab1:
    hero_box_class = "war-hero-box" if (score_5d >= 60 or is_above_ma5) else "war-hero-box-warn"
    score_color = "#00e676" if (score_5d >= 60 or is_above_ma5) else "#ffab40"
    banner_bg = "rgba(0, 230, 118, 0.16)" if (score_5d >= 60 or is_above_ma5) else "rgba(255, 152, 0, 0.22)"
    banner_color = "#00e676" if (score_5d >= 60 or is_above_ma5) else "#ffd54f"

    if is_above_ma10:
        dir_guide_msg = f"✅ <b>今日大盤（{twii_now:,.1f}點）已連破 日5MA（{twii_ma5:,.0f}點）、60分5MA（{fleet['k60_ma5']:,.0f}點）與 日10MA 十日線（{twii_ma10:,.0f}點）</b>！依城堡原則，十日線已由反壓轉為下檔地板支撐，短多趨勢確立，正挑戰上檔月線（{twii_ma20:,.0f}點）與半年線（{twii_ma120:,.0f}點）反壓區！"
    elif is_above_ma5:
        dir_guide_msg = f"✅ <b>今日大盤（{twii_now:,.1f}點）已正式站穩 60分5MA（{fleet['k60_ma5']:,.0f}點）與 日5MA 五日攻擊線（{twii_ma5:,.0f}點）之上</b>，確認單邊急殺結束、正式展開日線級跌深反彈波！"
    else:
        dir_guide_msg = f"🔑 收盤須突破 <b>60分5MA（{fleet['k60_ma5']:,.0f}點）與 日5MA（{twii_ma5:,.0f}點）</b> 才算單邊殺盤止跌。"

    st.markdown(f"""
    <div class="{hero_box_class}">
        <div class="war-score-title">🧭 【台股加權指數多空方向儀表板 ➔ 五維度＋台幣匯率量化診斷】（大盤多空總評分：<span style="font-size:36px; color:{score_color};">{score_5d}</span> / 100 分）</div>
        <div class="war-circuit-trace">{circuit_trace_str}</div><br>
        <div class="war-grade-banner" style="background:{banner_bg}; color:{banner_color}; border:1px solid {banner_color};">🧭 大盤當前方向定性：{grade_str}</div>
        <div class="war-dim-grid">
            <div class="war-dim-item"><b>1. 純個股維持率 ({s1_maint}/25分)</b><br>{s1_desc}</div>
            <div class="war-dim-item"><b>2. 大小台期貨 ({s2_fut}/25分)</b><br>{s2_desc}</div>
            <div class="war-dim-item"><b>3. 選擇權PCR＋權證 ({s3_opt_war}/20分)</b><br>{s3_desc}</div>
            <div class="war-dim-item"><b>4. 60分K與日5MA扳機 ({s4_k60}/15分)</b><br>{s4_desc}</div>
            <div class="war-dim-item"><b>5. 空間與KDJ彈力 ({s5_space}/15分)</b><br>{s5_desc}</div>
            <div class="war-dim-item"><b>6. 台幣匯率熱錢監控</b><br>{fx_status_str}</div>
        </div>
        <div class="war-action-box">
            <div style="font-size:18.5px; font-weight:900; color:#ffd54f; margin-bottom:6px;">
                🎯 台股大盤方向導航 ＆ 股市水庫理論建議資金水位：【{water_level_str}】
            </div>
            <b>①【長線保護傘 vs. 中短線位階】</b>：目前 <b>240MA 年線（{twii_ma240:,.0f}點）與 120MA 半年線（{twii_ma120:,.0f}點）雙雙維持上揚助漲</b>（長線牛市架構未破）。{dir_guide_msg}<br>
            <b>②【上檔三道反壓觀察區（個股逢高分批鎖利點）】</b>：反彈第一道壓力在 <b>下彎 20MA月線與5月線（{twii_ma20:,.0f}~{twii_m_ma5:,.0f}點）</b>、第二道在 <b>120MA半年線（{twii_ma120:,.0f}點）</b>、第三道極限反壓在 <b>60MA季線（{twii_ma60:,.0f}點）</b>。由於月季線仍扣抵高檔，指數初碰月線與半年線區間必有震盪拉回打第二隻腳（W底右腳），個股操作請遵循「買在支撐、不追高乖離、專注三率三升（T=1.12）與法人同買股」！
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("#### 🔮 【大盤多空趨勢預估中心】美股 VIX / 費半 / 台積ADR × 台股散戶恐慌指數 × 波段「預計漲到哪／跌到哪」點位導航")
    p_col1, p_col2, p_col3 = st.columns([1.35, 1.25, 2.4])

    with p_col1:
        sox_c = "#ff4d4f" if tf["sox_chg"] >= 0 else "#00e676"
        tsm_c = "#ff4d4f" if tf["tsm_chg"] >= 0 else "#00e676"
        nvda_c = "#ff4d4f" if tf["nvda_chg"] >= 0 else "#00e676"
        st.markdown(f"""
        <div class="radar-card-highlight">
            <div class="radar-title">🌎 美股 VIX 恐慌 ＆ 費半/台積ADR連動</div>
            <div class="radar-big-val" style="color:{tf['vix_color']};">{tf['vix_now']:.2f} <span style="font-size:18px;">({tf['vix_chg_pct']:+.2f}%)</span></div>
            <div class="radar-sub-highlight" style="background:rgba(255,255,255,0.08); color:{tf['vix_color']}; border:1px solid {tf['vix_color']};">
                {tf['vix_status']}
            </div>
            <div class="radar-detail-row">• <b>VIX 均線</b>：5MA <b style="color:#ffd54f;">{tf['vix_ma5']:.2f}</b> ｜ 20MA <b>{tf['vix_ma20']:.2f}</b></div>
            <div class="radar-detail-row">• <b>美股AI連動</b>：費半 <b style="color:{sox_c};">{tf['sox_chg']:+.2f}%</b>｜台積ADR <b style="color:{tsm_c};">{tf['tsm_chg']:+.2f}%</b>｜輝達 <b style="color:{nvda_c};">{tf['nvda_chg']:+.2f}%</b></div>
        </div>
        """, unsafe_allow_html=True)

    with p_col2:
        st.markdown(f"""
        <div class="radar-card-highlight">
            <div class="radar-title">😱 台股散戶綜合恐慌指數 (0~100)</div>
            <div class="radar-big-val" style="color:{tf['panic_color']};">{tf['tw_panic_idx']} <span style="font-size:18px;">/ 100 分</span></div>
            <div class="radar-sub-highlight" style="background:rgba(255,255,255,0.08); color:{tf['panic_color']}; border:1px solid {tf['panic_color']};">
                {tf['panic_label']}
            </div>
            <div class="radar-detail-row">• <b>合成權重</b>：維持率(35%)＋PCR(25%)＋小台散戶/權證(20%)＋VIX(20%)</div>
            <div class="radar-detail-row">• <b>實戰意義</b>：<b>&gt;60分代表散戶恐慌停損、籌碼乾淨；&lt;30分代表散戶過度樂觀須防拉回</b></div>
        </div>
        """, unsafe_allow_html=True)

    with p_col3:
        t1_title = "第一目標（挑戰下彎20MA月線與5月線反壓區）" if is_above_ma10 else "第一目標（短線必測：日10MA與外資初步回補區）"
        t2_title = "第二目標（波段中樞：上揚120MA半年線頸線區）"
        t3_title = "第三極限目標（強勢軋空天花板：60MA季線＆布林上軌）"
        s1_title = "第一支撐（短線強弱分界：已突破之日10MA/60分5MA）" if is_above_ma10 else "第一支撐（短線強弱分界：已突破之日5MA/60分5MA）"
        s2_title = "第二支撐（波段防守線：日5MA五日線與外資滿足區）" if is_above_ma10 else "第二支撐（W底右腳預計落點）"

        st.markdown(f"""
        <div class="radar-card-highlight" style="border-color:#00e676;">
            <div class="radar-title" style="color:#ffd54f; font-size:17.5px;">🧭 大盤長中短多空定性 ＆ 波段「預計漲到哪裡 ／ 跌到哪裡」量化預估（已按點位由低至高排序）</div>
            <div class="radar-detail-row">• <b>目前多空定性</b>：{tf['long_trend']} ｜ {tf['mid_trend']} ｜ <b style="color:#00e676;">{tf['short_trend']}</b></div>
            <hr style="border-color:rgba(255,255,255,0.15); margin:8px 0;">
            <div class="radar-detail-row" style="color:#ff8a80; font-size:16px;">🚀 <b>【預計這波反彈會漲到哪裡？（上檔三階段目標點位）】</b>：</div>
            <div class="radar-detail-row">&nbsp;&nbsp;🎯 <b>{t1_title}</b>：<b style="color:#ffd54f;">{tf['up_t1'][0]:,.0f} ～ {tf['up_t1'][1]:,.0f} 點</b></div>
            <div class="radar-detail-row">&nbsp;&nbsp;🎯 <b>{t2_title}</b>：<b style="color:#ffd54f;">{tf['up_t2'][0]:,.0f} ～ {tf['up_t2'][1]:,.0f} 點</b>（初碰易震盪換手）</div>
            <div class="radar-detail-row">&nbsp;&nbsp;🎯 <b>{t3_title}</b>：<b style="color:#ffd54f;">{tf['up_t3'][0]:,.0f} ～ {tf['up_t3'][1]:,.0f} 點</b>（初碰必拉回打第二隻腳！）</div>
            <div class="radar-detail-row" style="color:#80d8ff; font-size:16px; margin-top:6px;">🛡️ <b>【預計拉回會跌到哪裡？（下檔三道防守支撐底線）】</b>：</div>
            <div class="radar-detail-row">&nbsp;&nbsp;🛡️ <b>{s1_title}</b>：<b style="color:#00e676;">{tf['dn_s1'][0]:,.0f} ～ {tf['dn_s1'][1]:,.0f} 點</b> ｜ <b>{s2_title}</b>：<b>{tf['dn_s2'][0]:,.0f} ～ {tf['dn_s2'][1]:,.0f} 點</b> ｜ <b>終極鐵底（240MA年線）</b>：<b style="color:#ffd54f;">{tf['dn_s3']:,.0f} 點</b></div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("#### 🏛️ 加權指數多週期技術位階 ＆ 股市水庫資金控管司令塔")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(f"📊 台股加權指數 ({fleet['date_str']})", f"{twii_now:,.1f} 點", f"{fleet['twii_chg_pct']:+.2f}% (高低 {fleet['twii_low']:,.0f}~{fleet['twii_high']:,.0f})", delta_color="inverse")
        st.caption(f"• 開盤：`{twii_open:,.0f}` ｜ 昨收：`{twii_prev_c:,.0f}`\n\n• 10週均線：`{twii_w_ma10:,.0f}` ｜ 5月均線：`{twii_m_ma5:,.0f}`")
    with col2:
        st.metric("📈 日KDJ 與 60分KDJ 動能", f"日J {j_now:.1f}", f"{j_trend_str} ｜ 60分J={k60_j:.1f}", delta_color="normal")
        st.caption(f"• 日DMI：`+DI={fleet['pdi_d']}` vs `-DI={fleet['mdi_d']}` (`ADX={fleet['adx_d']}`)\n\n• 60分5MA：`{fleet['k60_ma5']:,.0f}` ｜ 60分20MA：`{fleet['k60_ma20']:,.0f}`")
    with col3:
        st.metric("🎯 外資空單成本與回補錨點", f"{est_short_cost:,.0f} 點", f"外資空單每口剩賺 +{short_profit_pts:,.0f}點 (+{short_profit_pct:.1f}%)", delta_color="off")
        st.caption(f"• 初步回補區：`{target_cover_1:,.0f} 點` ｜ 終極滿足區：`{target_cover_2:,.0f} 點`\n\n• 大台實質空單(扣投信)：**`{real_directional_fini_oi:+,} 口`**")
    with col4:
        st.metric("💧 股市水庫建議資金水位", water_level_str.split("(")[0].strip(), f"依據五維度總分 {score_5d}/100 分動態精算", delta_color="off")
        st.caption(f"• 策略：{water_level_str.split('(')[-1].replace(')', '')}\n\n• 原則：**保留現金水庫彈性，待月線走平二測突破再擴充水位**")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("#### 🧭 【大盤各級均線支撐壓力導航表】動態城堡原則（壓力突破轉地板支撐 vs. 上檔蓋頭反壓）")
    def build_pure_ma_row(ma_name, ma_val, ma_prev_val, deduct_val, role_tag, action_guide):
        dist_pts = round(ma_val - twii_now, 0)
        dist_pct = round((ma_val - twii_now) / max(1.0, twii_now) * 100.0, 2)
        slope_str = "📉 下彎蓋頭" if ma_val < ma_prev_val else "📈 上揚支撐"
        deduct_str = f"扣高 {deduct_val:,.0f} (助跌)" if deduct_val > twii_now * 1.01 else f"扣低 {deduct_val:,.0f} (助漲)"
        pos_str = f"上方 +{dist_pts:,.0f}點 (+{dist_pct:.2f}%)" if dist_pts >= 0 else f"✅已站上！下方 {dist_pts:,.0f}點 ({dist_pct:.2f}%)"
        return {
            "均線名稱與週期": ma_name,
            "當前動態點位 (距現價)": f"{ma_val:,.0f} 點 ({pos_str})",
            "均線方向 ＆ 扣抵狀態": f"{slope_str} ｜ {deduct_str}",
            "動態城堡定性 (壓力 vs 支撐)": role_tag,
            "大盤方向與個股實戰應對指引": action_guide
        }

    ma10_role = "✅ 【十日線已突破轉支撐】" if is_above_ma10 else "🚨 【短線第一道反壓】"
    ma10_guide = f"已帶量突破10MA({twii_ma10:,.0f}點)轉為短線地板支撐！未跌破前反彈波延續。" if is_above_ma10 else f"短線反彈第一目標區({twii_ma10:,.0f}點)，突破才算轉強。"

    df_ma_matrix = pd.DataFrame([
        build_pure_ma_row("① 60分K 5MA (極短線)", fleet["k60_ma5"], fleet["k60_ma5"] + 50, fleet["k60_ma5"] + 150, "✅ 【微觀止跌已站上】" if twii_now >= fleet["k60_ma5"] else "🔑 【微觀止跌第一關】", "目前已站上此線轉為極短線支撐；盤中守穩則多方控盤。"),
        build_pure_ma_row("② 日K 5MA (五日攻擊線)", twii_ma5, fleet["ma5_prev"], fleet["deduct_5d"], "✅ 【五日線已突破轉支撐】" if is_above_ma5 else "🔑 【短線止跌確認線】", f"已突破日5MA({twii_ma5:,.0f}點)且5MA翻揚助漲！為短線多方慣性防守線。"),
        build_pure_ma_row("③ 日K 10MA (十日慣性線)", twii_ma10, fleet["ma10_prev"], fleet["deduct_10d"], ma10_role, ma10_guide),
        build_pure_ma_row("④ 日K 20MA (月線・多空生命線)", twii_ma20, fleet["ma20_prev"], fleet["deduct_20d"], "✅ 【月線已突破】" if twii_now >= twii_ma20 else "🏆 【上檔第一反壓 ➔ 二測突破真反轉】", f"目前最近之上檔蓋頭反壓({twii_ma20:,.0f}點)！初碰下彎月線易震盪拉回打第二隻腳；站穩才算波段空翻多。"),
        build_pure_ma_row("⑤ 日K 120MA (半年線・輕警戒)", twii_ma120, fleet["ma120_prev"], fleet["deduct_120d"], "✅ 【半年線已收復】" if twii_now >= twii_ma120 else "🧭 【上檔第二目標區】(上揚半年線)", f"半年線({twii_ma120:,.0f}點)仍上揚扣低！與月線構成 `45,144~45,387點` 密集壓力帶，個股逢高勿追高。"),
        build_pure_ma_row("⑥ 日K 60MA (季線・外資成本區)", twii_ma60, fleet["ma60_prev"], fleet["deduct_60d"], "🚨 【終極蓋頭反壓】(中期強壓區)", f"本波反彈極限目標({twii_ma60:,.0f}點)！季線扣高下彎，反彈至此區個股宜分批減碼鎖利。"),
        build_pure_ma_row("⑦ 日K 240MA (年線・真正鐵底)", twii_ma240, fleet["ma240_prev"], twii_ma240 * 0.92, "💎 【長線真正強支撐】(牛熊防線)", f"真正的長線鐵底支撐({twii_ma240:,.0f}點)，未跌破前長線皆屬多頭架構。")
    ])
    st.dataframe(df_ma_matrix, use_container_width=True, hide_index=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("#### 🧭 大盤五大籌碼與台幣匯率雷達（大小台期貨拆解 × 純個股維持率 × 台幣匯率 × 選擇權PCR × 權證避險）")
    d_col1, d_col2, d_col3, d_col4, d_col5 = st.columns(5)

    with d_col1:
        st.markdown(f"""
        <div class="radar-card-highlight">
            <div class="radar-title">1. 大小台期貨動向 (已區分大/小台)</div>
            <div class="radar-big-val" style="color:#ffd54f;">投信 {sitc_tx_chg_v:+,} 口</div>
            <div class="radar-sub-highlight" style="background:rgba(245,158,11,0.18); color:#fcd34d; border:1px solid #f59e0b;">
                🎯 外資佈空成本：<span style="font-size:19px; color:#fff;">{est_short_cost:,.0f} 點</span><br>
                💰 每口剩賺：<span style="color:#00e676;">+{short_profit_pts:,.0f} 點 (+{short_profit_pct:.1f}%)</span>
            </div>
            <div class="radar-detail-row">• <b>大台今日淨量</b>：外資 <b style="color:#00e676;">{in_fini_chg:+,}</b>｜投信 <b style="color:#ff4d4f;">{sitc_tx_chg_v:+,}</b></div>
            <div class="radar-detail-row">• <b>小台今日淨量</b>：外資 <b style="color:#00e676;">{fini_mtx_chg_v:+,}</b>｜散戶 <b style="color:#00e676;">{retail_mtx_chg_v:+,}</b></div>
            <div class="radar-detail-row">• 滿足點：<b>{target_cover_2:,.0f} 點</b>｜大台實質空單：<b>{real_directional_fini_oi:+,}</b></div>
            <div class="radar-score-pill">得分：{s2_fut} / 25 分</div>
        </div>
        """, unsafe_allow_html=True)

    with d_col2:
        maint_color = "#00e676" if in_maint_pct <= 155.0 else "#fbbf24"
        maint_zone_label = f"🚨 {in_maint_pct:.1f}% 破135%！T+2斷頭底！" if in_maint_pct <= 135.0 else ("🟢 底部清洗完成強勢回升中！" if in_maint_pct <= 160.0 else "🟡 >160% 融資水位正常")
        st.markdown(f"""
        <div class="radar-card-highlight">
            <div class="radar-title">2. 純個股融資維持率 (排除ETF)</div>
            <div class="radar-big-val" style="color:{maint_color};">{in_maint_pct:.1f} %</div>
            <div class="radar-sub-highlight" style="background:rgba(0,230,118,0.16); color:{maint_color}; border:1px solid {maint_color};">{maint_zone_label}</div>
            <div class="radar-detail-row">• 常態落底區：<b style="color:#ffd54f;">135% ～ 145%</b></div>
            <div class="radar-detail-row">• 斷頭法規：<b>&lt;135% ➔ T+2 早盤斷頭</b></div>
            <div class="radar-detail-row">• 範圍：<b>剔除 00xx ETF 重複計算</b></div>
            <div class="radar-score-pill">得分：{s1_maint} / 25 分</div>
        </div>
        """, unsafe_allow_html=True)

    with d_col3:
        fx_is_appreciating = (fx_chg_ntd < 0) and (fx_now <= fx_ma5)
        fx_card_color = "#00e676" if fx_is_appreciating else ("#fbbf24" if fx_now <= fx_ma5 else "#ff4d4f")
        fx_dir_str = f"升值 {abs(fx_cents_chg):.1f} 分" if fx_chg_ntd < 0 else (f"貶值 {abs(fx_cents_chg):.1f} 分 (仍守5MA內)" if (fx_chg_ntd > 0 and fx_now <= fx_ma5) else (f"貶值 {abs(fx_cents_chg):.1f} 分" if fx_chg_ntd > 0 else "持平"))
        st.markdown(f"""
        <div class="radar-card-highlight">
            <div class="radar-title">3. 新台幣匯率 (USD/TWD 熱錢)</div>
            <div class="radar-big-val" style="color:{fx_card_color};">{fx_now:.3f} 元</div>
            <div class="radar-sub-highlight" style="background:rgba(255,255,255,0.08); color:{fx_card_color}; border:1px solid {fx_card_color};">今日：{fx_dir_str}</div>
            <div class="radar-detail-row">• 匯率 5MA 轉折線：<b style="color:#ffd54f;">{fx_ma5:.3f} 元</b></div>
            <div class="radar-detail-row">• 匯率 20MA 月線：<b>{fx_ma20:.3f} 元</b></div>
            <div class="radar-detail-row">• 關鍵：<b>跌破 {fx_ma5:.3f} = 台幣轉升 (+5分)</b></div>
            <div class="radar-score-pill">即時熱錢指標</div>
        </div>
        """, unsafe_allow_html=True)

    with d_col4:
        pcr_color = "#00e676" if (in_pcr <= 75.0 or in_pcr >= 90.0) else "#fbbf24"
        pcr_msg = "🟢 PCR>=80% 莊家支撐轉強(免扣分)" if in_pcr >= 80.0 else ("⚠️ PCR<70% 觸發扣10分" if in_pcr < 70.0 else "⚠️ PCR<80% 莊家偏空扣5分")
        st.markdown(f"""
        <div class="radar-card">
            <div class="radar-title">4. 選擇權全月份 P/C Ratio</div>
            <div class="radar-big-val" style="color:{pcr_color};">{in_pcr:.1f} %</div>
            <div class="radar-sub-highlight" style="background:rgba(255,255,255,0.08); color:{pcr_color};">{pcr_msg}</div>
            <div class="radar-detail-row">• 距平衡(100%)：<b>{in_pcr - 100.0:+.1f}%</b></div>
            <div class="radar-detail-row">• 來源：<b>期交所雙通道直連</b></div>
            <div class="radar-score-pill">得分：{s3_pcr} / 12 分</div>
        </div>
        """, unsafe_allow_html=True)

    with d_col5:
        war_color = "#ff4d4f" if in_warrant_yi > 0 else ("#00e676" if in_warrant_yi <= -35.0 else "#fbbf24")
        st.markdown(f"""
        <div class="radar-card">
            <div class="radar-title">5. 權證：自營商(避險)</div>
            <div class="radar-big-val" style="color:{war_color};">{in_warrant_yi:+.2f} 億</div>
            <div class="radar-sub-highlight" style="background:rgba(255,255,255,0.08); color:{war_color};">{'🔥 認購大戶點火助漲' if in_warrant_yi > 0 else '🎯 認購停損宣洩'}</div>
            <div class="radar-detail-row">• 意義：<b>權證真實 Delta 避險</b></div>
            <div class="radar-detail-row">• 來源：<b>證交所 BFI82U</b></div>
            <div class="radar-score-pill">得分：{s3_war} / 8 分</div>
        </div>
        """, unsafe_allow_html=True)

with tab2:
    st.caption("💡 【短線雙軌分流＋淘金客 EPS SOP v2.0＋三大法人T86籌碼】左側已凍結「代碼＋股名」，估值診斷欄已標註三率乘數狀態（`🔥三率三升T=1.12` / `⚠️毛利稀釋T=0.88`）！")
    st.markdown("#### 🔥 【表 1-A：短線看技術＋基本面（有基之彈・單月與累計年營收雙成長＋旱地拔蔥點火榜）】")
    if not df_launch_fund.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_launch_fund)), use_container_width=True)
    else: st.info("今日盤面暫無符合「旱地拔蔥突破＋單月與累計年營收雙成長」之標的。")

    st.markdown("#### 🎭 【表 1-B：短線純看技術面（不看基本面・主力純籌碼旱地拔蔥點火炒作榜）】")
    if not df_launch_pure.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_launch_pure)), use_container_width=True)
    else: st.info("今日盤面暫無符合「純技術籌碼旱地拔蔥點火」之標的。")

    st.markdown("#### 🎯 【表二：N字洗盤蓄勢 ＆ 拉回月線/十日線低接榜（含三大法人買賣超與 EPS SOP v2.0）】")
    if not df_pullback.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_pullback)), use_container_width=True)
    else: st.info("今日暫無符合量縮回測月線有守之標的。")

    st.markdown("#### 🛡️ 【表三：上升軌道推升 ＆ KD鈍化 / 3日急拉警戒榜（含三大法人買賣超與 EPS SOP v2.0）】")
    if not df_orbit.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_orbit)), use_container_width=True)
    else: st.info("今日暫無符合多頭軌道推升之標的。")

with tab3:
    st.caption("💎 【淘金客 EPS 預估 SOP v2.0（時序收斂 ＋ 三率體質乘數 T）】公式：`EPS_預估 = (累計EPS / W) * [W + (1 - W) * M * T] * 0.95`，避免前三季已公布獲利重複灌水，並自動標註 `🔥三率三升T=1.12` 或 `⚠️毛利稀釋T=0.88`！")
    st.markdown("#### 🏛️ 【表 3-A：👑 電子權值股 ＆ 🏦 金融官股/龍頭金控 長線多頭核心波段榜（含 EPS SOP v2.0 ＋ 三大法人買賣超）】")
    if not df_long_bc.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_long_bc)), use_container_width=True)
    else: st.info("今日大盤回檔，暫無同時站上月線且 DMI 多方之權值股；請參考下方【表 3-B】低估鑽石買點！")

    st.markdown("#### 🔄 【表 3-B：🔄 轉機題材特選股 ＆ 💎 電子/金融權值股回測長線支撐買點榜（含 EPS SOP v2.0 ＋ 三大法人買賣超）】")
    if not df_long_turn.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_long_turn)), use_container_width=True)
    else: st.info("目前暫無符合條件之轉機題材股或權值回測買點。")

with tab4:
    target_latest_ym = get_target_revenue_ym_list()[0][2]
    st.markdown("#### 📈 【上市櫃每月營收動態撈取 ＆ 玩法 1~5 全整合（2年破繭 ＋ EPS SOP v2.0 ＋ 三大法人 ＋ 備註AI真偽）快篩專區】")
    st.caption(f"📡 動態月份引擎：自動推算最新應公布月份為 **{target_latest_ym} (民國年月)**！已全面導入「淘金客 EPS 預估 SOP v2.0 (含三率乘數T)」、「三大法人買賣超」並徹底排除營建建案交屋股！")

    if df_all_rev.empty:
        st.warning("⚠️ 目前證交所/公開資訊觀測站月營收暫無回應，請點選上方「🔄 立即重抓」按鈕再試。")
    else:
        f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns([1.7, 1.4, 1.0, 1.0, 2.9])
        search_kw = f_col1.text_input("🔍 搜尋代碼/名稱/備註", value="", placeholder="例如：2301、光寶科、AI...")
        mkt_filter = f_col2.selectbox("🏛️ 市場與籌碼篩選", [f"全部市場 (含{target_latest_ym}與上月)", f"⚡ 僅看已公告最新月 ({target_latest_ym})", "🔥 僅看法人買超 (外資>0 或 投信>0)", "🏦 僅看投信買超認養股", "🔥 僅看含 CB 可轉債標的", "僅看上市", "僅看上櫃"])
        min_yoy = f_col3.number_input("📊 最低單月YoY(%)", value=20.0, step=5.0, format="%.1f")
        min_mom = f_col4.number_input("📈 最低月增MoM(%)", value=0.0, step=5.0, format="%.1f")
        quick_mode = f_col5.selectbox(
            "⚡ 玩法 1~5 實戰快篩模式 (自動剔除營建建案交屋與微型股)",
            [
                "🌱 久旱逢甘霖：前期累計衰退/低迷 ➔ 本月首次爆發 YoY>=20% 轉機股 (排除營建)",
                "👑 玩法1終極獵殺：久旱破繭(YoY>=20%) ＋ 大戶鎖碼(>=50%) ＋ 今日收紅或法人買超 ＋ 排除營建/業外/一次性認列",
                "🔥 玩法3可轉債套利：久旱破繭或高成長(YoY>=20%) ＋ 具【🔥含CB可轉債】主力拉抬誘因股",
                "💎 低估鑽石成長股：單月YoY>=20% 且 現價低於【💎EPS合理切入價 (SOP v2.0)】",
                "🚀 有基之彈雙成長：單月YoY>=設定值 且 累計年營收YoY>0% 且 MoM>=設定值",
                "📋 顯示全市場所有公司 (含營建與所有月份)"
            ]
        )

        df_filtered = df_all_rev.copy()
        if "僅看已公告最新月" in mkt_filter:
            df_filtered = df_filtered[df_filtered["資料年月"] == target_latest_ym]
        elif "法人買超" in mkt_filter:
            df_filtered = df_filtered[(df_filtered["外資買賣(張)"] > 0) | (df_filtered["投信買賣(張)"] > 0)]
        elif "投信買超" in mkt_filter:
            df_filtered = df_filtered[df_filtered["投信買賣(張)"] > 0]
        elif "CB" in mkt_filter:
            df_filtered = df_filtered[df_filtered["CB可轉債"].str.contains("CB", na=False)]
        elif mkt_filter == "僅看上市":
            df_filtered = df_filtered[df_filtered["市場"] == "上市"]
        elif mkt_filter == "僅看上櫃":
            df_filtered = df_filtered[df_filtered["市場"] == "上櫃"]

        if search_kw.strip():
            kw = search_kw.strip()
            df_filtered = df_filtered[
                df_filtered["代碼"].str.contains(kw, case=False, na=False)
                | df_filtered["公司名稱"].str.contains(kw, case=False, na=False)
                | df_filtered["產業別"].str.contains(kw, case=False, na=False)
                | df_filtered["財報營收備註說明"].str.contains(kw, case=False, na=False)
            ]
        else:
            not_construction_mask = (
                (~df_filtered["產業別"].str.contains("建材營造|營造", na=False))
                & (~df_filtered["備註AI真偽鑑別"].str.contains("🏗️", na=False))
                & (~df_filtered["轉機與成長型態"].str.contains("🏗️", na=False))
                & (~df_filtered["代碼"].isin(CONSTRUCTION_BLACKLIST))
            )
            if "久旱逢甘霖" in quick_mode:
                df_filtered = df_filtered[
                    df_filtered["轉機與成長型態"].str.contains("久旱逢甘霖|低迷翻揚", na=False)
                    & (df_filtered["年增YoY%"] >= min_yoy)
                    & (df_filtered["當月營收(億)"].fillna(0) >= 0.20)
                    & not_construction_mask
                ]
            elif "玩法1終極獵殺" in quick_mode:
                df_filtered = df_filtered[
                    df_filtered["轉機與成長型態"].str.contains("久旱逢甘霖|低迷翻揚", na=False)
                    & (df_filtered["年增YoY%"] >= min_yoy)
                    & (df_filtered["_tdcc_num"] >= 50.0)
                    & ((df_filtered["今日%"].fillna(-1.0) >= 0.0) | (df_filtered["外資買賣(張)"] > 0) | (df_filtered["投信買賣(張)"] > 0))
                    & (~df_filtered["備註AI真偽鑑別"].str.contains("⚠️|🏗️", na=False))
                    & (df_filtered["當月營收(億)"].fillna(0) >= 0.20)
                    & not_construction_mask
                ]
            elif "玩法3可轉債套利" in quick_mode:
                df_filtered = df_filtered[
                    (df_filtered["年增YoY%"] >= min_yoy)
                    & (df_filtered["CB可轉債"].str.contains("CB", na=False))
                    & (df_filtered["當月營收(億)"].fillna(0) >= 0.20)
                    & not_construction_mask
                ]
            elif "低估鑽石" in quick_mode:
                df_filtered = df_filtered[
                    (df_filtered["年增YoY%"] >= min_yoy)
                    & (df_filtered["估值診斷"].str.contains("低估鑽石", na=False))
                    & (df_filtered["當月營收(億)"].fillna(0) >= 0.20)
                    & not_construction_mask
                ]
            elif "有基之彈雙成長" in quick_mode:
                df_filtered = df_filtered[
                    (df_filtered["年增YoY%"] >= min_yoy)
                    & (df_filtered["累計YoY%"].fillna(-1) > 0.0)
                    & (df_filtered["月增MoM%"] >= min_mom)
                    & (df_filtered["當月營收(億)"].fillna(0) >= 0.20)
                    & not_construction_mask
                ]

        v_col1, v_col2 = st.columns([3.2, 4.8])
        if v_col1.button("🔬 點此執行【24個月(2年)歷史首次 YoY>=20%】深度驗證並自動剔除非首次股", type="primary"):
            with st.spinner(f"正在啟動 12 執行緒高速併發調閱 FinMind 過去 24 個月歷史營收 (共 {len(df_filtered)} 檔)..."):
                cand_codes = tuple(df_filtered["代碼"].tolist())
                st.session_state["ver_24m_map"] = verify_24m_first_breakout_batch(cand_codes, threshold_yoy=min_yoy)

        if "ver_24m_map" in st.session_state and st.session_state["ver_24m_map"]:
            ver_map = st.session_state["ver_24m_map"]
            verified_in_current = [c for c in df_filtered["代碼"] if c in ver_map]
            if verified_in_current:
                keep_codes = [c for c, val in ver_map.items() if (isinstance(val, tuple) and val[0])]
                df_filtered = df_filtered[df_filtered["代碼"].isin(keep_codes)].copy()
                df_filtered["轉機與成長型態"] = df_filtered["代碼"].map(lambda c: ver_map[c][2])
                df_filtered["_months_gap"] = df_filtered["代碼"].map(lambda c: ver_map[c][1])
                df_filtered = df_filtered.sort_values(by=["_months_gap", "年增YoY%"], ascending=[False, False]).drop(columns=["_months_gap"])
                v_col2.success(f"✅ 已套用 24 個月歷史破繭驗證！當前清單共 {len(df_filtered)} 檔真正【👑 2年首見破繭 / 🌟 久違半年以上首破 20%】純轉機股！")
        else:
            v_col2.caption("💡 點擊左側紅色按鈕後，系統會自動剔除上個月就已達標的老面孔，並自動記憶驗證結果！")

        cnt_latest_ym = int((df_all_rev["資料年月"] == target_latest_ym).sum())
        total_cnt = len(df_all_rev)
        cb_turn_cnt = int((df_all_rev["轉機與成長型態"].str.contains("久旱逢甘霖|低迷翻揚", na=False) & df_all_rev["CB可轉債"].str.contains("CB", na=False)).sum())
        match_cnt = len(df_filtered)

        m_c1, m_c2, m_c3, m_c4 = st.columns(4)
        m_c1.metric("📅 動態目標最新年月", f"{target_latest_ym} (10日截止)", f"已公告 {cnt_latest_ym} 家 / 全市場 {total_cnt} 家")
        m_c2.metric("🔥 久旱破繭＋含CB可轉債", f"{cb_turn_cnt:,} 家", "玩法3：具可轉債拉抬誘因轉機股")
        m_c3.metric("💎 全市場低估鑽石成長股", f"{int((df_all_rev['估值診斷'].str.contains('低估鑽石', na=False) & (df_all_rev['年增YoY%'] >= 20.0)).sum()):,} 家", "EPS SOP v2.0 低估鑽石")
        m_c4.metric("🎯 符合當前篩選條件家數", f"{match_cnt:,} 家", "已整合三大法人與三率乘數T")

        with st.expander(f"📋 【玩法 1~5 專屬 AI 深度獵殺數據包】點此展開一鍵複製當前 {match_cnt} 檔轉機股完整明細貼給 Gemini 挑出前 3 強！", expanded=False):
            hunt_lines = [
                f"【⚡ 淘金客 v23.0 雙核 20 模組強制聯網深度獵殺指令】：請針對以下分頁四篩選出之 {match_cnt} 檔『久旱破繭轉機股』，自主聯網查證其最新法說會題材、主力分點籌碼、資券借與CB可轉債轉換價，結合『淘金客 EPS 預估 SOP v2.0（時序收斂＋三率乘數T）』，嚴格依『模組20：雙核全維度輸出格式』為我挑出前 3 檔最具波段爆發力之真轉機鑽石股！"
            ]
            for idx_r, (_, r_rev) in enumerate(df_filtered.head(35).iterrows(), 1):
                eps_u = f"{r_rev['預估EPS(元)']:.2f}元" if pd.notna(r_rev.get("預估EPS(元)")) else "待轉盈"
                pe_u = f"{r_rev['本益比(PE)']:.1f}x" if pd.notna(r_rev.get("本益比(PE)")) else "無PE"
                hunt_lines.append(
                    f"{idx_r}. 【{r_rev['代碼']} {r_rev['公司名稱']} ({r_rev['市場']}/{r_rev['產業別']}/{r_rev['資料年月']})】"
                    f"現價={r_rev['現價']}({r_rev['今日%']:+}%) | 型態={r_rev['轉機與成長型態']} | "
                    f"法人={r_rev.get('法人動向', '-')}[外資{r_rev.get('外資買賣(張)', 0):+,}張/投信{r_rev.get('投信買賣(張)', 0):+,}張] | "
                    f"備註真偽={r_rev.get('備註AI真偽鑑別', '-')} | CB狀態={r_rev.get('CB可轉債', '-')} | "
                    f"單月YoY={r_rev['年增YoY%']:+}% / 月增MoM={r_rev['月增MoM%']:+}% / 累計YoY={r_rev['累計YoY%']:+}% | "
                    f"EPS估值(SOPv2.0):預估EPS={eps_u} / PE={pe_u} / 💎切入價={r_rev['💎EPS合理切入價']} ({r_rev['估值診斷']}) | "
                    f"400張大戶={r_rev.get('400張大戶%', '-')} | 財報備註原文：{r_rev['財報營收備註說明']}"
                )
            st.text_area("📋 全選複製以下轉機股清單貼給 Gemini，即可消耗剩餘 Token 進行法說題材與籌碼深度交叉獵殺：", value="\n".join(hunt_lines), height=220)

        df_display_rev = df_filtered.drop(columns=["_tdcc_num"], errors="ignore")
        csv_bytes = df_display_rev.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button(label=f"📥 一鍵下載當前篩選之【上市櫃月營收與三大法人估值總表 ({match_cnt} 家)】CSV 檔", data=csv_bytes, file_name=f"tw_monthly_revenue_eps_{target_latest_ym}.csv", mime="text/csv")

        def color_rev_val(val):
            try:
                v = float(val)
                return "color: #ff3b30; font-weight: bold;" if v > 0 else ("color: #00b853; font-weight: bold;" if v < 0 else "")
            except Exception:
                return ""

        df_pinned_rev = pin_code_name_index(df_display_rev)
        rev_color_cols = [c for c in ["今日%", "外資買賣(張)", "投信買賣(張)", "月增MoM%", "年增YoY%", "累計YoY%"] if c in df_pinned_rev.columns]
        rev_fmt = {"現價": "{:.2f}", "預估EPS(元)": "{:.2f}", "本益比(PE)": "{:.1f}", "💎EPS合理切入價": "{:.2f}", "今日%": "{:+.2f}%", "外資買賣(張)": "{:+,d}", "投信買賣(張)": "{:+,d}", "當月營收(億)": "{:,.2f}", "月增MoM%": "{:+.2f}%", "年增YoY%": "{:+.2f}%", "累計營收(億)": "{:,.2f}", "累計YoY%": "{:+.2f}%"}
        st.dataframe(df_pinned_rev.style.map(color_rev_val, subset=rev_color_cols).format(rev_fmt, na_rep="-"), use_container_width=True, height=600)

if auto_refresh:
    time.sleep(20)
    fetch_mis_realtime_batch.clear()
    st.rerun()
