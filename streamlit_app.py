import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import datetime
import pytz
import requests
import urllib3

# 關閉 SSL 憑證警告（確保證交所與櫃買中心 OpenAPI 連線順暢）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(
    page_title="台股每日戰情室 v31.5",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ==========================================
# 模組 1：台灣時間定錨與 U 型微笑量能推估引擎
# ==========================================
def get_tw_now():
    tz = pytz.timezone("Asia/Taipei")
    return datetime.datetime.now(tz)

def get_volume_multiplier():
    """盤中 U 型微笑曲線量能推估倍率（盤後與假日自動定錨為 1.0）"""
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
    # U 型權重校正：早盤量能佔比高，避免線性放大過度高估
    if elapsed <= 15:
        mult = (270.0 / elapsed) * 0.42
    elif elapsed <= 60:
        mult = (270.0 / elapsed) * 0.58
    elif elapsed <= 150:
        mult = (270.0 / elapsed) * 0.78
    elif elapsed <= 240:
        mult = (270.0 / elapsed) * 0.90
    else:
        mult = 270.0 / elapsed
    return round(max(1.0, min(mult, 12.0)), 2), f"盤中即時推估 (U型倍率 {mult:.2f}x)"

# ==========================================
# 模組 2：證交所/櫃買中心 OpenAPI 與集保大戶、月營收直連
# ==========================================
@st.cache_data(ttl=600, show_spinner=False)
def fetch_official_market_data():
    """抓取證交所與櫃買中心最新全市場日成交資訊、集保400張大戶、月營收YoY"""
    headers = {"User-Agent": "Mozilla/5.0"}
    price_dict = {}
    tdcc_dict = {}
    rev_dict = {}

    # 1. 證交所全市場日收盤行情 (STOCK_DAY_ALL)
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=10, verify=False)
        if r.status_code == 200:
            for row in r.json():
                code = str(row.get("Code", "")).strip()
                if len(code) == 4 and code.isdigit():
                    try:
                        c_price = float(str(row.get("ClosingPrice", "0")).replace(",", ""))
                        chg_str = str(row.get("Change", "0")).replace(",", "").replace("+", "").replace("X", "").strip()
                        chg_val = float(chg_str) if chg_str not in ["", "-", "--"] else 0.0
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

    # 2. 櫃買中心上櫃日收盤行情
    try:
        r_tpex = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", headers=headers, timeout=10, verify=False)
        if r_tpex.status_code == 200:
            for row in r_tpex.json():
                code = str(row.get("SecuritiesCompanyCode", "")).strip()
                if len(code) == 4 and code.isdigit():
                    try:
                        c_price = float(str(row.get("Close", "0")).replace(",", ""))
                        chg_str = str(row.get("Change", "0")).replace(",", "").replace("+", "").strip()
                        chg_val = float(chg_str) if chg_str not in ["", "-", "--", "除息", "除權"] else 0.0
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

    # 3. TDCC 集保 400 張以上大戶比例（嚴格過濾級距 12~15，排除級距 16 合計項避免破百 Bug）
    try:
        r_tdcc = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap11_L", headers=headers, timeout=10, verify=False)
        if r_tdcc.status_code == 200:
            temp_tdcc = {}
            for row in r_tdcc.json():
                code = str(row.get("證券代號", row.get("公司代號", ""))).strip()
                lvl = str(row.get("持股分級", "")).strip()
                pct_str = str(row.get("占集保庫存數比例(%)", row.get("佔集保庫存數比例(%)", "0"))).replace(",", "")
                if lvl in ["12", "13", "14", "15"]:
                    try:
                        temp_tdcc[code] = temp_tdcc.get(code, 0.0) + float(pct_str)
                    except Exception:
                        pass
            for k, v in temp_tdcc.items():
                if 0 < v <= 100.0:
                    tdcc_dict[k] = round(v, 1)
    except Exception:
        pass

    # 4. 上市櫃最新月營收 YoY (%)
    for url in [
        "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
        "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"
    ]:
        try:
            r_rev = requests.get(url, headers=headers, timeout=10, verify=False)
            if r_rev.status_code == 200:
                for row in r_rev.json():
                    code = str(row.get("公司代號", "")).strip()
                    yoy_str = str(row.get("去年同月增減(%)", "0")).replace(",", "").strip()
                    try:
                        rev_dict[code] = round(float(yoy_str), 1)
                    except Exception:
                        pass
        except Exception:
            pass

    return price_dict, tdcc_dict, rev_dict

# ==========================================
# 模組 3：核心高動能科技與重電種子資料庫
# ==========================================
CORE_WATCHLIST = {
    "2330": ("台積電", "晶圓與封測", "TW"), "2454": ("聯發科", "IC設計與IP", "TW"),
    "2379": ("瑞昱", "IC設計與IP", "TW"), "3034": ("聯詠", "IC設計與IP", "TW"),
    "8016": ("矽創", "IC設計與IP", "TW"), "3035": ("智原", "IC設計與IP", "TW"),
    "3661": ("世芯-KY", "IC設計與IP", "TW"), "3443": ("創意", "IC設計與IP", "TW"),
    "3529": ("力旺", "IC設計與IP", "TWO"), "5274": ("信驊", "IC設計與IP", "TWO"),
    "5269": ("祥碩", "IC設計與IP", "TW"), "4966": ("譜瑞-KY", "IC設計與IP", "TWO"),
    "3533": ("嘉澤", "高速傳輸與連接器", "TW"), "3653": ("健策", "AI散熱與均熱片", "TW"),
    "3017": ("奇鋐", "AI散熱與均熱片", "TW"), "3324": ("雙鴻", "AI散熱與均熱片", "TWO"),
    "2382": ("廣達", "AI伺服器與組裝", "TW"), "3231": ("緯創", "AI伺服器與組裝", "TW"),
    "6669": ("緯穎", "AI伺服器與組裝", "TW"), "2317": ("鴻海", "AI伺服器與組裝", "TW"),
    "2308": ("台達電", "電源與儲能", "TW"), "3711": ("日月光投控", "晶圓與封測", "TW"),
    "2449": ("京元電子", "晶圓與封測", "TW"), "3264": ("欣銓", "晶圓與封測", "TWO"),
    "6187": ("萬潤", "CoWoS與設備", "TWO"), "3583": ("辛耘", "CoWoS與設備", "TW"),
    "3131": ("弘塑", "CoWoS與設備", "TWO"), "2059": ("川湖", "伺服器導軌與機殼", "TW"),
    "8210": ("勤誠", "伺服器導軌與機殼", "TW"), "2383": ("台光電", "PCB與CCL高頻材料", "TW"),
    "6274": ("台燿", "PCB與CCL高頻材料", "TWO"), "2368": ("金像電", "PCB與CCL高頻材料", "TW"),
    "3081": ("聯亞", "CPO與光通訊", "TWO"), "4979": ("華星光", "CPO與光通訊", "TWO"),
    "3163": ("波若威", "CPO與光通訊", "TWO"), "3363": ("上詮", "CPO與光通訊", "TWO"),
    "1513": ("中興電", "重電與綠能電纜", "TW"), "1519": ("華城", "重電與綠能電纜", "TW"),
    "1503": ("士電", "重電與綠能電纜", "TW"), "1514": ("亞力", "重電與綠能電纜", "TW"),
    "1609": ("大亞", "重電與綠能電纜", "TW"), "3558": ("神準", "網通與光通訊", "TWO"),
    "2345": ("智邦", "網通與光通訊", "TW"), "6285": ("啟碁", "網通與光通訊", "TW")
}

# ==========================================
# 模組 4：技術指標演算核心 (日/週/月 K、KD、MACD、布林、城堡點位)
# ==========================================
def compute_kd(highs, lows, closes, n=9):
    k_val, d_val = 50.0, 50.0
    for i in range(len(closes)):
        start_idx = max(0, i - n + 1)
        hh = np.max(highs[start_idx:i+1])
        ll = np.min(lows[start_idx:i+1])
        rsv = ((closes[i] - ll) / (hh - ll) * 100.0) if (hh != ll) else 50.0
        k_val = (2.0 / 3.0) * k_val + (1.0 / 3.0) * rsv
        d_val = (2.0 / 3.0) * d_val + (1.0 / 3.0) * k_val
    return round(k_val, 1), round(d_val, 1)

def compute_macd(closes):
    s = pd.Series(closes)
    ema12 = s.ewm(span=12, adjust=False).mean()
    ema26 = s.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    osc = dif - dea
    return dif.values, dea.values, osc.values

@st.cache_data(ttl=300, show_spinner=False)
def fetch_and_analyze_fleet():
    """抓取大盤 (^TWII)、0050正2 (00631L.TW)、0050 (0050.TW) 真實歷史與現價"""
    tickers = ["^TWII", "00631L.TW", "0050.TW"]
    data = yf.download(tickers, period="2y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)

    def extract_series(tkr):
        df_t = data[tkr].dropna( subset=["Close"] )
        return df_t

    df_twii = extract_series("^TWII")
    df_2x = extract_series("00631L.TW")
    df_1x = extract_series("0050.TW")

    c_twii = df_twii["Close"].values.astype(float)
    h_twii = df_twii["High"].values.astype(float)
    l_twii = df_twii["Low"].values.astype(float)

    twii_now = round(float(c_twii[-1]), 1)
    twii_prev = float(c_twii[-2]) if len(c_twii) > 1 else twii_now
    twii_chg_pct = round((twii_now - twii_prev) / twii_prev * 100, 2)

    ma20 = round(float(np.mean(c_twii[-20:])), 1)
    ma60 = round(float(np.mean(c_twii[-60:])), 1)
    ma240 = round(float(np.mean(c_twii[-240:])), 1) if len(c_twii) >= 240 else round(float(np.mean(c_twii)), 1)
    std20 = float(np.std(c_twii[-20:]))
    bb_upper = round(ma20 + 2.0 * std20, 1)
    bb_lower = round(ma20 - 2.0 * std20, 1)

    bias_20 = round((twii_now - ma20) / ma20 * 100, 2)
    bias_60 = round((twii_now - ma60) / ma60 * 100, 2)
    k9, d9 = compute_kd(h_twii[-60:], l_twii[-60:], c_twii[-60:])
    _, _, osc_twii = compute_macd(c_twii)
    macd_hist = round(float(osc_twii[-1]), 1)

    p_2x = round(float(df_2x["Close"].values[-1]), 2)
    p_2x_prev = float(df_2x["Close"].values[-2]) if len(df_2x) > 1 else p_2x
    p_2x_chg = round((p_2x - p_2x_prev) / p_2x_prev * 100, 2)

    p_1x = round(float(df_1x["Close"].values[-1]), 2)
    p_1x_prev = float(df_1x["Close"].values[-2]) if len(df_1x) > 1 else p_1x
    p_1x_chg = round((p_1x - p_1x_prev) / p_1x_prev * 100, 2)

    return {
        "twii_now": twii_now, "twii_chg_pct": twii_chg_pct,
        "ma20": ma20, "ma60": ma60, "ma240": ma240,
        "bb_upper": bb_upper, "bb_lower": bb_lower,
        "bias_20": bias_20, "bias_60": bias_60,
        "k9": k9, "d9": d9, "macd_hist": macd_hist,
        "p_2x": p_2x, "p_2x_chg": p_2x_chg,
        "p_1x": p_1x, "p_1x_chg": p_1x_chg,
        "date_str": df_twii.index[-1].strftime("%Y-%m-%d")
    }

@st.cache_data(ttl=600, show_spinner=False)
def scan_all_stocks():
    """執行七大實戰名將與長線波段雙核掃描"""
    price_dict, tdcc_dict, rev_dict = fetch_official_market_data()
    vol_mult, vol_note = get_volume_multiplier()

    # 合併種子池與全市場當日帶量突破強勢股
    candidates = dict(CORE_WATCHLIST)
    for code, info in price_dict.items():
        if code not in candidates and info["vol_lots"] >= 2500 and info["chg_pct"] >= 3.0 and info["close"] >= 25.0:
            candidates[code] = (info["name"], "全市場動態破繭", info["market"])
            if len(candidates) >= 75:
                break

    yf_map = {f"{c}.{'TW' if m == 'TW' else 'TWO'}": c for c, (_, _, m) in candidates.items()}
    yf_tickers = list(yf_map.keys())

    raw_df = yf.download(yf_tickers, period="1y", interval="1d", progress=False, group_by="ticker", auto_adjust=False)

    list_tab2_launch = []
    list_tab2_pullback = []
    list_tab2_orbit = []
    list_tab3_long = []

    for yf_tkr, code in yf_map.items():
        try:
            df_s = raw_df[yf_tkr].dropna(subset=["Close"])
            if len(df_s) < 65:
                continue
            c = df_s["Close"].values.astype(float)
            h = df_s["High"].values.astype(float)
            l = df_s["Low"].values.astype(float)
            v = (df_s["Volume"].values.astype(float) / 1000.0)

            # 若官方 API 有最新收盤/盤中價則優先校正
            name, sector, _ = candidates[code]
            if code in price_dict:
                c[-1] = price_dict[code]["close"]
                if price_dict[code]["vol_lots"] > 0:
                    v[-1] = price_dict[code]["vol_lots"]

            curr_p = round(float(c[-1]), 2)
            prev_p = float(c[-2])
            chg_1d = round((curr_p - prev_p) / prev_p * 100, 2) if prev_p > 0 else 0.0
            chg_3d = round((curr_p - float(c[-4])) / float(c[-4]) * 100, 2) if len(c) >= 4 else chg_1d

            est_vol = int(round(v[-1] * vol_mult))
            ma5_vol = max(1.0, float(np.mean(v[-6:-1])))
            vol_ratio = round(est_vol / ma5_vol, 2)

            # 排除殭屍股（成交量 < 500張 且 股價 < 100元）
            if est_vol < 500 and curr_p < 100.0:
                continue

            ma5 = round(float(np.mean(c[-5:])), 2)
            ma10 = round(float(np.mean(c[-10:])), 2)
            ma20 = round(float(np.mean(c[-20:])), 2)
            ma60 = round(float(np.mean(c[-60:])), 2)
            ma120 = round(float(np.mean(c[-120:])), 2) if len(c) >= 120 else ma60

            std20 = float(np.std(c[-20:]))
            bb_up = round(ma20 + 2.0 * std20, 2)
            bb_bw = round((4.0 * std20 / ma20) * 100, 1) if ma20 > 0 else 99.0

            # 計算過去 5 日均線糾結度 (5/10/20MA 最大最小差距%)
            prev_ma5 = float(np.mean(c[-6:-1]))
            prev_ma10 = float(np.mean(c[-11:-1]))
            prev_ma20 = float(np.mean(c[-21:-1]))
            ma_tangle_pct = round((max(prev_ma5, prev_ma10, prev_ma20) - min(prev_ma5, prev_ma10, prev_ma20)) / prev_ma20 * 100, 2)

            k9, d9 = compute_kd(h[-60:], l[-60:], c[-60:])
            dif, dea, osc = compute_macd(c)

            # 週 K 與月 K 大趨勢判定 (模組 3 點線面)
            df_weekly = df_s["Close"].resample("W").last().dropna()
            w_c = df_weekly.values.astype(float)
            w_ma10 = float(np.mean(w_c[-10:])) if len(w_c) >= 10 else float(np.mean(w_c))
            _, _, w_osc = compute_macd(w_c) if len(w_c) >= 26 else (None, None, np.array([0.0]))
            w_bull = (curr_p >= w_ma10) and (float(w_osc[-1]) >= 0 if len(w_osc) > 0 else True)

            # 城堡四大點位 (模組 5)
            high_20d = round(float(np.max(h[-20:])), 2)
            castle_ceiling = round(max(bb_up, high_20d), 2)
            castle_month = ma20
            castle_iron = round(max(ma10, float(l[-1])), 2) if curr_p >= ma10 else ma20
            castle_stop = round(min(ma60, float(np.min(l[-20:]))), 2)

            tdcc_val = tdcc_dict.get(code, None)
            tdcc_str = f"{tdcc_val:.1f}%" if tdcc_val is not None else "盤後更新"
            yoy_val = rev_dict.get(code, None)
            yoy_str = f"{yoy_val:+.1f}%" if yoy_val is not None else "待公告"

            row_base = {
                "代碼": code,
                "股名": name,
                "族群": sector,
                "現價": curr_p,
                "今日%": chg_1d,
                "3日%": chg_3d,
                "5MA量比": vol_ratio,
                "預估量(張)": est_vol,
                "天花板(反壓)": castle_ceiling,
                "月線承接": castle_month,
                "鋼鐵防線": castle_iron,
                "極限停損": castle_stop,
                "400張大戶%": tdcc_str,
                "月營收YoY": yoy_str
            }

            # 七大實戰名將分類判讀 (排除空頭死貓跳：要求 curr_p >= ma20 且 ma20 >= ma60 * 0.98)
            if curr_p >= ma20 and ma20 >= ma60 * 0.98:
                # 1. 表一：階段 3(3) 旱地拔蔥 & 名將點火榜
                if chg_1d >= 2.0 and vol_ratio >= 1.35:
                    score = 85 + int(min(15, vol_ratio * 3 + chg_1d))
                    if ma_tangle_pct <= 3.5:
                        pattern = "🌸 瑞昱/矽創型：均線糾結後帶量長紅突破"
                        score += 3
                    elif osc[-1] > osc[-2] and osc[-2] < 0:
                        pattern = "💎 智原型：MACD綠柱收斂翻紅帶量上攻"
                        score += 2
                    elif bb_bw <= 14.0 and curr_p >= ma20:
                        pattern = "🔥 雙鴻/奇鋐型：布林壓縮旱地拔蔥點火"
                        score += 2
                    else:
                        pattern = "🚀 嘉澤/信驊型：多頭帶量突破頸線表態"
                    r1 = {"實戰優先分": min(100, score), "名將戰法診斷": pattern, **row_base}
                    list_tab2_launch.append(r1)

                # 2. 表二：N字洗盤蓄勢 & 拉回月線/十日線低接榜
                elif (-2.5 <= chg_1d <= 2.2) and (vol_ratio <= 0.85) and (curr_p >= ma20):
                    score = 82 + int((1.0 - vol_ratio) * 15)
                    pattern = "⚡ 神準/台光電型：N字量縮回測10/20MA有守"
                    r2 = {"蓄勢評分": min(98, score), "名將戰法診斷": pattern, **row_base}
                    list_tab2_pullback.append(r2)

                # 3. 表三：上升軌道推升 & 3日急拉警戒榜
                elif curr_p >= ma5 >= ma10 >= ma20:
                    if chg_3d >= 10.0 or curr_p >= bb_up:
                        pattern = "⚠️ 3日急拉/逼近布林上軌：切勿追高・分批鎖利"
                    else:
                        pattern = "🛡️ 台積/台達電型：沿5/10MA上升軌道穩步推升"
                    r3 = {"趨勢狀態": pattern, **row_base}
                    list_tab2_orbit.append(r3)

            # 4. 長期投資波段區 (週K多頭 + 站穩季線半年線 + 營收/大戶正向)
            if curr_p >= ma60 and ma20 >= ma60 and w_bull:
                l_score = 85
                if yoy_val and yoy_val > 0:
                    l_score += 8
                if tdcc_val and tdcc_val >= 50.0:
                    l_score += 7
                r_long = {
                    "波段總分": min(100, l_score),
                    "長線格局定性": "✅ 週K守10週線＋日線多頭排列 (長線保護短線)",
                    **row_base
                }
                list_tab3_long.append(r_long)

        except Exception:
            continue

    df_t1 = pd.DataFrame(list_tab2_launch).sort_values(by="實戰優先分", ascending=False) if list_tab2_launch else pd.DataFrame()
    df_t2 = pd.DataFrame(list_tab2_pullback).sort_values(by="蓄勢評分", ascending=False) if list_tab2_pullback else pd.DataFrame()
    df_t3 = pd.DataFrame(list_tab2_orbit).sort_values(by="3日%", ascending=False) if list_tab2_orbit else pd.DataFrame()
    df_long = pd.DataFrame(list_tab3_long).sort_values(by="波段總分", ascending=False) if list_tab3_long else pd.DataFrame()

    return df_t1, df_t2, df_t3, df_long, vol_note

# ==========================================
# 模組 5：Streamlit 戰情室主介面
# ==========================================
now_str = get_tw_now().strftime("%Y-%m-%d %H:%M:%S")
st.markdown("## 🏛️ 台股每日戰情室 v31.5 (Streamlit 雲端實戰版)")
st.caption(f"🕒 台灣時間定錨：{now_str} ｜ 數據源：證交所/櫃買中心 OpenAPI ＆ Yahoo Finance 真實K線")

# 將持倉設定直接放在主畫面可展開面板（解決手機版找不到左側欄與翻譯亂碼問題）
with st.expander("⚙️ 點此展開／調整【0050正2 ＋ 0050 母子艦隊持倉與成本參數】", expanded=False):
    c_in1, c_in2, c_in3, c_in4, c_in5 = st.columns(5)
    with c_in1:
        in_lots_2x = st.number_input("0050正2 持有張數", value=3.0, step=0.1, format="%.2f")
    with c_in2:
        in_cost_2x = st.number_input("0050正2 平均成本(元)", value=34.76, step=0.1, format="%.2f")
    with c_in3:
        in_lots_1x = st.number_input("0050 壓艙石張數", value=1.0, step=0.1, format="%.2f")
    with c_in4:
        in_cost_1x = st.number_input("0050 平均成本(元)", value=101.90, step=0.1, format="%.2f")
    with c_in5:
        in_cash_pool = st.number_input("已收割待買回現金池(元)", value=0.0, step=1000.0, format="%.0f")

if st.button("🔄 立即重新抓取最新盤況與報價"):
    st.cache_data.clear()

with st.spinner("📡 正在連線證交所與 Yahoo Finance 同步真實大盤與個股 K 線數據..."):
    fleet = fetch_and_analyze_fleet()
    df_launch, df_pullback, df_orbit, df_long, vol_note = scan_all_stocks()

# 計算母子艦隊真實市值與損益（已修復 f-string 格式語法 Bug）
twii_now = fleet["twii_now"]
p_2x = fleet["p_2x"]
p_1x = fleet["p_1x"]

val_2x = int(round(in_lots_2x * 1000 * p_2x))
cost_total_2x = int(round(in_lots_2x * 1000 * in_cost_2x))
pnl_2x = val_2x - cost_total_2x
pnl_2x_pct = round(((p_2x - in_cost_2x) / max(0.01, in_cost_2x)) * 100, 2)

val_1x = int(round(in_lots_1x * 1000 * p_1x))
cost_total_1x = int(round(in_lots_1x * 1000 * in_cost_1x))
pnl_1x = val_1x - cost_total_1x
pnl_1x_pct = round(((p_1x - in_cost_1x) / max(0.01, in_cost_1x)) * 100, 2)

total_fleet_val = val_2x + val_1x + int(in_cash_pool)
total_pnl = pnl_2x + pnl_1x
ratio_2x_pct = round((val_2x / max(1, total_fleet_val)) * 100, 1)
effective_leverage = round(((val_1x * 1.0 + val_2x * 2.0) / max(1, total_fleet_val)) * 100, 1)

tab1, tab2, tab3 = st.tabs([
    "📊 0050正2 ＋ 0050 母子艦隊滾雪球戰情室",
    "⚡ 短線操作精選區 (七大實戰名將)",
    "🏛️ 長期投資波段區 (雙守有基)"
])

with tab1:
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("#### 🏛️ 加權指數司令塔")
        st.metric(
            label=f"加權指數 ({fleet['date_str']})",
            value=f"{twii_now:,.1f} 點",
            delta=f"{fleet['twii_chg_pct']:+.2f}%"
        )
        kd_state = "金叉多頭" if fleet["k9"] >= fleet["d9"] else "死叉整理"
        st.caption(
            f"• 月線(20MA)：`{fleet['ma20']:,.1f}` (乖離 `{fleet['bias_20']:+.2f}%`)\n\n"
            f"• 季線(60MA)：`{fleet['ma60']:,.1f}` (乖離 `{fleet['bias_60']:+.2f}%`)\n\n"
            f"• 日KD：`K9={fleet['k9']}` / `D9={fleet['d9']}` ({kd_state}) ｜ MACD柱：`{fleet['macd_hist']:+.1f}`"
        )

    with col2:
        st.markdown("#### 🚀 0050正2 (00631L 子艦隊)")
        st.metric(
            label=f"00631L 現價 ({p_2x_chg_str := f'{fleet[\"p_2x_chg\"]:+.2f}%'})",
            value=f"{p_2x:,.2f} 元",
            delta=f"{pnl_2x:+,} 元 ({pnl_2x_pct:+.2f}%)"
        )
        st.caption(
            f"• 持有部位：`{in_lots_2x:.2f} 張` (均價 `{in_cost_2x:.2f} 元`)\n\n"
            f"• 當前市值：`{val_2x:,} 元` (佔總艦隊 `{ratio_2x_pct}%`)"
        )

    with col3:
        st.markdown("#### 🛡️ 0050 壓艙石 ＆ 總艦隊")
        st.metric(
            label=f"0050 現價 ({p_1x:,.2f} 元) ｜ 艦隊總市值",
            value=f"{total_fleet_val:,} 元",
            delta=f"總損益 {total_pnl:+,} 元"
        )
        st.caption(
            f"• 0050 損益：`{pnl_1x:+,} 元 ({pnl_1x_pct:+.2f}%)`\n\n"
            f"• 現金池：`{int(in_cash_pool):,} 元` ｜ 整體有效曝險：`{effective_leverage}%`"
        )

    st.markdown("---")

    # 動態計算大盤三層指標對應之 0050正2 目標價與滾張數效益 (Beta ≈ 2.0)
    def calc_target_2x(target_twii):
        pct_diff = (target_twii - twii_now) / twii_now
        return round(p_2x * (1.0 + pct_diff * 2.0), 2)

    t_bb_up = calc_target_2x(fleet["bb_upper"])
    t_ma20 = calc_target_2x(fleet["ma20"])
    t_ma60 = calc_target_2x(fleet["ma60"])
    t_ma240 = calc_target_2x(fleet["ma240"])

    # 若於布林上軌賣出 1 張正2 (扣除手續費與證交稅約 0.2425%)
    net_cash_harvest = int(round(t_bb_up * 1000 * (1.0 - 0.002425)))
    shares_at_ma20 = int(net_cash_harvest // max(1.0, t_ma20))
    shares_at_ma60 = int(net_cash_harvest // max(1.0, t_ma60))

    # 動態判斷今日艦隊戰略指令
    if twii_now >= fleet["bb_upper"]:
        st.warning(
            f"🔥 **【高檔收割警報】大盤已觸及布林上軌 ({fleet['bb_upper']:,.1f} 點)！**\n\n"
            f"• **行動指令**：建議逢高收割 **第 1 張機動 0050正2**（目標價約 **{t_bb_up:.2f} 元**），回收約 **{net_cash_harvest:,} 元** 轉入現金池，等待回測月線 `{t_ma20:.2f} 元` 買回滾大股數！"
        )
    elif twii_now >= fleet["ma20"]:
        st.info(
            f"☀️ **【多頭主升段續抱】大盤穩守月線 ({fleet['ma20']:,.1f} 點) 之上，全艦隊抱緊處理！**\n\n"
            f"• **行動指令**：手頭 `{in_lots_2x:.1f} 張 0050正2 ＋ {in_lots_1x:.1f} 張 0050` 請安心續抱（紀律 1/4：均線未破一律尊重）。\n"
            f"• **下一收割錨點**：當大盤攻抵布林上軌 **{fleet['bb_upper']:,.1f} 點**（對應正2約 **{t_bb_up:.2f} 元**）時，再執行第 1 張高檔鎖利。"
        )
    elif twii_now >= fleet["ma60"]:
        st.success(
            f"📈 **【良性洗盤回測區】大盤回測月線 ({fleet['ma20']:,.1f} 點) 與季線 ({fleet['ma60']:,.1f} 點) 區間！**\n\n"
            f"• **行動指令**：若現金池有先前高檔收割資金，可關注日 KD 金叉訊號分批買回正2（參考承接價 **{t_ma60:.2f} ~ {t_ma20:.2f} 元**），完成「高賣低買賺股數」循環！"
        )
    else:
        st.error(
            f"❄️ **【破季線防禦警戒】大盤跌破 60MA 季線 ({fleet['ma60']:,.1f} 點)！**\n\n"
            f"• **行動指令**：建議將子艦隊降載保留現金，嚴守長線年線大底 **{fleet['ma240']:,.1f} 點**（對應正2約 **{t_ma240:.2f} 元**）與 KD 落底金叉再行重裝回補。"
        )

    st.markdown("#### 🎯 大盤四層指標 ➔ 0050正2 (00631L) 波段收割與回補精算表 (真實點位連動)")
    df_t50_levels = pd.DataFrame([
        {
            "波段階段與大盤指標錨點": "🔥 階段1：高檔收割點 (大盤布林上軌)",
            "真實大盤點位": f"{fleet['bb_upper']:,.1f} 點",
            "0050正2 對應目標價": f"{t_bb_up:,.2f} 元",
            "艦隊操作指令": "逢高賣出第 1 張機動正2 鎖利",
            "滾雪球增股效益試算": f"淨回收約 {net_cash_harvest:,} 元現金入池"
        },
        {
            "波段階段與大盤指標錨點": "📈 階段2：多頭回測承接 (大盤20MA月線)",
            "真實大盤點位": f"{fleet['ma20']:,.1f} 點",
            "0050正2 對應目標價": f"{t_ma20:,.2f} 元",
            "艦隊操作指令": "月線有守且量縮止穩買回",
            "滾雪球增股效益試算": f"高檔賣1張於此買回 ➔ 變 {shares_at_ma20:,} 股 ({shares_at_ma20 - 1000:+,}股)"
        },
        {
            "波段階段與大盤指標錨點": "🛡️ 階段3：波段黃金買回點 (大盤60MA季線)",
            "真實大盤點位": f"{fleet['ma60']:,.1f} 點",
            "0050正2 對應目標價": f"{t_ma60:,.2f} 元",
            "艦隊操作指令": "季線有守且日 KD 低檔金叉買回",
            "滾雪球增股效益試算": f"高檔賣1張於此買回 ➔ 變 {shares_at_ma60:,} 股 ({shares_at_ma60 - 1000:+,}股)"
        },
        {
            "波段階段與大盤指標錨點": "💎 階段4：長線鐵血大底 (大盤240MA年線)",
            "真實大盤點位": f"{fleet['ma240']:,.1f} 點",
            "0050正2 對應目標價": f"{t_ma240:,.2f} 元",
            "艦隊操作指令": "歷史級國安防線・現金池全數重裝",
            "滾雪球增股效益試算": f"高檔賣1張於此買回 ➔ 變 {int(net_cash_harvest // max(1.0, t_ma240)):,} 股"
        }
    ])
    st.dataframe(df_t50_levels, use_container_width=True, hide_index=True)

with tab2:
    st.caption(f"⚡ 量能推估狀態：{vol_note} ｜ 城堡點位：天花板(20日高/布林上軌)、月線承接(20MA)、鋼鐵防線(10MA/紅K低)、極限停損(60MA)")
    
    st.markdown("#### 🔥 【表一：階段 3(3) 旱地拔蔥 ＆ 七大實戰名將點火榜】")
    if not df_launch.empty:
        st.dataframe(df_launch, use_container_width=True, hide_index=True)
    else:
        st.info("今日盤面暫無符合「帶量 > 1.35倍 ＋ 突破均線糾結」之點火標的，請保持耐心切勿追高（紀律 6：永遠只做看得懂的部分）。")

    st.markdown("#### 🎯 【表二：N字洗盤蓄勢 ＆ 拉回月線/十日線低接榜】")
    if not df_pullback.empty:
        st.dataframe(df_pullback, use_container_width=True, hide_index=True)
    else:
        st.info("今日暫無符合「量縮回測月線有守」之洗盤標的。")

    st.markdown("#### 🛡️ 【表三：上升軌道推升 ＆ 3日急拉警戒榜】")
    if not df_orbit.empty:
        st.dataframe(df_orbit, use_container_width=True, hide_index=True)
    else:
        st.info("今日暫無符合多頭軌道推升之標的。")

with tab3:
    st.markdown("#### 🏛️ 【長期投資波段區：週K守10週線 ＋ 日線多頭 ＋ 基本面/大戶雙守旗艦榜】")
    st.caption("篩選基準：嚴守模組 3「長線保護短線」原則（站穩 10 週均線、日線 20MA > 60MA、並標註 TDCC 400張大戶比例與最新月營收 YoY）")
    if not df_long.empty:
        st.dataframe(df_long, use_container_width=True, hide_index=True)
    else:
        st.info("目前無符合長線波段條件之標的。")
