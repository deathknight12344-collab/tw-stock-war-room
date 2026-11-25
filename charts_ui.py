import base64, re
import numpy as np
import pandas as pd

from config_events import clean_html, xml_esc, map_tw_theme_cluster
from data_engine import fetch_single_stock_anti_trap

# ==========================================
# 共用：SVG 響應式縮放包裝器（電腦滿版／手機可單指橫滑／支援 1.5x・2.0x 放大）
# ==========================================
def _wrap_svg_zoom(svg_xml: str, zoom_mode: str = "auto") -> str:
    b64_svg = base64.b64encode(svg_xml.encode("utf-8")).decode("utf-8")
    if zoom_mode == "1.5x":
        img_style = "width:150%; min-width:1100px; height:auto; display:block;"
        hint = "🔍 目前為 1.5x 放大模式（左右滑動檢視細節）"
        cls_img = "svg-fixed-img"
    elif zoom_mode == "2.0x":
        img_style = "width:200%; min-width:1500px; height:auto; display:block;"
        hint = "🔍 目前為 2.0x 超大模式（左右滑動檢視細節）"
        cls_img = "svg-fixed-img"
    else:
        img_style = "width:100%; height:auto; display:block;"
        hint = "📱 手機版可單指左右滑動看大圖，或點上方倍率鍵放大"
        cls_img = "svg-auto-img"
    return (
        f'<div class="svg-zoom-hint" style="font-size:12.5px; color:#93c5fd; margin:2px 0 5px 2px;">{hint}</div>'
        f'<div class="svg-zoom-box"><img class="{cls_img}" src="data:image/svg+xml;base64,{b64_svg}" style="{img_style}" /></div>'
    )

# ==========================================
# 1. 模組 0（對接淘金客 v24.4）：三階燈號 × 四軌落底 × 政策逃命波
# ==========================================
def evaluate_crash_and_bottom_radar_v244(
    twii_now, twii_chg_pct, twii_ma60, twii_ma120, twii_ma240, twii_30d_dd_pct, sp500_below_ma200,
    vix_now, sox_dd_pct, fini_oi, margin_dd_pct, margin_2d_chg_yi, margin_2d_pct, maint_pct,
    bottom_4track: dict, limit_up_cnt: int, adv_ratio_pct: float,
    basis_pts, basis_valid: bool, basis_stabilized: bool, consec_cover_days, cover_3d_sum,
    fx_stopped_dep, limit_down_cnt, had_limit_down_100,
    ban_active, days_since_ban, consec_above_ma10
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
    b1_ok = (margin_dd_pct >= twii_30d_dd_pct) or (margin_2d_chg_yi <= -500.0) or (margin_2d_pct <= -7.0) or (maint_pct <= 150.0)

    hit_2c_override = (twii_chg_pct >= 4.0) and ((limit_up_cnt >= 100) or (adv_ratio_pct >= 85.0))
    b2_ok = bool(bottom_4track.get("passed", False) or hit_2c_override)
    b2_detail = bottom_4track.get("detail", "")
    if hit_2c_override and not bottom_4track.get("passed", False):
        b2_detail = f"2c無量鎖漲停/廣度豁免(單日+{twii_chg_pct:.1f}%・漲停{limit_up_cnt}家/上漲比{adv_ratio_pct:.0f}%)"
    precursor_txt = bottom_4track.get("precursor", "")

    b3_ok = bool(basis_valid and (basis_pts < 0) and (basis_rate_pct >= 0.8) and basis_stabilized)
    if not basis_valid:
        b3_desc = "期貨跨日待盤後同步（暫不計分，可於側邊欄覆寫）"
    elif basis_pts >= 0:
        b3_desc = f"轉正價差 <b>{basis_pts:+.0f}點</b>（無恐慌逆價差）"
    else:
        b3_desc = f"逆價差 <b>{basis_pts:+.0f}點 / {basis_rate_pct:.2f}%</b> ｜ {'已止穩不擴大✅' if basis_stabilized else '未確認止穩'}"

    b4_ok = (consec_cover_days >= 3) and (cover_3d_sum >= 10000) and fx_stopped_dep

    b_items = [
        (b1_ok, f"① 融資真斷頭（近2日 <b>{margin_2d_chg_yi:+.1f}億 / {margin_2d_pct:+.1f}%</b> ｜ 維持率 <b>{maint_pct:.1f}%</b>）"),
        (b2_ok, f"② 四軌價量／廣度反轉（<b>{b2_detail}</b>）"),
        (b3_ok, f"③ 恐慌逆價差率 ≧ 0.8% 收斂（{b3_desc}）"),
        (b4_ok, f"④ 外資空單連3日回補&gt;1萬口(ΔOI)＋台幣止貶（連補 <b>{consec_cover_days}日 / {cover_3d_sum:+,}口</b> ｜ 台幣{'已止貶✅' if fx_stopped_dep else '續貶⚠️'}）"),
        (had_limit_down_100 and (limit_down_cnt < 20), f"⑤ 恐慌廣度收斂（當日跌停 <b>{limit_down_cnt} 家</b> {'< 20家達標' if (had_limit_down_100 and limit_down_cnt < 20) else '尚未收斂至<20家'}）")
    ]
    b_count = sum(1 for flag, _ in b_items if flag)
    bottom_confirmed = (b_count >= 4)
    is_policy_escape_wave = bool(ban_active and (0 <= days_since_ban <= 14) and (not bottom_confirmed) and (consec_above_ma10 < 3))

    if crash_warning_on and (not bottom_confirmed):
        master_state, lamp_short = "RED_LOCKED", "🔴紅燈鎖死"
        master_badge = f"🔴 紅燈未落底【總開關鎖死】（預警 {w_count}/5・落底 {b_count}/5）"
        master_action = "嚴禁動用新現金追買或開槓桿！僅核准三項豁免：(a) 股轉債轉入00985B/00713；(b) 比價折價≧10~15%換龍頭；(c) 淨降倉換強新穀倉1龍頭(獨立分帳先守平衡價，站上5MA後守5MA)。"
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
# 2. 滾動風險事件視覺化水平時間軸 SVG 圖（支援手機滑動與倍率縮放）
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
# 3. v63.1 校準版：左側 40 日真實歷史 ＋ 右側三情境扇形預測 SVG 圖（右端零截斷 ＋ 事件三層防重疊 ＋ 可下載放大）
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

    # 修正右端截斷：預測終點收在 x=985，右邊保留 215px 完整容納點位標籤！
    x_left, x_today, x_right = 68, 560, 995
    y_top, y_bot = 68, 370

    def to_y(val: float) -> int:
        ratio = (val - y_lo) / max(1.0, (y_hi - y_lo))
        return int(round(y_bot - ratio * (y_bot - y_top)))

    grid_svg = []
    for k in range(5):
        gv = y_lo + (y_hi - y_lo) * (k / 4.0)
        gy = to_y(gv)
        grid_svg.append(f'<line x1="{x_left}" y1="{gy}" x2="{x_right}" y2="{gy}" stroke="#1e293b" stroke-width="1" stroke-dasharray="4,4"/>')
        grid_svg.append(f'<text x="{x_left - 8}" y="{gy + 4}" fill="#94a3b8" font-family="sans-serif" font-size="12" text-anchor="end">{gv:,.0f}</text>')

    ref_svg = []
    for lbl, rval, rcol in [
        (f"月線20MA {fleet['ma20']:,.0f}", fleet["ma20"], "#f59e0b"),
        (f"年線240MA {fleet['ma240']:,.0f}", fleet["ma240"], "#a855f7"),
        (f"月20MA鐵板 {fleet['m_ma20']:,.0f}", fleet["m_ma20"], "#00e676")
    ]:
        if y_lo <= rval <= y_hi:
            ry = to_y(rval)
            ref_svg.append(f'<line x1="{x_left}" y1="{ry}" x2="{x_right}" y2="{ry}" stroke="{rcol}" stroke-width="1.3" stroke-opacity="0.72" stroke-dasharray="6,4"/>')
            ref_svg.append(f'<text x="{x_left + 6}" y="{ry - 5}" fill="{rcol}" font-family="sans-serif" font-size="12" font-weight="bold">{xml_esc(lbl)}</text>')

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

    # 上方重大事件標籤：改為 3 層垂直錯開，徹底根除碰撞重疊
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
    y_badge = max(y_top + 20, min(y_bot - 22, y_now - 28))

    # 右側三終點標籤強制防重疊並完整落於 1200 畫布內
    end_items = sorted([
        ("bull", to_y(pu[-1]), "#69f0ae", f"🟢樂觀({probs['bull']}%) {pu[-1]:,.0f}"),
        ("base", to_y(pb[-1]), "#fcd34d", f"🟡基準({probs['base']}%) {pb[-1]:,.0f}"),
        ("bear", to_y(pd_p[-1]), "#ff8a80", f"🔴保守({probs['bear']}%) {pd_p[-1]:,.0f}")
    ], key=lambda t: t[1])
    end_labels_svg = []
    prev_ly = y_top - 10
    for _, raw_y, col_t, txt_t in end_items:
        ly = max(raw_y, prev_ly + 26)
        ly = min(y_bot - 6, ly)
        prev_ly = ly
        end_labels_svg.append(f'<text x="{fx_xs[-1] + 14}" y="{ly + 4}" fill="{col_t}" font-family="sans-serif" font-size="13.5" font-weight="900">{xml_esc(txt_t)}</text>')

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 422" width="100%" height="auto">
<rect width="1200" height="422" rx="12" fill="#09111a" stroke="#334155" stroke-width="2"/>
<rect x="{x_left}" y="{y_top}" width="{x_today - x_left}" height="{y_bot - y_top}" fill="#0f172a"/>
<rect x="{x_today}" y="{y_top}" width="{x_right - x_today}" height="{y_bot - y_top}" fill="#131c31"/>
<text x="{(x_left + x_today)//2}" y="44" fill="#38bdf8" font-family="sans-serif" font-size="15" font-weight="900" text-anchor="middle">◀ 過去 {n_h} 日真實歷史走勢（實線）</text>
<text x="{(x_today + x_right)//2}" y="44" fill="#fcd34d" font-family="sans-serif" font-size="15" font-weight="900" text-anchor="middle">未來 1～4 週全浮動三情境預測（虛線） ▶</text>
{''.join(grid_svg)}
{''.join(ref_svg)}
{''.join(ev_marks_svg)}
<polygon points="{fan_pts}" fill="#f59e0b" fill-opacity="0.10"/>
<line x1="{x_today}" y1="54" x2="{x_today}" y2="{y_bot + 6}" stroke="#ffd54f" stroke-width="2.2" stroke-dasharray="5,4"/>
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
<circle cx="{x_today}" cy="{y_now}" r="14" fill="{dot_col}" fill-opacity="0.32"/>
<circle cx="{x_today}" cy="{y_now}" r="7" fill="{dot_col}" stroke="#ffffff" stroke-width="2.5"/>
<rect x="{x_today - 82}" y="{y_badge - 15}" width="164" height="28" rx="6" fill="{dot_col}" stroke="#09111a" stroke-width="2"/>
<text x="{x_today}" y="{y_badge + 4}" fill="#ffffff" font-family="sans-serif" font-size="14" font-weight="900" text-anchor="middle">📍今日 {tw_now:,.0f}點</text>
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
• <b>📍 時間單向推進基準</b>：左側為過去 {len(hist_c)} 日真實收盤（高 <b>{max(hist_c):,.0f}</b> ／ 低 <b>{min(hist_c):,.0f}</b>）；紅點固定於今日 <b>{tw_now:,.0f} 點</b>，右側依 ATR({fleet['atr_14_pts']:,.0f}點)、均線、外資 ΔOI 與事件自動推演（🟡基準 {probs['base']}% ／ 🟢樂觀 {probs['bull']}% ／ 🔴保守 {probs['bear']}%）<br>
• <b>總開關狀態</b>：{radar['master_badge']} ➔ <span style="color:#ffd54f;">{radar['master_action']}</span>{policy_warn}{precursor_b}
</div>
{_wrap_svg_zoom(svg_xml, zoom_mode)}
</div>
""")

def build_forecast_mobile_cards_html(forecast: dict) -> str:
    """手機友善直式三情境作戰卡片（免橫向拉表格即可一眼看清完整劇本）"""
    cards = []
    for r in forecast.get("table_rows", []):
        title = r["預測情境（圖中線色）"]
        b_col = "#fbbf24" if "基準" in title else ("#00e676" if "樂觀" in title else "#ff4d4f")
        cards.append(
            f"<div style='background:#0f172a; border-left:5px solid {b_col}; border-radius:10px; padding:12px 14px; margin-bottom:10px; color:#f8fafc; font-size:14.2px; line-height:1.65;'>"
            f"<div style='font-size:15.5px; font-weight:900; color:{b_col}; margin-bottom:4px;'>{title} ｜ 機率 {r['動態機率']}</div>"
            f"• <b>預測路徑</b>：{r['未來 1～4 週預測路徑與目標區']}<br>"
            f"• <b>確認分界</b>：{r['確認／轉折分界']}<br>"
            f"• <b>實戰操作</b>：<span style='color:#fde047;'>{r['四水庫實戰操作']}</span></div>"
        )
    return clean_html("".join(cards))

# ==========================================
# 4. 盤面族群格式化 ＋ 8 大【先結論、後數據、附數字含意】指標面板 (已修正微調 3：60分K高檔鈍化判讀)
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
    hot_divs = [f"<div style='margin:4px 0; padding:5px 9px; background:rgba(0,0,0,0.3); border-radius:6px; border-left:3px solid #ff8a80; font-size:13.8px;'><b style='color:#ffd54f;'>🔥 {r['theme']}</b> (均 <b style='color:#ff8a80;'>{r['avg_chg']:+.2f}%</b>｜量 {r['blended_yod']:.2f}x)<br>&nbsp;&nbsp;↳ {r['leaders_html']}</div>" for _, r in df_th.head(3).iterrows()]
    cold_divs = [f"<div style='margin:4px 0; padding:5px 9px; background:rgba(0,0,0,0.3); border-radius:6px; border-left:3px solid #69f0ae; font-size:13.8px;'><b style='color:#e2e8f0;'>❄️ {r['theme']}</b> (均 {r['avg_chg']:+.2f}%｜量 {r['blended_yod']:.2f}x)<br>&nbsp;&nbsp;↳ {r['leaders_html']}</div>" for _, r in df_th.tail(2).sort_values(by="avg_chg").iterrows()]
    return {"hot_html": "".join(hot_divs), "cold_html": "".join(cold_divs), "top1_name": "、".join(df_th["theme"].head(2).tolist())}

def format_sector_bold_html(sec_list: list[str], is_up: bool = True) -> str:
    if not sec_list: return ""
    col, icon = ("#ff8a80", "🔥 美股領漲") if is_up else ("#69f0ae", "❄️ 美股領跌")
    out = []
    for s in sec_list:
        sb = re.sub(r"([\u4e00-\u9fa5A-Za-z]+\([A-Z]+\))", r"<b style='color:#fff;'>\1</b>", s).replace("指標股：", "<b style='color:#ffd54f;'>指標股：</b>")
        out.append(f"<div style='margin:4px 0; padding:5px 9px; background:rgba(0,0,0,0.3); border-radius:6px; border-left:3px solid {col}; font-size:13.8px;'><b style='color:{col};'>{icon}</b> {sb}</div>")
    return "".join(out)

def build_8_indicator_panels_html(
    fleet: dict, us_data: dict, deriv: dict, breadth: dict, scan_data, tw_hot_sec_info: dict,
    sb_fini_oi: int, sb_margin_2d: float, sb_margin_2d_pct: float, sb_margin_dd: float,
    sb_maint: float, sb_basis: float, sb_consec_cover: int, sb_cover_3d: int,
    sb_limit_up: int, sb_limit_dn: int, vix_now: float, vix_chg: float, tnx_val: float, tnx_chg: float,
    sox_chg: float, tsm_chg: float, tsm_ext: str, nvda_chg: float, nvda_ext: str, fx_now: float, fx_stopped_dep: bool
) -> str:
    sox_c = "#ff4d4f" if sox_chg >= 0 else "#00e676"
    tsm_c = "#ff4d4f" if tsm_chg >= 0 else "#00e676"
    nvda_c = "#ff4d4f" if nvda_chg >= 0 else "#00e676"

    # 1. 台股廣度與熱門族群
    adv_r = breadth["adv_ratio_pct"]
    c1_concl = "🚀 全面普漲／廣度推力爆發" if (adv_r >= 85 or sb_limit_up >= 100) else ("🟢 多方家數佔優" if adv_r >= 55 else ("🚨 空方恐慌普跌" if adv_r <= 25 else "⚖️ 盤面分化震盪"))
    if scan_data:
        c1_body = f"• <b>全市場廣度（{breadth['src']}）</b>：上漲 <b>{breadth['up_cnt']:,}</b> ／ 下跌 <b>{breadth['dn_cnt']:,}</b>（上漲比 <b>{adv_r:.1f}%</b>｜漲停 <b>{sb_limit_up}</b> ／ 跌停 <b>{sb_limit_dn}</b>）<br>{tw_hot_sec_info['hot_html']}{tw_hot_sec_info['cold_html']}"
    else:
        c1_body = (
            f"• <b>全市場廣度（{breadth['src']}）</b>：上漲 <b style='color:#ff8a80;'>{breadth['up_cnt']:,} 家</b> ／ 下跌 <b style='color:#69f0ae;'>{breadth['dn_cnt']:,} 家</b>（上漲比 <b>{adv_r:.1f}%</b>）<br>"
            f"• <b>極端鎖死家數</b>：漲停 <b style='color:#ff8a80;'>{sb_limit_up} 家</b> ／ 跌停 <b style='color:#69f0ae;'>{sb_limit_dn} 家</b><br>"
            f"• 💡 點擊頂部【🚀 啟動全台個股池 (Layer 2)】即可展開 15 大熱門爆量族群領頭羊。"
        )
    c1_meaning = "📖 數字含意：上漲比 ≧85% 或漲停 ≧100家 觸發『2025/04 廣度推力豁免』（無量鎖漲停亦判反轉）；跌停由破百縮至 &lt;20家 代表恐慌殺盤結束。"

    # 2. 美股 VIX / 費半 / ADR
    c2_concl = "🚨 恐慌爆表（VIX ≧ 30）" if vix_now >= 30 else ("🔥 半導體強彈修復" if sox_chg >= 2.0 else ("⚠️ 避險情緒偏高（VIX 20~29）" if vix_now >= 20 else "☀️ 美股風險偏好穩健"))
    c2_body = (
        f"• <b>美股氛圍</b>：{us_data['session_tag']} ｜ VIX <b>{vix_now:.2f}</b>({vix_chg:+.1f}%)<br>"
        f"• <b>AI 核心連動</b>：費半 <b style='color:{sox_c};'>{sox_chg:+.2f}%</b> ｜ 台積ADR <b style='color:{tsm_c};'>{tsm_chg:+.2f}%{tsm_ext}</b> ｜ 輝達 <b style='color:{nvda_c};'>{nvda_chg:+.2f}%{nvda_ext}</b>"
        f"{format_sector_bold_html(us_data['top_up_sectors'], True)}{format_sector_bold_html(us_data['bot_dn_sectors'], False)}"
    )
    c2_meaning = "📖 數字含意：VIX ≧30 屬股災恐慌區、20~29 屬高波動警戒、&lt;20 屬常態多頭安全區；費半與台積ADR直接定調次日台股開盤跳空方向。"

    # 3. 台股散戶綜合恐慌指數
    s_maint = float(np.clip((170.0 - sb_maint) / 35.0 * 100.0, 0, 100))
    s_pcr = float(np.clip((120.0 - deriv["pc_ratio"]) / 90.0 * 100.0, 0, 100))
    s_vix = float(np.clip((vix_now - 13.0) / 25.0 * 100.0, 0, 100))
    s_ret_w = float(np.clip(50.0 + (-deriv["retail_mtx_oi"] / 150.0) + (-deriv["warrant_hedge_yi"] / 15.0), 0, 100))
    fear_idx = round(0.35 * s_maint + 0.25 * s_pcr + 0.20 * s_vix + 0.20 * s_ret_w, 1)
    if fear_idx >= 65: c3_concl, c3_col = f"🔥 極度恐慌清洗區 ({fear_idx:.1f}分・醞釀報復強彈)", "#00e676"
    elif fear_idx >= 45: c3_concl, c3_col = f"🟡 偏空警戒震盪區 ({fear_idx:.1f}分・籌碼換手中)", "#fbbf24"
    elif fear_idx >= 28: c3_concl, c3_col = f"☀️ 籌碼健康平衡區 ({fear_idx:.1f}分・適合順勢選股)", "#38bdf8"
    else: c3_concl, c3_col = f"⚠️ 市場過度貪婪區 ({fear_idx:.1f}分・慎防高檔拉回)", "#ff8a80"
    c3_body = (
        f"• <b>四因子合成</b>：維持率恐慌(35%)={s_maint:.0f} ｜ PCR偏空(25%)={s_pcr:.0f} ｜ VIX恐慌(20%)={s_vix:.0f} ｜ 小台散戶＆權證(20%)={s_ret_w:.0f}<br>"
        f"• <b>目前總分</b>：<b style='color:{c3_col}; font-size:17px;'>{fear_idx:.1f} / 100 分</b>（分數越高代表散戶越恐慌、浮額洗得越乾淨）"
    )
    c3_meaning = "📖 數字含意：&gt;60分 屬散戶認輸斷頭區（反指標易出波段強彈）；35~55分 為平衡選股區；&lt;28分 代表散戶瘋狂追價須防隨時修正。"

    # 4. 融資維持率與大盤融資曲線
    c4_concl = "💎 進入真斷頭甜美清洗區（維持率 ≦150% 或 融資減幅追上大盤）" if (sb_maint <= 150 or sb_margin_dd >= fleet["twii_30d_dd_pct"]) else ("🟡 融資去槓桿進行中" if sb_margin_2d < 0 else "⚠️ 融資餘額偏高、籌碼仍待沉澱")
    c4_body = (
        f"• <b>動態融資維持率</b>：<b style='color:#ffd54f; font-size:16px;'>{sb_maint:.1f}%</b>（{deriv['margin_src']}）<br>"
        f"• <b>上市融資餘額</b>：最新 <b>{deriv['margin_balance_yi']:,.1f} 億</b>（單日 <b>{deriv['margin_chg_yi']:+.1f}億</b> ｜ 近2日 <b>{sb_margin_2d:+.1f}億 / {sb_margin_2d_pct:+.1f}%</b> ｜ 30日減幅 <b>-{sb_margin_dd:.1f}%</b>）"
    )
    c4_meaning = "📖 數字含意：整戶維持率跌破 150%（純上市個股破 135%~145%）會強制引發券商追繳斷頭；當『30日融資減幅% ≧ 大盤跌幅%』代表散戶槓桿徹底洗淨。"

    # 5. 大小台期貨動向＆外資佈空成本
    if sb_fini_oi < -80000: c5_concl = f"🚨 外資極限重空 ({sb_fini_oi:+,}口・空單大於8萬口壓制)"
    elif sb_fini_oi < -60000: c5_concl = f"🔴 外資股災避險空單 ({sb_fini_oi:+,}口)"
    elif sb_fini_oi < -40000: c5_concl = f"🟡 外資警戒避險偏空 ({sb_fini_oi:+,}口)"
    else: c5_concl = f"🟢 外資期貨水位中性偏多 ({sb_fini_oi:+,}口)"
    b_txt = f"{'正價差' if sb_basis >= 0 else '逆價差'} <b>{sb_basis:+.0f}點</b> ({deriv['basis_src']})" if deriv.get("basis_valid") else f"<b>{deriv['basis_src']}</b>"
    c5_body = (
        f"• <b>大台指 ({deriv['fut_src']})</b>：外資 <b>{sb_fini_oi:+,}</b> (含夜盤ΔOI <b>{deriv['fini_tx_oi_delta']:+,}</b>／日盤交易 {deriv['fini_tx_chg']:+,}) ｜ 投信 <b>{deriv['sitc_tx_oi']:+,}</b>({deriv['sitc_tx_chg']:+,}) ｜ 自營 <b>{deriv['dealer_tx_oi']:+,}</b>({deriv['dealer_tx_chg']:+,})<br>"
        f"• <b>實質對沖與小台散戶</b>：外資+投信實質淨額 <b>{deriv['real_fini_oi']:+,}口</b> ｜ 小台散戶未平倉 <b>{deriv['retail_mtx_oi']:+,}口</b>(日{deriv['retail_mtx_chg']:+,}) ｜ 價差：{b_txt}<br>"
        f"• <b>外資佈空成本推估</b>：均價約 <b>{deriv['est_short_cost']:,.0f}點</b>（獲利 {deriv['short_profit_pts']:+,.0f}點）｜ 回補連補 <b>{sb_consec_cover}日</b> / 3日累計 <b>{sb_cover_3d:+,}口</b>"
    )
    c5_meaning = "📖 數字含意：外資大台淨空單 &gt;4萬警戒、&gt;6萬股災、&gt;8萬極限對沖；『ΔOI含夜盤』與『日盤交易』常因夜盤回補而不同；小台散戶淨口數為負代表散戶看空反指標偏多。"

    # 6. 選擇權 PCR ＋ 自營商權證避險
    pcr_v, wh_v = deriv["pc_ratio"], deriv["warrant_hedge_yi"]
    c6_concl = "🟢 選擇權下檔撐盤強 (PCR > 120%)" if pcr_v >= 120 else ("🔴 上檔買權反壓沉重 (PCR < 80%)" if pcr_v < 80 else "⚖️ 選擇權多空拉鋸 (PCR 80%~120%)")
    c6_body = (
        f"• <b>全月份選擇權 P/C Ratio</b>：<b style='color:#ffd54f; font-size:16px;'>{pcr_v:.1f}%</b><br>"
        f"• <b>自營商權證 Delta 避險</b>：<b style='color:{'#ff8a80' if wh_v >= 0 else '#69f0ae'};'>{wh_v:+.2f} 億元</b>（日期 {deriv.get('twse_date') or '最新'}）"
    )
    c6_meaning = "📖 數字含意：PCR = 賣權OI ÷ 買權OI，&lt;80%代表莊家大賣買權壓天花板（偏空）、&gt;120%代表下檔鐵板支撐強；權證避險大賣逾 -35億 代表避險/停損宣洩。"

    # 7. 60分K微觀扳機 ＆ 日KDJ動能空間（修正微調 3：加入 0.3% 容錯與高檔鈍化判讀）
    near_60_ma5 = (fleet["twii_now"] >= fleet["k60_ma5"] * 0.997)
    above_day_ma5 = (fleet["twii_now"] >= fleet["ma5"])
    above_60_ma20 = (fleet["twii_now"] >= fleet["k60_ma20"])
    if near_60_ma5 and above_day_ma5:
        c7_concl = "🔥 60分K高檔鈍化震盪（穩守日5MA，防短線過熱回測）" if fleet["k60_j"] >= 95.0 else "🟢 60分K與日5MA同步站穩（短線右側止跌訊號亮起）"
    elif above_day_ma5 and above_60_ma20:
        c7_concl = "🟢 穩守日5MA與60分20MA（高檔換手整理中）"
    elif near_60_ma5 or above_60_ma20:
        c7_concl = "🟡 60分K反彈測試中（尚未站回日5MA）"
    else:
        c7_concl = "🔴 60分K弱勢蓋壓（短線仍受均線壓制）"
    c7_body = (
        f"• <b>60分K微觀結構</b>：現價 <b>{fleet['twii_now']:,.0f}</b> vs 60分5MA <b>{fleet['k60_ma5']:,.0f}</b> ／ 60分20MA <b>{fleet['k60_ma20']:,.0f}</b> ｜ 60分J值 <b>{fleet['k60_j']:.1f}</b> ({'↗勾頭' if fleet['k60_j'] >= fleet['k60_j_prev'] else '↘下彎'})<br>"
        f"• <b>日線 KDJ 與乖離</b>：日K <b>{fleet['k9']:.1f}</b> ／ 日D <b>{fleet['d9']:.1f}</b> ／ 日J <b>{fleet['j9']:.1f}</b> ｜ 乖離率：5MA(<b>{fleet['bias_5']:+.1f}%</b>) / 10MA(<b>{fleet['bias_10']:+.1f}%</b>) / 20MA(<b>{fleet['bias_20']:+.1f}%</b>)"
    )
    c7_meaning = "📖 數字含意：止跌須先站上『60分5MA』再收復『日5MA』；60分J &gt;95 代表短線急漲高檔鈍化防隔日回測，日J &lt;10 屬極度超賣醞釀反彈。"

    # 8. 宏觀美債 10Y (^TNX) ＆ 新台幣匯率利差雷達
    if tnx_val >= 5.0 and fx_now >= 32.5: c8_concl = "🚨 雙紅燈外資提款警戒（美債 ≧5% ＋ 台幣 ≧32.5）"
    elif fx_stopped_dep and tnx_val < 5.0: c8_concl = "🟢 債匯壓力緩解（美債 <5% 且台幣止貶，助外資回補）"
    else: c8_concl = "🟡 債匯高檔警戒（美債偏高或台幣續貶，嚴控估值）"
    c8_body = (
        f"• <b>美國 10 年期公債殖利率 (^TNX)</b>：<b style='color:{'#ff8a80' if tnx_val >= 5.0 else '#69f0ae'}; font-size:16px;'>{tnx_val:.2f}%</b> ({tnx_chg:+.2f}%)<br>"
        f"• <b>美元兌新台幣 (USD/TWD)</b>：現匯 <b>{fx_now:.3f}</b>（日變動 {deriv['fx_chg_ntd']:+.3f} ｜ 5MA={deriv['fx_ma5']:.3f} ／ 20MA={deriv['fx_ma20']:.3f} ｜ <b>{'已止貶✅' if fx_stopped_dep else '續貶⚠️'}</b>）"
    )
    c8_meaning = "📖 數字含意：^TNX 突破 5.0% 會直接壓縮科技股本益比；台幣當日走貶（日變動&gt;0）且貶破 32.5 代表外資匯出提款，系統自動置頂『新穀倉1』與『政策內需防彈衣』。"

    return clean_html(f"""
    <div class="war-hero-box" style="border-left-color:#38bdf8;">
    <div style="font-size:18px; font-weight:900; color:#ffd54f; margin-bottom:8px;">
    🧭 【大盤 8 大核心指標全景儀表板】（先講結論 ➔ 數據明細 ➔ 📖 數字含意與判讀基準）
    </div>
    <div class="war-dim-grid">
    <div class="war-dim-item" style="border-left:5px solid #ff4d4f;"><span class="war-dim-concl">1. 🔥 台股廣度／熱門族群 ➔ {c1_concl}</span>{c1_body}<span class="meaning-note">{c1_meaning}</span></div>
    <div class="war-dim-item" style="border-left:5px solid #f59e0b;"><span class="war-dim-concl">2. 🌎 美股 VIX＆費半板塊 ➔ {c2_concl}</span>{c2_body}<span class="meaning-note">{c2_meaning}</span></div>
    <div class="war-dim-item" style="border-left:5px solid #a855f7;"><span class="war-dim-concl">3. 😱 散戶綜合恐慌指數 ➔ {c3_concl}</span>{c3_body}<span class="meaning-note">{c3_meaning}</span></div>
    <div class="war-dim-item" style="border-left:5px solid #00e676;"><span class="war-dim-concl">4. 🛡️ 融資維持率與曲線 ➔ {c4_concl}</span>{c4_body}<span class="meaning-note">{c4_meaning}</span></div>
    <div class="war-dim-item" style="border-left:5px solid #ffd54f;"><span class="war-dim-concl">5. 📊 大小台期貨＆外資成本 ➔ {c5_concl}</span>{c5_body}<span class="meaning-note">{c5_meaning}</span></div>
    <div class="war-dim-item" style="border-left:5px solid #38bdf8;"><span class="war-dim-concl">6. 🎯 選擇權PCR＋權證避險 ➔ {c6_concl}</span>{c6_body}<span class="meaning-note">{c6_meaning}</span></div>
    <div class="war-dim-item" style="border-left:5px solid #ec4899;"><span class="war-dim-concl">7. ⚡ 60分K扳機＆日KDJ ➔ {c7_concl}</span>{c7_body}<span class="meaning-note">{c7_meaning}</span></div>
    <div class="war-dim-item" style="border-left:5px solid #14b8a6;"><span class="war-dim-concl">8. 💵 美債10Y＆台幣利差 ➔ {c8_concl}</span>{c8_body}<span class="meaning-note">{c8_meaning}</span></div>
    </div>
    </div>
    """)

# ==========================================
# 5. 淘金客 v24.4 雙模式戰情數據包組裝器 (防截斷核對 ＋ 持倉連動)
# ==========================================
def build_market_outpost_line_v244(fleet: dict, radar: dict, deriv: dict, forecast: dict, tnx_val: float, fx_now: float, ban_active: bool, next_ev: dict) -> str:
    tw = fleet["twii_now"]
    pos_60, pos_120, pos_240 = (">=" if tw >= fleet["ma60"] else "<"), (">=" if tw >= fleet["ma120"] else "<"), (">=" if tw >= fleet["ma240"] else "<")
    ban_tag = "限空令生效中(凍結借券賣減看多加分)" if ban_active else "無限空令"
    esc_tag = "/⚠️政策逃命波" if radar["is_policy_escape_wave"] else ""
    b2_tag = f"四軌落底:{'✅' + radar['b2_detail'] if radar['b2_ok'] else '未達標'}"
    pre_tag = f" | {radar['precursor_txt']}" if radar.get("precursor_txt") else ""
    ev_tag = f"近3日事件:{next_ev['日期']}{next_ev['_short']}(倒數{next_ev['_days']}天)" if (next_ev and next_ev["_days"] <= 3) else (f"下個事件:{next_ev['日期']}{next_ev['_short']}" if next_ev else "無近事件")
    pr = forecast["probs"]
    return (
        f"[大盤前哨] 加權={tw:,.1f}({fleet['twii_chg_pct']:+.2f}%・{fleet['date_str']}) | "
        f"位階:{pos_60}60MA({fleet['ma60']:,.0f})/{pos_120}120MA({fleet['ma120']:,.0f})/{pos_240}240MA({fleet['ma240']:,.0f})/月20MA({fleet['m_ma20']:,.0f}) | "
        f"模組0燈號:{radar['lamp_short']}(預警{radar['w_count']}/5・落底{radar['b_count']}/5・{b2_tag}{esc_tag}){pre_tag} | "
        f"未來預測機率:基準{pr['base']}%/樂觀{pr['bull']}%/保守{pr['bear']}% | "
        f"大台外資OI={deriv['fini_tx_oi']:+,}(ΔOI{deriv['fini_tx_oi_delta']:+,}) | {ban_tag} | PCR={deriv['pc_ratio']:.1f}% | ^TNX={tnx_val:.2f}% | TWD={fx_now:.3f} | {ev_tag}"
    )

def _format_tdcc(d_rec: dict, live_tdcc_map: dict) -> str:
    code = d_rec["代碼"]
    if code in live_tdcc_map:
        w = live_tdcc_map[code]
        return f"400張:{w['p400']:.1f}%({w['d400']:+.2f}%)/千張:{w['p1000']:.1f}%({w['d1000']:+.2f}%)[{w['date']}]"
    return d_rec.get("大戶400/千張", "N/A")

def build_mode_a_guard_package_v244(codes: list[str], source_label: str, batch_label: str, all_details: dict, outpost_line: str, now_str: str, live_tdcc_map: dict) -> str:
    valid_codes = [c for c in codes[:6] if c in all_details]
    lines = [f"【淘金客 v24.4｜模式A 守門員快篩批次包（來源：{source_label}｜{batch_label}｜實附 {len(valid_codes)} 檔，核對無漏檔）】({now_str})", outpost_line]
    for idx, c in enumerate(valid_codes, 1):
        r = all_details[c]
        up_sh = f"{r['上影線%']:.1f}%" if pd.notna(r.get("上影線%")) else "N/A"
        lines.append(
            f"{idx}. {c} {r['_raw_name']} | {r['現價']}({r['今日%']:+.2f}%/3日{r['3日%']:+.2f}%) | 型態:{r.get('_pattern_tag', r['短線屬性'])} | "
            f"DMI:{r['DMI方向雙檢']} J={r['日J值']} | BIAS10={r['BIAS10']} | 量比{r['5MA量比']}x/上影{up_sh}/位階{r['收盤位階']}/當沖{r['當沖率']} | "
            f"OBV:{r['OBV趨勢']}/RS20D:{r['RS20D%']}/大戶:{_format_tdcc(r, live_tdcc_map)} | ATR%={r['ATR%波動尺']} | 月營收YoY={r['單月營收YoY']}"
        )
    lines.append(f"[批次結尾核對：實附 {len(valid_codes)} 檔完畢] ➔ 請執行【淘金客 v24.4 模式A 守門員快篩】：集中聯網補查六大死因，以簡表裁決通過／觀察／淘汰並點名進模式B之 Top 1～2！")
    return "\n".join(lines)

def build_mode_b_deep_package_v244(codes: list[str], all_details: dict, price_dict_all: dict, outpost_line: str, now_str: str, live_tdcc_map: dict, holding_notes: dict, is_yellow_red: bool) -> str:
    target_codes = codes[:3]
    pe_note = "【黃/紅燈殺估值期：請嚴審產業P/E天花板】" if is_yellow_red else ""
    lines = [f"【淘金客 v24.4｜模式B 決選深檢戰情包（實附 {len(target_codes)} 檔，核對無漏檔）】({now_str})", outpost_line]
    for idx, c in enumerate(target_codes, 1):
        r = all_details.get(c)
        hold_s = holding_notes.get(c, "空手新單（無既有部位）")
        if r:
            mkt = r.get("_mkt", "TW")
            anti_trap = fetch_single_stock_anti_trap(c, mkt)
            up_sh = f"{r['上影線%']:.1f}%" if pd.notna(r.get("上影線%")) else "N/A"
            eps_s = f"{r['預估EPS(元)']:.2f}元" if pd.notna(r.get("預估EPS(元)")) else "N/A"
            pe_s = f"{r['本益比(PE)']:.1f}x" if pd.notna(r.get("本益比(PE)")) else "N/A"
            lines.extend([
                f"=== 決選標的 {idx}：{c} {r['_raw_name']} ({mkt}) ===",
                f"• 持倉帳務狀態：{hold_s}",
                f"• 報價與賽道：現價={r['現價']}({r['今日%']:+.2f}% / 3日{r['3日%']:+.2f}%) | 型態={r.get('_pattern_tag', r['短線屬性'])} | 穀倉={r['穀倉賽道與防墊料']} | CB={r.get('_cb', '➖')}",
                f"• 完整均線與城堡：5MA={r['_ma5']} / 10MA={r['_ma10']} / 20MA={r['_ma20']} / 60MA={r['_ma60']} / 120MA={r['_ma120']} / 240MA={r['_ma240']} | 天花板={r['天花板(反壓)']} / 鋼鐵防線={r['鋼鐵防線']} / 停損={r['極限停損']}",
                f"• DMI、乖離與波動：{r['DMI方向雙檢']} | 日J={r['日J值']} | BIAS10={r['BIAS10']} / BIAS20={r['BIAS20']} | ATR%={r['ATR%波動尺']}",
                f"• 假突破三合一：5MA量比={r['5MA量比']}x (昨量比{r['昨日量比']}x / 預估{r['預估量(張)']:,}張) | 上影線%={up_sh} | 收盤位階={r['收盤位階']} | 當沖率={r['當沖率']}",
                f"• 量能與籌碼動能：OBV={r['OBV趨勢']} | RS20D={r['RS20D%']} | 法人={r['法人動向']} (自營商與Sum-Check請聯網補齊) | 大戶={_format_tdcc(r, live_tdcc_map)}",
                f"• 基本面與防墊料：最新月營收YoY={r['單月營收YoY']} / 累計YoY={r['累計年營收YoY']} | 預估EPS={eps_s} / PE={pe_s} ({r['估值診斷']}){pe_note} | 季報防墊料={anti_trap.get('note', 'N/A')}"
            ])
        else:
            p_inf = price_dict_all.get(c, {})
            mkt = p_inf.get("market", "TW")
            anti_trap = fetch_single_stock_anti_trap(c, mkt)
            lines.extend([
                f"=== 決選標的 {idx}：{c} {p_inf.get('name', c)} ({mkt}・尚未執行Layer2全掃) ===",
                f"• 持倉帳務狀態：{hold_s}",
                f"• 基礎報價：現價={p_inf.get('close', 'N/A')}({p_inf.get('chg_pct', 0):+.2f}%) | 成交量={p_inf.get('vol_lots', 0):,}張 | 當沖率={p_inf.get('day_trade_str', 'N/A')}",
                f"• 籌碼與防墊料：大戶={_format_tdcc({'代碼': c, '大戶400/千張': 'N/A'}, live_tdcc_map)} | 季報防墊料={anti_trap.get('note', 'N/A')} | 完整技術面=N/A(請先執行Layer2掃描或由淘金客聯網補查)"
            ])
    pk_tail = " ※本次傳入多檔，請於最末段強制附加【決選優先排序表】排出第 1～3 順位！" if len(target_codes) >= 2 else ""
    lines.append(f"[深檢結尾核對：實附 {len(target_codes)} 檔完畢] ➔ 請執行【淘金客 v24.4 模式B 模組 20 雙核深檢】：強制補搜處置新制、自營商Sum-Check、主動ETF踩踏、資券借與法說催化劑，獨立列出 App 訊號裁決、城堡各階具體價位與雙軌行動！{pk_tail}")
    return "\n".join(lines)
