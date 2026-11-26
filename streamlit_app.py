import streamlit as st
import pandas as pd
import datetime, os, json, time, re, importlib

st.set_page_config(page_title="台股每日戰情室 v65.1", page_icon="🚨", layout="wide", initial_sidebar_state="auto")

st.markdown("""
<style>
.war-hero-box { background: linear-gradient(135deg, #0f2027 0%, #203a43 50%, #2c5364 100%); color: #fff; padding: 20px 24px; border-radius: 14px; border-left: 8px solid #00e676; margin-bottom: 16px; box-shadow: 0 6px 18px rgba(0,0,0,0.25); }
.war-hero-box-danger { background: linear-gradient(135deg, #2b0f14 0%, #451219 50%, #5c1620 100%); color: #fff; padding: 20px 24px; border-radius: 14px; border-left: 8px solid #ff4d4f; margin-bottom: 16px; box-shadow: 0 6px 20px rgba(255,77,79,0.28); }
.war-hero-box-warn { background: linear-gradient(135deg, #2b1d1d 0%, #3d251e 50%, #4a2c2a 100%); color: #fff; padding: 20px 24px; border-radius: 14px; border-left: 8px solid #fbbf24; margin-bottom: 16px; box-shadow: 0 6px 18px rgba(0,0,0,0.25); }
.event-banner-red { background: linear-gradient(90deg, #7f1d1d 0%, #991b1b 100%); color: #fff; padding: 11px 18px; border-radius: 10px; border: 2px solid #f87171; margin-bottom: 12px; font-size: 15px; line-height: 1.6; }
.event-banner-yellow { background: linear-gradient(90deg, #422006 0%, #713f12 100%); color: #fef08a; padding: 11px 18px; border-radius: 10px; border: 2px solid #facc15; margin-bottom: 12px; font-size: 15px; line-height: 1.6; }
.event-banner-blue { background: linear-gradient(90deg, #0f172a 0%, #1e293b 100%); color: #e2e8f0; padding: 11px 18px; border-radius: 10px; border: 1px solid #38bdf8; margin-bottom: 12px; font-size: 15px; line-height: 1.6; }
.radar-grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr)); gap: 14px; margin: 12px 0 16px 0; }
.radar-panel { background: rgba(15, 23, 42, 0.85); border-radius: 12px; padding: 15px 18px; font-size: 14.8px; line-height: 1.7; border: 1px solid rgba(255,255,255,0.18); }
.forecast-grid-3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 310px), 1fr)); gap: 12px; margin: 10px 0 16px 0; }
.forecast-card { overflow-wrap: anywhere; word-break: break-word; box-shadow: 0 4px 12px rgba(0,0,0,0.22); }
.war-score-title { font-size: 24px; font-weight: 900; color: #ffd54f; margin-bottom: 8px; line-height: 1.4; }
.war-dim-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr)); gap: 12px; margin: 12px 0 16px 0; }
.war-dim-item { background: rgba(255,255,255,0.08); padding: 13px 16px; border-radius: 10px; font-size: 14.6px; line-height: 1.68; }
.war-dim-concl { font-size: 15.8px; font-weight: 900; color: #ffd54f; margin-bottom: 6px; border-bottom: 1px solid rgba(255,255,255,0.15); padding-bottom: 5px; display: block; }
.meaning-note { font-size: 13.4px; color: #cbd5e1; background: rgba(0,0,0,0.28); padding: 5px 9px; border-radius: 6px; margin-top: 6px; display: block; line-height: 1.55; }
.tab-guide-box { background: #161b22; color: #e5e7eb; border-left: 5px solid #38bdf8; padding: 12px 16px; border-radius: 8px; margin-bottom: 14px; font-size: 14.5px; line-height: 1.7; }
.swap-card { background: #0f172a; color: #f8fafc; border-radius: 12px; padding: 16px 20px; border: 2px solid #38bdf8; margin-bottom: 14px; line-height: 1.72; }
.short-ledger-ok { background: #0d231d; color: #e2e8f0; border-radius: 12px; padding: 15px 19px; border: 2px solid #00e676; margin-bottom: 14px; line-height: 1.7; }
.short-ledger-stop { background: #2d0f14; color: #fff; border-radius: 12px; padding: 15px 19px; border: 2px solid #ff4d4f; margin-bottom: 14px; line-height: 1.7; }
.svg-zoom-box { width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; border-radius: 10px; background: #09111a; }
.svg-zoom-box img { max-width: none !important; }
div[data-testid="stMarkdownContainer"] { overflow-wrap: anywhere; word-break: break-word; }

@media (max-width: 768px) {
    .war-hero-box, .war-hero-box-danger, .war-hero-box-warn { padding: 13px 13px; border-left-width: 5px; }
    .war-score-title { font-size: 18px; }
    .war-score-idx { font-size: 25px !important; display: inline-block; }
    .war-sub-title { font-size: 16px !important; }
    .radar-grid-2, .war-dim-grid, .forecast-grid-3 { grid-template-columns: 1fr !important; gap: 10px; }
    .radar-panel, .war-dim-item, .forecast-card { padding: 11px 12px !important; font-size: 13.6px !important; line-height: 1.6 !important; }
    .war-dim-concl { font-size: 14.6px; }
    .meaning-note { font-size: 12.6px; }
    .event-banner-red, .event-banner-yellow, .event-banner-blue { font-size: 13.5px; padding: 10px 12px; }
    .swap-card, .short-ledger-ok, .short-ledger-stop, .tab-guide-box { padding: 12px 13px; font-size: 13.6px; }
    .svg-auto-img { width: 780px !important; min-width: 780px !important; }
}
</style>
""", unsafe_allow_html=True)

# 副程式檔案修改時間自動重載防呆（含 market_feeds 依賴鏈）
import config_events, market_feeds, data_engine, charts_ui
_need_reload = False
for _m in (config_events, market_feeds, data_engine, charts_ui):
    if getattr(_m, "__file__", None):
        _mt = os.path.getmtime(_m.__file__)
        if _need_reload or getattr(_m, "_loaded_mtime", None) != _mt:
            importlib.reload(_m)
            _m._loaded_mtime = _mt
            _need_reload = True

from config_events import (
    get_tw_now, get_target_revenue_ym_list, get_volume_multiplier, clean_html,
    pin_code_name_index, is_zombie_stock_tiered, style_tw_stock_df, prioritize_granary_and_armor_df,
    CUSTOM_EVENTS_FILE, MSCI_CACHE_FILE, MSCI_THEME_BELLWETHERS, OLD_GRANARY_ODM_WATCH,
    CORE_WATCHLIST, POLICY_DEFENSE_ARMOR, CONSTRUCTION_BLACKLIST,
    infer_msci_theme, parse_msci_text_override, get_dynamic_rolling_events
)
from data_engine import (
    fetch_dynamic_msci_data, fetch_mis_realtime_core16, fetch_market_prices_fast,
    fetch_us_sectors_and_leaders, fetch_auto_derivatives_and_margin_v63,
    compute_dynamic_market_forecast, fetch_and_analyze_fleet_fast,
    fetch_tdcc_weekly_delta_ondemand, fetch_stock_margin_short_sbl_batch,
    fetch_ondemand_stocks_detail, fetch_pair_swap_history,
    scan_all_stocks_on_demand, fetch_all_monthly_revenue_df_on_demand, verify_24m_first_breakout_batch
)
from charts_ui import (
    build_intraday_timeline_banner_html, evaluate_crash_and_bottom_radar_v244,
    build_daily_summary_and_3day_card_html, build_event_timeline_svg_html,
    build_realtime_forecast_svg_raw, build_realtime_and_forecast_svg_html,
    build_forecast_responsive_cards_html, compute_tw_hot_sectors,
    build_8_indicator_panels_html, build_market_outpost_line_v244,
    build_market_panorama_package_v244, build_mode_a_guard_package_v244, build_mode_b_deep_package_v244
)

ZOOM_OPT_MAP = {"自適應 (預設)": "auto", "🔍 1.5x 放大": "1.5x", "🔎 2.0x 超大": "2.0x"}

# ==========================================
# 個人持股設定永久存取（v65.1 全浮動：含日月光短單均價）
# ==========================================
PORTFOLIO_STATE_FILE = "portfolio_state.json"
DEFAULT_PORTFOLIO_STATE = {
    "lots_00985b": 25.0,
    "cost_00985b": 10.42,
    "lots_00713": 0.0,
    "shares_3711_short": 300,
    "short_3711_avg": 474.0,
    "short_3711_be": 476.79,
    "short_3711_above_ma5": True,
    "fish_3711_price": 450.0,
    "lots_00400a": 5.0,
    "cost_00400a": 11.89,
    "target_00400a_low": 0.0,
    "lots_6239": 0.0,
    "lots_3264": 0.0,
    "twd_cash_wan": 44.8,
    "usd_cash": 2300.0
}

def load_portfolio_state() -> dict:
    st_dict = dict(DEFAULT_PORTFOLIO_STATE)
    if os.path.exists(PORTFOLIO_STATE_FILE):
        try:
            with open(PORTFOLIO_STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    st_dict.update(saved)
        except Exception:
            pass
    return st_dict

def save_portfolio_state(state_dict: dict) -> bool:
    try:
        with open(PORTFOLIO_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state_dict, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

pf_saved = load_portfolio_state()

# ==========================================
# 主標題與三層獨立控制列
# ==========================================
now_tw_dt = get_tw_now()
now_str = now_tw_dt.strftime("%Y-%m-%d %H:%M:%S")
st.markdown("## 🚨 台股每日戰情室 v65.1 (夜盤4時段路由＋特定法人/MaxOI＋結論說明重構＋淘金客 v25.0)")

btn_c1, btn_c2, btn_c3, btn_c4 = st.columns([1.8, 2.0, 1.5, 1.7])
if btn_c1.button("⚡ 秒速刷新大盤與四水庫 (2秒)", use_container_width=True):
    fetch_mis_realtime_core16.clear()
    fetch_market_prices_fast.clear()
    fetch_us_sectors_and_leaders.clear()
    fetch_auto_derivatives_and_margin_v63.clear()
    st.rerun()

trigger_layer2_top = btn_c2.button("🚀 啟動／重掃全台個股池 (Layer 2)", type="primary", use_container_width=True, key="btn_l2_top")

if btn_c3.button("🔄 全域清快取重抓", use_container_width=True):
    st.cache_data.clear()
    for k_pop in ["v65_scan_data", "v65_rev_df", "mode_a_manual_txt", "mode_b_pkg_txt", "ver_24m_map"]:
        st.session_state.pop(k_pop, None)
    st.rerun()

auto_refresh = btn_c4.checkbox("⏱️ 每 20 秒自動刷新大盤", value=False)

# ================= LAYER 1：秒開大盤與個人核心資產 =================
with st.spinner("⚡ Layer 1 秒開載入中：大盤對時、夜盤4時段路由、特定法人/MaxOI、外資20日連買賣與四水庫..."):
    fleet = fetch_and_analyze_fleet_fast()
    us_data = fetch_us_sectors_and_leaders()

deriv = fleet["deriv"]
etf_m = fleet["etf_metrics"]
breadth = fleet["breadth"]
rt_quotes = fleet["rt_quotes"]
fast_prices = fleet["fast_prices"]

vix_q = us_data["quotes"].get("%5EVIX", {})
vix_now, vix_chg = vix_q.get("close", 26.6), vix_q.get("chg_pct", 0.0)
tnx_val, tnx_chg = us_data.get("tnx_val", 4.92), us_data.get("tnx_chg", 0.0)
sox_chg = us_data["quotes"].get("%5ESOX", {}).get("chg_pct", 0.0)
tsm_q, nvda_q = us_data["quotes"].get("TSM", {}), us_data["quotes"].get("NVDA", {})
tsm_chg, tsm_ext = tsm_q.get("chg_pct", 0.0), (f" ({tsm_q['ext_label']})" if tsm_q.get("ext_label") else "")
nvda_chg, nvda_ext = nvda_q.get("chg_pct", 0.0), (f" ({nvda_q['ext_label']})" if nvda_q.get("ext_label") else "")
fx_now, fx_ma5, fx_chg_ntd = deriv["fx_now"], deriv["fx_ma5"], deriv["fx_chg_ntd"]
fx_stopped_dep = bool(fx_chg_ntd <= 0.0)
macro_withdrawal_alert = bool((tnx_val >= 5.0 or us_data.get("credit_spread_alert", False)) and fx_now >= 32.50)

def _run_layer2_scan():
    with st.spinner("🔍 Layer 2 執行中：掃描全台 ~170 檔非殭屍股 K 線、週10/月5MA、20MA扣抵、真實當沖率、處置/注意/除權息與穀倉..."):
        st.session_state["v65_scan_data"] = scan_all_stocks_on_demand(fleet["twii_20d_ret"], macro_withdrawal_alert)

if trigger_layer2_top:
    _run_layer2_scan()

scan_data = st.session_state.get("v65_scan_data", None)
price_dict_active = scan_data["price_dict"] if scan_data else fast_prices
all_scanned_details = scan_data["all_details"] if scan_data else {}
msci_data = scan_data["msci_data"] if scan_data else fetch_dynamic_msci_data(1)

# ================= 左側雙階讀取覆寫控制台 =================
with st.sidebar:
    st.markdown("### 🎛️ 雙階讀取覆寫控制台 (全浮動)")
    n_win = deriv.get("night_window", "DAY_REFLECTED")
    if deriv.get("night_valid") and (n_win in ["PRE_MARKET", "WEEKEND"] or deriv.get("night_is_live_mis")):
        night_cap = f" ｜ 🌙夜盤：{deriv['night_tx_close']:,.0f}({deriv['night_tx_chg']:+,.0f})"
    else:
        night_cap = f" ｜ ⚡NQ：{us_data.get('nq_chg', 0.0):+.2f}%"
    st.caption(f"📡 期貨：{deriv['fut_src']} ｜ 融資：{deriv['margin_src']} ｜ 價差：{deriv['basis_src']}{night_cap}")

    sb_night_override_pts = st.number_input("🌙 即時夜盤手動覆寫 (0=自動，輸入點數)", value=0.0, min_value=0.0, step=50.0, format="%.0f")
    sb_fini_oi = st.number_input("外資台指期淨未平倉 (口)", value=int(deriv["fini_tx_oi"]), step=1000)
    sb_margin_2d = st.number_input("近2日上市融資增減 (億)", value=float(deriv["margin_2d_chg_yi"]), step=50.0, format="%.1f")
    sb_margin_2d_pct = st.number_input("近2日融資變動幅 (%)", value=float(deriv["margin_2d_pct"]), step=1.0, format="%.1f")
    sb_margin_dd = st.number_input("30日波段融資減幅 (%)", value=float(deriv["margin_30d_dd_pct"]), step=1.0, format="%.1f")
    sb_maint = st.number_input("整戶融資維持率 (%，線性校準可覆寫)", value=float(deriv["stock_only_maint_pct"]), step=0.5, format="%.1f")
    sb_basis = st.number_input("期現貨價差 (點，負=逆價差)", value=float(deriv["basis_pts"]), step=20.0, format="%.0f")
    sb_basis_stable = st.checkbox("恐慌逆價差已達極點且不再擴大", value=False)
    sb_consec_cover = st.number_input("外資期貨連補天數 (日・ΔOI口徑)", value=int(deriv["consec_cover_days"]), min_value=0, max_value=15, step=1)
    sb_cover_3d = st.number_input("外資近3日累計回補 (口・ΔOI)", value=int(deriv["cover_3d_sum"]), step=1000)
    sb_limit_dn = st.number_input("當日上市櫃跌停家數 (家)", value=int(breadth["limit_down"]), min_value=0, step=5)
    sb_limit_up = st.number_input("當日上市櫃漲停家數 (家・2025/04條款)", value=int(breadth["limit_up"]), min_value=0, step=10)
    sb_had_100 = st.checkbox("本波曾見破百檔跌停高峰", value=True)
    sb_ban_active = st.checkbox("限空令 / 國安基金護盤生效中", value=True)
    sb_ban_date = st.date_input("限空令／護盤起始日", value=datetime.date(2026, 11, 23))

    st.markdown("---")
    st.markdown("### 💧 最新四水庫＆短單分帳（全浮動）")
    sb_lots_00985b = st.number_input("水庫三：00985B 張數", value=float(pf_saved["lots_00985b"]), min_value=0.0, step=1.0, format="%.1f")
    sb_cost_00985b = st.number_input("00985B 成交均價 (元)", value=float(pf_saved["cost_00985b"]), min_value=0.0, step=0.05, format="%.2f")
    sb_lots_00713 = st.number_input("水庫三：00713 張數", value=float(pf_saved["lots_00713"]), min_value=0.0, step=0.5, format="%.1f")
    sb_shares_3711_short = st.number_input("換強短單：日月光零股 (股)", value=int(pf_saved["shares_3711_short"]), min_value=0, step=100)
    sb_short_3711_avg = st.number_input("日月光短單成交均價 (元)", value=float(pf_saved.get("short_3711_avg", 474.0)), min_value=1.0, step=1.0, format="%.2f")
    sb_short_3711_be = st.number_input("日月光短單損益平衡價 (元)", value=float(pf_saved["short_3711_be"]), min_value=0.0, step=1.0, format="%.2f")
    sb_short_3711_above_ma5 = st.checkbox("日月光短單已站穩5MA(升級破5MA停利)", value=bool(pf_saved["short_3711_above_ma5"]))
    sb_fish_3711 = st.number_input("日月光低接釣魚目標價 (元・禁追500+)", value=float(pf_saved["fish_3711_price"]), min_value=100.0, step=5.0, format="%.1f")
    sb_lots_00400a = st.number_input("待清零收尾：00400A 張數", value=float(pf_saved["lots_00400a"]), min_value=0.0, step=1.0, format="%.1f")
    sb_cost_00400a = st.number_input("00400A 成交均價 (元)", value=float(pf_saved["cost_00400a"]), min_value=0.0, step=0.1, format="%.2f")
    sb_target_00400a = st.number_input("00400A 自訂掛賣下緣價 (0=自動浮動)", value=float(pf_saved.get("target_00400a_low", 0.0)), min_value=0.0, step=0.05, format="%.2f")
    sb_lots_6239 = st.number_input("衛星剩餘：力成(6239) 張數", value=float(pf_saved["lots_6239"]), min_value=0.0, step=0.1, format="%.1f")
    sb_lots_3264 = st.number_input("衛星剩餘：欣銓(3264) 張數", value=float(pf_saved["lots_3264"]), min_value=0.0, step=0.1, format="%.1f")
    sb_twd_cash_wan = st.number_input("水庫四：純台幣現金 (萬元)", value=float(pf_saved["twd_cash_wan"]), min_value=0.0, step=1.0, format="%.1f")
    sb_usd_cash = st.number_input("水庫四：美金現金 (USD)", value=float(pf_saved["usd_cash"]), min_value=0.0, step=100.0, format="%.0f")

    pf_b1, pf_b2 = st.columns(2)
    if pf_b1.button("💾 儲存持股設定", use_container_width=True):
        ok_save = save_portfolio_state({
            "lots_00985b": sb_lots_00985b, "cost_00985b": sb_cost_00985b, "lots_00713": sb_lots_00713,
            "shares_3711_short": sb_shares_3711_short, "short_3711_avg": sb_short_3711_avg,
            "short_3711_be": sb_short_3711_be, "short_3711_above_ma5": sb_short_3711_above_ma5,
            "fish_3711_price": sb_fish_3711, "lots_00400a": sb_lots_00400a, "cost_00400a": sb_cost_00400a,
            "target_00400a_low": sb_target_00400a, "lots_6239": sb_lots_6239, "lots_3264": sb_lots_3264,
            "twd_cash_wan": sb_twd_cash_wan, "usd_cash": sb_usd_cash
        })
        if ok_save: st.success("✅ 已存檔！重整保留最新持股。")
        else: st.error("寫入失敗")
    if pf_b2.button("🔄 重置預設", use_container_width=True):
        if os.path.exists(PORTFOLIO_STATE_FILE):
            try: os.remove(PORTFOLIO_STATE_FILE)
            except Exception: pass
        st.rerun()

# ================= 全域頂部：模組二 滾動風險事件時間軸 ＋ 多空劇本 =================
ev_rows, upcoming_ev_rows, next_ev = get_dynamic_rolling_events(horizon_days=90)
if next_ev:
    d_left = next_ev["_days"]
    ev_cls = "event-banner-red" if d_left <= 3 else ("event-banner-yellow" if d_left <= 7 else "event-banner-blue")
    ev_warn_tip = "🚨 【高波動警戒：事件前 3 天內！反彈遇壓減碼、嚴禁開槓桿！】" if d_left <= 3 else "📌 自動滾動追蹤未來 90 天官宣與月度例行事件。"
    st.markdown(clean_html(f"""
    <div class="{ev_cls}">
    <b>📅 下一個重大風險事件倒數：{next_ev['日期']}（{next_ev['倒數狀態']}）➔ {next_ev['重大風險事件']}</b><br>
    • {ev_warn_tip} ｜ 🟢 <b>優於預期</b>：{next_ev['🟢 優於預期／鴿派劇本']} ｜ 🔴 <b>差於預期</b>：{next_ev['🔴 差於預期／鷹派劇本']}
    </div>
    """), unsafe_allow_html=True)

with st.expander("🗓️ 點此展開【自動滾動風險事件視覺化時間軸圖（可放大滑動）＋ 多空劇本表 ＋ 自訂事件新增】", expanded=False):
    ev_zoom_lbl = st.radio("🔍 時間軸圖表縮放倍率", list(ZOOM_OPT_MAP.keys()), horizontal=True, key="ev_svg_zoom")
    st.markdown(build_event_timeline_svg_html(upcoming_ev_rows, zoom_mode=ZOOM_OPT_MAP[ev_zoom_lbl]), unsafe_allow_html=True)
    st.dataframe(pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in ev_rows]), use_container_width=True, hide_index=True)

    st.markdown("##### ➕ 新增單次突發風險事件")
    ce_c1, ce_c2, ce_c3, ce_c4 = st.columns([1.2, 1.5, 2.2, 2.2])
    ce_date = ce_c1.date_input("事件日期", value=now_tw_dt.date() + datetime.timedelta(days=5), key="ce_d")
    ce_short = ce_c2.text_input("時間軸簡稱 (8字內)", value="", placeholder="例：台積電法說會", key="ce_s")
    ce_bull = ce_c3.text_input("🟢 優於預期劇本", value="展望優於預期帶動 AI 估值修復", key="ce_b")
    ce_bear = ce_c4.text_input("🔴 差於預期劇本", value="下修資本支出引發供應鏈修正", key="ce_r")
    if st.button("💾 儲存新增事件至滾動時間軸", key="btn_add_ce"):
        if ce_short.strip():
            cur_list = []
            if os.path.exists(CUSTOM_EVENTS_FILE):
                try:
                    with open(CUSTOM_EVENTS_FILE, "r", encoding="utf-8") as f: cur_list = json.load(f)
                except Exception: cur_list = []
            cur_list.append({"date": ce_date.strftime("%Y-%m-%d"), "short": ce_short.strip(), "title": f"【自訂】{ce_short.strip()}", "level": "HIGH", "bull": ce_bull, "bear": ce_bear, "action": "事件前3天控管槓桿與倉位"})
            try:
                with open(CUSTOM_EVENTS_FILE, "w", encoding="utf-8") as f: json.dump(cur_list, f, ensure_ascii=False)
                st.success("✅ 已寫入滾動事件簿！重新整理即可顯示於時間軸。")
            except Exception as e:
                st.error(f"儲存失敗：{e}")

# 計算模組 0 (v25.0) 與全浮動三情境預測（含手動夜盤即時覆寫連動）
basis_valid_eff = bool(deriv.get("basis_valid", False) or abs(sb_basis) > 0.1)
deriv_eff = dict(deriv)
deriv_eff["fini_tx_oi"] = sb_fini_oi
deriv_eff["basis_pts"] = sb_basis
deriv_eff["basis_valid"] = basis_valid_eff
if not deriv.get("basis_valid", False) and abs(sb_basis) > 0.1:
    deriv_eff["basis_src"] = "指揮官手動覆寫"

night_pct_ov = None
if sb_night_override_pts > 5000:
    ref_n = deriv.get("tx_close") if deriv.get("tx_close", 0) > 5000 else fleet["twii_now"]
    n_chg_ov = round(sb_night_override_pts - ref_n, 0)
    n_pct_ov = round((n_chg_ov / max(1.0, ref_n)) * 100.0, 2)
    deriv_eff["night_valid"], deriv_eff["night_is_live_mis"], deriv_eff["night_is_stale"] = True, True, False
    deriv_eff["night_tx_close"], deriv_eff["night_tx_chg"], deriv_eff["night_tx_chg_pct"] = round(sb_night_override_pts, 0), n_chg_ov, n_pct_ov
    deriv_eff["night_src"], deriv_eff["night_tx_time"] = "指揮官手動輸入", now_tw_dt.strftime("%Y-%m-%d %H:%M")
    night_pct_ov = n_pct_ov

days_since_ban = (now_tw_dt.date() - sb_ban_date).days
is_br_stale = bool(breadth.get("is_stale_intraday", False))
target_400a_arg = sb_target_00400a if sb_target_00400a > 0 else None
eff_t1_400 = sb_target_00400a if sb_target_00400a > 0 else etf_m["00400A"]["exit_t1"]
eff_t2_400 = max(eff_t1_400 + 0.30, etf_m["00400A"]["exit_t2"])

radar = evaluate_crash_and_bottom_radar_v244(
    fleet["twii_now"], fleet["twii_chg_pct"], fleet["ma60"], fleet["ma120"], fleet["ma240"], fleet["twii_30d_dd_pct"], fleet["sp500_below_ma200"],
    vix_now, fleet["sox_dd_pct"], sb_fini_oi, sb_margin_dd, sb_margin_2d, sb_margin_2d_pct, sb_maint,
    fleet["bottom_4track"], sb_limit_up, breadth["adv_ratio_pct"],
    sb_basis, basis_valid_eff, sb_basis_stable, sb_consec_cover, sb_cover_3d,
    fx_stopped_dep, sb_limit_dn, sb_had_100,
    sb_ban_active, days_since_ban, fleet["twii_consec_above_ma10"],
    breadth_is_stale=is_br_stale
)
forecast = compute_dynamic_market_forecast(
    fleet, radar, vix_now, sox_chg, upcoming_ev_rows, sb_fini_oi, deriv["fini_tx_oi_delta"],
    lots_00400a=sb_lots_00400a, target_00400a_low=target_400a_arg,
    shares_3711_short=sb_shares_3711_short, fish_3711_price=sb_fish_3711,
    night_pct_override=night_pct_ov
)

t_min_now = now_tw_dt.hour * 60 + now_tw_dt.minute

# 四水庫即時價格與資產市值試算
def get_live_p(code_k, default_v):
    is_open_t = (now_tw_dt.weekday() < 5) and (t_min_now >= 540)
    rt_v = rt_quotes.get(code_k, {}).get("close", 0.0) if is_open_t else 0.0
    return float(rt_v or price_dict_active.get(code_k, {}).get("close") or default_v)

p_2330, p_3711 = get_live_p("2330", 2150.0), get_live_p("3711", 511.0)
p_6239, p_3264 = get_live_p("6239", 191.0), get_live_p("3264", 141.5)
p_2884, p_2881 = get_live_p("2884", 39.1), get_live_p("2881", 134.0)
p_2812, p_2887, p_2834 = get_live_p("2812", 16.15), get_live_p("2887", 34.35), get_live_p("2834", 16.35)
p_avgo = us_data["quotes"].get("AVGO", {}).get("close") or etf_m["AVGO"]["p"]
p_nvda = us_data["quotes"].get("NVDA", {}).get("close") or etf_m["NVDA"]["p"]
p_sgov = us_data["quotes"].get("SGOV", {}).get("close") or etf_m["SGOV"]["p"]
p_00400a, p_00985b, p_00713 = etf_m["00400A"]["p"], etf_m["00985B"]["p"], etf_m["00713"]["p"]
ma5_3711 = etf_m.get("3711", {}).get("ma5", 493.6)

val_r1 = 400000.0
val_r2_tw_semi = 4000 * p_2330 + 3000 * p_3711
val_r2_tw_fin = 100453 * p_2884 + 11743 * p_2881 + 27148 * p_2812 + 4882 * p_2887 + 158 * 9.59 + 1090 * p_2834
val_r2_us_semi = (35 * p_avgo + 80 * p_nvda) * fx_now
val_r2 = val_r2_tw_semi + val_r2_tw_fin + val_r2_us_semi
val_r3 = sb_lots_00985b * 1000 * p_00985b + sb_lots_00713 * 1000 * p_00713
val_r4 = (30 * p_sgov + sb_usd_cash) * fx_now + sb_twd_cash_wan * 10000.0
val_short_3711 = sb_shares_3711_short * p_3711
val_sat_semi = sb_lots_6239 * 1000 * p_6239 + sb_lots_3264 * 1000 * p_3264
val_sat_400a = sb_lots_00400a * 1000 * p_00400a
total_assets = val_r1 + val_r2 + val_r3 + val_r4 + val_short_3711 + val_sat_semi + val_sat_400a
semi_total = val_r2_tw_semi + val_r2_us_semi + val_short_3711 + val_sat_semi
semi_ratio_pct = round((semi_total / max(1.0, total_assets)) * 100.0, 1)

portfolio_summary_obj = {
    "total_wan": total_assets / 10000.0, "semi_pct": semi_ratio_pct,
    "lots_00985b": sb_lots_00985b, "cost_00985b": sb_cost_00985b, "lots_00713": sb_lots_00713, "r3_wan": val_r3 / 10000.0,
    "twd_cash_wan": sb_twd_cash_wan, "usd_cash": sb_usd_cash, "r4_wan": val_r4 / 10000.0,
    "lots_00400a": sb_lots_00400a, "cost_00400a": sb_cost_00400a, "t1_400": eff_t1_400, "t2_400": eff_t2_400,
    "shares_3711_short": sb_shares_3711_short, "avg_3711": sb_short_3711_avg, "p_3711": p_3711,
    "ma5_3711": ma5_3711, "be_3711": sb_short_3711_be, "fish_3711": sb_fish_3711
}

holding_notes_map = {
    "3711": f"【雙軌分帳】(1)水庫二傳家寶底倉 3張(@172.67)不動；(2)淨降倉換強獨立短單 {sb_shares_3711_short}股(@{sb_short_3711_avg:.2f}/平衡價{sb_short_3711_be:.2f}/5MA={ma5_3711:.1f})；(3)紀律鎖定：嚴禁500元以上追高，低接釣魚鎖定 {sb_fish_3711:.0f} 元區間！",
    "6239": f"已全數出清 ({sb_lots_6239:.1f}張)，資金已併入水庫四純現金",
    "3264": f"已全數出清 ({sb_lots_3264:.1f}張)",
    "2330": "水庫二傳家寶核心底倉 4張 (@229.11，全程死抱不動)",
    "2884": "水庫二傳家寶核心底倉 100.453張 (@19.15)",
    "2881": "水庫二傳家寶核心底倉 11.743張 (@69.50)",
    "2812": "水庫二傳家寶核心底倉 27.148張 (@7.83)",
    "2887": "水庫二傳家寶核心底倉 4.882張 (@9.97)",
    "2834": "水庫二傳家寶核心底倉 1.090張 (@8.67)",
    "00400A": f"最後收尾剩餘 {sb_lots_00400a:.0f}張 (@{sb_cost_00400a:.2f}，逢彈 {eff_t1_400:.2f}~{eff_t2_400:.2f} 清零轉 00985B)" if sb_lots_00400a > 0 else "已全數清零退場轉入 00985B",
    "00985B": f"水庫三核心低波短債 {sb_lots_00985b:.1f}張 (@{sb_cost_00985b:.2f}，第二道現金流持續累積)"
}

outpost_line = build_market_outpost_line_v244(fleet, radar, deriv_eff, forecast, tnx_val, fx_now, sb_ban_active, next_ev, us_data=us_data)

if scan_data:
    df_launch = prioritize_granary_and_armor_df(scan_data["df_launch"], macro_withdrawal_alert, "實戰優先分")
    df_pullback = prioritize_granary_and_armor_df(scan_data["df_pullback"], macro_withdrawal_alert, "蓄勢評分")
    df_orbit = scan_data["df_orbit"]
    df_long_bc = prioritize_granary_and_armor_df(scan_data["df_long_bc"], macro_withdrawal_alert, "波段總分")
    df_long_turn = prioritize_granary_and_armor_df(scan_data["df_long_turn"], macro_withdrawal_alert, "波段總分")
    df_granary_audit = scan_data["df_granary"]
    df_msci_audit = scan_data["df_msci"]
    tw_hot_sec_info = compute_tw_hot_sectors(scan_data["sec_recs"])
    if not df_launch.empty and "短線屬性" in df_launch.columns:
        df_launch_fund = df_launch[df_launch["短線屬性"].str.contains("有基之彈|核心權值|政策內需", na=False)].copy()
        df_launch_pure = df_launch[df_launch["短線屬性"].str.contains("純技術炒作", na=False)].copy()
    else:
        df_launch_fund, df_launch_pure = pd.DataFrame(), pd.DataFrame()
else:
    df_launch = df_pullback = df_orbit = df_long_bc = df_long_turn = df_granary_audit = df_msci_audit = df_launch_fund = df_launch_pure = pd.DataFrame()
    tw_hot_sec_info = {"hot_html": "", "cold_html": "", "top1_name": "待掃描"}

# ==========================================
# 六大功能分頁架構
# ==========================================
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🚨 總結＆雷達＆戰情包",
    "⚖️ 換股＆短單＆四水庫",
    f"⚡ 短線旱地拔蔥榜 {'✅' if scan_data else '💤'}",
    f"🏛️ 長線波段＆MSCI池 {'✅' if scan_data else '💤'}",
    f"🌾 穀倉與防墊料專區 {'✅' if scan_data else '💤'}",
    f"📈 上市櫃月營收快篩 {'✅' if 'v65_rev_df' in st.session_state else '💤'}"
])

# ==================== TAB 1 ====================
with tab1:
    # 1. 盤中模組 9 時間軸 ＋ 夜盤 4 時段預警 ＋ 美股休市偵測
    st.markdown(build_intraday_timeline_banner_html(now_tw_dt, radar["master_state"], deriv_eff, next_ev, us_data=us_data), unsafe_allow_html=True)

    # 2. 加權指數 ＋ 模組 0 雙雷達主卡片
    hero_cls = "war-hero-box-danger" if radar["master_state"] == "RED_LOCKED" else ("war-hero-box-warn" if radar["master_state"] == "YELLOW" else "war-hero-box")
    idx_col = "#ff4d4f" if fleet["twii_chg_pct"] >= 0 else "#00e676"
    w_html = "<br>".join([f"{'🔴' if f else '⚪'} {txt}" for f, txt in radar["w_items"]])
    b_html = "<br>".join([f"{'🟢' if f else '⚪'} {txt}" for f, txt in radar["b_items"]])
    if radar.get("precursor_txt"):
        b_html += f"<br><span style='color:#fde047; font-weight:800;'>{radar['precursor_txt']}</span>"
    ban_badge = "🛡️ <b>限空令生效中</b>（凍結借券賣減看多加分，改看外資現貨＋期貨ΔOI連補＋台幣止貶）" if sb_ban_active else "⚪ 目前無限空令"

    sec_s = fleet.get("sector_struct", {})
    otc_tag_hero = f" ｜ 櫃買OTC：<b>{sec_s['otc_chg']:+.2f}%</b>（{sec_s['tag']}）" if sec_s.get("otc_chg") is not None else ""
    if is_br_stale:
        breadth_hero_str = f"⚡ 盤中核心權值溫度：<b>上漲比 {breadth.get('core_rt_adv_pct', 50):.0f}%（{breadth.get('core_rt_up', 0)}漲 / {breadth.get('core_rt_dn', 0)}跌）</b>{otc_tag_hero}"
    else:
        breadth_hero_str = f"市場廣度（{breadth['src']}）：<b>上漲比 {breadth['adv_ratio_pct']:.0f}%（漲停 {sb_limit_up} ／ 跌停 {sb_limit_dn}）</b>{otc_tag_hero}"

    st.markdown(clean_html(f"""
    <div class="{hero_cls}">
    <div class="war-score-title">
    📊 台股加權指數：<span class="war-score-idx" style="font-size:34px; color:{idx_col};">{fleet['twii_now']:,.1f} 點 ({fleet['twii_chg_pct']:+.2f}%)</span>
    &nbsp;｜&nbsp; {radar['master_badge']}
    </div>
    <div style="font-size:14.4px; color:#e2e8f0; background:rgba(0,0,0,0.35); padding:8px 13px; border-radius:8px; margin-bottom:10px; line-height:1.68;">
    • 🕒 <b>三源最新對時</b>：{fleet['date_str']}（{fleet['quote_src']}｜開 <b>{fleet['twii_open']:,.0f}</b> ／ 低 <b>{fleet['twii_low']:,.0f}</b> ～ 高 <b>{fleet['twii_high']:,.0f}</b> ／ 昨收 <b>{fleet['twii_prev_c']:,.0f}</b>）｜ <b>月20MA：{fleet['m_ma20']:,.0f}</b> ｜ <b>年線：{fleet['ma240']:,.0f}</b><br>
    • ⚖️ <b>模組 0 最高權限指令</b>：<b style="color:#fde047;">{radar['master_action']}</b><br>
    • {ban_badge} ｜ 連站10MA：<b>{fleet['twii_consec_above_ma10']} 日</b> ｜ {breadth_hero_str}
    </div>
    <div class="radar-grid-2">
    <div class="radar-panel" style="border-left:5px solid {'#ff4d4f' if radar['crash_warning_on'] else '#64748b'};">
    <span class="war-dim-concl" style="color:#ff8a80;">🔴 一、股災啟動預警雷達（觸發 {radar['w_count']} / 5 項 ｜ ≧3項亮紅燈）</span>
    {w_html}
    </div>
    <div class="radar-panel" style="border-left:5px solid {'#00e676' if radar['bottom_confirmed'] else '#f59e0b'};">
    <span class="war-dim-concl" style="color:#69f0ae;">🟢 二、股災落底確認雷達（達成 {radar['b_count']} / 5 項 ｜ ≧4項共振解鎖）</span>
    {b_html}
    </div>
    </div>
    </div>
    """), unsafe_allow_html=True)

    # 3. 當日戰情總結 ＆ 未來 3 日推演卡（v65.1 2×2 結論：說明重構）
    st.markdown(build_daily_summary_and_3day_card_html(
        fleet, radar, deriv_eff, us_data, forecast, next_ev,
        p_3711, ma5_3711, sb_short_3711_be, sb_shares_3711_short, etf_m,
        lots_00400a=sb_lots_00400a, lots_00985b=sb_lots_00985b,
        twd_cash_wan=sb_twd_cash_wan, fish_price_3711=sb_fish_3711,
        target_00400a_low=target_400a_arg, short_3711_avg=sb_short_3711_avg
    ), unsafe_allow_html=True)

    # 4. v65.1 三軌戰情數據包匯出中心（對接淘金客 v25.0）
    st.markdown("### 🤖 【淘金客 v25.0 三軌戰情數據包匯出中心】（防截斷核對 ＋ 右上角一鍵複製）")
    pkg_tab_0, pkg_tab_a, pkg_tab_b = st.tabs([
        "🌍 按鈕 0：大盤全景多空＆四水庫包（含 8 大指標/特定法人/MaxOI）",
        "🛡️ 按鈕 A：初篩海選／守門員快篩（跨榜或手動 5～6 檔）",
        "🏆 按鈕 B：決選 PK 比賽深檢（1～5 檔＋橫向 PK 表＋資券借＋週月線）"
    ])

    with pkg_tab_0:
        p0_c1, p0_c2 = st.columns([2.2, 3.8])
        inc_pf_0 = p0_c1.checkbox("💧 附帶個人四水庫與鎖定紀律快照", value=True, key="pkg0_inc_pf")
        p0_c2.caption("💡 專供貼給《淘金客 v25.0》啟動【模式 0 大盤全景總控】，已內嵌成交額、8 大指標、特定法人、Max OI 與夜盤 4 時段狀態。")
        pkg0_txt = build_market_panorama_package_v244(
            fleet, radar, deriv_eff, us_data, breadth, forecast,
            upcoming_ev_rows, now_str,
            sb_fini_oi, sb_margin_2d, sb_margin_2d_pct, sb_margin_dd,
            sb_maint, sb_basis, sb_consec_cover, sb_cover_3d,
            sb_limit_up, sb_limit_dn, sb_ban_active, days_since_ban,
            fx_stopped_dep, portfolio_summary=portfolio_summary_obj, include_portfolio=inc_pf_0
        )
        st.code(pkg0_txt, language="text")

    with pkg_tab_a:
        if not scan_data:
            st.info("⚡ 尚未執行 Layer 2 全市場掃描：可點下方按鈕掃描全台 170 檔，或直接在右側手動輸入 1～6 檔代號快篩。")
            a_top1, a_top2 = st.columns([2.2, 3.0])
            if a_top1.button("🚀 啟動全台個股掃描 (Layer 2)", type="primary", key="btn_l2_tab1_a", use_container_width=True):
                _run_layer2_scan()
                st.rerun()
            manual_a_fast = a_top2.text_input("⌨️ 或直接輸入 1～6 檔 4 碼代號海選（逗號分隔）", value="", placeholder="例：2330, 3711, 2454, 2383, 2881", key="mode_a_manual_fast")
            man_a_codes = list(dict.fromkeys(re.findall(r"\b(\d{4})\b", manual_a_fast)))[:6]
            if man_a_codes and st.button("⚡ 按需補抓並生成【模式 A 守門員快篩包】", type="primary", key="btn_gen_a_fast"):
                with st.spinner(f"正在補抓 {', '.join(man_a_codes)} 技術指標、真實當沖率與大戶週變動..."):
                    det_a = fetch_ondemand_stocks_detail(tuple(man_a_codes), fleet["twii_20d_ret"], macro_withdrawal_alert)
                    tdcc_a = fetch_tdcc_weekly_delta_ondemand(tuple(man_a_codes))
                    msbl_a = fetch_stock_margin_short_sbl_batch(tuple(man_a_codes))
                    st.session_state["mode_a_manual_txt"] = build_mode_a_guard_package_v244(man_a_codes, "手動海選", f"自選 {len(man_a_codes)} 檔", det_a, outpost_line, now_str, tdcc_a, margin_sbl_map=msbl_a)
            if st.session_state.get("mode_a_manual_txt"):
                st.code(st.session_state["mode_a_manual_txt"], language="text")
        else:
            union_codes = list(dict.fromkeys(
                (df_launch_fund["代碼"].tolist() if not df_launch_fund.empty else []) +
                (df_pullback["代碼"].tolist() if not df_pullback.empty else []) +
                (df_long_bc["代碼"].tolist() if not df_long_bc.empty else []) +
                (df_msci_audit[df_msci_audit["MSCI異動別"].str.contains("新增", na=False)]["代碼"].tolist() if not df_msci_audit.empty else []) +
                (df_granary_audit[~df_granary_audit["代碼"].isin(OLD_GRANARY_ODM_WATCH)]["代碼"].tolist() if not df_granary_audit.empty else []) +
                (df_long_turn["代碼"].tolist() if not df_long_turn.empty else [])
            ))
            pool_map = {
                f"🌟 跨榜聯集海選：全部初篩強股＆新穀倉＆MSCI ({len(union_codes)}檔)": union_codes,
                f"🔥 短線表1-A：有基之彈旱地拔蔥 ({len(df_launch_fund)}檔)": df_launch_fund["代碼"].tolist() if not df_launch_fund.empty else [],
                f"🎭 短線表1-B：純技術旱地拔蔥 ({len(df_launch_pure)}檔)": df_launch_pure["代碼"].tolist() if not df_launch_pure.empty else [],
                f"🎯 短線表二：N字洗盤低接 ({len(df_pullback)}檔)": df_pullback["代碼"].tolist() if not df_pullback.empty else [],
                f"🛡️ 短線表三：上升軌道推升 ({len(df_orbit)}檔)": df_orbit["代碼"].tolist() if not df_orbit.empty else [],
                f"🏛️ 長線表3-A：核心多頭權值 ({len(df_long_bc)}檔)": df_long_bc["代碼"].tolist() if not df_long_bc.empty else [],
                f"🔄 長線表3-B：權值超跌回測與轉機 ({len(df_long_turn)}檔)": df_long_turn["代碼"].tolist() if not df_long_turn.empty else [],
                f"🌐 本季 MSCI 新增主流股 ({len(df_msci_audit[df_msci_audit['MSCI異動別'].str.contains('新增', na=False)]) if not df_msci_audit.empty else 0}檔)": df_msci_audit[df_msci_audit["MSCI異動別"].str.contains("新增", na=False)]["代碼"].tolist() if not df_msci_audit.empty else [],
                f"🌾 宏爺三大新穀倉＆防彈衣 ({len(df_granary_audit[~df_granary_audit['代碼'].isin(OLD_GRANARY_ODM_WATCH)]) if not df_granary_audit.empty else 0}檔)": df_granary_audit[~df_granary_audit["代碼"].isin(OLD_GRANARY_ODM_WATCH)]["代碼"].tolist() if not df_granary_audit.empty else []
            }
            pool_keys = list(pool_map.keys())
            pa_c1, pa_c2, pa_c3, pa_c4 = st.columns([2.6, 1.0, 1.7, 1.9])
            sel_pool_k = pa_c1.selectbox("📂 選擇快篩來源清單（推薦跨榜聯集）", pool_keys, index=0, key="mode_a_pool")
            batch_sz = pa_c2.radio("每批上限", [5, 6], horizontal=True, key="mode_a_bs")
            pool_codes = list(dict.fromkeys([str(x) for x in pool_map[sel_pool_k] if str(x) in all_scanned_details]))
            manual_a_str = st.text_input("⌨️ 或手動輸入任意 4 碼代號覆寫本批海選（逗號分隔，上限 6 檔；留空則用上方清單）", value="", placeholder="例：3711, 2330, 2383, 2454, 1519", key="mode_a_manual_ov")
            man_a_ov = list(dict.fromkeys(re.findall(r"\b(\d{4})\b", manual_a_str)))[:6]

            if man_a_ov:
                fetch_a_tdcc = pa_c4.checkbox("📡 補抓 FinMind 大戶＆精準當沖", value=True, key="mode_a_tdcc_ov")
                missing_a = [c for c in man_a_ov if c not in all_scanned_details]
                merged_a = dict(all_scanned_details)
                if missing_a:
                    with st.spinner(f"按需補抓未掃描標的 {', '.join(missing_a)}..."):
                        merged_a.update(fetch_ondemand_stocks_detail(tuple(missing_a), fleet["twii_20d_ret"], macro_withdrawal_alert))
                live_tdcc_a = fetch_tdcc_weekly_delta_ondemand(tuple(man_a_ov)) if fetch_a_tdcc else {}
                msbl_a = fetch_stock_margin_short_sbl_batch(tuple(man_a_ov)) if fetch_a_tdcc else {}
                st.code(build_mode_a_guard_package_v244(man_a_ov, "手動海選", f"自選 {len(man_a_ov)} 檔", merged_a, outpost_line, now_str, live_tdcc_a, margin_sbl_map=msbl_a), language="text")
            elif pool_codes:
                n_batches = (len(pool_codes) + batch_sz - 1) // batch_sz
                batch_opts = [f"第 {i+1} 批 ({i*batch_sz+1}~{min((i+1)*batch_sz, len(pool_codes))} 檔)" for i in range(n_batches)]
                sel_b_lbl = pa_c3.selectbox("📑 自動切批切換", batch_opts, key="mode_a_batch")
                b_idx = batch_opts.index(sel_b_lbl)
                batch_codes = pool_codes[b_idx * batch_sz : (b_idx + 1) * batch_sz]
                fetch_a_tdcc = pa_c4.checkbox("📡 補抓本批 FinMind 大戶＆精準當沖", value=False, key="mode_a_tdcc")
                custom_a = st.multiselect("✏️ 可跨榜增減微調本批匯出標的（上限 6 檔）：", options=pool_codes, default=batch_codes, max_selections=6, format_func=lambda c: f"{c} {all_scanned_details[c]['_raw_name']}", key=f"mode_a_ms_{sel_pool_k}_{b_idx}")
                final_a_codes = custom_a if custom_a else batch_codes
                live_tdcc_a = fetch_tdcc_weekly_delta_ondemand(tuple(final_a_codes)) if fetch_a_tdcc else {}
                msbl_a = fetch_stock_margin_short_sbl_batch(tuple(final_a_codes)) if fetch_a_tdcc else {}
                st.code(build_mode_a_guard_package_v244(final_a_codes, sel_pool_k.split(" (")[0], sel_b_lbl, all_scanned_details, outpost_line, now_str, live_tdcc_a, margin_sbl_map=msbl_a), language="text")
            else:
                st.info("目前選擇的清單無符合標的，請切換至『🌟 跨榜聯集海選』或直接手動輸入代號！")

    with pkg_tab_b:
        if all_scanned_details:
            all_opt_codes = sorted(all_scanned_details.keys(), key=lambda c: (0 if c in ["3711", "2330", "2884", "2881", "2454", "2383", "2308", "1519"] else 1, c))
            default_b = [c for c in ["3711", "2330"] if c in all_opt_codes][:2]
            pb_c1, pb_c2 = st.columns([3.2, 2.0])
            sel_b_codes = pb_c1.multiselect("🎯 勾選比賽／決選標的（最多 5 檔；庫存股自動帶入成本與停損）：", options=all_opt_codes, default=default_b, max_selections=5, format_func=lambda c: f"{c} {all_scanned_details[c]['_raw_name']} ({all_scanned_details[c]['現價']})", key="mode_b_ms")
            manual_b_str = pb_c2.text_input("⌨️ 或手動輸入 1～5 檔 4 碼代號（逗號分隔）", value="", placeholder="例：3711, 2330, 2454, 2383", key="mode_b_manual")
        else:
            st.caption("💡 免跑 Layer 2 亦可直接輸入 1～5 檔代號！按下生成將自動按需補抓完整 6 均線、週10/月5MA、20MA扣抵、資券借四位階、真實當沖率與大戶週變動。")
            sel_b_codes = []
            manual_b_str = st.text_input("⌨️ 直接輸入 1～5 檔 4 碼代號（逗號分隔）", value="3711, 2330", placeholder="例：3711, 2330, 2454, 2881", key="mode_b_manual_fast")

        manual_b_codes = list(dict.fromkeys(re.findall(r"\b(\d{4})\b", manual_b_str)))[:5]
        target_b_codes = manual_b_codes if manual_b_codes else sel_b_codes
        if st.button("⚡ 生成【淘金客 v25.0 模式 B 決選 PK 深檢包】（補抓資券借 ＋ 週月線 ＋ 真實當沖 ＋ 大戶週變動）", type="primary", key="btn_gen_mode_b"):
            if target_b_codes:
                with st.spinner(f"正在為 {', '.join(target_b_codes)} 補齊完整均線/週月線、資券借四位階、真實當沖、季報防墊料與大戶週變動..."):
                    merged_b = dict(all_scanned_details)
                    missing_b = [c for c in target_b_codes if c not in merged_b]
                    if missing_b:
                        merged_b.update(fetch_ondemand_stocks_detail(tuple(missing_b), fleet["twii_20d_ret"], macro_withdrawal_alert))
                    live_tdcc_b = fetch_tdcc_weekly_delta_ondemand(tuple(target_b_codes))
                    margin_sbl_b = fetch_stock_margin_short_sbl_batch(tuple(target_b_codes))

                    pair_note_b = ""
                    if len(target_b_codes) == 2:
                        c1, c2 = target_b_codes[0], target_b_codes[1]
                        d1, d2 = merged_b.get(c1, price_dict_active.get(c1, {})), merged_b.get(c2, price_dict_active.get(c2, {}))
                        m1, m2 = d1.get("_mkt", d1.get("market", "TW")), d2.get("_mkt", d2.get("market", "TW"))
                        p1, p2 = float(d1.get("現價", d1.get("close", 0)) or 0), float(d2.get("現價", d2.get("close", 0)) or 0)
                        n1, n2 = d1.get("_raw_name", d1.get("name", c1)), d2.get("_raw_name", d2.get("name", c2))
                        if p1 > 0 and p2 > 0:
                            ps_b = fetch_pair_swap_history(c1, m1, c2, m2, p1, p2)
                            if ps_b.get("ok"):
                                pair_note_b = f"{n1}({c1}) 換 {n2}({c2})：當下 {ps_b['curr_ratio']:.3f}張換1張 vs 240日中位數 {ps_b['median_ratio']:.3f}張（相對常態折價 {ps_b['discount_pct']:+.1f}%；紅燈豁免門檻≧+10~15%）"

                    st.session_state["mode_b_pkg_txt"] = build_mode_b_deep_package_v244(
                        target_b_codes, merged_b, price_dict_active, outpost_line, now_str,
                        live_tdcc_b, holding_notes_map, radar["master_state"] in ["YELLOW", "RED_LOCKED"],
                        margin_sbl_map=margin_sbl_b, pair_swap_note=pair_note_b
                    )
            else:
                st.warning("請先勾選或輸入 1～5 檔個股代號！")
        if st.session_state.get("mode_b_pkg_txt"):
            st.code(st.session_state["mode_b_pkg_txt"], language="text")

    # 5. 走勢圖（頂部膠囊零遮擋＋均線標籤防碰撞）
    fc_ctrl1, fc_ctrl2 = st.columns([3.2, 1.8])
    fc_zoom_lbl = fc_ctrl1.radio("🔍 走勢圖縮放倍率（手機可單指橫滑）", list(ZOOM_OPT_MAP.keys()), horizontal=True, key="fc_svg_zoom")
    svg_raw_bytes = build_realtime_forecast_svg_raw(fleet, radar, forecast).encode("utf-8")
    fc_ctrl2.download_button(
        label="📥 下載高解析 SVG 原圖 (無限放大)",
        data=svg_raw_bytes,
        file_name=f"twii_forecast_{fleet['twii_date_iso']}.svg",
        mime="image/svg+xml",
        use_container_width=True
    )
    st.markdown(build_realtime_and_forecast_svg_html(fleet, radar, forecast, zoom_mode=ZOOM_OPT_MAP[fc_zoom_lbl]), unsafe_allow_html=True)

    # 6. 未來 1～4 週三情境作戰卡片
    st.markdown("#### 🔮 【未來 1～4 週大盤走勢預測與三情境作戰卡片】（100% 自動換行零裁切・每日浮動重算）")
    fc_view_mode = st.radio("📱 顯示樣式切換", ["🃏 響應式防切字卡片 (電腦/手機預設)", "📊 簡表模式 (供複製)"], horizontal=True, key="fc_view_mode_v65")
    if "卡片" in fc_view_mode:
        st.markdown(build_forecast_responsive_cards_html(forecast), unsafe_allow_html=True)
    else:
        st.dataframe(pd.DataFrame(forecast["table_rows"]), use_container_width=True, hide_index=True)

    # 7. 8 大【結論：說明】高可讀性評估面板
    st.markdown(build_8_indicator_panels_html(
        fleet, us_data, deriv_eff, breadth, scan_data, tw_hot_sec_info,
        sb_fini_oi, sb_margin_2d, sb_margin_2d_pct, sb_margin_dd, sb_maint, sb_basis,
        sb_consec_cover, sb_cover_3d, sb_limit_up, sb_limit_dn, vix_now, vix_chg,
        tnx_val, tnx_chg, sox_chg, tsm_chg, tsm_ext, nvda_chg, nvda_ext, fx_now, fx_stopped_dep
    ), unsafe_allow_html=True)

    # 8. 大盤各級均線動態城堡導航表
    st.markdown("#### 🧭 【大盤各級均線動態城堡導航表】（含月 20MA 與月 36MA 股災鐵板）")
    def ma_row(nm, val, guide):
        diff = round(val - fleet["twii_now"], 0)
        pct = round(diff / max(1.0, fleet["twii_now"]) * 100.0, 2)
        pos = f"上方 +{diff:,.0f}點 (+{pct:.2f}%)" if diff >= 0 else f"✅下方支撐 {diff:,.0f}點 ({pct:.2f}%)"
        return {"均線週期": nm, "動態點位 (距現價)": f"{val:,.0f} 點 ({pos})", "實戰意義": guide}

    st.dataframe(pd.DataFrame([
        ma_row("① 日 20MA (月線・波段反壓)", fleet["ma20"], "下彎蓋頭強壓，反彈至此區宜收尾 00400A 與弱勢股"),
        ma_row("② 日 240MA (年線・多空生命線)", fleet["ma240"], "跌破觸發股災警戒；站回有助修復信心但仍須看月線與落底4/5"),
        ma_row("③ 日 10MA (政策逃命波驗證線)", fleet["ma10"], "限空令後須連 3 日站穩 10MA 且落底達 4/5 才脫離逃命波"),
        ma_row("④ 日 5MA (五日攻擊線)", fleet["ma5"], "短線止跌第一步須重新站上並走平"),
        ma_row("⑤ 月 20MA (第一隻腳核心鐵板)", fleet["m_ma20"], "歷史急殺股災第一道強力支撐區"),
        ma_row("⑥ 月 36MA (極端系統底)", fleet["m_ma36"], "若美股引發二次崩跌之終極防禦底線")
    ]), use_container_width=True, hide_index=True)

# ==================== TAB 2: 換股計算機、短單分帳與最新四水庫 ====================
with tab2:
    st.markdown(f"#### ⚡ 【模組三之一：日月光短單獨立分帳監控 ＆ {sb_fish_3711:.0f} 元低接釣魚紀律（嚴禁 500 元以上追高）】")
    if sb_shares_3711_short > 0:
        short_cost_est = round(sb_shares_3711_short * sb_short_3711_avg * 1.001425, 0)
        short_pnl = round((p_3711 - sb_short_3711_be) * sb_shares_3711_short, 0)
        short_pnl_pct = round(((p_3711 - sb_short_3711_avg) / max(1.0, sb_short_3711_avg)) * 100.0, 2)
        trailing_ma5_active = bool(sb_short_3711_above_ma5 or (p_3711 >= ma5_3711))
        hit_be = (p_3711 < sb_short_3711_be)
        hit_ma5_trail = trailing_ma5_active and (p_3711 < ma5_3711)
        trigger_exit = hit_be or hit_ma5_trail
        sl_cls = "short-ledger-stop" if trigger_exit else "short-ledger-ok"
        if trigger_exit:
            reason_s = f"跌破損益平衡價 ({sb_short_3711_be:.2f})" if hit_be else f"跌破 5MA 移動停利 ({ma5_3711:.2f})"
            sl_status = f"🚨 【觸發獨立短單停損／停利警戒！】現價 {p_3711:.1f} 已{reason_s} ➔ 嚴守 v25.0 紀律 5 核對執行撤退，絕不變長單！"
            active_line_str = f"防線 = 5MA {ma5_3711:.2f} 元 ／ 硬底線 {sb_short_3711_be:.2f} 元"
        elif trailing_ma5_active:
            active_line_str = f"已啟動【破 5MA 移動停利防線 = {ma5_3711:.2f} 元】（硬底線 {sb_short_3711_be:.2f} 元）"
            sl_status = f"✅ 【短單強勢續抱・沿 5MA 移動停利】現價 {p_3711:.1f} 站穩 5MA ({ma5_3711:.2f}) 之上！"
        else:
            active_line_str = f"階段一防守：【先守損益平衡價 = {sb_short_3711_be:.2f} 元】（待突破 5MA {ma5_3711:.2f} 元後切換為破 5MA 停利）"
            sl_status = f"✅ 【短單安全續抱中】穩守損益平衡價 {sb_short_3711_be:.2f} 元之上！"

        fish_gap_pct = round(((p_3711 - sb_fish_3711) / max(1.0, p_3711)) * 100.0, 1)
        chase_warn = "⛔ <b style='color:#fde047;'>目前現價已達 500 元以上禁追高區！</b>" if p_3711 >= 500.0 else f"距釣魚目標價 <b>{sb_fish_3711:.0f} 元</b> 尚有 <b>-{fish_gap_pct:.1f}%</b> 空間"
        st.markdown(clean_html(f"""
        <div class="{sl_cls}">
        <b>🎯 淨降倉換強獨立短單：日月光投控 (3711) 零股 {sb_shares_3711_short:,} 股（均價 {sb_short_3711_avg:.2f} 元 ｜ 預估含費成本 {short_cost_est:,.0f} 元）</b><br>
        • <b>即時損益與防線</b>：現價 <b>{p_3711:.2f} 元</b>（帳面預估 <b>{short_pnl:+,.0f} 元 / {short_pnl_pct:+.2f}%</b>）｜ <b>{active_line_str}</b><br>
        • <b>即時紀律裁決</b>：<b style="font-size:16px;">{sl_status}</b><br>
        • <b>🎣 戰情室低接釣魚鎖定</b>：{chase_warn} ｜ 保留水庫四純現金 <b>{sb_twd_cash_wan:.1f} 萬元</b> 鎖定 <b>{sb_fish_3711:.0f} 元</b> 區間及 12 月初利空落地。<br>
        • <i>（註：此筆短單與水庫二傳家寶 3,000 股 @172.67 完全分帳管理）</i>
        </div>
        """), unsafe_allow_html=True)
    else:
        st.info(f"目前無進行中之換強短單部位；保留水庫四純現金 {sb_twd_cash_wan:.1f} 萬元，嚴禁 500 元以上追高日月光，鎖定 {sb_fish_3711:.0f} 元區間低接。")

    st.markdown("#### ⚖️ 【模組三之二：通用雙股比價換龍頭計算機（對接 v25.0 折價 10%～15% 豁免門檻）】")
    sw_p1, sw_p2, sw_p3 = st.columns([2.2, 1.4, 1.4])
    swap_preset = sw_p1.selectbox("🔄 選擇比價換股組合", ["🏦 玉山金(2884) 換 富邦金(2881) [金融換龍頭]", "🔬 聯發科(2454) vs 日月光(3711) [IC/封測比價]", "⚙️ 自訂任意兩檔比價"], key="swap_preset_sel")
    c_sell_def, c_buy_def = ("2884", "2881") if "玉山金" in swap_preset else (("2454", "3711") if "聯發科" in swap_preset else ("2884", "2881"))
    c_sell = sw_p2.text_input("賣出股代號 (弱/次龍頭)", value=c_sell_def, key="sw_cs").strip()
    c_buy = sw_p3.text_input("買入目標代號 (龍頭)", value=c_buy_def, key="sw_cb").strip()

    if len(c_sell) == 4 and len(c_buy) == 4:
        inf_s, inf_b = price_dict_active.get(c_sell, {}), price_dict_active.get(c_buy, {})
        nm_s, mkt_s, nm_b, mkt_b = inf_s.get("name", c_sell), inf_s.get("market", "TW"), inf_b.get("name", c_buy), inf_b.get("market", "TW")
        ps_live, pb_live = get_live_p(c_sell, inf_s.get("close", 100.0) or 100.0), get_live_p(c_buy, inf_b.get("close", 100.0) or 100.0)
        pair_stat = fetch_pair_swap_history(c_sell, mkt_s, c_buy, mkt_b, ps_live, pb_live)
        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric(f"賣出：{nm_s} ({c_sell})", f"{ps_live:.2f} 元")
        sc2.metric(f"買入：{nm_b} ({c_buy})", f"{pb_live:.2f} 元")
        sc3.metric("當下換股比 (賣幾張換1張龍頭)", f"{pair_stat['curr_ratio']:.3f} 張", f"240日中位數 {pair_stat['median_ratio']:.3f} 張")
        sc4.metric("相對常態折價幅度", f"{pair_stat['discount_pct']:+.1f}%", "門檻：>= +10% ~ +15%")
        disc_v = pair_stat["discount_pct"]
        if disc_v >= 15.0: pair_verdict = f"💎 【超值換龍頭綠燈！】折價 +{disc_v:.1f}% (≧15%)，符合 v25.0 紅燈豁免 (b)！"
        elif disc_v >= 10.0: pair_verdict = f"🟢 【達標換龍頭觀察區！】折價 +{disc_v:.1f}% (10%~15%)，可於 09:15 後分批換手！"
        elif disc_v >= 0.0: pair_verdict = f"🟡 【折價尚不足 (+{disc_v:.1f}%)】尚未達 10% 黃金門檻，建議觀望！"
        else: pair_verdict = f"🔴 【溢價不划算 ({disc_v:.1f}%)】目前換入龍頭比平常更貴，嚴禁盲目追換！"
        st.markdown(clean_html(f"""
        <div class="swap-card">
        • <b>📐 近 240 交易日比價區間</b>：最低 <b>{pair_stat['min_ratio']:.3f} 張</b> ～ 中位數 <b>{pair_stat['median_ratio']:.3f} 張</b> ～ 最高 <b>{pair_stat['max_ratio']:.3f} 張</b> ｜ 目前：<b>{pair_stat['curr_ratio']:.3f} 張 {nm_s} 換 1 張 {nm_b}</b><br>
        • <b>🚦 v25.0 模組 0/18 比價裁決</b>：<b style="color:#00e676; font-size:15.5px;">{pair_verdict}</b>
        </div>
        """), unsafe_allow_html=True)

    st.markdown("#### 🔄 【模組三之三：00400A 最後收尾轉債試算 ＆ 水庫三 (00985B / 00713) 進場殖利率反推】")
    div_985_ttm, div_985_ann, div_713_ttm = etf_m["00985B"]["div_ttm"], etf_m["00985B"]["div_latest"] * 12.0, etf_m["00713"]["div_ttm"]
    y_985_ttm, y_985_ann, y_713_ttm = round(div_985_ttm / max(0.1, p_00985b) * 100.0, 2), round(div_985_ann / max(0.1, p_00985b) * 100.0, 2), round(div_713_ttm / max(0.1, p_00713) * 100.0, 2)
    cur_985b_annual_inc = sb_lots_00985b * 1000.0 * div_985_ttm

    if sb_lots_00400a > 0:
        sell_400a_val = sb_lots_00400a * 1000.0 * p_00400a
        conv_985b_lots = sell_400a_val / max(1.0, p_00985b * 1000.0)
        conv_985b_annual_inc = conv_985b_lots * 1000.0 * div_985_ttm
        st.markdown(clean_html(f"""
        <div class="tab-guide-box" style="border-left-color:#fbbf24;">
        <b>⚠️ 00400A 最後收尾進度（僅剩最後 {sb_lots_00400a:.0f} 張）</b>：<br>
        • <b>現況與禁買紅線</b>：00400A 現價 <b>{p_00400a:.2f} 元</b>（餘 {sb_lots_00400a:.0f} 張 @{sb_cost_00400a:.2f}），12/08 除息腰斬至 <b>0.06 元</b>；<b>嚴禁加碼，亦嚴禁買 00406A 與垃圾債</b>。<br>
        • <b>動態收尾退場區</b>：鎖定 <b>{eff_t1_400:.2f}～{eff_t2_400:.2f} 元</b> 出清（可於側邊欄自訂掛賣價；成交後將張數調為 0 並按💾存檔）。<br>
        • <b>收尾轉債效益</b>：剩餘 {sb_lots_00400a:.0f} 張（約 <b>{sell_400a_val/10000:.1f} 萬元</b>）全轉 <b>00985B ({p_00985b:.2f}元)</b> 可再添 <b>{conv_985b_lots:.1f} 張</b>（合計衝破 <b>{sb_lots_00985b + conv_985b_lots:.1f} 張</b>），年債息增至約 <b style="color:#00e676;">{cur_985b_annual_inc + conv_985b_annual_inc:,.0f} 元</b>！
        </div>
        """), unsafe_allow_html=True)
    else:
        st.markdown(clean_html(f"""
        <div class="tab-guide-box" style="border-left-color:#00e676;">
        <b>✅ 【股轉債任務圓滿完成！】00400A 已全數清零退場</b>：<br>
        • 目前水庫三已穩健累積 <b>00985B {sb_lots_00985b:.1f} 張 (@{sb_cost_00985b:.2f})</b>，預估年領 RR2 債息約 <b style="color:#00e676;">{cur_985b_annual_inc:,.0f} 元</b>（約 {cur_985b_annual_inc/12:,.0f} 元/月）！
        </div>
        """), unsafe_allow_html=True)

    st.dataframe(pd.DataFrame([
        {"水庫三核心標的": "00985B 群益ESG投等債0-5 (月配65%)", "即時現價": f"{p_00985b:.2f} 元", "近一年/最新年化配息": f"TTM {div_985_ttm:.3f}元 / 年化 {div_985_ann:.3f}元", "目前殖利率": f"TTM {y_985_ttm:.2f}% / 年化 {y_985_ann:.2f}%", "💎 超值重錘區": f"(≧6.0%) <= {div_985_ttm/0.06:.2f} ~ {div_985_ann/0.06:.2f} 元", "✅ 合理建倉區": f"(5.5%~6.0%) {div_985_ann/0.06:.2f} ~ {div_985_ann/0.055:.2f} 元", "⚖️ 底線進場價": f"(≧5.0%) <= {div_985_ttm/0.05:.2f} ~ {div_985_ann/0.05:.2f} 元", "當前買點判定": "💎 超值建倉區！" if y_985_ann >= 6.0 else ("✅ 合理建倉區" if y_985_ann >= 5.5 else "⚖️ 達5%底線")},
        {"水庫三核心標的": "00713 元大台灣高息低波 (季配35%)", "即時現價": f"{p_00713:.2f} 元", "近一年/最新年化配息": f"TTM {div_713_ttm:.2f}元", "目前殖利率": f"TTM {y_713_ttm:.2f}%", "💎 超值重錘區": f"(≧7.0%) <= {div_713_ttm/0.07:.2f} 元", "✅ 合理建倉區": f"(6.0%~7.0%) {div_713_ttm/0.07:.2f} ~ {div_713_ttm/0.06:.2f} 元", "⚖️ 底線進場價": f"(≧5.5%) <= {div_713_ttm/0.055:.2f} 元", "當前買點判定": "💎 超值重錘區" if y_713_ttm >= 7.0 else ("✅ 合理建倉區！" if y_713_ttm >= 6.0 else "⚖️ 觀察區")}
    ]), use_container_width=True, hide_index=True)

    st.markdown("#### 💧 【模組三之四：全資產四水庫即時市值、半導體集中度與被動現金流進度】")
    r_c1, r_c2, r_c3, r_c4 = st.columns(4)
    r_c1.metric("💰 全資產即時估值 (零槓桿)", f"{total_assets/10000:,.1f} 萬元", f"匯率 {fx_now:.3f}")
    r_c2.metric("🔬 半導體總曝險佔比", f"{semi_ratio_pct:.1f}%", "⚠️ >50% 須靠水庫三平衡" if semi_ratio_pct > 50 else "✅ 配置均衡", delta_color="inverse")
    r_c3.metric("🌊 水庫三累積進度 (目標200萬)", f"{val_r3/10000:,.1f} / 200 萬", f"00985B {sb_lots_00985b:.0f}張 ({val_r3/2000000*100:.1f}%)")
    r_c4.metric("🔫 水庫四機動彈藥 (SGOV+現金)", f"{val_r4/10000:,.1f} 萬元", f"含台幣現金 {sb_twd_cash_wan:.1f}萬 + USD")

    sat_parts = []
    if sb_lots_00400a > 0: sat_parts.append(f"00400A 餘{sb_lots_00400a:.0f}張(@{sb_cost_00400a:.2f})")
    if sb_lots_6239 > 0: sat_parts.append(f"力成{sb_lots_6239:.1f}張")
    if sb_lots_3264 > 0: sat_parts.append(f"欣銓{sb_lots_3264:.1f}張")
    sat_desc = " ＋ ".join(sat_parts) if sat_parts else "✅ 力成/欣銓/00400A 均已清空歸零"

    st.dataframe(pd.DataFrame([
        {"水庫分類": "水庫一：生活＋急用雙水庫 (永不動用)", "核心組成": "日常 20萬 ＋ 急用定存 20萬 (撐9個月開銷)", "即時市值(萬)": round(val_r1/10000, 1), "佔比(%)": round(val_r1/total_assets*100, 1)},
        {"水庫分類": "水庫二：傳家寶底倉 — 台股半導體", "核心組成": f"台積電4張(@{p_2330:,.0f}) ＋ 日月光3張(@{p_3711:,.0f})", "即時市值(萬)": round(val_r2_tw_semi/10000, 1), "佔比(%)": round(val_r2_tw_semi/total_assets*100, 1)},
        {"水庫分類": "水庫二：傳家寶底倉 — 台股金融股息", "核心組成": "玉山金100.45張、富邦金11.74張、台中銀27.15張、台新新光金4.88張、臺企銀1.09張", "即時市值(萬)": round(val_r2_tw_fin/10000, 1), "佔比(%)": round(val_r2_tw_fin/total_assets*100, 1)},
        {"水庫分類": "水庫二：傳家寶底倉 — 美股 AI 龍頭", "核心組成": f"AVGO 35股(${p_avgo:.2f}) ＋ NVDA 80股(${p_nvda:.2f})", "即時市值(萬)": round(val_r2_us_semi/10000, 1), "佔比(%)": round(val_r2_us_semi/total_assets*100, 1)},
        {"水庫分類": "水庫三：第二道現金流 (目標200萬)", "核心組成": f"00985B {sb_lots_00985b:.1f}張(@{sb_cost_00985b:.2f}) ＋ 00713 {sb_lots_00713:.1f}張", "即時市值(萬)": round(val_r3/10000, 1), "佔比(%)": round(val_r3/total_assets*100, 1)},
        {"水庫分類": "水庫四：跨市機動彈藥 (力成+換倉充實)", "核心組成": f"純台幣現金 {sb_twd_cash_wan:.1f}萬 ＋ SGOV 30股(${p_sgov:.2f}) ＋ ${sb_usd_cash:,.0f}", "即時市值(萬)": round(val_r4/10000, 1), "佔比(%)": round(val_r4/total_assets*100, 1)},
        {"水庫分類": "獨立分帳：淨降倉換強短單", "核心組成": f"日月光零股 {sb_shares_3711_short}股(@{sb_short_3711_avg:.1f} / 平衡價{sb_short_3711_be:.2f} / 低接鎖定{sb_fish_3711:.0f})", "即時市值(萬)": round(val_short_3711/10000, 1), "佔比(%)": round(val_short_3711/total_assets*100, 1)},
        {"水庫分類": "待收尾與已清空衛星部位", "核心組成": sat_desc, "即時市值(萬)": round((val_sat_semi+val_sat_400a)/10000, 1), "佔比(%)": round((val_sat_semi+val_sat_400a)/total_assets*100, 1)}
    ]), use_container_width=True, hide_index=True)

    with st.expander("📦 次要收合：0050／0050正2 紀錄（目前已全數清空 0 張）", expanded=False):
        st.caption(f"0050正2 (00631L) 現價：{etf_m['00631L']['p']:.2f} 元（5MA={etf_m['00631L'].get('ma5', 0):.2f} / 20MA={etf_m['00631L'].get('ma20', 0):.2f}）｜ 已全數清空，依模組 0 雙雷達控管。")

# ==================== TAB 3: 短線旱地拔蔥榜 ====================
with tab3:
    if not scan_data:
        st.info("💤 【Layer 2 按需載入待機中】點下方按鈕才開始掃描全台 170 檔非殭屍股 K 線、週10/月5MA、20MA扣抵、真實當沖率與 5 組接軌指標（掃完後與分頁四、五共用免重跑）。")
        if st.button("🚀 立即啟動全台個股掃描 (Layer 2)", type="primary", key="btn_l2_tab3"):
            _run_layer2_scan()
            st.rerun()
    else:
        if radar["master_state"] == "RED_LOCKED":
            st.error(f"🚨 【淘金客 v25.0 紅燈鎖定（預警 {radar['w_count']}/5・落底 {radar['b_count']}/5）】嚴禁動用新現金追買！已自動排除高ADX空頭加速與 BIAS10>+12% 極度過熱股。")
        elif radar["master_state"] == "YELLOW":
            st.warning("🟡 【黃燈前哨警戒】禁止追高並優先收網多頭衛星股，僅限新穀倉1／防彈衣且無3日事件雷者小額試單。")
        else:
            st.success(f"{radar['master_badge']} ｜ 已於 {scan_data['ts']} 完成全台掃描！")

        st.markdown("#### 🔥 【表 1-A：短線看技術＋基本面（站穩月線＋DMI多方＋雙成長）】")
        if not df_launch_fund.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_launch_fund)), use_container_width=True)
        else: st.info("今日盤面暫無同時符合站穩月線、DMI多方、非過熱與營收雙成長之真旱地拔蔥標的。")

        st.markdown("#### 🎭 【表 1-B：短線純看技術面（不看基本面・主力籌碼旱地拔蔥榜）】")
        if not df_launch_pure.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_launch_pure)), use_container_width=True)
        else: st.info("今日暫無純技術點火標的。")

        st.markdown("#### 🎯 【表二：N字洗盤蓄勢 ＆ 拉回月線/十日線低接榜】")
        if not df_pullback.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_pullback)), use_container_width=True)
        else: st.info("今日暫無量縮回測月線有守之標的。")

        st.markdown("#### 🛡️ 【表三：上升軌道推升 ＆ 3日急拉過熱警戒榜】")
        if not df_orbit.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_orbit)), use_container_width=True)
        else: st.info("今日暫無多頭軌道推升標的。")

# ==================== TAB 4: 長線波段與 MSCI 動態主流池 ====================
with tab4:
    st.markdown(f"#### 🌐 【模組四：MSCI 每季動態納入/移除名單 × 受影響族群龍頭與當日熱門股連動】（當前季度：`{msci_data['quarter']}`）")
    with st.expander("🔄 每季公告日一鍵更新：貼上最新 MSCI 季度調整公告文字，3 秒自動解析並永久儲存", expanded=False):
        q_lbl = st.text_input("新季度標籤", value="2027/02 (季度調整)")
        raw_msci_txt = st.text_area("貼上新聞中的『標準指數新增/刪除、小型指數新增/刪除』段落（含 4 碼代號）：", height=110)
        if st.button("💾 解析並更新為最新季度 MSCI 名單"):
            if raw_msci_txt.strip():
                parsed_m = parse_msci_text_override(raw_msci_txt, price_dict_active, q_lbl)
                try:
                    with open(MSCI_CACHE_FILE, "w", encoding="utf-8") as f: json.dump(parsed_m, f, ensure_ascii=False)
                    fetch_dynamic_msci_data.clear()
                    st.session_state.pop("v65_scan_data", None)
                    st.success("✅ 已解析並儲存最新季度 MSCI 名單！請重新啟動 Layer 2 掃描套用。")
                except Exception as e:
                    st.error(f"儲存失敗：{e}")

    if not scan_data:
        st.info("💤 啟動 Layer 2 全台個股掃描後，即可檢視 MSCI 族群龍頭連動表、MSCI 完整 5 組指標表及長線波段表 3-A／3-B。")
        if st.button("🚀 立即啟動全台個股掃描 (Layer 2)", type="primary", key="btn_l2_tab4"):
            _run_layer2_scan()
            st.rerun()
    else:
        scanned_recs = scan_data["sec_recs"]
        theme_link_rows = []
        if not df_msci_audit.empty:
            for th_name, grp in df_msci_audit.groupby("影響細分族群"):
                adds = [f"{r['代碼']}{r['股名'].split(' ')[0]}({r['今日%']:+.1f}%)" for _, r in grp.iterrows() if "新增" in str(r["MSCI異動別"])]
                dels = [f"{r['代碼']}{r['股名'].split(' ')[0]}({r['今日%']:+.1f}%)" for _, r in grp.iterrows() if "刪除" in str(r["MSCI異動別"])]
                bell_list = MSCI_THEME_BELLWETHERS.get(th_name, [])
                bells_str = "、".join([f"{bn}({bc}) {price_dict_active.get(bc,{}).get('chg_pct',0):+.1f}%" for bc, bn in bell_list]) or "依盤面領漲股"
                same_th_recs = sorted([x for x in scanned_recs if (infer_msci_theme(x["code"], x["name"], x["official_sec"]) == th_name)], key=lambda x: (x["chg_1d"], x["yod_ratio"]), reverse=True)
                hot_in_th = "、".join([f"{x['name']}({x['code']}) {x['chg_1d']:+.1f}%[量{x['yod_ratio']:.1f}x]" for x in same_th_recs[:3]]) or "當日量縮整理"
                theme_link_rows.append({"受影響細分族群": th_name, "📈 本季 MSCI 納入受惠": "、".join(adds) if adds else "➖", "📉 本季 MSCI 剔除承壓": "、".join(dels) if dels else "➖", "👑 族群指標龍頭股": bells_str, "🔥 當日族群盤中熱門前3強": hot_in_th})
            st.dataframe(pd.DataFrame(theme_link_rows), use_container_width=True, hide_index=True)

        st.markdown("##### 📋 【本季 MSCI 完整納入/移除標的即時掃描表】")
        if not df_msci_audit.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_msci_audit)), use_container_width=True)

        st.markdown("#### 🏛️ 【表 3-A：核心權值與政策防彈衣 長線多頭波段榜】")
        if not df_long_bc.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_long_bc)), use_container_width=True)
        else: st.info("目前暫無同時站上月線與 DMI 多方之權值股，請看下方表 3-B。")

        st.markdown("#### 🔄 【表 3-B：權值股超跌回測支撐買點 ＆ 轉機題材榜】")
        if not df_long_turn.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_long_turn)), use_container_width=True)
        else: st.info("目前暫無符合條件之回測支撐買點。")

# ==================== TAB 5: 穀倉與防墊料專區 ====================
with tab5:
    st.markdown("#### 🌾 【宏爺講股：三大新穀倉 vs. 🛡️政策內需防彈衣 vs. 🐀最後一隻老鼠（防墊料壓貨）全景體檢專區】")
    st.markdown(clean_html("""
    <div class="tab-guide-box">
    • <b>🌟 新穀倉 1（CSP自研/共通規格 +15~23分）</b>：台積電、日月光投控、世芯-KY、創意、聯發科、穎崴、旺矽、台光電、金像電。<br>
    • <b>⚡ 新穀倉 2（解決缺電/過熱基建 +10~14分）</b>：台達電、光寶科、奇鋐、雙鴻、健策、順達、華城、士電。<br>
    • <b>📱 新穀倉 3（邊緣AI與記憶體封測 +5分）</b>：大立光、玉晶光、鴻海、力成、聯詠。<br>
    • <b>🛡️ 政策內需防彈衣（兆元水電工程/官股金控 +8~18分）</b>：中興電、亞力、國統、中鼎、亞翔、兆豐金、中信金、第一金。<br>
    • <b>🐀 防墊料濾網（扣減 -20分）</b>：硬體組裝 ODM 若月營收 YoY &gt; 0 但季報存貨或應收帳款 QoQ &gt;= 20% 且營業現金流為負，強制標記警示。
    </div>
    """), unsafe_allow_html=True)

    if not scan_data:
        st.info("💤 點擊下方按鈕啟動 Layer 2 掃描，即可載入三大新穀倉戰力排行與舊穀倉 ODM 存貨防墊料體檢表。")
        if st.button("🚀 立即啟動全台個股掃描 (Layer 2)", type="primary", key="btn_l2_tab5"):
            _run_layer2_scan()
            st.rerun()
    elif not df_granary_audit.empty:
        df_g_safe = df_granary_audit[~df_granary_audit["代碼"].isin(OLD_GRANARY_ODM_WATCH)].copy()
        df_g_odm = df_granary_audit[df_granary_audit["代碼"].isin(OLD_GRANARY_ODM_WATCH)].copy()
        st.markdown("##### 🌟 【一、三大新穀倉 ＆ 🛡️ 政策內需防彈衣 即時戰力排行】")
        st.dataframe(style_tw_stock_df(pin_code_name_index(df_g_safe)), use_container_width=True)
        st.markdown("##### 🐀 【二、最後一隻老鼠（舊穀倉純伺服器組裝 ODM 防墊料體檢表）】")
        if not df_g_odm.empty: st.dataframe(style_tw_stock_df(pin_code_name_index(df_g_odm)), use_container_width=True)

# ==================== TAB 6: 上市櫃月營收快篩 (Layer 3) ====================
with tab6:
    target_latest_ym = get_target_revenue_ym_list()[0][2]
    st.markdown(f"#### 📈 【上市櫃每月營收動態撈取 ＆ 三級除殭屍股快篩專區】（最新目標年月：`{target_latest_ym}`）")

    rev_b1, rev_b2 = st.columns([2.5, 4.5])
    if rev_b1.button("📊 啟動／重抓 1,972 家上市櫃月營收與三率估值 (Layer 3)", type="primary", key="btn_l3_rev"):
        with st.spinner("📡 Layer 3 執行中：正在下載全市場 1,972 家最新月營收、法人買賣超與三率 EPS 估值..."):
            st.session_state["v65_rev_df"] = fetch_all_monthly_revenue_df_on_demand()
            st.rerun()

    df_all_rev = st.session_state.get("v65_rev_df", pd.DataFrame())
    if df_all_rev.empty:
        rev_b2.info("💤 【Layer 3 按需載入待機中】點左側按鈕才爬取 1,972 家月營收與財報，避免拖慢每日開盤速度。")
    else:
        rev_b2.success(f"✅ 已載入全市場 {len(df_all_rev):,} 家上市櫃月營收與三大法人估值資料！")
        f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns([1.6, 1.4, 0.9, 0.9, 2.8])
        search_kw = f_col1.text_input("🔍 搜尋代碼/名稱/備註", value="", placeholder="例如：3711、2383、3189...")
        mkt_filter = f_col2.selectbox("🏛️ 市場與籌碼篩選", [f"全部市場 (含{target_latest_ym}與上月)", f"⚡ 僅看已公告最新月 ({target_latest_ym})", "🔥 僅看法人買超 (外資>0 或 投信>0)", "🏦 僅看投信買超認養股", "🔥 僅看含 CB 可轉債標的", "僅看上市", "僅看上櫃"])
        min_yoy = f_col3.number_input("📊 最低單月YoY(%)", value=20.0, step=5.0, format="%.1f")
        min_mom = f_col4.number_input("📈 最低月增MoM(%)", value=0.0, step=5.0, format="%.1f")
        quick_mode = f_col5.selectbox("⚡ 實戰快篩模式", ["🌱 久旱逢甘霖：前期累計衰退/低迷 ➔ 本月首次爆發 YoY>=20% 轉機股 (排除營建)", "👑 玩法1終極獵殺：久旱破繭(YoY>=20%) ＋ 大戶鎖碼(>=50%) ＋ 收紅或法人買超", "🔥 玩法3可轉債套利：高成長(YoY>=20%) ＋ 具【🔥含CB可轉債】標的", "💎 低估鑽石成長股：單月YoY>=20% 且 現價低於【💎EPS合理切入價】", "🚀 有基之彈雙成長：單月YoY>=設定值 且 累計YoY>0% 且 MoM>=設定值", "📋 顯示全市場所有公司"])

        z_col1, v_col1, v_col2 = st.columns([2.4, 2.8, 3.8])
        exclude_zombie_rev = z_col1.checkbox("🧹 自動剔除三級制無量殭屍股 (<150元須>=1,000張)", value=True)
        df_filtered = df_all_rev.copy()
        if exclude_zombie_rev and not df_filtered.empty and (not search_kw.strip()):
            vol_mult_now, _ = get_volume_multiplier()
            df_filtered = df_filtered[df_filtered.apply(lambda r: (str(r["代碼"]) in CORE_WATCHLIST) or (not is_zombie_stock_tiered(str(r["代碼"]), r["現價"], float(r.get("成交量(張)", 0) or 0) * vol_mult_now)), axis=1)].copy()

        if "僅看已公告最新月" in mkt_filter: df_filtered = df_filtered[df_filtered["資料年月"] == target_latest_ym]
        elif "法人買超" in mkt_filter: df_filtered = df_filtered[(df_filtered["外資買賣(張)"] > 0) | (df_filtered["投信買賣(張)"] > 0)]
        elif "投信買超" in mkt_filter: df_filtered = df_filtered[df_filtered["投信買賣(張)"] > 0]
        elif "CB" in mkt_filter: df_filtered = df_filtered[df_filtered["CB可轉債"].str.contains("CB", na=False)]
        elif mkt_filter == "僅看上市": df_filtered = df_filtered[df_filtered["市場"] == "上市"]
        elif mkt_filter == "僅看上櫃": df_filtered = df_filtered[df_filtered["市場"] == "上櫃"]

        if search_kw.strip():
            kw = search_kw.strip()
            df_filtered = df_filtered[df_filtered["代碼"].str.contains(kw, case=False, na=False) | df_filtered["公司名稱"].str.contains(kw, case=False, na=False) | df_filtered["產業別"].str.contains(kw, case=False, na=False) | df_filtered["財報營收備註說明"].str.contains(kw, case=False, na=False)]
        else:
            not_const = ((~df_filtered["產業別"].str.contains("建材營造|營造", na=False)) & (~df_filtered["備註AI真偽鑑別"].str.contains("🏗️", na=False)) & (~df_filtered["代碼"].isin(CONSTRUCTION_BLACKLIST))) | (df_filtered["代碼"].isin(POLICY_DEFENSE_ARMOR))
            if "久旱逢甘霖" in quick_mode: df_filtered = df_filtered[df_filtered["轉機與成長型態"].str.contains("久旱逢甘霖|低迷翻揚", na=False) & (df_filtered["年增YoY%"] >= min_yoy) & (df_filtered["當月營收(億)"].fillna(0) >= 0.20) & not_const]
            elif "玩法1" in quick_mode: df_filtered = df_filtered[df_filtered["轉機與成長型態"].str.contains("久旱逢甘霖|低迷翻揚", na=False) & (df_filtered["年增YoY%"] >= min_yoy) & (df_filtered["_tdcc_num"] >= 50.0) & ((df_filtered["今日%"].fillna(-1.0) >= 0.0) | (df_filtered["外資買賣(張)"] > 0) | (df_filtered["投信買賣(張)"] > 0)) & (~df_filtered["備註AI真偽鑑別"].str.contains("⚠️|🏗️", na=False)) & not_const]
            elif "玩法3" in quick_mode: df_filtered = df_filtered[(df_filtered["年增YoY%"] >= min_yoy) & df_filtered["CB可轉債"].str.contains("CB", na=False) & not_const]
            elif "低估鑽石" in quick_mode: df_filtered = df_filtered[(df_filtered["年增YoY%"] >= min_yoy) & df_filtered["估值診斷"].str.contains("低估鑽石", na=False) & not_const]
            elif "有基之彈" in quick_mode: df_filtered = df_filtered[(df_filtered["年增YoY%"] >= min_yoy) & (df_filtered["累計YoY%"].fillna(-1) > 0.0) & (df_filtered["月增MoM%"] >= min_mom) & not_const]

        if v_col1.button("🔬 執行【24個月首次 YoY>=20%】深度驗證", type="primary"):
            with st.spinner(f"併發調閱 FinMind 歷史月營收 (共 {len(df_filtered)} 檔)..."):
                st.session_state["ver_24m_map"] = verify_24m_first_breakout_batch(tuple(df_filtered["代碼"].tolist()), threshold_yoy=min_yoy)

        if st.session_state.get("ver_24m_map"):
            ver_map = st.session_state["ver_24m_map"]
            if any(c in ver_map for c in df_filtered["代碼"]):
                df_filtered = df_filtered[df_filtered["代碼"].isin([c for c, val in ver_map.items() if (isinstance(val, tuple) and val[0])])].copy()
                df_filtered["轉機與成長型態"] = df_filtered["代碼"].map(lambda c: ver_map[c][2])
                v_col2.success(f"✅ 已完成 24 個月破繭驗證！共 {len(df_filtered)} 檔純破繭轉機股。")

        df_display_rev = df_filtered.drop(columns=["_tdcc_num"], errors="ignore")
        st.download_button(label=f"📥 下載當前篩選月營收與法人估值表 ({len(df_display_rev)} 家) CSV", data=df_display_rev.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"), file_name=f"tw_rev_v65_{target_latest_ym}.csv", mime="text/csv")

        def color_rev_val(val):
            try:
                v = float(val)
                return "color: #ff3b30; font-weight: bold;" if v > 0 else ("color: #00b853; font-weight: bold;" if v < 0 else "")
            except Exception:
                return ""

        df_pinned_rev = pin_code_name_index(df_display_rev)
        rev_color_cols = [c for c in ["今日%", "外資買賣(張)", "投信買賣(張)", "月增MoM%", "年增YoY%", "累計YoY%"] if c in df_pinned_rev.columns]
        rev_fmt = {"現價": "{:.2f}", "預估EPS(元)": "{:.2f}", "本益比(PE)": "{:.1f}", "💎EPS合理切入價": "{:.2f}", "今日%": "{:+.2f}%", "成交量(張)": "{:,.0f}", "外資買賣(張)": "{:+,d}", "投信買賣(張)": "{:+,d}", "當月營收(億)": "{:,.2f}", "月增MoM%": "{:+.2f}%", "年增YoY%": "{:+.2f}%", "累計營收(億)": "{:,.2f}", "累計YoY%": "{:+.2f}%"}
        st.dataframe(df_pinned_rev.style.map(color_rev_val, subset=rev_color_cols).format(rev_fmt, na_rep="-"), use_container_width=True, height=580)

if auto_refresh:
    time.sleep(20)
    fetch_mis_realtime_core16.clear()
    st.rerun()
