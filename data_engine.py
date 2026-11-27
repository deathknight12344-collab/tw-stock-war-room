import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import datetime, requests, urllib3, io, os, json
from concurrent.futures import ThreadPoolExecutor, as_completed

from config_events import (
    get_tw_now, get_volume_multiplier, is_zombie_stock_tiered,
    TDCC_PREV_CACHE_FILE, MSCI_THEME_BELLWETHERS,
    GRANARY_1_CSP_COMMON, GRANARY_2_POWER_THERMAL, GRANARY_3_EDGE_MEMORY,
    POLICY_DEFENSE_ARMOR, OLD_GRANARY_ODM_WATCH, ODM_Q3_OFFICIAL_BASE,
    CORE_WATCHLIST, FINANCIAL_BLUE_CHIPS, CONSTRUCTION_BLACKLIST, CORE_PORTFOLIO_16,
    ACTIVE_CB_BASE_SET, MARGIN_DILUTION_SET, MARGIN_EXPANSION_SET,
    evaluate_granary_and_anti_trap, format_inst_chip_tag, classify_revenue_note_quality
)
from market_feeds import (
    get_us_market_holiday_status, fetch_twse_disposition_codes, fetch_twse_notice_codes,
    fetch_twse_exdiv_soon, fetch_dynamic_msci_data, fetch_bond_etf_nav_and_premium,
    parse_mis_price, fetch_yahoo_v8_realtime, fetch_mis_realtime_batch,
    fetch_mis_realtime_core16, fetch_market_sector_indices, fetch_market_prices_fast,
    fetch_us_sectors_and_leaders, classify_night_session_window, fetch_auto_derivatives_and_margin_v63
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# 共用標記工具：高當沖警戒、三率分級紅牌與上市櫃標記
# ==========================================
def format_day_trade_tag(ratio_pct: float, note: str = "") -> str:
    if ratio_pct > 60.0:
        return f"{ratio_pct:.1f}%{note} 🚨極度過熱"
    if ratio_pct > 50.0:
        return f"{ratio_pct:.1f}%{note} ⚠️高當沖"
    return f"{ratio_pct:.1f}%{note}"

def build_3rates_and_mkt_tags(code: str, mkt_code: str, sector: str, fin_info: dict) -> tuple[str, str, str]:
    mkt_lbl = "(上市)" if mkt_code == "TW" else "(上櫃) ⚠️上櫃流動性控倉"
    gm, om, nm = fin_info.get("gm", np.nan), fin_info.get("om", np.nan), fin_info.get("nm", np.nan)
    gm_s = f"{gm:.1f}%" if pd.notna(gm) else "N/A"
    om_s = f"{om:.1f}%" if pd.notna(om) else "N/A"
    nm_s = f"{nm:.1f}%" if pd.notna(nm) else "N/A"
    rates_str = f"毛{gm_s}/營{om_s}/淨{nm_s}"

    m_flag = ""
    if pd.notna(gm):
        is_semi = any(k in sector for k in ["半導體", "晶圓", "封測", "IC設計", "矽智財", "探針"]) or code in ["2330", "3711", "2454", "2449", "6239", "3264", "3034", "2379", "3661", "3443"]
        is_comp = any(k in sector for k in ["CCL", "PCB", "載板", "散熱", "電源", "連接器", "鏡頭", "重電", "矽光子", "零組件"]) or code in ["2308", "2301", "2383", "2368", "3017", "3324", "3653", "3533", "3008", "1519"]
        if gm < 10.0:
            m_flag = " ⚠️低毛利稀釋(<10%)"
        elif is_semi and gm >= 22.0:
            m_flag = " ✅高毛利(≧22%)"
        elif is_comp and gm >= 20.0:
            m_flag = " ✅高毛利(≧20%)"
    return mkt_lbl, f"{rates_str}{m_flag}", m_flag.strip()

# ==========================================
# 1. 技術指標、四軌落底、線性維持率校準與全浮動三情境預測引擎
# ==========================================
def detect_4track_bottom_signals(o_arr, h_arr, l_arr, c_arr, v_arr, limit_up_cnt: int, adv_ratio_pct: float, vol_mult_use: float = 1.0, fmtqik_val_ratio: float = 0.0, breadth_is_stale: bool = False) -> dict:
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
        hit_2c = ((not breadth_is_stale) and (chg_1d >= 4.0) and ((limit_up_cnt >= 100) or (adv_ratio_pct >= 85.0))) or ((chg_1d >= 6.0) and (l0 >= c1 * 1.03))

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
            br_txt = "廣度待盤後結算" if breadth_is_stale else f"漲停 {limit_up_cnt}家/上漲比 {adv_ratio_pct:.0f}%"
            out["detail"] = f"量比 {vol_ratio:.2f}x ｜ 下影/實體 {sh_ratio:.2f}x ｜ {br_txt}"
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

def compute_dynamic_pure_stock_maint(twii_now, ma10, ma20, ma60, margin_30d_dd_pct: float = 0.0):
    raw_anchor = 0.35 * max(1.0, ma10) + 0.45 * max(1.0, ma20) + 0.20 * max(1.0, ma60)
    flush_adj = min(0.06, max(0.0, float(margin_30d_dd_pct)) / 100.0 * 0.08)
    eff_anchor = max(1.0, raw_anchor * (1.0 - flush_adj))
    return round(max(115.0, min(205.0, 166.67 * (twii_now / eff_anchor))), 1)

def compute_dynamic_market_forecast(
    fleet: dict, radar: dict, vix_now: float, sox_chg: float, upcoming_ev_rows: list[dict],
    fini_oi: int, fini_oi_delta: int, lots_00400a: float = 0.0, target_00400a_low: float = None,
    shares_3711_short: int = 300, fish_3711_price: float = 450.0, night_pct_override: float = None
) -> dict:
    tw = float(fleet["twii_now"])
    ma5, ma10, ma20, ma60, ma240 = fleet["ma5"], fleet["ma10"], fleet["ma20"], fleet["ma60"], fleet["ma240"]
    m_ma20, m_ma36 = fleet["m_ma20"], fleet["m_ma36"]
    low20 = float(fleet.get("twii_20d_low", tw * 0.90))
    atr_pts = max(tw * 0.012, float(fleet.get("atr_14_pts", tw * 0.022)))
    exit_t1 = target_00400a_low if (target_00400a_low is not None and target_00400a_low > 0) else fleet["etf_metrics"]["00400A"]["exit_t1"]
    exit_t2 = max(exit_t1 + 0.30, fleet["etf_metrics"]["00400A"]["exit_t2"])

    if night_pct_override is not None:
        night_pct = float(night_pct_override)
    else:
        dv = fleet["deriv"]
        night_pct = dv.get("night_tx_chg_pct", 0.0) if (dv.get("night_valid") and not dv.get("night_is_stale", False)) else 0.0

    bull_score = sum([tw >= ma5, tw >= ma10, tw >= ma240, radar["b_count"] >= 3, fini_oi_delta > 0, (vix_now < 25.0 or sox_chg > 2.0), night_pct >= 1.0])
    bear_score = (2 if fini_oi < -80000 else (1 if fini_oi < -60000 else 0)) + sum([tw < ma20, radar["is_policy_escape_wave"], vix_now >= 30.0, sox_chg <= -3.0, night_pct <= -1.5])

    act_400a = f"剩餘 {lots_00400a:.0f}張 00400A 逢彈 ({exit_t1:.2f}~{exit_t2:.2f}元) 收尾轉入 00985B；" if lots_00400a > 0 else "00400A 已清零完成股轉債；"
    short_bull_txt = f"日月光 {shares_3711_short:,} 股短單沿 5MA 移動停利續抱不追高；" if shares_3711_short > 0 else "獨立短單空手觀望不追高；"
    short_bear_txt = f"日月光 {shares_3711_short:,} 股短單嚴守防線收現金，" if shares_3711_short > 0 else "保留全額水庫四現金，"

    if radar["master_state"] in ["RED_LOCKED", "YELLOW"]:
        p_bull = int(np.clip(18 + bull_score * 4 - bear_score * 2, 14, 40))
        p_bear = int(np.clip(18 + bear_score * 4 - bull_score * 2, 16, 44))
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
             "未來 1～4 週預測路徑與目標區": f"先上測月線反壓 {res_mid:,.0f}點 ➔ 遇重大事件回測 {sup_rf:,.0f}點 打右腳 ➔ 再修復攻 {path_base[3]:,.0f}點",
             "確認／轉折分界": f"上檔月線 {ma20:,.0f} 反壓沉重，外資空單 ({fini_oi:+,}) 未大幅回補前易反覆築底",
             "四水庫實戰操作": f"{act_400a}嚴禁於 500元以上追高日月光，水庫四現金鎖定 {fish_3711_price:.0f}元區間及落底4/5共振再低接"},
            {"預測情境（圖中線色）": "🟢 樂觀路徑：軋空 V 轉突破月線", "動態機率": f"{p_bull}%",
             "未來 1～4 週預測路徑與目標區": f"連 3 日站穩 10MA({ma10:,.0f}) ➔ 帶量突破月線({ma20:,.0f}) ➔ 軋空直攻季線 {path_bull[2]:,.0f}～{path_bull[3]:,.0f}點",
             "確認／轉折分界": "須同時滿足「連 3 日站穩 10MA ＋ 外資空單連 3 日回補逾萬口 ＋ 美股續強」",
             "四水庫實戰操作": f"{short_bull_txt}待模組0解鎖後分批切入新穀倉1與 MSCI 主流"},
            {"預測情境（圖中線色）": "🔴 保守路徑：政策逃命波失守再探底", "動態機率": f"{p_bear}%",
             "未來 1～4 週預測路徑與目標區": f"反彈不過 10MA/月線即轉弱 ➔ 跌破年線({ma240:,.0f})與5MA({ma5:,.0f}) ➔ 回測前低 {low20:,.0f}～月20MA {m_ma20:,.0f}點",
             "確認／轉折分界": f"若重大事件轉鷹且收盤失守 5MA({ma5:,.0f}) 與年線({ma240:,.0f})",
             "四水庫實戰操作": f"{short_bear_txt}死抱水庫二傳家寶與 00985B 債息，靜待 {fish_3711_price:.0f}元區間黃金釣魚單成交"}
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
            {"預測情境（圖中線色）": "🔴 保守路徑：高檔過熱回測月線洗盤", "動態機率": f"{p_bear}%", "未來 1～4 週預測路徑與目標區": f"失守 5MA 回測月線 {ma20:,.0f}～季線 {ma60:,.0f}點", "確認／轉折分界": "爆量黑K跌破 10MA 或觸發黃燈前哨", "四水庫實戰操作": "優先收網多頭衛星股，保留現金轉入水庫三 00985B 防禦"}
        ]

    ev_marks = [{"days": ev["_days"], "label": f"{ev['日期'][5:]} {ev['_short'][:7]}"} for ev in upcoming_ev_rows if 1 <= ev["_days"] <= 30][:3]
    return {"mode_label": mode_label, "probs": {"base": p_base, "bull": p_bull, "bear": p_bear}, "paths": {"base": path_base, "bull": path_bull, "bear": path_bear}, "events_on_axis": ev_marks, "table_rows": table_rows}

# ==========================================
# 2. Layer 1 秒開主流程（新增 00933B / 00687B 報價、債券淨值折溢價與 OTC 背離差）
# ==========================================
@st.cache_data(ttl=600, show_spinner=False)
def download_fleet_history():
    tickers = ["^TWII", "^GSPC", "^SOX", "^VIX", "TWD=X", "00400A.TW", "00985B.TW", "00933B.TWO", "00687B.TWO", "00713.TW", "00631L.TW", "3711.TW", "AVGO", "NVDA", "SGOV"]
    try: return yf.download(tickers, period="5y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=120, show_spinner=False)
def download_fleet_60m():
    try: return yf.download(["^TWII"], period="1mo", interval="60m", progress=False, group_by="ticker", auto_adjust=False)
    except Exception: return pd.DataFrame()

def fetch_and_analyze_fleet_fast():
    rt_quotes = fetch_mis_realtime_core16()
    fast_prices, twii_fmtqik, breadth_cached = fetch_market_prices_fast()
    sec_ind = fetch_market_sector_indices()
    bond_nav = fetch_bond_etf_nav_and_premium(("00985B", "00933B", "00687B"))
    breadth = dict(breadth_cached)
    data, data_60m = download_fleet_history(), download_fleet_60m()
    now_tw = get_tw_now()
    today_str = now_tw.strftime("%Y-%m-%d")
    is_open_today = (now_tw.weekday() < 5) and ((now_tw.hour * 60 + now_tw.minute) >= 540)
    vol_mult_now, vol_note = get_volume_multiplier()

    core_rt_up = sum(1 for c, _ in CORE_PORTFOLIO_16 if c in rt_quotes and rt_quotes[c].get("chg_pct", 0.0) > 0.0)
    core_rt_dn = sum(1 for c, _ in CORE_PORTFOLIO_16 if c in rt_quotes and rt_quotes[c].get("chg_pct", 0.0) < 0.0)
    core_rt_tot = core_rt_up + core_rt_dn
    breadth["core_rt_up"], breadth["core_rt_dn"] = core_rt_up, core_rt_dn
    breadth["core_rt_adv_pct"] = round((core_rt_up / max(1, core_rt_tot)) * 100.0, 1) if core_rt_tot > 0 else 50.0

    twii_now, twii_chg_pct = (twii_fmtqik["close"] or 39151.8), (twii_fmtqik["chg_pct"] if twii_fmtqik["close"] > 0 else 0.0)
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
        "00400A": {"p": 11.05, "ma10": 11.50, "ma20": 12.10, "exit_t1": 11.35, "exit_t2": 12.05, "div_latest": 0.06, "div_ttm": 0.60},
        "00985B": {"p": 10.45, "ma20": 10.35, "div_latest": 0.053, "div_ttm": 0.596},
        "00933B": {"p": 15.85, "ma20": 15.95, "div_latest": 0.082, "div_ttm": 0.96},
        "00687B": {"p": 28.10, "ma20": 28.40, "div_latest": 0.36, "div_ttm": 1.44},
        "00713": {"p": 61.40, "ma20": 59.50, "div_latest": 1.25, "div_ttm": 3.81},
        "00631L": {"p": 29.00, "ma5": 28.00, "ma20": 31.50},
        "3711": {"p": 511.0, "ma5": 493.6, "ma20": 510.0},
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

                bottom_4track = detect_4track_bottom_signals(
                    o_tw, h_tw, l_tw, c_tw, v_tw, breadth["limit_up"], breadth["adv_ratio_pct"],
                    vol_mult_now if used_rt_today else 1.0, fmtqik_val_ratio, breadth_is_stale=breadth.get("is_stale_intraday", False)
                )

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

            for sym_e, code_e in [("00400A.TW", "00400A"), ("00985B.TW", "00985B"), ("00933B.TWO", "00933B"), ("00687B.TWO", "00687B"), ("00713.TW", "00713"), ("00631L.TW", "00631L"), ("3711.TW", "3711"), ("AVGO", "AVGO"), ("NVDA", "NVDA"), ("SGOV", "SGOV")]:
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
                            t1_val = round(max(p_e * 1.02, min(m10_e, p_e * 1.08)), 2)
                            t2_val = round(max(t1_val + 0.35, min(m20_e, p_e * 1.15)), 2)
                            etf_metrics["00400A"].update({"ma10": m10_e, "ma20": m20_e, "exit_t1": t1_val, "exit_t2": t2_val})
                        elif code_e in ["00985B", "00933B", "00687B", "00713", "00631L", "3711"] and len(c_e) >= 20:
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

    rt_o00 = rt_quotes.get("o00", {})
    otc_close, otc_chg = rt_o00.get("close", 0.0), rt_o00.get("chg_pct", None)
    otc_div = round(otc_chg - twii_chg_pct, 2) if otc_chg is not None else None
    if otc_chg is not None:
        if twii_chg_pct >= 0.3 and otc_chg <= -0.5: sec_tag = f"⚠️ 拉積撐盤／櫃買背離 ({otc_div:+.2f}%)"
        elif twii_chg_pct >= 0.8 and otc_chg >= 0.8: sec_tag = "🚀 權值與中小型健康普漲"
        elif otc_chg >= twii_chg_pct + 0.8: sec_tag = "🔥 內資中小型領軍強攻"
        elif twii_chg_pct <= -0.8 and otc_chg <= -0.8: sec_tag = "🚨 權值與中小型齊殺"
        else: sec_tag = "⚖️ 權值與中小型分化輪動"
    else:
        sec_tag = "待櫃買同步"
    sector_struct = {
        "otc_close": otc_close, "otc_chg": otc_chg, "otc_div": otc_div,
        "elec_chg": sec_ind.get("elec_chg"), "semi_chg": sec_ind.get("semi_chg"),
        "fin_chg": sec_ind.get("fin_chg"), "non_ef_chg": sec_ind.get("non_ef_chg"),
        "date_iso": sec_ind.get("date_iso", ""), "tag": sec_tag
    }

    deriv = fetch_auto_derivatives_and_margin_v63(twii_now, twii_date_iso)
    deriv["stock_only_maint_pct"] = compute_dynamic_pure_stock_maint(twii_now, ma10, ma20, ma60, deriv.get("margin_30d_dd_pct", 0.0))
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
        "breadth": breadth, "sector_struct": sector_struct, "bottom_4track": bottom_4track,
        "twii_date_iso": twii_date_iso, "date_str": date_str, "quote_src": quote_src, "vol_note": vol_note,
        "deriv": deriv, "etf_metrics": etf_metrics, "bond_nav": bond_nav, "rt_quotes": rt_quotes, "fast_prices": fast_prices
    }

# ==========================================
# 3. 真實當沖率、個股資券借、三率 (毛/營/淨) 與按需個股掃描
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
    headers, dt_shares, now_tw, dt_date_iso = {"User-Agent": "Mozilla/5.0"}, {}, get_tw_now(), ""
    for d_back in range(0, 5):
        chk_dt = now_tw - datetime.timedelta(days=d_back)
        if chk_dt.weekday() >= 5: continue
        ymd = chk_dt.strftime("%Y%m%d")
        try:
            r_tw = requests.get(f"https://www.twse.com.tw/rwd/zh/dayTrading/TWTB4U?date={ymd}&response=json", headers=headers, timeout=6, verify=False)
            if r_tw.status_code == 200:
                j_tw = r_tw.json()
                if j_tw.get("stat") == "OK" and j_tw.get("tables"):
                    found_tw = False
                    for tbl in j_tw["tables"]:
                        fields = [str(f).strip() for f in tbl.get("fields", [])]
                        rows = tbl.get("data", [])
                        if len(rows) > 50 and any("代號" in f for f in fields):
                            c_idx = next((i for i, f in enumerate(fields) if "代號" in f), 0)
                            v_idx = next((i for i, f in enumerate(fields) if "成交股數" in f), 3)
                            for row in rows:
                                if len(row) > max(c_idx, v_idx):
                                    c, vs = str(row[c_idx]).strip(), str(row[v_idx]).replace(",", "").strip()
                                    if len(c) == 4 and c.isdigit() and vs.replace(".", "").isdigit() and float(vs) > 0:
                                        dt_shares[c] = float(vs)
                            if len(dt_shares) > 50:
                                dt_date_iso = chk_dt.strftime("%Y-%m-%d"); found_tw = True; break
                    if found_tw: break
        except Exception:
            continue
    try:
        r_otc = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_day_trading", headers=headers, timeout=6, verify=False)
        if r_otc.status_code == 200 and isinstance(r_otc.json(), list):
            for row in r_otc.json():
                c = str(row.get("SecuritiesCompanyCode", row.get("Code", ""))).strip()
                if not (len(c) == 4 and c.isdigit()): continue
                v_raw = next((str(row[k]).replace(",", "").strip() for k in ["DayTradingVolume", "DayTradingShares", "Volume", "BuySellVolume"] if k in row and str(row[k]).strip()), "")
                if v_raw.replace(".", "").isdigit() and float(v_raw) > 0: dt_shares[c] = float(v_raw)
    except Exception:
        pass
    today_iso = now_tw.strftime("%Y-%m-%d")
    dt_note = f"({dt_date_iso[5:]})" if (dt_date_iso and dt_date_iso != today_iso) else ("(前日)" if ((now_tw.weekday() < 5) and (540 <= (now_tw.hour * 60 + now_tw.minute) < 990)) else "")
    return dt_shares, dt_note

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_twse_sbl_map() -> tuple[dict, str]:
    headers, sbl_map, sbl_date, now_tw = {"User-Agent": "Mozilla/5.0"}, {}, "", get_tw_now()
    for d_back in range(0, 5):
        chk_dt = now_tw - datetime.timedelta(days=d_back)
        if chk_dt.weekday() >= 5: continue
        ymd = chk_dt.strftime("%Y%m%d")
        try:
            r = requests.get(f"https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U?date={ymd}&response=json", headers=headers, timeout=6, verify=False)
            if r.status_code == 200:
                j = r.json()
                rows = j.get("data", []) or next((tb.get("data", []) for tb in j.get("tables", []) if len(tb.get("data", [])) > 100), [])
                if len(rows) > 100:
                    for row in rows:
                        if len(row) >= 13 and len(str(row[0]).strip()) == 4 and str(row[0]).strip().isdigit():
                            try:
                                prev_sh, cur_sh = float(str(row[8]).replace(",", "").strip() or 0), float(str(row[12]).replace(",", "").strip() or 0)
                                sbl_map[str(row[0]).strip()] = {"sbl_bal": int(round(cur_sh / 1000.0)), "sbl_chg": int(round((cur_sh - prev_sh) / 1000.0))}
                            except Exception: pass
                    if len(sbl_map) > 100: sbl_date = chk_dt.strftime("%Y-%m-%d"); break
        except Exception:
            continue
    return sbl_map, sbl_date

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_stock_margin_short_sbl_batch(codes_tuple: tuple) -> dict:
    sbl_map, sbl_date = fetch_twse_sbl_map()
    start_d, out = (get_tw_now() - datetime.timedelta(days=14)).strftime("%Y-%m-%d"), {}
    def _one(code):
        info = {"margin_str": "N/A (請聯網補查)", "exact_dt_str": ""}
        m_part, m_date = "", ""
        try:
            r_m = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMarginPurchaseShortSale&data_id={code}&start_date={start_d}", timeout=4.0, verify=False)
            if r_m.status_code == 200 and r_m.json().get("data"):
                lr = pd.DataFrame(r_m.json()["data"]).sort_values("date").iloc[-1]
                m_date = str(lr.get("date", ""))
                mb, mp = int(round(float(lr.get("MarginPurchaseTodayBalance", 0) or 0))), int(round(float(lr.get("MarginPurchaseYesterdayBalance", 0) or 0)))
                sb, sp = int(round(float(lr.get("ShortSaleTodayBalance", 0) or 0))), int(round(float(lr.get("ShortSaleYesterdayBalance", 0) or 0)))
                s_ratio = round((sb / max(1, mb)) * 100.0, 1) if mb > 0 else 0.0
                m_part = f"融資{mb:,}張({mb - mp:+,}) / 融券{sb:,}張({sb - sp:+,}・券資比{s_ratio:.1f}%)"
        except Exception:
            pass
        sbl_info = sbl_map.get(code)
        sbl_part = f" / 借券賣出餘額{sbl_info['sbl_bal']:,}張({sbl_info['sbl_chg']:+,})" if sbl_info else " / 借券賣出:N/A"
        d_tag = m_date or sbl_date
        if m_part: info["margin_str"] = f"{m_part}{sbl_part}" + (f" [{d_tag}]" if d_tag else "")
        elif sbl_info: info["margin_str"] = f"融資券:N/A{sbl_part}" + (f" [{sbl_date}]" if sbl_date else "")
        try:
            r_dt = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDayTrading&data_id={code}&start_date={start_d}", timeout=3.5, verify=False)
            r_pr = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={code}&start_date={start_d}", timeout=3.5, verify=False)
            if r_dt.status_code == 200 and r_pr.status_code == 200 and r_dt.json().get("data") and r_pr.json().get("data"):
                df_d, df_p = pd.DataFrame(r_dt.json()["data"]).sort_values("date"), pd.DataFrame(r_pr.json()["data"]).sort_values("date")
                ld_dt, dt_v = str(df_d["date"].iloc[-1]), float(df_d["Volume"].iloc[-1] or 0)
                match_p = df_p[df_p["date"] == ld_dt]
                if not match_p.empty and dt_v > 0:
                    tot_v = float(match_p["Trading_Volume"].iloc[-1] or 0)
                    if tot_v > 0 and 0.0 < (dt_v / tot_v) * 100.0 <= 99.9:
                        info["exact_dt_str"] = format_day_trade_tag(round((dt_v / tot_v) * 100.0, 1), f"({ld_dt[5:]})")
        except Exception:
            pass
        return code, info
    with ThreadPoolExecutor(max_workers=5) as ex:
        for fut in as_completed([ex.submit(_one, c) for c in list(codes_tuple)[:6]]):
            ck, val = fut.result(); out[ck] = val
    return out

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_official_3rates_and_cum_eps():
    """v65.2 強化：同步抓取最新單季 毛利率 (gm)、營益率 (om)、淨利率 (nm) 與累計 EPS"""
    headers, fin_dict = {"User-Agent": "Mozilla/5.0"}, {}
    def clean_num(val):
        try:
            s = str(val).replace(",", "").replace("%", "").replace("+", "").strip()
            return float(s) if s not in ["", "-", "--", "N/A", "None"] else np.nan
        except Exception: return np.nan
    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap17_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap17_O"]:
        try:
            r = requests.get(url, headers=headers, timeout=7, verify=False)
            if r.status_code == 200:
                for row in r.json():
                    c = str(row.get("公司代號", row.get("證券代號", ""))).strip()
                    if len(c) == 4 and c.isdigit():
                        fin_dict[c] = {
                            "gm": clean_num(next((v for k, v in row.items() if "毛利率" in k), np.nan)),
                            "om": clean_num(next((v for k, v in row.items() if "營業利益率" in k or "營益率" in k), np.nan)),
                            "nm": clean_num(next((v for k, v in row.items() if "稅後純益率" in k or "淨利率" in k or "純益率" in k), np.nan)),
                            "cum_eps": np.nan
                        }
        except Exception: continue
    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci"]:
        try:
            r = requests.get(url, headers=headers, timeout=7, verify=False)
            if r.status_code == 200:
                for row in r.json():
                    c = str(row.get("公司代號", row.get("證券代號", ""))).strip()
                    if len(c) == 4 and c.isdigit():
                        eps_v = clean_num(next((v for k, v in row.items() if "基本每股盈餘" in k or "每股盈餘" in k), np.nan))
                        if c not in fin_dict: fin_dict[c] = {"gm": np.nan, "om": np.nan, "nm": np.nan, "cum_eps": eps_v}
                        else: fin_dict[c]["cum_eps"] = eps_v
        except Exception: continue
    return fin_dict

@st.cache_data(ttl=600, show_spinner=False)
def fetch_full_market_enrichment():
    fast_p, _, _ = fetch_market_prices_fast()
    price_dict = {k: dict(v) for k, v in fast_p.items()}
    tdcc_dict, rev_dict, headers, now_tw = {}, {}, {"User-Agent": "Mozilla/5.0"}, get_tw_now()

    try:
        dt_map, dt_note = fetch_day_trade_shares_batch()
        for code, dt_sh in dt_map.items():
            if code in price_dict and float(price_dict[code].get("vol_shares", 0)) > 0 and dt_sh > 0:
                ratio_dt = round((dt_sh / float(price_dict[code]["vol_shares"])) * 100.0, 1)
                if 0.1 <= ratio_dt <= 99.5: price_dict[code]["day_trade_str"] = format_day_trade_tag(ratio_dt, dt_note)
    except Exception: pass

    for d_back in range(0, 5):
        chk_dt = now_tw - datetime.timedelta(days=d_back)
        if chk_dt.weekday() >= 5: continue
        d_iso_t86 = chk_dt.strftime("%Y-%m-%d")
        try:
            r_t86 = requests.get(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={chk_dt.strftime('%Y%m%d')}&selectType=ALLBUT0999&response=json", headers=headers, timeout=7, verify=False)
            if r_t86.status_code == 200 and len(r_t86.json().get("data", [])) > 100:
                for row in r_t86.json()["data"]:
                    c = str(row[0]).strip()
                    if c in price_dict and len(row) >= 12:
                        try:
                            f_sh = float(str(row[4]).replace(",", "").strip()) + (float(str(row[7]).replace(",", "").strip()) if len(row) >= 8 else 0.0)
                            price_dict[c]["fini_lots"] = int(round(f_sh / 1000.0))
                            price_dict[c]["sitc_lots"] = int(round(float(str(row[10]).replace(",", "").strip()) / 1000.0))
                            price_dict[c]["dealer_lots"] = int(round(float(str(row[11]).replace(",", "").strip()) / 1000.0))
                            price_dict[c]["inst_date"] = d_iso_t86
                        except Exception: pass
                break
        except Exception: continue

    for d_back in range(0, 5):
        chk_dt = now_tw - datetime.timedelta(days=d_back)
        if chk_dt.weekday() >= 5: continue
        roc_d, d_iso_otc = f"{chk_dt.year - 1911}/{chk_dt.month:02d}/{chk_dt.day:02d}", chk_dt.strftime("%Y-%m-%d")
        try:
            r_o = requests.get(f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d={roc_d}&o=json", headers=headers, timeout=7, verify=False)
            if r_o.status_code == 200 and len(r_o.json().get("aaData", [])) > 100:
                for row in r_o.json()["aaData"]:
                    c = str(row[0]).strip()
                    if c in price_dict and len(row) >= 14:
                        try:
                            f_sh = float(str(row[10]).replace(",", "").strip()) if len(row) >= 20 else float(str(row[4]).replace(",", "").strip())
                            s_sh = float(str(row[13]).replace(",", "").strip()) if len(row) >= 20 else float(str(row[7]).replace(",", "").strip())
                            d_sh = float(str(row[22]).replace(",", "").strip()) if len(row) >= 23 else (float(str(row[10]).replace(",", "").strip()) if len(row) < 20 else 0.0)
                            price_dict[c]["fini_lots"], price_dict[c]["sitc_lots"], price_dict[c]["dealer_lots"], price_dict[c]["inst_date"] = int(round(f_sh / 1000.0)), int(round(s_sh / 1000.0)), int(round(d_sh / 1000.0)), d_iso_otc
                        except Exception: pass
                break
        except Exception: continue

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
        except Exception: pass

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
                        v1000 = t1000_map.get(ck); price_dict[ck]["tdcc_1000"] = v1000
                        if ck in p400: price_dict[ck]["tdcc_400_delta"] = round(v400 - float(p400[ck]), 2)
                        if v1000 is not None and ck in p1000: price_dict[ck]["tdcc_1000_delta"] = round(v1000 - float(p1000[ck]), 2)
                if cur_d and prev_snap.get("cur_date") != cur_d:
                    try:
                        with open(TDCC_PREV_CACHE_FILE, "w", encoding="utf-8") as f:
                            json.dump({"cur_date": cur_d, "cur_400": tdcc_dict, "cur_1000": t1000_map, "prev_date": prev_snap.get("prev_date", ""), "prev_400": p400, "prev_1000": p1000}, f)
                    except Exception: pass
    except Exception: pass

    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap05_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"]:
        try:
            r_rev = requests.get(url, headers=headers, timeout=10, verify=False)
            if r_rev.status_code == 200:
                for row in r_rev.json():
                    c, sec_s = str(row.get("公司代號", row.get("證券代號", ""))).strip(), str(row.get("產業別", "")).strip()
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
        except Exception: continue
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
    if code in MARGIN_DILUTION_SET or (pd.notna(gm_v) and gm_v < 10.0 and not is_fin) or (r6 > 10 and pd.notna(om_v) and om_v < 4.2 and not is_fin): T, tb = 0.88, "⚠️毛利稀釋T=0.88"
    elif code in MARGIN_EXPANSION_SET or (pd.notna(gm_v) and pd.notna(om_v) and gm_v >= 28 and om_v >= 15 and s_yoy >= c_yoy and s_yoy >= 15): T, tb = 1.12, "🔥三率三升T=1.12"
    else: T, tb = 1.00, "⚖️三率持平T=1.00"
    W = 0.78 if is_fin else 0.74
    cf = (W + (1.0 - W) * M * T) * 0.95
    tech_e = round(float(support_price), 2) if (support_price is not None and pd.notna(support_price) and support_price > 0) else round(curr_p * 0.96, 2)
    if pe_official is not None and pd.notna(pe_official) and 0.5 < float(pe_official) <= 65.0:
        pe_v = round(float(pe_official), 1); ttm = curr_p / pe_v
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
    """v65.2 強化：季報防墊料（存貨 QoQ >= 20% 標警戒）＋ 多季「三率三升（季增/年增）」自動驗算"""
    base_res = dict(ODM_Q3_OFFICIAL_BASE[code]) if code in ODM_Q3_OFFICIAL_BASE else {"ok": False, "inv_qoq": None, "ar_qoq": None, "ocf_positive": None, "note": "N/A (交由淘金客聯網查核)"}
    if code in ODM_Q3_OFFICIAL_BASE: base_res["ok"] = True
    base_res["rates_trend"] = ""
    try:
        tkr = yf.Ticker(f"{code}.{'TW' if mkt == 'TW' else 'TWO'}")
        if not base_res.get("ok"):
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
                inv_warn = " ⚠️防墊料警戒" if (inv_q is not None and inv_q >= 20.0) else ""
                i_s = f"存貨QoQ {inv_q:+.1f}%{inv_warn}" if inv_q is not None else "存貨QoQ N/A"
                a_s = f"應收QoQ {ar_q:+.1f}%" if ar_q is not None else "應收QoQ N/A"
                o_s = ("營業現金流為正(✅)" if ocf_p else "營業現金流為負(⚠️)") if ocf_p is not None else "OCF N/A"
                base_res.update({"ok": True, "inv_qoq": inv_q, "ar_qoq": ar_q, "ocf_positive": ocf_p, "note": f"{i_s} / {a_s} / {o_s}"})

        # 多季三率三升（季增 / 年增）自動比對
        inc = tkr.quarterly_income_stmt
        if inc is not None and not inc.empty and inc.shape[1] >= 2:
            def _find_r(keys):
                return next((i for i in inc.index if any(k in str(i) for k in keys)), None)
            r_rev, r_gp, r_op, r_ni = _find_r(["Total Revenue", "Operating Revenue"]), _find_r(["Gross Profit"]), _find_r(["Operating Income", "EBIT"]), _find_r(["Net Income"])
            if all(x is not None for x in [r_rev, r_gp, r_op, r_ni]):
                def _q_rates(col_i):
                    rv = float(inc.loc[r_rev].iloc[col_i])
                    if pd.isna(rv) or rv <= 0: return None
                    return (float(inc.loc[r_gp].iloc[col_i]) / rv * 100.0, float(inc.loc[r_op].iloc[col_i]) / rv * 100.0, float(inc.loc[r_ni].iloc[col_i]) / rv * 100.0)
                q0, q1 = _q_rates(0), _q_rates(1)
                q4 = _q_rates(4) if inc.shape[1] >= 5 else None
                tags = []
                if q0 and q1 and all(pd.notna(x) for x in q0 + q1):
                    if q0[0] > q1[0] and q0[1] > q1[1] and q0[2] > q1[2]: tags.append("🔥季增三率三升")
                if q0 and q4 and all(pd.notna(x) for x in q0 + q4):
                    if q0[0] > q4[0] and q0[1] > q4[1] and q0[2] > q4[2]: tags.append("🔥年增三率三升")
                if tags: base_res["rates_trend"] = "＋".join(tags)
    except Exception:
        pass
    return base_res

@st.cache_data(ttl=43200, show_spinner=False)
def fetch_tdcc_weekly_delta_ondemand(codes_tuple: tuple) -> dict:
    out, start_d = {}, (get_tw_now() - datetime.timedelta(days=28)).strftime("%Y-%m-%d")
    def _one(code):
        try:
            r = requests.get(f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockHoldingSharesPer&data_id={code}&start_date={start_d}", timeout=3.5, verify=False)
            if r.status_code == 200 and r.json().get("data"):
                df_t = pd.DataFrame(r.json()["data"]); dates = sorted(df_t["date"].unique())
                if len(dates) >= 2:
                    lv400 = {"400,001-600,000", "600,001-800,000", "800,001-1,000,000", "more than 1,000,001", "12", "13", "14", "15"}
                    lv1000 = {"more than 1,000,001", "15"}
                    cs, ps = df_t[df_t["date"] == dates[-1]], df_t[df_t["date"] == dates[-2]]
                    p400_c, p400_p = float(cs[cs["HoldingSharesLevel"].astype(str).isin(lv400)]["percent"].sum()), float(ps[ps["HoldingSharesLevel"].astype(str).isin(lv400)]["percent"].sum())
                    p1000_c, p1000_p = float(cs[cs["HoldingSharesLevel"].astype(str).isin(lv1000)]["percent"].sum()), float(ps[ps["HoldingSharesLevel"].astype(str).isin(lv1000)]["percent"].sum())
                    if p400_c > 0 and p400_p > 0:
                        return code, {"p400": round(p400_c, 2), "d400": round(p400_c - p400_p, 2), "p1000": round(p1000_c, 2), "d1000": round(p1000_c - p1000_p, 2), "date": str(dates[-1])}
        except Exception: pass
        return code, None
    with ThreadPoolExecutor(max_workers=5) as ex:
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
    except Exception: pass
    return res

@st.cache_data(ttl=300, show_spinner=False)
def fetch_ondemand_stocks_detail(codes_tuple: tuple, twii_20d_ret: float, macro_withdrawal_alert: bool) -> dict:
    codes = [c for c in list(codes_tuple)[:6] if len(str(c)) == 4 and str(c).isdigit()]
    if not codes: return {}
    price_dict, tdcc_dict, rev_dict = fetch_full_market_enrichment()
    fin_3rates_all = fetch_official_3rates_and_cum_eps()
    disp_map, notice_set, exdiv_map, cb_set = fetch_twse_disposition_codes(), fetch_twse_notice_codes(), fetch_twse_exdiv_soon(5), fetch_active_cb_codes()
    vol_mult, _ = get_volume_multiplier()
    now_tw = get_tw_now()
    today_str, today_ymd = now_tw.strftime("%Y-%m-%d"), now_tw.strftime("%Y%m%d")
    is_open = (now_tw.weekday() < 5) and ((now_tw.hour * 60 + now_tw.minute) >= 540)
    c_pairs = [(c, price_dict.get(c, {}).get("market", "TW")) for c in codes]
    rt_quotes = fetch_mis_realtime_batch(tuple(c_pairs))
    yf_syms = [f"{c}.{'TW' if m == 'TW' else 'TWO'}" for c, m in c_pairs]
    raw_df = yf.download(yf_syms, period="1y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)

    out = {}
    for (code, mkt_code), yf_tkr in zip(c_pairs, yf_syms):
        try:
            df_s = raw_df if (len(yf_syms) == 1 and "Close" in raw_df.columns) else (raw_df[yf_tkr] if (not raw_df.empty and yf_tkr in raw_df) else pd.DataFrame())
            if df_s.empty: continue
            df_s = df_s.dropna(subset=["Open", "High", "Low", "Close"]).copy()
            if len(df_s) < 25: continue
            o, c, h, l, v = df_s["Open"].values.astype(float), df_s["Close"].values.astype(float), df_s["High"].values.astype(float), df_s["Low"].values.astype(float), df_s["Volume"].values.astype(float) / 1000.0
            last_kd, rt = df_s.index[-1].strftime("%Y-%m-%d"), rt_quotes.get(code, {})
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
            chg_3d = round((curr_p - float(c[-4])) / max(0.01, float(c[-4])) * 100, 2) if len(c) >= 4 else chg_1d
            est_vol = int(round(v[-1] * (vol_mult if is_open else 1.0)))
            yest_vol, ma5_vol = max(1.0, float(v[-2])), max(1.0, float(np.mean(v[-6:-1])))
            yod_ratio, vol_ratio = round(est_vol / yest_vol, 2), round(est_vol / ma5_vol, 2)
            ma5, ma10, ma20 = round(float(np.mean(c[-5:])), 2), round(float(np.mean(c[-10:])), 2), round(float(np.mean(c[-20:])), 2)
            ma60 = round(float(np.mean(c[-60:])), 2) if len(c) >= 60 else ma20
            ma120 = round(float(np.mean(c[-120:])), 2) if len(c) >= 120 else ma60
            ma240 = round(float(np.mean(c[-240:])), 2) if len(c) >= 240 else ma120
            w_c, m_c = df_s["Close"].resample("W-FRI").last().dropna().values.astype(float), df_s["Close"].resample("ME").last().dropna().values.astype(float)
            if len(w_c) > 0: w_c[-1] = curr_p
            if len(m_c) > 0: m_c[-1] = curr_p
            w_ma10, m_ma5 = (round(float(np.mean(w_c[-10:])), 2) if len(w_c) >= 5 else ma60), (round(float(np.mean(m_c[-5:])), 2) if len(m_c) >= 3 else ma120)
            deduct10_avg = round(float(np.mean(c[-20:-10])), 2) if len(c) >= 20 else ma20
            deduct_20ma_str = f"✅低扣助漲({deduct10_avg})" if deduct10_avg < curr_p else f"⚠️高扣反壓({deduct10_avg})"
            bb_up = round(ma20 + 2.0 * float(np.std(c[-20:])), 2)
            sk, sd, sj, _, _ = compute_kdj(h[-60:], l[-60:], c[-60:])
            pdi, mdi, adx, _, _, atr_pct = compute_dmi_and_atr(h[-90:], l[-90:], c[-90:])
            dmi_tag, _ = classify_dmi_direction(pdi, mdi, adx, sj)
            dmi_str = f"+DI:{pdi}/-DI:{mdi}/ADX:{adx} [{dmi_tag}]" if pd.notna(adx) else "N/A"
            atr_s = (f"{atr_pct:.1f}% ⚠️高波動(建議收盤確認/倉位減半)" if atr_pct > 4.5 else f"{atr_pct:.1f}%") if pd.notna(atr_pct) else "N/A"
            bias10 = round(((curr_p - ma10) / ma10) * 100.0, 1) if ma10 > 0 else np.nan
            bias20 = round(((curr_p - ma20) / ma20) * 100.0, 1) if ma20 > 0 else np.nan
            b10_s = "N/A" if pd.isna(bias10) else (f"{bias10:+.1f}% 🚨極度過熱" if bias10 > 12.0 else (f"{bias10:+.1f}% ⚠️追高過熱" if bias10 > 10.0 else f"{bias10:+.1f}%"))
            b20_s = f"{bias20:+.1f}%" if pd.notna(bias20) else "N/A"
            drng = float(h[-1] - l[-1])
            up_sh_p = 0.0 if drng < 1e-6 else round(((float(h[-1]) - max(float(o[-1]), float(c[-1]))) / drng) * 100.0, 1)
            cp_p = (100.0 if chg_1d >= 0 else 0.0) if drng < 1e-6 else round(((float(c[-1]) - float(l[-1])) / drng) * 100.0, 1)
            cp_s = f"{cp_p:.0f}% (收破中軸)" if cp_p < 50.0 else f"{cp_p:.0f}%"
            p_meta = price_dict.get(code, {})
            name = p_meta.get("name") or CORE_WATCHLIST.get(code, (code, "", mkt_code))[0]
            sector = p_meta.get("sector") or CORE_WATCHLIST.get(code, (code, "電子/一般", mkt_code))[1]
            mkt_lbl, rates_str, _ = build_3rates_and_mkt_tags(code, mkt_code, sector, fin_3rates_all.get(code, {}))
            obv_t, _ = compute_obv_trend(c[-60:], v[-60:])
            rs20_s = f"{round(((curr_p - float(c[-21])) / float(c[-21])) * 100.0 - twii_20d_ret, 1):+.1f}%" if (len(c) >= 21 and c[-21] > 0) else "N/A"
            t400, t1000, d400, d1000 = tdcc_dict.get(code), p_meta.get("tdcc_1000"), p_meta.get("tdcc_400_delta"), p_meta.get("tdcc_1000_delta")
            tdcc_s = f"400張:{t400:.1f}%{f'({d400:+.2f}%)' if d400 is not None else ''}/千張:{f'{t1000:.1f}%' + (f'({d1000:+.2f}%)' if d1000 is not None else '') if t1000 is not None else 'N/A'}" if t400 is not None else "N/A"
            raw_ceil = max(bb_up, round(float(np.max(h[-21:-1])), 2))
            ceil_p = round(curr_p * 1.07, 2) if raw_ceil <= curr_p else round(raw_ceil, 2)
            iron_p = round(min(curr_p, max(ma10, float(l[-1])) if curr_p >= ma10 else min(ma20, float(l[-1]))), 2)
            stop_p = round(min(ma60, float(np.min(l[-20:]))), 2)
            yoy_v, cum_v, pe_o = rev_dict.get(code), p_meta.get("cum_yoy"), p_meta.get("pe", np.nan)
            fl, sl, dl, i_dt = p_meta.get("fini_lots", 0), p_meta.get("sitc_lots", 0), p_meta.get("dealer_lots", 0), p_meta.get("inst_date", "")
            inst_sum_str = f"外{fl:+,}/投{sl:+,}/自{dl:+,}=合計{fl+sl+dl:+,}張" + (f" [{i_dt[5:]}]" if i_dt else "")
            g_tag, _, g_short = evaluate_granary_and_anti_trap(code, yoy_v, macro_withdrawal_alert)
            est_e, pe_d, ep_in, ep_tg, v_diag = compute_eps_valuation(code, curr_p, sector, yoy_v, cum_v, pe_o, iron_p)
            out[code] = {
                "代碼": code, "股名": name, "市場別": mkt_lbl, "_raw_name": name, "_mkt": mkt_code, "族群": sector,
                "三率(毛/營/淨)": rates_str, "穀倉賽道與防墊料": g_tag, "現價": curr_p, "今日%": chg_1d, "3日%": chg_3d,
                "短線屬性": f"按需快檢｜{g_short}", "_pattern_tag": f"按需快檢｜{g_short}",
                "DMI方向雙檢": dmi_str, "日J值": sj, "20MA扣抵": deduct_20ma_str, "BIAS10": b10_s, "BIAS20": b20_s,
                "5MA量比": vol_ratio, "上影線%": up_sh_p, "收盤位階": cp_s, "當沖率": p_meta.get("day_trade_str", "N/A"),
                "OBV趨勢": obv_t, "RS20D%": rs20_s, "大戶400/千張": tdcc_s, "ATR%波動尺": atr_s,
                "法人動向": format_inst_chip_tag(fl, sl), "_inst_sum": inst_sum_str, "_inst_date": i_dt,
                "預估EPS(元)": est_e, "本益比(PE)": pe_d, "💎EPS合理切入價": ep_in, "🎯EPS目標價": ep_tg, "估值診斷": v_diag,
                "昨日量比": yod_ratio, "預估量(張)": est_vol, "天花板(反壓)": ceil_p, "月線承接": ma20, "鋼鐵防線": iron_p, "極限停損": stop_p,
                "單月營收YoY": f"{yoy_v:+.1f}%" if yoy_v is not None else "待公告", "累計年營收YoY": f"{cum_v:+.1f}%" if cum_v is not None else "待公告",
                "_cb": ("🔥含CB可轉債" if code in cb_set else "➖"), "_ma5": ma5, "_ma10": ma10, "_ma20": ma20, "_ma60": ma60, "_ma120": ma120, "_ma240": ma240,
                "_w_ma10": w_ma10, "_m_ma5": m_ma5,
                "_risk_alert": (disp_map.get(code, "") + (" 👀注意股" if code in notice_set else "") + (f" 📅{exdiv_map[code]}" if code in exdiv_map else "")).strip() or "✅正常無處置/近期除息"
            }
        except Exception: continue
    return out

@st.cache_data(ttl=600, show_spinner=False)
def download_historical_klines(yf_tickers_tuple):
    try: return yf.download(list(yf_tickers_tuple), period="1y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)
    except Exception: return pd.DataFrame()

def scan_all_stocks_on_demand(twii_20d_ret: float, macro_withdrawal_alert: bool):
    price_dict, tdcc_dict, rev_dict = fetch_full_market_enrichment()
    fin_3rates_all = fetch_official_3rates_and_cum_eps()
    msci_data = fetch_dynamic_msci_data(len(price_dict))
    notice_set, exdiv_map, cb_set = fetch_twse_notice_codes(), fetch_twse_exdiv_soon(5), fetch_active_cb_codes()
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
            last_kd, (name, sector, mkt_code), rt = df_s.index[-1].strftime("%Y-%m-%d"), candidates[code], rt_quotes.get(code, {})
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
            mkt_lbl, rates_str, _ = build_3rates_and_mkt_tags(code, mkt_code, sector, fin_3rates_all.get(code, {}))
            sec_recs.append({"code": code, "name": name, "sector": sector, "official_sec": p_meta.get("sector") or sector, "close": curr_p, "chg_1d": chg_1d, "est_vol": est_vol, "yest_vol": yest_vol, "yod_ratio": yod_ratio})
            ma5, ma10, ma20 = round(float(np.mean(c[-5:])), 2), round(float(np.mean(c[-10:])), 2), round(float(np.mean(c[-20:])), 2)
            ma60 = round(float(np.mean(c[-60:])), 2) if len(c) >= 60 else ma20
            ma120 = round(float(np.mean(c[-120:])), 2) if len(c) >= 120 else ma60
            ma240 = round(float(np.mean(c[-240:])), 2) if len(c) >= 240 else ma120
            w_c, m_c = df_s["Close"].resample("W-FRI").last().dropna().values.astype(float), df_s["Close"].resample("ME").last().dropna().values.astype(float)
            if len(w_c) > 0: w_c[-1] = curr_p
            if len(m_c) > 0: m_c[-1] = curr_p
            w_ma10, m_ma5 = (round(float(np.mean(w_c[-10:])), 2) if len(w_c) >= 5 else ma60), (round(float(np.mean(m_c[-5:])), 2) if len(m_c) >= 3 else ma120)
            deduct10_avg = round(float(np.mean(c[-20:-10])), 2) if len(c) >= 20 else ma20
            deduct_20ma_str = f"✅低扣助漲({deduct10_avg})" if deduct10_avg < curr_p else f"⚠️高扣反壓({deduct10_avg})"
            std20 = float(np.std(c[-20:]))
            bb_up, bb_bw = round(ma20 + 2.0 * std20, 2), (round((4.0 * std20 / ma20) * 100, 1) if ma20 > 0 else 99.0)
            sk, sd, sj, _, kd_cr = compute_kdj(h[-60:], l[-60:], c[-60:])
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
            dt_s, (obv_t, _) = p_meta.get("day_trade_str", "N/A (交由淘金客聯網查核)"), compute_obv_trend(c[-60:], v[-60:])
            if len(c) >= 21 and c[-21] > 0:
                s20r = ((curr_p - float(c[-21])) / float(c[-21])) * 100.0; rs20 = round(s20r - twii_20d_ret, 1)
                rs20_s = f"{rs20:+.1f}% (逆勢抗跌)" if (twii_20d_ret < 0 and rs20 > 0 and (curr_p >= float(np.max(c[-20:])) * 0.985 or s20r > 0)) else f"{rs20:+.1f}%"
            else: rs20_s = "N/A"
            t400, t1000, d400, d1000 = tdcc_dict.get(code), p_meta.get("tdcc_1000"), p_meta.get("tdcc_400_delta"), p_meta.get("tdcc_1000_delta")
            tdcc_s = f"400張:{t400:.1f}%{f'({d400:+.2f}%)' if d400 is not None else ''}/千張:{f'{t1000:.1f}%' + (f'({d1000:+.2f}%)' if d1000 is not None else '') if t1000 is not None else 'N/A'}" if t400 is not None else "N/A"
            raw_ceil = max(bb_up, round(float(np.max(h[-21:-1])), 2))
            ceil_p = round(curr_p * 1.07, 2) if raw_ceil <= curr_p else round(raw_ceil, 2)
            iron_p = round(min(curr_p, max(ma10, float(l[-1])) if curr_p >= ma10 else min(ma20, float(l[-1]))), 2)
            stop_p = round(min(ma60, float(np.min(l[-20:]))), 2)
            yoy_v, cum_v, pe_o = rev_dict.get(code), p_meta.get("cum_yoy"), p_meta.get("pe", np.nan)
            fl, sl, dl, i_dt = p_meta.get("fini_lots", 0), p_meta.get("sitc_lots", 0), p_meta.get("dealer_lots", 0), p_meta.get("inst_date", "")
            inst_sum_str = f"外{fl:+,}/投{sl:+,}/自{dl:+,}=合計{fl+sl+dl:+,}張" + (f" [{i_dt[5:]}]" if i_dt else "")
            yoy_s, cum_s = (f"{yoy_v:+.1f}%" if yoy_v is not None else "待公告"), (f"{cum_v:+.1f}%" if cum_v is not None else "待公告")
            g_tag, g_bon, g_short = evaluate_granary_and_anti_trap(code, yoy_v, macro_withdrawal_alert)
            is_bc, is_fin, is_def = (code in CORE_WATCHLIST), (code in FINANCIAL_BLUE_CHIPS), (code in POLICY_DEFENSE_ARMOR)
            has_fund = (yoy_v is not None and yoy_v > 0) and (cum_v is None or cum_v > 0)
            st_tag = f"🐀疑似墊料壓貨({g_bon}分)" if "疑似墊料" in g_tag else (f"🛡️政策內需防彈衣(+{g_bon}分)" if is_def else (f"🔥有基之彈｜{g_short}" if has_fund else (f"🏦核心權值｜{g_short}" if is_bc else "🎭純技術炒作")))
            est_e, pe_d, ep_in, ep_tg, v_diag = compute_eps_valuation(code, curr_p, sector, yoy_v, cum_v, pe_o, iron_p)
            risk_badges = (" 🚨[處置]" if code in disp_map else (" 👀[注意]" if code in notice_set else "")) + (f" 📅[{exdiv_map[code]}]" if code in exdiv_map else "") + (" 🔥[CB]" if code in cb_set else "") + (" ⚠️量縮" if is_zombie_stock_tiered(code, curr_p, est_vol, ma5_vol) else "")

            row_base = {
                "代碼": code, "股名": f"{name}{risk_badges}", "市場別": mkt_lbl, "族群": sector, "三率(毛/營/淨)": rates_str,
                "穀倉賽道與防墊料": g_tag, "現價": curr_p, "今日%": chg_1d, "3日%": chg_3d, "短線屬性": st_tag,
                "DMI方向雙檢": dmi_str, "日J值": sj, "20MA扣抵": deduct_20ma_str, "BIAS10": b10_s, "BIAS20": b20_s, "5MA量比": vol_ratio, "上影線%": up_sh_p, "收盤位階": cp_s, "當沖率": dt_s,
                "OBV趨勢": obv_t, "RS20D%": rs20_s, "大戶400/千張": tdcc_s, "ATR%波動尺": atr_s, "法人動向": format_inst_chip_tag(fl, sl), "外資買賣(張)": fl, "投信買賣(張)": sl,
                "預估EPS(元)": est_e, "本益比(PE)": pe_d, "💎EPS合理切入價": ep_in, "🎯EPS目標價": ep_tg, "估值診斷": v_diag,
                "昨日量比": yod_ratio, "預估量(張)": est_vol, "天花板(反壓)": ceil_p, "月線承接": ma20, "鋼鐵防線": iron_p, "極限停損": stop_p, "單月營收YoY": yoy_s, "累計年營收YoY": cum_s
            }
            all_det[code] = {
                **row_base, "_raw_name": name, "_mkt": mkt_code, "_cb": ("🔥含CB可轉債" if code in cb_set else "➖"),
                "_ma5": ma5, "_ma10": ma10, "_ma20": ma20, "_ma60": ma60, "_ma120": ma120, "_ma240": ma240, "_w_ma10": w_ma10, "_m_ma5": m_ma5,
                "_inst_date": i_dt, "_pattern_tag": st_tag, "_inst_sum": inst_sum_str,
                "_risk_alert": (disp_map.get(code, "") + (" 👀注意股" if code in notice_set else "") + (f" 📅{exdiv_map[code]}" if code in exdiv_map else "")).strip() or "✅正常無處置/近期除息"
            }

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
                    pat = f"🔒 無量鎖漲停突破(惜售) [{g_short}]" if (chg_1d >= 7.5 and vol_ratio < 1.0) else (f"👑 KD×DMI共振突破 [{g_short}]" if kd_dmi_res else (f"🔥 布林壓縮突破 [{g_short}]" if bb_bw <= 16.0 else f"🚀 站穩月線帶量突破 [{g_short}]"))
                    all_det[code]["_pattern_tag"] = pat; l_launch.append({"實戰優先分": min(115, 92 + g_bon), "名將戰法診斷": pat, **row_base})
                elif (-3.5 <= chg_1d <= 2.2) and (chg_3d >= -6.5) and (vol_ratio <= 0.95) and (sj <= 68.0) and pd.notna(pdi) and (pdi >= mdi):
                    pat = f"⚡ N字量縮守月線 [{g_short}]"; all_det[code]["_pattern_tag"] = pat
                    l_pull.append({"蓄勢評分": min(115, 84 + int((1.0 - vol_ratio) * 15) + g_bon), "名將戰法診斷": pat, **row_base})
                elif curr_p >= ma5 >= ma10 >= ma20 and pd.notna(pdi) and (pdi > mdi) and chg_1d >= -2.0:
                    pat = f"🔥 3日急拉過熱 [{g_short}]" if (chg_3d >= 10.5 or (pd.notna(bias10) and bias10 > 10.0)) else f"🛡️ 沿5/10MA穩步推升 [{g_short}]"
                    all_det[code]["_pattern_tag"] = pat; l_orbit.append({"趨勢狀態": pat, **row_base})

            if is_bc and (not heavy_dn):
                bc_ok = has_fund or (yoy_v is not None and yoy_v >= 0) or (cum_v is not None and cum_v >= 0) or is_fin or is_def
                if bc_ok and (curr_p >= ma20) and pd.notna(pdi) and (pdi > mdi) and (not is_bear_acc) and (chg_3d < 9.5) and (sj <= 88.0):
                    l_bc.append({"波段總分": max(40, min(115, 85 + g_bon + (4 if has_fund else 0))), "長線格局定性": f"{g_short}＋{'🔥雙成長' if has_fund else '🏦穩健'}", **row_base})
                elif bc_ok and (curr_p < ma20) and (curr_p >= ma240 * 0.85):
                    dtg = f"🚨【{g_short}・空頭加速警戒】" if is_bear_acc else (f"🟢【{g_short}・月線下築底反彈】" if (curr_p >= ma5 and chg_1d >= 0) else f"⏳【{g_short}・股災超跌回測】")
                    l_turn.append({"波段總分": max(40, min(115, 82 + g_bon - (10 if is_bear_acc else 0))), "類型與轉機標註": dtg, **row_base})
        except Exception: continue

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
        except Exception: return np.nan
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
        except Exception: continue
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
                    lym, lyv = ys[-1]; prior = ys[:-1][-23:]; ms, lhit = 0, "無"
                    for p_ym, v in reversed(prior):
                        if v < threshold_yoy: ms += 1
                        else: lhit = p_ym; break
                    if ms >= len(prior) and len(prior) >= 12: return code, (True, 24, f"👑【2年首見破繭！】{lym}首破+{lyv:.1f}%")
                    if ms >= 6: return code, (True, ms, f"🌟【久違{ms}個月首破20%！】(前次{lhit}，本月+{lyv:.1f}%)")
                    return code, (False, ms, f"❌ 非首次 (僅隔{ms}個月)")
        except Exception: pass
        return code, None
    with ThreadPoolExecutor(max_workers=12) as ex:
        for fut in as_completed([ex.submit(_chk, c) for c in codes_tuple[:150]]):
            ck, vr = fut.result()
            if vr is not None: verified[ck] = vr
    return verified
