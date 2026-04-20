from __future__ import annotations
from typing import Optional, List, Tuple, Dict
from collections import OrderedDict
import streamlit as st
import boto3
import pandas as pd
import openpyxl
from io import BytesIO
import plotly.graph_objects as go
import datetime
import re
import streamlit.components.v1 as components

# ── Constants ────────────────────────────────────────────────────────────────
BUCKET = "cmon-stage-backend-369568916817-ap-southeast-1-reports"
PREFIX = "Dashboard/"
REGION = "ap-southeast-1"

# Columns shown in HUD tables and heatmap (7D% dropped from display per spec)
DISPLAY_TF = ["1D%", "5D%", "1M%", "3M%", "6M%", "1Y%"]
RS_COLS    = ["RS 1D", "RS 5D", "RS 10D", "RS 20D"]

# Grid quadrant → (growth_arrow, inflation_arrow)
# G1 = Growth ↑, Inflation ↓  |  G2 = Growth ↑, Inflation ↑
# G3 = Growth ↓, Inflation ↑  |  G4 = Growth ↓, Inflation ↓
GRID_Q_MAP: Dict[int, Tuple[str, str]] = {
    1: ("↑", "↓"), 2: ("↑", "↑"), 3: ("↓", "↑"), 4: ("↓", "↓"),
}

# Compass quadrant → (liquidity_arrow, credit_arrow)
COMPASS_Q_MAP: Dict[int, Tuple[str, str]] = {
    1: ("↑", "↓"), 2: ("↑", "↑"), 3: ("↓", "↑"), 4: ("↓", "↓"),
}

# Clock quadrant colours for CLOCK tab
CLOCK_Q_COLOR: Dict[int, str] = {1: "#E87722", 2: "#00C851", 3: "#88DD44", 4: "#FF4444"}

# HUD groups: OrderedDict { display_name: (ticker_list, default_rs_denom_or_None) }
# None denom = no RS calculation for that group
HUD_GROUPS: "OrderedDict[str, Tuple[List[str], Optional[str]]]" = OrderedDict([
    ("US EQUITIES", (
        ["DJI", "SPX", "IXIC", "RUT", "VIX"],
        "SPX",
    )),
    ("INDEX ETF", (
        ["DIA", "SPY", "QQQ", "IWM"],
        "SPX",
    )),
    ("SECTOR ETF", (
        ["XLB", "XLI", "XLY", "XLC", "XLK", "XME", "XLRE", "XLP", "XLU",
         "XLE", "XOP", "XHB", "PBS", "PBJ", "PEJ", "TAN", "ICLN",
         "XLF", "KBE", "KRE", "KIE", "IAI", "XLV", "XHE",
         "IYT", "JETS", "BLOK", "SOCL", "SOXX", "ROBO", "SKYY",
         "FDN", "HACK", "CIBR", "KWEB", "MJ",
         "ARKK", "ARKG", "ARKW", "ARKF", "ARKQ", "IZRL"],
        "SPX",
    )),
    ("US INTEREST RATES", (
        ["US03MY", "US01Y", "US02Y", "US05Y", "US10Y", "US20Y", "US30Y", "MOVE"],
        None,
    )),
    ("BONDS ETF", (
        ["SHY", "IEF", "TLT", "TMF"],
        "SPX",
    )),
    ("SPREADS", (
        ["T10Y2Y", "T10Y3M"],
        None,
    )),
    ("RATES", (
        ["DFEDTARU", "FEDFUNDS", "CPIAUCSL", "GDP", "DRTSCILM"],
        None,
    )),
    ("COMMODITIES METALS", (
        ["DBC", "USO", "UNG", "GLD", "GDX", "GDXJ", "SLV", "SIL",
         "JJC", "CPER", "JJN", "WOOD", "SLX", "URA"],
        "USCI",
    )),
    ("COMMODITIES CONT.", (
        ["USCI", "USOIL", "NATGAS", "GOLD", "SILVER", "COPPER",
         "NICKEL", "LITHIUM", "SLX", "WOOD", "URANIUM", "COAL"],
        "USCI",
    )),
    ("AGRICULTURAL", (
        ["DBA", "WEAT", "SOYB", "CORN", "RICE", "CANE", "COTTON"],
        "DBA",
    )),
    ("COUNTRY ETF", (
        ["KWEB", "FXI", "EWJ", "EWZ", "EWT", "EWG", "EWH", "EWI",
         "EWW", "EWU", "PIN", "IDX", "VNM", "EWM", "EIDO", "EPHE",
         "EWY", "EWA", "EWC", "EWS", "EWP", "EWL", "EZA", "INDA"],
        "SPX",
    )),
    ("FOREIGN RATES", (
        ["JP10Y", "CN10Y", "HK10Y", "PH10Y", "EU10Y", "GB10Y",
         "FR10Y", "DE10Y", "IT10Y", "ES10Y", "SG10Y", "KR10Y"],
        None,
    )),
    ("FX", (
        ["USDPHP", "USDJPY", "USDCNY", "USDAUD", "USDEUR", "USDGBP",
         "USDCHF", "USDSGD", "USDKRW", "USDHKD", "USDIDR", "USDINR",
         "USDRUB", "USDTHB", "USDTRY", "DXY", "UUP"],
        "DXY",
    )),
    ("CRYPTO", (
        ["BTC", "ETH", "BITO"],
        "DXY",
    )),
])

# TradingView symbol prefix overrides.
# ETFs (SPY, QQQ, XLF, GLD, SLV, EWJ, etc.) are passed as-is — TV resolves them.
# Tickers absent from this map fall back to the raw ticker in to_tv_symbol().
TV_SYMBOL_MAP: Dict[str, str] = {
    # US equity indexes
    # SP:SPX / DJ:DJI require a data subscription on TradingView's free widget;
    # FOREXCOM CFDs track the same indexes and load reliably on the free tier.
    "SPX":      "FOREXCOM:SPXUSD",
    "DJI":      "FOREXCOM:DJIUSD",
    "IXIC":     "NASDAQ:IXIC",
    "RUT":      "TVC:RUT",
    "VIX":      "TVC:VIX",

    # US Treasury yields
    "US03MY":   "TVC:US03MY",
    "US01Y":    "TVC:US01Y",
    "US02Y":    "TVC:US02Y",
    "US05Y":    "TVC:US05Y",
    "US10Y":    "TVC:US10Y",
    "US20Y":    "TVC:US20Y",
    "US30Y":    "TVC:US30Y",
    "MOVE":     "TVC:MOVE",

    # Spreads (FRED)
    "T10Y2Y":   "FRED:T10Y2Y",
    "T10Y3M":   "FRED:T10Y3M",

    # Macro rates (FRED)
    "DFEDTARU": "FRED:DFEDTARUL",   # note trailing L in FRED series ID
    "FEDFUNDS": "FRED:FEDFUNDS",
    "CPIAUCSL": "FRED:CPIAUCSL",
    "GDP":      "FRED:GDP",
    "DRTSCILM": "FRED:DRTSCILM",

    # Foreign sovereign yields (TVC)
    "JP10Y":    "TVC:JP10Y",
    "CN10Y":    "TVC:CN10Y",
    "HK10Y":    "TVC:HK10Y",
    "PH10Y":    "TVC:PH10Y",
    "EU10Y":    "TVC:DE10Y",        # Germany proxy for EUR zone
    "GB10Y":    "TVC:GB10Y",
    "FR10Y":    "TVC:FR10Y",
    "DE10Y":    "TVC:DE10Y",
    "IT10Y":    "TVC:IT10Y",
    "ES10Y":    "TVC:ES10Y",
    "SG10Y":    "TVC:SG10Y",
    "KR10Y":    "TVC:KR10Y",

    # FX — major pairs use FX:, exotic/EM pairs use FX_IDC:
    "DXY":      "TVC:DXY",
    "USDJPY":   "FX:USDJPY",
    "USDAUD":   "FX:USDAUD",
    "USDEUR":   "FX:USDEUR",
    "USDGBP":   "FX:USDGBP",
    "USDCHF":   "FX:USDCHF",
    "USDPHP":   "FX_IDC:USDPHP",
    "USDCNY":   "FX_IDC:USDCNY",
    "USDSGD":   "FX_IDC:USDSGD",
    "USDKRW":   "FX_IDC:USDKRW",
    "USDHKD":   "FX_IDC:USDHKD",
    "USDIDR":   "FX_IDC:USDIDR",
    "USDINR":   "FX_IDC:USDINR",
    "USDRUB":   "FX_IDC:USDRUB",
    "USDTHB":   "FX_IDC:USDTHB",
    "USDTRY":   "FX_IDC:USDTRY",

    # Crypto
    "BTC":      "CRYPTO:BTCUSD",
    "ETH":      "CRYPTO:ETHUSD",
    "BTCUSD":   "CRYPTO:BTCUSD",
    "ETHUSD":   "CRYPTO:ETHUSD",

    # Commodities continuous (not ETFs)
    "USOIL":    "TVC:USOIL",
    "NATGAS":   "TVC:NATGAS",
    "GOLD":     "TVC:GOLD",
    "SILVER":   "TVC:SILVER",
    "COPPER":   "COMEX:HG1!",
}


# ── S3 helpers ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def list_dashboard_dates() -> list[datetime.date]:
    s3 = boto3.client("s3", region_name=REGION)
    paginator = s3.get_paginator("list_objects_v2")
    dates = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=PREFIX):
        for obj in page.get("Contents", []):
            fname = obj["Key"].rsplit("/", 1)[-1]
            m = re.match(r"dashboard_(\d{4}-\d{2}-\d{2})\.xlsx$", fname)
            if m:
                try:
                    dates.append(datetime.date.fromisoformat(m.group(1)))
                except ValueError:
                    pass
    return sorted(dates, reverse=True)


@st.cache_data(ttl=300)
def load_workbook(date_str: str) -> openpyxl.Workbook:
    s3 = boto3.client("s3", region_name=REGION)
    obj = s3.get_object(Bucket=BUCKET, Key=f"{PREFIX}dashboard_{date_str}.xlsx")
    return openpyxl.load_workbook(BytesIO(obj["Body"].read()), data_only=True)


# ── Sheet parsers ────────────────────────────────────────────────────────────

def _pct(current, ref) -> Optional[float]:
    """Safe % change: (current - ref) / |ref| * 100."""
    try:
        if ref and ref != 0:
            return (current - ref) / abs(ref) * 100
    except (TypeError, ZeroDivisionError):
        pass
    return None


def parse_hud(ws) -> pd.DataFrame:
    """
    HUD direct-value columns (0-based):
      0=Symbol, 2=Ref1D, 4=Ref5D, 6=Ref7D, 8=Ref1M,
      10=Ref3M, 12=Ref6M, 14=Ref1Y, 16=CurVal, 18=STD-7D, 19=EMA-7D

    The % diff formula cells all have stale cached 0s; we recompute from refs.
    Ref1D/Ref5D/Ref1M are stored in the DataFrame for RS computation.
    Category-header rows have Symbol but no Current Value.
    """
    records = []
    current_sector = "Unknown"

    for row in ws.iter_rows(min_row=2, values_only=True):
        symbol = row[0] if row else None
        if symbol is None:
            continue
        cur_val = row[16] if len(row) > 16 else None
        if cur_val is None:
            current_sector = str(symbol)
            continue

        ref1d = row[2]  if len(row) > 2  else None
        ref5d = row[4]  if len(row) > 4  else None
        ref7d = row[6]  if len(row) > 6  else None
        ref1m = row[8]  if len(row) > 8  else None
        ref3m = row[10] if len(row) > 10 else None
        ref6m = row[12] if len(row) > 12 else None
        ref1y = row[14] if len(row) > 14 else None
        ema7d = row[19] if len(row) > 19 else None

        records.append({
            "Symbol":  str(symbol),
            "Sector":  current_sector,
            "Price":   cur_val,
            # Raw refs kept for RS computation
            "Ref1D":   ref1d,
            "Ref5D":   ref5d,
            "Ref1M":   ref1m,
            # Display columns
            "STD-7D":       row[18] if len(row) > 18 else None,
            "EMA%Diff-7D":  _pct(cur_val, ema7d),
            "1D%":  _pct(cur_val, ref1d),
            "5D%":  _pct(cur_val, ref5d),
            "7D%":  _pct(cur_val, ref7d),   # kept internally, not displayed
            "1M%":  _pct(cur_val, ref1m),
            "3M%":  _pct(cur_val, ref3m),
            "6M%":  _pct(cur_val, ref6m),
            "1Y%":  _pct(cur_val, ref1y),
        })

    df = pd.DataFrame(records)
    # Deduplicate by Symbol: the Excel lists some tickers under multiple sector
    # headers (e.g. GLD appears in both Materials and Gold sections). Keep the
    # first occurrence so HUD groups don't show duplicate rows for the same ticker.
    df = df.drop_duplicates(subset=["Symbol"], keep="first")
    return df


def parse_compass(ws):
    """Returns (metrics_list, quadrant_int, since_str)."""
    metrics, quadrant, since = [], None, None
    for row in ws.iter_rows(values_only=True):
        if row[0] is None:
            continue
        label = str(row[0])
        if label.startswith("Quadrant"):
            try:
                quadrant = int(row[1])
            except (TypeError, ValueError):
                pass
            m = re.search(r"since (\d{4}-\d{2}-\d{2})", label)
            since = m.group(1) if m else None
            break
        if label == "Symbol":
            continue
        ref_val = row[2] if len(row) > 2 else None
        cur_val = row[4] if len(row) > 4 else None
        if cur_val is not None and ref_val is not None and isinstance(cur_val, (int, float)):
            pct = _pct(cur_val, ref_val)
            if pct is not None:
                trend = ("Accelerating" if pct > 0 else "Decelerating") \
                    if ("credit" in label.lower() or "spread" in label.lower()) \
                    else ("Tightening" if pct > 0 else "Loosening")
            else:
                trend = "—"
            metrics.append({
                "Metric":    label,
                "Ref Value": ref_val,
                "Cur Value": cur_val,
                "% Change":  f"{pct:+.2f}%" if pct is not None else "—",
                "Trend":     trend,
            })
    return metrics, quadrant, since


def parse_grid(ws):
    """Returns (metrics_list, quadrant_int, since_str)."""
    metrics, quadrant, since = [], None, None
    for row in ws.iter_rows(values_only=True):
        if row[0] is None:
            continue
        label = str(row[0])
        if label.startswith("Quadrant"):
            try:
                quadrant = int(row[1])
            except (TypeError, ValueError):
                pass
            m = re.search(r"since (\d{4}-\d{2}-\d{2})", label)
            since = m.group(1) if m else None
            break
        if label == "Metrics":
            continue
        cur = row[3] if len(row) > 3 else None
        if cur is not None and isinstance(cur, (int, float)):
            pct_raw = row[4] if len(row) > 4 else None
            metrics.append({
                "Metric":       label,
                "Release Date": str(row[1]) if row[1] else "—",
                "Prev Value":   row[2] if len(row) > 2 else None,
                "Cur Value":    cur,
                "% Change":     f"{pct_raw:+.2f}%" if isinstance(pct_raw, (int, float)) else "—",
            })
    return metrics, quadrant, since


def parse_clock(ws):
    """Returns list of {Symbol, Quadrant, Q Label, Since}."""
    in_section, skip_header = False, False
    data = []
    for row in ws.iter_rows(values_only=True):
        if row[0] is None:
            continue
        if str(row[0]) == "QUADRANTS":
            in_section, skip_header = True, True
            continue
        if in_section and skip_header:
            skip_header = False
            continue
        if in_section and row[0] is not None:
            q_str = str(row[1]) if row[1] else ""
            m = re.match(r"^(\d+)\s+-\s+", q_str)
            if not m:
                break
            data.append({
                "Symbol":   str(row[0]),
                "Quadrant": int(m.group(1)),
                "Q Label":  q_str,
                "Since":    str(row[2]) if row[2] else "",
            })
    return data


# ── RS computation ───────────────────────────────────────────────────────────

def compute_rs(
    group_df: pd.DataFrame,
    hud_df: pd.DataFrame,
    denom_ticker: str,
) -> pd.DataFrame:
    """
    Adds RS 1D, RS 5D, RS 10D, RS 20D, RS Score columns to group_df.

    RS = (ticker_return - denom_return) * 100  [percentage points]
      ticker_return = (Price / RefXD) - 1
      denom_return  = (denom_Price / denom_RefXD) - 1

    RS 10D: no native 10-day reference in the Excel file; computed via linear
    interpolation between Ref5D and Ref1M:
        ref_10D ≈ ref_5D + (ref_1M - ref_5D) * (5/16)
    Rationale: 10D is ~5 trading days beyond the 5D anchor; 1M (~21TD) is ~16
    trading days beyond the same anchor. Linear interpolation between the two.

    RS 20D uses Ref1M as proxy (1M ≈ 21 trading days).
    """
    out = group_df.copy()

    denom_rows = hud_df[hud_df["Symbol"] == denom_ticker]
    if denom_rows.empty:
        for col in RS_COLS + ["RS Score"]:
            out[col] = float("nan")
        return out

    d = denom_rows.iloc[0]
    d_price = d["Price"]

    def _dret(ref_col: str) -> Optional[float]:
        ref = d.get(ref_col)
        if ref and ref != 0 and d_price:
            return (d_price / ref) - 1.0
        return None

    def _dret_10d() -> Optional[float]:
        r5 = d.get("Ref5D")
        r1m = d.get("Ref1M")
        if r5 and r1m and r5 != 0 and d_price:
            ref_10d = r5 + (r1m - r5) * (5.0 / 16.0)
            if ref_10d != 0:
                return (d_price / ref_10d) - 1.0
        return None

    dr1d  = _dret("Ref1D")
    dr5d  = _dret("Ref5D")
    dr10d = _dret_10d()
    dr20d = _dret("Ref1M")

    def _rs(row: pd.Series, ref_col: str, dret: Optional[float]) -> float:
        if dret is None:
            return float("nan")
        ref = row.get(ref_col)
        cur = row["Price"]
        if ref and ref != 0 and cur:
            return ((cur / ref) - 1.0 - dret) * 100.0
        return float("nan")

    def _rs_10d(row: pd.Series, dret: Optional[float]) -> float:
        if dret is None:
            return float("nan")
        r5 = row.get("Ref5D")
        r1m = row.get("Ref1M")
        cur = row["Price"]
        if r5 and r1m and r5 != 0 and cur:
            ref_10d = r5 + (r1m - r5) * (5.0 / 16.0)
            if ref_10d != 0:
                return ((cur / ref_10d) - 1.0 - dret) * 100.0
        return float("nan")

    out["RS 1D"]  = out.apply(lambda r: _rs(r, "Ref1D", dr1d),   axis=1)
    out["RS 5D"]  = out.apply(lambda r: _rs(r, "Ref5D", dr5d),   axis=1)
    out["RS 10D"] = out.apply(lambda r: _rs_10d(r, dr10d),        axis=1)
    out["RS 20D"] = out.apply(lambda r: _rs(r, "Ref1M", dr20d),  axis=1)
    return out


# ── TradingView symbol mapping ────────────────────────────────────────────────

def to_tv_symbol(ticker: str) -> str:
    return TV_SYMBOL_MAP.get(ticker, ticker)


# ── Regime status boxes ───────────────────────────────────────────────────────

def _dir_color(arrow: str, axis: str) -> str:
    """Return hex color appropriate for the direction + axis combination."""
    if axis == "inflation":
        return "#FF8C00" if arrow == "↑" else "#00C851"
    if axis == "credit":
        return "#00C851" if arrow == "↑" else "#FF8C00"
    # growth / liquidity: ↑ = good
    return "#00C851" if arrow == "↑" else "#FF4444"


def render_grid_box(
    grid_metrics: list,
    grid_q: Optional[int],
    grid_since: Optional[str],
) -> None:
    g_arr, i_arr = GRID_Q_MAP.get(grid_q, ("?", "?")) if grid_q else ("?", "?")
    g_col = _dir_color(g_arr, "growth")
    i_col = _dir_color(i_arr, "inflation")

    gdp = next((m for m in grid_metrics if "growth" in m["Metric"].lower()
                or "gdp" in m["Metric"].lower()), None)
    cpi = next((m for m in grid_metrics if "inflation" in m["Metric"].lower()
                or "cpi" in m["Metric"].lower()), None)

    def _stat(m: Optional[dict]) -> str:
        if not m:
            return "—"
        cur  = m.get("Cur Value", "")
        pct  = m.get("% Change", "")
        date = m.get("Release Date", "")
        val_str = f"{cur}%" if isinstance(cur, float) else str(cur)
        return f'{val_str} <span style="color:#9CA3AF;font-size:0.7rem">{pct} &nbsp; as of {date}</span>'

    since_line = f'<div style="font-size:0.65rem;color:#6B7280;margin-top:8px">In this regime since {grid_since}</div>' if grid_since else ""

    st.markdown(f"""
    <div style="background:#111827;border:1px solid #374151;border-radius:8px;padding:14px 16px">
      <div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;text-transform:uppercase;margin-bottom:10px">GRID — Growth / Inflation</div>
      <div style="display:flex;gap:20px;align-items:baseline;margin-bottom:10px">
        <div>
          <div style="font-size:0.68rem;color:#6B7280">GROWTH</div>
          <div style="font-size:1.6rem;font-weight:700;color:{g_col};line-height:1.1">{g_arr} {"Rising" if g_arr=="↑" else "Falling"}</div>
        </div>
        <div style="width:1px;background:#374151;align-self:stretch;margin:0 4px"></div>
        <div>
          <div style="font-size:0.68rem;color:#6B7280">INFLATION</div>
          <div style="font-size:1.6rem;font-weight:700;color:{i_col};line-height:1.1">{i_arr} {"Rising" if i_arr=="↑" else "Falling"}</div>
        </div>
      </div>
      <div style="border-top:1px solid #1F2937;padding-top:8px;font-size:0.78rem;color:#E5E7EB">
        <div style="margin-bottom:3px"><span style="color:#9CA3AF">GDP&nbsp;</span>{_stat(gdp)}</div>
        <div><span style="color:#9CA3AF">CPI&nbsp;</span>{_stat(cpi)}</div>
      </div>
      {since_line}
    </div>""", unsafe_allow_html=True)


def render_compass_box(
    compass_metrics: list,
    compass_q: Optional[int],
    compass_since: Optional[str],
) -> None:
    l_arr, c_arr = COMPASS_Q_MAP.get(compass_q, ("?", "?")) if compass_q else ("?", "?")
    l_col = _dir_color(l_arr, "liquidity")
    c_col = _dir_color(c_arr, "credit")

    # COMPASS metrics[0] = DFEDTARU (Fed Funds), metrics[1] = DRTSCILM (SLOS)
    fed  = compass_metrics[0] if len(compass_metrics) > 0 else None
    slos = compass_metrics[1] if len(compass_metrics) > 1 else None

    def _stat(m: Optional[dict], label: str) -> str:
        if not m:
            return f'<span style="color:#9CA3AF">{label}&nbsp;</span>—'
        cur   = m.get("Cur Value", "")
        ref   = m.get("Ref Value", "")
        trend = m.get("Trend", "")
        pct   = m.get("% Change", "")
        trend_col = "#00C851" if "loos" in str(trend).lower() or "decel" in str(trend).lower() else "#FF8C00"
        return (f'<span style="color:#9CA3AF">{label}&nbsp;</span>'
                f'<b>{cur}</b> '
                f'<span style="color:{trend_col};font-size:0.72rem">{trend}</span> '
                f'<span style="color:#6B7280;font-size:0.7rem">(was {ref}, {pct})</span>')

    since_line = f'<div style="font-size:0.65rem;color:#6B7280;margin-top:8px">In this regime since {compass_since}</div>' if compass_since else ""

    st.markdown(f"""
    <div style="background:#111827;border:1px solid #374151;border-radius:8px;padding:14px 16px">
      <div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;text-transform:uppercase;margin-bottom:10px">COMPASS — Liquidity / Credit</div>
      <div style="display:flex;gap:20px;align-items:baseline;margin-bottom:10px">
        <div>
          <div style="font-size:0.68rem;color:#6B7280">LIQUIDITY</div>
          <div style="font-size:1.6rem;font-weight:700;color:{l_col};line-height:1.1">{l_arr} {"Easing" if l_arr=="↑" else "Tightening"}</div>
        </div>
        <div style="width:1px;background:#374151;align-self:stretch;margin:0 4px"></div>
        <div>
          <div style="font-size:0.68rem;color:#6B7280">CREDIT</div>
          <div style="font-size:1.6rem;font-weight:700;color:{c_col};line-height:1.1">{c_arr} {"Easing" if c_arr=="↑" else "Tightening"}</div>
        </div>
      </div>
      <div style="border-top:1px solid #1F2937;padding-top:8px;font-size:0.78rem;color:#E5E7EB">
        <div style="margin-bottom:3px">{_stat(fed,  "Fed Funds")}</div>
        <div>{_stat(slos, "SLOS")}</div>
      </div>
      {since_line}
    </div>""", unsafe_allow_html=True)


# ── HUD group table ───────────────────────────────────────────────────────────

def render_hud_group(
    group_name: str,
    group_df: pd.DataFrame,
    has_rs: bool,
    denom_ticker: Optional[str],
) -> Optional[str]:
    """
    Renders one group's mini-table with color-coded delta columns.
    Returns the Symbol of the selected row, or None.
    Clicking a row triggers a Streamlit rerun; the selected ticker is returned
    on that rerun to update the TradingView chart.
    """
    if group_df.empty:
        return None

    # Build display column list
    show_cols = ["Symbol", "Price"] + DISPLAY_TF
    if has_rs:
        show_cols += RS_COLS
        # Note: RS 10D is interpolated (ref_10D = ref_5D + (ref_1M−ref_5D)×5/16)
    show_cols = [c for c in show_cols if c in group_df.columns]

    disp = group_df[show_cols].reset_index(drop=True)

    # Format specs
    fmt: Dict[str, str] = {"Price": "{:.4g}"}
    for c in DISPLAY_TF:
        if c in disp.columns:
            fmt[c] = "{:+.2f}%"
    for c in RS_COLS:
        if c in disp.columns:
            fmt[c] = "{:+.2f}pp"

    # Per-cell text colour (green pos / red neg / grey zero-or-nan)
    color_cols = [c for c in show_cols if c not in ("Symbol", "Price")]

    def _cell_color(v: object) -> str:
        if not isinstance(v, (int, float)) or pd.isna(v):
            return "color: #6B7280"
        if v > 0:
            return "color: #00C851; font-weight:500"
        if v < 0:
            return "color: #FF4444; font-weight:500"
        return "color: #9CA3AF"

    styled = (
        disp.style
        .format(fmt, na_rep="—")
        .map(_cell_color, subset=color_cols)
        .set_properties(**{"font-size": "0.76rem", "padding": "2px 6px"})
        .set_table_styles([
            {"selector": "thead th",
             "props": "background:#1F2937;color:#9CA3AF;font-size:0.68rem;"
                      "text-transform:uppercase;letter-spacing:0.5px;padding:4px 6px"},
        ])
    )

    # Section header bar
    if has_rs and denom_ticker:
        denom_label = f"RS vs {denom_ticker}"
        denom_color = "#5b7fa6"
    else:
        denom_label = "no RS"
        denom_color = "#4a5568"
    st.markdown(
        f'<div style="background:#1a1f35;color:#8b9dc3;font-size:0.65rem;font-weight:700;'
        f'letter-spacing:2px;padding:4px 10px;text-transform:uppercase;'
        f'border-left:3px solid #3b4f8a;margin-top:14px;margin-bottom:4px">'
        f'{group_name}'
        f'<span style="color:{denom_color};font-weight:400;font-size:0.6rem;'
        f'letter-spacing:1px;margin-left:8px">{denom_label}</span></div>',
        unsafe_allow_html=True,
    )

    # Ticker buttons — click any to update TradingView chart
    # Key includes absolute list index so cross-group duplicate tickers (e.g.
    # KWEB in SECTOR ETF + COUNTRY ETF, SLX/WOOD in two commodity groups) never
    # produce a Streamlit duplicate-key error.
    tickers_list = group_df["Symbol"].tolist()
    clicked: Optional[str] = None
    max_per_row = 10
    for i in range(0, len(tickers_list), max_per_row):
        row_t = tickers_list[i : i + max_per_row]
        btn_cols = st.columns(len(row_t))
        for j, ticker in enumerate(row_t):
            idx = i + j
            with btn_cols[j]:
                if st.button(ticker, key=f"btn_{group_name}_{idx}_{ticker}", use_container_width=True):
                    clicked = ticker

    n_rows = len(disp)
    tbl_height = min(380, 35 * n_rows + 40)

    st.dataframe(
        styled,
        hide_index=True,
        width="stretch",
        height=tbl_height,
    )

    return clicked


# ── Heatmap (% change across timeframes) ─────────────────────────────────────

def heatmap_figure(
    hud_df: pd.DataFrame,
    sort_col: Optional[str],
    timeframes: List[str],
) -> go.Figure:
    """
    Full heatmap of % change data grouped by HUD_GROUPS.
    When sort_col is set, tickers are sorted globally by that column.
    Group separator rows (NaN) are inserted when sort_col is None.
    """
    y_labels: List[str] = []
    z_rows: List[List[Optional[float]]] = []
    separator_positions: List[int] = []

    if sort_col:
        # Global sort: flatten all tickers and sort
        all_tickers = [t for tickers, _ in HUD_GROUPS.values() for t in tickers]
        flat = hud_df[hud_df["Symbol"].isin(all_tickers)].copy()
        flat = flat.dropna(subset=[sort_col]).sort_values(sort_col, ascending=False)
        for _, row in flat.iterrows():
            y_labels.append(row["Symbol"])
            z_rows.append([row.get(tf) for tf in timeframes])
    else:
        # Grouped: insert NaN separator rows per group
        for gname, (tickers, _) in HUD_GROUPS.items():
            mask = hud_df["Symbol"].isin(tickers)
            g = hud_df[mask].copy()
            if g.empty:
                continue
            # Group header row (NaN)
            separator_positions.append(len(y_labels))
            y_labels.append(f"▸ {gname}")
            z_rows.append([float("nan")] * len(timeframes))
            # Ticker rows
            order = {t: i for i, t in enumerate(tickers)}
            g["_ord"] = g["Symbol"].map(order)
            for _, row in g.sort_values("_ord").iterrows():
                y_labels.append(row["Symbol"])
                z_rows.append([row.get(tf) for tf in timeframes])

    clamp = 15
    text_matrix = [
        [f"{v:+.1f}%" if (v is not None and not pd.isna(v)) else "" for v in row]
        for row in z_rows
    ]

    fig = go.Figure(go.Heatmap(
        z=z_rows,
        x=timeframes,
        y=y_labels,
        text=text_matrix,
        texttemplate="%{text}",
        textfont={"size": 8},
        colorscale=[
            [0.00, "#67000d"],
            [0.30, "#cc3300"],
            [0.50, "#111827"],
            [0.70, "#005500"],
            [1.00, "#00aa44"],
        ],
        zmin=-clamp,
        zmax=clamp,
        zmid=0,
        showscale=True,
        colorbar=dict(title="% chg", thickness=12, len=0.5, tickfont=dict(size=9)),
    ))

    # Shade group header rows
    for pos in separator_positions:
        fig.add_shape(
            type="rect",
            xref="paper", yref="y",
            x0=0, x1=1,
            y0=y_labels[pos], y1=y_labels[pos],
            line=dict(color="#3b4f8a", width=0),
            fillcolor="#1a1f35",
            opacity=0.9,
        )

    n = len(y_labels)
    fig.update_layout(
        height=max(400, 20 * n + 80),
        margin=dict(l=120, r=40, t=30, b=20),
        paper_bgcolor="#0E1117",
        plot_bgcolor="#0E1117",
        font=dict(color="#E5E7EB", size=9),
        yaxis=dict(
            autorange="reversed",
            tickfont=dict(size=8),
            showgrid=False,
        ),
        xaxis=dict(side="top", showgrid=False),
    )
    return fig


# ── TradingView chart widget ──────────────────────────────────────────────────

def tradingview_widget(symbol: str, render_count: int = 0) -> str:
    # render_count is a cache-buster: each symbol change increments it,
    # guaranteeing a new content hash so Streamlit fully reloads the iframe
    # rather than patching srcdoc in place (which does not re-run <script> tags
    # in Chrome/Safari when the element is mutated via innerHTML).
    cid = f"tv_{render_count}"
    return f"""<!-- rc={render_count} sym={symbol} -->
    <div class="tradingview-widget-container" style="height:480px">
      <div id="{cid}" style="height:100%"></div>
      <script src="https://s3.tradingview.com/tv.js"></script>
      <script>
      new TradingView.widget({{
        "autosize": true,
        "symbol": "{symbol}",
        "interval": "D",
        "timezone": "exchange",
        "theme": "dark",
        "style": "1",
        "locale": "en",
        "toolbar_bg": "#111827",
        "enable_publishing": false,
        "allow_symbol_change": true,
        "container_id": "{cid}"
      }});
      </script>
    </div>"""


# ── Main app ──────────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="Market Dashboard",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown("""
    <style>
      .block-container { padding-top: 0.75rem; padding-bottom: 1rem; }
      section[data-testid="stSidebar"] { min-width: 240px; max-width: 280px; }
      div[data-testid="stHorizontalBlock"] { gap: 6px; }
    </style>""", unsafe_allow_html=True)

    # ── Session state defaults ────────────────────────────────────────────────
    if "tv_symbol" not in st.session_state:
        st.session_state["tv_symbol"] = "TVC:SPX"
    if "tv_render_count" not in st.session_state:
        st.session_state["tv_render_count"] = 0
    for gname, (_, default_denom) in HUD_GROUPS.items():
        key = f"rs_denom_{gname}"
        if key not in st.session_state:
            st.session_state[key] = default_denom if default_denom else ""

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("### 📊 Market Dashboard")
        st.markdown("---")

        with st.spinner("Listing S3 files…"):
            available_dates = list_dashboard_dates()
        if not available_dates:
            st.error("No dashboard files found in S3.")
            st.stop()

        selected_date = st.selectbox(
            "Date",
            available_dates,
            format_func=lambda d: d.strftime("%a %d %b %Y"),
        )
        date_str = selected_date.isoformat()

        st.markdown("---")
        with st.expander("RS Denominators", expanded=False):
            st.caption("Override the RS benchmark for any group. Leave blank to hide RS columns.")
            for gname, (_, default_denom) in HUD_GROUPS.items():
                if default_denom is not None:
                    st.text_input(
                        gname,
                        key=f"rs_denom_{gname}",
                        placeholder=default_denom,
                    )

    # ── Load data ─────────────────────────────────────────────────────────────
    with st.spinner(f"Loading dashboard_{date_str}.xlsx from S3…"):
        try:
            wb = load_workbook(date_str)
        except Exception as exc:
            st.error(f"Failed to load file: {exc}")
            st.stop()

    sheet_names = wb.sheetnames
    hud_df = parse_hud(wb[sheet_names[0]])
    compass_metrics, compass_q, compass_since = parse_compass(wb[sheet_names[1]])
    grid_metrics,    grid_q,    grid_since    = parse_grid(wb[sheet_names[2]])
    clock_data                                = parse_clock(wb[sheet_names[3]])

    # ── Tabs ─────────────────────────────────────────────────────────────────
    tab_live, tab_heatmap, tab_backtest, tab_clock = st.tabs(
        ["  LIVE  ", "  HEATMAP  ", "  BACKTEST  ", "  CLOCK  "]
    )

    # ════════════════════════════════════════════════════════════════════════
    # LIVE TAB
    # ════════════════════════════════════════════════════════════════════════
    with tab_live:
        st.markdown(
            f"<div style='font-size:0.75rem;color:#6B7280;margin-bottom:8px'>"
            f"Dashboard date: <b style='color:#9CA3AF'>{selected_date.strftime('%A, %d %b %Y')}</b></div>",
            unsafe_allow_html=True,
        )

        # ── ROW 1: Regime status boxes ────────────────────────────────────
        col_grid, col_compass = st.columns(2, gap="small")
        with col_grid:
            render_grid_box(grid_metrics, grid_q, grid_since)
        with col_compass:
            render_compass_box(compass_metrics, compass_q, compass_since)

        # ── ROW 2: TradingView chart — placeholder created here for visual
        # position; filled AFTER the HUD buttons below are processed so that
        # a ticker click on this run updates session state before the widget
        # is rendered. render_count cache-buster ensures iframe fully reloads.
        st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
        chart_label = st.empty()
        chart_slot  = st.empty()
        st.markdown("<div style='margin-top:4px'></div>", unsafe_allow_html=True)

        # ── ROW 3: HUD tables ────────────────────────────────────────────
        # RS explanation — collapsible, shown once above all groups
        with st.expander("ⓘ What is RS (Relative Strength)?", expanded=False):
            st.markdown(
                """
**RS (Relative Strength)** measures how much a ticker **outperformed (+)** or
**underperformed (−)** its denominator over each time window.

**Formula:** `RS = ticker_return − denominator_return`

Values are in **percentage points (pp)**. A positive RS means the ticker beat
its benchmark; negative means it lagged.

**Example:** RS 1D of **+1.50** means the ticker beat SPX by 1.5% today.

> *RS 10D is interpolated:* `ref₁₀D ≈ ref₅D + (ref₁M − ref₅D) × 5/16`
> *(10 trading days sits ~5TD beyond the 5D anchor; 1M is ~16TD beyond it.)*
                """,
                unsafe_allow_html=False,
            )

        for gname, (tickers, _) in HUD_GROUPS.items():
            denom = st.session_state.get(f"rs_denom_{gname}", "").strip()
            has_rs = bool(denom)

            order_map = {t: i for i, t in enumerate(tickers)}
            g = hud_df[hud_df["Symbol"].isin(tickers)].copy()
            if g.empty:
                continue
            g["_ord"] = g["Symbol"].map(order_map)
            g = g.sort_values("_ord").drop("_ord", axis=1).reset_index(drop=True)

            if has_rs:
                g = compute_rs(g, hud_df, denom)

            clicked = render_hud_group(gname, g, has_rs, denom or None)
            if clicked:
                new_sym = to_tv_symbol(clicked)
                if new_sym != st.session_state["tv_symbol"]:
                    st.session_state["tv_symbol"] = new_sym
                    st.session_state["tv_render_count"] += 1
                # No st.rerun() needed — chart_slot is filled below after the
                # HUD loop completes, so it always renders with the current
                # (possibly just-updated) session state on this same run.

        # Fill chart slot with the final (post-click) session state.
        # This executes after the HUD loop so session state is already current.
        chart_label.markdown(
            f"<div style='font-size:0.68rem;color:#6B7280;margin-bottom:2px'>"
            f"Chart: <b style='color:#9CA3AF'>{st.session_state['tv_symbol']}</b>"
            f" &nbsp;·&nbsp; click any ticker button to update</div>",
            unsafe_allow_html=True,
        )
        with chart_slot:
            components.html(
                tradingview_widget(
                    st.session_state["tv_symbol"],
                    st.session_state["tv_render_count"],
                ),
                height=500,
                scrolling=False,
            )
        st.caption(
            "If the chart is blank, this symbol may not be available on "
            "TradingView's free tier. Search it directly at tradingview.com."
        )

    # ════════════════════════════════════════════════════════════════════════
    # HEATMAP TAB
    # ════════════════════════════════════════════════════════════════════════
    with tab_heatmap:
        h_col1, h_col2, h_col3 = st.columns([2, 3, 2])
        with h_col1:
            sort_opt = st.selectbox(
                "Sort by",
                ["Group order"] + DISPLAY_TF,
                key="hm_sort",
            )
        with h_col2:
            selected_tfs = st.multiselect(
                "Timeframes",
                DISPLAY_TF,
                default=DISPLAY_TF,
                key="hm_tfs",
            )
        with h_col3:
            clamp_val = st.slider("Color clamp ±%", 5, 30, 15, key="hm_clamp")

        sort_col = None if sort_opt == "Group order" else sort_opt

        if not selected_tfs:
            st.info("Select at least one timeframe.")
        else:
            fig = heatmap_figure(hud_df, sort_col, selected_tfs)
            # Apply user clamp override
            fig.data[0].zmin = -clamp_val
            fig.data[0].zmax =  clamp_val
            st.plotly_chart(fig, use_container_width=True)

    # ════════════════════════════════════════════════════════════════════════
    # BACKTEST TAB
    # ════════════════════════════════════════════════════════════════════════
    with tab_backtest:
        st.info("🔧 Regime scenario engine — coming in next build.")

    # ════════════════════════════════════════════════════════════════════════
    # CLOCK TAB
    # ════════════════════════════════════════════════════════════════════════
    with tab_clock:
        st.markdown("#### CLOCK — Bull / Bear Trend per Index")
        st.caption(
            "Quadrant 2 = Bullish Trend (Strong Bull) · "
            "Quadrant 3 = Bullish Sentiment (Weak Bull) · "
            "Quadrant 4 = Bearish Trend (Strong Bear) · "
            "Quadrant 1 = Bearish Sentiment (Weak Bear)"
        )

        if not clock_data:
            st.warning("No CLOCK data found in this file.")
        else:
            clock_df = pd.DataFrame(clock_data)

            def _q_style(v: object) -> str:
                try:
                    q = int(v)
                    color = CLOCK_Q_COLOR.get(q, "#9CA3AF")
                    return f"color: {color}; font-weight: 600"
                except (TypeError, ValueError):
                    return "color: #9CA3AF"

            styled_clock = (
                clock_df[["Symbol", "Q Label", "Since"]]
                .rename(columns={"Q Label": "Quadrant", "Since": "In regime since"})
                .style
                .map(_q_style, subset=["Quadrant"])
                .set_properties(**{"font-size": "0.82rem"})
            )
            st.dataframe(styled_clock, width="stretch", hide_index=True)


if __name__ == "__main__":
    main()
