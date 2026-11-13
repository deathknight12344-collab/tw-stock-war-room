import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import datetime, pytz, requests, urllib3, io, os, json, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="台股每日戰情室 v56.0", page_icon="🧭", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
.war-hero-box { background: linear-gradient(135deg, #0f2027 0%, #203a43 50%, #2c5364 100%); color: #fff; padding: 22px 26px; border-radius: 14px; border-left: 8px solid #00e676; margin-bottom: 18px; box-shadow: 0 6px 18px rgba(0,0,0,0.25); }
.war-hero-box-warn { background: linear-gradient(135deg, #2b1d1d 0%, #3d251e 50%, #4a2c2a 100%); color: #fff; padding: 22px 26px; border-radius: 14px; border-left: 8px solid #ff9800; margin-bottom: 18px; box-shadow: 0 6px 18px rgba(0,0,0,0.25); }
.war-score-title { font-size: 25px; font-weight: 900; color: #ffd54f; margin-bottom: 8px; line-height: 1.35; }
.war-circuit-trace { font-size: 15.5px; font-weight: 700; color: #ffab91; background: rgba(0,0,0,0.38); padding: 6px 12px; border-radius: 6px; margin-bottom: 10px; display: inline-block; }
.war-grade-banner { font-size: 19px; font-weight: 800; padding: 8px 14px; border-radius: 8px; margin-bottom: 14px; display: inline-block; }
.war-dim-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 12px; margin: 12px 0 16px 0; }
.war-dim-item { background: rgba(255,255,255,0.08); padding: 12px 15px; border-radius: 10px; font-size: 15px; line-height: 1.55; }
.war-dim-concl { font-size: 16.5px; font-weight: 900; color: #ffd54f; margin-bottom: 5px; border-bottom: 1px solid rgba(255,255,255,0.15); padding-bottom: 4px; display: block; }
.war-action-box { background: rgba(0,0,0,0.36); padding: 15px 18px; border-radius: 10px; border: 1px solid rgba(255,213,79,0.5); font-size: 16px; line-height: 1.68; }
.stage-pill-done { background: rgba(0,230,118,0.2); color: #00e676; border: 1px solid #00e676; padding: 2px 8px; border-radius: 6px; font-weight: 800; font-size: 14px; }
.stage-pill-active { background: rgba(255,77,79,0.25); color: #ff8a80; border: 1px solid #ff4d4f; padding: 2px 8px; border-radius: 6px; font-weight: 900; font-size: 14px; }
.stage-pill-wait { background: rgba(255,213,79,0.18); color: #ffd54f; border: 1px solid #ffd54f; padding: 2px 8px; border-radius: 6px; font-weight: 800; font-size: 14px; }
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
    fmt_dict = {k: "{:.2f}" for k in ["現價", "💎EPS合理切入價", "🎯EPS目標價", "預估EPS(元)", "昨日量比", "5MA量比", "天花板(反壓)", "月線承接", "鋼鐵防線", "極限停損"] if k in df.columns}
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
MARGIN_EXPANSION_SET = {"2330", "3017", "3324", "3653", "2059", "2383", "2368", "6274", "2345", "2308", "3711", "6239", "6187", "3131", "3583", "5274", "2301", "3443", "3661", "2454"}

def compute_eps_valuation(code, curr_p, sector_str, single_yoy, cum_yoy, pe_official, support_price=None):
    if curr_p is None or pd.isna(curr_p) or curr_p <= 0:
        return np.nan, np.nan, np.nan, np.nan, "➖ 暫無報價"

    is_fin = ("金融" in sector_str) or (code in ["2881", "2882", "2891", "2886", "2884", "2885", "2892", "2880", "5880", "2890", "2883", "2887", "2801", "2834"])
    if is_fin: pe_low, pe_high = 12.0, 15.5
    elif any(k in sector_str for k in ["IC設計", "矽智財", "散熱", "BMC", "ASIC", "高速傳輸", "CoWoS"]) or code in ["2454", "3034", "2379", "3017", "3653", "2059", "5274", "3529", "3661", "3443", "6669", "3324", "5269", "6187", "3131"]:
        pe_low, pe_high = 20.0, 28.0
    elif any(k in sector_str for k in ["晶圓", "封測", "扇形封裝", "CCL", "PCB", "連接器", "鏡頭", "電源"]) or code in ["2330", "3711", "6239", "2308", "3533", "2383", "2368", "3008", "2449", "3264", "6274"]:
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
                ext_chg_pct = None
                ext_label = ""
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
    all_syms = ["%5EVIX", "%5ESOX", "NQ=F"]
    for etf_sym, _, leaders in US_SECTOR_DEFINITIONS:
        all_syms.append(etf_sym)
        for l_sym, _ in leaders:
            if l_sym not in all_syms:
                all_syms.append(l_sym)

    quotes_map = {}
    with ThreadPoolExecutor(max_workers=12) as executor:
        fut_map = {executor.submit(fetch_yahoo_v8_realtime, sym, 2): sym for sym in all_syms}
        for fut in as_completed(fut_map):
            sym_k = fut_map[fut]
            res_q = fut.result()
            if res_q:
                quotes_map[sym_k] = res_q

    tsm_q = quotes_map.get("TSM", {})
    m_state = tsm_q.get("mkt_state", "CLOSED")
    nq_q = quotes_map.get("NQ=F", {})
    nq_chg = nq_q.get("chg_pct", 0.0)

    if m_state == "REGULAR":
        us_session_tag = "🟢【美股盤中即時交易中】"
    elif m_state in ["PRE", "PREPRE"]:
        us_session_tag = f"🌅【美股收盤＋盤前電子盤 (那指期 NQ {nq_chg:+.2f}%)】"
    else:
        us_session_tag = f"🏁【美股正規收盤 ＋ 🌙盤後/那指期 (NQ {nq_chg:+.2f}%)】"

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
        top_leaders_txt = "、".join([s for _, s in leader_strs[:3]])
        sector_list.append({
            "etf": etf_sym,
            "name": sec_name,
            "chg_pct": sec_chg,
            "summary": f"<b>{sec_name}({etf_sym} {sec_chg:+.2f}%{ext_lbl})</b> ➔ 指標股：{top_leaders_txt}"
        })

    sector_list.sort(key=lambda x: x["chg_pct"], reverse=True)
    top_up_sectors = [x["summary"] for x in sector_list[:3]]
    bot_dn_sectors = [x["summary"] for x in sorted(sector_list, key=lambda x: x["chg_pct"])[:2]]

    return {
        "session_tag": us_session_tag,
        "quotes": quotes_map,
        "nq_chg": nq_chg,
        "top_up_sectors": top_up_sectors,
        "bot_dn_sectors": bot_dn_sectors
    }

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
        curr = float(closes[-1]) if len(closes) > 0 else 43973.8
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
    # 保底基準已更新為 2026/11/12 盤後最新確定數據（外資大台 +4,733口 / 未平倉 -99,573口）
    res = {
        "fini_tx_oi": -99573, "fini_tx_chg": 4733, "sitc_tx_oi": 98901, "sitc_tx_chg": -2969, "dealer_tx_chg": -1764,
        "fini_mtx_oi": 4545, "fini_mtx_chg": 206, "sitc_mtx_chg": 0, "dealer_mtx_chg": -585, "retail_mtx_chg": 379,
        "pc_ratio": 85.6, "warrant_hedge_yi": -35.60, "official_stock_maint": None, "official_maint_date": "",
        "stock_margin_chg_yi": 0.0, "fut_date": "2026/11/12", "twse_date": ""
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
            df_f.columns = [str(c).strip() for c in df_f.columns]
            if len(df_f.columns) >= 14:
                dates = df_f.iloc[:, 0].dropna().unique()
                if len(dates) > 0:
                    latest_d = dates[-1]
                    res["fut_date"] = str(latest_d).strip()
                    sub_f = df_f[df_f.iloc[:, 0] == latest_d]
                    oi_net_col = next((c for c in df_f.columns if "未平倉" in c and "口數" in c and "淨額" in c), None)
                    oi_long_col = next((c for c in df_f.columns if "多方" in c and "未平倉" in c and "口數" in c), None)
                    oi_short_col = next((c for c in df_f.columns if "空方" in c and "未平倉" in c and "口數" in c), None)
                    for _, row in sub_f.iterrows():
                        prod_name, ident = str(row.iloc[1]).strip(), str(row.iloc[2]).strip()
                        net_trade = int(float(str(row.iloc[7]).replace(",", "").strip() or 0))
                        if oi_net_col:
                            oi_net = int(float(str(row[oi_net_col]).replace(",", "").strip() or 0))
                        elif oi_long_col and oi_short_col:
                            oi_net = int(float(str(row[oi_long_col]).replace(",", "").strip() or 0)) - int(float(str(row[oi_short_col]).replace(",", "").strip() or 0))
                        else:
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
    "6239": ("力成", "👑電子權值・FOPLP扇形封裝龍頭", "TW"),
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
        hh, ll = np.nanmax(highs[start_idx:i+1]), np.nanmin(lows[start_idx:i+1])
        rsv = ((closes[i] - ll) / (hh - ll) * 100.0) if (pd.notna(hh) and pd.notna(ll) and hh > ll) else 50.0
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

    twii_now = twii_official["close"] if twii_official["close"] > 0 else 43973.8
    twii_chg_pct = twii_official["chg_pct"]
    twii_open = twii_high = twii_low = twii_prev_c = twii_now
    ma5, ma10, ma20, ma60, ma120, ma240 = 43139.0, 42815.0, 44649.0, 46220.0, 45444.0, 39155.0
    w_ma10, m_ma5 = 46691.0, 45275.0
    ma5_prev, ma10_prev, ma20_prev, ma60_prev, ma120_prev, ma240_prev = ma5, ma10, ma20, ma60, ma120, ma240
    deduct_5d, deduct_10d, deduct_20d, deduct_60d, deduct_120d = ma5, ma10, ma20, ma60, ma120
    bb_upper, bb_lower = 48922.0, 40500.0
    k9, d9, j9, j9_prev, kd_cross_d = 67.4, 48.8, 104.7, 88.7, True
    pdi_d, mdi_d, adx_d, adx_d_prev, dmi_bull_d = 21.7, 33.9, 33.7, 34.6, False
    date_str, quote_src = today_str, "Yahoo Finance / 證交所"
    est_short_cost, target_cover_1, target_cover_2 = 45033.0, 43232.0, 41656.0
    k60_k, k60_d, k60_j, k60_j_prev, k60_kd_cross = 62.2, 65.3, 55.9, 48.3, False
    k60_pdi, k60_mdi, k60_adx, k60_adx_prev, k60_dmi_bull = 28.0, 22.0, 31.0, 30.0, True
    k60_ma5, k60_ma20, k60_ma60 = 43690.0, 43193.0, 44100.0
    fx_now, fx_prev, fx_chg_ntd, fx_ma5, fx_ma20 = 32.836, 32.940, -0.104, 32.840, 32.647

    try:
        if not data.empty and "^TWII" in data:
            df_twii = data["^TWII"].dropna(subset=["Open", "High", "Low", "Close"]).copy()
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
            df_60 = data_60m["^TWII"].dropna(subset=["Open", "High", "Low", "Close"]).copy()
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

    # 儲存全體掃描個股之「今日預估量 vs 昨日成交量比 (yod_vol_ratio)」供熱門族群雷達聚合運算
    scanned_sector_records = []

    for yf_tkr, code in yf_map.items():
        try:
            if raw_df.empty or yf_tkr not in raw_df: continue
            df_s = raw_df[yf_tkr].dropna(subset=["Open", "High", "Low", "Close"]).copy()
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
            yest_vol = max(1.0, float(v[-2])) if len(v) >= 2 else max(1.0, float(v[-1]))
            yod_vol_ratio = round(est_vol / yest_vol, 2)
            vol_ratio = round(est_vol / max(1.0, float(np.mean(v[-6:-1]))), 2)

            p_meta = price_dict.get(code, {})
            official_sec = p_meta.get("sector") or sector
            scanned_sector_records.append({
                "code": code, "name": name, "sector": sector, "official_sec": official_sec,
                "close": curr_p, "chg_1d": chg_1d, "est_vol": est_vol, "yest_vol": yest_vol,
                "yod_ratio": yod_vol_ratio, "ma5_ratio": vol_ratio,
                "turnover_yi": round(est_vol * curr_p / 100000.0, 2)
            })

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
            elif is_blue_chip and (cum_yoy_val is None or cum_yoy_val > 0.0):
                short_track_tag = "🏦核心權值(穩健護城河)"
            else:
                short_track_tag = "🎭純技術炒作(不看基本面)"

            est_eps, pe_disp, eps_entry_p, eps_target_p, val_diag = compute_eps_valuation(code, curr_p, sector, yoy_val, cum_yoy_val, pe_official, support_price=castle_iron)

            row_base = {
                "代碼": code, "股名": f"{name}{cb_tag}{liq_tag}", "族群": sector, "現價": curr_p, "今日%": chg_1d, "3日%": chg_3d,
                "短線屬性": short_track_tag,
                "法人動向": inst_tag, "外資買賣(張)": f_lots, "投信買賣(張)": s_lots,
                "預估EPS(元)": est_eps, "本益比(PE)": pe_disp, "💎EPS合理切入價": eps_entry_p, "🎯EPS目標價": eps_target_p, "估值診斷": val_diag,
                "日J值": stk_j9, "DMI多空": dmi_str, "ADX強度": adx_str, "昨日量比": yod_vol_ratio, "5MA量比": vol_ratio, "預估量(張)": est_vol,
                "天花板(反壓)": castle_ceiling, "月線承接": ma20, "鋼鐵防線": castle_iron, "極限停損": castle_stop,
                "400張大戶%": f"{tdcc_val:.1f}%" if tdcc_val is not None else "未揭露",
                "單月營收YoY": yoy_str, "累計年營收YoY": cum_yoy_str
            }
            is_heavy_dump = (chg_1d <= -2.0 and vol_ratio >= 1.25) or (chg_1d <= -4.2)

            is_above_ma20_trend = (curr_p >= ma20) and (curr_p >= ma5) and (curr_p >= ma10) and (curr_p >= ma60 or ma20 >= ma60 * 0.94)
            is_true_ma20_breakout = (curr_p >= ma20) and (curr_p >= ma5) and (curr_p >= ma10) and (prev_p < ma20) and (chg_1d >= 3.5) and (vol_ratio >= 1.25 or chg_1d >= 8.5) and (pdi > mdi)

            if (is_above_ma20_trend or is_true_ma20_breakout) and (not is_heavy_dump):
                is_overheated = (chg_3d >= 14.0) or (stk_j9 >= 93.0 and not kd_dmi_resonance and chg_1d < 8.5)
                if (is_true_ma20_breakout and not is_overheated) or ((chg_1d >= 2.0 and (vol_ratio >= 1.20 or chg_1d >= 7.5) and kd_dmi_resonance and not is_overheated) or (chg_1d >= 2.8 and (vol_ratio >= 1.25 or chg_1d >= 7.5) and not is_overheated and (pdi > mdi) and adx >= 16.0)):
                    if kd_dmi_resonance:
                        score, pattern = 100, f"👑 KD×DMI雙金叉共振旱地拔蔥 [{short_track_tag}]"
                    elif bb_bw <= 16.0:
                        score, pattern = 98, f"🔥 雙鴻/奇鋐型：布林壓縮旱地拔蔥 [{short_track_tag}]"
                    elif ma_tangle_pct <= 4.0:
                        score, pattern = 97, f"🌸 瑞昱/矽創型：均線糾結旱地拔蔥 [{short_track_tag}]"
                    elif is_true_ma20_breakout:
                        score, pattern = 96, f"🌱 階段3(3)旱地拔蔥：帶量長紅突破月線20MA [{short_track_tag}]"
                    else:
                        score, pattern = min(95, 86 + int(min(9, vol_ratio * 2.0 + chg_1d))), f"🚀 站穩月線帶量突破頸線 [{short_track_tag}]"
                    list_tab2_launch.append({"實戰優先分": score, "名將戰法診斷": pattern, **row_base})
                elif (-3.5 <= chg_1d <= 2.2) and (chg_3d >= -6.5) and (vol_ratio <= 0.95) and (stk_j9 <= 68.0) and (pdi >= mdi):
                    list_tab2_pullback.append({"蓄勢評分": min(99, 84 + int((1.0 - vol_ratio) * 15) + (3 if pdi > mdi else 0)), "名將戰法診斷": f"⚡ N字量縮守月線 [{short_track_tag}]", **row_base})
                elif curr_p >= ma5 >= ma10 >= ma20 and (pdi > mdi) and chg_1d >= -2.0:
                    pattern = f"🔥 3日急拉過熱 [{short_track_tag}]" if chg_3d >= 10.5 else (f"🚀 KD鈍化惟DMI續強 [{short_track_tag}]" if (stk_j9 >= 90.0 and pdi > mdi and adx_up) else f"🛡️ 沿5/10MA穩步推升 [{short_track_tag}]")
                    list_tab2_orbit.append({"趨勢狀態": pattern, **row_base})

            if is_blue_chip and (not is_heavy_dump):
                bc_rev_ok = has_solid_fund or (yoy_val is not None and yoy_val >= 0.0) or (cum_yoy_val is not None and cum_yoy_val >= 0.0) or (is_fin and (yoy_val is None or yoy_val >= -5.0))
                bc_tdcc_ok = (tdcc_val is None) or (tdcc_val >= 42.0)
                fund_qual = "🔥單月+累計雙成長" if has_solid_fund else "🏦累計年增穩健"
                if bc_rev_ok and bc_tdcc_ok and (curr_p >= ma20) and (pdi > mdi) and (chg_3d < 9.5) and (stk_j9 <= 88.0):
                    l_score = min(100, 88 + (3 if is_fin else 0) + (4 if has_solid_fund else 0) + (4 if (tdcc_val and tdcc_val >= 65.0) else 0) + (3 if "低估鑽石" in val_diag else 0) + (2 if (f_lots > 0 and s_lots > 0) else 0))
                    list_tab3_core_bluechip.append({"波段總分": l_score, "長線格局定性": f"👑 權值龍頭＋{fund_qual}" if kd_dmi_resonance else (f"🏦 績優金控＋{fund_qual}" if is_fin else f"✅ 長線多頭＋{fund_qual}"), **row_base})
                elif bc_rev_ok and (curr_p < ma20) and (curr_p >= ma240 * 0.94):
                    dip_score = min(98, 86 + (4 if chg_1d >= 0.0 else 0) + (3 if stk_j_up else 0) + (3 if has_solid_fund else 0) + (3 if "低估鑽石" in val_diag else 0) + (2 if (f_lots > 0 or s_lots > 0) else 0))
                    dip_tag = f"🟢【權值月線下築底反彈・{fund_qual}】(上檔月線{ma20}反壓)" if (curr_p >= ma5 and chg_1d >= 0.0) else f"⏳【權值左側回測支撐・{fund_qual}】"
                    list_tab3_turnaround_or_dip.append({"波段總分": dip_score, "類型與轉機標註": dip_tag, **row_base})
            elif (not is_blue_chip) and (code not in CONSTRUCTION_BLACKLIST) and (not is_heavy_dump):
                if (yoy_val is not None and 25.0 <= yoy_val <= 400.0) and (tdcc_val is not None and tdcc_val >= 48.0) and (curr_p >= ma20 >= ma60) and (pdi > mdi) and (stk_j9 <= 85.0):
                    t_row = dict(row_base)
                    t_row["股名"] = f"🔄【轉機股】{name}{cb_tag}{liq_tag}"
                    rev_type = "雙成長爆發" if (cum_yoy_val and cum_yoy_val > 0) else "久旱破繭轉機"
                    list_tab3_turnaround_or_dip.append({"波段總分": 92 + (4 if kd_dmi_resonance else 0) + (2 if (f_lots > 0 or s_lots > 0) else 0), "類型與轉機標註": f"🔄【{rev_type}】單月YoY{yoy_str}/累計{cum_yoy_str}＋DMI翻揚", **t_row})
        except Exception: continue

    st.session_state["scanned_sector_records"] = scanned_sector_records
    df_t1 = pd.DataFrame(list_tab2_launch).sort_values(by="實戰優先分", ascending=False) if list_tab2_launch else pd.DataFrame()
    df_t2 = pd.DataFrame(list_tab2_pullback).sort_values(by="蓄勢評分", ascending=False) if list_tab2_pullback else pd.DataFrame()
    df_t3 = pd.DataFrame(list_tab2_orbit).sort_values(by="3日%", ascending=False) if list_tab2_orbit else pd.DataFrame()
    df_long_bc = pd.DataFrame(list_tab3_core_bluechip).sort_values(by="波段總分", ascending=False) if list_tab3_core_bluechip else pd.DataFrame()
    df_long_turn = pd.DataFrame(list_tab3_turnaround_or_dip).sort_values(by=["波段總分", "今日%"], ascending=[False, False]) if list_tab3_turnaround_or_dip else pd.DataFrame()
    return df_t1, df_t2, df_t3, df_long_bc, df_long_turn, vol_note, rt_quotes
    # ==========================================
# 【v56.0 下半部完整程式碼：新增「台股當日熱門爆量族群雷達(預估量vs昨日量)」＋ 11/13 正2@32.57出清與力成/光寶科精準導航】
# 直接貼在 scan_all_stocks() 的 return 那一行正下方 (上半部維持 v56.0 不動！)
# ==========================================
import base64

ELECTRONICS_BLUE_CHIPS["6239"] = ("力成", "👑電子權值・FOPLP扇形封裝龍頭", "TW")
CORE_WATCHLIST["6239"] = ("力成", "👑電子權值・FOPLP扇形封裝龍頭", "TW")
MARGIN_EXPANSION_SET.add("6239")

def clean_html(raw_html: str) -> str:
    """移除多行 HTML 字串之行首縮排與空行，防止 Streamlit Markdown 將 HTML 誤判為程式碼區塊"""
    return "\n".join(line.strip() for line in raw_html.splitlines() if line.strip())


def map_tw_theme_cluster(code: str, name: str, sector: str, official_sec: str) -> str:
    """將台股個股映射至 14 大實戰主流題材族群，供當日預估量增比與熱門族群雷達聚合運算"""
    s_all = f"{sector} {official_sec} {name}"
    if code in ["2454", "3034", "3443", "3661", "5274", "3529", "5269", "8016"] or any(k in s_all for k in ["IC設計", "矽智財", "ASIC", "BMC", "驅動"]):
        return "👑 IC設計與矽智財IP"
    if code in ["3711", "6239", "2449", "3264", "6187", "3131", "3583"] or any(k in s_all for k in ["封測", "扇形封裝", "CoWoS", "封裝", "測試"]):
        return "🔬 先進封測與CoWoS/FOPLP"
    if code in ["2330", "2303", "5347"] or "晶圓" in s_all:
        return "🏭 晶圓代工與半導體"
    if code in ["2383", "2368", "6274", "3533"] or any(k in s_all for k in ["CCL", "PCB", "連接器", "銅箔", "電路板"]):
        return "⚡ 高頻CCL與伺服器PCB"
    if code in ["3017", "3324", "3653", "2059"] or any(k in s_all for k in ["散熱", "均熱片", "水冷", "導軌"]):
        return "❄️ AI水冷散熱與伺服器機構"
    if code in ["2317", "2382", "3231", "6669", "2357", "4938"] or any(k in s_all for k in ["AI伺服器", "AI PC", "電腦及週邊"]):
        return "🖥️ AI伺服器與PC硬體"
    if code in ["2308", "2301"] or any(k in s_all for k in ["電源", "儲能", "BBU"]):
        return "🔋 雲端電源與BBU儲能"
    if code in ["2345", "2379", "3558", "6285"] or any(k in s_all for k in ["網通", "通信網路", "光通訊", "衛星"]):
        return "📡 高階網通與光通訊CPO"
    if any(k in s_all for k in ["金融", "金控", "銀行", "保險", "證券"]):
        return "🏦 金融官股與績優金控"
    if any(k in s_all for k in ["化學", "化工", "材料", "特化", "塑膠", "玻陶", "玻璃"]):
        return "🧪 特用化學與半導體材料"
    if any(k in s_all for k in ["生技", "醫療", "新藥", "製藥"]):
        return "🧬 生技醫療與新藥研發"
    if any(k in s_all for k in ["資訊服務", "軟體", "資安", "系統整合"]):
        return "☁️ 資訊軟體與AI資安系統"
    if any(k in s_all for k in ["光電", "光學", "鏡頭", "面板"]):
        return "📷 光學鏡頭與光電顯示"
    if any(k in s_all for k in ["電機", "機械", "工具機", "重電", "電線電纜"]):
        return "⚙️ 重電能源與機器人工具機"
    return "🚀 電子零組件與中小型破繭股"


def compute_tw_hot_sectors(records: list[dict]) -> dict:
    """
    依據當日盤中/收盤『預估成交量 ÷ 昨日實際成交量 (昨量比 YoD)』與『族群平均漲跌幅』，
    動態推算台股今日資金最集中的【🔥 當日熱門爆量領漲族群 Top 4】與【❄️ 當日量縮/弱勢族群】！
    """
    if not records:
        return {"hot_html": "", "cold_html": "", "hot_text": "暫無數據", "cold_text": "暫無數據"}

    df_r = pd.DataFrame(records)
    df_r["theme"] = df_r.apply(lambda r: map_tw_theme_cluster(str(r["code"]), str(r["name"]), str(r["sector"]), str(r["official_sec"])), axis=1)

    theme_rows = []
    for theme_name, grp in df_r.groupby("theme"):
        if len(grp) == 0:
            continue
        # 計算族群「整體預估量 ÷ 昨日總量」與「個股平均昨量比」之綜合量增倍數
        tot_est_v = float(grp["est_vol"].sum())
        tot_yest_v = max(1.0, float(grp["yest_vol"].sum()))
        agg_yod = tot_est_v / tot_yest_v
        mean_yod = float(grp["yod_ratio"].clip(upper=5.0).mean())
        blended_yod = round(0.55 * agg_yod + 0.45 * mean_yod, 2)

        avg_chg = round(float(grp["chg_1d"].mean()), 2)
        max_chg = round(float(grp["chg_1d"].max()), 2)
        up_cnt = int((grp["chg_1d"] > 0).sum())
        tot_cnt = len(grp)

        # 依漲幅與預估量增比綜合計算「當日族群熱度積分」
        heat_score = avg_chg * 2.2 + max_chg * 0.6 + (blended_yod - 1.0) * 6.5

        grp_sorted = grp.sort_values(by=["chg_1d", "yod_ratio"], ascending=[False, False])
        leader_items_html = []
        leader_items_txt = []
        for _, st_row in grp_sorted.head(3).iterrows():
            c_chg = float(st_row["chg_1d"])
            c_yod = float(st_row["yod_ratio"])
            c_color = "#ff8a80" if c_chg >= 0 else "#69f0ae"
            leader_items_html.append(
                f"<b style='color:#ffffff;'>{st_row['name']}({st_row['code']})</b> "
                f"<b style='color:{c_color};'>{c_chg:+.2f}%</b>"
                f"<span style='color:#ffd54f; font-size:13px;'>[昨量比{c_yod:.1f}x]</span>"
            )
            leader_items_txt.append(f"{st_row['name']}({st_row['code']}){c_chg:+.2f}%[昨量比{c_yod:.1f}x]")

        theme_rows.append({
            "theme": theme_name,
            "avg_chg": avg_chg,
            "blended_yod": blended_yod,
            "up_ratio": f"{up_cnt}/{tot_cnt}紅",
            "heat_score": heat_score,
            "leaders_html": "、".join(leader_items_html),
            "leaders_txt": "、".join(leader_items_txt)
        })

    if not theme_rows:
        return {"hot_html": "", "cold_html": "", "hot_text": "暫無數據", "cold_text": "暫無數據"}

    df_th = pd.DataFrame(theme_rows).sort_values(by="heat_score", ascending=False).reset_index(drop=True)
    top_hot = df_th.head(4)
    bot_cold = df_th.tail(2).sort_values(by="avg_chg", ascending=True)

    hot_divs = []
    hot_txts = []
    for _, r_th in top_hot.iterrows():
        vol_badge_col = "#ffd54f" if r_th["blended_yod"] >= 1.15 else "#93c5fd"
        vol_desc = f"預估量增 {r_th['blended_yod']:.2f}x (較昨量{(r_th['blended_yod']-1.0)*100:+.0f}%)"
        hot_divs.append(
            f"<div style='margin:5px 0; padding:5px 9px; background:rgba(0,0,0,0.28); border-radius:6px; border-left:3px solid #ff8a80; font-size:14.5px; line-height:1.5;'>"
            f"<span style='background:rgba(255,77,79,0.22); color:#ff8a80; padding:1px 6px; border-radius:4px; font-weight:900; margin-right:6px;'>🔥 熱門族群</span>"
            f"<b style='color:#ffd54f;'>{r_th['theme']}</b> "
            f"(<b style='color:#ff8a80;'>均漲 {r_th['avg_chg']:+.2f}%</b> ｜ <b style='color:{vol_badge_col};'>📊 {vol_desc}</b>) "
            f"➔ <b style='color:#ffd54f;'>領頭羊：</b>{r_th['leaders_html']}</div>"
        )
        hot_txts.append(f"{r_th['theme']}(均漲{r_th['avg_chg']:+.2f}%/昨量比{r_th['blended_yod']:.2f}x ➔ 指標股:{r_th['leaders_txt']})")

    cold_divs = []
    cold_txts = []
    for _, r_th in bot_cold.iterrows():
        vol_desc = f"昨量比 {r_th['blended_yod']:.2f}x"
        cold_divs.append(
            f"<div style='margin:5px 0; padding:5px 9px; background:rgba(0,0,0,0.28); border-radius:6px; border-left:3px solid #69f0ae; font-size:14.5px; line-height:1.5;'>"
            f"<span style='background:rgba(0,230,118,0.18); color:#69f0ae; padding:1px 6px; border-radius:4px; font-weight:900; margin-right:6px;'>❄️ 量縮/調節</span>"
            f"<b style='color:#e2e8f0;'>{r_th['theme']}</b> "
            f"(<b>均 {r_th['avg_chg']:+.2f}%</b> ｜ <b>{vol_desc}</b>) "
            f"➔ <b style='color:#9ca3af;'>代表股：</b>{r_th['leaders_html']}</div>"
        )
        cold_txts.append(f"{r_th['theme']}(均{r_th['avg_chg']:+.2f}%/昨量比{r_th['blended_yod']:.2f}x ➔ {r_th['leaders_txt']})")

    return {
        "hot_html": "".join(hot_divs),
        "cold_html": "".join(cold_divs),
        "hot_text": " ； ".join(hot_txts),
        "cold_text": " ； ".join(cold_txts)
    }


@st.cache_data(ttl=60, show_spinner=False)
def refresh_live_taifex_oi_and_pcr(base_deriv: dict) -> dict:
    """強制校正期交所『大台/小台未平倉口數淨額 (OI)』與『選擇權 P/C Ratio』，確保 100% 即時浮動"""
    d = dict(base_deriv)
    headers = {"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"}
    now_tw = get_tw_now()

    try:
        q_end = now_tw.strftime("%Y/%m/%d")
        q_start = (now_tw - datetime.timedelta(days=7)).strftime("%Y/%m/%d")
        r_fut = requests.post(
            "https://www.taifex.com.tw/cht/3/futContractsDateDown",
            data={"queryStartDate": q_start, "queryEndDate": q_end},
            headers=headers, timeout=8, verify=False
        )
        if r_fut.status_code == 200 and len(r_fut.content) > 200:
            df_f = pd.read_csv(io.StringIO(r_fut.content.decode("cp950", errors="ignore")))
            df_f.columns = [str(c).strip() for c in df_f.columns]
            if len(df_f.columns) >= 14:
                latest_d = df_f.iloc[:, 0].dropna().unique()[-1]
                d["fut_date"] = str(latest_d).strip()
                sub_f = df_f[df_f.iloc[:, 0] == latest_d]
                oi_net_col = next((c for c in df_f.columns if "未平倉" in c and "口數" in c and "淨額" in c), None)
                oi_long_col = next((c for c in df_f.columns if "多方" in c and "未平倉" in c and "口數" in c), None)
                oi_short_col = next((c for c in df_f.columns if "空方" in c and "未平倉" in c and "口數" in c), None)
                tr_net_col = next((c for c in df_f.columns if "交易" in c and "口數" in c and "淨額" in c), None)

                def to_int_safe(v):
                    try:
                        return int(float(str(v).replace(",", "").strip()))
                    except Exception:
                        return 0

                for _, row in sub_f.iterrows():
                    prod_name = str(row.iloc[1]).strip()
                    ident = str(row.iloc[2]).strip()
                    net_tr = to_int_safe(row[tr_net_col]) if tr_net_col else to_int_safe(row.iloc[7])
                    if oi_net_col:
                        oi_val = to_int_safe(row[oi_net_col])
                    elif oi_long_col and oi_short_col:
                        oi_val = to_int_safe(row[oi_long_col]) - to_int_safe(row[oi_short_col])
                    else:
                        oi_val = to_int_safe(row.iloc[13])

                    if ("臺股期貨" in prod_name or "台股期貨" in prod_name) and ("小型" not in prod_name) and ("微型" not in prod_name):
                        if "外資" in ident:
                            d["fini_tx_chg"] = net_tr
                            if oi_val != 0: d["fini_tx_oi"] = oi_val
                        elif "投信" in ident:
                            d["sitc_tx_chg"] = net_tr
                            if oi_net_col or (oi_long_col and oi_short_col) or oi_val != 0: d["sitc_tx_oi"] = oi_val
                        elif "自營" in ident:
                            d["dealer_tx_chg"] = net_tr
                    elif "小型臺指" in prod_name or "小型台指" in prod_name:
                        if "外資" in ident:
                            d["fini_mtx_chg"] = net_tr
                            if oi_val != 0: d["fini_mtx_oi"] = oi_val
                        elif "投信" in ident:
                            d["sitc_mtx_chg"] = net_tr
                        elif "自營" in ident:
                            d["dealer_mtx_chg"] = net_tr
                d["retail_mtx_chg"] = -(d["fini_mtx_chg"] + d.get("sitc_mtx_chg", 0) + d.get("dealer_mtx_chg", 0))
                d["real_fini_oi"] = d["fini_tx_oi"] + d["sitc_tx_oi"]
    except Exception:
        pass

    try:
        q_end = now_tw.strftime("%Y/%m/%d")
        q_start = (now_tw - datetime.timedelta(days=10)).strftime("%Y/%m/%d")
        r_pcr = requests.post(
            "https://www.taifex.com.tw/cht/3/pcRatioDown",
            data={"queryStartDate": q_start, "queryEndDate": q_end},
            headers=headers, timeout=6, verify=False
        )
        if r_pcr.status_code == 200 and len(r_pcr.content) > 50:
            df_p = pd.read_csv(io.StringIO(r_pcr.content.decode("cp950", errors="ignore")))
            if not df_p.empty:
                pcr_col = next((c for c in df_p.columns if "買賣權未平倉量比率" in str(c) or "比率" in str(c)), df_p.columns[-1])
                val_p = float(str(df_p[pcr_col].iloc[-1]).replace(",", "").strip())
                if 30.0 <= val_p <= 250.0:
                    d["pc_ratio"] = round(val_p, 1)
    except Exception:
        pass

    return d


@st.cache_data(ttl=120, show_spinner=False)
def fetch_key_stocks_live_mas(rt_quotes: dict) -> dict:
    """
    【模組 5 城堡原則動態精算器 (v56.0 納入力成回測年線 249.5~253 支撐與挑戰 270 季線、光寶科 60分K 箱型突破導航)】
    """
    res = {
        "6239": {
            "p": 253.5, "ma5": 245.5, "ma10": 243.0, "ma20": 248.0, "ma60": 272.8, "ma120": 290.0, "ma240": 249.5,
            "j_val": 86.5,
            "pullback_ma240_low": 248.5, "pullback_ma240_high": 253.0,
            "b_dip_low": 232.0, "b_dip_high": 238.0,
            "c_buy_low": 215.0, "c_buy_high": 225.0,
            "b_take_low": 268.0, "b_take_high": 275.0,
            "d1_take_low": 290.0, "d1_take_high": 305.0,
            "d2_buy_low": 200.0, "d2_buy_high": 212.0
        },
        "2301": {
            "p": 232.2, "ma5": 226.5, "ma10": 225.1, "ma20": 241.4, "ma60": 273.2, "ma120": 248.6, "ma240": 195.0,
            "b_dip_low": 225.0, "b_dip_high": 230.0,
            "c_buy_low": 208.0, "c_buy_high": 218.0,
            "b_take_low": 241.4, "b_take_high": 248.6,
            "d1_take_low": 265.0, "d1_take_high": 285.0,
            "d2_buy_low": 195.0, "d2_buy_high": 208.0
        },
        "00631L": {"p": 32.54, "ma5": 30.10, "ma10": 30.20, "ma20": 33.50, "ma60": 35.10, "ma120": 34.00, "ma240": 25.00},
        "00400A": {"p": 13.00, "ma5": 12.65, "ma10": 12.68, "ma20": 12.85, "ma60": 13.05, "ma120": 12.90, "ma240": 12.20}
    }
    try:
        df_k = yf.download(["6239.TW", "2301.TW", "00631L.TW", "00400A.TW"], period="1y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
        for code_s in ["6239", "2301", "00631L", "00400A"]:
            tkr = f"{code_s}.TW"
            if not df_k.empty and tkr in df_k:
                df_sub = df_k[tkr].dropna(subset=["Close", "Low", "High"])
                c_arr = df_sub["Close"].values.astype(float)
                h_arr = df_sub["High"].values.astype(float)
                l_arr = df_sub["Low"].values.astype(float)
                if len(c_arr) >= 20:
                    rt_p = rt_quotes.get(code_s, {}).get("close", 0.0)
                    if rt_p and rt_p > 0:
                        c_arr[-1] = float(rt_p)
                    else:
                        q_rt = fetch_yahoo_v8_realtime(tkr, 2)
                        if q_rt and q_rt["close"] > 0:
                            c_arr[-1] = float(q_rt["close"])
                    curr_p = round(float(c_arr[-1]), 2)
                    res[code_s]["p"] = curr_p
                    res[code_s]["ma5"] = round(float(np.mean(c_arr[-5:])), 2)
                    res[code_s]["ma10"] = round(float(np.mean(c_arr[-10:])), 2)
                    res[code_s]["ma20"] = round(float(np.mean(c_arr[-20:])), 2)
                    res[code_s]["ma60"] = round(float(np.mean(c_arr[-60:])), 2) if len(c_arr) >= 60 else res[code_s]["ma20"]
                    res[code_s]["ma120"] = round(float(np.mean(c_arr[-120:])), 2) if len(c_arr) >= 120 else res[code_s]["ma60"]
                    res[code_s]["ma240"] = round(float(np.mean(c_arr[-240:])), 2) if len(c_arr) >= 240 else res[code_s]["ma120"]
                    _, _, j_stk, _, _ = compute_kdj(h_arr[-60:], l_arr[-60:], c_arr[-60:])
                    res[code_s]["j_val"] = j_stk

                    if code_s == "6239":
                        # 力成短線回測年線(240MA)/月線(20MA)支撐區 (約 248~253元) 與挑戰季線 (~270元)
                        ma_sup_lo = round(min(res["6239"]["ma20"], res["6239"]["ma240"], 249.5), 1)
                        ma_sup_hi = round(max(min(curr_p, 253.0), ma_sup_lo + 2.5), 1)
                        tk_lo = round(max(268.0, min(res["6239"]["ma60"] - 3.0, 270.0)), 1)
                        tk_hi = round(max(tk_lo + 5.0, res["6239"]["ma60"]), 1)
                        res["6239"].update({
                            "pullback_ma240_low": ma_sup_lo, "pullback_ma240_high": ma_sup_hi,
                            "b_dip_low": 232.0, "b_dip_high": 238.0,
                            "c_buy_low": 215.0, "c_buy_high": 225.0,
                            "b_take_low": tk_lo, "b_take_high": tk_hi,
                            "d1_take_low": round(max(288.0, res["6239"]["ma120"]), 0), "d1_take_high": 305.0,
                            "d2_buy_low": 200.0, "d2_buy_high": 212.0
                        })
                    elif code_s == "2301":
                        r_l_cands = [x for x in [res["2301"]["ma20"], res["2301"]["ma120"]] if x > curr_p * 1.01]
                        tk_l_lo = round(min(r_l_cands) if r_l_cands else 241.4, 1)
                        tk_l_hi = round(max(r_l_cands) if len(r_l_cands) >= 2 else tk_l_lo + 6.0, 1)
                        res["2301"].update({
                            "b_dip_low": round(res["2301"]["ma10"], 1), "b_dip_high": round(max(res["2301"]["ma5"], 228.0), 1),
                            "c_buy_low": 208.0, "c_buy_high": 218.0,
                            "b_take_low": tk_l_lo, "b_take_high": tk_l_hi,
                            "d1_take_low": 265.0, "d1_take_high": round(max(285.0, res["2301"]["ma60"]), 0),
                            "d2_buy_low": round(min(198.0, res["2301"]["ma240"]), 0), "d2_buy_high": 210.0
                        })
    except Exception:
        pass
    return res


def format_sector_bold_html(sec_list: list[str], is_up: bool = True) -> str:
    if not sec_list:
        return ""
    badge_bg = "rgba(255, 77, 79, 0.22)" if is_up else "rgba(0, 230, 118, 0.18)"
    badge_col = "#ff8a80" if is_up else "#69f0ae"
    icon = "🔥 美股領漲" if is_up else "❄️ 美股領跌"
    out_rows = []
    for s in sec_list:
        s_bold = re.sub(r"([\u4e00-\u9fa5A-Za-z]+\([A-Z]+\))", r"<b style='color:#ffffff;'>\1</b>", s)
        s_bold = s_bold.replace("指標股：", "<b style='color:#ffd54f;'>指標股：</b>")
        out_rows.append(
            f"<div style='margin:5px 0; padding:5px 9px; background:rgba(0,0,0,0.28); border-radius:6px; border-left:3px solid {badge_col}; font-size:14.5px; line-height:1.5;'>"
            f"<span style='background:{badge_bg}; color:{badge_col}; padding:1px 6px; border-radius:4px; font-weight:900; margin-right:6px;'>{icon}</span>"
            f"{s_bold}</div>"
        )
    return "".join(out_rows)


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
            status_tag = "🟢 站穩5MA/10MA強勢推進・正挑戰節點B月線與半年線反壓區（買支撐不追高）"
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
    trace_str = f"⚡ 評分演算軌跡：<b>原始動能 {raw_score} 分</b> ➔ <b>{' ＋ '.join(penalties)}</b> ➔ <b style='color:#ffd54f;'>最終裁決 {score} / 100 分</b>" if penalties else f"✅ 原始動能 {raw_score} 分 ➔ 最終裁決 {score} / 100 分"
    return score, status_tag, trace_str, is_capped


@st.cache_data(ttl=120, show_spinner=False)
def fetch_vix_and_panic_forecast(twii_now, ma5, ma10, ma20, ma60, ma120, ma240, m_ma5, bb_upper, twii_low, ma120_rising, ma240_rising, j_now, j_delta, k60_ma5, in_maint_pct, in_pcr, retail_mtx_chg, in_warrant_yi, est_short_cost, target_cover_1, target_cover_2):
    us_data = fetch_us_sectors_and_leaders()
    q_map = us_data["quotes"]

    vix_q = q_map.get("%5EVIX", {})
    vix_now = vix_q.get("close", 18.70)
    vix_chg_pct = vix_q.get("chg_pct", +6.92)
    vix_ma5, vix_ma20 = 22.14, 19.30
    try:
        df_vix = yf.download("^VIX", period="2mo", interval="1d", progress=False, auto_adjust=False)
        if not df_vix.empty and "Close" in df_vix:
            c_vix = df_vix["Close"].dropna().values.astype(float).flatten()
            if len(c_vix) >= 5:
                c_vix[-1] = vix_now
                vix_ma5 = round(float(np.mean(c_vix[-5:])), 2)
                vix_ma20 = round(float(np.mean(c_vix[-20:])), 2) if len(c_vix) >= 20 else vix_ma5
    except Exception:
        pass

    sox_q = q_map.get("%5ESOX", {})
    tsm_q = q_map.get("TSM", {})
    nvda_q = q_map.get("NVDA", {})
    sox_chg = sox_q.get("chg_pct", -0.54)
    tsm_chg = tsm_q.get("chg_pct", -1.90)
    nvda_chg = nvda_q.get("chg_pct", -4.34)
    tsm_ext_str = f" ({tsm_q['ext_label']})" if tsm_q.get("ext_label") else ""
    nvda_ext_str = f" ({nvda_q['ext_label']})" if nvda_q.get("ext_label") else ""

    if vix_now >= 28.0:
        vix_status, vix_color = f"🚨 極度恐慌警戒 ({vix_now:.2f} >= 28)", "#ff4d4f"
    elif vix_now >= 20.0:
        if vix_now < vix_ma5 or vix_chg_pct < 0:
            vix_status, vix_color = f"🟢 恐慌高檔回落 ({vix_now:.2f} < 5MA {vix_ma5:.2f})", "#00e676"
        else:
            vix_status, vix_color = f"🟡 避險情緒偏高 ({vix_now:.2f} > 20)", "#fbbf24"
    else:
        vix_status, vix_color = f"☀️ 風險偏好穩定 ({vix_now:.2f} < 20 常態安全區)", "#00e676"

    p_maint = np.clip((170.0 - in_maint_pct) / 40.0 * 100.0, 0.0, 100.0)
    p_pcr = np.clip((120.0 - in_pcr) / 60.0 * 100.0, 0.0, 100.0)
    p_retail = np.clip(50.0 + (-retail_mtx_chg / 100.0) + (-in_warrant_yi / 8.0), 0.0, 100.0)
    p_vix = np.clip((vix_now - 12.0) / 20.0 * 100.0, 0.0, 100.0)

    tw_panic_idx = int(round(0.35 * p_maint + 0.25 * p_pcr + 0.20 * p_retail + 0.20 * p_vix))
    if tw_panic_idx >= 70:
        panic_label, panic_color = f"😨 極度恐慌清洗區 ({tw_panic_idx}/100) ➔ 散戶斷頭認輸、強烈反彈訊號", "#00e676"
    elif tw_panic_idx >= 55:
        panic_label, panic_color = f"🟡 恐慌釋放轉折區 ({tw_panic_idx}/100) ➔ 浮額降溫有利多方反攻", "#ffd54f"
    elif tw_panic_idx >= 35:
        panic_label, panic_color = f"☀️ 情緒中性常態區 ({tw_panic_idx}/100) ➔ 籌碼多空平衡、選股操作", "#e5e7eb"
    else:
        panic_label, panic_color = f"🔥 散戶極度貪婪過熱 ({tw_panic_idx}/100) ➔ 居高思危、留意追高風險", "#ff4d4f"

    long_trend = "📈 【長線多頭保護傘未破】(240MA年線與120MA半年線雙雙上揚助漲)" if (twii_now >= ma240 and ma240_rising) else "🚨 【長線跌破年線轉空】"
    mid_trend = "📈 【中線多頭排列】" if twii_now >= ma60 else ("🧭 【中線收復半年線・挑戰季線】" if twii_now >= ma120 else "📉 【中線處於月/季線下大三角收斂修正波】(半年線僅為輕警戒)")
    if twii_now >= ma10 and twii_now >= ma5:
        short_trend = f"🔥 【短線守穩 5MA({ma5:,.0f}) 與 10MA({ma10:,.0f}) 強勢推進】(正挑戰月線 {ma20:,.0f} 與半年線 {ma120:,.0f} 節點B反壓區！)"
    elif twii_now >= ma5:
        short_trend = "🔥 【短線進行 b浪回測日5MA支撐】(守穩5MA後預計展開 c浪攻節點B！)"
    else:
        short_trend = "⚠️ 【短線回測 5MA 下方尋求支撐】"

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
        "us_session_tag": us_data["session_tag"],
        "sox_chg": sox_chg, "tsm_chg": tsm_chg, "nvda_chg": nvda_chg,
        "tsm_ext_str": tsm_ext_str, "nvda_ext_str": nvda_ext_str,
        "nq_chg": us_data["nq_chg"],
        "top_up_sectors": us_data["top_up_sectors"],
        "bot_dn_sectors": us_data["bot_dn_sectors"],
        "tw_panic_idx": tw_panic_idx, "panic_label": panic_label, "panic_color": panic_color,
        "long_trend": long_trend, "mid_trend": mid_trend, "short_trend": short_trend,
        "up_t1": (up_t1_low, up_t1_high), "up_t2": (up_t2_low, up_t2_high), "up_t3": (up_t3_low, up_t3_high),
        "dn_s1": (dn_s1_low, dn_s1_high), "dn_s2": (dn_s2_low, dn_s2_high), "dn_s3": dn_s3_val
    }


def pin_code_name_index(df):
    if df.empty:
        return df
    df_c = df.copy()
    if "代碼" in df_c.columns:
        is_core = df_c["代碼"].astype(str).isin(CORE_WATCHLIST.keys())
        if "短線屬性" in df_c.columns:
            df_c.loc[is_core & df_c["短線屬性"].astype(str).str.contains("純技術"), "短線屬性"] = "🏦核心權值(穩健護城河)"
        for diag_col in ["名將戰法診斷", "趨勢狀態"]:
            if diag_col in df_c.columns:
                df_c.loc[is_core, diag_col] = df_c.loc[is_core, diag_col].astype(str).str.replace("🎭純技術炒作(不看基本面)", "🏦核心權值(穩健護城河)", regex=False)
    name_col = "股名" if "股名" in df_c.columns else ("公司名稱" if "公司名稱" in df_c.columns else None)
    if "代碼" in df_c.columns and name_col:
        return df_c.set_index(["代碼", name_col])
    return df_c


def build_dynamic_triangle_blueprint_html(twii_now, twii_high, ma5, ma10, ma20, ma60, ma120, ma240, j_now, k60_j, k_mas):
    """
    【v56.0 三層物理分離式 SVG 架構 ＋ 11/13 正2@32.57已獲利出清 ＋ 力成回測年線(249.5~253)後挑戰270季線 ＋ 光寶科60分K箱型突破導航】
    """
    m6239 = k_mas["6239"]
    m2301 = k_mas["2301"]
    p_6239 = m6239["p"]
    p_2301 = m2301["p"]
    p_2x, m5_2x = k_mas["00631L"]["p"], k_mas["00631L"]["ma5"]
    p_00400a = k_mas["00400A"]["p"]

    pti_ma240_sup = f"{m6239['pullback_ma240_low']:.1f}~{m6239['pullback_ma240_high']:.1f}"
    pti_b_dip = f"{m6239['b_dip_low']:.0f}~{m6239['b_dip_high']:.0f}"
    pti_c_buy = f"{m6239['c_buy_low']:.0f}~{m6239['c_buy_high']:.0f}"
    pti_b_take = f"{m6239['b_take_low']:.1f}~{m6239['b_take_high']:.1f}"
    pti_d1_take = f"{m6239['d1_take_low']:.0f}~{m6239['d1_take_high']:.0f}"
    pti_d2_buy = f"{m6239['d2_buy_low']:.0f}~{m6239['d2_buy_high']:.0f}"

    lite_c_buy = f"{m2301['c_buy_low']:.0f}~{m2301['c_buy_high']:.0f}"
    lite_b_take = f"{m2301['b_take_low']:.1f}~{m2301['b_take_high']:.1f}"
    lite_d1_take = f"{m2301['d1_take_low']:.0f}~{m2301['d1_take_high']:.0f}"
    lite_d2_buy = f"{m2301['d2_buy_low']:.0f}~{m2301['d2_buy_high']:.0f}"

    if twii_now >= 43600:
        curr_x, curr_y = 465, 162
        badge_x, badge_y = 465, 222
        curr_stage_title = f"📍 當前位置：{twii_now:,.0f}點（11/13 已將最後1張正2@32.57獲利出清✅ ➔ 正挑戰【節點B：{ma20:,.0f}～{ma120:,.0f}點】月/半年線反壓區！）"
        active_stage = 1
    elif twii_now >= 41800:
        curr_x, curr_y = 390, 232
        badge_x, badge_y = 390, 175
        curr_stage_title = f"📍 當前位置：{twii_now:,.0f}點（處於【11月中 b浪回測5MA {ma5:,.0f}點】良性換手買點區！）"
        active_stage = 1
    else:
        curr_x, curr_y = 665, 222
        badge_x, badge_y = 665, 165
        curr_stage_title = f"📍 當前位置：{twii_now:,.0f}點（回測大三角下軌【節點C：41,500～42,000點】右腳黃金買點區！）"
        active_stage = 2

    svg_xml = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 430" width="100%" height="auto">
<rect width="1200" height="430" rx="12" fill="#09111a" stroke="#334155" stroke-width="2"/>
<rect x="25" y="100" width="1150" height="212" rx="8" fill="#0f172a" stroke="#1e293b" stroke-width="1"/>
<line x1="25" y1="150" x2="1175" y2="150" stroke="#1e293b" stroke-width="1" stroke-dasharray="4,4"/>
<line x1="25" y1="205" x2="1175" y2="205" stroke="#1e293b" stroke-width="1" stroke-dasharray="4,4"/>
<line x1="25" y1="260" x2="1175" y2="260" stroke="#1e293b" stroke-width="1" stroke-dasharray="4,4"/>

<line x1="85" y1="84" x2="85" y2="112" stroke="#f97316" stroke-width="1.5" stroke-dasharray="3,3"/>
<line x1="285" y1="84" x2="285" y2="162" stroke="#00e676" stroke-width="1.5" stroke-dasharray="3,3"/>
<line x1="500" y1="84" x2="500" y2="142" stroke="#f59e0b" stroke-width="1.5" stroke-dasharray="3,3"/>
<line x1="780" y1="84" x2="780" y2="178" stroke="#f97316" stroke-width="1.5" stroke-dasharray="3,3"/>

<line x1="165" y1="280" x2="165" y2="326" stroke="#00e676" stroke-width="1.5" stroke-dasharray="3,3"/>
<line x1="390" y1="232" x2="390" y2="326" stroke="#38bdf8" stroke-width="1.5" stroke-dasharray="3,3"/>
<line x1="665" y1="222" x2="665" y2="326" stroke="#10b981" stroke-width="1.5" stroke-dasharray="3,3"/>
<line x1="910" y1="285" x2="910" y2="326" stroke="#10b981" stroke-width="1.5" stroke-dasharray="3,3"/>

<line x1="85" y1="112" x2="915" y2="192" stroke="#cbd5e1" stroke-width="2.5" stroke-dasharray="9,6"/>
<line x1="165" y1="280" x2="915" y2="202" stroke="#cbd5e1" stroke-width="2.5" stroke-dasharray="9,6"/>

<line x1="935" y1="190" x2="1160" y2="190" stroke="#38bdf8" stroke-width="2.5"/>
<line x1="935" y1="255" x2="1160" y2="255" stroke="#38bdf8" stroke-width="2.5"/>

<polyline points="85,112 165,280 285,162 390,232 500,142 665,222 780,178 910,285 965,198 1015,245 1065,196 1115,245 1155,185" fill="none" stroke="#f59e0b" stroke-width="4.5" stroke-linejoin="round"/>
<polyline points="85,112 165,280 500,142 665,222 780,178 910,285" fill="none" stroke="#ef4444" stroke-width="1.8" stroke-opacity="0.65"/>

<circle cx="85" cy="112" r="7" fill="#f97316" stroke="#fff" stroke-width="2"/>
<circle cx="165" cy="280" r="7" fill="#00e676" stroke="#fff" stroke-width="2"/>
<circle cx="285" cy="162" r="7" fill="#00e676" stroke="#fff" stroke-width="2"/>
<circle cx="390" cy="232" r="7" fill="#38bdf8" stroke="#fff" stroke-width="2"/>
<circle cx="500" cy="142" r="8" fill="#f59e0b" stroke="#fff" stroke-width="2"/>
<circle cx="665" cy="222" r="8" fill="#10b981" stroke="#fff" stroke-width="2"/>
<circle cx="780" cy="178" r="7" fill="#f97316" stroke="#fff" stroke-width="2"/>
<circle cx="910" cy="285" r="8" fill="#10b981" stroke="#fff" stroke-width="2"/>

<line x1="{curr_x}" y1="{curr_y}" x2="{badge_x}" y2="{badge_y}" stroke="#00e676" stroke-width="2.5"/>
<circle cx="{curr_x}" cy="{curr_y}" r="15" fill="#00e676" fill-opacity="0.32"/>
<circle cx="{curr_x}" cy="{curr_y}" r="8" fill="#00e676" stroke="#ffffff" stroke-width="3"/>
<rect x="{badge_x - 88}" y="{badge_y - 16}" width="176" height="30" rx="7" fill="#00e676" stroke="#09111a" stroke-width="2.5"/>
<text x="{badge_x}" y="{badge_y + 5}" fill="#09111a" font-family="sans-serif" font-size="14.5" font-weight="900" text-anchor="middle">📍現價 {twii_now:,.0f}點</text>

<rect x="20" y="14" width="145" height="68" rx="7" fill="#16222f" stroke="#475569" stroke-width="1.5"/>
<text x="92" y="38" fill="#e2e8f0" font-family="sans-serif" font-size="13" font-weight="bold" text-anchor="middle">2026/10初高點</text>
<text x="92" y="64" fill="#fcd34d" font-family="sans-serif" font-size="15" font-weight="900" text-anchor="middle">49,968點</text>

<rect x="180" y="14" width="215" height="68" rx="7" fill="#112a26" stroke="#00e676" stroke-width="1.5"/>
<text x="287" y="36" fill="#a7f3d0" font-family="sans-serif" font-size="13" font-weight="bold" text-anchor="middle">✅11/12~13 (a/c浪強彈)</text>
<text x="287" y="56" fill="#00e676" font-family="sans-serif" font-size="14" font-weight="900" text-anchor="middle">44,000～{twii_high:,.0f}點</text>
<text x="287" y="74" fill="#6ee7b7" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">(廣達@291.5/正2@32.57全出清)</text>

<rect x="410" y="14" width="250" height="68" rx="7" fill="#2d1b19" stroke="#f59e0b" stroke-width="2"/>
<text x="535" y="35" fill="#ffffff" font-family="sans-serif" font-size="13.5" font-weight="900" text-anchor="middle">🎯節點B：2026/11底 (c浪目標)</text>
<text x="535" y="55" fill="#fcd34d" font-family="sans-serif" font-size="14.5" font-weight="900" text-anchor="middle">44,200～{ma120:,.0f}點</text>
<text x="535" y="74" fill="#fda4af" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">(力成攻270季線/光寶科攻{lite_b_take})</text>

<rect x="675" y="14" width="225" height="68" rx="7" fill="#16222f" stroke="#f97316" stroke-width="1.5"/>
<text x="787" y="38" fill="#e2e8f0" font-family="sans-serif" font-size="13" font-weight="bold" text-anchor="middle">⏳節點D1：2027/3底 (Q1高點)</text>
<text x="787" y="64" fill="#fcd34d" font-family="sans-serif" font-size="14.5" font-weight="900" text-anchor="middle">44,000點 (壓線逃命頂)</text>

<rect x="920" y="14" width="255" height="68" rx="7" fill="#132338" stroke="#38bdf8" stroke-width="1.5"/>
<text x="1047" y="38" fill="#93c5fd" font-family="sans-serif" font-size="13" font-weight="bold" text-anchor="middle">2027/4 ～ 2027/9 箱型期</text>
<text x="1047" y="64" fill="#fcd34d" font-family="sans-serif" font-size="14.5" font-weight="900" text-anchor="middle">40,000～43,000點大箱型盤整</text>

<rect x="20" y="328" width="245" height="84" rx="7" fill="#112a26" stroke="#00e676" stroke-width="1.5"/>
<text x="142" y="352" fill="#e2e8f0" font-family="sans-serif" font-size="13.5" font-weight="bold" text-anchor="middle">✅節點A：2026/11初 (已完成)</text>
<text x="142" y="376" fill="#00e676" font-family="sans-serif" font-size="14.5" font-weight="900" text-anchor="middle">37,837點 (恐慌左腳錨點)</text>
<text x="142" y="398" fill="#6ee7b7" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">刺穿年線爆量收腳確立底限</text>

<rect x="280" y="328" width="260" height="84" rx="7" fill="#13283c" stroke="#38bdf8" stroke-width="2.5"/>
<text x="410" y="352" fill="#93c5fd" font-family="sans-serif" font-size="13.5" font-weight="900" text-anchor="middle">🔥11月中 (b浪換手・力成回測年線)</text>
<text x="410" y="376" fill="#fcd34d" font-family="sans-serif" font-size="14.5" font-weight="900" text-anchor="middle">大盤守5MA {ma5:,.0f}點 (正2已出清✅)</text>
<text x="410" y="398" fill="#00e676" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">力成回測年線({pti_ma240_sup})守穩再攻270</text>

<rect x="555" y="328" width="275" height="84" rx="7" fill="#112a26" stroke="#10b981" stroke-width="1.5"/>
<text x="692" y="352" fill="#e2e8f0" font-family="sans-serif" font-size="13.5" font-weight="bold" text-anchor="middle">⏳節點C：12月中～1月中 (右腳底)</text>
<text x="692" y="376" fill="#6ee7b7" font-family="sans-serif" font-size="14.5" font-weight="900" text-anchor="middle">41,500～42,000點 (黃金買點)</text>
<text x="692" y="398" fill="#fcd34d" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">買力成({pti_c_buy})/光寶科({lite_c_buy})/00400A</text>

<rect x="845" y="328" width="335" height="84" rx="7" fill="#16222f" stroke="#10b981" stroke-width="1.5"/>
<text x="1012" y="352" fill="#e2e8f0" font-family="sans-serif" font-size="13.5" font-weight="bold" text-anchor="middle">💎節點D2：2027/4初～5月 (C波終極底)</text>
<text x="1012" y="376" fill="#6ee7b7" font-family="sans-serif" font-size="14.5" font-weight="900" text-anchor="middle">40,000～41,000點 (年線{ma240:,.0f}鐵板)</text>
<text x="1012" y="398" fill="#fcd34d" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">月MACD翻綠洗盤底・00400A重錘滿倉繁殖</text>
</svg>"""
    b64_svg = base64.b64encode(svg_xml.encode("utf-8")).decode("utf-8")

    bdr_s1 = "3px solid #ffd54f; box-shadow: 0 0 14px rgba(255,213,79,0.65)" if active_stage == 1 else "1px solid rgba(255,255,255,0.22)"
    bdr_s2 = "3px solid #00e676; box-shadow: 0 0 14px rgba(0,230,118,0.65)" if active_stage == 2 else "1px solid rgba(255,255,255,0.22)"

    raw_card = f"""
<div style="background: linear-gradient(180deg, #0d1520 0%, #16222f 100%); border: 2px solid #eab308; border-radius: 14px; padding: 18px 20px; margin-bottom: 18px; color: #fff; box-shadow: 0 8px 24px rgba(0,0,0,0.35);">
<div style="text-align:center; font-size:23px; font-weight:900; color:#fcd34d; letter-spacing:1px; border-bottom:1px solid rgba(252,211,77,0.3); padding-bottom:8px; margin-bottom:10px;">
🗺️ 加權指數 2026/11 ~ 2027/09 大三角收斂與雙主軸終極作戰藍圖（盤中/收盤動態即時定位）
</div>
<div style="text-align:center; font-size:15.5px; font-weight:800; color:#00e676; background:rgba(0,230,118,0.12); padding:8px 12px; border-radius:8px; margin-bottom:12px; border:1px solid rgba(0,230,118,0.4); line-height:1.55;">
{curr_stage_title}<br>
📡 <b>最新核心持倉與技術導航</b>：
<b>力成(6239) = {p_6239:.1f}元</b> (持1張@253｜<span style="color:#ffd54f;">日J={m6239['j_val']:.0f}偏高，短線觀察回測年線/月線 {pti_ma240_sup}元 換手守穩再攻季線 {pti_b_take}元！</span>) &nbsp;｜&nbsp;
<b>光寶科(2301) = {p_2301:.1f}元</b> (持1張@216｜<span style="color:#ffd54f;">60分K於 228~234.5元 箱型盤整待下週月線扣低突破攻 {lite_b_take}元！</span>) &nbsp;｜&nbsp;
<b>0050正2 = {p_2x:.2f}元</b> (<span style="color:#00e676;">11/13已於@32.57全數獲利出清✅ 空手等回測5MA {m5_2x:.2f}元</span>) &nbsp;｜&nbsp;
<b>00400A = {p_00400a:.2f}元</b> (持31張｜<span style="color:#ffd54f;">等拉回 12.40~12.60元 買區</span>)
</div>
<img src="data:image/svg+xml;base64,{b64_svg}" style="width:100%; height:auto; display:block; border-radius:10px; margin-bottom:14px;" />
<div style="font-size:17px; font-weight:900; color:#fcd34d; margin: 6px 0 8px 0;">
一、【FOPLP扇形封裝(力成6239) ＆ 800VDC雲端電源(光寶科2301) ＋ 正2波段階段】（廣達@291.5、正2@32.57 均已獲利出清✅）
</div>
<div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:10px; margin-bottom:14px;">
<div style="background:linear-gradient(135deg,#7f1d1d,#991b1b); padding:10px 12px; border-radius:8px; border:{bdr_s1}; font-size:14px; line-height:1.5;">
<b style="color:#fef08a; font-size:15px;">🔥 11月中~11月底 (換手蓄勢 ➔ 攻節點B)</b><br>
• <b>0050正2</b>：11/12接@30.99 ➔ <b>11/13早盤@32.57已獲利出清✅</b><br>
• <b>力成(6239)</b>：短線J值高，<b>回測年線/月線({pti_ma240_sup}元)守穩再向上挑戰季線 {pti_b_take}元</b> (破線則等 {pti_b_dip}元 低接)<br>
• <b>光寶科(2301)</b>：<b>60分K於 228~234.5元 盤整</b>，守日10MA({m2301['ma10']:.1f})待下週突破攻 <b>{lite_b_take}元</b>！
</div>
<div style="background:linear-gradient(135deg,#1e3a8a,#1d4ed8); padding:10px 12px; border-radius:8px; border:{bdr_s2}; font-size:14px; line-height:1.5;">
<b style="color:#fef08a; font-size:15px;">⏳ 節點C：12月中~1月中右腳 (41,500~42,000)</b><br>
• <b>雙龍頭波段黃金買點！</b><br>
• <b>力成(6239)建議進場價</b>：回測前波起漲底 <b>{pti_c_buy}元</b> 買進第 2、3 張重裝倉 (迎接2027 H1 FOPLP試產)<br>
• <b>光寶科(2301)建議進場價</b>：回測 <b>{lite_c_buy}元</b> 買進第 2、3 張
</div>
<div style="background:linear-gradient(135deg,#78350f,#b45309); padding:10px 12px; border-radius:8px; border:1px solid rgba(255,255,255,0.22); font-size:14px; line-height:1.5;">
<b style="color:#fef08a; font-size:15px;">🎯 節點D1：2027/3底 (44,000點 Q1逃命頂)</b><br>
• 趁 Q1 扇形封裝(FOPLP)與 800VDC 作夢行情：<br>
• <b>力成(6239)衝半年線與前高 {pti_d1_take}元 獲利了結！</b><br>
• <b>光寶科(2301)衝季線與前高 {lite_d1_take}元 賣出2張鎖利！</b><br>
• 全面收回現金，避開 4~5月 C 波下殺！
</div>
<div style="background:linear-gradient(135deg,#713f12,#a16207); padding:10px 12px; border-radius:8px; border:1px solid rgba(255,255,255,0.22); font-size:14px; line-height:1.5;">
<b style="color:#fef08a; font-size:15px;">💎 節點D2：2027/4初~5月 (40,000~41,000底)</b><br>
• 月MACD翻綠＋報稅季回測年線({ma240:,.0f}) C波底<br>
• <b>力成於 {pti_d2_buy}元、光寶科於 {lite_d2_buy}元 長線低接</b><br>
• 或將波段獲利全數轉入 00400A 長線收息！
</div>
</div>
<div style="font-size:17px; font-weight:900; color:#fcd34d; margin: 6px 0 8px 0;">
二、【00400A 存股建倉與繁殖階段】（目前持有 31張 @14.40 ｜ 現價 <span style="color:#ffd54f;">{p_00400a:.2f}元</span>，靜待拉回 12.60元 以下買區）
</div>
<div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap:8px;">
<div style="background:#0f4c75; padding:9px 10px; border-radius:8px; border:{bdr_s1}; font-size:13.5px; line-height:1.45;">
<b style="color:#00e676;">🔥第一階(11月中b浪拉回4.2~4.3萬)</b><br>
<b>建議進場價：12.40~12.60元</b> (現價{p_00400a:.2f}不追高，拉回買3~4張)
</div>
<div style="background:#0284c7; padding:9px 10px; border-radius:8px; border:1px solid rgba(255,255,255,0.22); font-size:13.5px; line-height:1.45;">
<b style="color:#fef08a;">⏳節點B(11月底44,200~{ma120:,.0f}點)</b><br>
<b>12.8~13.1元 暫停追高</b>，握緊剛出清廣達/正2之現金！
</div>
<div style="background:#0369a1; padding:9px 10px; border-radius:8px; border:{bdr_s2}; font-size:13.5px; line-height:1.45;">
<b style="color:#6ee7b7;">⏳第二階・節點C(12月中~1月中右腳)</b><br>
<b>建議進場價：12.15~12.45元</b> 投40%資金買 6~7張 (推升至41張)
</div>
<div style="background:#1e3a8a; padding:9px 10px; border-radius:8px; border:1px solid rgba(255,255,255,0.22); font-size:13.5px; line-height:1.45;">
<b style="color:#ffd54f;">🎯節點D1(2027/2~3月底44,000點)</b><br>
<b>12.6~13.2元 暫停單筆加碼</b>，僅用月配息0.12元滾零股
</div>
<div style="background:#334155; padding:9px 10px; border-radius:8px; border:1px solid rgba(255,255,255,0.22); font-size:13.5px; line-height:1.45;">
<b style="color:#6ee7b7;">💎第三階・節點D2(2027/4~5月終極底)</b><br>
<b>建議進場價：11.80~12.10元</b> 重錘買8~12張，破50張自體繁殖！
</div>
</div>
</div>
"""
    return clean_html(raw_card)


# ==========================================
# Streamlit 戰情室主介面 (v56.0 台股當日熱門爆量族群雷達 ＋ 11/13 最新持倉導航版)
# ==========================================
now_str = get_tw_now().strftime("%Y-%m-%d %H:%M:%S")
st.markdown("## 🧭 台股每日戰情室 v56.0 (台股當日熱門爆量族群雷達 ＋ 大三角藍圖 ＋ 力成6239/光寶科2301 技術導航版)")

btn_col1, btn_col2, btn_col3 = st.columns([2, 2, 3])
if btn_col1.button("⚡ 秒速刷新盤中即時報價、熱門族群量增比與藍圖定位", use_container_width=True):
    fetch_mis_realtime_batch.clear()
    fetch_us_sectors_and_leaders.clear()
    fetch_vix_and_panic_forecast.clear()
    fetch_key_stocks_live_mas.clear()
if btn_col2.button("🔄 立即重抓 14:00 後期交所、三大法人與最新月營收", use_container_width=True):
    st.cache_data.clear()
auto_refresh = btn_col3.checkbox("⏱️ 開啟盤中每 20 秒自動刷新即時報價", value=False)

with st.spinner("📡 正在同步台股當日熱門爆量族群(預估量vs昨日量)、三層零重疊作戰藍圖、力成/光寶科技術導航與 EPS SOP v2.0..."):
    df_launch, df_pullback, df_orbit, df_long_bc, df_long_turn, vol_note, rt_quotes = scan_all_stocks()
    fleet = fetch_and_analyze_fleet(rt_quotes)
    price_dict_all, tdcc_dict_all, _, _ = fetch_official_market_data()
    df_all_rev = fetch_all_monthly_revenue_df(price_dict_all)
    if not df_all_rev.empty:
        df_all_rev["400張大戶%"] = df_all_rev["代碼"].map(lambda c: f"{tdcc_dict_all[c]:.1f}%" if c in tdcc_dict_all else "未揭露")
        df_all_rev["_tdcc_num"] = df_all_rev["代碼"].map(lambda c: float(tdcc_dict_all.get(c, 0.0)))

# 計算台股當日熱門爆量族群與領頭羊指標股
tw_hot_sec_info = compute_tw_hot_sectors(st.session_state.get("scanned_sector_records", []))

deriv = refresh_live_taifex_oi_and_pcr(fleet["deriv"])
k_mas = fetch_key_stocks_live_mas(rt_quotes)
m6239, m2301 = k_mas["6239"], k_mas["2301"]
p_6239, p_2301, p_2x, p_00400a = m6239["p"], m2301["p"], k_mas["00631L"]["p"], k_mas["00400A"]["p"]

pti_ma240_sup_s = f"{m6239['pullback_ma240_low']:.1f}~{m6239['pullback_ma240_high']:.1f}"
pti_b_dip_s = f"{m6239['b_dip_low']:.0f}~{m6239['b_dip_high']:.0f}"
pti_c_buy_s = f"{m6239['c_buy_low']:.0f}~{m6239['c_buy_high']:.0f}"
pti_b_take_s = f"{m6239['b_take_low']:.1f}~{m6239['b_take_high']:.1f}"
liteon_res_s = f"{m2301['b_take_low']:.1f}~{m2301['b_take_high']:.1f}"
liteon_c_buy_s = f"{m2301['c_buy_low']:.0f}~{m2301['c_buy_high']:.0f}"

in_fini_oi, in_fini_chg, in_sitc_oi = deriv["fini_tx_oi"], deriv["fini_tx_chg"], deriv["sitc_tx_oi"]
sitc_tx_chg_v, fini_mtx_chg_v, retail_mtx_chg_v = deriv.get("sitc_tx_chg", -2969), deriv.get("fini_mtx_chg", 206), deriv.get("retail_mtx_chg", 379)
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

st.caption(f"🕒 台灣時間：{now_str} ｜ 報價：{fleet['quote_src']} ｜ {vol_note} ｜ {tf['us_session_tag']} ｜ 台幣：{fx_now:.3f} ({fx_cents_chg:+.1f}分)")

s1_maint = 25 if in_maint_pct <= 135.0 else (22 if in_maint_pct <= 145.0 else (16 if in_maint_pct <= 152.0 else (12 if in_maint_pct <= 165.0 else 5)))
s1_concl = f"📌 結論：底部斷頭清洗完成、回升至常態區 ({in_maint_pct:.1f}%)" if 145.0 < in_maint_pct <= 165.0 else (f"📌 結論：進入落底甜美區 ({in_maint_pct:.1f}%)" if in_maint_pct <= 145.0 else f"📌 結論：融資水位偏高 ({in_maint_pct:.1f}%)")

if in_fini_chg >= 4500 or fini_mtx_chg_v >= 4500:
    s2_fut = 22 if in_fini_chg < 0 else 25
    s2_concl = f"📌 結論：外資期貨趁拉回大舉回補多單！(大台{in_fini_chg:+,}口 / 小台{fini_mtx_chg_v:+,}口)"
elif sitc_tx_chg_v >= 2500 and retail_mtx_chg_v < 0:
    s2_fut = 18
    s2_concl = f"📌 結論：投信大台重倉撐盤 ({sitc_tx_chg_v:+,}口) ＋ 散戶小台認輸被洗出 ({retail_mtx_chg_v:+,}口)"
else:
    s2_fut = 14
    s2_concl = f"📌 結論：土洋期貨多空換手 (大台外資{in_fini_chg:+,}口 vs 投信{sitc_tx_chg_v:+,}口)"

s3_pcr = 12 if (in_pcr <= 75.0 or in_pcr >= 100.0) else (10 if in_pcr >= 82.0 else 8)
s3_war = 8 if in_warrant_yi >= 3.0 else (7 if in_warrant_yi <= -35.0 else 4)
s3_opt_war = s3_pcr + s3_war
s3_concl = f"📌 結論：選擇權莊家支撐轉強 (PCR {in_pcr:.1f}%) ＋ 認購權證大戶點火 ({in_warrant_yi:+.2f}億)" if in_warrant_yi > 0 else f"📌 結論：PCR {in_pcr:.1f}% 支撐穩 ＋ 認購權證停損宣洩 ({in_warrant_yi:+.2f}億・洗浮額)"

if (k60_k >= k60_d) and (twii_now >= fleet["k60_ma5"]):
    s4_k60 = 15 if is_above_ma5 else 13
    s4_concl = f"📌 結論：短線右側多方控盤確立，惟 60分J={k60_j:.1f} 達破百超買區" if k60_j >= 95 else f"📌 結論：已突破 60分5MA 與 日5MA！微觀右側轉攻確立"
elif twii_now >= fleet["k60_ma5"] and is_above_ma5:
    s4_k60 = 13
    s4_concl = f"📌 結論：站穩 60分5MA 與 日5MA 之上，短多結構穩健"
else:
    s4_k60 = 10 if is_above_ma5 else 0
    s4_concl = f"📌 結論：60分K跌破60分5MA({fleet['k60_ma5']:,.0f})展開 b浪換手，60分J={k60_j:.1f}"

s5_space = 15 if (twii_now >= twii_ma10 and is_above_ma5) else (12 if (is_above_ma5 and j_delta > 0) else 8)
s5_concl = f"📌 結論：守穩 5MA({twii_ma5:,.0f}) 與 10MA({twii_ma10:,.0f}) 強勢推進，惟日J={j_now:.1f}偏高勿追高" if j_now >= 90 else f"📌 結論：守穩 5MA({twii_ma5:,.0f}) 與 10MA({twii_ma10:,.0f}) 之上良性推進"

raw_score_5d = s1_maint + s2_fut + s3_opt_war + s4_k60 + s5_space
score_5d, grade_str, circuit_trace_str, is_circuit_capped = apply_macro_circuit_breaker(
    raw_score_5d, twii_now, twii_ma60, twii_ma120, twii_ma240, ma120_is_rising,
    in_pcr, in_maint_pct, fx_now, fx_ma5, fx_chg_ntd, above_ma5=is_above_ma5
)

fx_score_adj_str = "+5分 (升破5MA獎勵)" if (fx_now <= fx_ma5 and fx_chg_ntd < 0) else ("-5分 (貶破5MA扣分)" if (fx_now > fx_ma5 and fx_chg_ntd >= 0.04) else "0分 (守5MA內中性)")
fx_cmp_sym = "<=" if fx_now <= fx_ma5 else ">"
if fx_now <= fx_ma5 and fx_chg_ntd < 0:
    fx_concl = f"📌 結論：台幣止貶轉升破5MA ({fx_now:.3f} <= {fx_ma5:.3f}) ➔ 外資熱錢回流！"
elif fx_now <= fx_ma5:
    fx_concl = f"📌 結論：台幣守穩 5MA 轉折線內 ({fx_now:.3f} <= {fx_ma5:.3f}) ➔ 資金面中性偏穩"
else:
    fx_concl = f"📌 結論：台幣貶破 5MA ({fx_now:.3f} > {fx_ma5:.3f}) ➔ 短線外資匯出引發震盪"

if twii_now >= twii_ma20 and score_5d >= 70:
    water_level_pct, water_level_note = "70% ～ 80%", "月線上多頭推進・積極順勢操作"
elif is_above_ma5 and score_5d >= 60:
    water_level_pct, water_level_note = "50% ～ 60%", f"11/13已將最後1張正2@32.57獲利出清✅ 力成(6239)待回測年線{pti_ma240_sup_s}元守穩攻270季線，光寶科(2301)待60分K盤整突破攻{liteon_res_s}元！"
else:
    water_level_pct, water_level_note = "30% ～ 40%", "回測支撐防禦・分批低接力成(6239)、光寶科(2301)與00400A"

if not df_launch.empty and "短線屬性" in df_launch.columns:
    df_launch_fund = df_launch[df_launch["短線屬性"].str.contains("有基之彈|核心權值", na=False)].copy()
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
        f"昨量比={r.get('昨日量比', '-')}x/5MA量比={r.get('5MA量比', '-')}(預估{r.get('預估量(張)', '-')}張) | "
        f"技術:J={r.get('日J值', '-')}/{r.get('DMI多空', '-')}/ADX={r.get('ADX強度', '-')} | "
        f"城堡點位:[天花板{r.get('天花板(反壓)', '-')}/月線{r.get('月線承接', '-')}/鋼鐵防線{r.get('鋼鐵防線', '-')}/停損{r.get('極限停損', '-')}] | "
        f"400張大戶={r.get('400張大戶%', '-')} | "
        f"營收:[單月YoY={r.get('單月營收YoY', '-')}/累計年YoY={r.get('累計年營收YoY', '-')}] | "
        f"EPS估值(SOPv2.0):[預估EPS={eps_s}/PE={pe_s}/💎合理切入={ent_s}/🎯目標={tgt_s}/{r.get('估值診斷', '-')}]"
    )

def build_complete_ai_snapshot():
    up_sec_clean = re.sub(r"<[^>]+>", "", " ； ".join(tf["top_up_sectors"]))
    dn_sec_clean = re.sub(r"<[^>]+>", "", " ； ".join(tf["bot_dn_sectors"]))
    lines = [
        f"【⚡ 淘金客 v23.0 雙核 20 模組強制聯網深度健檢指令】（{now_str}）：請針對以下戰情室總結數據包，優先自主聯網查證美股/台指夜盤、重點個股之主力分點籌碼、資券借變化、CB可轉債轉換價與最新法說會催化劑，並結合『大三角收斂四大波浪節點(A~D2)』與『淘金客 EPS 預估 SOP v2.0（時序收斂＋三率乘數T）』給出具體買賣點位與實戰行動指令！",
        f"1. [大盤指數、多空方向與總評分]：大盤現價={twii_now:,.1f}點 (今日{fleet['twii_chg_pct']:+.2f}% | 開盤{twii_open:,.0f}/高{fleet['twii_high']:,.0f}/低{fleet['twii_low']:,.0f}/昨收{twii_prev_c:,.0f}) | 大盤多空總評分={score_5d}/100分 ({grade_str}) | 股市水庫建議水位={water_level_pct}({water_level_note}) | 核心持倉與技術導航：力成(6239)={p_6239:.1f}元(持1張@253，日J={m6239['j_val']:.0f}偏高，短線觀察回測年線/月線{pti_ma240_sup_s}元換手有守再攻季線{pti_b_take_s}元，節點C黃金買點{pti_c_buy_s}元)、光寶科(2301)={p_2301:.1f}元(持1張@216，60分K於228~234.5元箱型盤整守10MA {m2301['ma10']:.1f}元，待下週月線扣低突破攻{liteon_res_s}元，節點C加碼{liteon_c_buy_s}元)、0050正2={p_2x:.2f}元(11/13已於@32.57獲利出清✅目前0張空手，待回測5MA {k_mas['00631L']['ma5']:.2f}元再評估)、00400A={p_00400a:.2f}元(持31張@14.4，第一階進場價12.40~12.60元)",
        f"2. [🔥台股當日熱門爆量領漲族群(預估量vs昨日量)]：熱門領漲族群={tw_hot_sec_info['hot_text']} | 量縮/調節族群={tw_hot_sec_info['cold_text']}",
        f"3. [八大評估指標明細與個別評分]："
        f"(1)純個股融資維持率={in_maint_pct:.1f}% [得分:{s1_maint}/25分] | "
        f"(2)大小台期貨：大台外資={in_fini_chg:+,}口(未平倉{in_fini_oi:+,})/大台投信={sitc_tx_chg_v:+,}口(未平倉{in_sitc_oi:+,})/大台實質空單={real_directional_fini_oi:+,}口、小台外資={fini_mtx_chg_v:+,}口/小台散戶={retail_mtx_chg_v:+,}口、外資佈空成本={est_short_cost:,.0f}點(滿足點{target_cover_2:,.0f}點) [得分:{s2_fut}/25分] | "
        f"(3)選擇權PCR={in_pcr:.1f}% [得分:{s3_pcr}/12分] ＋ 自營商權證避險={in_warrant_yi:+.2f}億 [得分:{s3_war}/8分] (期權合計:{s3_opt_war}/20分) | "
        f"(4)60分K與日5MA微觀扳機：60分5MA={fleet['k60_ma5']:,.0f}/60分J={k60_j:.1f} [得分:{s4_k60}/15分] | "
        f"(5)均線空間與日KDJ彈力：5MA={twii_ma5:,.0f}/10MA={twii_ma10:,.0f}/20MA月線={twii_ma20:,.0f}(乖離{fleet['bias_20']:+.2f}%)/60MA季線={twii_ma60:,.0f}/120MA半年線={twii_ma120:,.0f}/240MA年線={twii_ma240:,.0f}、日K={fleet['k9']}/D={fleet['d9']}/J={j_now:.1f}({j_trend_str})、日DMI(+DI={fleet['pdi_d']}/-DI={fleet['mdi_d']}/ADX={fleet['adx_d']}) [得分:{s5_space}/15分] | "
        f"(6)新台幣匯率={fx_now:.3f}元 (5MA={fx_ma5:.3f}/20MA={fx_ma20:.3f}) [加減分:{fx_score_adj_str}] | "
        f"(7)美股({tf['us_session_tag']})VIX與板塊連動：VIX={tf['vix_now']:.2f}({tf['vix_chg_pct']:+.2f}%)、費半SOX={tf['sox_chg']:+.2f}%、台積電ADR={tf['tsm_chg']:+.2f}%{tf['tsm_ext_str']}、輝達NVDA={tf['nvda_chg']:+.2f}%{tf['nvda_ext_str']} | 美股領漲：{up_sec_clean} | 美股領跌：{dn_sec_clean} | "
        f"(8)台股散戶綜合恐慌指數={tf['tw_panic_idx']}/100分 ({tf['panic_label']})"
    ]
    lines.append("4. [短線表1-A：站穩月線且DMI多方之有基之彈旱地拔蔥榜]：" + (" ； ".join([fmt_stock_full_detail(r) for _, r in df_launch_fund.head(8).iterrows()]) if not df_launch_fund.empty else "今日暫無符合標的"))
    lines.append("5. [短線表1-B：純看技術面旱地拔蔥榜]：" + (" ； ".join([fmt_stock_full_detail(r) for _, r in df_launch_pure.head(8).iterrows()]) if not df_launch_pure.empty else "今日暫無符合標的"))
    lines.append("6. [短線表2：N字洗盤量縮守月線榜]：" + (" ； ".join([fmt_stock_full_detail(r) for _, r in df_pullback.head(8).iterrows()]) if not df_pullback.empty else "今日暫無符合標的"))
    lines.append("7. [短線表3：上升軌道推升與急拉警戒榜]：" + (" ； ".join([fmt_stock_full_detail(r) for _, r in df_orbit.head(8).iterrows()]) if not df_orbit.empty else "今日暫無符合標的"))
    lines.append("8. [長線表3-A：電子與金融權值核心多頭榜]：" + (" ； ".join([fmt_stock_full_detail(r) for _, r in df_long_bc.head(10).iterrows()]) if not df_long_bc.empty else "今日暫無站上月線之權值股"))
    lines.append("9. [長線表3-B：權值股回測支撐買點＆轉機股榜(含EPS SOP v2.0鑽石切入價)]：" + (" ； ".join([f"{r.get('類型與轉機標註', '')} " + fmt_stock_full_detail(r) for _, r in df_long_turn.head(12).iterrows()]) if not df_long_turn.empty else "今日暫無符合標的"))
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
            lines.append("10. [分頁四：久旱逢甘霖(已排除營建與一次性認列＋含三大法人買賣超＋EPS SOP v2.0)真轉機前15強]：" + " ； ".join(t_list))
    return "\n".join(lines)

tab1, tab2, tab3, tab4 = st.tabs([
    "🧭 大盤多空儀表板",
    "⚡ 短線旱地拔蔥榜",
    "🏛️ 長線鑽石波段榜",
    "📈 上市櫃月營收快篩"
])

with tab1:
    hero_box_class = "war-hero-box" if (score_5d >= 60 or is_above_ma5) else "war-hero-box-warn"
    score_color = "#00e676" if (score_5d >= 60 or is_above_ma5) else "#ffab40"
    banner_bg = "rgba(0, 230, 118, 0.16)" if (score_5d >= 60 or is_above_ma5) else "rgba(255, 152, 0, 0.22)"
    banner_color = "#00e676" if (score_5d >= 60 or is_above_ma5) else "#ffd54f"
    idx_color = "#ff4d4f" if fleet["twii_chg_pct"] >= 0 else "#00e676"
    sox_c = "#ff4d4f" if tf["sox_chg"] >= 0 else "#00e676"
    tsm_c = "#ff4d4f" if tf["tsm_chg"] >= 0 else "#00e676"
    nvda_c = "#ff4d4f" if tf["nvda_chg"] >= 0 else "#00e676"

    up_sec_cards = format_sector_bold_html(tf["top_up_sectors"], is_up=True)
    dn_sec_cards = format_sector_bold_html(tf["bot_dn_sectors"], is_up=False)
    war_note_txt = "正值代表認購大戶進場點火" if in_warrant_yi >= 0 else "負值逾35億代表散戶認購停損宣洩、浮額洗淨具反彈契機"

    master_hero_html = f"""
<div class="{hero_box_class}">
<div class="war-score-title">
📊 台股加權指數：<span style="font-size:34px; color:{idx_color};">{twii_now:,.1f} 點 ({fleet['twii_chg_pct']:+.2f}%)</span>
&nbsp;｜&nbsp; 🏆 多空總評分：<span style="font-size:34px; color:{score_color};">{score_5d}</span> / 100 分
&nbsp;｜&nbsp; 💧 建議水庫水位：<span style="font-size:30px; color:#80d8ff;">{water_level_pct}</span>
</div>
<div class="war-circuit-trace">
🕒 對時：<b>{fleet['date_str']}</b> (開盤 <b>{twii_open:,.0f}</b> / 低 <b>{fleet['twii_low']:,.0f}</b> ～ 高 <b>{fleet['twii_high']:,.0f}</b> / 昨收 <b>{twii_prev_c:,.0f}</b>) &nbsp;｜&nbsp; {circuit_trace_str}
</div><br>
<div class="war-grade-banner" style="background:{banner_bg}; color:{banner_color}; border:1px solid {banner_color};">
🧭 大盤多空方向總結：{grade_str} &nbsp;｜&nbsp; 📌 實戰策略：{water_level_note}
</div>
<div class="war-dim-grid">
<div class="war-dim-item" style="border-left: 5px solid #ff4d4f;">
<span class="war-dim-concl" style="font-size:17.5px;">1. 🔥 台股當日熱門爆量族群雷達（預估量 vs. 昨日量成長比 × 族群漲幅）</span>
<div style="font-size:14.5px; color:#ffd54f; margin-bottom:5px;">
• <b>量能推進器</b>：{vol_note}（自動比對全市場核心與強勢股「今日預估量 ÷ 昨日收盤量」）
</div>
{tw_hot_sec_info['hot_html']}
{tw_hot_sec_info['cold_html']}
</div>
<div class="war-dim-item" style="border-left: 5px solid {tf['vix_color']};">
<span class="war-dim-concl" style="font-size:17.5px;">2. 🌎 美股 VIX ＆ 費半/台積ADR與領漲領跌板塊：{tf['us_session_tag']}</span>
<div style="font-size:15px; margin-bottom:6px;">
• <b>核心結論</b>：<b style="color:{tf['vix_color']};">{tf['vix_status']}</b> ｜ <b>費半(SOX)</b>：<b style="color:{sox_c};">{tf['sox_chg']:+.2f}%</b> ｜ <b>台積ADR</b>：<b style="color:{tsm_c};">{tf['tsm_chg']:+.2f}%{tf['tsm_ext_str']}</b> ｜ <b>輝達</b>：<b style="color:{nvda_c};">{tf['nvda_chg']:+.2f}%{tf['nvda_ext_str']}</b>
</div>
{up_sec_cards}
{dn_sec_cards}
</div>
<div class="war-dim-item" style="border-left: 5px solid {tf['panic_color']};">
<span class="war-dim-concl">3. 😱 台股散戶綜合恐慌指數（得分：{tf['tw_panic_idx']} / 100 分）</span>
• <b>重點結論</b>：<b style="color:{tf['panic_color']};">{tf['panic_label']}</b><br>
• <b>合成權重</b>：<b>純個股維持率(35%)</b> ＋ <b>PCR(25%)</b> ＋ <b>小台散戶/權證(20%)</b> ＋ <b>美股VIX(20%)</b>。<br>
• <b>判讀基準</b>：<b>&gt;60分</b>代表散戶斷頭清洗、強烈反彈訊號；<b>35~55分</b>為籌碼平衡選股區；<b>&lt;30分</b>防過熱拉回。
</div>
<div class="war-dim-item" style="border-left: 5px solid #00e676;">
<span class="war-dim-concl">4. 🛡️ 純個股融資維持率（得分：{s1_maint} / 25 分）｜ {s1_concl}</span>
• <b>現況數據</b>：剔除 ETF 後之純個股維持率為 <b style="color:#ffd54f; font-size:16.5px;">{in_maint_pct:.1f}%</b>，已自11月初斷頭底回升。<br>
• <b>判讀基準</b>：<b style="color:#00e676;">135% ～ 145% 為常態波段落底甜美區</b>；跌破 <b>135%</b> 觸發 T+2 早盤強制斷頭萬箭齊發。
</div>
<div class="war-dim-item" style="border-left: 5px solid #ffd54f;">
<span class="war-dim-concl">5. 📊 大小台期貨動向（得分：{s2_fut} / 25 分）｜ {s2_concl}</span>
• <b>大台籌碼</b>：<b>外資 {in_fini_chg:+,}口</b> (未平倉 <b>{in_fini_oi:+,}口</b>) ｜ <b style="color:#ff8a80;">投信 {sitc_tx_chg_v:+,}口</b> (未平倉 <b>{in_sitc_oi:+,}口</b>) ➔ 實質空單 <b>{real_directional_fini_oi:+,}口</b>。<br>
• <b>小台與成本</b>：<b>小台外資 {fini_mtx_chg_v:+,}口</b> / <b style="color:#00e676;">小台散戶 {retail_mtx_chg_v:+,}口</b> ｜ 🎯 <b>外資佈空成本 {est_short_cost:,.0f}點</b> (滿足點 <b>{target_cover_2:,.0f}點</b>)。
</div>
<div class="war-dim-item" style="border-left: 5px solid #00e676;">
<span class="war-dim-concl">6. 🎯 選擇權PCR＋權證避險（得分：{s3_opt_war} / 20 分）｜ {s3_concl}</span>
• <b>選擇權 PCR</b>：全月份 Put/Call Ratio 為 <b style="color:#00e676; font-size:16.5px;">{in_pcr:.1f}%</b>（得分 <b>{s3_pcr}/12分</b>；<b>&gt;80%免扣分</b>、莊家下檔支撐穩固）。<br>
• <b>權證避險</b>：自營商真實 Delta 避險買賣超為 <b style="color:#ff8a80; font-size:16.5px;">{in_warrant_yi:+.2f} 億元</b>（得分 <b>{s3_war}/8分</b>；<b>{war_note_txt}</b>）。
</div>
<div class="war-dim-item" style="border-left: 5px solid #00e676;">
<span class="war-dim-concl">7. ⚡ 60分K與日5MA微觀扳機（得分：{s4_k60} / 15 分）｜ {s4_concl}</span>
• <b>微觀位階</b>：60分5MA 為 <b>{fleet['k60_ma5']:,.0f}點</b>、60分20MA 為 <b>{fleet['k60_ma20']:,.0f}點</b>；60分KDJ 之 K={k60_k:.1f} / D={k60_d:.1f} / <b style="color:#ffd54f;">J={k60_j:.1f} ({k60_j_trend})</b>。<br>
• <b>實戰意義</b>：目前已重新站上 60分5MA(<b>{fleet['k60_ma5']:,.0f}點</b>) 與 日5MA(<b>{twii_ma5:,.0f}點</b>)，展開 c 浪挑戰月線(<b>{twii_ma20:,.0f}點</b>)反壓。
</div>
<div class="war-dim-item" style="border-left: 5px solid #00e676;">
<span class="war-dim-concl">8. 📈 均線空間、KDJ與台幣匯率（空間得分：{s5_space}/15分 ｜ 匯率：{fx_score_adj_str}）</span>
• <b>各級均線</b>：<b>5MA {twii_ma5:,.0f}</b>({fleet['bias_5']:+.1f}%) ｜ <b>10MA {twii_ma10:,.0f}</b>({fleet['bias_10']:+.1f}%) ｜ <b>月線 {twii_ma20:,.0f}</b>({fleet['bias_20']:+.1f}%) ｜ <b>季線 {twii_ma60:,.0f}</b> ｜ <b>半年線 {twii_ma120:,.0f}</b> ｜ <b>年線 {twii_ma240:,.0f}</b>。<br>
• <b>指標與匯率</b>：日J=<b style="color:#ffd54f;">{j_now:.1f} ({j_trend_str})</b> ｜ 台幣 <b style="color:#00e676;">{fx_now:.3f}元</b> (5MA={fx_ma5:.3f}，{fx_concl.replace('📌 結論：', '')})。
</div>
</div>
<div class="war-action-box">
<div style="font-size:19px; font-weight:900; color:#ffd54f; margin-bottom:8px; border-bottom:1px solid rgba(255,213,79,0.3); padding-bottom:4px;">
🔮 【大盤長中短多空趨勢定性 ＆ 波段「預計漲到哪裡 ／ 跌到哪裡」階段導航總結】：
</div>
• <b>📌 核心結論（目前所處階段）</b>：<span class="stage-pill-active">🔥 11/13 已將最後 1 張正2(@32.57) 獲利出清✅！大盤正挑戰【節點B：月線 {twii_ma20:,.0f} ～ 半年線 {twii_ma120:,.0f} 點】反壓區，日J={j_now:.1f}偏高，個股買支撐不追高！</span><br>
• <b>🧭 雙龍頭專屬技術導航</b>：<br>
&nbsp;&nbsp;① <b>力成 (6239，現價 {p_6239:.1f}元，持1張@253)</b>：<b style="color:#00e676;">今日已突破年線({m6239['ma240']:.1f}元)與月線({m6239['ma20']:.1f}元)翻紅！</b>惟短線日J({m6239['j_val']:.0f})偏高，預計先<b>回測年線與月線支撐區 ({pti_ma240_sup_s}元) 良性換手後，再向上挑戰季線 {pti_b_take_s}元 (270元關卡)</b>！<br>
&nbsp;&nbsp;② <b>光寶科 (2301，現價 {p_2301:.1f}元，持1張@216)</b>：<b style="color:#ffd54f;">目前於 60分K (228~234.5元) 進行箱型橫盤整理</b>，日線穩守 10MA({m2301['ma10']:.1f}元)之上；待下週月線扣抵值下降走平後，有望帶量突破箱頂直攻 <b>月線與半年線 ({liteon_res_s}元)</b>！<br>
• 🚀 <b style="color:#ff8a80; font-size:16.5px;">大盤預計會【漲到哪裡】（上檔三階段目標與完成度標註）</b>：<br>
&nbsp;&nbsp;① <b>第一階段（突破 5MA/10MA 攻抵 44,000～{fleet['twii_high']:,.0f} 點）</b>：<span class="stage-pill-done">✅ 已達成！（廣達@291.5、0050@103.05、正2@32.31/32.13/32.57 全數獲利收割完畢！）</span><br>
&nbsp;&nbsp;② <b>第二階段（節點B：挑戰下彎月線 {twii_ma20:,.0f} ～ 5月線 {twii_m_ma5:,.0f} ～ 半年線 {twii_ma120:,.0f} 點）</b>：<span class="stage-pill-active">🎯 當前逼近中（距月線僅差 {max(0.0, twii_ma20 - twii_now):,.0f} 點！光寶科於 {liteon_res_s}元 鎖利、力成於 {pti_b_take_s}元 調節價差）</span><br>
&nbsp;&nbsp;③ <b>第三階段（極限反壓：下彎 60MA 季線 {twii_ma60:,.0f} ～ 布林上軌 {fleet['bb_upper']:,.0f} 點）</b>：<span class="stage-pill-wait">⏳ 強壓天花板（季線扣高下彎，初碰必拉回打 12月中 節點C 右腳）</span><br>
• 🛡️ <b style="color:#80d8ff; font-size:16.5px;">大盤預計拉回會【跌到哪裡】（下檔三道防守支撐與建議進場價位）</b>：<br>
&nbsp;&nbsp;① <b>第一道支撐（極短線分界：60分5MA {fleet['k60_ma5']:,.0f} ～ 日5MA {twii_ma5:,.0f} 點）</b>：<span class="stage-pill-done">🛡️ 短多防守線（力成回測年線 {pti_ma240_sup_s}元 守穩可逢低布局）</span><br>
&nbsp;&nbsp;② <b>第二道支撐（十日線與換手區：日10MA {twii_ma10:,.0f} 點與 42,000～42,500 點）</b>：<span class="stage-pill-wait">🎯 短線低接區（力成若洗盤至 {pti_b_dip_s}元 低接第2張 ｜ 00400A 拉回 12.40~12.60元 買3~4張）</span><br>
&nbsp;&nbsp;③ <b>第三道支撐（節點C：12月中~1月中右腳 41,500～42,000 點 ／ 節點D2：明年4~5月年線 {twii_ma240:,.0f}～41,000 點終極底）</b>：<span class="stage-pill-wait">💎 波段黃金進場價：力成於 {pti_c_buy_s}元 買第2、3張 ｜ 光寶科於 {liteon_c_buy_s}元 買第2、3張 ｜ 00400A 於 12.15~12.45元 投40%資金買6~7張</span>
</div>
</div>
"""
    st.markdown(clean_html(master_hero_html), unsafe_allow_html=True)

    # 2. 三層物理分離式 Base64 SVG 大三角收斂與雙主軸終極作戰藍圖
    st.markdown(
        build_dynamic_triangle_blueprint_html(
            twii_now, fleet["twii_high"], twii_ma5, twii_ma10, twii_ma20, twii_ma60, twii_ma120, twii_ma240,
            j_now, k60_j, k_mas
        ),
        unsafe_allow_html=True
    )

    # 3. 終極定案：四大波浪節點 × 力成(6239) × 光寶科(2301) × 0050正2 × 00400A 建議進場價與操作總表
    st.markdown("#### 📋 【三、終極定案：四大波浪節點 × 力成(6239) × 光寶科(2301) × 0050正2 × 00400A 建議進場價與操作總表】")
    df_blueprint_table = pd.DataFrame([
        {
            "波浪節點與時間": "0. 節點A ➔ 11/12~13 強彈波",
            "目前狀態": "✅ 已完成 (廣達與正2均已高檔獲利出清！)",
            "大盤動態點位": f"37,837 ➔ {fleet['twii_high']:,.0f}點",
            "力成(6239) 扇形封裝主軸": f"持1張@253.0 (現價{p_6239:.1f}元已站上年線{m6239['ma240']:.1f}與月線{m6239['ma20']:.1f})",
            "光寶科(2301) 雲端電源主軸": f"持1張@216.0 (現價{p_2301:.1f}元，帳面獲利+16,200元)",
            "0050正2 (00631L) 操作": "11/12買1張@30.99 ➔ 11/13早盤已於 @32.57 獲利出清✅ (目前0張空手)",
            "00400A 存股 (持31張@14.4)": f"現價 {p_00400a:.2f}元，暫停追高、等待拉回 12.60元 以下買區"
        },
        {
            "波浪節點與時間": "1. 短線換手支撐 (回測年線/5MA)",
            "目前狀態": f"🔥 盤中隨時觸發 (大盤日J={j_now:.1f}偏高)",
            "大盤動態點位": f"42,815～{twii_ma5:,.0f}點 (守5MA/10MA)",
            "力成(6239) 扇形封裝主軸": f"🎯【回測年線支撐】先看回測年線/月線 {pti_ma240_sup_s}元 守穩再上；若洗盤至 {pti_b_dip_s}元 低接第2張",
            "光寶科(2301) 雲端電源主軸": f"🎯【60分K箱型整理】於 228~234.5元 橫盤消化賣壓，守日10MA({m2301['ma10']:.1f}元)續抱待下週突破",
            "0050正2 (00631L) 操作": f"空手觀望；若大盤急跌回測日5MA/10MA (正2約 {k_mas['00631L']['ma5']:.2f}~30.2元) 才考慮短接",
            "00400A 存股 (持31張@14.4)": "🎯【第一階進場價：12.40~12.60元】拉回買區時試單買3~4張(動用30%資金)"
        },
        {
            "波浪節點與時間": "2. 節點B (11月底 c浪攻月/半年線)",
            "目前狀態": "🎯 下週主攻目標區",
            "大盤動態點位": f"44,200～{twii_ma120:,.0f}點 (月線{twii_ma20:,.0f})",
            "力成(6239) 扇形封裝主軸": f"🏆【短線挑戰270目標】換手後向上挑戰季線反壓 {pti_b_take_s}元 (270關卡) 調節價差或鎖利！",
            "光寶科(2301) 雲端電源主軸": f"🏆【下週突破目標價】60分K盤整突破後，挑戰月/半年線 ({liteon_res_s}元) 逢高鎖利換現！",
            "0050正2 (00631L) 操作": "已於 @32.57 提早獲利收割完畢✅ 保持空手避開月/半年線反壓震盪",
            "00400A 存股 (持31張@14.4)": "12.80~13.10元暫停追高，握緊現金等節點C右腳"
        },
        {
            "波浪節點與時間": "3. 節點C (12月中~1月中右腳)",
            "目前狀態": "⏳ 波段雙主軸黃金買點",
            "大盤動態點位": "41,500～42,000點 (量縮打右腳)",
            "力成(6239) 扇形封裝主軸": f"💎【波段黃金進場價】於前波起漲底 {pti_c_buy_s}元 買進第 2、3 張迎接2027 FOPLP！",
            "光寶科(2301) 雲端電源主軸": f"💎【波段黃金進場價】於前波平台 {liteon_c_buy_s}元 買進第 2、3 張光寶科",
            "0050正2 (00631L) 操作": "空手觀望 (資金集中於力成、光寶科與00400A)",
            "00400A 存股 (持31張@14.4)": "💎【第二階進場價：12.15~12.45元】投40%資金買 6~7張 (推升至41張)"
        },
        {
            "波浪節點與時間": "4. 節點D1 (2027/3底 Q1壓線頂)",
            "目前狀態": "⏳ 2027 Q1 波段逃命頂",
            "大盤動態點位": "44,000點 (大三角上軌末端)",
            "力成(6239) 扇形封裝主軸": f"🏆【波段主升目標價】趁 FOPLP 試產題材衝 {m6239['d1_take_low']:.0f}~{m6239['d1_take_high']:.0f}元 分批獲利了結！",
            "光寶科(2301) 雲端電源主軸": f"🏆【波段主升目標價】趁 800VDC 量產衝 {m2301['d1_take_low']:.0f}~{m2301['d1_take_high']:.0f}元 賣出2張鎖利！",
            "0050正2 (00631L) 操作": "空手觀望，嚴防月MACD翻綠與報稅季賣壓",
            "00400A 存股 (持31張@14.4)": "12.60~13.20元暫停單筆加碼，僅用月配息 0.12元 滾零股"
        },
        {
            "波浪節點與時間": "5. 節點D2 (2027/4~5月 C波終極底)",
            "目前狀態": "⏳ 2027 終極重錘抄底區",
            "大盤動態點位": f"40,000～41,000點 (年線{twii_ma240:,.0f}+)",
            "力成(6239) 扇形封裝主軸": f"🎯【長線鐵底進場價】於 {m6239['d2_buy_low']:.0f}~{m6239['d2_buy_high']:.0f}元 長線低接迎接下半年CPO整合",
            "光寶科(2301) 雲端電源主軸": f"🎯【長線鐵底進場價】於年線區 {m2301['d2_buy_low']:.0f}~{m2301['d2_buy_high']:.0f}元 低接1張或轉存股",
            "0050正2 (00631L) 操作": "空手觀望或長線低接",
            "00400A 存股 (持31張@14.4)": "👑【第三階進場價：11.80~12.10元】重錘買 8~12張，破50張自體繁殖！"
        }
    ])
    st.dataframe(df_blueprint_table, use_container_width=True, hide_index=True)

    # 4. 程式資料總結數據包
    with st.expander("📋 點此展開／收合【程式資料總結數據包 ➔ 內含台股當日熱門爆量族群、大盤全指標評分、力成/光寶科導航與三大法人明細】", expanded=True):
        st.text_area("📱 手機/電腦可直接全選複製以下總結資料包貼給 Gemini 淘金客進行雙核 20 模組深度健檢：", value=build_complete_ai_snapshot(), height=195)

    # 5. 大盤各級均線支撐壓力與扣抵點位導航表
    st.markdown("#### 🧭 【細節查核】大盤各級均線支撐壓力與扣抵點位導航表（動態城堡原則：壓力突破轉支撐 vs. 上檔蓋頭反壓）")
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
    ma10_guide = f"已站穩10MA({twii_ma10:,.0f}點)與5MA({twii_ma5:,.0f}點)之上，短多結構穩固！" if is_above_ma10 else f"短線反彈第一目標區({twii_ma10:,.0f}點)，突破才算轉強。"

    df_ma_matrix = pd.DataFrame([
        build_pure_ma_row("① 60分K 5MA (極短線)", fleet["k60_ma5"], fleet["k60_ma5"] + 50, fleet["k60_ma5"] + 150, "✅ 【微觀止跌已站上】" if twii_now >= fleet["k60_ma5"] else "🔑 【b浪換手轉折線】", "已重新站回60分5MA之上，極短線多方控盤挑戰月線反壓！"),
        build_pure_ma_row("② 日K 5MA (五日攻擊線・短撐)", twii_ma5, fleet["ma5_prev"], fleet["deduct_5d"], "✅ 【五日線已突破轉支撐】" if is_above_ma5 else "🔑 【短線止跌確認線】", f"日5MA({twii_ma5:,.0f}點)翻揚扣低強力助漲！未跌破前波段反彈持續。"),
        build_pure_ma_row("③ 日K 10MA (十日慣性線)", twii_ma10, fleet["ma10_prev"], fleet["deduct_10d"], ma10_role, ma10_guide),
        build_pure_ma_row("④ 日K 20MA (月線・節點B第一反壓)", twii_ma20, fleet["ma20_prev"], fleet["deduct_20d"], "✅ 【月線已突破】" if twii_now >= twii_ma20 else "🏆 【節點B第一反壓 ➔ 二測突破真反轉】", f"目前最近之上檔蓋頭反壓({twii_ma20:,.0f}點)！初碰下彎月線易震盪換手。"),
        build_pure_ma_row("⑤ 日K 120MA (半年線・節點B上軌)", twii_ma120, fleet["ma120_prev"], fleet["deduct_120d"], "✅ 【半年線已收復】" if twii_now >= twii_ma120 else "🧭 【節點B核心目標區】(上揚半年線)", f"半年線({twii_ma120:,.0f}點)與月線構成 `{twii_ma20:,.0f}~{twii_ma120:,.0f}點` 節點B反壓帶，短線急拉股分批鎖利。"),
        build_pure_ma_row("⑥ 日K 60MA (季線・外資成本區)", twii_ma60, fleet["ma60_prev"], fleet["deduct_60d"], "🚨 【終極蓋頭反壓】(中期強壓區)", f"本波反彈極限天花板({twii_ma60:,.0f}點)！季線扣高下彎，反彈至此區個股宜減碼鎖利。"),
        build_pure_ma_row("⑦ 日K 240MA (年線・節點D2真正鐵底)", twii_ma240, fleet["ma240_prev"], twii_ma240 * 0.92, "💎 【長線真正強支撐】(牛熊防線)", f"真正的長線鐵底支撐({twii_ma240:,.0f}點)，明年4~5月上移至 40,000~41,000 點構成節點D2鐵板。")
    ])
    st.dataframe(df_ma_matrix, use_container_width=True, hide_index=True)

with tab2:
    st.caption("💡 【v56.0 嚴格旱地拔蔥濾網＋昨日量比】已新增「昨日量比（今日預估量 ÷ 昨日收盤量）」，並強制規定入榜股必須 **「現價站上 20MA 月線 且 DMI 多方控盤 (+DI > -DI)」**！")
    st.markdown("#### 🔥 【表 1-A：短線看技術＋基本面（站穩月線＋DMI多方＋單月與累計年營收雙成長・真旱地拔蔥點火榜）】")
    if not df_launch_fund.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_launch_fund)), use_container_width=True)
    else: st.info("今日盤面暫無同時符合「站穩20MA月線 ＋ DMI(+DI>-DI)多方 ＋ 單月與累計營收雙成長」之真旱地拔蔥標的。")

    st.markdown("#### 🎭 【表 1-B：短線純看技術面（站穩月線＋DMI多方・不看基本面之主力純籌碼旱地拔蔥炒作榜）】")
    if not df_launch_pure.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_launch_pure)), use_container_width=True)
    else: st.info("今日盤面暫無符合「站穩月線＋純技術籌碼旱地拔蔥點火」之標的。")

    st.markdown("#### 🎯 【表二：N字洗盤蓄勢 ＆ 拉回月線/十日線低接榜（含三大法人買賣超與 EPS SOP v2.0）】")
    if not df_pullback.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_pullback)), use_container_width=True)
    else: st.info("今日暫無符合量縮回測月線有守之標的。")

    st.markdown("#### 🛡️ 【表三：上升軌道推升 ＆ KD鈍化 / 3日急拉警戒榜（含三大法人買賣超與 EPS SOP v2.0）】")
    if not df_orbit.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_orbit)), use_container_width=True)
    else: st.info("今日暫無符合多頭軌道推升之標的。")

with tab3:
    st.caption("💎 【淘金客 EPS 預估 SOP v2.0（時序收斂 ＋ 三率體質乘數 T）】已將新核心持股 `6239 力成 (👑電子權值・FOPLP扇形封裝龍頭)` 納入核心權值追蹤池！")
    st.markdown("#### 🏛️ 【表 3-A：👑 電子權值股 ＆ 🏦 金融官股/龍頭金控 長線多頭核心波段榜（站穩月線 ＋ DMI多方 ＋ EPS SOP v2.0）】")
    if not df_long_bc.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_long_bc)), use_container_width=True)
    else: st.info("今日大盤回檔，暫無同時站上月線且 DMI 多方之權值股；請參考下方【表 3-B】低估鑽石買點！")

    st.markdown("#### 🔄 【表 3-B：🔄 轉機題材特選股 ＆ 💎 電子/金融權值股月線下築底反彈買點榜（含 6239 力成、2301 光寶科等回測支撐標的）】")
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
        search_kw = f_col1.text_input("🔍 搜尋代碼/名稱/備註", value="", placeholder="例如：6239、力成、2301、光寶科...")
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
