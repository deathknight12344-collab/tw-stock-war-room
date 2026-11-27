import base64, re
import numpy as np
import pandas as pd

from config_events import clean_html, xml_esc, map_tw_theme_cluster, get_tw_now
from data_engine import fetch_single_stock_anti_trap

# ==========================================
# 共用工具：純文字去標籤、恐慌分數、債市四大哨兵、SVG 縮放與夜盤格式化
# ==========================================
def _strip_html(s: str) -> str:
    if not s:
        return ""
    txt = re.sub(r"<[^>]+>", "", str(s))
    return txt.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&").replace("&nbsp;", " ").strip()

def _fmt_us_quote_html(q: dict) -> str:
    """台股習慣獨立上色：正規盤與盤後/盤前各自紅漲綠跌"""
    chg = float(q.get("chg_pct", 0.0) or 0.0)
    c_main = "#ff8a80" if chg >= 0 else "#69f0ae"
    ext_v = q.get("ext_chg_pct", None)
    ext_lbl = q.get("ext_label", "")
    ext_html = ""
    if ext_lbl and ext_v is not None:
        c_ext = "#ff8a80" if float(ext_v) >= 0 else "#69f0ae"
        ext_html = f" <span style='color:{c_ext};'>({ext_lbl})</span>"
    return f"<b style='color:{c_main};'>{chg:+.2f}%</b>{ext_html}"

def _compute_retail_fear_index(sb_maint: float, pc_ratio: float, vix_now: float, retail_mtx_oi: int, warrant_hedge_yi: float) -> dict:
    s_maint = float(np.clip((170.0 - sb_maint) / 35.0 * 100.0, 0, 100))
    s_pcr = float(np.clip((120.0 - pc_ratio) / 90.0 * 100.0, 0, 100))
    s_vix = float(np.clip((vix_now - 13.0) / 25.0 * 100.0, 0, 100))
    s_ret_w = float(np.clip(50.0 + (-retail_mtx_oi / 150.0) + (-warrant_hedge_yi / 15.0), 0, 100))
    fear_idx = round(0.35 * s_maint + 0.25 * s_pcr + 0.20 * s_vix + 0.20 * s_ret_w, 1)
    if fear_idx >= 65:
        lbl, col = f"🔥 極度恐慌清洗區 ({fear_idx:.1f}分・醞釀報復強彈)", "#00e676"
    elif fear_idx >= 45:
        lbl, col = f"🟡 偏空警戒震盪區 ({fear_idx:.1f}分・籌碼換手中)", "#fbbf24"
    elif fear_idx >= 28:
        lbl, col = f"☀️ 籌碼健康平衡區 ({fear_idx:.1f}分・適合順勢選股)", "#38bdf8"
    else:
        lbl, col = f"⚠️ 市場過度貪婪區 ({fear_idx:.1f}分・慎防高檔拉回)", "#ff8a80"
    return {"score": fear_idx, "s_maint": s_maint, "s_pcr": s_pcr, "s_vix": s_vix, "s_ret_w": s_ret_w, "label": lbl, "color": col}

def evaluate_bond_4_sentinels(tnx_val: float, fx_now: float, etf_m: dict, bond_nav: dict, fed_cut_signal: bool = False) -> dict:
    """v65.2 新增：債市四大自動監控哨兵（長債偷跑 / 降息換軌 / 匯損警戒 / 折溢價防呆）"""
    p_985 = etf_m.get("00985B", {}).get("p", 10.45)
    p_933 = etf_m.get("00933B", {}).get("p", 15.85)
    p_687 = etf_m.get("00687B", {}).get("p", 28.10)
    nav_985 = (bond_nav or {}).get("00985B", {})
    nav_933 = (bond_nav or {}).get("00933B", {})
    nav_687 = (bond_nav or {}).get("00687B", {})

    # 哨兵 A：^TNX 4.95% ~ 5.15% 且 00933B / 00687B 折價或落入參考買區
    tnx_in_zone = (tnx_val >= 4.95)
    is_933_disc = bool(nav_933.get("ok") and nav_933.get("prem_pct") is not None and nav_933["prem_pct"] <= 0.0)
    is_687_disc = bool(nav_687.get("ok") and nav_687.get("prem_pct") is not None and nav_687["prem_pct"] <= 0.0)
    a_hit = tnx_in_zone and (is_933_disc or is_687_disc or (15.30 <= p_933 <= 15.60) or (27.00 <= p_687 <= 27.60))
    if a_hit:
        a_status = f"🟢 觸發分批建倉！（^TNX={tnx_val:.2f}% 進入 4.95~5.15% 區；00933B={p_933:.2f}[買區15.30~15.60] / 00687B={p_687:.2f}[買區27.00~27.60]）"
    elif tnx_val >= 4.85:
        a_status = f"🟡 逼近偷跑線（^TNX={tnx_val:.2f}%，距 4.95% 僅 {4.95 - tnx_val:+.2f}%；00933B={p_933:.2f} / 00687B={p_687:.2f}）"
    else:
        a_status = f"⚪ 未觸發（^TNX={tnx_val:.2f}% < 4.95%；00933B={p_933:.2f} / 00687B={p_687:.2f}）"

    # 哨兵 B：Fed 降息換軌訊號（側邊欄開關）
    b_status = "🟢 已轉降息循環！新資金由短債轉向長債 (00933B/00687B) 鎖利" if fed_cut_signal else "⚪ 尚未轉向降息（維持 00985B 短債為主、靜待 Fed 點陣圖轉鴿）"

    # 哨兵 C：台幣強升匯損警戒 (<= 31.5)
    if fx_now <= 31.50:
        c_status = f"🚨 觸發匯損警戒！（USD/TWD={fx_now:.3f} <= 31.5 ➔ 美元短債續抱領息，新資金改配台幣防禦股 00713）"
    elif fx_now <= 31.85:
        c_status = f"🟡 逼近警戒線（USD/TWD={fx_now:.3f}，留意 31.5 關卡）"
    else:
        c_status = f"✅ 匯率安全（USD/TWD={fx_now:.3f} > 31.5，無立即匯損壓力）"

    # 哨兵 D：折溢價防呆 (>0.5% 紅燈禁追買)
    d_parts, d_alerts = [], []
    for code_b, p_b, nv in [("00985B", p_985, nav_985), ("00933B", p_933, nav_933), ("00687B", p_687, nav_687)]:
        if nv.get("ok") and nv.get("prem_pct") is not None:
            pr_v = nv["prem_pct"]
            if pr_v > 0.5:
                d_alerts.append(f"{code_b}溢價{pr_v:+.2f}%")
                d_parts.append(f"{code_b}:{p_b:.2f}/淨{nv['nav']:.2f}(🚨{pr_v:+.2f}%)")
            else:
                d_parts.append(f"{code_b}:{p_b:.2f}/淨{nv['nav']:.2f}({pr_v:+.2f}%)")
        else:
            d_parts.append(f"{code_b}:{p_b:.2f}(淨值待查官網)")

    if d_alerts:
        d_status = f"🚨 紅燈禁追買！【{'、'.join(d_alerts)} > 0.5%】（{' ｜ '.join(d_parts)}）"
    else:
        d_status = f"✅ 折溢價正常（{' ｜ '.join(d_parts)}）"

    return {
        "a_hit": a_hit, "a_status": a_status,
        "b_hit": fed_cut_signal, "b_status": b_status,
        "c_hit": (fx_now <= 31.50), "c_status": c_status,
        "d_hit": bool(d_alerts), "d_status": d_status,
        "summary_line": f"哨兵A(長債偷跑):{a_status} | 哨兵B(降息換軌):{b_status} | 哨兵C(匯損):{c_status} | 哨兵D(折溢價):{d_status}"
    }

def build_bond_4_sentinels_html(sentinels: dict) -> str:
    b_col = "#ff4d4f" if (sentinels["c_hit"] or sentinels["d_hit"]) else ("#00e676" if (sentinels["a_hit"] or sentinels["b_hit"]) else "#38bdf8")
    return clean_html(f"""
    <div style="background:#0f172a; border:2px solid {b_col}; border-left:6px solid {b_col}; border-radius:11px; padding:12px 16px; margin-bottom:14px; color:#f8fafc; font-size:14px; line-height:1.7;">
    <div style="font-size:16px; font-weight:900; color:#ffd54f; margin-bottom:6px;">🔔 【水庫三：債市四大自動監控哨兵】（短債 00985B ＋ 長債預設 00933B ／ 00687B）</div>
    • <b>哨兵 A（長債偷跑點 ^TNX 4.95~5.15%）</b>：{sentinels['a_status']}<br>
    • <b>哨兵 B（Fed 降息換軌訊號）</b>：{sentinels['b_status']}<br>
    • <b>哨兵 C（台幣匯損警戒 <= 31.5）</b>：{sentinels['c_status']}<br>
    • <b>哨兵 D（折溢價防呆 > 0.5% 禁追）</b>：{sentinels['d_status']}
    </div>
    """)

def _format_sector_struct_line(sec: dict, as_html: bool = True) -> str:
    if not sec:
        return ""
    parts = []
    if sec.get("otc_chg") is not None:
        v = sec["otc_chg"]
        div_s = f" [背離差{sec['otc_div']:+.2f}%]" if sec.get("otc_div") is not None else ""
        parts.append(f"櫃買OTC <b style='color:{'#ff8a80' if v>=0 else '#69f0ae'};'>{v:+.2f}%</b>{div_s}" if as_html else f"櫃買OTC {v:+.2f}%{div_s}")
    for k, nm in [("elec_chg", "電子"), ("semi_chg", "半導體"), ("fin_chg", "金融"), ("non_ef_chg", "非金電")]:
        if sec.get(k) is not None:
            v = sec[k]
            parts.append(f"{nm} <b style='color:{'#ff8a80' if v>=0 else '#69f0ae'};'>{v:+.2f}%</b>" if as_html else f"{nm} {v:+.2f}%")
    if not parts:
        return ""
    tag = sec.get("tag", "")
    return f"• <b>類股結構</b>：{tag}（{' ｜ '.join(parts)}）<br>" if as_html else f"{tag}（{' / '.join(parts)}）"

def _wrap_svg_zoom(svg_xml: str, zoom_mode: str = "auto") -> str:
    b64_svg = base64.b64encode(svg_xml.encode("utf-8")).decode("utf-8")
    if zoom_mode == "1.5x":
        img_style, hint, cls_img = "width:150%; min-width:1100px; height:auto; display:block;", "🔍 目前為 1.5x 放大模式（左右滑動檢視細節）", "svg-fixed-img"
    elif zoom_mode == "2.0x":
        img_style, hint, cls_img = "width:200%; min-width:1500px; height:auto; display:block;", "🔍 目前為 2.0x 超大模式（左右滑動檢視細節）", "svg-fixed-img"
    else:
        img_style, hint, cls_img = "width:100%; height:auto; display:block;", "📱 手機版可單指左右滑動看大圖，或點上方倍率鍵放大", "svg-auto-img"
    return (
        f'<div class="svg-zoom-hint" style="font-size:12.5px; color:#93c5fd; margin:2px 0 5px 2px;">{hint}</div>'
        f'<div class="svg-zoom-box"><img class="{cls_img}" src="data:image/svg+xml;base64,{b64_svg}" style="{img_style}" /></div>'
    )

def _format_night_tx_segment(deriv: dict, now_tw_dt, nq_chg_override=None, as_html: bool = True) -> str:
    nq_v = nq_chg_override if nq_chg_override is not None else deriv.get("night_nq_chg")
    win = deriv.get("night_window", "DAY_REFLECTED")
    is_live = bool(deriv.get("night_is_live_mis", False))
    is_valid = bool(deriv.get("night_valid", False) and deriv.get("night_tx_close", 0) > 5000)
    nc, n_chg, n_pct = deriv.get("night_tx_close", 0.0), deriv.get("night_tx_chg", 0.0), deriv.get("night_tx_chg_pct", 0.0)
    tm_s, src_s = deriv.get("night_tx_time", ""), deriv.get("night_src", "")
    n_col = "#ff8a80" if n_chg >= 0 else "#69f0ae"
    nq_col = "#ff8a80" if (nq_v is not None and nq_v >= 0) else "#69f0ae"

    if win == "NIGHT_LIVE":
        if is_valid and is_live:
            return (f"🌙 <b>台指期夜盤即時</b>：<b style='color:{n_col};'>{nc:,.0f} ({n_chg:+,.0f}點 / {n_pct:+.2f}%)</b> <span style='color:#94a3b8; font-size:12px;'>[{src_s} {tm_s}]</span>"
                    if as_html else f"台指期夜盤即時={nc:,.0f}({n_chg:+,.0f}/{n_pct:+.2f}%・{src_s} {tm_s})")
        nq_txt = (f"⚡ <b>夜盤交易中（雲端即時受限・NQ代理 <b style='color:{nq_col};'>{nq_v:+.2f}%</b>）</b> <span style='color:#94a3b8; font-size:12px;'>[可於側邊欄手動輸入夜盤點數]</span>"
                  if as_html else f"夜盤交易中(雲端即時受限・NQ代理={nq_v:+.2f}%)") if nq_v is not None else ("⚡ 夜盤交易中（雲端即時受限）" if as_html else "夜盤交易中(雲端受限)")
        return nq_txt

    if win in ["PRE_MARKET", "WEEKEND"] and is_valid:
        lbl = "今晨夜盤結算" if win == "PRE_MARKET" else "週六夜盤最終結算"
        return (f"🌙 <b>{lbl}</b>：<b style='color:{n_col};'>{nc:,.0f} ({n_chg:+,.0f}點 / {n_pct:+.2f}%)</b> <span style='color:#94a3b8; font-size:12px;'>[{src_s} {tm_s}]</span>"
                if as_html else f"{lbl}={nc:,.0f}({n_chg:+,.0f}/{n_pct:+.2f}%・{tm_s})")

    if win == "DAY_REFLECTED" and is_valid:
        return (f"<span style='color:#94a3b8;'>🌙 今晨夜盤已由日盤反映 ({tm_s})：{nc:,.0f} ({n_chg:+,.0f} / {n_pct:+.2f}%)</span>"
                if as_html else f"今晨夜盤已反映({tm_s}):{nc:,.0f}({n_chg:+,.0f}/{n_pct:+.2f}%)")

    if nq_v is not None:
        return (f"⚡ <b>電子盤代理(那指期NQ)</b>：<b style='color:{nq_col};'>{nq_v:+.2f}%</b>"
                if as_html else f"NQ那指期={nq_v:+.2f}%")
    return ""

# ==========================================
# 0. 盤中模組 9 動態時間軸提示條
# ==========================================
def build_intraday_timeline_banner_html(now_tw_dt, master_state: str, deriv: dict, next_ev: dict, us_data: dict = None) -> str:
    wd, t_min = now_tw_dt.weekday(), now_tw_dt.hour * 60 + now_tw_dt.minute
    is_red = (master_state == "RED_LOCKED")
    msci_today = bool(next_ev and next_ev.get("_days") == 0 and ("MSCI" in str(next_ev.get("重大風險事件", ""))))

    night_seg = _format_night_tx_segment(deriv, now_tw_dt, nq_chg_override=(us_data.get("nq_chg") if us_data else None), as_html=True)
    night_str = f" ｜ {night_seg}" if night_seg else ""
    hol = (us_data or {}).get("holiday_info", {})
    hol_line = f"<br>• <b style='color:#fde047;'>{hol['banner_note']}</b>" if hol.get("banner_note") else ""

    if wd >= 5:
        slot_lbl, tip, b_col, bg = "🛋️ 週末休市復盤時段", "專注檢查四水庫比例、債市哨兵與下週重大風險事件，規劃下週開盤劇本。", "#38bdf8", "#0f172a"
    elif t_min < 510:
        slot_lbl, tip, b_col, bg = "🌅 盤前暖身（08:30前）", "檢視美股電子盤與今晨 05:00 夜盤結算，預先確認停利/停損點位與當日事件。", "#38bdf8", "#0f172a"
    elif t_min < 540:
        slot_lbl, tip, b_col, bg = "⏳ 08:30-09:00 試搓無視期", "試搓假掛單一律無視！以『美股費半/ADR ＋ 今晨夜盤結算』推演 09:00 開盤跳空，切勿盤前掛市價單。", "#fbbf24", "#2e2409"
    elif t_min <= 555:
        if is_red:
            slot_lbl, tip, b_col, bg = "🚨 09:00-09:15 紅燈恐慌避開區", "開盤首 15 分鐘為斷頭賣壓集中區！減碼弱勢股或比價換股請避開此低點（瀕臨跌停除外），改 09:15 後穩定執行。", "#ff4d4f", "#3b0f14"
        else:
            slot_lbl, tip, b_col, bg = "🔔 09:00-09:15 開盤定型期", "觀察跳空缺口是否迅速回補與開盤量能方向，不急於首 5 分鐘追高殺低。", "#fbbf24", "#2e2409"
    elif t_min < 600:
        slot_lbl, tip, b_col, bg = "📊 09:15-10:00 流動性穩定窗口", "盤面價差收斂、真買賣盤現形，適合執行分批減碼、股轉債或守線檢核。", "#00e676", "#0d231d"
    elif t_min < 660:
        slot_lbl, tip, b_col, bg = "🌏 10:00-11:00 亞股連動檢驗期", "日股/韓股/港股方向定調，檢視早盤強勢股是否能守住開盤價與均價線。", "#38bdf8", "#0f172a"
    elif t_min < 700:
        slot_lbl, tip, b_col, bg = "⚡ 11:00-11:30 主力偷襲表態期", "中場常有大戶點火或隔日沖出貨，留意量比＞2.5x 伴隨高當沖之假突破誘多。", "#fbbf24", "#2e2409"
    elif t_min < 780:
        slot_lbl, tip, b_col, bg = "⚖️ 11:30-13:00 午盤換手觀察期", "檢視預估量能與收盤位階，對照城堡防線決定尾盤去留。", "#38bdf8", "#0f172a"
    elif t_min <= 810:
        if msci_today:
            slot_lbl, tip, b_col, bg = "🚨 13:00-13:30 MSCI 生效尾盤甩尾警戒", "13:25-13:30 尾盤將出現被動資金爆量甩尾！嚴禁追高新增股，提防剔除股瞬間下殺。", "#ff4d4f", "#3b0f14"
        else:
            slot_lbl, tip, b_col, bg = "🏁 13:00-13:30 尾盤定調決策期", "高波動股（ATR%＞4.5%）與短單防線一律以收盤前 15 分鐘確認為準，防盤中假跌破洗盤。", "#fbbf24", "#2e2409"
    else:
        slot_lbl, tip, b_col, bg = "🌙 盤後／夜盤籌碼追蹤期", "13:30期權 ➔ 16:00法人 ➔ 21:00融資；15:00 後為台指期即時夜盤時段，同步追蹤美股與 NQ/ES。", "#38bdf8", "#0f172a"

    return clean_html(f"""
    <div style="background:{bg}; border:1.5px solid {b_col}; border-left:6px solid {b_col}; border-radius:10px; padding:9px 15px; margin-bottom:12px; color:#f8fafc; font-size:14px; line-height:1.6;">
    <b>⏱️ 模組 9 時間軸【{slot_lbl}】</b>{night_str}<br>
    • {tip}{hol_line}
    </div>
    """)

# ==========================================
# 1. 模組 0：三階燈號 × 四軌落底 × 盤中廣度防呆
# ==========================================
def evaluate_crash_and_bottom_radar_v244(
    twii_now, twii_chg_pct, twii_ma60, twii_ma120, twii_ma240, twii_30d_dd_pct, sp500_below_ma200,
    vix_now, sox_dd_pct, fini_oi, margin_dd_pct, margin_2d_chg_yi, margin_2d_pct, maint_pct,
    bottom_4track: dict, limit_up_cnt: int, adv_ratio_pct: float,
    basis_pts, basis_valid: bool, basis_stabilized: bool, consec_cover_days, cover_3d_sum,
    fx_stopped_dep, limit_down_cnt, had_limit_down_100,
    ban_active, days_since_ban, consec_above_ma10,
    breadth_is_stale: bool = False
) -> dict:
    fini_tag = f"{fini_oi:+,} 口 【🚨極限對沖】" if fini_oi < -80000 else f"{fini_oi:+,} 口"
    w_items = [
        (twii_30d_dd_pct >= 12.0, f"① 30日急殺跌速 ≧ 12%（目前回檔 <b>-{twii_30d_dd_pct:.1f}%</b>）"),
        ((twii_now < twii_ma240) or sp500_below_ma200, f"② 長線生命線失守（加權 {'<' if twii_now < twii_ma240 else '≧'} 年線{twii_ma240:,.0f} ｜ 標普 {'< 200MA' if sp500_below_ma200 else '≧ 200MA'}）"),
        ((vix_now >= 30.0) or (sox_dd_pct >= 25.0), f"③ 美股恐慌爆表（VIX <b>{vix_now:.2f}</b> ｜ 費半波段跌幅 <b>-{sox_dd_pct:.1f}%</b>）"),
        (fini_oi < -60000, f"④ 外資期貨重空 &gt; 6萬口（目前淨空單 <b>{fini_tag}</b>）"),
        (margin_dd_pct < twii_30d_dd_pct, f"⑤ 融資減幅尚未追上大盤跌幅（融資減 <b>-{margin_dd_pct:.1f}%</b> vs 大盤跌 <b>-{twii_30d_dd_pct:.1f}%</b>）")
    ]
    w_count = sum(1 for flag, _ in w_items if flag)
    crash_warning_on = (w_count >= 3)

    basis_rate_pct = round((abs(basis_pts) / max(1.0, twii_now)) * 100.0, 2) if (basis_valid and basis_pts < 0) else 0.0
    b1_hits = []
    if margin_dd_pct >= twii_30d_dd_pct: b1_hits.append(f"30日融資減-{margin_dd_pct:.1f}% ≧ 大盤-{twii_30d_dd_pct:.1f}%✅")
    if margin_2d_chg_yi <= -500.0 or margin_2d_pct <= -7.0: b1_hits.append(f"近2日急減{margin_2d_chg_yi:+.1f}億/{margin_2d_pct:+.1f}%✅")
    if maint_pct <= 150.0: b1_hits.append(f"維持率{maint_pct:.1f}%≦150%✅")
    b1_ok = len(b1_hits) > 0
    b1_reason = " ｜ ".join(b1_hits) if b1_ok else f"近2日 {margin_2d_chg_yi:+.1f}億/{margin_2d_pct:+.1f}% ｜ 維持率 {maint_pct:.1f}% ｜ 30日減-{margin_dd_pct:.1f}%"

    hit_2c_override = (not breadth_is_stale) and (twii_chg_pct >= 4.0) and ((limit_up_cnt >= 100) or (adv_ratio_pct >= 85.0))
    b2_ok = bool(bottom_4track.get("passed", False) or hit_2c_override)
    b2_detail = bottom_4track.get("detail", "")
    if hit_2c_override and not bottom_4track.get("passed", False):
        b2_detail = f"2c無量鎖漲停/廣度豁免(單日+{twii_chg_pct:.1f}%・漲停{limit_up_cnt}家/上漲比{adv_ratio_pct:.0f}%)"
    precursor_txt = bottom_4track.get("precursor", "")

    b3_ok = bool(basis_valid and (basis_pts < 0) and (basis_rate_pct >= 0.8) and basis_stabilized)
    if not basis_valid: b3_desc = "期貨跨日待盤後同步（暫不計分，可於側邊欄覆寫）"
    elif basis_pts >= 0: b3_desc = f"轉正價差 <b>{basis_pts:+.0f}點</b>（無恐慌逆價差）"
    else: b3_desc = f"逆價差 <b>{basis_pts:+.0f}點 / {basis_rate_pct:.2f}%</b> ｜ {'已止穩不擴大✅' if basis_stabilized else '未確認止穩'}"

    b4_ok = (consec_cover_days >= 3) and (cover_3d_sum >= 10000) and fx_stopped_dep
    if breadth_is_stale:
        b5_ok, b5_desc = False, f"前日收盤跌停 <b>{limit_down_cnt} 家</b>（盤中待 14:30 結算今日家數）"
    else:
        b5_ok = bool(had_limit_down_100 and (limit_down_cnt < 20))
        b5_desc = f"當日跌停 <b>{limit_down_cnt} 家</b> {'< 20家達標✅' if b5_ok else '尚未收斂至<20家'}"

    b_items = [
        (b1_ok, f"① 融資籌碼清洗達標（<b>{b1_reason}</b> ｜ 維持率 {maint_pct:.1f}%）"),
        (b2_ok, f"② 四軌價量／廣度反轉（<b>{b2_detail}</b>）"),
        (b3_ok, f"③ 恐慌逆價差率 ≧ 0.8% 收斂（{b3_desc}）"),
        (b4_ok, f"④ 外資空單連3日回補&gt;1萬口(ΔOI)＋台幣止貶（連補 <b>{consec_cover_days}日 / {cover_3d_sum:+,}口</b> ｜ 台幣{'已止貶✅' if fx_stopped_dep else '續貶⚠️'}）"),
        (b5_ok, f"⑤ 恐慌廣度收斂（{b5_desc}）")
    ]
    b_count = sum(1 for flag, _ in b_items if flag)
    bottom_confirmed = (b_count >= 4)
    is_policy_escape_wave = bool(ban_active and (0 <= days_since_ban <= 14) and (not bottom_confirmed) and (consec_above_ma10 < 3))

    if crash_warning_on and (not bottom_confirmed):
        master_state, lamp_short = "RED_LOCKED", "🔴紅燈鎖死"
        master_badge = f"🔴 紅燈未落底【總開關鎖死】（預警 {w_count}/5・落底 {b_count}/5）"
        master_action = "嚴禁動用新現金追買或開槓桿！僅核准三項豁免：(a) 股轉債轉入00985B/00933B/00713；(b) 比價折價≧10~15%換龍頭；(c) 淨降倉換強新穀倉1龍頭(獨立分帳先守平衡價，站上5MA後守5MA)。"
    elif crash_warning_on and bottom_confirmed:
        master_state, lamp_short = "BOTTOM_UNLOCKED", "🟢落底解鎖"
        master_badge = f"🟢 落底確認【總開關解鎖】（預警 {w_count}/5 ＆ 落底共振 {b_count}/5 達標！）"
        master_action = "正式解封新資金！開放水庫四/機動現金分 3 批加碼新穀倉1龍頭權值股作傳家寶存股，同步啟動 MSCI 主流觀察池！"
    elif (twii_now < twii_ma60) or (twii_now < twii_ma120):
        master_state, lamp_short = "YELLOW", "🟡黃燈警戒"
        master_badge = f"🟡 黃燈前哨警戒（觸及季線/半年線・預警 {w_count}/5）"
        master_action = "拉高審核門檻禁止追高！優先收網多頭股息衛星股轉現或轉債，僅限防彈衣／新穀倉1且無3日事件雷者小額試單。"
    else:
        master_state, lamp_short = "GREEN", "🟢綠燈常態"
        master_badge = f"🟢 綠燈多頭常態（站穩季半年線・預警 {w_count}/5）"
        master_action = "正常審核 App 個股訊號，啟動多頭股息衛星循環（嚴守破 10/20MA 或硬停損 -7%~-10% 無條件撤退）。"

    return {
        "w_items": w_items, "w_count": w_count, "crash_warning_on": crash_warning_on,
        "b_items": b_items, "b_count": b_count, "bottom_confirmed": bottom_confirmed,
        "b2_ok": b2_ok, "b2_detail": b2_detail, "precursor_txt": precursor_txt,
        "basis_rate_pct": basis_rate_pct, "is_policy_escape_wave": is_policy_escape_wave,
        "master_state": master_state, "lamp_short": lamp_short,
        "master_badge": master_badge, "master_action": master_action
    }

# ==========================================
# 1B. v65.2：【📋 當日戰情總結 ＆ 未來 3 日推演卡】大盤原因導向操盤口令 ＋ 動態移除 00400A
# ==========================================
def build_daily_summary_and_3day_card_html(
    fleet: dict, radar: dict, deriv: dict, us_data: dict, forecast: dict, next_ev: dict,
    p_3711: float, ma5_3711: float, sb_short_3711_be: float, sb_shares_3711_short: int, etf_m: dict,
    lots_00400a: float = 0.0, lots_00985b: float = 25.0, twd_cash_wan: float = 50.64,
    fish_price_3711: float = 450.0, target_00400a_low: float = None, short_3711_avg: float = 474.0
) -> str:
    now_tw = get_tw_now()
    t_min = now_tw.hour * 60 + now_tw.minute
    is_post_close = (now_tw.weekday() >= 5) or (t_min >= 810)
    is_intraday = (now_tw.weekday() < 5) and (540 <= t_min < 810)

    tw, chg_tw = fleet["twii_now"], fleet["twii_chg_pct"]
    ma5, ma10, ma20, ma240 = fleet["ma5"], fleet["ma10"], fleet["ma20"], fleet["ma240"]
    nq_chg, es_chg = us_data.get("nq_chg", 0.0), us_data.get("es_chg", 0.0)
    hol = us_data.get("holiday_info", {})
    pos_240 = f"守住年線 {ma240:,.0f}" if tw >= ma240 else f"失守年線 {ma240:,.0f}"
    pos_5 = f"穩守日5MA ({ma5:,.0f})" if tw >= ma5 else f"失守日5MA ({ma5:,.0f})"
    sec_tag = fleet.get("sector_struct", {}).get("tag", "")
    night_seg = _format_night_tx_segment(deriv, now_tw, nq_chg_override=nq_chg, as_html=True)

    # 區塊 1：盤勢定調與前瞻
    if is_post_close:
        sub_t = "盤後覆盤 ＆ 明日作戰前瞻"
        c1_concl = f"{'🟢 強彈修復' if chg_tw>=1.5 else ('🔴 重挫測撐' if chg_tw<=-1.5 else '🟡 區間換手')}（{pos_240}）"
        c1_lines = [
            f"• <b>收盤位階</b>：{tw:,.0f} ({chg_tw:+.2f}%)，{pos_5}｜{sec_tag}",
            f"• <b>籌碼現況</b>：外資現貨 {deriv['spot_fini_yi']:+.1f}億、期貨空單 {deriv['fini_tx_oi']:+,}口" if deriv.get("spot_valid") else f"• <b>籌碼現況</b>：期貨空單 {deriv['fini_tx_oi']:+,}口",
            f"• <b>明日指引</b>：<b style='color:#fde047;'>🇺🇸 美股因【{hol['holiday_name']}】休市</b>，留意內資主導量縮震盪" if hol.get("is_holiday") else f"• <b>夜盤前瞻</b>：{night_seg or f'NQ {nq_chg:+.2f}% / ES {es_chg:+.2f}%'}"
        ]
    elif is_intraday:
        sub_t = "盤中即時控盤 ＆ 10 秒決策"
        c1_concl = f"{'🟢 盤中強彈攻堅' if chg_tw>=1.5 else ('🔴 盤中急殺測撐' if chg_tw<=-1.5 else '🟡 盤中多空拉鋸')}（{pos_240}）"
        c1_lines = [
            f"• <b>即時點位</b>：{tw:,.0f} ({chg_tw:+.2f}%)，{pos_5}｜上壓 10MA({ma10:,.0f})",
            f"• <b>結構觀察</b>：{sec_tag or '追蹤核心權值與中小型同步度'}",
            "• <b>盤中紀律</b>：紅燈反彈嚴禁追高，高波動股以 13:15 後收盤確認"
        ]
    else:
        sub_t = "盤前跳空預警 ＆ 今日劇本"
        c1_concl = f"🇺🇸 美股【{hol['holiday_name']}】休市・內資主導" if hol.get("is_holiday") else f"{'🟢 盤前偏多測壓' if nq_chg>=0.8 else ('🔴 盤前偏空測撐' if nq_chg<=-0.8 else '🟡 盤前震盪開盤')}"
        c1_lines = [
            f"• <b>昨日基準</b>：{tw:,.0f} ({chg_tw:+.2f}%)，{pos_240}",
            f"• <b>夜盤／電子盤</b>：{night_seg or f'NQ {nq_chg:+.2f}% / ES {es_chg:+.2f}%'}",
            "• <b>開盤守則</b>：08:30-09:00 試搓無視，紅燈減碼避開 09:00-09:15 恐慌低點"
        ]

    # 區塊 2：未來 3 日方向預判
    probs = forecast["probs"]
    k60_lbl = f"60分J={fleet['k60_j']:.0f} 近過熱防震盪" if fleet["k60_j"] >= 90 else (f"60分J={fleet['k60_j']:.0f} 下彎尋底" if fleet["k60_j"] < fleet["k60_j_prev"] else f"60分J={fleet['k60_j']:.0f} 換手走揚")
    ev_txt = f"<b>{next_ev['日期']} {next_ev['_short']}</b>（尚餘 {next_ev['_days']} 天）" if next_ev else "近期無重大事件"
    if radar["master_state"] == "RED_LOCKED":
        c2_concl = f"🟡 先震盪測壓、反覆築底（主路徑 {probs['base']}%／保守 {probs['bear']}%）"
    elif radar["master_state"] == "YELLOW":
        c2_concl = f"🟡 季半年線攻防震盪（主路徑 {probs['base']}%）"
    else:
        c2_concl = f"🟢 偏多沿均線推進（基準 {probs['base']}%／樂觀 {probs['bull']}%）"
    c2_lines = [
        f"• <b>短線箱型</b>：大概率在 <b>日5MA ({ma5:,.0f}) ～ 月線 ({ma20:,.0f})</b> 間洗刷",
        f"• <b>動能驗證</b>：{k60_lbl}；連站 10MA 僅 {fleet['twii_consec_above_ma10']}/3 日，未脫逃命波",
        f"• <b>風險倒數</b>：{ev_txt}（事件前 3 日嚴禁追高與槓桿）"
    ]

    # 區塊 3：v65.2 重構「今日實戰操作口令」（先講大盤原因 ➔ 再下操盤方向，並動態移除已清零 00400A）
    t1_400 = target_00400a_low if (target_00400a_low is not None and target_00400a_low > 0) else etf_m["00400A"]["exit_t1"]
    t2_400 = max(t1_400 + 0.30, etf_m["00400A"]["exit_t2"])
    if radar["master_state"] == "RED_LOCKED":
        c3_concl = f"因大盤仍處紅燈未落底 ({radar['b_count']}/5) 且外資空單重壓 ➔ 建議『不追高、保留現金、守既有短單防線』"
    elif radar["master_state"] == "BOTTOM_UNLOCKED":
        c3_concl = f"因落底共振 {radar['b_count']}/5 達標解鎖 ➔ 建議『分 3 批動用現金低接新穀倉 1 龍頭』"
    elif radar["master_state"] == "YELLOW":
        c3_concl = "因大盤觸及季半年線前哨警戒 ➔ 建議『禁追高、優先收網衛星股轉現或轉債』"
    else:
        c3_concl = "因大盤站穩季半年線維持多頭 ➔ 建議『順勢操作高毛利強股，嚴守破線紀律』"

    if sb_shares_3711_short > 0:
        if p_3711 < sb_short_3711_be:
            s_cmd = f"• <b>🚨 日月光短單 ({sb_shares_3711_short}股 @{short_3711_avg:.0f})</b>：現價 {p_3711:.1f} <b style='color:#ff8a80;'>跌破平衡價 {sb_short_3711_be:.2f}，嚴守紀律撤退！</b>"
        elif p_3711 < ma5_3711:
            s_cmd = f"• <b>⚠️ 日月光短單 ({sb_shares_3711_short}股 @{short_3711_avg:.0f})</b>：現價 {p_3711:.1f} 破 5MA({ma5_3711:.1f})，核對停利或守死平衡價 {sb_short_3711_be:.2f}"
        else:
            s_cmd = f"• <b>⚡ 日月光短單 ({sb_shares_3711_short}股 @{short_3711_avg:.0f})</b>：現價 <b style='color:#00e676;'>{p_3711:.1f}</b> 沿 5MA({ma5_3711:.1f}) 移動停利（<b style='color:#fde047;'>⛔禁追500+</b>・釣魚鎖定 {fish_price_3711:.0f}元）"
    else:
        s_cmd = f"• <b>🎣 低接釣魚紀律</b>：嚴禁 500+ 追高日月光，水庫四現金鎖定 <b>{fish_price_3711:.0f} 元</b> 區間"

    c3_lines = [
        f"• <b>💧 機動現金 ({twd_cash_wan:.2f}萬)</b>：{'鎖定不動（單筆預留買足 2 整張預算 ≦ ' + f'{twd_cash_wan*10000/2000:.0f}' + '元），等利空落地或落底 4/5' if radar['master_state']=='RED_LOCKED' else '分批布局高毛利新穀倉1（優先買足 2 整張）'}",
        s_cmd
    ]
    if lots_00400a > 0:
        c3_lines.append(f"• <b>🔄 00400A 收尾 ({lots_00400a:.0f}張)</b>：逢彈 <b>{t1_400:.2f}～{t2_400:.2f} 元</b> 出清轉入 00985B (現 {lots_00985b:.0f}張)")
    else:
        c3_lines.append(f"• <b>🛡️ 水庫三債息防線</b>：00985B 續抱 {lots_00985b:.0f} 張領息，盯緊 ^TNX 4.95% 長債 (00933B) 偷跑點")

    # 區塊 4：關鍵轉折監看指標
    sc = deriv.get("spot_fini_consec", 0)
    sc_sum = deriv.get("spot_fini_consec_sum", deriv.get("spot_fini_5d_sum", 0.0))
    sc_lbl = f"連買{sc}日({sc_sum:+.0f}億)" if sc > 0 else (f"連賣{abs(sc)}日({sc_sum:+.0f}億)" if sc < 0 else "持平")
    c4_concl = f"⚖️ 站回年線 ({ma240:,.0f}) ＆ 連 3 日站穩 10MA ({ma10:,.0f}) 始脫逃命波"
    spec_c4 = f"前五 {deriv['top5_spec_net']:+,} / 前十 {deriv['top10_spec_net']:+,} 口（破解ETF避險干擾）" if deriv.get("top_spec_valid") else "持續追蹤期貨實質淨額與台幣動向"
    c4_lines = [
        f"• <b>防守／轉強線</b>：守穩日 5MA <b>{ma5:,.0f}</b>，上攻須帶量收復年線 <b>{ma240:,.0f}</b>",
        f"• <b>三共振解鎖鍵</b>：外資現貨轉買（目前 {sc_lbl}）＋ 期貨 ΔOI 連 3 日補萬口 ＋ 台幣止貶",
        f"• <b>特定法人對照</b>：{spec_c4}"
    ]

    b_col = "#ff4d4f" if radar["master_state"] == "RED_LOCKED" else ("#fbbf24" if radar["master_state"] == "YELLOW" else "#00e676")
    def _box(title, concl, lines, accent):
        return (
            f"<div style='background:rgba(15,23,42,0.82); border:1px solid rgba(255,255,255,0.12); border-left:4px solid {accent}; border-radius:9px; padding:10px 13px;'>"
            f"<div style='font-size:14.2px; font-weight:900; color:#93c5fd; margin-bottom:4px;'>{title}</div>"
            f"<div style='font-size:14.4px; font-weight:900; color:#ffd54f; background:rgba(0,0,0,0.28); padding:4px 8px; border-radius:6px; margin-bottom:6px;'>結論：{concl}</div>"
            f"<div style='font-size:13.8px; color:#e2e8f0; line-height:1.62;'>{'<br>'.join(lines)}</div></div>"
        )

    return clean_html(f"""
    <div style="background: linear-gradient(135deg, #0d1b2a 0%, #1b263b 100%); border: 2px solid {b_col}; border-left: 7px solid {b_col}; border-radius: 13px; padding: 14px 16px; margin-bottom: 15px; color: #f8fafc; box-shadow: 0 4px 16px rgba(0,0,0,0.28);">
    <div style="font-size:17.5px; font-weight:900; color:#ffd54f; border-bottom:1px solid rgba(255,213,79,0.28); padding-bottom:6px; margin-bottom:10px;">
    📋 【當日戰情總結 ＆ 未來 3 日多空推演】（{sub_t}・先結論後說明）
    </div>
    <div class="radar-grid-2" style="margin:0;">
    {_box("🧭 一、盤勢定調與前瞻", c1_concl, c1_lines, "#38bdf8")}
    {_box("🔭 二、未來 3 日方向預判", c2_concl, c2_lines, "#fbbf24")}
    {_box("🎯 三、今日實戰操作口令", c3_concl, c3_lines, "#00e676")}
    {_box("⚡ 四、關鍵轉折監看指標", c4_concl, c4_lines, "#ec4899")}
    </div>
    </div>
    """)

# ==========================================
# 2. 滾動風險事件視覺化水平時間軸 SVG 圖
# ==========================================
def build_event_timeline_svg_html(upcoming_rows: list[dict], zoom_mode: str = "auto") -> str:
    evs = upcoming_rows[:6]
    if not evs: return ""
    n = len(evs)
    xs = [105 + i * int(990 / max(1, n - 1)) for i in range(n)] if n > 1 else [600]
    nodes_svg = []
    for i, ev in enumerate(evs):
        cx, d_left = xs[i], ev["_days"]
        col = "#ff4d4f" if d_left <= 3 else ("#facc15" if d_left <= 7 else "#38bdf8")
        bg_box = "#2d1216" if d_left <= 3 else ("#2e2409" if d_left <= 7 else "#122136")
        d_badge = "今日登場" if d_left == 0 else f"倒數 {d_left} 天"
        short_lbl, date_mmdd = xml_esc(ev["_short"][:11]), xml_esc(ev["日期"][5:])
        box_y, line_y1, line_y2 = (14, 78, 104) if i % 2 == 0 else (136, 118, 136)
        nodes_svg.append(
            f'<line x1="{cx}" y1="{line_y1}" x2="{cx}" y2="{line_y2}" stroke="{col}" stroke-width="2" stroke-dasharray="3,2"/>'
            f'<circle cx="{cx}" cy="111" r="9" fill="{col}" stroke="#0f172a" stroke-width="3"/>'
            f'<rect x="{cx - 90}" y="{box_y}" width="180" height="64" rx="8" fill="{bg_box}" stroke="{col}" stroke-width="1.8"/>'
            f'<text x="{cx}" y="{box_y + 22}" fill="{col}" font-family="sans-serif" font-size="13.5" font-weight="900" text-anchor="middle">{date_mmdd} ｜ {d_badge}</text>'
            f'<text x="{cx}" y="{box_y + 47}" fill="#f8fafc" font-family="sans-serif" font-size="14.5" font-weight="800" text-anchor="middle">{short_lbl}</text>'
        )
    svg_xml = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 214" width="100%" height="auto">
<rect width="1200" height="214" rx="10" fill="#0b1220" stroke="#334155" stroke-width="1.5"/>
<line x1="55" y1="111" x2="1145" y2="111" stroke="#475569" stroke-width="4" stroke-linecap="round"/>
<polygon points="1145,104 1163,111 1145,118" fill="#64748b"/>
{''.join(nodes_svg)}
</svg>"""
    return _wrap_svg_zoom(svg_xml, zoom_mode)

# ==========================================
# 3. 走勢圖（頂部膠囊 0% 遮擋線條 ＋ 左側均線標籤防碰撞錯開）
# ==========================================
def build_realtime_forecast_svg_raw(fleet: dict, radar: dict, forecast: dict) -> str:
    hist_c = fleet.get("hist_closes", [])
    hist_d = fleet.get("hist_dates", [])
    tw_now = float(fleet["twii_now"])
    if len(hist_c) < 5:
        hist_c = [tw_now * 1.12, tw_now * 1.05, tw_now * 0.96, tw_now * 0.98, tw_now]
        hist_d = ["D-20", "D-15", "D-10", "D-5", "今日"]

    pb, pu, pd_p = forecast["paths"]["base"], forecast["paths"]["bull"], forecast["paths"]["bear"]
    probs = forecast["probs"]

    all_vals = list(hist_c) + pb + pu + pd_p + [fleet["ma20"], fleet["ma240"], fleet["m_ma20"]]
    y_max_raw, y_min_raw = max(all_vals), min(all_vals)
    pad = max(650.0, (y_max_raw - y_min_raw) * 0.14)
    y_hi, y_lo = y_max_raw + pad, max(1000.0, y_min_raw - pad)

    x_left, x_today, x_right = 68, 560, 995
    y_top, y_bot = 74, 376

    def to_y(val: float) -> int:
        ratio = (val - y_lo) / max(1.0, (y_hi - y_lo))
        return int(round(y_bot - ratio * (y_bot - y_top)))

    grid_svg = []
    for k in range(5):
        gv = y_lo + (y_hi - y_lo) * (k / 4.0)
        gy = to_y(gv)
        grid_svg.append(f'<line x1="{x_left}" y1="{gy}" x2="{x_right}" y2="{gy}" stroke="#1e293b" stroke-width="1" stroke-dasharray="4,4"/>')
        grid_svg.append(f'<text x="{x_left - 8}" y="{gy + 4}" fill="#94a3b8" font-family="sans-serif" font-size="12" text-anchor="end">{gv:,.0f}</text>')

    ref_raw = [
        (f"月線20MA {fleet['ma20']:,.0f}", fleet["ma20"], "#f59e0b"),
        (f"年線240MA {fleet['ma240']:,.0f}", fleet["ma240"], "#a855f7"),
        (f"月20MA鐵板 {fleet['m_ma20']:,.0f}", fleet["m_ma20"], "#00e676")
    ]
    ref_sorted = sorted([(lbl, rval, to_y(rval), rcol) for lbl, rval, rcol in ref_raw if y_lo <= rval <= y_hi], key=lambda t: t[2])
    ref_svg, prev_ref_y = [], y_top - 12
    for lbl, rval, ry, rcol in ref_sorted:
        ly_txt = min(y_bot - 6, max(ry - 5, prev_ref_y + 18))
        prev_ref_y = ly_txt
        ref_svg.append(f'<line x1="{x_left}" y1="{ry}" x2="{x_right}" y2="{ry}" stroke="{rcol}" stroke-width="1.3" stroke-opacity="0.72" stroke-dasharray="6,4"/>')
        ref_svg.append(f'<text x="{x_left + 6}" y="{ly_txt}" fill="{rcol}" font-family="sans-serif" font-size="12" font-weight="bold">{xml_esc(lbl)}</text>')

    n_h = len(hist_c)
    hist_pts = [f"{int(round(x_left + (x_today - x_left) * (idx_h / max(1, n_h - 1))))},{to_y(val_h)}" for idx_h, val_h in enumerate(hist_c)]
    hist_poly = " ".join(hist_pts)

    hi_i, lo_i = int(np.argmax(hist_c)), int(np.argmin(hist_c))
    hi_x = int(round(x_left + (x_today - x_left) * (hi_i / max(1, n_h - 1))))
    lo_x = int(round(x_left + (x_today - x_left) * (lo_i / max(1, n_h - 1))))
    hi_y, lo_y = to_y(hist_c[hi_i]), to_y(hist_c[lo_i])

    x_ticks_svg = []
    for ti in sorted(set([0, n_h // 3, (2 * n_h) // 3, n_h - 1])):
        tx = int(round(x_left + (x_today - x_left) * (ti / max(1, n_h - 1))))
        dl = "今日" if ti == n_h - 1 else xml_esc(hist_d[ti])
        tcol = "#ffd54f" if ti == n_h - 1 else "#94a3b8"
        x_ticks_svg.append(f'<text x="{tx}" y="{y_bot + 22}" fill="{tcol}" font-family="sans-serif" font-size="12.5" font-weight="bold" text-anchor="middle">{dl}</text>')

    fx_xs = [x_today, 700, 845, 985]
    for fx_x, fx_l in zip(fx_xs[1:], ["+1週", "+2~3週", "+4週"]):
        x_ticks_svg.append(f'<text x="{fx_x}" y="{y_bot + 22}" fill="#93c5fd" font-family="sans-serif" font-size="12.5" font-weight="bold" text-anchor="middle">{fx_l}</text>')

    def _pts_str(arr):
        return " ".join(f"{fx_xs[i]},{to_y(arr[i])}" for i in range(min(len(fx_xs), len(arr))))

    poly_bull, poly_base, poly_bear = _pts_str(pu), _pts_str(pb), _pts_str(pd_p)
    fan_pts = _pts_str(pu) + " " + " ".join(f"{fx_xs[i]},{to_y(pd_p[i])}" for i in range(len(fx_xs) - 1, -1, -1))

    ev_marks_svg = []
    for idx_e, em in enumerate(forecast.get("events_on_axis", [])):
        ex = int(round(x_today + (fx_xs[-1] - x_today) * min(0.95, max(0.06, em["days"] / 28.0))))
        ey_lbl = y_top + 18 + (idx_e % 3) * 20
        ex_lbl = max(x_today + 65, min(x_right - 55, ex))
        ev_marks_svg.append(
            f'<line x1="{ex}" y1="{y_top}" x2="{ex}" y2="{y_bot}" stroke="#f87171" stroke-width="1.3" stroke-opacity="0.55" stroke-dasharray="3,3"/>'
            f'<text x="{ex_lbl}" y="{ey_lbl}" fill="#fca5a5" font-family="sans-serif" font-size="12" font-weight="bold" text-anchor="middle">⚡{xml_esc(em["label"])}</text>'
        )

    y_now = to_y(tw_now)
    dot_col = "#ff4d4f" if radar["master_state"] == "RED_LOCKED" else ("#fbbf24" if radar["master_state"] == "YELLOW" else "#00e676")

    end_items = sorted([
        ("bull", to_y(pu[-1]), "#69f0ae", f"🟢樂觀({probs['bull']}%) {pu[-1]:,.0f}"),
        ("base", to_y(pb[-1]), "#fcd34d", f"🟡基準({probs['base']}%) {pb[-1]:,.0f}"),
        ("bear", to_y(pd_p[-1]), "#ff8a80", f"🔴保守({probs['bear']}%) {pd_p[-1]:,.0f}")
    ], key=lambda t: t[1])
    end_labels_svg, prev_ly = [], y_top - 10
    for _, raw_y, col_t, txt_t in end_items:
        ly = min(y_bot - 6, max(raw_y, prev_ly + 26))
        prev_ly = ly
        end_labels_svg.append(f'<text x="{fx_xs[-1] + 14}" y="{ly + 4}" fill="{col_t}" font-family="sans-serif" font-size="13.5" font-weight="900">{xml_esc(txt_t)}</text>')

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 426" width="100%" height="auto">
<rect width="1200" height="426" rx="12" fill="#09111a" stroke="#334155" stroke-width="2"/>
<rect x="{x_left}" y="{y_top}" width="{x_today - x_left}" height="{y_bot - y_top}" fill="#0f172a"/>
<rect x="{x_today}" y="{y_top}" width="{x_right - x_today}" height="{y_bot - y_top}" fill="#131c31"/>
<text x="270" y="42" fill="#38bdf8" font-family="sans-serif" font-size="14.5" font-weight="900" text-anchor="middle">◀ 過去 {n_h} 日真實歷史走勢（實線）</text>
<text x="825" y="42" fill="#fcd34d" font-family="sans-serif" font-size="14.5" font-weight="900" text-anchor="middle">未來 1～4 週全浮動三情境預測（虛線） ▶</text>
{''.join(grid_svg)}
{''.join(ref_svg)}
{''.join(ev_marks_svg)}
<polygon points="{fan_pts}" fill="#f59e0b" fill-opacity="0.10"/>
<line x1="{x_today}" y1="48" x2="{x_today}" y2="{y_bot + 6}" stroke="#ffd54f" stroke-width="2.2" stroke-dasharray="5,4"/>
<rect x="{x_today - 86}" y="20" width="172" height="28" rx="14" fill="{dot_col}" stroke="#ffd54f" stroke-width="2"/>
<text x="{x_today}" y="39" fill="#ffffff" font-family="sans-serif" font-size="14" font-weight="900" text-anchor="middle">📍今日 {tw_now:,.0f}點</text>
<polyline points="{hist_poly}" fill="none" stroke="#38bdf8" stroke-width="3.6" stroke-linejoin="round"/>
<circle cx="{hi_x}" cy="{hi_y}" r="5.5" fill="#f97316" stroke="#fff" stroke-width="1.5"/>
<text x="{hi_x}" y="{max(y_top + 16, hi_y - 10)}" fill="#fdba74" font-family="sans-serif" font-size="12.5" font-weight="bold" text-anchor="middle">高 {hist_c[hi_i]:,.0f}</text>
<circle cx="{lo_x}" cy="{lo_y}" r="5.5" fill="#ef4444" stroke="#fff" stroke-width="1.5"/>
<text x="{lo_x}" y="{min(y_bot - 6, lo_y + 18)}" fill="#fca5a5" font-family="sans-serif" font-size="12.5" font-weight="bold" text-anchor="middle">低 {hist_c[lo_i]:,.0f}</text>
<polyline points="{poly_bull}" fill="none" stroke="#00e676" stroke-width="3.2" stroke-dasharray="7,5" stroke-linejoin="round"/>
<polyline points="{poly_base}" fill="none" stroke="#fbbf24" stroke-width="4.2" stroke-dasharray="9,5" stroke-linejoin="round"/>
<polyline points="{poly_bear}" fill="none" stroke="#ff4d4f" stroke-width="3.2" stroke-dasharray="7,5" stroke-linejoin="round"/>
<circle cx="{fx_xs[-1]}" cy="{to_y(pu[-1])}" r="5.5" fill="#00e676"/>
<circle cx="{fx_xs[-1]}" cy="{to_y(pb[-1])}" r="6" fill="#fbbf24"/>
<circle cx="{fx_xs[-1]}" cy="{to_y(pd_p[-1])}" r="5.5" fill="#ff4d4f"/>
{''.join(end_labels_svg)}
<circle cx="{x_today}" cy="{y_now}" r="13" fill="{dot_col}" fill-opacity="0.30"/>
<circle cx="{x_today}" cy="{y_now}" r="6.5" fill="{dot_col}" stroke="#ffffff" stroke-width="2.5"/>
{''.join(x_ticks_svg)}
</svg>"""

def build_realtime_and_forecast_svg_html(fleet: dict, radar: dict, forecast: dict, zoom_mode: str = "auto") -> str:
    hist_c = fleet.get("hist_closes", []) or [fleet["twii_now"]]
    tw_now = float(fleet["twii_now"])
    probs = forecast["probs"]
    svg_xml = build_realtime_forecast_svg_raw(fleet, radar, forecast)
    border_col = "#ff4d4f" if radar["master_state"] == "RED_LOCKED" else ("#fbbf24" if radar["master_state"] == "YELLOW" else "#00e676")
    policy_warn = "<br>• <span style='color:#ff8a80;'>⚠️ <b>政策逃命波警示</b>：限空令/護盤後 10 交易日內，落底未達 4/5 且未連 3 日站穩 10MA，急彈一律定性為「政策逃命波」！</span>" if radar["is_policy_escape_wave"] else ""
    precursor_b = f"<br>• <span style='color:#fde047;'>{radar['precursor_txt']}</span>" if radar.get("precursor_txt") else ""

    return clean_html(f"""
<div style="background: linear-gradient(180deg, #0d1520 0%, #16222f 100%); border: 2px solid {border_col}; border-radius: 14px; padding: 14px 16px; margin-bottom: 12px; color: #fff;">
<div class="war-sub-title" style="text-align:center; font-size:19.5px; font-weight:900; color:#fcd34d; border-bottom:1px solid rgba(252,211,77,0.3); padding-bottom:7px; margin-bottom:10px;">
📈 加權指數真實歷史軌跡 × 未來 1～4 週全浮動三情境預測走勢圖（{forecast['mode_label']}）
</div>
<div style="font-size:14.4px; font-weight:800; color:#f8fafc; background:rgba(0,0,0,0.35); padding:9px 13px; border-radius:8px; margin-bottom:10px; border-left:4px solid {border_col}; line-height:1.65;">
• <b>📍 推進基準</b>：左側 {len(hist_c)} 日真實收盤（高 <b>{max(hist_c):,.0f}</b> ／ 低 <b>{min(hist_c):,.0f}</b>）；紅點鎖定今日 <b>{tw_now:,.0f} 點</b>，右側動態推演（🟡基準 {probs['base']}% ／ 🟢樂觀 {probs['bull']}% ／ 🔴保守 {probs['bear']}%）<br>
• <b>總開關狀態</b>：{radar['master_badge']} ➔ <span style="color:#ffd54f;">{radar['master_action']}</span>{policy_warn}{precursor_b}
</div>
{_wrap_svg_zoom(svg_xml, zoom_mode)}
</div>
""")

def build_forecast_responsive_cards_html(forecast: dict) -> str:
    cards = []
    for r in forecast.get("table_rows", []):
        title = r["預測情境（圖中線色）"]
        b_col = "#fbbf24" if "基準" in title else ("#00e676" if "樂觀" in title else "#ff4d4f")
        cards.append(
            f"<div class='forecast-card' style='background:#0f172a; border:1px solid rgba(255,255,255,0.14); border-top:4px solid {b_col}; border-radius:11px; padding:13px 15px; color:#f8fafc; font-size:14.2px; line-height:1.65;'>"
            f"<div style='font-size:15.5px; font-weight:900; color:{b_col}; border-bottom:1px solid rgba(255,255,255,0.12); padding-bottom:6px; margin-bottom:7px;'>{title} <span style='float:right; background:rgba(255,255,255,0.12); padding:1px 8px; border-radius:6px; color:#fff;'>機率 {r['動態機率']}</span></div>"
            f"• <b>📍 預測路徑</b>：{r['未來 1～4 週預測路徑與目標區']}<br>"
            f"• <b>⚖️ 確認分界</b>：{r['確認／轉折分界']}<br>"
            f"• <b>💧 四水庫操作</b>：<span style='color:#fde047;'>{r['四水庫實戰操作']}</span></div>"
        )
    return clean_html(f"<div class='forecast-grid-3'>{''.join(cards)}</div>")

build_forecast_mobile_cards_html = build_forecast_responsive_cards_html

# ==========================================
# 4. 盤面族群格式化 ＋ 8 大【結論：說明】高可讀性面板（修正美股盤後紅漲綠跌 ＋ 並排 ΔOI/日盤）
# ==========================================
def compute_tw_hot_sectors(records: list[dict]) -> dict:
    if not records:
        return {"hot_html": "", "cold_html": "", "hot_text": "尚未啟動 Layer 2 掃描", "cold_text": "", "top1_name": "待掃描"}
    df_r = pd.DataFrame(records)
    df_r["theme"] = df_r.apply(lambda r: map_tw_theme_cluster(str(r["code"]), str(r["name"]), str(r["sector"]), str(r["official_sec"])), axis=1)
    theme_rows = []
    for theme_name, grp in df_r.groupby("theme"):
        if len(grp) == 0: continue
        blended_yod = round(0.55 * (float(grp["est_vol"].sum()) / max(1.0, float(grp["yest_vol"].sum()))) + 0.45 * float(grp["yod_ratio"].clip(upper=5.0).mean()), 2)
        avg_chg, max_chg = round(float(grp["chg_1d"].mean()), 2), round(float(grp["chg_1d"].max()), 2)
        grp_s = grp.sort_values(by=["chg_1d", "yod_ratio"], ascending=[False, False])
        l_html = [f"<b style='color:#fff;'>{r['name']}({r['code']})</b> <b style='color:{'#ff8a80' if r['chg_1d']>=0 else '#69f0ae'};'>{r['chg_1d']:+.2f}%</b><span style='color:#ffd54f; font-size:12px;'>[量{r['yod_ratio']:.1f}x]</span>" for _, r in grp_s.head(3).iterrows()]
        theme_rows.append({"theme": theme_name, "avg_chg": avg_chg, "blended_yod": blended_yod, "heat": avg_chg * 2.2 + max_chg * 0.6 + (blended_yod - 1.0) * 6.5, "leaders_html": "、".join(l_html)})
    if not theme_rows:
        return {"hot_html": "", "cold_html": "", "hot_text": "暫無", "cold_text": "", "top1_name": "主流"}
    df_th = pd.DataFrame(theme_rows).sort_values(by="heat", ascending=False).reset_index(drop=True)
    hot_divs = [f"<div style='margin:4px 0; padding:5px 9px; background:rgba(0,0,0,0.3); border-radius:6px; border-left:3px solid #ff8a80; font-size:13.5px;'><b style='color:#ffd54f;'>🔥 {r['theme']}</b> (均 <b style='color:#ff8a80;'>{r['avg_chg']:+.2f}%</b>｜量 {r['blended_yod']:.2f}x)<br>&nbsp;&nbsp;↳ {r['leaders_html']}</div>" for _, r in df_th.head(3).iterrows()]
    cold_divs = [f"<div style='margin:4px 0; padding:5px 9px; background:rgba(0,0,0,0.3); border-radius:6px; border-left:3px solid #69f0ae; font-size:13.5px;'><b style='color:#e2e8f0;'>❄️ {r['theme']}</b> (均 {r['avg_chg']:+.2f}%｜量 {r['blended_yod']:.2f}x)<br>&nbsp;&nbsp;↳ {r['leaders_html']}</div>" for _, r in df_th.tail(2).sort_values(by="avg_chg").iterrows()]
    return {"hot_html": "".join(hot_divs), "cold_html": "".join(cold_divs), "top1_name": "、".join(df_th["theme"].head(2).tolist())}

def format_sector_bold_html(sec_list: list[str], is_up: bool = True) -> str:
    if not sec_list: return ""
    col, icon = ("#ff8a80", "🔥 美股領漲") if is_up else ("#69f0ae", "❄️ 美股領跌")
    out = []
    for s in sec_list:
        sb = re.sub(r"([\u4e00-\u9fa5A-Za-z]+\([A-Z]+\))", r"<b style='color:#fff;'>\1</b>", s).replace("指標股：", "<b style='color:#ffd54f;'>指標股：</b>")
        sb = re.sub(r"(\+\d+\.\d+%)", r"<span style='color:#ff8a80;'>\1</span>", sb)
        sb = re.sub(r"(-\d+\.\d+%)", r"<span style='color:#69f0ae;'>\1</span>", sb)
        out.append(f"<div style='margin:4px 0; padding:5px 9px; background:rgba(0,0,0,0.3); border-radius:6px; border-left:3px solid {col}; font-size:13.5px;'><b style='color:{col};'>{icon}</b> {sb}</div>")
    return "".join(out)

def build_8_indicator_panels_html(
    fleet: dict, us_data: dict, deriv: dict, breadth: dict, scan_data, tw_hot_sec_info: dict,
    sb_fini_oi: int, sb_margin_2d: float, sb_margin_2d_pct: float, sb_margin_dd: float,
    sb_maint: float, sb_basis: float, sb_consec_cover: int, sb_cover_3d: int,
    sb_limit_up: int, sb_limit_dn: int, vix_now: float, vix_chg: float, tnx_val: float, tnx_chg: float,
    sox_chg: float, tsm_chg: float, tsm_ext: str, nvda_chg: float, nvda_ext: str, fx_now: float, fx_stopped_dep: bool
) -> str:
    now_tw = get_tw_now()

    def _panel(title, concl, explain_html, meaning, accent):
        return (
            f"<div class='war-dim-item' style='border-left:5px solid {accent};'>"
            f"<span class='war-dim-concl'>{title}</span>"
            f"<div style='background:rgba(0,0,0,0.32); padding:5px 9px; border-radius:6px; margin-bottom:7px; font-weight:900; color:#fde047;'>結論：{concl}</div>"
            f"<div style='font-size:13.8px; line-height:1.68;'><b>說明：</b><br>{explain_html}</div>"
            f"<span class='meaning-note'>{meaning}</span></div>"
        )

    # 1. 台股廣度與類股結構（含 OTC 背離差）
    adv_r, is_br_stale = breadth["adv_ratio_pct"], bool(breadth.get("is_stale_intraday", False))
    core_up, core_dn, core_adv = breadth.get("core_rt_up", 0), breadth.get("core_rt_dn", 0), breadth.get("core_rt_adv_pct", 50.0)
    sec_struct_html = _format_sector_struct_line(fleet.get("sector_struct", {}), as_html=True)
    if is_br_stale:
        c1_concl = f"{'🟢 盤中權值偏多' if (core_adv>=70 or fleet['twii_chg_pct']>=1.5) else ('🔴 盤中權值偏弱' if (core_adv<=30 or fleet['twii_chg_pct']<=-1.5) else '⚖️ 盤中權值震盪')}（權值上漲比 {core_adv:.0f}%）"
        c1_body = f"• <b>盤中權值溫度</b>：上漲 {core_up} 檔 ／ 下跌 {core_dn} 檔<br>{sec_struct_html}• <span style='color:#94a3b8;'>前日收盤參考：漲 {breadth['up_cnt']:,} ／ 跌 {breadth['dn_cnt']:,}</span>" + (f"{tw_hot_sec_info['hot_html']}" if scan_data else "")
    else:
        c1_concl = "🚀 全面普漲／廣度推力爆發" if (adv_r >= 85 or sb_limit_up >= 100) else ("🟢 多方家數佔優" if adv_r >= 55 else ("🚨 空方恐慌普跌" if adv_r <= 25 else "⚖️ 盤面分化震盪"))
        c1_body = f"• <b>全市場家數</b>：上漲 <b style='color:#ff8a80;'>{breadth['up_cnt']:,}</b> ／ 下跌 <b style='color:#69f0ae;'>{breadth['dn_cnt']:,}</b>（上漲比 <b>{adv_r:.1f}%</b>）<br>• <b>漲跌停家數</b>：漲停 <b style='color:#ff8a80;'>{sb_limit_up}</b> ／ 跌停 <b style='color:#69f0ae;'>{sb_limit_dn}</b><br>{sec_struct_html}" + (f"{tw_hot_sec_info['hot_html']}" if scan_data else "")
    p1 = _panel("1. 🔥 台股廣度／類股結構", c1_concl, c1_body, "📖 判讀：搭配 OTC 背離差與電子／金融／非金電辨識拉積盤或普漲；收盤上漲比≧85%或漲停≧100家觸發廣度豁免。", "#ff4d4f")

    # 2. 美股 VIX / 費半 / ADR（v65.2 修正：盤後獨立紅漲綠跌 ＋ 補 ES 標普期指）
    hol = us_data.get("holiday_info", {})
    c2_concl = f"🇺🇸 美股【{hol['holiday_name']}】休市（僅期貨薄量）" if hol.get("is_holiday") else ("🚨 恐慌爆表（VIX ≧ 30）" if vix_now >= 30 else ("🔥 半導體強彈修復" if sox_chg >= 2.0 else ("⚠️ 避險情緒偏高（VIX 20~29）" if vix_now >= 20 else "☀️ 風險偏好穩健")))
    nq_v, es_v = us_data.get("nq_chg", 0.0), us_data.get("es_chg", 0.0)
    nq_c, es_c = ("#ff8a80" if nq_v >= 0 else "#69f0ae"), ("#ff8a80" if es_v >= 0 else "#69f0ae")
    sox_html = _fmt_us_quote_html(us_data["quotes"].get("%5ESOX", {"chg_pct": sox_chg}))
    tsm_html = _fmt_us_quote_html(us_data["quotes"].get("TSM", {"chg_pct": tsm_chg}))
    nvda_html = _fmt_us_quote_html(us_data["quotes"].get("NVDA", {"chg_pct": nvda_chg}))
    c2_body = (
        f"• <b>恐慌與期指</b>：VIX <b>{vix_now:.2f}</b>({vix_chg:+.1f}%) ｜ NQ <b style='color:{nq_c};'>{nq_v:+.2f}%</b> ｜ ES <b style='color:{es_c};'>{es_v:+.2f}%</b><br>"
        f"• <b>AI 半導體</b>：費半 {sox_html} ｜ 台積ADR {tsm_html} ｜ 輝達 {nvda_html}"
        f"{format_sector_bold_html(us_data['top_up_sectors'][:2], True)}{format_sector_bold_html(us_data['bot_dn_sectors'][:1], False)}"
    )
    p2 = _panel("2. 🌎 美股 VIX＆費半板塊", c2_concl, c2_body, "📖 判讀：紅漲綠跌獨立標示正規盤與盤後；VIX ≧30 屬恐慌區、美股休市或半日市時重點盯 NQ/ES 電子盤。", "#f59e0b")

    # 3. 散戶綜合恐慌指數
    f_info = _compute_retail_fear_index(sb_maint, deriv["pc_ratio"], vix_now, deriv["retail_mtx_oi"], deriv["warrant_hedge_yi"])
    c3_body = (
        f"• <b>目前總分</b>：<b style='color:{f_info['color']}; font-size:16px;'>{f_info['score']:.1f} / 100 分</b><br>"
        f"• <b>四大因子</b>：維持率(35%)={f_info['s_maint']:.0f} ｜ PCR(25%)={f_info['s_pcr']:.0f}<br>"
        f"• <b>延伸因子</b>：VIX(20%)={f_info['s_vix']:.0f} ｜ 小台散戶＆權證(20%)={f_info['s_ret_w']:.0f}"
    )
    p3 = _panel("3. 😱 散戶綜合恐慌指數", f_info["label"], c3_body, "📖 判讀：>60分 散戶認輸斷頭區（易醞釀強彈）；35~55分 平衡選股區；<28分 過度貪婪防修正。", "#a855f7")

    # 4. 融資維持率與曲線
    m_flush_ok = (sb_margin_dd >= fleet["twii_30d_dd_pct"])
    c4_concl = "💎 籌碼清洗達標（30日融資減幅 ≧ 大盤回檔）" if (sb_maint <= 150 or m_flush_ok) else ("🟡 融資去槓桿進行中" if sb_margin_2d < 0 else "⚠️ 融資回流、籌碼待沉澱")
    c4_body = (
        f"• <b>線性維持率</b>：<b style='color:#ffd54f;'>{sb_maint:.1f}%</b>（{deriv['margin_src']}）<br>"
        f"• <b>上市融資餘額</b>：<b>{deriv['margin_balance_yi']:,.1f} 億</b>（單日 {deriv['margin_chg_yi']:+.1f}億 ／ 近2日 {sb_margin_2d:+.1f}億）<br>"
        f"• <b>30日波段減幅</b>：<b>-{sb_margin_dd:.1f}%</b>（{'已超越' if m_flush_ok else '低於'}大盤回檔 -{fleet['twii_30d_dd_pct']:.1f}% {'✅' if m_flush_ok else '⚠️'}）"
    )
    p4 = _panel("4. 🛡️ 融資維持率與曲線", c4_concl, c4_body, "📖 判讀：整戶破 150% 觸發追繳；即使維持率回升，只要 30日融資減幅 ≧ 大盤跌幅即代表高檔浮額已洗淨。", "#00e676")

    # 5. 大小台期貨（v65.2 並排顯示 ΔOI 與 日盤，與官方截圖 100% 對齊）
    spec_ok = bool(deriv.get("top_spec_valid", False))
    spec_hint = f"，但特定法人前五 {deriv['top5_spec_net']:+,} 口偏多對沖" if (spec_ok and sb_fini_oi < -60000 and (deriv["top5_spec_net"] or 0) > 0) else ""
    if sb_fini_oi < -80000: c5_concl = f"🚨 外資帳面極限重空 ({sb_fini_oi:+,}口){spec_hint}"
    elif sb_fini_oi < -60000: c5_concl = f"🔴 外資股災避險空單 ({sb_fini_oi:+,}口){spec_hint}"
    elif sb_fini_oi < -40000: c5_concl = f"🟡 外資警戒避險偏空 ({sb_fini_oi:+,}口)"
    else: c5_concl = f"🟢 外資期貨水位中性偏多 ({sb_fini_oi:+,}口)"

    spec_line = (f"• <b>特定法人({deriv['top_spec_date']})</b>：近月前五 <b>{deriv['top5_spec_net']:+,}</b> ／ 前十 <b>{deriv['top10_spec_net']:+,}</b> 口"
                 + (f"（全月前十 {deriv['top_spec_all_10']:+,}）" if deriv.get("top_spec_all_10") is not None else "") + "<br>") if spec_ok else ""
    b_txt = f"{'正價差' if sb_basis >= 0 else '逆價差'} <b>{sb_basis:+.0f}點</b>" if deriv.get("basis_valid") else deriv["basis_src"]
    night_seg5 = _format_night_tx_segment(deriv, now_tw, nq_chg_override=us_data.get("nq_chg"), as_html=True)
    c5_body = (
        f"• <b>大台外資</b>：<b>{sb_fini_oi:+,}</b>（含夜盤ΔOI <b>{deriv['fini_tx_oi_delta']:+,}</b> ／ 日盤 <b>{deriv['fini_tx_chg']:+,}</b>）<br>"
        f"• <b>投信／自營</b>：投信 <b>{deriv['sitc_tx_oi']:+,}</b>(日{deriv['sitc_tx_chg']:+,}) ｜ 自營 <b>{deriv['dealer_tx_oi']:+,}</b>(日{deriv['dealer_tx_chg']:+,})<br>"
        f"{spec_line}"
        f"• <b>實質與散戶</b>：外+投淨額 <b>{deriv['real_fini_oi']:+,}</b> ｜ 小台散戶 <b>{deriv['retail_mtx_oi']:+,}</b>(日{deriv['retail_mtx_chg']:+,}) ｜ 價差：{b_txt}<br>"
        f"• <b>夜盤動態</b>：{night_seg5 or '待更新'}<br>"
        f"• <b>外資空單成本</b>：約 <b>{deriv['est_short_cost']:,.0f}</b>（獲利 {deriv['short_profit_pts']:+,.0f}點・連補 {sb_consec_cover}日 / 3日 {sb_cover_3d:+,}口）"
    )
    p5 = _panel("5. 📊 大小台期貨＆特定法人＆夜盤", c5_concl, c5_body, "📖 判讀：括號並排顯示『含夜盤ΔOI』與『日盤淨交易量』；外資空單常含 ETF 避險，須對照特定法人與實質淨額。", "#ffd54f")

    # 6. 選擇權 PCR ＋ Max OI ＋ 現貨 Sum-Check
    pcr_v, wh_v = deriv["pc_ratio"], deriv["warrant_hedge_yi"]
    c6_concl = "🟢 選擇權下檔撐盤強 (PCR > 120%)" if pcr_v >= 120 else ("🔴 買權反壓沉重 (PCR < 80%)" if pcr_v < 80 else "⚖️ 選擇權多空拉鋸 (PCR 80%~120%)")
    max_oi_line = (f"• <b>Max OI 箱型 ({deriv['max_oi_contract']})</b>：天花板 <b>{deriv['max_oi_call_strike']:,}</b> ({deriv['max_oi_call_oi']:,}口) ／ 地板 <b>{deriv['max_oi_put_strike']:,}</b> ({deriv['max_oi_put_oi']:,}口)<br>"
                   if deriv.get("max_oi_valid") else "")
    if deriv.get("spot_valid"):
        sc_c, sc_sum = deriv["spot_fini_consec"], deriv.get("spot_fini_consec_sum", deriv.get("spot_fini_5d_sum", 0.0))
        sc_lbl = f"連買{sc_c}日({sc_sum:+.0f}億)" if sc_c > 0 else (f"連賣{abs(sc_c)}日({sc_sum:+.0f}億)" if sc_c < 0 else "持平")
        spot_lines = (
            f"• <b>外資現貨 ({deriv.get('twse_date','')})</b>：<b style='color:{'#ff8a80' if deriv['spot_fini_yi']>=0 else '#69f0ae'};'>{deriv['spot_fini_yi']:+.1f} 億</b>（{sc_lbl}）<br>"
            f"• <b>內資對作</b>：投信 <b>{deriv['spot_sitc_yi']:+.1f}億</b> ｜ 自營自行 <b>{deriv['spot_dealer_self_yi']:+.1f}億</b> ｜ 避險 <b>{wh_v:+.1f}億</b><br>"
            f"• <b>三大合計</b>：<b>{deriv['spot_total_yi']:+.1f} 億</b>（Sum-Check 驗算通過）"
        )
    else:
        spot_lines = f"• <b>自營權證避險</b>：<b>{wh_v:+.2f} 億</b>（現貨待盤後）"
    c6_body = f"• <b>選擇權 PCR</b>：<b style='color:#ffd54f;'>{pcr_v:.1f}%</b><br>{max_oi_line}{spot_lines}"
    p6 = _panel("6. 🎯 PCR＆Max OI＆現貨 Sum-Check", c6_concl, c6_body, "📖 判讀：Max OI 買權／賣權最大未平倉即莊家劃定的短線天花板與地板；限空令期須看外資現貨轉買共振。", "#38bdf8")

    # 7. 60分K微觀扳機 ＆ 日KDJ
    near_60_ma5, above_day_ma5, above_60_ma20 = (fleet["twii_now"] >= fleet["k60_ma5"] * 0.997), (fleet["twii_now"] >= fleet["ma5"]), (fleet["twii_now"] >= fleet["k60_ma20"])
    is_heavy_drop, k60_j_falling = (fleet["twii_chg_pct"] <= -2.0), (fleet["k60_j"] < fleet["k60_j_prev"])
    if near_60_ma5 and above_day_ma5:
        if is_heavy_drop: c7_concl = "🟡 急殺測日5MA（動能未穩，待隔日確認）"
        elif k60_j_falling: c7_concl = "🟡 暫守5MA但60分J下彎（待勾頭確認）"
        elif fleet["k60_j"] >= 90.0: c7_concl = f"🔥 站穩雙5MA，但60分J={fleet['k60_j']:.1f} 近過熱防震盪"
        else: c7_concl = "🟢 60分K與日5MA同步站穩（右側止跌亮起）"
    elif above_day_ma5 and above_60_ma20:
        c7_concl = "🟢 穩守日5MA與60分20MA（高檔換手整理）"
    else:
        c7_concl = "🔴 60分K弱勢蓋壓（短線仍受均線壓制）"

    c7_body = (
        f"• <b>60分K位階</b>：現價 <b>{fleet['twii_now']:,.0f}</b> vs 5MA(<b>{fleet['k60_ma5']:,.0f}</b>) ／ 20MA(<b>{fleet['k60_ma20']:,.0f}</b>)<br>"
        f"• <b>60分K動能</b>：J 值 <b>{fleet['k60_j']:.1f}</b>（{'↗勾頭' if not k60_j_falling else '↘下彎'}・>95 鈍化警戒）<br>"
        f"• <b>日線 KDJ</b>：K={fleet['k9']:.1f} ／ D={fleet['d9']:.1f} ／ J={fleet['j9']:.1f}<br>"
        f"• <b>均線乖離</b>：5MA(<b>{fleet['bias_5']:+.1f}%</b>) ／ 10MA(<b>{fleet['bias_10']:+.1f}%</b>) ／ 20MA(<b>{fleet['bias_20']:+.1f}%</b>)"
    )
    p7 = _panel("7. ⚡ 60分K扳機＆日KDJ", c7_concl, c7_body, "📖 判讀：止跌須同時滿足『站穩60分5MA＋日5MA＋60分J勾頭』；60分J >95 防隔日回吐，日J <10 極度超賣。", "#ec4899")

    # 8. 美債 10Y ＋ 信用利差 ＋ 台幣
    cs_alert, cs_str = us_data.get("credit_spread_alert", False), us_data.get("credit_spread_str", "信用利差常態")
    if (tnx_val >= 5.0 or cs_alert) and fx_now >= 32.5: c8_concl = "🚨 雙紅燈外資提款／信用利差警戒"
    elif fx_stopped_dep and tnx_val < 5.0 and (not cs_alert): c8_concl = "🟢 債匯與信用利差穩定（助外資回補）"
    else: c8_concl = "🟡 債匯高檔警戒（美債偏高或台幣續貶）"
    c8_body = (
        f"• <b>美債 10Y (^TNX)</b>：<b style='color:{'#ff8a80' if tnx_val>=5.0 else '#69f0ae'};'>{tnx_val:.2f}%</b> ({tnx_chg:+.2f}%)<br>"
        f"• <b>信用利差</b>：{cs_str}<br>"
        f"• <b>美元台幣</b>：現匯 <b>{fx_now:.3f}</b>（日 {deriv['fx_chg_ntd']:+.3f}・5MA={deriv['fx_ma5']:.3f}・<b>{'已止貶✅' if fx_stopped_dep else '續貶⚠️'}</b>）"
    )
    p8 = _panel("8. 💵 美債10Y＆信用利差＆台幣", c8_concl, c8_body, "📖 判讀：^TNX 4.95~5.15% 為長債 (00933B) 偷跑區；若美債急跌伴隨 HYG 大跌屬衰退恐慌，優先配新穀倉1與防彈衣。", "#14b8a6")

    return clean_html(f"""
    <div class="war-hero-box" style="border-left-color:#38bdf8;">
    <div style="font-size:18px; font-weight:900; color:#ffd54f; margin-bottom:8px;">
    🧭 【大盤 8 大核心指標全景儀表板】（先結論 ➔ 短條列說明 ➔ 📖 判讀口訣）
    </div>
    <div class="war-dim-grid">{p1}{p2}{p3}{p4}{p5}{p6}{p7}{p8}</div>
    </div>
    """)

# ==========================================
# 5. 淘金客 v25.0 三軌戰情數據包匯出中心（按鈕 0 ＋ 按鈕 A ＋ 按鈕 B）
# ==========================================
def build_market_outpost_line_v244(fleet: dict, radar: dict, deriv: dict, forecast: dict, tnx_val: float, fx_now: float, ban_active: bool, next_ev: dict, us_data: dict = None) -> str:
    tw = fleet["twii_now"]
    pos_60, pos_120, pos_240 = (">=" if tw >= fleet["ma60"] else "<"), (">=" if tw >= fleet["ma120"] else "<"), (">=" if tw >= fleet["ma240"] else "<")
    ban_tag = "限空令生效中(凍結借券賣減看多加分)" if ban_active else "無限空令"
    esc_tag = "/⚠️政策逃命波" if radar["is_policy_escape_wave"] else ""
    b2_tag = f"四軌落底:{'✅' + radar['b2_detail'] if radar['b2_ok'] else '未達標'}"
    ev_tag = f"近3日事件:{next_ev['日期']}{next_ev['_short']}(倒數{next_ev['_days']}天)" if (next_ev and next_ev["_days"] <= 3) else (f"下個事件:{next_ev['日期']}{next_ev['_short']}" if next_ev else "無近事件")
    pr = forecast["probs"]
    hol = (us_data or {}).get("holiday_info", {})
    hol_tag = f" | 🇺🇸美股{hol['holiday_name']}休市" if hol.get("is_holiday") else ""
    night_txt = _format_night_tx_segment(deriv, get_tw_now(), nq_chg_override=(us_data.get("nq_chg") if us_data else None), as_html=False)
    night_seg = f" | {night_txt}" if night_txt else ""
    spec_seg = f"/前五特法={deriv['top5_spec_net']:+,}/前十={deriv['top10_spec_net']:+,}" if deriv.get("top_spec_valid") else ""
    sc_c, sc_sum = deriv.get("spot_fini_consec", 0), deriv.get("spot_fini_consec_sum", deriv.get("spot_fini_5d_sum", 0.0))
    spot_tag = f" | 現貨Sum-Check:外{deriv['spot_fini_yi']:+.1f}/投{deriv['spot_sitc_yi']:+.1f}/自營{deriv['spot_dealer_self_yi']+deriv['warrant_hedge_yi']:+.1f}=合計{deriv['spot_total_yi']:+.1f}億(外資連{sc_c:+d}日/{sc_sum:+.0f}億)" if deriv.get("spot_valid") else ""
    return (
        f"[大盤前哨] 加權={tw:,.1f}({fleet['twii_chg_pct']:+.2f}%・量{fleet.get('val_yi',0):,.0f}億・{fleet['date_str']}){night_seg}{hol_tag} | "
        f"位階:{pos_60}60MA({fleet['ma60']:,.0f})/{pos_120}120MA({fleet['ma120']:,.0f})/{pos_240}240MA({fleet['ma240']:,.0f})/月20MA({fleet['m_ma20']:,.0f}) | "
        f"模組0燈號:{radar['lamp_short']}(預警{radar['w_count']}/5・落底{radar['b_count']}/5・{b2_tag}{esc_tag}) | "
        f"預測機率:基準{pr['base']}%/樂觀{pr['bull']}%/保守{pr['bear']}% | "
        f"大台外資OI={deriv['fini_tx_oi']:+,}(ΔOI{deriv['fini_tx_oi_delta']:+,}/日盤{deriv['fini_tx_chg']:+,}){spec_seg}/小台散戶={deriv['retail_mtx_oi']:+,}{spot_tag} | {ban_tag} | PCR={deriv['pc_ratio']:.1f}% | ^TNX={tnx_val:.2f}% | TWD={fx_now:.3f} | {ev_tag}"
    )

def build_market_panorama_package_v244(
    fleet: dict, radar: dict, deriv: dict, us_data: dict, breadth: dict, forecast: dict,
    upcoming_ev_rows: list[dict], now_str: str,
    sb_fini_oi: int, sb_margin_2d: float, sb_margin_2d_pct: float, sb_margin_dd: float,
    sb_maint: float, sb_basis: float, sb_consec_cover: int, sb_cover_3d: int,
    sb_limit_up: int, sb_limit_dn: int, ban_active: bool, days_since_ban: int,
    fx_stopped_dep: bool, portfolio_summary: dict = None, include_portfolio: bool = True,
    bond_sentinels: dict = None
) -> str:
    """🌍 按鈕 0（v65.2）：補入 ES 期指、OTC 背離差、MSCI 尾盤提醒與債市四大哨兵"""
    vix_q = us_data["quotes"].get("%5EVIX", {})
    vix_now, vix_chg = vix_q.get("close", 0.0), vix_q.get("chg_pct", 0.0)
    sox_chg = us_data["quotes"].get("%5ESOX", {}).get("chg_pct", 0.0)
    tsm_q, nvda_q = us_data["quotes"].get("TSM", {}), us_data["quotes"].get("NVDA", {})
    tsm_chg, nvda_chg = tsm_q.get("chg_pct", 0.0), nvda_q.get("chg_pct", 0.0)
    tsm_ext = f"({tsm_q['ext_label']})" if tsm_q.get("ext_label") else ""
    nvda_ext = f"({nvda_q['ext_label']})" if nvda_q.get("ext_label") else ""
    tnx_val, tnx_chg = us_data.get("tnx_val", 0.0), us_data.get("tnx_chg", 0.0)

    f_info = _compute_retail_fear_index(sb_maint, deriv["pc_ratio"], vix_now, deriv["retail_mtx_oi"], deriv["warrant_hedge_yi"])
    sec_txt = _format_sector_struct_line(fleet.get("sector_struct", {}), as_html=False) or "待結算"
    val_yi_s = f"{fleet['val_yi']:,.1f}億" if fleet.get("val_yi", 0) > 0 else "盤中待結算"

    lines = [
        f"【淘金客 v25.0｜按鈕 0：大盤全景多空研判 ＆ 四水庫／債市哨兵戰情包】({now_str})",
        f"=== 一、加權指數價量與多級城堡位階 ({fleet['date_str']}・{fleet['quote_src']}) ===",
        f"• 加權現價={fleet['twii_now']:,.1f} ({fleet['twii_chg_pct']:+.2f}%) | 成交金額={val_yi_s} | 開={fleet['twii_open']:,.0f} / 高={fleet['twii_high']:,.0f} / 低={fleet['twii_low']:,.0f} / 昨收={fleet['twii_prev_c']:,.0f} | 30日回檔=-{fleet['twii_30d_dd_pct']:.1f}% | 14日ATR={fleet['atr_14_pts']:,.0f}點({fleet['atr_14_pct']:.2f}%)",
        f"• 多級均線：5MA={fleet['ma5']:,.0f} / 10MA={fleet['ma10']:,.0f}(連站{fleet['twii_consec_above_ma10']}日) / 20MA={fleet['ma20']:,.0f} / 60MA={fleet['ma60']:,.0f} / 120MA={fleet['ma120']:,.0f} / 240MA={fleet['ma240']:,.0f} | 月20MA={fleet['m_ma20']:,.0f} / 月36MA={fleet['m_ma36']:,.0f}",
        f"• 類股與OTC背離：{sec_txt}",
        "",
        f"=== 二、模組 0 股災雙雷達逐項清單（{radar['master_badge']}） ===",
        f"• 政策護盤：{'限空令/護盤生效中(第' + str(days_since_ban) + '天)' if ban_active else '無限空令'} | 政策逃命波：{'⚠️是' if radar['is_policy_escape_wave'] else '否'}",
        "• [預警 " + f"{radar['w_count']}/5]：" + " ； ".join([f"{'🔴' if f else '⚪'}{_strip_html(t)}" for f, t in radar["w_items"]]),
        "• [落底 " + f"{radar['b_count']}/5]：" + " ； ".join([f"{'🟢' if f else '⚪'}{_strip_html(t)}" for f, t in radar["b_items"]]),
        "",
        "=== 三、大盤 8 大核心指標完整明細 ==="
    ]

    br_line = (f"1. [廣度] 盤中權值溫度：漲{breadth.get('core_rt_up',0)}/跌{breadth.get('core_rt_dn',0)} (上漲比{breadth.get('core_rt_adv_pct',50):.1f}%)；全市場待14:30結算 | 類股：{sec_txt}"
               if breadth.get("is_stale_intraday") else f"1. [廣度] {breadth['src']}：上漲{breadth['up_cnt']:,}/下跌{breadth['dn_cnt']:,} (上漲比{breadth['adv_ratio_pct']:.1f}%) | 漲停{sb_limit_up}/跌停{sb_limit_dn} | 類股：{sec_txt}")

    us_up_short = "；".join([_strip_html(x) for x in us_data.get("top_up_sectors", [])[:2]])
    us_dn_short = "；".join([_strip_html(x) for x in us_data.get("bot_dn_sectors", [])[:1]])
    night_txt = _format_night_tx_segment(deriv, get_tw_now(), nq_chg_override=us_data.get("nq_chg"), as_html=False)
    basis_txt = f"{'正價差' if sb_basis>=0 else '逆價差'}{sb_basis:+.0f}點({deriv['basis_src']})" if deriv.get("basis_valid") else deriv["basis_src"]
    spec_txt = f" | 前五特法={deriv['top5_spec_net']:+,}/前十特法={deriv['top10_spec_net']:+,}口({deriv['top_spec_date']})" if deriv.get("top_spec_valid") else ""
    max_oi_txt = f" | MaxOI({deriv['max_oi_contract']}):天花板{deriv['max_oi_call_strike']:,}({deriv['max_oi_call_oi']:,}口)/地板{deriv['max_oi_put_strike']:,}({deriv['max_oi_put_oi']:,}口)" if deriv.get("max_oi_valid") else ""

    sc_c, sc_sum = deriv.get("spot_fini_consec", 0), deriv.get("spot_fini_consec_sum", deriv.get("spot_fini_5d_sum", 0.0))
    sc_lbl = f"連買{sc_c}日({sc_sum:+.0f}億)" if sc_c > 0 else (f"連賣{abs(sc_c)}日({sc_sum:+.0f}億)" if sc_c < 0 else "持平")
    spot_line = (f"現貨Sum-Check({deriv.get('twse_date','')}):外資{deriv['spot_fini_yi']:+.1f}億({sc_lbl}) + 投信{deriv['spot_sitc_yi']:+.1f}億 + 自營自行{deriv['spot_dealer_self_yi']:+.1f}億 + 避險{deriv['warrant_hedge_yi']:+.1f}億 = 合計{deriv['spot_total_yi']:+.1f}億"
                 if deriv.get("spot_valid") else f"自營避險={deriv['warrant_hedge_yi']:+.1f}億")

    lines.extend([
        br_line,
        f"2. [美股] {us_data['session_tag']} | VIX={vix_now:.2f}({vix_chg:+.1f}%) | 費半={sox_chg:+.2f}% | 台積ADR={tsm_chg:+.2f}%{tsm_ext} | 輝達={nvda_chg:+.2f}%{nvda_ext} | NQ={us_data.get('nq_chg',0):+.2f}% / ES={us_data.get('es_chg',0):+.2f}%({us_data.get('es_close',0):,.0f})",
        f"   ↳ 美股板塊：領漲[{us_up_short}] ｜ 領跌[{us_dn_short}]",
        f"3. [恐慌指數] {_strip_html(f_info['label'])}（維持率={f_info['s_maint']:.0f}/PCR={f_info['s_pcr']:.0f}/VIX={f_info['s_vix']:.0f}/小台權證={f_info['s_ret_w']:.0f}）",
        f"4. [融資信用] 餘額={deriv['margin_balance_yi']:,.1f}億(單日{deriv['margin_chg_yi']:+.1f}億 / 近2日{sb_margin_2d:+.1f}億・{sb_margin_2d_pct:+.1f}% / 30日減-{sb_margin_dd:.1f}%) | 維持率={sb_maint:.1f}% ({deriv['margin_src']})",
        f"5. [期貨特定法人與夜盤] ({deriv['fut_src']}) 大台外資={sb_fini_oi:+,}(ΔOI{deriv['fini_tx_oi_delta']:+,}/日盤{deriv['fini_tx_chg']:+,}) / 投信={deriv['sitc_tx_oi']:+,}(日{deriv['sitc_tx_chg']:+,}) / 自營={deriv['dealer_tx_oi']:+,}(日{deriv['dealer_tx_chg']:+,}) | 實質淨額={deriv['real_fini_oi']:+,}{spec_txt} | 小台散戶={deriv['retail_mtx_oi']:+,}(日{deriv['retail_mtx_chg']:+,}) | 價差={basis_txt} | {night_txt} | 外資空單成本約{deriv['est_short_cost']:,.0f}(連補{sb_consec_cover}日/3日{sb_cover_3d:+,}口)",
        f"6. [PCR/MaxOI/現貨] PCR={deriv['pc_ratio']:.1f}%{max_oi_txt} | {spot_line}",
        f"7. [微觀技術] 60分K:現價{fleet['twii_now']:,.0f} vs 5MA({fleet['k60_ma5']:,.0f})/20MA({fleet['k60_ma20']:,.0f})・J={fleet['k60_j']:.1f}({'↗' if fleet['k60_j']>=fleet['k60_j_prev'] else '↘'}) | 日KDJ:K={fleet['k9']:.1f}/D={fleet['d9']:.1f}/J={fleet['j9']:.1f} | 乖離:5MA({fleet['bias_5']:+.1f}%)/10MA({fleet['bias_10']:+.1f}%)/20MA({fleet['bias_20']:+.1f}%)",
        f"8. [債匯利差] 美債10Y={tnx_val:.2f}%({tnx_chg:+.2f}%) | {us_data.get('credit_spread_str','')} | USD/TWD={deriv['fx_now']:.3f}(日{deriv['fx_chg_ntd']:+.3f}・{'已止貶✅' if fx_stopped_dep else '續貶⚠️'})",
        "",
        "=== 四、事件倒數（14天內）與三情境推演 ===",
        "• 重大事件：" + (" ｜ ".join([f"{ev['日期']}({ev['倒數狀態']}){ev['_short']}" for ev in upcoming_ev_rows if ev["_days"] <= 14][:5]) or "未來 14 天無重大事件"),
        f"• 三情境機率：基準{forecast['probs']['base']}%(終點{forecast['paths']['base'][-1]:,.0f}) ／ 樂觀{forecast['probs']['bull']}%(終點{forecast['paths']['bull'][-1]:,.0f}) ／ 保守{forecast['probs']['bear']}%(終點{forecast['paths']['bear'][-1]:,.0f})"
    ])

    if bond_sentinels:
        lines.extend(["", "=== 五、水庫三：債市四大自動監控哨兵 ===", f"• {bond_sentinels['summary_line']}"])

    if include_portfolio and portfolio_summary:
        s_avg = portfolio_summary.get("avg_3711", 474.0)
        p400_txt = f"00400A 餘{portfolio_summary['lots_00400a']:.0f}張(@{portfolio_summary['cost_00400a']:.2f}・逢彈{portfolio_summary['t1_400']:.2f}~{portfolio_summary['t2_400']:.2f}轉00985B) | " if portfolio_summary["lots_00400a"] > 0 else "00400A 已清零 | "
        lines.extend([
            "",
            "=== 六、個人四水庫與鎖定紀律快照 ===",
            f"• 總資產={portfolio_summary['total_wan']:,.1f}萬（半導體曝險{portfolio_summary['semi_pct']:.1f}%）| 水庫一=40萬 | 水庫二(傳家寶)=台積4張+日月光3張+金融股息+AVGO/NVDA不動",
            f"• 水庫三(目標200萬)：00985B {portfolio_summary['lots_00985b']:.1f}張(@{portfolio_summary['cost_00985b']:.2f}) + 00933B {portfolio_summary.get('lots_00933b',0):.1f}張 + 00713 {portfolio_summary['lots_00713']:.1f}張（現值{portfolio_summary['r3_wan']:.1f}萬）",
            f"• 水庫四(機動彈藥)：純台幣現金 {portfolio_summary['twd_cash_wan']:.2f}萬（2張預算上限≦{portfolio_summary['twd_cash_wan']*10000/2000:.0f}元） + SGOV 30股 + USD ${portfolio_summary['usd_cash']:,.0f}（合計{portfolio_summary['r4_wan']:.1f}萬）",
            f"• 執行中部位：{p400_txt}日月光短單 {portfolio_summary['shares_3711_short']}股(@{s_avg:.1f}・現價{portfolio_summary['p_3711']:.1f}/5MA={portfolio_summary['ma5_3711']:.1f}/平衡價{portfolio_summary['be_3711']:.2f}；嚴禁500+追高，釣魚單鎖定{portfolio_summary['fish_3711']:.0f}元)"
        ])

    lines.append("[大盤全景包結尾核對完畢] ➔ 請執行【淘金客 v25.0 模式 0 大盤全景多空與四水庫／債市哨兵總控審查】（各段先結論後說明）！")
    return "\n".join(lines)

def _format_tdcc(d_rec: dict, live_tdcc_map: dict) -> str:
    code = d_rec["代碼"]
    if code in live_tdcc_map:
        w = live_tdcc_map[code]
        return f"400張:{w['p400']:.1f}%({w['d400']:+.2f}%)/千張:{w['p1000']:.1f}%({w['d1000']:+.2f}%)[{w['date']}]"
    return d_rec.get("大戶400/千張", "N/A")

def _resolve_day_trade(code: str, raw_dt: str, margin_sbl_map: dict = None) -> str:
    if margin_sbl_map and code in margin_sbl_map and margin_sbl_map[code].get("exact_dt_str"):
        return margin_sbl_map[code]["exact_dt_str"]
    if raw_dt and (not str(raw_dt).startswith("0.0%")):
        return str(raw_dt)
    return "N/A(請聯網補查)"

def build_mode_a_guard_package_v244(codes: list[str], source_label: str, batch_label: str, all_details: dict, outpost_line: str, now_str: str, live_tdcc_map: dict, margin_sbl_map: dict = None) -> str:
    valid_codes = [c for c in codes[:6] if c in all_details]
    lines = [f"【淘金客 v25.0｜模式A 守門員快篩批次包（來源：{source_label}｜{batch_label}｜實附 {len(valid_codes)} 檔，核對無漏檔）】({now_str})", outpost_line]
    for idx, c in enumerate(valid_codes, 1):
        r = all_details[c]
        up_sh = f"{r['上影線%']:.1f}%" if pd.notna(r.get("上影線%")) else "N/A"
        dt_show = _resolve_day_trade(c, r.get("當沖率", ""), margin_sbl_map)
        mkt_s = r.get("市場別", "(上市)" if r.get("_mkt", "TW") == "TW" else "(上櫃) ⚠️上櫃流動性控倉")
        rates_s = r.get("三率(毛/營/淨)", "N/A")
        lines.append(
            f"{idx}. {c} {r['_raw_name']} {mkt_s} [{r.get('_risk_alert', '正常')}] | {r['現價']}({r['今日%']:+.2f}%/3日{r['3日%']:+.2f}%) | 三率:{rates_s} | 型態:{r.get('_pattern_tag', r['短線屬性'])} | "
            f"DMI:{r['DMI方向雙檢']} J={r['日J值']} | 20MA扣抵:{r.get('20MA扣抵', 'N/A')} | BIAS10={r['BIAS10']} | 量比{r['5MA量比']}x/上影{up_sh}/位階{r['收盤位階']}/當沖{dt_show} | "
            f"OBV:{r['OBV趨勢']}/RS20D:{r['RS20D%']}/法人:{r.get('_inst_sum', r['法人動向'])}/大戶:{_format_tdcc(r, live_tdcc_map)} | ATR%={r['ATR%波動尺']} | 月營收YoY={r['單月營收YoY']}"
        )
    lines.append(f"[批次結尾核對：實附 {len(valid_codes)} 檔完畢] ➔ 請執行【淘金客 v25.0 模式A 守門員快篩】：納入低毛利(<10%)與上櫃控倉審查，集中聯網補查六大死因並點名進模式B之 Top 1～2！")
    return "\n".join(lines)

def build_mode_b_deep_package_v244(
    codes: list[str], all_details: dict, price_dict_all: dict, outpost_line: str, now_str: str,
    live_tdcc_map: dict, holding_notes: dict, is_yellow_red: bool,
    margin_sbl_map: dict = None, pair_swap_note: str = ""
) -> str:
    target_codes = codes[:5]
    pe_note = "【黃/紅燈殺估值期：嚴審產業P/E天花板】" if is_yellow_red else ""
    mode_title = f"決選 PK 比賽深檢戰情包（共 {len(target_codes)} 檔同場競技" if len(target_codes) >= 2 else "單股深度健檢戰情包（1 檔"
    lines = [f"【淘金客 v25.0｜模式B {mode_title}，核對無漏檔）】({now_str})", outpost_line]

    if len(target_codes) >= 2:
        lines.append("=== 📊 多股橫向 PK 快速對照表 ===")
        for idx, c in enumerate(target_codes, 1):
            r = all_details.get(c)
            if r:
                dt_show = _resolve_day_trade(c, r.get("當沖率", ""), margin_sbl_map)
                pe_s = f"{r['本益比(PE)']:.1f}x" if pd.notna(r.get("本益比(PE)")) else "N/A"
                mkt_s = r.get("市場別", "(上市)" if r.get("_mkt", "TW") == "TW" else "(上櫃)")
                lines.append(f"PK{idx}. {c} {r['_raw_name']} {mkt_s} | 現價{r['現價']}({r['今日%']:+.2f}%) | 三率:{r.get('三率(毛/營/淨)','N/A')} | DMI:{r['DMI方向雙檢']} | 20MA扣抵:{r.get('20MA扣抵','N/A')} | 當沖:{dt_show} | 法人:{r.get('_inst_sum', r['法人動向'])} | PE:{pe_s}")
            else:
                p_inf = price_dict_all.get(c, {})
                lines.append(f"PK{idx}. {c} {p_inf.get('name', c)} | 現價{p_inf.get('close', 'N/A')}({p_inf.get('chg_pct', 0):+.2f}%)")
        if pair_swap_note:
            lines.append(f"⚖️ 雙股比價換股折價分析：{pair_swap_note}")

    for idx, c in enumerate(target_codes, 1):
        r = all_details.get(c)
        hold_s = holding_notes.get(c, "空手新單（無既有部位）")
        m_sbl_str = (margin_sbl_map or {}).get(c, {}).get("margin_str", "N/A (交由淘金客聯網補查)")
        if r:
            mkt = r.get("_mkt", "TW")
            mkt_s = r.get("市場別", "(上市)" if mkt == "TW" else "(上櫃) ⚠️上櫃流動性控倉")
            anti_trap = fetch_single_stock_anti_trap(c, mkt)
            r_trend = f" [{anti_trap['rates_trend']}]" if anti_trap.get("rates_trend") else ""
            up_sh = f"{r['上影線%']:.1f}%" if pd.notna(r.get("上影線%")) else "N/A"
            eps_s = f"{r['預估EPS(元)']:.2f}元" if pd.notna(r.get("預估EPS(元)")) else "N/A"
            pe_s = f"{r['本益比(PE)']:.1f}x" if pd.notna(r.get("本益比(PE)")) else "N/A"
            dt_show = _resolve_day_trade(c, r.get("當沖率", ""), margin_sbl_map)
            lines.extend([
                f"=== 決選標的 {idx}：{c} {r['_raw_name']} {mkt_s} ===",
                f"• 持倉帳務與排雷：{hold_s} ｜ 處置/注意/除息：{r.get('_risk_alert', '待查')}",
                f"• 報價與賽道：現價={r['現價']}({r['今日%']:+.2f}% / 3日{r['3日%']:+.2f}%) | 型態={r.get('_pattern_tag', r['短線屬性'])} | 穀倉={r['穀倉賽道與防墊料']} | CB={r.get('_cb', '➖')}",
                f"• 財報三率與防墊料：三率(毛/營/淨)={r.get('三率(毛/營/淨)','N/A')}{r_trend} | 季報防墊料={anti_trap.get('note', 'N/A')}",
                f"• 完整均線與城堡：5MA={r['_ma5']} / 10MA={r['_ma10']} / 20MA={r['_ma20']}({r.get('20MA扣抵', 'N/A')}) / 60MA={r['_ma60']} / 120MA={r['_ma120']} / 240MA={r['_ma240']} | 點線面:週10MA={r.get('_w_ma10','N/A')} / 月5MA={r.get('_m_ma5','N/A')} | 天花板={r['天花板(反壓)']} / 鋼鐵防線={r['鋼鐵防線']} / 停損={r['極限停損']}",
                f"• DMI、乖離與波動：{r['DMI方向雙檢']} | 日J={r['日J值']} | BIAS10={r['BIAS10']} / BIAS20={r['BIAS20']} | ATR%={r['ATR%波動尺']}",
                f"• 假突破三合一：5MA量比={r['5MA量比']}x (昨量比{r['昨日量比']}x / 預估{r['預估量(張)']:,}張) | 上影線%={up_sh} | 收盤位階={r['收盤位階']} | 當沖率={dt_show}",
                f"• 量能與籌碼動能：OBV={r['OBV趨勢']} | RS20D={r['RS20D%']} | 法人Sum-Check={r.get('_inst_sum', r['法人動向'])} | 大戶={_format_tdcc(r, live_tdcc_map)}",
                f"• 資券借四位階：{m_sbl_str}",
                f"• 營收與估值：月營收YoY={r['單月營收YoY']} / 累計YoY={r['累計年營收YoY']} | 預估EPS={eps_s} / PE={pe_s} ({r['估值診斷']}){pe_note}"
            ])
        else:
            p_inf = price_dict_all.get(c, {})
            mkt = p_inf.get("market", "TW")
            anti_trap = fetch_single_stock_anti_trap(c, mkt)
            dt_show = _resolve_day_trade(c, p_inf.get("day_trade_str", ""), margin_sbl_map)
            lines.extend([
                f"=== 決選標的 {idx}：{c} {p_inf.get('name', c)} ({'上市' if mkt=='TW' else '上櫃'}) ===",
                f"• 持倉帳務狀態：{hold_s}",
                f"• 基礎報價：現價={p_inf.get('close', 'N/A')}({p_inf.get('chg_pct', 0):+.2f}%) | 成交量={p_inf.get('vol_lots', 0):,}張 | 當沖率={dt_show}",
                f"• 籌碼、資券借與防墊料：大戶={_format_tdcc({'代碼': c, '大戶400/千張': 'N/A'}, live_tdcc_map)} | 資券借={m_sbl_str} | 季報防墊料={anti_trap.get('note', 'N/A')}"
            ])

    pk_tail = f" ※本次傳入 {len(target_codes)} 檔同場 PK，請於最末段強制附加【決選優先排序表】排出第 1～{len(target_codes)} 順位！" if len(target_codes) >= 2 else ""
    lines.append(f"[深檢結尾核對：實附 {len(target_codes)} 檔完畢] ➔ 請執行【淘金客 v25.0 模式B 模組 20 雙核深檢】（各支柱先結論後說明）：集中聯網補查主動ETF踩踏、法說催化劑與分點家數差！{pk_tail}")
    return "\n".join(lines)
