import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import datetime, pytz, requests, urllib3, io, os, json, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed

from config_events import (
    get_tw_now, get_volume_multiplier, parse_roc_or_iso_date, is_zombie_stock_tiered,
    MSCI_CACHE_FILE, TDCC_PREV_CACHE_FILE, MSCI_DEFAULT_2026_11, MSCI_THEME_BELLWETHERS,
    GRANARY_1_CSP_COMMON, GRANARY_2_POWER_THERMAL, GRANARY_3_EDGE_MEMORY,
    POLICY_DEFENSE_ARMOR, OLD_GRANARY_ODM_WATCH, ODM_Q3_OFFICIAL_BASE,
    CORE_WATCHLIST, FINANCIAL_BLUE_CHIPS, CONSTRUCTION_BLACKLIST, CORE_PORTFOLIO_16,
    ACTIVE_CB_BASE_SET, MARGIN_DILUTION_SET, MARGIN_EXPANSION_SET, US_SECTOR_DEFINITIONS,
    evaluate_granary_and_anti_trap, format_inst_chip_tag, classify_revenue_note_quality
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# 1. 處置股、MSCI 動態池與即時報價 (Layer 1 基礎工具)
# ==========================================
@st.cache_data(ttl=1800, show_spinner=False)
def fetch_twse_disposition_codes():
    disp_map = {"3189": "⚠️ MSCI 觀察期(11/10~11/16)曾列處置股，本輪恐延後轉入標準指數"}
    try:
        r = requests.get("https://www.twse.com.tw/rwd/zh/announcement/punish?response=json", headers={"User-Agent": "Mozilla/5.0"}, timeout=5, verify=False)
        if r.status_code == 200:
            for row in r.json().get("data", []):
                if len(row) >= 7 and len(str(row[2]).strip()) == 4 and str(row[2]).strip().isdigit():
                    disp_map[str(row[2]).strip()] = f"🚨 現行處置股 ({str(row[6]).strip()})"
    except Exception:
        pass
    return disp_map

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
    queries = ["tse_t00.tw"] + [f"{'tse' if m == 'TW' else 'otc'}_{c}.tw" for c, m in all_pairs]
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
    fx_v8 = fetch_yahoo_v8_realtime("TWD=X", decimals=3)
    if fx_v8 and fx_v8["close"] > 10.0:
        rt_dict["USDTWD"] = fx_v8
    return rt_dict

@st.cache_data(ttl=15, show_spinner=False)
def fetch_mis_realtime_core16():
    return fetch_mis_realtime_batch(tuple(CORE_PORTFOLIO_16))

@st.cache_data(ttl=180, show_spinner=False)
def fetch_market_prices_fast():
    """Layer 1 秒開：FMTQIK(含每日成交金額) ＋ MI_INDEX即時廣度 ＋ 全上市櫃收盤"""
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
                                    "pe": np.nan, "cum_yoy": None, "fini_lots": 0, "sitc_lots": 0, "sector": "",
                                    "tdcc_1000": None, "tdcc_400_delta": None, "tdcc_1000_delta": None, "day_trade_str": "N/A (交由淘金客聯網查核)"
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
    breadth = {"limit_up": lim_up, "limit_down": lim_dn, "up_cnt": up_cnt, "dn_cnt": dn_cnt, "adv_ratio_pct": adv_ratio, "src": "上市櫃收盤彙整"}

    if mi_breadth and (mi_breadth["limit_up"] > lim_up or abs(mi_breadth["adv_ratio_pct"] - adv_ratio) >= 12.0):
        breadth = {
            "limit_up": mi_breadth["limit_up"], "limit_down": mi_breadth["limit_down"],
            "up_cnt": mi_breadth["up_cnt"], "dn_cnt": mi_breadth["dn_cnt"],
            "adv_ratio_pct": mi_breadth["adv_ratio_pct"], "src": f"證交所MI_INDEX即時({mi_breadth['date_iso']})"
        }
    return price_dict, twii_fmtqik, breadth

@st.cache_data(ttl=120, show_spinner=False)
def fetch_us_sectors_and_leaders():
    all_syms = ["%5EVIX", "%5ESOX", "%5ETNX", "%5EGSPC", "%5EIXIC", "%5EDJI", "NQ=F", "SGOV"]
    for etf_sym, _, leaders in US_SECTOR_DEFINITIONS:
        all_syms.append(etf_sym)
        for l_sym, _ in leaders:
            if l_sym not in all_syms: all_syms.append(l_sym)
    quotes_map = {}
    with ThreadPoolExecutor(max_workers=12) as executor:
        fut_map = {executor.submit(fetch_yahoo_v8_realtime, sym, 3): sym for sym in all_syms}
        for fut in as_completed(fut_map):
            res_q = fut.result()
            if res_q: quotes_map[fut_map[fut]] = res_q
    tnx_q = quotes_map.get("%5ETNX", {})
    tnx_raw = tnx_q.get("close", 4.92)
    tnx_val = round(tnx_raw / 10.0, 2) if tnx_raw > 15.0 else round(tnx_raw, 2)
    tnx_chg = tnx_q.get("chg_pct", 0.0)
    m_state = quotes_map.get("TSM", {}).get("mkt_state", "CLOSED")
    nq_chg = quotes_map.get("NQ=F", {}).get("chg_pct", 0.0)
    us_session_tag = "🟢【美股盤中即時交易中】" if m_state == "REGULAR" else (f"🌅【美股收盤＋盤前電子盤 (NQ {nq_chg:+.2f}%)】" if m_state in ["PRE", "PREPRE"] else f"🏁【美股正規收盤 ＋ 🌙盤後/那指期 (NQ {nq_chg:+.2f}%)】")
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
        "session_tag": us_session_tag, "quotes": quotes_map, "tnx_val": tnx_val, "tnx_chg": tnx_chg, "nq_chg": nq_chg,
        "top_up_sectors": [x["summary"] for x in sector_list[:3]],
        "bot_dn_sectors": [x["summary"] for x in sorted(sector_list, key=lambda x: x["chg_pct"])[:2]]
    }

# ==========================================
# 2. 期貨雙口徑 ΔOI、小台散戶、嚴格同日價差與 45 日大盤融資曲線
# ==========================================
@st.cache_data(ttl=120, show_spinner=False)
def fetch_auto_derivatives_and_margin_v63(twii_now: float, twii_date_iso: str):
    headers = {"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"}
    now_tw = get_tw_now()
    res = {
        "fini_tx_oi": 0, "fini_tx_oi_delta": 0, "fini_tx_chg": 0,
        "sitc_tx_oi": 0, "sitc_tx_chg": 0, "dealer_tx_oi": 0, "dealer_tx_chg": 0, "real_fini_oi": 0,
        "fini_mtx_oi": 0, "fini_mtx_chg": 0, "sitc_mtx_oi": 0, "sitc_mtx_chg": 0,
        "dealer_mtx_oi": 0, "dealer_mtx_chg": 0, "retail_mtx_oi": 0, "retail_mtx_chg": 0,
        "consec_cover_days": 0, "cover_3d_sum": 0, "fut_src": "自動讀取中", "fut_date": "",
        "tx_close": 0.0, "basis_pts": 0.0, "basis_valid": False, "basis_src": "待對日同步",
        "pc_ratio": 80.0, "warrant_hedge_yi": 0.0, "twse_date": "",
        "margin_balance_yi": 0.0, "margin_chg_yi": 0.0, "margin_2d_chg_yi": 0.0, "margin_2d_pct": 0.0,
        "margin_30d_dd_pct": 0.0, "margin_src": "自動讀取中"
    }

    # A. PCR
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

    # B. 自營商權證避險 (BFI82U)
    for d_back in range(0, 6):
        chk_d = (now_tw - datetime.timedelta(days=d_back)).strftime("%Y%m%d")
        try:
            r_bfi = requests.get(f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate={chk_d}&type=day&response=json", headers=headers, timeout=5, verify=False)
            if r_bfi.status_code == 200 and len(r_bfi.json().get("data", [])) >= 3:
                for row in r_bfi.json()["data"]:
                    if "避險" in str(row[0]):
                        res["warrant_hedge_yi"] = round(float(str(row[3]).replace(",", "").strip()) / 1e8, 2)
                        res["twse_date"] = chk_d; break
                if res["twse_date"]: break
        except Exception:
            continue

    # C. 大台＋小台三大法人 (OI、日盤交易與真實 ΔOI)
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
                                elif "投信" in ident and d_cur == latest_d:
                                    res["sitc_tx_chg"], res["sitc_tx_oi"] = tr, oi
                                elif "自營" in ident and d_cur == latest_d:
                                    res["dealer_tx_chg"], res["dealer_tx_oi"] = tr, oi
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
                        res["consec_cover_days"] = consec
                        res["cover_3d_sum"] = int(sum(oi_deltas[-3:])) if oi_deltas else 0
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
        except Exception:
            pass

    # D. 嚴格同日校準台指期與期現貨價差
    tx_ok = False
    try:
        r_mis = requests.post(
            "https://mis.taifex.com.tw/futures/api/getQuoteList",
            json={"MarketType": "0", "SymbolType": "F", "KindID": "1", "CID": "TXF", "ExpireMonth": "", "RowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"},
            headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}, timeout=4, verify=False
        )
        if r_mis.status_code == 200:
            q_list = r_mis.json().get("RtData", {}).get("QuoteList", [])
            near_rows = [q for q in q_list if str(q.get("SymbolID", "")).startswith("TXF") and ("/" not in str(q.get("SymbolID", ""))) and str(q.get("CLastPrice", "")).replace(",", "").replace(".", "").isdigit()]
            if near_rows:
                c_tx = float(str(near_rows[0]["CLastPrice"]).replace(",", ""))
                q_date_iso = parse_roc_or_iso_date(near_rows[0].get("CDate", ""))
                if c_tx > 5000 and (not q_date_iso or q_date_iso == twii_date_iso):
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

# ==========================================
# 3. 技術指標、四軌落底偵測器與動態三情境預測引擎
# ==========================================
def detect_4track_bottom_signals(o_arr, h_arr, l_arr, c_arr, v_arr, limit_up_cnt: int, adv_ratio_pct: float, vol_mult_use: float = 1.0, fmtqik_val_ratio: float = 0.0) -> dict:
    out = {"passed": False, "track_hit": "未觸發", "detail": "尚無底部反轉確認型態", "precursor": "", "vol_ratio": 1.0, "sh_ratio": 0.0}
    try:
        if len(c_arr) < 22: return out
        o0, h0, l0, c0 = float(o_arr[-1]), float(h_arr[-1]), float(l_arr[-1]), float(c_arr[-1])
        o1, h1, l1, c1 = float(o_arr[-2]), float(h_arr[-2]), float(l_arr[-2]), float(c_arr[-2])
        o2, c2 = float(o_arr[-3]), float(c_arr[-3])
        v0, v1 = float(v_arr[-1]) * vol_mult_use, max(1.0, float(v_arr[-2]))
        pos_v = [float(x) for x in v_arr[-21:-1] if float(x) > 0]
        vol_20ma = max(1.0, float(np.mean(pos_v))) if pos_v else 1.0
        vol_ratio = round(v0 / vol_20ma, 2) if v0 > 0 else (round(fmtqik_val_ratio, 2) if fmtqik_val_ratio > 0 else 1.0)
        chg_1d = ((c0 - c1) / max(1.0, c1)) * 100.0
        day_rng, body = max(1.0, h0 - l0), max(10.0, abs(c0 - o0))
        sh_ratio = round(max(0.0, min(c0, o0) - l0) / body, 2)
        up_sh_ratio = round(max(0.0, h0 - max(c0, o0)) / body, 2)
        close_pos = (c0 - l0) / day_rng
        close_above_low_pct = ((c0 - l0) / max(1.0, l0)) * 100.0
        out["vol_ratio"], out["sh_ratio"] = vol_ratio, sh_ratio

        hit_2a = (vol_ratio >= 1.25) and (sh_ratio >= 1.5) and (close_above_low_pct >= 1.5)
        is_bull_engulf = (c1 < o1) and (c0 > o0) and (c0 >= o1) and (o0 <= c1 * 1.003)
        is_strong_red = (c0 > o0) and (chg_1d >= 2.5) and (close_pos >= 0.75)
        is_morning_star = (c2 < o2 * 0.985) and (abs(c1 - o1) <= abs(c2 - o2) * 0.45) and (c0 > o0) and (c0 >= (o2 + c2) / 2.0)
        is_gap_up_rev = (l0 > h1 * 1.002) and (c0 > o0)
        hit_2b = (vol_ratio >= 1.20 or (v0 > 0 and v0 > v1)) and (is_bull_engulf or is_strong_red or is_morning_star or is_gap_up_rev)
        hit_2c = ((chg_1d >= 4.0) and ((limit_up_cnt >= 100) or (adv_ratio_pct >= 85.0))) or ((chg_1d >= 6.0) and (l0 >= c1 * 1.03))

        recent_15_lows = l_arr[-15:]
        low_idx = int(np.argmin(recent_15_lows))
        days_since_low = (len(recent_15_lows) - 1) - low_idx
        min_l15 = float(recent_15_lows[low_idx])
        hit_ftd = (4 <= days_since_low <= 10) and (chg_1d >= 1.7) and (v0 > 0 and v0 > v1)
        hit_wyckoff_st = (days_since_low >= 3) and (l0 >= min_l15 * 0.992) and (l0 <= min_l15 * 1.035) and (vol_ratio < 0.75) and (c0 > o0)
        hit_2d = hit_ftd or hit_wyckoff_st

        near_20d_low = (l0 <= float(np.min(l_arr[-20:])) * 1.04)
        if near_20d_low and (abs(c0 - o0) <= day_rng * 0.16): out["precursor"] = "🟡 底部前哨：低檔十字線煞車（待隔日紅K確認）"
        elif near_20d_low and (up_sh_ratio >= 1.5) and (vol_ratio >= 1.15): out["precursor"] = "🟡 底部前哨：低檔帶量沖天炮試盤（禁追，待吞噬上影確認）"

        hits = []
        if hit_2c: hits.append(f"2c無量鎖漲停/廣度豁免(單日+{chg_1d:.1f}%・漲停{limit_up_cnt}家/上漲比{adv_ratio_pct:.0f}%)")
        if hit_2a: hits.append(f"2a爆量神針探底(量{vol_ratio:.2f}x・下影{sh_ratio:.1f}x)")
        if hit_2b:
            lbl_2b = "多頭吞噬" if is_bull_engulf else ("晨星反轉" if is_morning_star else ("跳空反轉" if is_gap_up_rev else "帶量長紅"))
            hits.append(f"2b{lbl_2b}(量{vol_ratio:.2f}x・漲{chg_1d:+.1f}%)")
        if hit_2d: hits.append(f"2d{'歐尼爾FTD確認日' if hit_ftd else '威科夫縮量右腳'}(低點後第{days_since_low}日)")

        if hits:
            out["passed"] = True; out["track_hit"] = " ＋ ".join(hits); out["detail"] = out["track_hit"]
        else:
            out["detail"] = f"量比 {vol_ratio:.2f}x ｜ 下影/實體 {sh_ratio:.2f}x ｜ 漲停 {limit_up_cnt}家/上漲比 {adv_ratio_pct:.0f}%"
    except Exception:
        pass
    return out

def estimate_foreign_short_cost_and_target(highs, lows, closes, volumes):
    if len(closes) < 20:
        curr = float(closes[-1]) if len(closes) > 0 else 40000.0
        return round(curr * 1.06, 0), round(curr * 1.02, 0), round(curr * 0.98, 0)
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

def compute_kdj(highs, lows, closes, n=9):
    try:
        k_val, d_val, j_val, k_prev, d_prev, j_prev = 50.0, 50.0, 50.0, 50.0, 50.0, 50.0
        for i in range(len(closes)):
            k_prev, d_prev, j_prev = k_val, d_val, j_val
            s_i = max(0, i - n + 1)
            hh, ll = np.nanmax(highs[s_i:i+1]), np.nanmin(lows[s_i:i+1])
            rsv = ((closes[i] - ll) / (hh - ll) * 100.0) if (pd.notna(hh) and pd.notna(ll) and hh > ll) else 50.0
            k_val = (2.0 / 3.0) * k_val + (1.0 / 3.0) * rsv
            d_val = (2.0 / 3.0) * d_val + (1.0 / 3.0) * k_val
            j_val = 3.0 * k_val - 2.0 * d_val
        return round(k_val, 1), round(d_val, 1), round(j_val, 1), round(j_prev, 1), (k_val >= d_val and k_prev <= d_prev + 3.0)
    except Exception:
        return 50.0, 50.0, 50.0, 50.0, False

def compute_dmi_and_atr(highs, lows, closes, n=14):
    try:
        if len(closes) < n + 5: return 25.0, 25.0, 20.0, 20.0, False, np.nan
        h, l, c = pd.Series(highs, dtype=float), pd.Series(lows, dtype=float), pd.Series(closes, dtype=float)
        up_m, dn_m = h.diff(), -l.diff()
        p_dm = np.where((up_m > dn_m) & (up_m > 0), up_m, 0.0)
        m_dm = np.where((dn_m > up_m) & (dn_m > 0), dn_m, 0.0)
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1.0/n, adjust=False).mean()
        pdi = 100.0 * pd.Series(p_dm).ewm(alpha=1.0/n, adjust=False).mean() / atr.replace(0, np.nan)
        mdi = 100.0 * pd.Series(m_dm).ewm(alpha=1.0/n, adjust=False).mean() / atr.replace(0, np.nan)
        dx = 100.0 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
        adx = dx.fillna(0.0).ewm(alpha=1.0/n, adjust=False).mean()
        pdi_n, mdi_n = round(float(pdi.iloc[-1]), 1), round(float(mdi.iloc[-1]), 1)
        pdi_p, mdi_p = float(pdi.iloc[-2]), float(mdi.iloc[-2])
        adx_n, adx_p = round(float(adx.iloc[-1]), 1), round(float(adx.iloc[-2]), 1)
        dmi_b = (pdi_n > mdi_n) and (adx_n > adx_p) and ((pdi_p <= mdi_p + 4.0) or (pdi_n - mdi_n > pdi_p - mdi_p))
        atr_pct = round((float(atr.iloc[-1]) / float(c.iloc[-1])) * 100.0, 2) if (pd.notna(atr.iloc[-1]) and float(c.iloc[-1]) > 0) else np.nan
        return pdi_n, mdi_n, adx_n, adx_p, bool(dmi_b), atr_pct
    except Exception:
        return np.nan, np.nan, np.nan, np.nan, False, np.nan

def classify_dmi_direction(pdi, mdi, adx, j_val):
    try:
        if any(pd.isna(x) for x in [pdi, mdi, adx]): return "N/A", False
        if adx > 25.0 and pdi > mdi: return ("多頭趨勢確立(強勢鈍化)" if (pd.notna(j_val) and j_val > 100.0) else "多頭趨勢確立"), False
        if adx > 25.0 and mdi > pdi: return "空頭加速（防高ADX假多頭）", True
        if adx < 20.0: return "盤整無趨勢", False
        return ("趨勢萌芽(偏多)" if pdi >= mdi else "趨勢萌芽(偏空)"), False
    except Exception:
        return "N/A", False

def compute_obv_trend(closes, volumes):
    try:
        if len(closes) < 22 or len(volumes) < 22: return "N/A", False
        diff_c = np.diff(closes, prepend=closes[0])
        obv = np.cumsum(np.where(diff_c > 0, volumes, np.where(diff_c < 0, -volumes, 0.0)))
        s5, s20 = float(obv[-1] - obv[-6]), float(obv[-1] - obv[-21])
        if (float(np.max(closes[-5:])) >= float(np.max(closes[-20:])) * 0.995) and (float(np.max(obv[-5:])) < float(np.max(obv[-20:])) * 0.995 or s5 <= 0.0):
            return "⚠️OBV背離", True
        if s5 > 0 and s20 > 0: return "📈雙斜率向上", False
        if s5 > 0 and s20 <= 0: return "🌱短線翻揚", False
        if s5 <= 0 and s20 > 0: return "⚖️高檔換手", False
        return "📉斜率向下", False
    except Exception:
        return "N/A", False

def compute_dynamic_pure_stock_maint(twii_now, ma20, ma60):
    cost_anchor = 0.70 * max(1.0, ma20) + 0.30 * max(1.0, ma60)
    return round(max(115.0, min(195.0, 166.7 * ((twii_now / max(1.0, cost_anchor)) ** 1.95))), 1)

def compute_dynamic_market_forecast(fleet: dict, radar: dict, vix_now: float, sox_chg: float, upcoming_ev_rows: list[dict], fini_oi: int, fini_oi_delta: int) -> dict:
    """全浮動未來 1～4 週大盤三情境機率與路徑推演引擎（零寫死點位）"""
    tw = float(fleet["twii_now"])
    ma5, ma10, ma20, ma60, ma240 = fleet["ma5"], fleet["ma10"], fleet["ma20"], fleet["ma60"], fleet["ma240"]
    m_ma20, m_ma36 = fleet["m_ma20"], fleet["m_ma36"]
    low20 = float(fleet.get("twii_20d_low", tw * 0.90))
    atr_pts = max(tw * 0.012, float(fleet.get("atr_14_pts", tw * 0.022)))
    exit_t1, exit_t2 = fleet["etf_metrics"]["00400A"]["exit_t1"], fleet["etf_metrics"]["00400A"]["exit_t2"]

    bull_score = sum([tw >= ma5, tw >= ma10, tw >= ma240, radar["b_count"] >= 3, fini_oi_delta > 0, (vix_now < 25.0 or sox_chg > 2.0)])
    bear_score = (2 if fini_oi < -80000 else (1 if fini_oi < -60000 else 0)) + sum([tw < ma20, radar["is_policy_escape_wave"], vix_now >= 30.0])

    if radar["master_state"] in ["RED_LOCKED", "YELLOW"]:
        p_bull = int(np.clip(18 + bull_score * 4 - bear_score * 2, 15, 40))
        p_bear = int(np.clip(18 + bear_score * 4 - bull_score * 2, 15, 40))
        p_base = max(25, 100 - p_bull - p_bear)
        p_base += (100 - (p_base + p_bull + p_bear))

        res_mid = round(max(tw + atr_pts * 0.9, ma20), 0)
        res_hi = round(max(res_mid + atr_pts * 1.4, ma60), 0)
        sup_rf = round(max(low20 * 1.015, min(tw - atr_pts * 1.2, ma5 * 0.985)), 0)
        sup_dp = round(max(m_ma36, min(low20 * 0.97, m_ma20)), 0)

        path_base = [tw, res_mid, sup_rf, round((res_mid + res_hi) * 0.5, 0)]
        path_bull = [tw, round(max(ma20, tw + atr_pts * 1.5), 0), round(max(ma60 * 0.98, ma20 + atr_pts * 1.8), 0), round(max(ma60 * 1.03, res_hi + atr_pts), 0)]
        path_bear = [tw, round(min(ma5, tw - atr_pts * 0.8), 0), round(max(m_ma20, low20), 0), sup_dp]

        mode_label = "股災修正／築底反彈三情境預測（100% 動態浮動）"
        table_rows = [
            {"預測情境（圖中線色）": "🟡 基準主路徑：遇壓震盪 ➔ 打 W 底右腳", "動態機率": f"{p_base}%",
             "未來 1～4 週預測路徑與目標區": f"先上測月線反壓 {res_mid:,.0f}點 ➔ 遇重大事件量縮回測 {sup_rf:,.0f}點 打右腳 ➔ 再修復攻 {path_base[3]:,.0f}點",
             "確認／轉折分界": f"上檔月線 {ma20:,.0f} 反壓沉重，外資空單 ({fini_oi:+,}) 未大幅回補前易反覆築底",
             "四水庫實戰操作": f"彈近 {ma20:,.0f} 執行 00400A ({exit_t1}~{exit_t2}元) 分批退場轉 00985B；右腳縮量不破前低且落底達4/5再動用水庫四"},
            {"預測情境（圖中線色）": "🟢 樂觀路徑：軋空 V 轉突破月線", "動態機率": f"{p_bull}%",
             "未來 1～4 週預測路徑與目標區": f"連 3 日站穩 10MA({ma10:,.0f}) ➔ 帶量突破月線({ma20:,.0f}) ➔ 軋空直攻季線 {path_bull[2]:,.0f}～{path_bull[3]:,.0f}點",
             "確認／轉折分界": "須同時滿足「連 3 日站穩 10MA ＋ 外資空單連 3 日回補逾萬口 ＋ 美股續強」",
             "四水庫實戰操作": "日月光 300 股短單沿 5MA 移動停利續抱不預設高點；靜待解鎖後分批切入新穀倉1與 MSCI 主流"},
            {"預測情境（圖中線色）": "🔴 保守路徑：政策逃命波失守再探底", "動態機率": f"{p_bear}%",
             "未來 1～4 週預測路徑與目標區": f"反彈不過 10MA/月線即轉弱 ➔ 跌破年線({ma240:,.0f})與5MA({ma5:,.0f}) ➔ 回測前低 {low20:,.0f}～月20MA {m_ma20:,.0f}點",
             "確認／轉折分界": f"若數據轉鷹（PCE/FOMC）且收盤失守 5MA({ma5:,.0f}) 與年線({ma240:,.0f})",
             "四水庫實戰操作": "嚴格執行獨立短單停利／停損收回台幣現金，死抱水庫二傳家寶與 00985B 避險等待終極買點"}
        ]
    else:
        p_base = int(np.clip(45 + bull_score * 3 - bear_score * 3, 35, 60))
        p_bull = int(np.clip(25 + bull_score * 2, 20, 40))
        p_bear = max(15, 100 - p_base - p_bull)
        p_base += (100 - p_base - p_bull - p_bear)
        path_base = [tw, round(tw + atr_pts * 1.2, 0), round(tw + atr_pts * 0.8, 0), round(tw + atr_pts * 2.5, 0)]
        path_bull = [tw, round(tw + atr_pts * 2.0, 0), round(tw + atr_pts * 3.5, 0), round(tw + atr_pts * 5.0, 0)]
        path_bear = [tw, round(ma10, 0), round(ma20, 0), round(min(ma20, ma60), 0)]
        mode_label = "多頭主升／震盪推進三情境預測（100% 動態浮動）"
        table_rows = [
            {"預測情境（圖中線色）": "🟡 基準主路徑：沿 5/10MA 震盪推升", "動態機率": f"{p_base}%", "未來 1～4 週預測路徑與目標區": f"沿 5MA({ma5:,.0f}) 換手墊高至 {path_base[1]:,.0f}～{path_base[3]:,.0f}點", "確認／轉折分界": f"日 10MA({ma10:,.0f}) 未破維持多頭慣性", "四水庫實戰操作": "啟動多頭股息衛星循環，嚴守破 10/20MA 或硬停損 -7%~-10%"},
            {"預測情境（圖中線色）": "🟢 樂觀路徑：外資回補軋空加速", "動態機率": f"{p_bull}%", "未來 1～4 週預測路徑與目標區": f"帶量突破前高挑戰 {path_bull[2]:,.0f}～{path_bull[3]:,.0f}點", "確認／轉折分界": "外資現貨連買＋成交量維持 20MA 之上", "四水庫實戰操作": "抱緊新穀倉1與傳家寶，高檔 BIAS10>12% 禁追高"},
            {"預測情境（圖中線色）": "🔴 保守路徑：高檔過熱回測月線洗盤", "動態機率": f"{p_bear}%", "未來 1～4 週預測路徑與目標區": f"失守 5MA 回測月線 {ma20:,.0f}～季線 {ma60:,.0f}點", "確認／轉折分界": "爆量黑K跌破 10MA 或觸發黃燈前哨", "四水庫實戰操作": "優先收網多頭衛星股，保留現金轉入水庫三防禦"}
        ]

    ev_marks = [{"days": ev["_days"], "label": f"{ev['日期'][5:]} {ev['_short'][:7]}"} for ev in upcoming_ev_rows if 1 <= ev["_days"] <= 30][:3]
    return {"mode_label": mode_label, "probs": {"base": p_base, "bull": p_bull, "bear": p_bear}, "paths": {"base": path_base, "bull": path_bull, "bear": path_bear}, "events_on_axis": ev_marks, "table_rows": table_rows}

# ==========================================
# 4. Layer 1 秒開主流程
# ==========================================
@st.cache_data(ttl=600, show_spinner=False)
def download_fleet_history():
    tickers = ["^TWII", "^GSPC", "^SOX", "^VIX", "TWD=X", "00400A.TW", "00985B.TW", "00713.TW", "00631L.TW", "3711.TW", "AVGO", "NVDA", "SGOV"]
    try: return yf.download(tickers, period="5y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=120, show_spinner=False)
def download_fleet_60m():
    try: return yf.download(["^TWII"], period="1mo", interval="60m", progress=False, group_by="ticker", auto_adjust=False)
    except Exception: return pd.DataFrame()

def fetch_and_analyze_fleet_fast():
    rt_quotes = fetch_mis_realtime_core16()
    fast_prices, twii_fmtqik, breadth = fetch_market_prices_fast()
    data, data_60m = download_fleet_history(), download_fleet_60m()
    now_tw = get_tw_now()
    today_str = now_tw.strftime("%Y-%m-%d")
    is_open_today = (now_tw.weekday() < 5) and ((now_tw.hour * 60 + now_tw.minute) >= 540)
    vol_mult_now, vol_note = get_volume_multiplier()

    twii_now, twii_chg_pct = (twii_fmtqik["close"] or 40122.3), (twii_fmtqik["chg_pct"] if twii_fmtqik["close"] > 0 else 0.0)
    twii_open = twii_high = twii_low = twii_prev_c = twii_now
    ma5 = ma10 = ma20 = ma60 = ma120 = ma240 = m_ma20 = m_ma36 = twii_now
    twii_30d_high = twii_60d_high = twii_now
    twii_20d_low = twii_now * 0.90
    twii_30d_dd_pct, twii_consec_above_ma10, twii_20d_ret = 0.0, 0, 0.0
    twii_date_iso = twii_fmtqik.get("date_iso") or today_str
    date_str, quote_src = twii_date_iso, "TWSE FMTQIK / Yahoo"
    fx_now, fx_prev, fx_chg_ntd, fx_ma5, fx_ma20 = 32.85, 32.85, 0.0, 32.84, 32.65
    sp500_below_ma200, sox_dd_pct, sox_ma240 = True, 35.0, 10200.0
    k9, d9, j9, j9_prev = 50.0, 50.0, 50.0, 50.0
    k60_k, k60_d, k60_j, k60_j_prev, k60_ma5, k60_ma20 = 50.0, 50.0, 50.0, 50.0, twii_now, twii_now
    atr_14_pts, atr_14_pct = round(twii_now * 0.02, 0), 2.0
    est_short_cost, target_cover_1, target_cover_2 = round(twii_now * 1.05, 0), round(twii_now * 1.01, 0), round(twii_now * 0.97, 0)
    hist_dates, hist_closes = [], []
    bottom_4track = {"passed": False, "track_hit": "未觸發", "detail": "待計算", "precursor": "", "vol_ratio": 1.0, "sh_ratio": 0.0}

    etf_metrics = {
        "00400A": {"p": 11.09, "ma10": 11.68, "ma20": 12.43, "exit_t1": 11.68, "exit_t2": 12.43, "div_latest": 0.06, "div_ttm": 0.60},
        "00985B": {"p": 10.40, "ma20": 10.35, "div_latest": 0.053, "div_ttm": 0.596},
        "00713": {"p": 61.40, "ma20": 59.50, "div_latest": 1.25, "div_ttm": 3.81},
        "00631L": {"p": 29.00, "ma5": 28.00, "ma20": 31.50},
        "3711": {"p": 530.0, "ma5": 504.2, "ma20": 510.0},
        "AVGO": {"p": 313.55}, "NVDA": {"p": 165.06}, "SGOV": {"p": 100.64}
    }

    try:
        if not data.empty and "^TWII" in data:
            df_twii = data["^TWII"].dropna(subset=["Open", "High", "Low", "Close"]).copy()
            if len(df_twii) >= 35:
                o_tw, c_tw, h_tw, l_tw, v_tw = df_twii["Open"].values.astype(float), df_twii["Close"].values.astype(float), df_twii["High"].values.astype(float), df_twii["Low"].values.astype(float), df_twii["Volume"].values.astype(float)
                d_idx_list = [d.strftime("%Y-%m-%d") for d in df_twii.index]
                last_k_date = d_idx_list[-1]
                rt_t00 = rt_quotes.get("t00", {})
                rt_iso, fmt_iso = rt_t00.get("date_iso", ""), twii_fmtqik.get("date_iso", "")

                used_rt_today = False
                if is_open_today and rt_t00.get("close", 0) > 0 and rt_iso == today_str:
                    if last_k_date == today_str:
                        o_tw[-1] = rt_t00["open"] if rt_t00.get("open", 0) > 0 else o_tw[-1]
                        c_tw[-1], h_tw[-1], l_tw[-1] = rt_t00["close"], max(h_tw[-1], rt_t00["high"]), min(l_tw[-1], rt_t00["low"])
                    else:
                        o_tw, c_tw = np.append(o_tw, rt_t00.get("open") or rt_t00["close"]), np.append(c_tw, rt_t00["close"])
                        h_tw, l_tw, v_tw = np.append(h_tw, rt_t00["high"]), np.append(l_tw, rt_t00["low"]), np.append(v_tw, 0.0)
                        d_idx_list.append(today_str)
                    twii_date_iso, used_rt_today = today_str, True
                    date_str, quote_src = f"{today_str} {rt_t00.get('time', '')}", f"🟢 即時對時 ({rt_t00.get('time', '')})"
                else:
                    if fmt_iso > last_k_date and twii_fmtqik["close"] > 0:
                        if rt_iso == fmt_iso and rt_t00.get("high", 0) > 0:
                            o_tw, h_tw, l_tw, c_tw = np.append(o_tw, rt_t00["open"]), np.append(h_tw, rt_t00["high"]), np.append(l_tw, rt_t00["low"]), np.append(c_tw, twii_fmtqik["close"])
                        else:
                            o_tw, h_tw, l_tw, c_tw = np.append(o_tw, twii_fmtqik["close"]), np.append(h_tw, twii_fmtqik["close"]), np.append(l_tw, twii_fmtqik["close"]), np.append(c_tw, twii_fmtqik["close"])
                        v_tw = np.append(v_tw, 0.0); d_idx_list.append(fmt_iso)
                        twii_date_iso, date_str, quote_src = fmt_iso, f"{fmt_iso} 官方收盤", f"📦 證交所 FMTQIK 收盤 ({fmt_iso})"
                    elif rt_iso > last_k_date and rt_t00.get("close", 0) > 0:
                        o_tw, h_tw, l_tw, c_tw, v_tw = np.append(o_tw, rt_t00["open"]), np.append(h_tw, rt_t00["high"]), np.append(l_tw, rt_t00["low"]), np.append(c_tw, rt_t00["close"]), np.append(v_tw, 0.0)
                        d_idx_list.append(rt_iso)
                        twii_date_iso, date_str, quote_src = rt_iso, f"{rt_iso} 收盤", f"📦 Yahoo v8 收盤 ({rt_iso})"
                    else:
                        twii_date_iso, date_str, quote_src = last_k_date, f"{last_k_date} 收盤", f"📦 完整收盤 ({last_k_date})"

                fmtqik_val_ratio = 0.0
                val_map = twii_fmtqik.get("val_map", {})
                if val_map:
                    vals_list = [v for _, v in sorted(val_map.items()) if v > 0]
                    if len(vals_list) >= 3:
                        cur_v_yi = val_map.get(twii_date_iso, vals_list[-1])
                        fmtqik_val_ratio = round(cur_v_yi / max(1.0, float(np.mean(vals_list[:-1]))), 2)

                twii_open, twii_now, twii_high, twii_low = round(float(o_tw[-1]), 1), round(float(c_tw[-1]), 1), round(float(h_tw[-1]), 1), round(float(l_tw[-1]), 1)
                twii_prev_c = round(float(c_tw[-2]), 1) if len(c_tw) > 1 else twii_now
                twii_chg_pct = round(((twii_now - twii_prev_c) / max(1.0, twii_prev_c)) * 100.0, 2)
                twii_20d_ret = round(((twii_now - float(c_tw[-21])) / max(1.0, float(c_tw[-21]))) * 100.0, 2) if len(c_tw) >= 21 else 0.0
                ma5, ma10, ma20 = round(float(np.mean(c_tw[-5:])), 1), round(float(np.mean(c_tw[-10:])), 1), round(float(np.mean(c_tw[-20:])), 1)
                ma60 = round(float(np.mean(c_tw[-60:])), 1) if len(c_tw) >= 60 else ma20
                ma120 = round(float(np.mean(c_tw[-120:])), 1) if len(c_tw) >= 120 else ma60
                ma240 = round(float(np.mean(c_tw[-240:])), 1) if len(c_tw) >= 240 else ma120

                hist_dates = [d[5:] for d in d_idx_list[-40:]]
                hist_closes = [round(float(x), 1) for x in c_tw[-40:]]
                twii_20d_low = round(float(np.min(l_tw[-20:])), 1)

                ma10_ser = pd.Series(c_tw).rolling(10).mean().values
                twii_consec_above_ma10 = 0
                for ii in range(len(c_tw) - 1, max(9, len(c_tw) - 15), -1):
                    if pd.notna(ma10_ser[ii]) and c_tw[ii] >= ma10_ser[ii]: twii_consec_above_ma10 += 1
                    else: break

                twii_30d_high = round(float(np.max(h_tw[-35:])), 1)
                twii_60d_high = round(float(np.max(h_tw[-65:])), 1) if len(h_tw) >= 65 else twii_30d_high
                twii_30d_dd_pct = round(((twii_30d_high - twii_now) / max(1.0, twii_30d_high)) * 100.0, 2)

                k9, d9, j9, j9_prev, _ = compute_kdj(h_tw[-60:], l_tw[-60:], c_tw[-60:])
                _, _, _, _, _, atr_p = compute_dmi_and_atr(h_tw[-60:], l_tw[-60:], c_tw[-60:])
                if pd.notna(atr_p) and atr_p > 0:
                    atr_14_pct, atr_14_pts = round(float(atr_p), 2), round(twii_now * float(atr_p) / 100.0, 0)
                est_short_cost, target_cover_1, target_cover_2 = estimate_foreign_short_cost_and_target(h_tw, l_tw, c_tw, v_tw)

                m_closes = df_twii["Close"].resample("ME").last().dropna().values.astype(float)
                if len(m_closes) > 0:
                    m_closes[-1] = twii_now
                    m_ma20 = round(float(np.mean(m_closes[-20:])), 1) if len(m_closes) >= 20 else round(ma240 * 0.88, 1)
                    m_ma36 = round(float(np.mean(m_closes[-36:])), 1) if len(m_closes) >= 36 else round(m_ma20 * 0.90, 1)

                bottom_4track = detect_4track_bottom_signals(o_tw, h_tw, l_tw, c_tw, v_tw, breadth["limit_up"], breadth["adv_ratio_pct"], vol_mult_now if used_rt_today else 1.0, fmtqik_val_ratio)

            if "^GSPC" in data:
                c_sp = data["^GSPC"]["Close"].dropna().values.astype(float)
                if len(c_sp) >= 200: sp500_below_ma200 = bool(float(c_sp[-1]) < float(np.mean(c_sp[-200:])))

            if "^SOX" in data:
                df_sx = data["^SOX"].dropna(subset=["High", "Close"])
                if len(df_sx) >= 60:
                    c_sx, h_sx = df_sx["Close"].values.astype(float), df_sx["High"].values.astype(float)
                    sox_hi = float(np.max(h_sx[-150:]))
                    sox_dd_pct = round(((sox_hi - float(c_sx[-1])) / max(1.0, sox_hi)) * 100.0, 2)
                    sox_ma240 = round(float(np.mean(c_sx[-240:])), 2) if len(c_sx) >= 240 else round(float(np.mean(c_sx)), 2)

            if "TWD=X" in data:
                c_fx = data["TWD=X"]["Close"].dropna().values.astype(float)
                if len(c_fx) >= 20:
                    if "USDTWD" in rt_quotes and rt_quotes["USDTWD"]["close"] > 10.0:
                        fx_now, fx_prev = round(float(rt_quotes["USDTWD"]["close"]), 3), round(float(rt_quotes["USDTWD"]["prev_close"]), 3)
                        c_fx[-1] = fx_now
                    else:
                        fx_now, fx_prev = round(float(c_fx[-1]), 3), round(float(c_fx[-2]), 3)
                    fx_chg_ntd = round(fx_now - fx_prev, 3)
                    fx_ma5, fx_ma20 = round(float(np.mean(c_fx[-5:])), 3), round(float(np.mean(c_fx[-20:])), 3)

            for sym_e, code_e in [("00400A.TW", "00400A"), ("00985B.TW", "00985B"), ("00713.TW", "00713"), ("00631L.TW", "00631L"), ("3711.TW", "3711"), ("AVGO", "AVGO"), ("NVDA", "NVDA"), ("SGOV", "SGOV")]:
                if sym_e in data:
                    df_e = data[sym_e].dropna(subset=["Close", "High", "Low"])
                    if len(df_e) >= 5:
                        c_e = df_e["Close"].values.astype(float)
                        rt_e = rt_quotes.get(code_e, {}).get("close", 0.0) if is_open_today else 0.0
                        if rt_e and rt_e > 0: c_e[-1] = float(rt_e)
                        elif code_e in fast_prices and fast_prices[code_e].get("close", 0) > 0: c_e[-1] = float(fast_prices[code_e]["close"])
                        p_e = round(float(c_e[-1]), 2)
                        etf_metrics[code_e]["p"] = p_e
                        if code_e == "00400A" and len(c_e) >= 20:
                            m10_e, m20_e = round(float(np.mean(c_e[-10:])), 2), round(float(np.mean(c_e[-20:])), 2)
                            t1_val = round(max(p_e * 1.04, min(m10_e, p_e * 1.10)), 2)
                            t2_val = round(max(t1_val + 0.40, min(m20_e, p_e * 1.18)), 2)
                            etf_metrics["00400A"].update({"ma10": m10_e, "ma20": m20_e, "exit_t1": t1_val, "exit_t2": t2_val})
                        elif code_e in ["00985B", "00713", "00631L", "3711"] and len(c_e) >= 20:
                            etf_metrics[code_e]["ma5"] = round(float(np.mean(c_e[-5:])), 2)
                            etf_metrics[code_e]["ma20"] = round(float(np.mean(c_e[-20:])), 2)
    except Exception:
        pass

    try:
        if not data_60m.empty and "^TWII" in data_60m:
            df_60 = data_60m["^TWII"].dropna(subset=["High", "Low", "Close"]).copy()
            if len(df_60) >= 20:
                c60, h60, l60 = df_60["Close"].values.astype(float), df_60["High"].values.astype(float), df_60["Low"].values.astype(float)
                if abs(c60[-1] - twii_now) > 1.0: c60[-1], h60[-1], l60[-1] = twii_now, max(h60[-1], twii_now), min(l60[-1], twii_now)
                k60_ma5, k60_ma20 = round(float(np.mean(c60[-5:])), 1), round(float(np.mean(c60[-20:])), 1)
                k60_k, k60_d, k60_j, k60_j_prev, _ = compute_kdj(h60[-60:], l60[-60:], c60[-60:])
    except Exception:
        pass

    deriv = fetch_auto_derivatives_and_margin_v63(twii_now, twii_date_iso)
    deriv["stock_only_maint_pct"] = compute_dynamic_pure_stock_maint(twii_now, ma20, ma60)
    deriv["fx_now"], deriv["fx_prev"], deriv["fx_chg_ntd"], deriv["fx_ma5"], deriv["fx_ma20"] = fx_now, fx_prev, fx_chg_ntd, fx_ma5, fx_ma20
    deriv["est_short_cost"], deriv["target_cover_1"], deriv["target_cover_2"] = est_short_cost, target_cover_1, target_cover_2
    deriv["short_profit_pts"] = round(est_short_cost - twii_now, 0)
    deriv["short_profit_pct"] = round(((est_short_cost - twii_now) / max(1.0, est_short_cost)) * 100.0, 2)

    return {
        "twii_open": twii_open, "twii_prev_c": twii_prev_c, "twii_now": twii_now, "twii_chg_pct": twii_chg_pct,
        "twii_high": twii_high, "twii_low": twii_low, "twii_20d_ret": twii_20d_ret, "twii_20d_low": twii_20d_low,
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60, "ma120": ma120, "ma240": ma240,
        "m_ma20": m_ma20, "m_ma36": m_ma36,
        "bias_5": round((twii_now - ma5) / max(1.0, ma5) * 100.0, 2),
        "bias_10": round((twii_now - ma10) / max(1.0, ma10) * 100.0, 2),
        "bias_20": round((twii_now - ma20) / max(1.0, ma20) * 100.0, 2),
        "k9": k9, "d9": d9, "j9": j9, "j9_prev": j9_prev,
        "k60_k": k60_k, "k60_d": k60_d, "k60_j": k60_j, "k60_j_prev": k60_j_prev, "k60_ma5": k60_ma5, "k60_ma20": k60_ma20,
        "atr_14_pts": atr_14_pts, "atr_14_pct": atr_14_pct, "val_yi": twii_fmtqik.get("val_yi", 0.0),
        "hist_dates": hist_dates, "hist_closes": hist_closes,
        "twii_30d_high": twii_30d_high, "twii_60d_high": twii_60d_high, "twii_30d_dd_pct": twii_30d_dd_pct,
        "twii_consec_above_ma10": twii_consec_above_ma10,
        "sp500_below_ma200": sp500_below_ma200, "sox_dd_pct": sox_dd_pct, "sox_ma240": sox_ma240,
        "breadth": breadth, "bottom_4track": bottom_4track,
        "twii_date_iso": twii_date_iso, "date_str": date_str, "quote_src": quote_src, "vol_note": vol_note,
        "deriv": deriv, "etf_metrics": etf_metrics, "rt_quotes": rt_quotes, "fast_prices": fast_prices
    }

# ==========================================
# 5. Layer 2 ＆ Layer 3 按需啟動：個股掃描、防墊料、比價與月營收
# ==========================================
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_active_cb_codes():
    cb_codes = set(ACTIVE_CB_BASE_SET)
    for url in ["https://www.tpex.org.tw/openapi/v1/tpex_cb_daily_close_quotes", "https://www.tpex.org.tw/web/bond/tradeinfo/cb/CBPrice.php?l=zh-tw&o=json"]:
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5, verify=False)
            if r.status_code == 200:
                rows = r.json() if isinstance(r.json(), list) else r.json().get("aaData", [])
                for item in rows:
                    c_raw = str(item.get("BondCode", item.get("Code", item[0] if isinstance(item, list) and len(item) > 0 else ""))).strip()
                    if len(c_raw) in [5, 6] and c_raw[:4].isdigit(): cb_codes.add(c_raw[:4])
        except Exception:
            continue
    return cb_codes

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_day_trade_shares_batch() -> tuple[dict, str]:
    headers, dt_shares, now_tw = {"User-Agent": "Mozilla/5.0"}, {}, get_tw_now()
    dt_note = "(前日)" if ((now_tw.weekday() < 5) and (540 <= (now_tw.hour * 60 + now_tw.minute) < 960)) else ""
    try:
        r_tw = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/TWTB4U", headers=headers, timeout=5, verify=False)
        if r_tw.status_code == 200 and isinstance(r_tw.json(), list):
            for row in r_tw.json():
                c, v_raw = str(row.get("Code", row.get("證券代號", ""))).strip(), str(row.get("Volume", row.get("當日沖銷交易成交股數", "0"))).replace(",", "").strip()
                if len(c) == 4 and c.isdigit() and v_raw.replace(".", "").isdigit(): dt_shares[c] = float(v_raw)
    except Exception:
        pass
    try:
        r_otc = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_day_trading", headers=headers, timeout=5, verify=False)
        if r_otc.status_code == 200 and isinstance(r_otc.json(), list):
            for row in r_otc.json():
                c, v_raw = str(row.get("SecuritiesCompanyCode", row.get("Code", ""))).strip(), str(row.get("DayTradingVolume", row.get("Volume", "0"))).replace(",", "").strip()
                if len(c) == 4 and c.isdigit() and v_raw.replace(".", "").isdigit(): dt_shares[c] = float(v_raw)
    except Exception:
        pass
    return dt_shares, dt_note

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_official_3rates_and_cum_eps():
    headers, fin_dict = {"User-Agent": "Mozilla/5.0"}, {}
    def clean_num(val):
        try:
            s = str(val).replace(",", "").replace("%", "").replace("+", "").strip()
            return float(s) if s not in ["", "-", "--", "N/A", "None"] else np.nan
        except Exception:
            return np.nan
    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap17_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap17_O"]:
        try:
            r = requests.get(url, headers=headers, timeout=7, verify=False)
            if r.status_code == 200:
                for row in r.json():
                    c = str(row.get("公司代號", row.get("證券代號", ""))).strip()
                    if len(c) == 4 and c.isdigit():
                        fin_dict[c] = {"gm": clean_num(next((v for k, v in row.items() if "毛利率" in k), np.nan)), "om": clean_num(next((v for k, v in row.items() if "營業利益率" in k or "營益率" in k), np.nan)), "cum_eps": np.nan}
        except Exception:
            continue
    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci"]:
        try:
            r = requests.get(url, headers=headers, timeout=7, verify=False)
            if r.status_code == 200:
                for row in r.json():
                    c = str(row.get("公司代號", row.get("證券代號", ""))).strip()
                    if len(c) == 4 and c.isdigit():
                        eps_v = clean_num(next((v for k, v in row.items() if "基本每股盈餘" in k or "每股盈餘" in k), np.nan))
                        if c not in fin_dict: fin_dict[c] = {"gm": np.nan, "om": np.nan, "cum_eps": eps_v}
                        else: fin_dict[c]["cum_eps"] = eps_v
        except Exception:
            continue
    return fin_dict

@st.cache_data(ttl=600, show_spinner=False)
def fetch_full_market_enrichment():
    fast_p, _, _ = fetch_market_prices_fast()
    price_dict = {k: dict(v) for k, v in fast_p.items()}
    tdcc_dict, rev_dict = {}, {}
    headers, now_tw = {"User-Agent": "Mozilla/5.0"}, get_tw_now()

    try:
        dt_map, dt_note = fetch_day_trade_shares_batch()
        for code, dt_sh in dt_map.items():
            if code in price_dict and float(price_dict[code].get("vol_shares", 0)) > 0:
                ratio_dt = round((dt_sh / float(price_dict[code]["vol_shares"])) * 100.0, 1)
                if 0.0 <= ratio_dt <= 99.9: price_dict[code]["day_trade_str"] = f"{ratio_dt:.1f}%{dt_note}"
    except Exception:
        pass

    for d_back in range(0, 5):
        chk_dt = now_tw - datetime.timedelta(days=d_back)
        if chk_dt.weekday() >= 5: continue
        try:
            r_t86 = requests.get(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={chk_dt.strftime('%Y%m%d')}&selectType=ALLBUT0999&response=json", headers=headers, timeout=7, verify=False)
            if r_t86.status_code == 200 and len(r_t86.json().get("data", [])) > 100:
                for row in r_t86.json()["data"]:
                    c = str(row[0]).strip()
                    if c in price_dict and len(row) >= 12:
                        try:
                            price_dict[c]["fini_lots"] = int(round(float(str(row[4]).replace(",", "").strip()) / 1000.0))
                            price_dict[c]["sitc_lots"] = int(round(float(str(row[10]).replace(",", "").strip()) / 1000.0))
                        except Exception: pass
                break
        except Exception:
            continue

    for d_back in range(0, 5):
        chk_dt = now_tw - datetime.timedelta(days=d_back)
        if chk_dt.weekday() >= 5: continue
        roc_d = f"{chk_dt.year - 1911}/{chk_dt.month:02d}/{chk_dt.day:02d}"
        try:
            r_o = requests.get(f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d={roc_d}&o=json", headers=headers, timeout=7, verify=False)
            if r_o.status_code == 200 and len(r_o.json().get("aaData", [])) > 100:
                for row in r_o.json()["aaData"]:
                    c = str(row[0]).strip()
                    if c in price_dict and len(row) >= 14:
                        try:
                            f_sh = float(str(row[10]).replace(",", "").strip()) if len(row) >= 20 else float(str(row[4]).replace(",", "").strip())
                            s_sh = float(str(row[13]).replace(",", "").strip()) if len(row) >= 20 else float(str(row[7]).replace(",", "").strip())
                            price_dict[c]["fini_lots"], price_dict[c]["sitc_lots"] = int(round(f_sh / 1000.0)), int(round(s_sh / 1000.0))
                        except Exception: pass
                break
        except Exception:
            continue

    for pe_url, c_key, p_keys in [("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", "Code", ["PEratio"]), ("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", "SecuritiesCompanyCode", ["PriceEarningRatio", "PEratio"])]:
        try:
            r_pe = requests.get(pe_url, headers=headers, timeout=8, verify=False)
            if r_pe.status_code == 200:
                for row in r_pe.json():
                    c = str(row.get(c_key, row.get("Code", ""))).strip()
                    if c in price_dict:
                        for pk in p_keys:
                            ps = str(row.get(pk, "")).replace(",", "").strip()
                            if ps and ps not in ["-", "--", "N/A", "0", "0.00"]:
                                try: price_dict[c]["pe"] = round(float(ps), 1); break
                                except Exception: pass
        except Exception:
            pass

    try:
        r_csv = requests.get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", headers=headers, timeout=12, verify=False)
        if r_csv.status_code == 200 and len(r_csv.text) > 500:
            df_tdcc = pd.read_csv(io.StringIO(r_csv.text), dtype=str)
            df_tdcc.columns = [c.strip() for c in df_tdcc.columns]
            col_date, col_code, col_lvl, col_pct = next((c for c in df_tdcc.columns if "日期" in c), None), next((c for c in df_tdcc.columns if "代號" in c), None), next((c for c in df_tdcc.columns if "分級" in c), None), next((c for c in df_tdcc.columns if "比例" in c), None)
            if col_code and col_lvl and col_pct:
                cur_d = str(df_tdcc[col_date].iloc[0]).strip() if col_date else ""
                df_tdcc["code_clean"], df_tdcc["lvl_clean"] = df_tdcc[col_code].str.strip(), df_tdcc[col_lvl].str.strip()
                df_tdcc["pct_num"] = pd.to_numeric(df_tdcc[col_pct].str.replace(",", ""), errors="coerce").fillna(0.0)
                tdcc_dict = {str(k): round(float(v), 2) for k, v in df_tdcc[df_tdcc["lvl_clean"].isin(["12", "13", "14", "15"])].groupby("code_clean")["pct_num"].sum().to_dict().items() if 0.0 < float(v) <= 100.0}
                t1000_map = {str(k): round(float(v), 2) for k, v in df_tdcc[df_tdcc["lvl_clean"] == "15"].groupby("code_clean")["pct_num"].sum().to_dict().items() if 0.0 < float(v) <= 100.0}
                prev_snap = {}
                if os.path.exists(TDCC_PREV_CACHE_FILE):
                    try:
                        with open(TDCC_PREV_CACHE_FILE, "r", encoding="utf-8") as f: prev_snap = json.load(f)
                    except Exception: prev_snap = {}
                if cur_d and prev_snap.get("cur_date") and prev_snap.get("cur_date") != cur_d:
                    prev_snap["prev_date"], prev_snap["prev_400"], prev_snap["prev_1000"] = prev_snap.get("cur_date"), prev_snap.get("cur_400", {}), prev_snap.get("cur_1000", {})
                p400, p1000 = prev_snap.get("prev_400", {}), prev_snap.get("prev_1000", {})
                for ck, v400 in tdcc_dict.items():
                    if ck in price_dict:
                        v1000 = t1000_map.get(ck)
                        price_dict[ck]["tdcc_1000"] = v1000
                        if ck in p400: price_dict[ck]["tdcc_400_delta"] = round(v400 - float(p400[ck]), 2)
                        if v1000 is not None and ck in p1000: price_dict[ck]["tdcc_1000_delta"] = round(v1000 - float(p1000[ck]), 2)
                if cur_d and prev_snap.get("cur_date") != cur_d:
                    try:
                        with open(TDCC_PREV_CACHE_FILE, "w", encoding="utf-8") as f:
                            json.dump({"cur_date": cur_d, "cur_400": tdcc_dict, "cur_1000": t1000_map, "prev_date": prev_snap.get("prev_date", ""), "prev_400": p400, "prev_1000": p1000}, f)
                    except Exception: pass
    except Exception:
        pass

    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap05_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"]:
        try:
            r_rev = requests.get(url, headers=headers, timeout=10, verify=False)
            if r_rev.status_code == 200:
                for row in r_rev.json():
                    c = str(row.get("公司代號", row.get("證券代號", ""))).strip()
                    sec_s = str(row.get("產業別", "")).strip()
                    yoy_r = next((str(v).replace(",", "").strip() for k, v in row.items() if "同月增減" in k), None)
                    cum_r = next((str(v).replace(",", "").strip() for k, v in row.items() if "前期比較增減" in k), None)
                    if c and yoy_r not in [None, "", "-", "--", "N/A"]:
                        try: rev_dict[c] = round(float(yoy_r), 1)
                        except Exception: pass
                    if c in price_dict:
                        if sec_s and sec_s not in ["", "-", "None"]: price_dict[c]["sector"] = sec_s
                        if cum_r not in [None, "", "-", "--", "N/A"]:
                            try: price_dict[c]["cum_yoy"] = round(float(cum_r), 1)
                            except Exception: pass
        except Exception:
            continue
    return price_dict, tdcc_dict, rev_dict

def compute_eps_valuation(code, curr_p, sector_str, single_yoy, cum_yoy, pe_official, support_price=None):
    if curr_p is None or pd.isna(curr_p) or curr_p <= 0: return np.nan, np.nan, np.nan, np.nan, "➖ 暫無報價"
    is_fin = ("金融" in sector_str) or (code in ["2881", "2882", "2891", "2886", "2884", "2885", "2892", "2880", "5880", "2890", "2883", "2887", "2801", "2834", "2812"])
    if is_fin: pe_low, pe_high = 12.0, 15.5
    elif any(k in sector_str for k in ["IC設計", "矽智財", "散熱", "BMC", "ASIC", "高速傳輸", "CoWoS", "探針", "重電", "矽光子"]) or code in ["2454", "3034", "2379", "3017", "3653", "2059", "5274", "3529", "3661", "3443", "6669", "3324", "5269", "6187", "3131", "6515", "6223", "1519", "6683", "3163", "4991", "7822"]: pe_low, pe_high = 20.0, 28.0
    elif any(k in sector_str for k in ["晶圓", "封測", "扇形封裝", "CCL", "PCB", "載板", "連接器", "鏡頭", "電源", "機電", "水資源"]) or code in ["2330", "3711", "6239", "2308", "3533", "2383", "2368", "3008", "3406", "2449", "3264", "6274", "3189", "8046", "1503", "1513", "1514", "8936", "6139"]: pe_low, pe_high = 16.0, 22.0
    else: pe_low, pe_high = 14.0, 18.5
    s_yoy = float(single_yoy) if (single_yoy is not None and pd.notna(single_yoy)) else 0.0
    c_yoy = float(cum_yoy) if (cum_yoy is not None and pd.notna(cum_yoy)) else s_yoy
    r6 = 0.60 * c_yoy + 0.40 * s_yoy
    M = 1.20 if r6 >= 35 else (1.14 if r6 >= 20 else (1.08 if r6 >= 10 else (1.02 if r6 >= 0 else (0.94 if r6 >= -10 else 0.88))))
    f_info = fetch_official_3rates_and_cum_eps().get(code, {})
    gm_v, om_v, cum_eps_api = f_info.get("gm", np.nan), f_info.get("om", np.nan), f_info.get("cum_eps", np.nan)
    if code in MARGIN_DILUTION_SET or (r6 > 10 and pd.notna(om_v) and om_v < 4.2 and not is_fin): T, tb = 0.88, "⚠️毛利稀釋T=0.88"
    elif code in MARGIN_EXPANSION_SET or (pd.notna(gm_v) and pd.notna(om_v) and gm_v >= 28 and om_v >= 15 and s_yoy >= c_yoy and s_yoy >= 15): T, tb = 1.12, "🔥三率三升T=1.12"
    else: T, tb = 1.00, "⚖️三率持平T=1.00"
    W = 0.78 if is_fin else 0.74
    cf = (W + (1.0 - W) * M * T) * 0.95
    tech_e = round(float(support_price), 2) if (support_price is not None and pd.notna(support_price) and support_price > 0) else round(curr_p * 0.96, 2)
    if pe_official is not None and pd.notna(pe_official) and 0.5 < float(pe_official) <= 65.0:
        pe_v = round(float(pe_official), 1)
        ttm = curr_p / pe_v
        base_e = (cum_eps_api / W) if (pd.notna(cum_eps_api) and cum_eps_api > 0.1 and (0.35 * ttm <= (cum_eps_api / W) <= 1.8 * ttm)) else ttm
        est_e = round(max(0.1, base_e * cf), 2)
        ep_lo, ep_hi = round(est_e * pe_low, 2), round(est_e * pe_high, 2)
        if curr_p <= ep_lo: vt = f"💎 低估鑽石 (空間+{round((ep_lo - curr_p)/max(1,curr_p)*100,1)}%) [{tb}]"
        elif curr_p <= ep_hi: vt = f"✅ 估值合理 (距目標+{round((ep_hi - curr_p)/max(1,curr_p)*100,1)}%) [{tb}]"
        else: vt = f"⚠️ 超過常態PE (+{round((curr_p - ep_hi)/max(1,ep_hi)*100,1)}%) [{tb}]"
        return est_e, pe_v, ep_lo, ep_hi, vt
    return np.nan, (round(float(pe_official), 1) if pd.notna(pe_official) else np.nan), tech_e, round(curr_p * 1.12, 2), f"🔄 轉機觀察 (守{tech_e}) [{tb}]"

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_single_stock_anti_trap(code: str, mkt: str = "TW") -> dict:
    if code in ODM_Q3_OFFICIAL_BASE:
        b = dict(ODM_Q3_OFFICIAL_BASE[code]); b["ok"] = True; return b
    out = {"ok": False, "inv_qoq": None, "ar_qoq": None, "ocf_positive": None, "note": "N/A (交由淘金客聯網查核)"}
    try:
        tkr = yf.Ticker(f"{code}.{'TW' if mkt == 'TW' else 'TWO'}")
        bs, cf = tkr.quarterly_balance_sheet, tkr.quarterly_cashflow
        inv_q, ar_q, ocf_p = None, None, None
        if bs is not None and not bs.empty and bs.shape[1] >= 2:
            ir = next((i for i in bs.index if "Inventory" in str(i) or "Inventories" in str(i)), None)
            if ir is not None and pd.notna(bs.loc[ir].iloc[0]) and pd.notna(bs.loc[ir].iloc[1]) and float(bs.loc[ir].iloc[1]) > 0:
                inv_q = round(((float(bs.loc[ir].iloc[0]) - float(bs.loc[ir].iloc[1])) / float(bs.loc[ir].iloc[1])) * 100.0, 1)
            ar = next((i for i in bs.index if "Accounts Receivable" in str(i) or "Receivables" in str(i)), None)
            if ar is not None and pd.notna(bs.loc[ar].iloc[0]) and pd.notna(bs.loc[ar].iloc[1]) and float(bs.loc[ar].iloc[1]) > 0:
                ar_q = round(((float(bs.loc[ar].iloc[0]) - float(bs.loc[ar].iloc[1])) / float(bs.loc[ar].iloc[1])) * 100.0, 1)
        if cf is not None and not cf.empty and cf.shape[1] >= 1:
            orw = next((i for i in cf.index if "Operating Cash Flow" in str(i) or "Cash Flow From Continuing Operating" in str(i)), None)
            if orw is not None and pd.notna(cf.loc[orw].iloc[0]): ocf_p = bool(float(cf.loc[orw].iloc[0]) > 0)
        if any(x is not None for x in [inv_q, ar_q, ocf_p]):
            i_s = f"存貨QoQ {inv_q:+.1f}%" if inv_q is not None else "存貨QoQ N/A"
            a_s = f"應收QoQ {ar_q:+.1f}%" if ar_q is not None else "應收QoQ N/A"
            o_s = ("營業現金流為正(✅)" if ocf_p else "營業現金流為負(⚠️)") if ocf_p is not None else "OCF N/A"
            out = {"ok": True, "inv_qoq": inv_q, "ar_qoq": ar_q, "ocf_positive": ocf_p, "note": f"{i_s} / {a_s} / {o_s}"}
    except Exception:
        pass
    return out

@st.cache_data(ttl=43200, show_spinner=False)
def fetch_tdcc_weekly_delta_ondemand(codes_tuple: tuple) -> dict:
    out, start_d = {}, (get_tw_now() - datetime.timedelta(days=28)).strftime("%Y-%m-%d")
    def _one(code):
        try:
            r = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockHoldingSharesPer&data_id={code}&start_date={start_d}", timeout=3.5, verify=False)
            if r.status_code == 200 and r.json().get("data"):
                df_t = pd.DataFrame(r.json()["data"])
                dates = sorted(df_t["date"].unique())
                if len(dates) >= 2:
                    lv400 = {"400,001-600,000", "600,001-800,000", "800,001-1,000,000", "more than 1,000,001", "12", "13", "14", "15"}
                    lv1000 = {"more than 1,000,001", "15"}
                    cs, ps = df_t[df_t["date"] == dates[-1]], df_t[df_t["date"] == dates[-2]]
                    p400_c, p400_p = float(cs[cs["HoldingSharesLevel"].astype(str).isin(lv400)]["percent"].sum()), float(ps[ps["HoldingSharesLevel"].astype(str).isin(lv400)]["percent"].sum())
                    p1000_c, p1000_p = float(cs[cs["HoldingSharesLevel"].astype(str).isin(lv1000)]["percent"].sum()), float(ps[ps["HoldingSharesLevel"].astype(str).isin(lv1000)]["percent"].sum())
                    if p400_c > 0 and p400_p > 0:
                        return code, {"p400": round(p400_c, 2), "d400": round(p400_c - p400_p, 2), "p1000": round(p1000_c, 2), "d1000": round(p1000_c - p1000_p, 2), "date": str(dates[-1])}
        except Exception:
            pass
        return code, None
    with ThreadPoolExecutor(max_workers=4) as ex:
        for fut in as_completed([ex.submit(_one, c) for c in list(codes_tuple)[:6]]):
            ck, v = fut.result()
            if v is not None: out[ck] = v
    return out

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_pair_swap_history(code_sell: str, mkt_sell: str, code_buy: str, mkt_buy: str, p_sell_live: float, p_buy_live: float) -> dict:
    curr_r = round(p_buy_live / max(0.01, p_sell_live), 3)
    res = {"ok": False, "curr_ratio": curr_r, "median_ratio": curr_r, "min_ratio": curr_r, "max_ratio": curr_r, "discount_pct": 0.0}
    try:
        ts, tb = f"{code_sell}.{'TW' if mkt_sell == 'TW' else 'TWO'}", f"{code_buy}.{'TW' if mkt_buy == 'TW' else 'TWO'}"
        df_p = yf.download([ts, tb], period="1y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
        if not df_p.empty and ts in df_p and tb in df_p:
            al = pd.concat([df_p[ts]["Close"].dropna().rename("s"), df_p[tb]["Close"].dropna().rename("b")], axis=1).dropna()
            if len(al) >= 20:
                rs = (al["b"] / al["s"].replace(0, np.nan)).dropna().values.astype(float)
                med_r = round(float(np.median(rs)), 3)
                res = {"ok": True, "curr_ratio": curr_r, "median_ratio": med_r, "min_ratio": round(float(np.min(rs)), 3), "max_ratio": round(float(np.max(rs)), 3), "discount_pct": round(((med_r - curr_r) / max(0.001, med_r)) * 100.0, 1)}
    except Exception:
        pass
    return res

@st.cache_data(ttl=600, show_spinner=False)
def download_historical_klines(yf_tickers_tuple):
    try: return yf.download(list(yf_tickers_tuple), period="1y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
    except Exception: return pd.DataFrame()

def scan_all_stocks_on_demand(twii_20d_ret: float, macro_withdrawal_alert: bool):
    price_dict, tdcc_dict, rev_dict = fetch_full_market_enrichment()
    msci_data = fetch_dynamic_msci_data(len(price_dict))
    cb_set = fetch_active_cb_codes()
    vol_mult, vol_note = get_volume_multiplier()
    now_tw = get_tw_now()
    today_str, today_ymd = now_tw.strftime("%Y-%m-%d"), now_tw.strftime("%Y%m%d")
    is_open = (now_tw.weekday() < 5) and ((now_tw.hour * 60 + now_tw.minute) >= 540)

    candidates = dict(CORE_WATCHLIST)
    msci_code_role = {}
    for role_lbl, lst_k in [("🌟MSCI標準新增", "std_add"), ("📈MSCI小型新增", "small_add"), ("📉MSCI標準刪除", "std_del"), ("⚠️MSCI小型刪除", "small_del")]:
        for c, nm, th, def_mkt in msci_data.get(lst_k, []):
            msci_code_role[c] = (role_lbl, th)
            if c not in candidates: candidates[c] = (price_dict.get(c, {}).get("name", nm), f"{role_lbl}・{th}", price_dict.get(c, {}).get("market", def_mkt))
    for th_name, bl in MSCI_THEME_BELLWETHERS.items():
        for bc, bnm in bl:
            if bc not in candidates: candidates[bc] = (price_dict.get(bc, {}).get("name", bnm), f"👑族群指標・{th_name}", price_dict.get(bc, {}).get("market", "TW"))

    for code, info in sorted([(c, i) for c, i in price_dict.items() if c not in candidates and len(c) == 4 and c.isdigit() and (c not in CONSTRUCTION_BLACKLIST) and ("營造" not in i.get("sector", "")) and (not is_zombie_stock_tiered(c, i["close"], i["vol_lots"] * vol_mult)) and i["chg_pct"] >= 2.0 and i["close"] >= 15.0], key=lambda x: x[1]["chg_pct"], reverse=True)[:70]:
        candidates[code] = (info["name"], info.get("sector") or "⚡短線技術破繭", info["market"])
    for code, info in sorted([(c, i) for c, i in price_dict.items() if c not in candidates and len(c) == 4 and c.isdigit() and (c not in CONSTRUCTION_BLACKLIST) and ("營造" not in i.get("sector", "")) and (not is_zombie_stock_tiered(c, i["close"], i["vol_lots"] * vol_mult)) and i["close"] >= 18.0 and (i.get("fini_lots", 0) >= 100 or i.get("sitc_lots", 0) >= 30) and rev_dict.get(c, -1.0) > 0.0], key=lambda x: x[1].get("sitc_lots", 0) * 3 + x[1].get("fini_lots", 0), reverse=True)[:30]:
        candidates[code] = (info["name"], info.get("sector") or "🏦法人鎖碼成長", info["market"])

    rt_quotes = fetch_mis_realtime_batch(tuple((c, m) for c, (_, _, m) in candidates.items()))
    yf_map = {f"{c}.{'TW' if m == 'TW' else 'TWO'}": c for c, (_, _, m) in candidates.items()}
    raw_df = download_historical_klines(tuple(yf_map.keys()))
    disp_map = msci_data.get("disposition_map", {})

    l_launch, l_pull, l_orbit, l_bc, l_turn, l_gran, l_msci, sec_recs, all_det = [], [], [], [], [], [], [], [], {}
    for yf_tkr, code in yf_map.items():
        try:
            if raw_df.empty or yf_tkr not in raw_df: continue
            df_s = raw_df[yf_tkr].dropna(subset=["Open", "High", "Low", "Close"]).copy()
            if len(df_s) < 35: continue
            o, c, h, l, v = df_s["Open"].values.astype(float), df_s["Close"].values.astype(float), df_s["High"].values.astype(float), df_s["Low"].values.astype(float), df_s["Volume"].values.astype(float) / 1000.0
            last_kd = df_s.index[-1].strftime("%Y-%m-%d")
            name, sector, mkt_code = candidates[code]

            rt = rt_quotes.get(code, {})
            if is_open and rt.get("close", 0) > 0 and rt.get("date") == today_ymd:
                if last_kd == today_str:
                    if rt.get("open", 0) > 0: o[-1] = rt["open"]
                    c[-1], h[-1], l[-1] = rt["close"], max(h[-1], rt["high"]), min(l[-1], rt["low"])
                    if rt["vol_lots"] > 0: v[-1] = float(rt["vol_lots"])
                else:
                    o, c, h, l = np.append(o, rt.get("open") or rt["close"]), np.append(c, rt["close"]), np.append(h, rt["high"]), np.append(l, rt["low"])
                    v = np.append(v, float(rt["vol_lots"]) if rt["vol_lots"] > 0 else v[-1])

            curr_p, prev_p = round(float(c[-1]), 2), float(c[-2])
            chg_1d = round((curr_p - prev_p) / max(0.01, prev_p) * 100, 2)
            if abs(chg_1d) < 0.01 and code in price_dict and abs(price_dict[code].get("chg_pct", 0.0)) > 0.01 and not is_open: chg_1d = price_dict[code]["chg_pct"]
            chg_3d = round((curr_p - float(c[-4])) / max(0.01, float(c[-4])) * 100, 2) if len(c) >= 4 else chg_1d
            est_vol = int(round(v[-1] * (vol_mult if is_open else 1.0)))
            yest_vol, ma5_vol = max(1.0, float(v[-2])), max(1.0, float(np.mean(v[-6:-1])))
            yod_ratio, vol_ratio = round(est_vol / yest_vol, 2), round(est_vol / ma5_vol, 2)
            if (code not in CORE_WATCHLIST) and (code not in msci_code_role) and is_zombie_stock_tiered(code, curr_p, est_vol, ma5_vol): continue

            p_meta = price_dict.get(code, {})
            sec_recs.append({"code": code, "name": name, "sector": sector, "official_sec": p_meta.get("sector") or sector, "close": curr_p, "chg_1d": chg_1d, "est_vol": est_vol, "yest_vol": yest_vol, "yod_ratio": yod_ratio})

            ma5, ma10, ma20 = round(float(np.mean(c[-5:])), 2), round(float(np.mean(c[-10:])), 2), round(float(np.mean(c[-20:])), 2)
            ma60 = round(float(np.mean(c[-60:])), 2) if len(c) >= 60 else ma20
            ma120 = round(float(np.mean(c[-120:])), 2) if len(c) >= 120 else ma60
            ma240 = round(float(np.mean(c[-240:])), 2) if len(c) >= 240 else ma120
            std20 = float(np.std(c[-20:]))
            bb_up, bb_bw = round(ma20 + 2.0 * std20, 2), (round((4.0 * std20 / ma20) * 100, 1) if ma20 > 0 else 99.0)

            sk, sd, sj, sjp, kd_cr = compute_kdj(h[-60:], l[-60:], c[-60:])
            pdi, mdi, adx, adx_p, dmi_b, atr_pct = compute_dmi_and_atr(h[-90:], l[-90:], c[-90:])
            dmi_tag, is_bear_acc = classify_dmi_direction(pdi, mdi, adx, sj)
            dmi_str = f"+DI:{pdi}/-DI:{mdi}/ADX:{adx} [{dmi_tag}]" if pd.notna(adx) else "N/A"
            atr_s = (f"{atr_pct:.1f}% ⚠️高波動(建議收盤確認/倉位減半)" if atr_pct > 4.5 else f"{atr_pct:.1f}%") if pd.notna(atr_pct) else "N/A"
            kd_dmi_res = (sk >= sd) and pd.notna(pdi) and (pdi > mdi) and (adx > adx_p) and (adx >= 18.0) and (kd_cr or dmi_b)

            bias10 = round(((curr_p - ma10) / ma10) * 100.0, 1) if ma10 > 0 else np.nan
            bias20 = round(((curr_p - ma20) / ma20) * 100.0, 1) if ma20 > 0 else np.nan
            b10_s = "N/A" if pd.isna(bias10) else (f"{bias10:+.1f}% 🚨極度過熱" if bias10 > 12.0 else (f"{bias10:+.1f}% ⚠️追高過熱" if bias10 > 10.0 else f"{bias10:+.1f}%"))
            b20_s = f"{bias20:+.1f}%" if pd.notna(bias20) else "N/A"

            drng = float(h[-1] - l[-1])
            up_sh_p = 0.0 if drng < 1e-6 else round(((float(h[-1]) - max(float(o[-1]), float(c[-1]))) / drng) * 100.0, 1)
            cp_p = (100.0 if chg_1d >= 0 else 0.0) if drng < 1e-6 else round(((float(c[-1]) - float(l[-1])) / drng) * 100.0, 1)
            cp_s = f"{cp_p:.0f}% (收破中軸)" if cp_p < 50.0 else f"{cp_p:.0f}%"
            dt_s = p_meta.get("day_trade_str", "N/A (交由淘金客聯網查核)")

            obv_t, _ = compute_obv_trend(c[-60:], v[-60:])
            if len(c) >= 21 and c[-21] > 0:
                s20r = ((curr_p - float(c[-21])) / float(c[-21])) * 100.0
                rs20 = round(s20r - twii_20d_ret, 1)
                rs20_s = f"{rs20:+.1f}% (逆勢抗跌)" if (twii_20d_ret < 0 and rs20 > 0 and (curr_p >= float(np.max(c[-20:])) * 0.985 or s20r > 0)) else f"{rs20:+.1f}%"
            else: rs20_s = "N/A"

            t400, t1000, d400, d1000 = tdcc_dict.get(code), p_meta.get("tdcc_1000"), p_meta.get("tdcc_400_delta"), p_meta.get("tdcc_1000_delta")
            tdcc_s = f"400張:{t400:.1f}%{f'({d400:+.2f}%)' if d400 is not None else ''}/千張:{f'{t1000:.1f}%' + (f'({d1000:+.2f}%)' if d1000 is not None else '') if t1000 is not None else 'N/A'}" if t400 is not None else "N/A"

            raw_ceil = max(bb_up, round(float(np.max(h[-21:-1])), 2))
            ceil_p = round(curr_p * 1.07, 2) if raw_ceil <= curr_p else round(raw_ceil, 2)
            iron_p = round(min(curr_p, max(ma10, float(l[-1])) if curr_p >= ma10 else min(ma20, float(l[-1]))), 2)
            stop_p = round(min(ma60, float(np.min(l[-20:]))), 2)

            yoy_v, cum_v, pe_o = rev_dict.get(code), p_meta.get("cum_yoy"), p_meta.get("pe", np.nan)
            fl, sl = p_meta.get("fini_lots", 0), p_meta.get("sitc_lots", 0)
            yoy_s, cum_s = (f"{yoy_v:+.1f}%" if yoy_v is not None else "待公告"), (f"{cum_v:+.1f}%" if cum_v is not None else "待公告")
            g_tag, g_bon, g_short = evaluate_granary_and_anti_trap(code, yoy_v, macro_withdrawal_alert)
            is_bc, is_fin, is_def = (code in CORE_WATCHLIST), (code in FINANCIAL_BLUE_CHIPS), (code in POLICY_DEFENSE_ARMOR)
            has_fund = (yoy_v is not None and yoy_v > 0) and (cum_v is None or cum_v > 0)
            st_tag = f"🐀疑似墊料壓貨({g_bon}分)" if "疑似墊料" in g_tag else (f"🛡️政策內需防彈衣(+{g_bon}分)" if is_def else (f"🔥有基之彈｜{g_short}" if has_fund else (f"🏦核心權值｜{g_short}" if is_bc else "🎭純技術炒作")))
            est_e, pe_d, ep_in, ep_tg, v_diag = compute_eps_valuation(code, curr_p, sector, yoy_v, cum_v, pe_o, iron_p)

            row_base = {
                "代碼": code, "股名": f"{name}{' 🚨[處置]' if code in disp_map else ''}{' 🔥[CB]' if code in cb_set else ''}{' ⚠️量縮' if is_zombie_stock_tiered(code, curr_p, est_vol, ma5_vol) else ''}",
                "族群": sector, "穀倉賽道與防墊料": g_tag, "現價": curr_p, "今日%": chg_1d, "3日%": chg_3d, "短線屬性": st_tag,
                "DMI方向雙檢": dmi_str, "日J值": sj, "BIAS10": b10_s, "BIAS20": b20_s,
                "5MA量比": vol_ratio, "上影線%": up_sh_p, "收盤位階": cp_s, "當沖率": dt_s,
                "OBV趨勢": obv_t, "RS20D%": rs20_s, "大戶400/千張": tdcc_s, "ATR%波動尺": atr_s,
                "法人動向": format_inst_chip_tag(fl, sl), "外資買賣(張)": fl, "投信買賣(張)": sl,
                "預估EPS(元)": est_e, "本益比(PE)": pe_d, "💎EPS合理切入價": ep_in, "🎯EPS目標價": ep_tg, "估值診斷": v_diag,
                "昨日量比": yod_ratio, "預估量(張)": est_vol, "天花板(反壓)": ceil_p, "月線承接": ma20, "鋼鐵防線": iron_p, "極限停損": stop_p,
                "單月營收YoY": yoy_s, "累計年營收YoY": cum_s
            }
            all_det[code] = {**row_base, "_raw_name": name, "_mkt": mkt_code, "_cb": ("🔥含CB可轉債" if code in cb_set else "➖"), "_ma5": ma5, "_ma10": ma10, "_ma20": ma20, "_ma60": ma60, "_ma120": ma120, "_ma240": ma240, "_pattern_tag": st_tag}

            if code in msci_code_role:
                mr, mt = msci_code_role[code]
                l_msci.append({"MSCI異動別": mr, "影響細分族群": mt, "處置與生效狀態": disp_map.get(code, "✅ 正常交易 (11/30 尾盤生效)"), **row_base})
            if code in (GRANARY_1_CSP_COMMON | GRANARY_2_POWER_THERMAL | GRANARY_3_EDGE_MEMORY | POLICY_DEFENSE_ARMOR | OLD_GRANARY_ODM_WATCH):
                l_gran.append({"穀倉戰略總分": max(30, min(115, 85 + g_bon + (4 if curr_p >= ma20 else -3) + (3 if (pd.notna(pdi) and pdi > mdi) else 0))), **row_base})

            heavy_dn = (chg_1d <= -2.0 and vol_ratio >= 1.25) or (chg_1d <= -4.2)
            above20 = (curr_p >= ma20) and (curr_p >= ma5) and (curr_p >= ma10) and (curr_p >= ma60 or ma20 >= ma60 * 0.94)
            brk20 = (curr_p >= ma20) and (curr_p >= ma5) and (curr_p >= ma10) and (prev_p < ma20) and (chg_1d >= 3.5) and (vol_ratio >= 1.25 or chg_1d >= 8.5) and pd.notna(pdi) and (pdi > mdi)
            if (above20 or brk20) and (not heavy_dn) and (not is_bear_acc) and ("疑似墊料" not in g_tag):
                overh = (chg_3d >= 14.0) or (pd.notna(bias10) and bias10 > 12.0) or (sj >= 93.0 and not kd_dmi_res and chg_1d < 8.5)
                if (brk20 and not overh) or ((chg_1d >= 2.0 and (vol_ratio >= 1.20 or chg_1d >= 7.5) and kd_dmi_res and not overh) or (chg_1d >= 2.8 and (vol_ratio >= 1.25 or chg_1d >= 7.5) and not overh and pd.notna(pdi) and (pdi > mdi) and adx >= 16.0)):
                    pat = f"👑 KD×DMI共振突破 [{g_short}]" if kd_dmi_res else (f"🔥 布林壓縮突破 [{g_short}]" if bb_bw <= 16.0 else f"🚀 站穩月線帶量突破 [{g_short}]")
                    all_det[code]["_pattern_tag"] = pat
                    l_launch.append({"實戰優先分": min(115, 92 + g_bon), "名將戰法診斷": pat, **row_base})
                elif (-3.5 <= chg_1d <= 2.2) and (chg_3d >= -6.5) and (vol_ratio <= 0.95) and (sj <= 68.0) and pd.notna(pdi) and (pdi >= mdi):
                    pat = f"⚡ N字量縮守月線 [{g_short}]"
                    all_det[code]["_pattern_tag"] = pat
                    l_pull.append({"蓄勢評分": min(115, 84 + int((1.0 - vol_ratio) * 15) + g_bon), "名將戰法診斷": pat, **row_base})
                elif curr_p >= ma5 >= ma10 >= ma20 and pd.notna(pdi) and (pdi > mdi) and chg_1d >= -2.0:
                    pat = f"🔥 3日急拉過熱 [{g_short}]" if (chg_3d >= 10.5 or (pd.notna(bias10) and bias10 > 10.0)) else f"🛡️ 沿5/10MA穩步推升 [{g_short}]"
                    all_det[code]["_pattern_tag"] = pat
                    l_orbit.append({"趨勢狀態": pat, **row_base})

            if is_bc and (not heavy_dn):
                bc_ok = has_fund or (yoy_v is not None and yoy_v >= 0) or (cum_v is not None and cum_v >= 0) or is_fin or is_def
                if bc_ok and (curr_p >= ma20) and pd.notna(pdi) and (pdi > mdi) and (not is_bear_acc) and (chg_3d < 9.5) and (sj <= 88.0):
                    l_bc.append({"波段總分": max(40, min(115, 85 + g_bon + (4 if has_fund else 0))), "長線格局定性": f"{g_short}＋{'🔥雙成長' if has_fund else '🏦穩健'}", **row_base})
                elif bc_ok and (curr_p < ma20) and (curr_p >= ma240 * 0.85):
                    dtg = f"🚨【{g_short}・空頭加速警戒】" if is_bear_acc else (f"🟢【{g_short}・月線下築底反彈】" if (curr_p >= ma5 and chg_1d >= 0) else f"⏳【{g_short}・股災超跌回測】")
                    l_turn.append({"波段總分": max(40, min(115, 82 + g_bon - (10 if is_bear_acc else 0))), "類型與轉機標註": dtg, **row_base})
        except Exception:
            continue

    return {
        "df_launch": pd.DataFrame(l_launch).sort_values(by="實戰優先分", ascending=False) if l_launch else pd.DataFrame(),
        "df_pullback": pd.DataFrame(l_pull).sort_values(by="蓄勢評分", ascending=False) if l_pull else pd.DataFrame(),
        "df_orbit": pd.DataFrame(l_orbit).sort_values(by="3日%", ascending=False) if l_orbit else pd.DataFrame(),
        "df_long_bc": pd.DataFrame(l_bc).sort_values(by="波段總分", ascending=False) if l_bc else pd.DataFrame(),
        "df_long_turn": pd.DataFrame(l_turn).sort_values(by=["波段總分", "今日%"], ascending=[False, False]) if l_turn else pd.DataFrame(),
        "df_granary": pd.DataFrame(l_gran).sort_values(by=["穀倉戰略總分", "今日%"], ascending=[False, False]) if l_gran else pd.DataFrame(),
        "df_msci": pd.DataFrame(l_msci) if l_msci else pd.DataFrame(),
        "sec_recs": sec_recs, "all_details": all_det, "msci_data": msci_data, "price_dict": price_dict, "vol_note": vol_note, "ts": now_tw.strftime("%H:%M:%S")
    }

@st.cache_data(ttl=300, show_spinner=False)
def fetch_all_monthly_revenue_df_on_demand():
    price_dict, tdcc_dict, _ = fetch_full_market_enrichment()
    headers, records_by_code, cb_set = {"User-Agent": "Mozilla/5.0"}, {}, fetch_active_cb_codes()
    def to_f(v):
        try:
            s = str(v).replace(",", "").replace("+", "").replace("%", "").strip()
            return float(s) if s not in ["", "-", "--", "N/A", "None", "不適用"] else np.nan
        except Exception:
            return np.nan
    def build_ttag(cr, yoy, mom, cumr, cumy, sec, note="", c=""):
        if (c not in POLICY_DEFENSE_ARMOR) and ("建材營造" in sec or "營造" in sec or any(k in str(note) for k in ["建案", "交屋", "房地", "營建", "售地"])): return "🏗️ 營建入帳認列(排除波段)"
        if pd.isna(yoy) or yoy < 20.0: return "➖ 一般波動"
        py = cumy
        if pd.notna(cr) and pd.notna(cumr) and cumr > cr and (100+yoy) > 0 and pd.notna(cumy) and (100+cumy) > 0:
            pl = (cumr/(1+cumy/100)) - (cr/(1+yoy/100))
            if pl > 0: py = (((cumr - cr) - pl) / pl) * 100.0
        if pd.notna(py) and py <= 0 and yoy >= 20 and (pd.isna(mom) or mom >= 0): return f"🌱 久旱逢甘霖 (前累計{py:+.1f}% ➔ 本月+{yoy:.1f}%)"
        if pd.notna(py) and py <= 8 and yoy >= 25 and (pd.isna(mom) or mom >= 5): return f"🚀 低迷翻揚轉機 (前累計+{py:+.1f}% ➔ 本月+{yoy:.1f}%)"
        if yoy >= 20 and pd.notna(cumy) and cumy >= 20: return "🔥 長線強者恆強 (雙高成長)"
        return "📈 單月成長達標"
    for mkt_l, url in [("上市", "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"), ("上櫃", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O")]:
        try:
            r = requests.get(url, headers=headers, timeout=12, verify=False)
            if r.status_code == 200:
                for it in r.json():
                    c = str(it.get("公司代號", it.get("證券代號", ""))).strip()
                    if not (len(c) == 4 and c.isdigit()): continue
                    nm, ym, sec = str(it.get("公司名稱", "")).strip(), str(it.get("資料年月", "")).strip(), str(it.get("產業別", "")).strip()
                    cr, mom, yoy, cumr, cumy = to_f(it.get("營業收入-當月營收")), to_f(it.get("營業收入-上月比較增減(%)")), to_f(it.get("營業收入-去年同月增減(%)")), to_f(it.get("累計營業收入-當月累計營收")), to_f(it.get("累計營業收入-前期比較增減(%)"))
                    nt = str(it.get("備註", "")).strip()
                    if nt in ["-", "無", "NA", "None"]: nt = ""
                    pi = price_dict.get(c, {})
                    cp, vl, fl, sl = pi.get("close", np.nan), pi.get("vol_lots", 0), pi.get("fini_lots", 0), pi.get("sitc_lots", 0)
                    ee, pv, ei, _, vt = compute_eps_valuation(c, cp, sec, yoy, cumy, pi.get("pe", np.nan))
                    records_by_code[c] = {
                        "資料年月": ym, "市場": mkt_l, "代碼": c, "公司名稱": nm or pi.get("name", ""), "產業別": sec or "電子/其他",
                        "轉機與成長型態": build_ttag(cr, yoy, mom, cumr, cumy, sec, nt, c), "備註AI真偽鑑別": classify_revenue_note_quality(nt, sec, c),
                        "CB可轉債": "🔥含CB可轉債" if c in cb_set else "➖", "法人動向": format_inst_chip_tag(fl, sl), "外資買賣(張)": fl, "投信買賣(張)": sl,
                        "現價": cp, "今日%": pi.get("chg_pct", np.nan), "成交量(張)": vl, "預估EPS(元)": ee, "本益比(PE)": pv, "💎EPS合理切入價": ei, "估值診斷": vt,
                        "當月營收(億)": round(cr/1e5, 2) if pd.notna(cr) else np.nan, "月增MoM%": round(mom, 2) if pd.notna(mom) else np.nan,
                        "年增YoY%": round(yoy, 2) if pd.notna(yoy) else np.nan, "累計營收(億)": round(cumr/1e5, 2) if pd.notna(cumr) else np.nan,
                        "累計YoY%": round(cumy, 2) if pd.notna(cumy) else np.nan, "400張大戶%": f"{tdcc_dict[c]:.1f}%" if c in tdcc_dict else "未揭露",
                        "_tdcc_num": float(tdcc_dict.get(c, 0.0)), "財報營收備註說明": nt
                    }
        except Exception:
            continue
    return pd.DataFrame(list(records_by_code.values())).sort_values(by="年增YoY%", ascending=False, na_position="last").reset_index(drop=True) if records_by_code else pd.DataFrame()

@st.cache_data(ttl=1800, show_spinner=False)
def verify_24m_first_breakout_batch(codes_tuple, threshold_yoy=20.0):
    start_d, verified = (get_tw_now() - datetime.timedelta(days=1120)).strftime("%Y-%m-%d"), {}
    def _chk(code):
        try:
            r = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue&data_id={code}&start_date={start_d}", timeout=5, verify=False)
            if r.status_code == 200 and len(r.json().get("data", [])) >= 15:
                df_h = pd.DataFrame(r.json()["data"])
                df_h["ym"] = df_h["revenue_year"].astype(int) * 100 + df_h["revenue_month"].astype(int)
                df_h = df_h.sort_values("ym").reset_index(drop=True)
                rmap = dict(zip(df_h["ym"], df_h["revenue"].astype(float)))
                ys = [(f"{int(row['revenue_year'])-1911}{int(row['revenue_month']):02d}", round(((float(row["revenue"]) - rmap[(int(row['revenue_year'])-1)*100+int(row['revenue_month'])]) / rmap[(int(row['revenue_year'])-1)*100+int(row['revenue_month'])]) * 100.0, 1)) for _, row in df_h.iterrows() if ((int(row['revenue_year'])-1)*100+int(row['revenue_month'])) in rmap and rmap[(int(row['revenue_year'])-1)*100+int(row['revenue_month'])] > 0]
                if len(ys) >= 6:
                    lym, lyv = ys[-1]
                    prior = ys[:-1][-23:]
                    ms, lhit = 0, "無"
                    for p_ym, v in reversed(prior):
                        if v < threshold_yoy: ms += 1
                        else: lhit = p_ym; break
                    if ms >= len(prior) and len(prior) >= 12: return code, (True, 24, f"👑【2年首見破繭！】{lym}首破+{lyv:.1f}%")
                    if ms >= 6: return code, (True, ms, f"🌟【久違{ms}個月首破20%！】(前次{lhit}，本月+{lyv:.1f}%)")
                    return code, (False, ms, f"❌ 非首次 (僅隔{ms}個月)")
        except Exception:
            pass
        return code, None
    with ThreadPoolExecutor(max_workers=12) as ex:
        for fut in as_completed([ex.submit(_chk, c) for c in codes_tuple[:150]]):
            ck, vr = fut.result()
            if vr is not None: verified[ck] = vr
    return verified
