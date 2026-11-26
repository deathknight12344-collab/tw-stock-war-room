import streamlit as st
import pandas as pd
import numpy as np
import datetime, pytz, requests, urllib3, io, os, json, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed

from config_events import (
    get_tw_now, parse_roc_or_iso_date,
    MSCI_CACHE_FILE, MSCI_DEFAULT_2026_11, CORE_PORTFOLIO_16, US_SECTOR_DEFINITIONS
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# 0. 美股休市／提前收盤自動偵測（修正「休市休市」贅字）
# ==========================================
US_NYSE_HOLIDAYS = {
    "2026-01-01": "元旦", "2026-01-19": "馬丁路德金紀念日", "2026-02-16": "總統日",
    "2026-04-03": "耶穌受難日", "2026-05-25": "陣亡將士紀念日", "2026-06-19": "六月節",
    "2026-07-03": "美國國慶補假", "2026-09-07": "勞動節", "2026-11-26": "感恩節",
    "2026-12-25": "聖誕節",
    "2027-01-01": "元旦", "2027-01-18": "馬丁路德金紀念日", "2027-02-15": "總統日",
    "2027-03-26": "耶穌受難日", "2027-05-31": "陣亡將士紀念日", "2027-06-18": "六月節",
    "2027-07-05": "美國國慶補假", "2027-09-06": "勞動節", "2027-11-25": "感恩節",
    "2027-12-24": "聖誕節補假"
}
US_NYSE_EARLY_CLOSE = {
    "2026-11-27": "黑色星期五（美東 13:00 / 台北 02:00 提前收盤）",
    "2026-12-24": "平安夜（美東 13:00 / 台北 02:00 提前收盤）",
    "2027-11-26": "黑色星期五（美東 13:00 / 台北 02:00 提前收盤）"
}

def get_us_market_holiday_status() -> dict:
    now_tw = get_tw_now()
    now_et = now_tw.astimezone(pytz.timezone("America/New_York"))
    et_iso, tw_iso = now_et.strftime("%Y-%m-%d"), now_tw.strftime("%Y-%m-%d")
    check_dates = [tw_iso, et_iso] if now_tw.hour >= 9 else [et_iso, tw_iso]
    for d_iso in check_dates:
        if d_iso in US_NYSE_HOLIDAYS:
            nm = US_NYSE_HOLIDAYS[d_iso]
            return {
                "is_holiday": True, "is_early_close": False, "holiday_name": nm, "us_date": d_iso,
                "banner_note": f"🇺🇸 美股 ({d_iso}) 因【{nm}】休市：無美股現貨指引，留意台股由內資主導且易量縮震盪"
            }
        if d_iso in US_NYSE_EARLY_CLOSE:
            nm = US_NYSE_EARLY_CLOSE[d_iso]
            return {
                "is_holiday": False, "is_early_close": True, "holiday_name": nm, "us_date": d_iso,
                "banner_note": f"🇺🇸 美股 ({d_iso})【{nm}】：交投偏淡，慎防流動性降低引發尾盤波動"
            }
    return {"is_holiday": False, "is_early_close": False, "holiday_name": "", "us_date": et_iso, "banner_note": ""}

# ==========================================
# 1. 雙市處置股、上市注意股、近5日除權息與 MSCI 動態池
# ==========================================
@st.cache_data(ttl=1800, show_spinner=False)
def fetch_twse_disposition_codes():
    disp_map = {"3189": "⚠️ MSCI 觀察期曾列處置股，留意生效延後風險"}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get("https://www.twse.com.tw/rwd/zh/announcement/punish?response=json", headers=headers, timeout=5, verify=False)
        if r.status_code == 200:
            for row in r.json().get("data", []):
                if len(row) >= 7 and len(str(row[2]).strip()) == 4 and str(row[2]).strip().isdigit():
                    disp_map[str(row[2]).strip()] = f"🚨 上市處置 ({str(row[6]).strip()})"
    except Exception:
        pass
    try:
        r_tp = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_disposal_information", headers=headers, timeout=5, verify=False)
        if r_tp.status_code == 200 and isinstance(r_tp.json(), list):
            for row in r_tp.json():
                c = str(row.get("SecuritiesCompanyCode", row.get("Code", ""))).strip()
                period = str(row.get("DispositionPeriod", row.get("DispositionCondition", "分盤交易"))).strip()
                if len(c) == 4 and c.isdigit():
                    disp_map[c] = f"🚨 上櫃處置 ({period})"
    except Exception:
        pass
    return disp_map

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_twse_notice_codes() -> set:
    notice_set = set()
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/announcement/notice", headers={"User-Agent": "Mozilla/5.0"}, timeout=5, verify=False)
        if r.status_code == 200 and isinstance(r.json(), list):
            for row in r.json():
                c = str(row.get("Code", "")).strip()
                if len(c) == 4 and c.isdigit():
                    notice_set.add(c)
    except Exception:
        pass
    return notice_set

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_twse_exdiv_soon(days_ahead: int = 5) -> dict:
    ex_map = {}
    today_d = get_tw_now().date()
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL", headers={"User-Agent": "Mozilla/5.0"}, timeout=5, verify=False)
        if r.status_code == 200 and isinstance(r.json(), list):
            for row in r.json():
                c = str(row.get("Code", "")).strip()
                d_iso = parse_roc_or_iso_date(row.get("Date", ""))
                if len(c) == 4 and c.isdigit() and d_iso:
                    try:
                        ev_d = datetime.datetime.strptime(d_iso, "%Y-%m-%d").date()
                        diff = (ev_d - today_d).days
                        if -1 <= diff <= days_ahead:
                            ex_map[c] = f"{d_iso[5:]}{str(row.get('Exdividend', '除權息')).strip()}"
                    except Exception:
                        continue
    except Exception:
        pass
    return ex_map

@st.cache_data(ttl=21600, show_spinner=False)
def fetch_dynamic_msci_data(sig: int = 1) -> dict:
    data_out = dict(MSCI_DEFAULT_2026_11)
    if os.path.exists(MSCI_CACHE_FILE):
        try:
            with open(MSCI_CACHE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if saved.get("std_add") or saved.get("small_add"):
                    data_out = saved
        except Exception:
            pass
    data_out["disposition_map"] = fetch_twse_disposition_codes()
    return data_out

# ==========================================
# 2. 即時報價、類股結構（修正電子工業＆非金電匹配）、廣度與美股＋信用利差
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
                post_p, pre_p = meta.get("postMarketPrice", None), meta.get("preMarketPrice", None)
                ext_chg_pct, ext_label = None, ""
                if mkt_state in ["POST", "POSTPOST", "CLOSED"] and post_p is not None and float(post_p) > 0 and p_now > 0:
                    ext_chg_pct = round(((float(post_p) - p_now) / p_now) * 100.0, 2); ext_label = f"盤後 {ext_chg_pct:+.2f}%"
                elif mkt_state in ["PRE", "PREPRE"] and pre_p is not None and float(pre_p) > 0 and p_now > 0:
                    ext_chg_pct = round(((float(pre_p) - p_now) / p_now) * 100.0, 2); ext_label = f"盤前 {ext_chg_pct:+.2f}%"
                elif len(closes) > 1 and mkt_state != "REGULAR":
                    last_ext_p = float(closes[-1])
                    if abs(last_ext_p - p_now) / max(1.0, p_now) > 0.0005:
                        ext_chg_pct = round(((last_ext_p - p_now) / p_now) * 100.0, 2); ext_label = f"盤後 {ext_chg_pct:+.2f}%"

                if p_now > 0:
                    return {
                        "open": round(o_now, decimals), "close": round(p_now, decimals),
                        "high": round(max(h_now, p_now), decimals), "low": round(min(l_now, p_now), decimals),
                        "prev_close": round(y_prev, decimals),
                        "chg_pct": round(((p_now - y_prev) / y_prev) * 100, 2) if y_prev > 0 else 0.0,
                        "mkt_state": mkt_state, "ext_chg_pct": ext_chg_pct, "ext_label": ext_label,
                        "vol_lots": 0, "time": dt_obj.strftime("%H:%M:%S"),
                        "date": dt_obj.strftime("%Y%m%d"), "date_iso": dt_obj.strftime("%Y-%m-%d"), "ts": mkt_time
                    }
    except Exception:
        pass
    return None

@st.cache_data(ttl=15, show_spinner=False)
def fetch_mis_realtime_batch(code_market_tuples):
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp", "Cache-Control": "no-cache"}
    rt_dict = {}
    all_pairs = list(dict.fromkeys(list(code_market_tuples) + CORE_PORTFOLIO_16))
    queries = ["tse_t00.tw", "otc_o00.tw"] + [f"{'tse' if m == 'TW' else 'otc'}_{c}.tw" for c, m in all_pairs]
    for i in range(0, len(queries), 25):
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={'|'.join(queries[i:i + 25])}&json=1&delay=0&_={int(time.time() * 1000)}"
        try:
            r = requests.get(url, headers=headers, timeout=5, verify=False)
            if r.status_code == 200:
                for item in r.json().get("msgArray", []):
                    c_code = str(item.get("c", "")).strip()
                    p_now = parse_mis_price(item, is_index=(c_code in ["t00", "o00"]))
                    if not c_code or p_now is None: continue
                    def clean_f(k, dv):
                        try: return float(str(item.get(k, dv)).replace(",", ""))
                        except Exception: return dv
                    y_prev, o_now, h_now, l_now, v_lots = clean_f("y", p_now), clean_f("o", p_now), clean_f("h", p_now), clean_f("l", p_now), int(clean_f("v", 0))
                    d_raw = str(item.get("d", "")).strip()
                    d_iso = f"{d_raw[:4]}-{d_raw[4:6]}-{d_raw[6:8]}" if len(d_raw) == 8 else ""
                    rt_dict[c_code] = {
                        "open": round(o_now, 2), "close": round(p_now, 2), "high": round(max(h_now, p_now), 2), "low": round(min(l_now, p_now), 2),
                        "prev_close": round(y_prev, 2), "chg_pct": round(((p_now - y_prev) / y_prev) * 100, 2) if y_prev > 0 else 0.0,
                        "vol_lots": v_lots, "time": str(item.get("t", "")), "date": d_raw, "date_iso": d_iso
                    }
        except Exception:
            continue
    yv8_t00 = fetch_yahoo_v8_realtime("%5ETWII", decimals=2)
    if yv8_t00 and yv8_t00["close"] > 0:
        if "t00" not in rt_dict or yv8_t00.get("date_iso", "") > rt_dict["t00"].get("date_iso", ""):
            rt_dict["t00"] = yv8_t00
    if "o00" not in rt_dict or rt_dict["o00"].get("close", 0) <= 0:
        yv8_otc = fetch_yahoo_v8_realtime("%5ETWOII", decimals=2)
        if yv8_otc and yv8_otc["close"] > 0:
            rt_dict["o00"] = yv8_otc
    fx_v8 = fetch_yahoo_v8_realtime("TWD=X", decimals=3)
    if fx_v8 and fx_v8["close"] > 10.0:
        rt_dict["USDTWD"] = fx_v8
    return rt_dict

@st.cache_data(ttl=15, show_spinner=False)
def fetch_mis_realtime_core16():
    return fetch_mis_realtime_batch(tuple(CORE_PORTFOLIO_16))

@st.cache_data(ttl=180, show_spinner=False)
def fetch_market_sector_indices() -> dict:
    """v65.1 修正：精準匹配證交所正式名稱『電子工業類指數』與『未含金融電子指數』"""
    out = {"elec_chg": None, "semi_chg": None, "fin_chg": None, "non_ef_chg": None, "date_iso": ""}
    try:
        r = requests.get("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?type=IND&response=json", headers={"User-Agent": "Mozilla/5.0"}, timeout=5, verify=False)
        if r.status_code == 200:
            j = r.json()
            out["date_iso"] = parse_roc_or_iso_date(j.get("date", ""))
            for tbl in j.get("tables", []):
                for row in tbl.get("data", []):
                    if len(row) < 5: continue
                    nm = str(row[0]).strip().replace(" ", "")
                    sign_raw = str(row[2]).strip()
                    pct_s = str(row[4]).replace(",", "").replace("%", "").replace("+", "").strip()
                    try:
                        val = float(pct_s)
                        if ("-" in sign_raw or "green" in sign_raw.lower()) and val > 0:
                            val = -val
                    except Exception:
                        continue
                    if nm in ["電子類指數", "電子工業類指數"]:
                        out["elec_chg"] = round(val, 2)
                    elif nm in ["半導體類指數", "半導體業類指數"]:
                        out["semi_chg"] = round(val, 2)
                    elif nm in ["金融保險類指數", "金融保險業類指數"]:
                        out["fin_chg"] = round(val, 2)
                    elif ("未含金融電子" in nm) or ("非金電" in nm):
                        out["non_ef_chg"] = round(val, 2)
    except Exception:
        pass
    return out

@st.cache_data(ttl=180, show_spinner=False)
def fetch_market_prices_fast():
    headers = {"User-Agent": "Mozilla/5.0"}
    price_dict = {}
    twii_fmtqik = {"close": 0.0, "prev_close": 0.0, "chg_pct": 0.0, "date_iso": "", "val_yi": 0.0, "val_map": {}}
    track_etfs = {"0050", "00631L", "00400A", "00985B", "00713"}

    try:
        r_idx = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK", headers=headers, timeout=6, verify=False)
        if r_idx.status_code == 200 and len(r_idx.json()) > 0:
            rows_f = r_idx.json()
            val_map = {}
            for rf in rows_f:
                di = parse_roc_or_iso_date(rf.get("Date", ""))
                tv = float(str(rf.get("TradeValue", "0")).replace(",", "").strip() or 0)
                if di and tv > 0: val_map[di] = round(tv / 1e8, 1)
            last_row = rows_f[-1]
            idx_c = float(str(last_row.get("TAIEX", "0")).replace(",", ""))
            idx_chg = float(str(last_row.get("Change", "0")).replace(",", "").replace("+", ""))
            prev_idx = idx_c - idx_chg
            d_iso = parse_roc_or_iso_date(last_row.get("Date", ""))
            if idx_c > 0:
                twii_fmtqik = {
                    "close": round(idx_c, 2), "prev_close": round(prev_idx, 2),
                    "chg_pct": round((idx_chg / max(1.0, prev_idx)) * 100.0, 2),
                    "date_iso": d_iso, "val_yi": val_map.get(d_iso, 0.0), "val_map": val_map
                }
    except Exception:
        pass

    mi_breadth = None
    try:
        r_mi = requests.get("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?type=MS&response=json", headers=headers, timeout=5, verify=False)
        if r_mi.status_code == 200:
            j_mi = r_mi.json()
            mi_date = parse_roc_or_iso_date(j_mi.get("date", ""))
            for tbl in j_mi.get("tables", []):
                if "漲跌證券數" in str(tbl.get("title", "")):
                    u_cnt, u_lim, d_cnt, d_lim = 0, 0, 0, 0
                    for row in tbl.get("data", []):
                        lbl = str(row[0]).strip()
                        cell_stk = str(row[2] if len(row) >= 3 else row[1]).replace(",", "")
                        m = re.search(r"(\d+)\s*\(\s*(\d+)\s*\)", cell_stk)
                        if m:
                            if "上漲" in lbl: u_cnt, u_lim = int(m.group(1)), int(m.group(2))
                            elif "下跌" in lbl: d_cnt, d_lim = int(m.group(1)), int(m.group(2))
                    if (u_cnt + d_cnt) > 200:
                        mi_breadth = {"date_iso": mi_date, "up_cnt": u_cnt, "limit_up": u_lim, "dn_cnt": d_cnt, "limit_down": d_lim, "adv_ratio_pct": round((u_cnt / max(1, u_cnt + d_cnt)) * 100.0, 1)}
                    break
    except Exception:
        pass

    for url, mkt_code, c_k, n_k, p_k, v_k in [
        ("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", "TW", "Code", "Name", "ClosingPrice", "TradeVolume"),
        ("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", "TWO", "SecuritiesCompanyCode", "CompanyName", "Close", "TradingShares")
    ]:
        try:
            r_m = requests.get(url, headers=headers, timeout=8, verify=False)
            if r_m.status_code == 200:
                for row in r_m.json():
                    code = str(row.get(c_k, row.get("Code", ""))).strip()
                    if (len(code) == 4 and code.isdigit()) or code in track_etfs:
                        try:
                            c_str = str(row.get(p_k, row.get("ClosingPrice", ""))).replace(",", "").strip()
                            if not c_str or c_str in ["-", "--", "N/A", ""]: continue
                            c_p = float(c_str)
                            chg_raw = str(row.get("Change", "0")).replace(",", "").replace("+", "").replace("X", "").replace(" ", "").strip()
                            try: chg_v = float(chg_raw)
                            except Exception: chg_v = 0.0
                            if "-" in str(row.get("Change", "")) and chg_v > 0: chg_v = -chg_v
                            prev_c = c_p - chg_v
                            v_str = str(row.get(v_k, row.get("TradeVolume", "0"))).replace(",", "").strip()
                            v_sh = float(v_str) if v_str.replace(".", "").isdigit() else 0.0
                            if c_p > 0:
                                price_dict[code] = {
                                    "name": str(row.get(n_k, row.get("Name", ""))).strip(), "close": c_p,
                                    "chg_pct": round((chg_v / prev_c) * 100, 2) if prev_c > 0 else 0.0,
                                    "vol_lots": int(v_sh // 1000), "vol_shares": v_sh, "market": mkt_code,
                                    "pe": np.nan, "cum_yoy": None, "fini_lots": 0, "sitc_lots": 0, "dealer_lots": 0, "inst_date": "",
                                    "sector": "", "tdcc_1000": None, "tdcc_400_delta": None, "tdcc_1000_delta": None,
                                    "day_trade_str": "N/A (交由淘金客聯網查核)"
                                }
                        except Exception:
                            continue
        except Exception:
            pass

    lim_up = sum(1 for c, i in price_dict.items() if len(c) == 4 and c.isdigit() and i["chg_pct"] >= 9.4)
    lim_dn = sum(1 for c, i in price_dict.items() if len(c) == 4 and c.isdigit() and i["chg_pct"] <= -9.4)
    up_cnt = sum(1 for c, i in price_dict.items() if len(c) == 4 and c.isdigit() and i["chg_pct"] > 0.0)
    dn_cnt = sum(1 for c, i in price_dict.items() if len(c) == 4 and c.isdigit() and i["chg_pct"] < 0.0)
    adv_ratio = round((up_cnt / max(1, up_cnt + dn_cnt)) * 100.0, 1)

    now_b = get_tw_now()
    today_b_iso = now_b.strftime("%Y-%m-%d")
    is_intraday_b = (now_b.weekday() < 5) and (540 <= (now_b.hour * 60 + now_b.minute) < 870)
    fmt_d_iso = twii_fmtqik.get("date_iso", "")

    breadth = {
        "limit_up": lim_up, "limit_down": lim_dn, "up_cnt": up_cnt, "dn_cnt": dn_cnt,
        "adv_ratio_pct": adv_ratio, "date_iso": fmt_d_iso,
        "is_stale_intraday": bool(is_intraday_b and (fmt_d_iso != today_b_iso)),
        "src": f"前日收盤({fmt_d_iso}・盤中未結算)" if (is_intraday_b and fmt_d_iso != today_b_iso) else "上市櫃收盤彙整"
    }

    if mi_breadth and (mi_breadth["limit_up"] > lim_up or abs(mi_breadth["adv_ratio_pct"] - adv_ratio) >= 12.0 or mi_breadth["date_iso"] == today_b_iso):
        is_mi_stale = bool(is_intraday_b and (mi_breadth["date_iso"] != today_b_iso))
        breadth = {
            "limit_up": mi_breadth["limit_up"], "limit_down": mi_breadth["limit_down"],
            "up_cnt": mi_breadth["up_cnt"], "dn_cnt": mi_breadth["dn_cnt"],
            "adv_ratio_pct": mi_breadth["adv_ratio_pct"], "date_iso": mi_breadth["date_iso"],
            "is_stale_intraday": is_mi_stale,
            "src": f"前日MI_INDEX({mi_breadth['date_iso']}・盤中未結算)" if is_mi_stale else f"證交所MI_INDEX即時({mi_breadth['date_iso']})"
        }
    return price_dict, twii_fmtqik, breadth

@st.cache_data(ttl=120, show_spinner=False)
def fetch_us_sectors_and_leaders():
    all_syms = ["%5EVIX", "%5ESOX", "%5ETNX", "%5EGSPC", "%5EIXIC", "%5EDJI", "NQ=F", "SGOV", "HYG", "LQD"]
    for etf_sym, _, leaders in US_SECTOR_DEFINITIONS:
        all_syms.append(etf_sym)
        for l_sym, _ in leaders:
            if l_sym not in all_syms: all_syms.append(l_sym)
    quotes_map = {}
    with ThreadPoolExecutor(max_workers=14) as executor:
        fut_map = {executor.submit(fetch_yahoo_v8_realtime, sym, 3): sym for sym in all_syms}
        for fut in as_completed(fut_map):
            res_q = fut.result()
            if res_q: quotes_map[fut_map[fut]] = res_q

    tnx_q = quotes_map.get("%5ETNX", {})
    tnx_raw = tnx_q.get("close", 4.92)
    tnx_val = round(tnx_raw / 10.0, 2) if tnx_raw > 15.0 else round(tnx_raw, 2)
    tnx_chg = tnx_q.get("chg_pct", 0.0)

    hyg_chg = quotes_map.get("HYG", {}).get("chg_pct", 0.0)
    lqd_chg = quotes_map.get("LQD", {}).get("chg_pct", 0.0)
    spread_diff = round(hyg_chg - lqd_chg, 2)
    credit_spread_alert = bool((hyg_chg <= -0.8) or (spread_diff <= -0.6 and tnx_chg < -1.0))
    credit_spread_str = f"🚨 信用利差擴大警戒 (HYG {hyg_chg:+.2f}% vs LQD {lqd_chg:+.2f}%)" if credit_spread_alert else f"✅ 信用利差穩定 (HYG {hyg_chg:+.2f}% / LQD {lqd_chg:+.2f}%)"

    holiday_info = get_us_market_holiday_status()
    m_state = quotes_map.get("TSM", {}).get("mkt_state", "CLOSED")
    nq_chg = quotes_map.get("NQ=F", {}).get("chg_pct", 0.0)
    if holiday_info["is_holiday"]:
        us_session_tag = f"🇺🇸【美股{holiday_info['holiday_name']}休市・僅期貨薄量 (NQ {nq_chg:+.2f}%)】"
    elif holiday_info["is_early_close"]:
        us_session_tag = f"⏰【美股提前收盤日・NQ {nq_chg:+.2f}%】"
    elif m_state == "REGULAR":
        us_session_tag = "🟢【美股盤中即時交易中】"
    elif m_state in ["PRE", "PREPRE"]:
        us_session_tag = f"🌅【美股收盤＋盤前電子盤 (NQ {nq_chg:+.2f}%)】"
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
        sector_list.append({"etf": etf_sym, "name": sec_name, "chg_pct": sec_chg, "summary": f"<b>{sec_name}({etf_sym} {sec_chg:+.2f}%{ext_lbl})</b> ➔ 指標股：{'、'.join([s for _, s in leader_strs[:3]])}"})
    sector_list.sort(key=lambda x: x["chg_pct"], reverse=True)
    return {
        "session_tag": us_session_tag, "holiday_info": holiday_info, "quotes": quotes_map,
        "tnx_val": tnx_val, "tnx_chg": tnx_chg, "nq_chg": nq_chg,
        "hyg_chg": hyg_chg, "lqd_chg": lqd_chg, "credit_spread_alert": credit_spread_alert, "credit_spread_str": credit_spread_str,
        "top_up_sectors": [x["summary"] for x in sector_list[:3]],
        "bot_dn_sectors": [x["summary"] for x in sorted(sector_list, key=lambda x: x["chg_pct"])[:2]]
    }

# ==========================================
# 3. v65.1 新增：夜盤 4 時段時間感知路由 ＋ 前五大/十大特定法人 ＋ 選擇權 Max OI
# ==========================================
def classify_night_session_window(now_tw) -> tuple[str, str]:
    """依台北時間嚴格判定台指期夜盤所處時段，徹底杜絕拿 11 小時前舊結算冒充即時夜盤"""
    wd = now_tw.weekday()  # 0=週一 ... 4=週五, 5=週六, 6=週日
    t_min = now_tw.hour * 60 + now_tw.minute
    if (wd <= 4 and t_min >= 900) or (1 <= wd <= 5 and t_min < 300):
        open_d = now_tw.date() if t_min >= 900 else (now_tw.date() - datetime.timedelta(days=1))
        return "NIGHT_LIVE", open_d.strftime("%Y-%m-%d")
    if wd <= 4 and 300 <= t_min < 525:
        return "PRE_MARKET", now_tw.strftime("%Y-%m-%d")
    if wd <= 4 and 525 <= t_min < 900:
        return "DAY_REFLECTED", now_tw.strftime("%Y-%m-%d")
    return "WEEKEND", now_tw.strftime("%Y-%m-%d")

def _fetch_taifex_top_traders_and_max_oi(now_tw, headers: dict) -> dict:
    """v65.1 新增：抓取期交所『前五大／前十大特定法人台指期淨未平倉』與『選擇權 Max OI 買權壓力／賣權支撐履約價』"""
    out = {
        "top5_spec_net": None, "top10_spec_net": None, "top_spec_all_10": None, "top_spec_date": "", "top_spec_valid": False,
        "max_oi_call_strike": None, "max_oi_call_oi": 0, "max_oi_put_strike": None, "max_oi_put_oi": 0,
        "max_oi_contract": "", "max_oi_valid": False
    }
    def _num(v):
        try: return int(round(float(str(v).replace(",", "").strip())))
        except Exception: return 0
    def _paren_num(s):
        m = re.search(r"\(([-+]?[\d,]+)\)", str(s))
        return _num(m.group(1)) if m else 0

    # A. 前五大／前十大特定法人台指期淨未平倉
    try:
        r_lt = requests.post(
            "https://www.taifex.com.tw/cht/3/largeTraderFutDown",
            data={"queryStartDate": (now_tw - datetime.timedelta(days=10)).strftime("%Y/%m/%d"), "queryEndDate": now_tw.strftime("%Y/%m/%d")},
            headers=headers, timeout=5, verify=False
        )
        if r_lt.status_code == 200 and len(r_lt.content) > 200:
            df_lt = pd.read_csv(io.StringIO(r_lt.content.decode("cp950", errors="ignore")))
            df_lt.columns = [str(c).strip() for c in df_lt.columns]
            if len(df_lt.columns) >= 8:
                tx_df = df_lt[df_lt.iloc[:, 1].astype(str).str.contains("臺股期貨|台股期貨|TX", na=False) & (~df_lt.iloc[:, 1].astype(str).str.contains("小型|微型", na=False))]
                if not tx_df.empty:
                    ld_lt = sorted(tx_df.iloc[:, 0].astype(str).str.strip().unique())[-1]
                    sub = tx_df[tx_df.iloc[:, 0].astype(str).str.strip() == ld_lt]
                    b5_col = next((c for c in df_lt.columns if "前五大" in c and "買方" in c and "部位" in c), df_lt.columns[3])
                    b10_col = next((c for c in df_lt.columns if "前十大" in c and "買方" in c and "部位" in c), df_lt.columns[5])
                    s5_col = next((c for c in df_lt.columns if "前五大" in c and "賣方" in c and "部位" in c), df_lt.columns[7] if len(df_lt.columns) > 7 else df_lt.columns[4])
                    s10_col = next((c for c in df_lt.columns if "前十大" in c and "賣方" in c and "部位" in c), df_lt.columns[9] if len(df_lt.columns) > 9 else df_lt.columns[6])
                    near_rows = sub[~sub.iloc[:, 2].astype(str).str.contains("999999|所有", na=False)].sort_values(by=df_lt.columns[2])
                    all_rows = sub[sub.iloc[:, 2].astype(str).str.contains("999999|所有", na=False)]
                    if not near_rows.empty:
                        rn = near_rows.iloc[0]
                        b5, s5 = _paren_num(rn[b5_col]), _paren_num(rn[s5_col])
                        b10, s10 = _paren_num(rn[b10_col]), _paren_num(rn[s10_col])
                        if any(x > 0 for x in [b5, s5, b10, s10]):
                            out["top5_spec_net"], out["top10_spec_net"] = b5 - s5, b10 - s10
                            out["top_spec_date"], out["top_spec_valid"] = ld_lt, True
                    if not all_rows.empty:
                        ra = all_rows.iloc[0]
                        b10a, s10a = _paren_num(ra[b10_col]), _paren_num(ra[s10_col])
                        if b10a > 0 or s10a > 0:
                            out["top_spec_all_10"] = b10a - s10a
    except Exception:
        pass

    if not out["top_spec_valid"]:
        try:
            r_lt2 = requests.get("https://openapi.taifex.com.tw/v1/MarketDataOfLargeTradersFuturesPosition", headers=headers, timeout=5, verify=False)
            if r_lt2.status_code == 200 and isinstance(r_lt2.json(), list):
                tx_o = [x for x in r_lt2.json() if str(x.get("Contract", "")).strip() == "TX"]
                if tx_o:
                    near_o = [x for x in tx_o if str(x.get("ContractMonth", "")).strip() not in ["999999", "所有契約", "All"]]
                    near_o.sort(key=lambda x: str(x.get("ContractMonth", "")))
                    all_o = [x for x in tx_o if str(x.get("ContractMonth", "")).strip() in ["999999", "所有契約", "All"]]
                    def _kget(d, pats):
                        for k, v in d.items():
                            kl = k.lower()
                            if all(p in kl for p in pats): return _num(v)
                        return 0
                    if near_o:
                        r0 = near_o[0]
                        b5, s5 = _kget(r0, ["5", "spec", "buy"]), _kget(r0, ["5", "spec", "sell"])
                        b10, s10 = _kget(r0, ["10", "spec", "buy"]), _kget(r0, ["10", "spec", "sell"])
                        if any(x > 0 for x in [b5, s5, b10, s10]):
                            out["top5_spec_net"], out["top10_spec_net"] = b5 - s5, b10 - s10
                            out["top_spec_date"] = parse_roc_or_iso_date(r0.get("Date", ""))
                            out["top_spec_valid"] = True
                    if all_o:
                        b10a, s10a = _kget(all_o[0], ["10", "spec", "buy"]), _kget(all_o[0], ["10", "spec", "sell"])
                        if b10a > 0 or s10a > 0:
                            out["top_spec_all_10"] = b10a - s10a
        except Exception:
            pass

    # B. 選擇權最大未平倉履約價 (Max OI Call=上檔天花板 / Put=下檔地板)
    try:
        r_opt = requests.get("https://openapi.taifex.com.tw/v1/DailyMarketReportOpt", headers=headers, timeout=6, verify=False)
        if r_opt.status_code == 200 and isinstance(r_opt.json(), list):
            by_cm = {}
            for row in r_opt.json():
                if str(row.get("Contract", "")).strip() != "TXO": continue
                sess = str(row.get("TradingSession", "")).strip()
                if sess not in ["一般", "Regular", ""]: continue
                cm = str(row.get("ContractMonth(Week)", "")).strip()
                cp = str(row.get("Call/Put", "")).strip()
                oi = _num(row.get("OpenInterest", 0))
                try: strike = int(round(float(str(row.get("StrikePrice", "0")).replace(",", "").strip())))
                except Exception: strike = 0
                if cm and oi > 0 and strike > 5000:
                    by_cm.setdefault(cm, {"tot": 0, "C": {}, "P": {}})
                    by_cm[cm]["tot"] += oi
                    side = "C" if ("Call" in cp or "買" in cp) else ("P" if ("Put" in cp or "賣" in cp) else "")
                    if side:
                        by_cm[cm][side][strike] = by_cm[cm][side].get(strike, 0) + oi
            if by_cm:
                # 挑選總 OI 最大的主力到期合約（兼顧週選與月選）
                best_cm = max(by_cm.keys(), key=lambda k: by_cm[k]["tot"])
                c_map, p_map = by_cm[best_cm]["C"], by_cm[best_cm]["P"]
                if c_map and p_map:
                    c_max_k = max(c_map.keys(), key=lambda k: c_map[k])
                    p_max_k = max(p_map.keys(), key=lambda k: p_map[k])
                    out["max_oi_call_strike"], out["max_oi_call_oi"] = c_max_k, c_map[c_max_k]
                    out["max_oi_put_strike"], out["max_oi_put_oi"] = p_max_k, p_map[p_max_k]
                    out["max_oi_contract"], out["max_oi_valid"] = best_cm, True
    except Exception:
        pass
    return out

# ==========================================
# 4. 期貨雙口徑 ΔOI ＋ 夜盤 4 時段路由 ＋ 特定法人 ＋ Max OI ＋ 外資 20 日連買賣
# ==========================================
@st.cache_data(ttl=120, show_spinner=False)
def fetch_auto_derivatives_and_margin_v63(twii_now: float, twii_date_iso: str):
    headers = {"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"}
    now_tw = get_tw_now()
    night_window, night_open_iso = classify_night_session_window(now_tw)

    res = {
        "fini_tx_oi": 0, "fini_tx_oi_delta": 0, "fini_tx_chg": 0,
        "sitc_tx_oi": 0, "sitc_tx_chg": 0, "dealer_tx_oi": 0, "dealer_tx_chg": 0, "real_fini_oi": 0,
        "fini_mtx_oi": 0, "fini_mtx_chg": 0, "sitc_mtx_oi": 0, "sitc_mtx_chg": 0,
        "dealer_mtx_oi": 0, "dealer_mtx_chg": 0, "retail_mtx_oi": 0, "retail_mtx_chg": 0,
        "consec_cover_days": 0, "cover_3d_sum": 0, "fut_src": "自動讀取中", "fut_date": "",
        "tx_close": 0.0, "basis_pts": 0.0, "basis_valid": False, "basis_src": "待對日同步",
        "night_valid": False, "night_tx_close": 0.0, "night_tx_chg": 0.0, "night_tx_chg_pct": 0.0,
        "night_tx_time": "", "night_src": "", "night_is_live_mis": False, "night_nq_chg": None,
        "night_is_stale": False, "night_window": night_window,
        "pc_ratio": 80.0, "warrant_hedge_yi": 0.0, "twse_date": "",
        "spot_fini_yi": 0.0, "spot_sitc_yi": 0.0, "spot_dealer_self_yi": 0.0, "spot_total_yi": 0.0,
        "spot_fini_consec": 0, "spot_fini_consec_sum": 0.0, "spot_fini_5d_sum": 0.0, "spot_valid": False,
        "margin_balance_yi": 0.0, "margin_chg_yi": 0.0, "margin_2d_chg_yi": 0.0, "margin_2d_pct": 0.0,
        "margin_30d_dd_pct": 0.0, "margin_src": "自動讀取中",
        "top5_spec_net": None, "top10_spec_net": None, "top_spec_all_10": None, "top_spec_date": "", "top_spec_valid": False,
        "max_oi_call_strike": None, "max_oi_call_oi": 0, "max_oi_put_strike": None, "max_oi_put_oi": 0,
        "max_oi_contract": "", "max_oi_valid": False
    }

    # A. 選擇權 PCR
    pcr_ok = False
    try:
        r_p = requests.get("https://openapi.taifex.com.tw/v1/PutCallRatio", headers=headers, timeout=5, verify=False)
        if r_p.status_code == 200 and len(r_p.json()) > 0:
            lr = r_p.json()[-1]
            for k in ["PutCallOpenInterestRatio", "PutCallOpenInterestRatio%", "PutCallRatio"]:
                if k in lr and str(lr[k]).strip():
                    v = float(str(lr[k]).replace(",", "").strip())
                    if 10.0 <= v <= 250.0: res["pc_ratio"] = round(v, 1); pcr_ok = True; break
    except Exception:
        pass
    if not pcr_ok:
        try:
            r_pc = requests.post("https://www.taifex.com.tw/cht/3/pcRatioDown", data={"queryStartDate": (now_tw - datetime.timedelta(days=12)).strftime("%Y/%m/%d"), "queryEndDate": now_tw.strftime("%Y/%m/%d")}, headers=headers, timeout=5, verify=False)
            if r_pc.status_code == 200 and len(r_pc.content) > 50:
                lines = [ln.strip().rstrip(",") for ln in r_pc.content.decode("cp950", errors="ignore").splitlines() if re.match(r"^\d{4}/\d{1,2}/\d{1,2}", ln.strip())]
                if lines:
                    lines.sort(key=lambda s: s.split(",")[0])
                    parts = [p.strip() for p in lines[-1].split(",") if p.strip()]
                    if len(parts) >= 7:
                        v = float(parts[6].replace("%", ""))
                        if 10.0 <= v <= 250.0: res["pc_ratio"] = round(v, 1)
        except Exception:
            pass

    # B. 並發回溯近 20 交易日 BFI82U（外資真實連買賣天數）
    cand_dates = []
    for d_back in range(0, 32):
        chk_dt = now_tw - datetime.timedelta(days=d_back)
        if chk_dt.weekday() < 5: cand_dates.append(chk_dt.strftime("%Y%m%d"))
        if len(cand_dates) >= 20: break

    def _fetch_bfi_one(chk_d):
        try:
            r_bfi = requests.get(f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate={chk_d}&type=day&response=json", headers=headers, timeout=4.0, verify=False)
            if r_bfi.status_code == 200 and len(r_bfi.json().get("data", [])) >= 4:
                f_yi = s_yi = ds_yi = dh_yi = tot_yi = 0.0
                for row in r_bfi.json()["data"]:
                    nm = str(row[0]).strip()
                    val_yi = float(str(row[3]).replace(",", "").strip()) / 1e8
                    if "外資" in nm: f_yi += val_yi
                    elif "投信" in nm: s_yi += val_yi
                    elif "自行買賣" in nm: ds_yi += val_yi
                    elif "避險" in nm: dh_yi += val_yi
                    elif "合計" in nm: tot_yi = val_yi
                return (chk_d, round(f_yi, 2), round(s_yi, 2), round(ds_yi, 2), round(dh_yi, 2), round(tot_yi if tot_yi != 0 else (f_yi + s_yi + ds_yi + dh_yi), 2))
        except Exception:
            pass
        return None

    spot_days_full = []
    with ThreadPoolExecutor(max_workers=8) as ex_b:
        for fut in as_completed([ex_b.submit(_fetch_bfi_one, d) for d in cand_dates]):
            r_one = fut.result()
            if r_one: spot_days_full.append(r_one)

    if spot_days_full:
        spot_days_full.sort(key=lambda x: x[0], reverse=True)
        d0, f0, s0, ds0, dh0, t0 = spot_days_full[0]
        res["twse_date"], res["spot_valid"] = d0, True
        res["spot_fini_yi"], res["spot_sitc_yi"], res["spot_dealer_self_yi"], res["warrant_hedge_yi"], res["spot_total_yi"] = f0, s0, ds0, dh0, t0
        first_sign = 1 if f0 > 0 else (-1 if f0 < 0 else 0)
        consec_sp, consec_sum = 0, 0.0
        for _, fv, _, _, _, _ in spot_days_full:
            if (first_sign > 0 and fv > 0) or (first_sign < 0 and fv < 0):
                consec_sp += first_sign; consec_sum += fv
            else:
                break
        res["spot_fini_consec"] = consec_sp
        res["spot_fini_consec_sum"] = round(consec_sum, 1)
        res["spot_fini_5d_sum"] = round(sum(x[1] for x in spot_days_full[:5]), 1)

    # C. 大台＋小台三大法人
    fut_ok = False
    try:
        r_fut = requests.post("https://www.taifex.com.tw/cht/3/futContractsDateDown", data={"queryStartDate": (now_tw - datetime.timedelta(days=14)).strftime("%Y/%m/%d"), "queryEndDate": now_tw.strftime("%Y/%m/%d")}, headers=headers, timeout=6, verify=False)
        if r_fut.status_code == 200 and len(r_fut.content) > 200:
            df_f = pd.read_csv(io.StringIO(r_fut.content.decode("cp950", errors="ignore")))
            df_f.columns = [str(c).strip() for c in df_f.columns]
            if len(df_f.columns) >= 14:
                dates = sorted(df_f.iloc[:, 0].dropna().astype(str).str.strip().unique())
                if dates:
                    latest_d = dates[-1]
                    oi_net_col = next((c for c in df_f.columns if "未平倉" in c and "口數" in c and "淨額" in c), None)
                    oi_l_col = next((c for c in df_f.columns if "多方" in c and "未平倉" in c and "口數" in c), None)
                    oi_s_col = next((c for c in df_f.columns if "空方" in c and "未平倉" in c and "口數" in c), None)
                    tr_net_col = next((c for c in df_f.columns if "交易" in c and "口數" in c and "淨額" in c), None)
                    def _to_i(v):
                        try: return int(float(str(v).replace(",", "").strip()))
                        except Exception: return 0
                    daily_fini_ois = []
                    for d_cur in dates:
                        for _, row in df_f[df_f.iloc[:, 0].astype(str).str.strip() == d_cur].iterrows():
                            pn, ident = str(row.iloc[1]).strip(), str(row.iloc[2]).strip()
                            tr = _to_i(row[tr_net_col]) if tr_net_col else _to_i(row.iloc[7])
                            oi = _to_i(row[oi_net_col]) if oi_net_col else ((_to_i(row[oi_l_col]) - _to_i(row[oi_s_col])) if (oi_l_col and oi_s_col) else _to_i(row.iloc[13]))
                            if ("臺股期貨" in pn or "台股期貨" in pn) and ("小型" not in pn) and ("微型" not in pn):
                                if "外資" in ident:
                                    daily_fini_ois.append(oi)
                                    if d_cur == latest_d: res["fini_tx_chg"], res["fini_tx_oi"] = tr, oi
                                elif "投信" in ident and d_cur == latest_d: res["sitc_tx_chg"], res["sitc_tx_oi"] = tr, oi
                                elif "自營" in ident and d_cur == latest_d: res["dealer_tx_chg"], res["dealer_tx_oi"] = tr, oi
                            elif ("小型臺指" in pn or "小型台指" in pn) and d_cur == latest_d:
                                if "外資" in ident: res["fini_mtx_chg"], res["fini_mtx_oi"] = tr, oi
                                elif "投信" in ident: res["sitc_mtx_chg"], res["sitc_mtx_oi"] = tr, oi
                                elif "自營" in ident: res["dealer_mtx_chg"], res["dealer_mtx_oi"] = tr, oi
                    if daily_fini_ois and res["fini_tx_oi"] != 0:
                        oi_deltas = [daily_fini_ois[i] - daily_fini_ois[i - 1] for i in range(1, len(daily_fini_ois))]
                        res["fini_tx_oi_delta"] = oi_deltas[-1] if oi_deltas else res["fini_tx_chg"]
                        consec = 0
                        for dv in reversed(oi_deltas):
                            if dv > 0: consec += 1
                            else: break
                        res["consec_cover_days"], res["cover_3d_sum"] = consec, int(sum(oi_deltas[-3:])) if oi_deltas else 0
                        res["real_fini_oi"] = res["fini_tx_oi"] + res["sitc_tx_oi"]
                        res["retail_mtx_oi"] = -(res["fini_mtx_oi"] + res["sitc_mtx_oi"] + res["dealer_mtx_oi"])
                        res["retail_mtx_chg"] = -(res["fini_mtx_chg"] + res["sitc_mtx_chg"] + res["dealer_mtx_chg"])
                        res["fut_date"], res["fut_src"], fut_ok = latest_d, f"期交所直連({latest_d})", True
    except Exception:
        pass

    if not fut_ok:
        try:
            st_d = (now_tw - datetime.timedelta(days=20)).strftime("%Y-%m-%d")
            r_fm = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanFuturesInstitutionalInvestors&data_id=TX&start_date={st_d}", timeout=5, verify=False)
            if r_fm.status_code == 200 and r_fm.json().get("data"):
                df_fm = pd.DataFrame(r_fm.json()["data"])
                df_fini = df_fm[df_fm["institutional_investors"].astype(str).str.contains("外資|Foreign", case=False, na=False)].sort_values("date")
                df_sitc = df_fm[df_fm["institutional_investors"].astype(str).str.contains("投信|Investment_Trust", case=False, na=False)].sort_values("date")
                df_dlr = df_fm[df_fm["institutional_investors"].astype(str).str.contains("自營|Dealer", case=False, na=False)].sort_values("date")
                if not df_fini.empty:
                    ois = (df_fini["long_open_interest_balance_volume"].astype(float) - df_fini["short_open_interest_balance_volume"].astype(float)).values
                    trs = (df_fini["long_deal_volume"].astype(float) - df_fini["short_deal_volume"].astype(float)).values
                    res["fini_tx_oi"], res["fini_tx_chg"] = int(round(ois[-1])), int(round(trs[-1]))
                    oi_deltas = [int(round(ois[i] - ois[i - 1])) for i in range(1, len(ois))]
                    res["fini_tx_oi_delta"] = oi_deltas[-1] if oi_deltas else res["fini_tx_chg"]
                    consec = 0
                    for dv in reversed(oi_deltas):
                        if dv > 0: consec += 1
                        else: break
                    res["consec_cover_days"], res["cover_3d_sum"] = consec, int(sum(oi_deltas[-3:])) if oi_deltas else 0
                    res["fut_date"], res["fut_src"], fut_ok = str(df_fini["date"].iloc[-1]), f"FinMind備援({df_fini['date'].iloc[-1]})", True
                if not df_sitc.empty:
                    res["sitc_tx_oi"] = int(round(float(df_sitc["long_open_interest_balance_volume"].iloc[-1]) - float(df_sitc["short_open_interest_balance_volume"].iloc[-1])))
                    res["sitc_tx_chg"] = int(round(float(df_sitc["long_deal_volume"].iloc[-1]) - float(df_sitc["short_deal_volume"].iloc[-1])))
                if not df_dlr.empty:
                    res["dealer_tx_oi"] = int(round(float(df_dlr["long_open_interest_balance_volume"].iloc[-1]) - float(df_dlr["short_open_interest_balance_volume"].iloc[-1])))
                    res["dealer_tx_chg"] = int(round(float(df_dlr["long_deal_volume"].iloc[-1]) - float(df_dlr["short_deal_volume"].iloc[-1])))
                res["real_fini_oi"] = res["fini_tx_oi"] + res["sitc_tx_oi"]
                try:
                    r_mtx = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanFuturesInstitutionalInvestors&data_id=MTX&start_date={st_d}", timeout=5, verify=False)
                    if r_mtx.status_code == 200 and r_mtx.json().get("data"):
                        df_mx = pd.DataFrame(r_mtx.json()["data"])
                        ld_mx = str(df_mx["date"].max())
                        for _, r_m in df_mx[df_mx["date"] == ld_mx].iterrows():
                            inv_s = str(r_m.get("institutional_investors", ""))
                            oi_m = int(round(float(r_m.get("long_open_interest_balance_volume", 0)) - float(r_m.get("short_open_interest_balance_volume", 0))))
                            tr_m = int(round(float(r_m.get("long_deal_volume", 0)) - float(r_m.get("short_deal_volume", 0))))
                            if ("外資" in inv_s) or ("Foreign" in inv_s): res["fini_mtx_oi"], res["fini_mtx_chg"] = oi_m, tr_m
                            elif ("投信" in inv_s) or ("Investment_Trust" in inv_s): res["sitc_mtx_oi"], res["sitc_mtx_chg"] = oi_m, tr_m
                            elif ("自營" in inv_s) or ("Dealer" in inv_s): res["dealer_mtx_oi"], res["dealer_mtx_chg"] = oi_m, tr_m
                        res["retail_mtx_oi"] = -(res["fini_mtx_oi"] + res["sitc_mtx_oi"] + res["dealer_mtx_oi"])
                        res["retail_mtx_chg"] = -(res["fini_mtx_chg"] + res["sitc_mtx_chg"] + res["dealer_mtx_chg"])
                except Exception:
                    pass
        except Exception:
            pass

    # C2. v65.1 新增：前五大/十大特定法人 ＋ 選擇權 Max OI
    res.update(_fetch_taifex_top_traders_and_max_oi(now_tw, headers))

    # D1. 台指期日盤與期現貨價差
    tx_ok = False
    try:
        r_mis = requests.post(
            "https://mis.taifex.com.tw/futures/api/getQuoteList",
            json={"MarketType": "0", "SymbolType": "F", "KindID": "1", "CID": "TXF", "ExpireMonth": "", "RowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"},
            headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}, timeout=4, verify=False
        )
        if r_mis.status_code == 200:
            q_list = r_mis.json().get("RtData", {}).get("QuoteList", [])
            near_rows = [
                q for q in q_list
                if str(q.get("SymbolID", "")).strip().startswith("TXF")
                and ("-" not in str(q.get("SymbolID", ""))) and ("/" not in str(q.get("SymbolID", "")))
                and ("現貨" not in str(q.get("DispCName", "")))
                and str(q.get("CLastPrice", "")).replace(",", "").replace(".", "").isdigit()
                and float(str(q.get("CLastPrice", "0")).replace(",", "")) > 5000
            ]
            if near_rows:
                c_tx = float(str(near_rows[0]["CLastPrice"]).replace(",", ""))
                q_date_iso = parse_roc_or_iso_date(near_rows[0].get("CDate", ""))
                if not q_date_iso or q_date_iso == twii_date_iso:
                    res["tx_close"], res["basis_pts"], res["basis_valid"], res["basis_src"], tx_ok = c_tx, round(c_tx - twii_now, 0), True, f"期交所MIS即時({q_date_iso or twii_date_iso})", True
    except Exception:
        pass

    if not tx_ok:
        try:
            r_tx = requests.get("https://openapi.taifex.com.tw/v1/DailyMarketReportFut", headers=headers, timeout=5, verify=False)
            if r_tx.status_code == 200 and isinstance(r_tx.json(), list):
                tx_rows = [row for row in r_tx.json() if str(row.get("Contract", "")).strip() == "TX" and len(str(row.get("ContractMonth(Week)", "")).strip()) == 6 and str(row.get("TradingSession", "")).strip() in ["一般", "Regular", ""]]
                if tx_rows:
                    tx_rows.sort(key=lambda x: str(x.get("ContractMonth(Week)", "")))
                    row0 = tx_rows[0]
                    f_iso = parse_roc_or_iso_date(row0.get("Date", ""))
                    c_tx = float(str(row0.get("Last", row0.get("SettlementPrice", "0"))).replace(",", "").strip())
                    if c_tx > 5000 and f_iso == twii_date_iso:
                        res["tx_close"], res["basis_pts"], res["basis_valid"], res["basis_src"], tx_ok = c_tx, round(c_tx - twii_now, 0), True, f"期交所OpenAPI({f_iso})", True
        except Exception:
            pass

    if not tx_ok:
        try:
            st_d = (now_tw - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
            r_tx2 = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanFuturesDaily&data_id=TX&start_date={st_d}", timeout=5, verify=False)
            if r_tx2.status_code == 200 and r_tx2.json().get("data"):
                df_t = pd.DataFrame(r_tx2.json()["data"])
                df_t = df_t[(df_t["trading_session"] == "position") & (df_t["contract_date"].astype(str).str.len() == 6)]
                if not df_t.empty:
                    latest_d = str(df_t["date"].max())
                    if latest_d == twii_date_iso:
                        c_tx = float(df_t[df_t["date"] == latest_d].sort_values("contract_date")["close"].iloc[0])
                        if c_tx > 5000:
                            res["tx_close"], res["basis_pts"], res["basis_valid"], res["basis_src"], tx_ok = c_tx, round(c_tx - twii_now, 0), True, f"FinMind({latest_d})", True
        except Exception:
            pass
    if not tx_ok:
        res["basis_src"] = f"跨日待盤後同步(現貨{twii_date_iso})"

    # D2. 🌙 v65.1 核心升級：夜盤 4 時段時間感知路由（交易時段嚴禁退回 11 小時前舊結算！）
    if night_window == "NIGHT_LIVE":
        live_hit = False
        # 通道 1：期交所 MIS 即時夜盤 (MarketType=1)，嚴格驗證日期/時間 >= 本場開盤
        try:
            r_ah = requests.post(
                "https://mis.taifex.com.tw/futures/api/getQuoteList",
                json={"MarketType": "1", "SymbolType": "F", "KindID": "1", "CID": "TXF", "ExpireMonth": "", "RowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"},
                headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}, timeout=4, verify=False
            )
            if r_ah.status_code == 200:
                q_ah = r_ah.json().get("RtData", {}).get("QuoteList", [])
                ah_rows = [
                    q for q in q_ah
                    if str(q.get("SymbolID", "")).strip().startswith("TXF")
                    and ("-" not in str(q.get("SymbolID", ""))) and ("/" not in str(q.get("SymbolID", "")))
                    and ("現貨" not in str(q.get("DispCName", "")))
                    and str(q.get("CLastPrice", "")).replace(",", "").replace(".", "").isdigit()
                    and float(str(q.get("CLastPrice", "0")).replace(",", "")) > 5000
                ]
                if ah_rows:
                    q0 = ah_rows[0]
                    c_dt = parse_roc_or_iso_date(q0.get("CDate", ""))
                    c_tm = str(q0.get("CTime", "")).strip()
                    tm_hhmm = int(c_tm[:4]) if (len(c_tm) >= 4 and c_tm[:4].isdigit()) else 0
                    is_fresh_live = (c_dt > night_open_iso) or (c_dt == night_open_iso and tm_hhmm >= 1500)
                    if is_fresh_live:
                        n_last = float(str(q0.get("CLastPrice", "0")).replace(",", ""))
                        n_ref_s = str(q0.get("CRefPrice", "0")).replace(",", "").strip()
                        n_ref = float(n_ref_s) if n_ref_s.replace(".", "").isdigit() and float(n_ref_s) > 5000 else (res["tx_close"] or twii_now)
                        n_chg = round(n_last - n_ref, 0)
                        n_pct = round((n_chg / max(1.0, n_ref)) * 100.0, 2)
                        tm_fmt = f"{c_tm[:2]}:{c_tm[2:4]}" if len(c_tm) >= 4 and c_tm[:4].isdigit() else c_tm
                        res["night_valid"], res["night_is_live_mis"], live_hit = True, True, True
                        res["night_tx_close"], res["night_tx_chg"], res["night_tx_chg_pct"] = round(n_last, 0), n_chg, n_pct
                        res["night_tx_time"], res["night_src"] = f"{c_dt} {tm_fmt}".strip(), "期交所MIS即時"
        except Exception:
            pass

        # 通道 2：Yahoo 台灣期貨即時備援（僅採信時間戳 >= 本場 15:00 開盤）
        if not live_hit:
            try:
                open_dt_tw = pytz.timezone("Asia/Taipei").localize(datetime.datetime.strptime(f"{night_open_iso} 15:00:00", "%Y-%m-%d %H:%M:%S"))
                open_ts = int(open_dt_tw.timestamp())
                r_yh = requests.get(
                    "https://tw.stock.yahoo.com/_td-stock/api/resource/StockServices.stockList;fields=change,changePercent,price,regularMarketTime,symbol;symbols=WTX%26",
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=3.5, verify=False
                )
                if r_yh.status_code == 200:
                    j_yh = r_yh.json()
                    lst_y = j_yh if isinstance(j_yh, list) else j_yh.get("list", [])
                    if lst_y:
                        it = lst_y[0]
                        p_y = float(it.get("price", {}).get("raw", 0) if isinstance(it.get("price"), dict) else (it.get("price") or 0))
                        ts_y = int(it.get("regularMarketTime", 0) or 0)
                        if p_y > 5000 and ts_y >= open_ts:
                            chg_y = float(it.get("change", {}).get("raw", 0) if isinstance(it.get("change"), dict) else (it.get("change") or 0))
                            pct_y = float(it.get("changePercent", {}).get("raw", 0) if isinstance(it.get("changePercent"), dict) else (it.get("changePercent") or 0))
                            dt_y = datetime.datetime.fromtimestamp(ts_y, pytz.timezone("Asia/Taipei")).strftime("%Y-%m-%d %H:%M")
                            res["night_valid"], res["night_is_live_mis"], live_hit = True, True, True
                            res["night_tx_close"], res["night_tx_chg"], res["night_tx_chg_pct"] = round(p_y, 0), round(chg_y, 0), round(pct_y, 2)
                            res["night_tx_time"], res["night_src"] = dt_y, "Yahoo期貨即時"
            except Exception:
                pass

        if not live_hit:
            res["night_src"] = "夜盤交易中(雲端即時受限，改看NQ或側邊欄手動輸入)"
    else:
        # 非夜盤交易時段（PRE_MARKET / DAY_REFLECTED / WEEKEND）：抓最新已結算 05:00 收盤結果
        night_cands = []
        try:
            r_ah2 = requests.get("https://openapi.taifex.com.tw/v1/DailyMarketReportFut", headers=headers, timeout=5, verify=False)
            if r_ah2.status_code == 200 and isinstance(r_ah2.json(), list):
                ah_o = [
                    row for row in r_ah2.json()
                    if str(row.get("Contract", "")).strip() == "TX"
                    and len(str(row.get("ContractMonth(Week)", "")).strip()) == 6
                    and ("盤後" in str(row.get("TradingSession", "")) or "After" in str(row.get("TradingSession", "")))
                ]
                if ah_o:
                    ah_o.sort(key=lambda x: str(x.get("ContractMonth(Week)", "")))
                    r0 = ah_o[0]
                    n_last = float(str(r0.get("Last", r0.get("Close", "0"))).replace(",", "").strip() or 0)
                    n_chg_s = str(r0.get("Change", "0")).replace(",", "").replace("+", "").strip()
                    n_chg = float(n_chg_s) if n_chg_s not in ["", "-", "--"] else 0.0
                    raw_pct_s = str(r0.get("%Change", r0.get("Change%", ""))).replace("%", "").replace("+", "").strip()
                    n_pct = float(raw_pct_s) if raw_pct_s not in ["", "-", "--"] else 0.0
                    if abs(n_pct) < 0.001 and abs(n_chg) > 0:
                        n_pct = round((n_chg / max(1.0, n_last - n_chg)) * 100.0, 2)
                    if n_last > 5000:
                        f_iso = parse_roc_or_iso_date(r0.get("Date", ""))
                        night_cands.append({"date": f_iso, "close": round(n_last, 0), "chg": round(n_chg, 0), "pct": round(n_pct, 2), "time": f"{f_iso} 05:00結算", "src": "期交所盤後結算"})
        except Exception:
            pass

        try:
            st_ah = (now_tw - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
            r_ah3 = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanFuturesDaily&data_id=TX&start_date={st_ah}", timeout=5, verify=False)
            if r_ah3.status_code == 200 and r_ah3.json().get("data"):
                df_ah = pd.DataFrame(r_ah3.json()["data"])
                df_ah = df_ah[(df_ah["trading_session"] == "after_market") & (df_ah["contract_date"].astype(str).str.len() == 6)]
                if not df_ah.empty:
                    ld_ah = str(df_ah["date"].max())
                    r0 = df_ah[df_ah["date"] == ld_ah].sort_values("contract_date").iloc[0]
                    n_last, n_chg, n_pct = float(r0.get("close", 0) or 0), float(r0.get("spread", 0) or 0), float(r0.get("spread_per", 0) or 0)
                    if abs(n_pct) < 0.001 and abs(n_chg) > 0:
                        n_pct = round((n_chg / max(1.0, n_last - n_chg)) * 100.0, 2)
                    if n_last > 5000:
                        night_cands.append({"date": ld_ah, "close": round(n_last, 0), "chg": round(n_chg, 0), "pct": round(n_pct, 2), "time": f"{ld_ah} 05:00結算", "src": "FinMind夜盤結算"})
        except Exception:
            pass

        if night_cands:
            night_cands.sort(key=lambda x: x["date"], reverse=True)
            best_n = night_cands[0]
            res["night_valid"] = True
            res["night_tx_close"], res["night_tx_chg"], res["night_tx_chg_pct"] = best_n["close"], best_n["chg"], best_n["pct"]
            res["night_tx_time"], res["night_src"] = best_n["time"], best_n["src"]
            # 日盤時段 (08:45~15:00) 視為已反映退出預測權重；盤前 (05:00~08:45) 僅採信最新結算
            if night_window == "DAY_REFLECTED":
                res["night_is_stale"] = True
            elif night_window == "PRE_MARKET":
                min_ok_d = (now_tw.date() - datetime.timedelta(days=3 if now_tw.weekday() == 0 else 1)).strftime("%Y-%m-%d")
                res["night_is_stale"] = bool(best_n["date"] < min_ok_d)
            else:
                res["night_is_stale"] = False

    if not res["night_is_live_mis"]:
        nq_live = fetch_yahoo_v8_realtime("NQ=F", decimals=2)
        if nq_live:
            res["night_nq_chg"] = nq_live.get("chg_pct", 0.0)

    # E. 真實大盤融資曲線
    mgn_ok = False
    try:
        st_m = (now_tw - datetime.timedelta(days=55)).strftime("%Y-%m-%d")
        r_m = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockTotalMarginPurchaseShortSale&start_date={st_m}", timeout=6, verify=False)
        if r_m.status_code == 200 and r_m.json().get("data"):
            df_m = pd.DataFrame(r_m.json()["data"])
            df_mm = df_m[df_m["name"].astype(str).str.contains("MarginPurchaseMoney", case=False, na=False)].sort_values("date")
            if len(df_mm) >= 3:
                vals = df_mm["TodayBalance"].astype(float).values
                scale = 1e8 if np.median(vals) > 1e10 else (1e5 if np.median(vals) > 1e6 else 1.0)
                vals_yi = vals / scale
                cur_yi, prev1_yi, prev2_yi, hi30_yi = float(vals_yi[-1]), float(vals_yi[-2]), float(vals_yi[-3]), float(np.max(vals_yi[-30:]))
                res["margin_balance_yi"] = round(cur_yi, 1)
                res["margin_chg_yi"] = round(cur_yi - prev1_yi, 1)
                res["margin_2d_chg_yi"] = round(cur_yi - prev2_yi, 1)
                res["margin_2d_pct"] = round(((cur_yi - prev2_yi) / max(1.0, prev2_yi)) * 100.0, 2)
                res["margin_30d_dd_pct"] = round(((hi30_yi - cur_yi) / max(1.0, hi30_yi)) * 100.0, 2)
                res["margin_src"], mgn_ok = f"FinMind信用曲線({df_mm['date'].iloc[-1]})", True
    except Exception:
        pass

    if not mgn_ok:
        for d_back in range(0, 5):
            chk_dt = now_tw - datetime.timedelta(days=d_back)
            if chk_dt.weekday() >= 5: continue
            try:
                r_ms = requests.get(f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={chk_dt.strftime('%Y%m%d')}&selectType=MS&response=json", headers=headers, timeout=5, verify=False)
                if r_ms.status_code == 200:
                    tables = r_ms.json().get("tables", [])
                    if tables and tables[0].get("data"):
                        for row in tables[0]["data"]:
                            if "融資金額" in str(row[0]):
                                prev_k, cur_k = float(str(row[4]).replace(",", "").strip()), float(str(row[5]).replace(",", "").strip())
                                if cur_k > 100000:
                                    res["margin_balance_yi"] = round(cur_k / 1e5, 1)
                                    res["margin_chg_yi"] = round((cur_k - prev_k) / 1e5, 1)
                                    res["margin_2d_chg_yi"] = res["margin_chg_yi"]
                                    res["margin_2d_pct"] = round(((cur_k - prev_k) / max(1.0, prev_k)) * 100.0, 2)
                                    res["margin_src"], mgn_ok = f"證交所RWD({chk_dt.strftime('%Y-%m-%d')})", True
                                    break
                        if mgn_ok: break
            except Exception:
                continue
    return res
