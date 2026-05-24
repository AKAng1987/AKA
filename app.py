from __future__ import annotations
from typing import Optional, List, Tuple, Dict
from collections import OrderedDict
from pathlib import Path
import streamlit as st
import boto3
import pandas as pd
import openpyxl
from io import BytesIO
import plotly.graph_objects as go
import datetime
import re
import streamlit.components.v1 as components

import data_cache
import backtest_engine
import macro_data
import fomc_data
import rrg_data
from plotly.subplots import make_subplots

# ── Constants ────────────────────────────────────────────────────────────────
BUCKET = "cmon-stage-backend-369568916817-ap-southeast-1-reports"
PREFIX = "Dashboard/"
REGION = "ap-southeast-1"

DISPLAY_TF = ["1D%", "5D%", "1M%", "3M%", "6M%", "1Y%"]
RS_COLS    = ["RS 1D", "RS 5D", "RS 10D", "RS 20D"]

GRID_Q_MAP: Dict[int, Tuple[str, str]] = {
    1: ("↑", "↓"), 2: ("↑", "↑"), 3: ("↓", "↑"), 4: ("↓", "↓"),
}
COMPASS_Q_MAP: Dict[int, Tuple[str, str]] = {
    1: ("↑", "↓"), 2: ("↑", "↑"), 3: ("↓", "↑"), 4: ("↓", "↓"),
}
GRID_Q_LABELS: Dict[int, str] = {
    1: "G1 — Goldilocks",
    2: "G2 — Reflation",
    3: "G3 — Inflation",
    4: "G4 — Deflation",
}
COMPASS_Q_LABELS: Dict[int, str] = {
    1: "C1 — Liquidity↑ Credit↓",
    2: "C2 — Liquidity↑ Credit↑",
    3: "C3 — Liquidity↓ Credit↑",
    4: "C4 — Liquidity↓ Credit↓",
}

CLOCK_Q_COLOR: Dict[int, str] = {1: "#E87722", 2: "#00C851", 3: "#88DD44", 4: "#FF4444"}

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

_BACKTEST_EXCLUDE_GROUPS: frozenset = frozenset({
    "US INTEREST RATES", "SPREADS", "RATES", "FOREIGN RATES"
})
_BACKTEST_EXCLUDE_TICKERS: frozenset = frozenset()


def _build_backtest_universe() -> Tuple[List[str], Dict[str, str]]:
    """Derive backtest universe from HUD_GROUPS, excluding rate/macro groups."""
    universe: List[str] = []
    ticker_group: Dict[str, str] = {}
    seen: set = set()
    for gname, (tickers, _) in HUD_GROUPS.items():
        if gname in _BACKTEST_EXCLUDE_GROUPS:
            continue
        for t in tickers:
            if t not in _BACKTEST_EXCLUDE_TICKERS and t not in seen:
                seen.add(t)
                universe.append(t)
                ticker_group[t] = gname
    return universe, ticker_group


BACKTEST_UNIVERSE, TICKER_GROUP_MAP = _build_backtest_universe()
BACKTEST_GROUP_NAMES: List[str] = [
    g for g in HUD_GROUPS if g not in _BACKTEST_EXCLUDE_GROUPS
]

TV_SYMBOL_MAP: Dict[str, str] = {
    "SPX":      "FOREXCOM:SPXUSD",
    "DJI":      "FOREXCOM:DJIUSD",
    "IXIC":     "NASDAQ:IXIC",
    "RUT":      "TVC:RUT",
    "VIX":      "TVC:VIX",
    "US03MY":   "TVC:US03MY",
    "US01Y":    "TVC:US01Y",
    "US02Y":    "TVC:US02Y",
    "US05Y":    "TVC:US05Y",
    "US10Y":    "TVC:US10Y",
    "US20Y":    "TVC:US20Y",
    "US30Y":    "TVC:US30Y",
    "MOVE":     "TVC:MOVE",
    "T10Y2Y":   "FRED:T10Y2Y",
    "T10Y3M":   "FRED:T10Y3M",
    "DFEDTARU": "FRED:DFEDTARUL",
    "FEDFUNDS": "FRED:FEDFUNDS",
    "CPIAUCSL": "FRED:CPIAUCSL",
    "GDP":      "FRED:GDP",
    "DRTSCILM": "FRED:DRTSCILM",
    "JP10Y":    "TVC:JP10Y",
    "CN10Y":    "TVC:CN10Y",
    "HK10Y":    "TVC:HK10Y",
    "PH10Y":    "TVC:PH10Y",
    "EU10Y":    "TVC:DE10Y",
    "GB10Y":    "TVC:GB10Y",
    "FR10Y":    "TVC:FR10Y",
    "DE10Y":    "TVC:DE10Y",
    "IT10Y":    "TVC:IT10Y",
    "ES10Y":    "TVC:ES10Y",
    "SG10Y":    "TVC:SG10Y",
    "KR10Y":    "TVC:KR10Y",
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
    "BTC":      "CRYPTO:BTCUSD",
    "ETH":      "CRYPTO:ETHUSD",
    "BTCUSD":   "CRYPTO:BTCUSD",
    "ETHUSD":   "CRYPTO:ETHUSD",
    "USOIL":    "TVC:USOIL",
    "NATGAS":   "TVC:NATGAS",
    "GOLD":     "TVC:GOLD",
    "SILVER":   "TVC:SILVER",
    "COPPER":   "COMEX:HG1!",
}


# ── S3 helpers ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def list_dashboard_dates() -> list:
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
    try:
        if ref and ref != 0:
            return (current - ref) / abs(ref) * 100
    except (TypeError, ZeroDivisionError):
        pass
    return None


def parse_hud(ws) -> pd.DataFrame:
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
            "Symbol":       str(symbol),
            "Sector":       current_sector,
            "Price":        cur_val,
            "Ref1D":        ref1d,
            "Ref5D":        ref5d,
            "Ref1M":        ref1m,
            "STD-7D":       row[18] if len(row) > 18 else None,
            "EMA%Diff-7D":  _pct(cur_val, ema7d),
            "1D%":  _pct(cur_val, ref1d),
            "5D%":  _pct(cur_val, ref5d),
            "7D%":  _pct(cur_val, ref7d),
            "1M%":  _pct(cur_val, ref1m),
            "3M%":  _pct(cur_val, ref3m),
            "6M%":  _pct(cur_val, ref6m),
            "1Y%":  _pct(cur_val, ref1y),
        })
    df = pd.DataFrame(records)
    df = df.drop_duplicates(subset=["Symbol"], keep="first")
    return df


def parse_compass(ws):
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


def parse_clock(ws) -> dict:
    """
    Parse all four sections of the CLOCK sheet.

    Returns a dict with keys:
      key_indicators  — list of {Symbol, Ref Date, Ref Value, Cur Date, Cur Value}
      quadrants       — list of {Symbol, Quadrant (int), Q Label, Since}
      ema_quadrants   — list of {Symbol, EMA100Q (int|None), EMA100, EMA200Q, EMA200}
      signal_remarks  — list of {Symbol, Remarks}
    """
    rows = list(ws.iter_rows(values_only=True))

    result: Dict[str, list] = {
        "key_indicators": [],
        "quadrants": [],
        "ema_quadrants": [],
        "signal_remarks": [],
    }

    # Section 1 — key indicators: row 0 is header; rows 1–15 are data
    for row in rows[1:16]:
        if row[0] is None:
            continue
        result["key_indicators"].append({
            "Symbol":    str(row[0]),
            "Ref Date":  str(row[1]) if row[1] else "—",
            "Ref Value": row[2],
            "Cur Date":  str(row[3]) if row[3] else "—",
            "Cur Value": row[4],
        })

    # Sections 2-4 — walk the rest of the sheet looking for markers
    state = None
    for row in rows[16:]:
        cell0 = str(row[0]).strip() if row[0] is not None else ""
        cell1 = str(row[1]).strip() if row[1] is not None else ""

        if cell0 == "QUADRANTS":
            state = "quad_header"
            continue
        if cell0.lower().startswith("derived quadrant"):
            state = "ema_header"
            continue
        if cell0 == "INDICATORS":
            state = "ind_header"
            continue
        if cell0 == "Clock Strategy":
            break  # stop before strategy section

        if state == "quad_header":
            if cell0 == "Symbol":
                continue  # skip column header
            if cell0:
                q_str = cell1
                m = re.match(r"^(\d+)\s+-\s+", q_str)
                if not m:
                    state = None
                    continue
                result["quadrants"].append({
                    "Symbol":   cell0,
                    "Quadrant": int(m.group(1)),
                    "Q Label":  q_str,
                    "Since":    str(row[2]).strip() if row[2] else "—",
                })

        elif state == "ema_header":
            if cell0 == "Symbol":
                continue
            if cell0:
                def _q(s: str) -> Optional[int]:
                    mm = re.match(r"^(\d+)", s)
                    return int(mm.group(1)) if mm else None
                result["ema_quadrants"].append({
                    "Symbol":  cell0,
                    "EMA100Q": _q(cell1),
                    "EMA100":  cell1 if cell1 else "—",
                    "EMA200Q": _q(str(row[2]).strip() if row[2] else ""),
                    "EMA200":  str(row[2]).strip() if row[2] else "—",
                })

        elif state == "ind_header":
            if cell0 == "Symbol":
                continue
            if cell0:
                result["signal_remarks"].append({
                    "Symbol":  cell0,
                    "Remarks": cell1 if cell1 else "—",
                })
            elif not cell0 and not cell1:
                state = None  # blank row ends indicators

    return result


# ── RS computation ───────────────────────────────────────────────────────────

def compute_rs(
    group_df: pd.DataFrame,
    hud_df: pd.DataFrame,
    denom_ticker: str,
) -> pd.DataFrame:
    out = group_df.copy()
    denom_rows = hud_df[hud_df["Symbol"] == denom_ticker]
    if denom_rows.empty:
        for col in RS_COLS:
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
        r5 = d.get("Ref5D"); r1m = d.get("Ref1M")
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
        if dret is None: return float("nan")
        ref = row.get(ref_col); cur = row["Price"]
        if ref and ref != 0 and cur:
            return ((cur / ref) - 1.0 - dret) * 100.0
        return float("nan")

    def _rs_10d(row: pd.Series, dret: Optional[float]) -> float:
        if dret is None: return float("nan")
        r5 = row.get("Ref5D"); r1m = row.get("Ref1M"); cur = row["Price"]
        if r5 and r1m and r5 != 0 and cur:
            ref_10d = r5 + (r1m - r5) * (5.0 / 16.0)
            if ref_10d != 0:
                return ((cur / ref_10d) - 1.0 - dret) * 100.0
        return float("nan")

    out["RS 1D"]  = out.apply(lambda r: _rs(r, "Ref1D", dr1d),  axis=1)
    out["RS 5D"]  = out.apply(lambda r: _rs(r, "Ref5D", dr5d),  axis=1)
    out["RS 10D"] = out.apply(lambda r: _rs_10d(r, dr10d),       axis=1)
    out["RS 20D"] = out.apply(lambda r: _rs(r, "Ref1M", dr20d), axis=1)
    return out


# ── TradingView helpers ───────────────────────────────────────────────────────

def to_tv_symbol(ticker: str) -> str:
    return TV_SYMBOL_MAP.get(ticker, ticker)


def tradingview_widget(symbol: str, render_count: int = 0) -> str:
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


# ── Regime status boxes ───────────────────────────────────────────────────────

def _dir_color(arrow: str, axis: str) -> str:
    if axis == "inflation":
        return "#FF8C00" if arrow == "↑" else "#00C851"
    if axis == "credit":
        return "#00C851" if arrow == "↑" else "#FF8C00"
    return "#00C851" if arrow == "↑" else "#FF4444"


def render_grid_box(grid_metrics, grid_q, grid_since) -> None:
    g_arr, i_arr = GRID_Q_MAP.get(grid_q, ("?", "?")) if grid_q else ("?", "?")
    g_col = _dir_color(g_arr, "growth")
    i_col = _dir_color(i_arr, "inflation")

    gdp = next((m for m in grid_metrics if "growth" in m["Metric"].lower()
                or "gdp" in m["Metric"].lower()), None)
    cpi = next((m for m in grid_metrics if "inflation" in m["Metric"].lower()
                or "cpi" in m["Metric"].lower()), None)

    def _stat(m):
        if not m: return "—"
        cur = m.get("Cur Value", ""); pct = m.get("% Change", ""); date = m.get("Release Date", "")
        val_str = f"{cur}%" if isinstance(cur, float) else str(cur)
        return f'{val_str} <span style="color:#9CA3AF;font-size:0.7rem">{pct} &nbsp; as of {date}</span>'

    regime_name = GRID_Q_LABELS.get(grid_q, "") if grid_q else ""
    since_line = f'<div style="font-size:0.65rem;color:#6B7280;margin-top:8px">In this regime since {grid_since}</div>' if grid_since else ""
    st.markdown(f"""
    <div style="background:#111827;border:1px solid #374151;border-radius:8px;padding:14px 16px">
      <div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px">GRID — Growth / Inflation</div>
      <div style="font-size:1.05rem;font-weight:700;color:#FCD34D;margin-bottom:10px">{regime_name}</div>
      <div style="display:flex;gap:20px;align-items:baseline;margin-bottom:10px">
        <div><div style="font-size:0.68rem;color:#6B7280">GROWTH</div>
          <div style="font-size:1.6rem;font-weight:700;color:{g_col};line-height:1.1">{g_arr} {"Rising" if g_arr=="↑" else "Falling"}</div></div>
        <div style="width:1px;background:#374151;align-self:stretch;margin:0 4px"></div>
        <div><div style="font-size:0.68rem;color:#6B7280">INFLATION</div>
          <div style="font-size:1.6rem;font-weight:700;color:{i_col};line-height:1.1">{i_arr} {"Rising" if i_arr=="↑" else "Falling"}</div></div>
      </div>
      <div style="border-top:1px solid #1F2937;padding-top:8px;font-size:0.78rem;color:#E5E7EB">
        <div style="margin-bottom:3px"><span style="color:#9CA3AF">GDP&nbsp;</span>{_stat(gdp)}</div>
        <div><span style="color:#9CA3AF">CPI&nbsp;</span>{_stat(cpi)}</div>
      </div>
      {since_line}
    </div>""", unsafe_allow_html=True)


def render_compass_box(compass_metrics, compass_q, compass_since) -> None:
    l_arr, c_arr = COMPASS_Q_MAP.get(compass_q, ("?", "?")) if compass_q else ("?", "?")
    l_col = _dir_color(l_arr, "liquidity")
    c_col = _dir_color(c_arr, "credit")

    fed  = compass_metrics[0] if len(compass_metrics) > 0 else None
    slos = compass_metrics[1] if len(compass_metrics) > 1 else None

    def _stat(m, label):
        if not m:
            return f'<span style="color:#9CA3AF">{label}&nbsp;</span>—'
        cur = m.get("Cur Value", ""); ref = m.get("Ref Value", "")
        trend = m.get("Trend", ""); pct = m.get("% Change", "")
        trend_col = "#00C851" if "loos" in str(trend).lower() or "decel" in str(trend).lower() else "#FF8C00"
        return (f'<span style="color:#9CA3AF">{label}&nbsp;</span>'
                f'<b>{cur}</b> '
                f'<span style="color:{trend_col};font-size:0.72rem">{trend}</span> '
                f'<span style="color:#6B7280;font-size:0.7rem">(was {ref}, {pct})</span>')

    compass_label = COMPASS_Q_LABELS.get(compass_q, "") if compass_q else ""
    since_line = f'<div style="font-size:0.65rem;color:#6B7280;margin-top:8px">In this regime since {compass_since}</div>' if compass_since else ""
    st.markdown(f"""
    <div style="background:#111827;border:1px solid #374151;border-radius:8px;padding:14px 16px">
      <div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px">COMPASS — Liquidity / Credit</div>
      <div style="font-size:1.05rem;font-weight:700;color:#FCD34D;margin-bottom:10px">{compass_label}</div>
      <div style="display:flex;gap:20px;align-items:baseline;margin-bottom:10px">
        <div><div style="font-size:0.68rem;color:#6B7280">LIQUIDITY</div>
          <div style="font-size:1.6rem;font-weight:700;color:{l_col};line-height:1.1">{l_arr} {"Easing" if l_arr=="↑" else "Tightening"}</div></div>
        <div style="width:1px;background:#374151;align-self:stretch;margin:0 4px"></div>
        <div><div style="font-size:0.68rem;color:#6B7280">CREDIT</div>
          <div style="font-size:1.6rem;font-weight:700;color:{c_col};line-height:1.1">{c_arr} {"Easing" if c_arr=="↑" else "Tightening"}</div></div>
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
    if group_df.empty:
        return None

    show_cols = ["Symbol", "Price"] + DISPLAY_TF
    if has_rs:
        show_cols += RS_COLS
    show_cols = [c for c in show_cols if c in group_df.columns]

    disp = group_df[show_cols].reset_index(drop=True)

    fmt: Dict[str, str] = {"Price": "{:.4g}"}
    for c in DISPLAY_TF:
        if c in disp.columns:
            fmt[c] = "{:+.2f}%"
    for c in RS_COLS:
        if c in disp.columns:
            fmt[c] = "{:+.2f}pp"

    color_cols = [c for c in show_cols if c not in ("Symbol", "Price")]

    def _cell_color(v: object) -> str:
        if not isinstance(v, (int, float)) or pd.isna(v):
            return "color: #6B7280"
        if v > 0: return "color: #00C851; font-weight:500"
        if v < 0: return "color: #FF4444; font-weight:500"
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

    denom_label = f"RS vs {denom_ticker}" if has_rs and denom_ticker else "no RS"
    denom_color = "#5b7fa6" if has_rs and denom_ticker else "#4a5568"
    st.markdown(
        f'<div style="background:#1a1f35;color:#8b9dc3;font-size:0.65rem;font-weight:700;'
        f'letter-spacing:2px;padding:4px 10px;text-transform:uppercase;'
        f'border-left:3px solid #3b4f8a;margin-top:14px;margin-bottom:4px">'
        f'{group_name}'
        f'<span style="color:{denom_color};font-weight:400;font-size:0.6rem;'
        f'letter-spacing:1px;margin-left:8px">{denom_label}</span></div>',
        unsafe_allow_html=True,
    )

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
    st.dataframe(styled, hide_index=True, width="stretch", height=tbl_height)
    return clicked


# ── Heatmap ───────────────────────────────────────────────────────────────────

def heatmap_figure(hud_df: pd.DataFrame, sort_col: Optional[str], timeframes: List[str]) -> go.Figure:
    y_labels: List[str] = []
    z_rows: List[List[Optional[float]]] = []
    separator_positions: List[int] = []

    if sort_col:
        all_tickers = [t for tickers, _ in HUD_GROUPS.values() for t in tickers]
        flat = hud_df[hud_df["Symbol"].isin(all_tickers)].copy()
        flat = flat.dropna(subset=[sort_col]).sort_values(sort_col, ascending=False)
        for _, row in flat.iterrows():
            y_labels.append(row["Symbol"])
            z_rows.append([row.get(tf) for tf in timeframes])
    else:
        for gname, (tickers, _) in HUD_GROUPS.items():
            mask = hud_df["Symbol"].isin(tickers)
            g = hud_df[mask].copy()
            if g.empty:
                continue
            separator_positions.append(len(y_labels))
            y_labels.append(f"▸ {gname}")
            z_rows.append([float("nan")] * len(timeframes))
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
        z=z_rows, x=timeframes, y=y_labels,
        text=text_matrix, texttemplate="%{text}", textfont={"size": 8},
        colorscale=[
            [0.00, "#67000d"], [0.30, "#cc3300"], [0.50, "#111827"],
            [0.70, "#005500"], [1.00, "#00aa44"],
        ],
        zmin=-clamp, zmax=clamp, zmid=0,
        showscale=True,
        colorbar=dict(title="% chg", thickness=12, len=0.5, tickfont=dict(size=9)),
    ))
    for pos in separator_positions:
        fig.add_shape(
            type="rect", xref="paper", yref="y", x0=0, x1=1,
            y0=y_labels[pos], y1=y_labels[pos],
            line=dict(color="#3b4f8a", width=0), fillcolor="#1a1f35", opacity=0.9,
        )
    n = len(y_labels)
    fig.update_layout(
        height=max(400, 20 * n + 80),
        margin=dict(l=120, r=40, t=30, b=20),
        paper_bgcolor="#0E1117", plot_bgcolor="#0E1117",
        font=dict(color="#E5E7EB", size=9),
        yaxis=dict(autorange="reversed", tickfont=dict(size=8), showgrid=False),
        xaxis=dict(side="top", showgrid=False),
    )
    return fig


# ── Backtest helpers ──────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _get_regime_periods() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load model history + build regime period timeline. Cached for 1h."""
    grid_df    = data_cache.load_model("grid_US")
    compass_df = data_cache.load_model("compass_US")
    if grid_df.empty or compass_df.empty:
        return grid_df, compass_df, pd.DataFrame()
    periods_df = backtest_engine.build_regime_periods(grid_df, compass_df)
    return grid_df, compass_df, periods_df


@st.cache_data(ttl=3600, show_spinner=False)
def _get_prices_from_cache() -> Dict[str, pd.DataFrame]:
    """Read all available parquet files for the backtest universe. No DynamoDB calls."""
    return data_cache.load_all_prices(BACKTEST_UNIVERSE)


def _edge_color(edge: float) -> str:
    if pd.isna(edge): return "color: #6B7280"
    if edge >= 2.0:   return "color: #00C851; font-weight:700"
    if edge >= 1.0:   return "color: #88DD44; font-weight:500"
    if edge >= 0.5:   return "color: #FF8C00"
    return "color: #FF4444"


def _render_backtest_table(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("No tickers met the minimum occurrences threshold for this regime combo.")
        return

    def _edge_style(v: object) -> str:
        if not isinstance(v, (int, float)) or pd.isna(v): return "color: #6B7280"
        if v >= 2.0: return "color: #00C851; font-weight:700"
        if v >= 1.0: return "color: #88DD44; font-weight:500"
        if v >= 0.5: return "color: #FF8C00"
        return "color: #FF4444"

    def _pct_style(v: object) -> str:
        if not isinstance(v, (int, float)) or pd.isna(v): return "color: #6B7280"
        return "color: #00C851; font-weight:500" if v > 0 else "color: #FF4444; font-weight:500"

    _col_order = ["Ticker", "Edge", "Hit Rate", "Occurrences", "Avg High%", "Avg Low%", "Avg Return%"]
    df = df[[c for c in _col_order if c in df.columns]]
    fmt = {
        "Occurrences": "{:.0f}",
        "Avg High%": "{:+.2f}%",
        "Avg Low%": "{:+.2f}%",
        "Hit Rate": "{:.1f}%",
        "Edge": "{:.2f}",
        "Avg Return%": "{:+.2f}%",
    }
    styled = (
        df.style
        .format(fmt, na_rep="—")
        .map(_edge_style, subset=["Edge"])
        .map(_pct_style, subset=["Avg High%", "Avg Low%", "Avg Return%"])
        .set_properties(**{"font-size": "0.80rem", "padding": "3px 8px"})
        .set_table_styles([
            {"selector": "thead th",
             "props": "background:#1F2937;color:#9CA3AF;font-size:0.70rem;"
                      "text-transform:uppercase;letter-spacing:0.5px;padding:5px 8px"},
        ])
    )
    h = min(600, 38 * len(df) + 45)
    st.dataframe(styled, hide_index=True, width="stretch", height=h)


def _run_cache_load(universe: List[str]) -> None:
    """Fetch + cache price history for all symbols with a progress bar, then rerun."""
    prog      = st.progress(0)
    status    = st.empty()
    skipped: List[str] = []
    for i, sym in enumerate(universe):
        status.caption(f"Fetching {sym}… ({i + 1}/{len(universe)})")
        try:
            df = data_cache.load_price(sym, force=False)
            if df.empty:
                skipped.append(sym)
        except Exception:
            skipped.append(sym)
        prog.progress((i + 1) / len(universe))
    prog.empty()
    status.empty()
    st.cache_data.clear()
    warm_n = data_cache.cache_warm_count(universe)
    st.session_state["_cache_load_done"]    = warm_n
    st.session_state["_cache_load_skipped"] = skipped
    st.rerun()


# ── Main app ──────────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="CGI",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown("""
    <style>
      .block-container { padding-top: 0.5rem; padding-bottom: 1rem; }
      section[data-testid="stSidebar"] { min-width: 240px; max-width: 280px; }
      div[data-testid="stHorizontalBlock"] { gap: 6px; }

    </style>""", unsafe_allow_html=True)

    # ── Session state ─────────────────────────────────────────────────────────
    if "tv_symbol" not in st.session_state:
        st.session_state["tv_symbol"] = "SPY"
    if "tv_render_count" not in st.session_state:
        st.session_state["tv_render_count"] = 0
    for gname, (_, default_denom) in HUD_GROUPS.items():
        key = f"rs_denom_{gname}"
        if key not in st.session_state:
            st.session_state[key] = default_denom if default_denom else ""
    # Deep Dive chart state
    if "dd_tv_symbol" not in st.session_state:
        st.session_state["dd_tv_symbol"] = "SPY"
    if "dd_tv_render_count" not in st.session_state:
        st.session_state["dd_tv_render_count"] = 0
    if "dd_tv_label" not in st.session_state:
        st.session_state["dd_tv_label"] = None  # (start_str, end_str) or None

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("### Compass Grid Identifier (CGI)")
        active_tab = st.radio(
            "Go to",
            ["LIVE", "MACRO", "BACKTEST", "DEEP DIVE", "SCENARIO", "RRG", "CLOCK"],
            label_visibility="collapsed",
            key="active_tab",
        )
        st.divider()

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

        st.divider()
        with st.expander("RS Denominators", expanded=False):
            st.caption("Override the RS benchmark for any group. Leave blank to hide RS columns.")
            for gname, (_, default_denom) in HUD_GROUPS.items():
                if default_denom is not None:
                    st.text_input(gname, key=f"rs_denom_{gname}", placeholder=default_denom)

        st.divider()
        st.markdown("**Backtest Data**")

        warm  = data_cache.cache_warm_count(BACKTEST_UNIVERSE)
        total = len(BACKTEST_UNIVERSE)

        # Cache status line
        if warm == total:
            st.caption(f"✅ Cached: {warm}/{total} tickers")
        elif warm == 0:
            st.caption(f"⚠ Not loaded: 0/{total} tickers")
        else:
            st.caption(f"⚠ Partial: {warm}/{total} tickers")

        # Compass freshness
        compass_latest = data_cache.model_latest_date("compass_US")
        if compass_latest is not None:
            days_stale = (pd.Timestamp.today() - compass_latest).days
            stale_str  = compass_latest.strftime("%Y-%m-%d")
            if days_stale > 30:
                st.warning(f"⚠ Compass {days_stale}d stale ({stale_str})")
            else:
                st.caption(f"Compass: {stale_str}")

        # Single primary button for everything
        if st.button(
            "⬇ Load / Refresh All Price Data",
            key="sidebar_load_all",
            type="primary",
            use_container_width=True,
        ):
            with st.spinner("Refreshing model history…"):
                data_cache.load_model("grid_US",    force=True)
                data_cache.load_model("compass_US", force=True)
            st.cache_data.clear()
            _run_cache_load(BACKTEST_UNIVERSE)

        st.divider()
        st.markdown("**Macro Data**")
        try:
            _ms      = macro_data.macro_staleness()
            _n_stale = sum(_ms.values())
            if _n_stale:
                st.caption(f"⚠ {_n_stale} cache(s) stale")
            else:
                st.caption("✅ All macro caches fresh")
        except Exception:
            st.caption("Macro cache status unavailable")
        if st.button(
            "⬇ Refresh Macro Data",
            key="sidebar_macro_refresh",
            use_container_width=True,
        ):
            st.session_state["_macro_force"] = True
            st.rerun()

        st.divider()
        try:
            _rrg_info  = rrg_data.rrg_cache_info()
            _rrg_stale = sum(1 for v in _rrg_info.values() if v["stale"])
            if _rrg_stale:
                st.caption(f"⚠ {_rrg_stale} RRG cache(s) stale")
            else:
                st.caption("✅ RRG caches fresh")
        except Exception:
            st.caption("RRG cache status unavailable")
        if st.button(
            "⬇ Refresh RRG",
            key="sidebar_rrg_refresh",
            use_container_width=True,
        ):
            st.session_state["_rrg_force"] = True
            st.rerun()

    # ── Load dashboard workbook ───────────────────────────────────────────────
    with st.spinner(f"Loading dashboard_{date_str}.xlsx from S3…"):
        try:
            wb = load_workbook(date_str)
        except Exception as exc:
            st.error(f"Failed to load file: {exc}")
            st.stop()

    sheet_names = wb.sheetnames
    hud_df          = parse_hud(wb[sheet_names[0]])
    compass_metrics, compass_q, compass_since = parse_compass(wb[sheet_names[1]])
    grid_metrics,    grid_q,    grid_since    = parse_grid(wb[sheet_names[2]])
    clock_data = parse_clock(wb[sheet_names[3]])

    # ── Load regime timeline (small, always needed for backtest tabs) ─────────
    grid_df, compass_df, periods_df = _get_regime_periods()

    # ── Post-load success banner (set by _run_cache_load via st.rerun) ───────
    if st.session_state.get("_cache_load_done"):
        loaded_n = st.session_state.pop("_cache_load_done")
        skipped  = st.session_state.pop("_cache_load_skipped", [])
        st.success(f"Price data loaded: {loaded_n}/{len(BACKTEST_UNIVERSE)} tickers cached.")
        if skipped:
            with st.expander(f"⚠ {len(skipped)} tickers returned no data"):
                st.write(", ".join(skipped))

    # ════════════════════════════════════════════════════════════════════════
    # LIVE TAB
    # ════════════════════════════════════════════════════════════════════════
    if active_tab == "LIVE":
        st.markdown(
            f"<div style='font-size:0.75rem;color:#6B7280;margin-bottom:8px'>"
            f"Data date: <b style='color:#9CA3AF'>{selected_date.strftime('%A, %d %b %Y')}</b></div>",
            unsafe_allow_html=True,
        )

        # Fallback: if compass_q could not be parsed from Excel, read last known
        # quadrant from the DynamoDB model cache (model_compass_US.parquet).
        compass_stale_note = None
        if compass_q is None:
            _cb_path = data_cache.cache_path("model", "compass_US")
            if _cb_path.exists():
                try:
                    _cb_df = pd.read_parquet(_cb_path)
                    _cb_df["metrics_date"] = pd.to_datetime(_cb_df["metrics_date"])
                    _cb_df = _cb_df.dropna(subset=["quadrant"])
                    if not _cb_df.empty:
                        _cb_last = _cb_df.sort_values("metrics_date").iloc[-1]
                        compass_q = int(_cb_last["quadrant"])
                        compass_stale_note = _cb_last["metrics_date"].strftime("%Y-%m-%d")
                except Exception:
                    pass

        col_compass, col_grid = st.columns(2, gap="small")
        with col_compass:
            if compass_stale_note:
                st.warning(f"Compass stale — last known: {compass_stale_note}", icon="⚠️")
            render_compass_box(compass_metrics, compass_q, compass_since)
        with col_grid:
            render_grid_box(grid_metrics, grid_q, grid_since)

        st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
        chart_label = st.empty()
        chart_slot  = st.empty()
        st.markdown("<div style='margin-top:4px'></div>", unsafe_allow_html=True)

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

        chart_label.markdown(
            f"<div style='font-size:0.68rem;color:#6B7280;margin-bottom:2px'>"
            f"Chart: <b style='color:#9CA3AF'>{st.session_state['tv_symbol']}</b>"
            f" &nbsp;·&nbsp; click any ticker button to update</div>",
            unsafe_allow_html=True,
        )
        with chart_slot:
            components.html(
                tradingview_widget(st.session_state["tv_symbol"], st.session_state["tv_render_count"]),
                height=500, scrolling=False,
            )
        st.caption(
            "If the chart is blank, this symbol may not be available on "
            "TradingView's free tier. Search it directly at tradingview.com."
        )

    # ════════════════════════════════════════════════════════════════════════
    # BACKTEST TAB
    # ════════════════════════════════════════════════════════════════════════
    elif active_tab == "BACKTEST":
        st.markdown("#### Regime Backtest Engine")
        st.caption("Historical performance of every ETF during a specific Compass × Grid regime. "
                   "Entry = close on signal date. Edge = Avg High% ÷ |Avg Low%|.")

        if periods_df.empty:
            st.error("Failed to load regime history from DynamoDB. Check AWS credentials.")
        else:
            # Compass staleness warning
            if not compass_df.empty:
                latest_c = pd.to_datetime(compass_df["metrics_date"]).max()
                days_stale = (pd.Timestamp.today() - latest_c).days
                if days_stale > 30:
                    st.warning(
                        f"⚠ Compass model data is {days_stale} days stale "
                        f"(last update: {latest_c.date()}). "
                        "Regime combos involving recent Compass changes may be incomplete."
                    )

            # Show current regime from DB
            if len(periods_df) > 0:
                cur = periods_df.iloc[-1]
                cur_label = (f"Current regime (DB): "
                             f"**{COMPASS_Q_LABELS[int(cur['compass_q'])]}** × "
                             f"**{GRID_Q_LABELS[int(cur['grid_q'])]}** "
                             f"since {cur['start'].date()}")
                st.info(cur_label)

            # Controls row
            c1, c2, c3 = st.columns([1, 1, 2])
            with c1:
                bt_cq = st.selectbox(
                    "Compass Q", [1, 2, 3, 4], key="bt_cq",
                    format_func=lambda q: COMPASS_Q_LABELS[q],
                )
            with c2:
                bt_gq = st.selectbox(
                    "Grid Q", [1, 2, 3, 4], key="bt_gq",
                    format_func=lambda q: GRID_Q_LABELS[q],
                )
            with c3:
                min_occ = st.slider("Min occurrences", 1, 20, 5, key="bt_min_occ")

            # Sidebar: group filter + lookback period
            with st.sidebar:
                st.divider()
                st.markdown("**Filter Groups**")
                bt_shown_groups = st.multiselect(
                    "Groups",
                    BACKTEST_GROUP_NAMES,
                    default=BACKTEST_GROUP_NAMES,
                    key="bt_groups",
                    label_visibility="collapsed",
                )
                st.divider()
                st.markdown("**Lookback Period**")
                bt_lookback = st.radio(
                    "Lookback",
                    ["All history", "Last 10 years", "Last 5 years"],
                    key="bt_lookback",
                    label_visibility="collapsed",
                )

            # Lookback filter
            _bt_today = pd.Timestamp.today().normalize()
            if bt_lookback == "Last 10 years":
                _bt_cutoff = _bt_today - pd.DateOffset(years=10)
            elif bt_lookback == "Last 5 years":
                _bt_cutoff = _bt_today - pd.DateOffset(years=5)
            else:
                _bt_cutoff = None
            bt_periods_df = (
                periods_df if _bt_cutoff is None
                else periods_df[periods_df["start"] >= _bt_cutoff].reset_index(drop=True)
            )

            # How many periods match this combo?
            matching_periods = bt_periods_df[
                (bt_periods_df["grid_q"] == bt_gq) & (bt_periods_df["compass_q"] == bt_cq)
            ]
            st.caption(
                f"Regime **{COMPASS_Q_LABELS[bt_cq]} × {GRID_Q_LABELS[bt_gq]}** "
                f"(C{bt_cq}×G{bt_gq}) has occurred **{len(matching_periods)}** times "
                f"in the DB (grid_US from 2008, compass_US from 2004)."
            )
            if _bt_cutoff is not None:
                st.caption(f"Showing results from {_bt_cutoff.date()} to present")

            # Cache warmth — point user to sidebar button if not loaded
            warm_n = data_cache.cache_warm_count(BACKTEST_UNIVERSE)
            if warm_n == 0:
                st.warning(
                    "Price data not loaded. Use **⬇ Load / Refresh All Price Data** "
                    "in the sidebar to fetch all tickers (~60–120s, cached 24h)."
                )
            else:
                if warm_n < len(BACKTEST_UNIVERSE):
                    st.caption(
                        f"ℹ {warm_n}/{len(BACKTEST_UNIVERSE)} tickers cached — "
                        "use sidebar button to load the rest."
                    )

                with st.spinner("Computing backtest stats…"):
                    prices_map = _get_prices_from_cache()
                    bt_table   = backtest_engine.build_backtest_table(
                        BACKTEST_UNIVERSE, prices_map, bt_periods_df, bt_gq, bt_cq, min_occ
                    )

                # Build display copy with proxy annotations
                bt_display = bt_table.copy()
                bt_display["Ticker"] = bt_display["Ticker"].apply(
                    lambda t: f"{t} (proxy: {data_cache.BACKTEST_PROXIES[t]})"
                    if t in data_cache.BACKTEST_PROXIES else t
                )

                # Grouped display by asset class
                _bt_total = 0
                for gname in bt_shown_groups:
                    _grp_tickers = {t for t, g in TICKER_GROUP_MAP.items() if g == gname}
                    _grp_mask = bt_table["Ticker"].isin(_grp_tickers)
                    _grp_df = bt_display[_grp_mask].reset_index(drop=True)
                    if _grp_df.empty:
                        continue
                    st.markdown(
                        f'<div style="background:#1a1f35;color:#8b9dc3;font-size:0.65rem;'
                        f'font-weight:700;letter-spacing:2px;padding:4px 10px;'
                        f'text-transform:uppercase;border-left:3px solid #3b4f8a;'
                        f'margin-top:14px;margin-bottom:4px">{gname}</div>',
                        unsafe_allow_html=True,
                    )
                    _render_backtest_table(_grp_df)
                    _bt_total += len(_grp_df)

                if _bt_total == 0:
                    st.info("No tickers met the minimum occurrences threshold for this regime combo.")

                # Occurrence detail expander
                if _bt_total > 0:
                    st.markdown("---")
                    with st.expander("Show Occurrence Detail"):
                        _occ_tickers = bt_table["Ticker"].tolist()
                        _sel_ticker = st.selectbox(
                            "Select ticker for detail",
                            _occ_tickers,
                            key="bt_occ_ticker",
                            format_func=lambda t: (
                                f"{t} (proxy: {data_cache.BACKTEST_PROXIES[t]})"
                                if t in data_cache.BACKTEST_PROXIES else t
                            ),
                        )
                        _occ_prices = prices_map.get(_sel_ticker)
                        if _occ_prices is not None and not _occ_prices.empty:
                            _occ_detail = backtest_engine.get_occurrences(
                                _occ_prices, bt_periods_df, bt_gq, bt_cq
                            )
                            if _occ_detail.empty:
                                st.info(f"No occurrences found for {_sel_ticker}.")
                            else:
                                _occ_show = _occ_detail[
                                    ["start_date", "end_date", "duration_days",
                                     "high_pct", "low_pct", "return_pct"]
                                ].copy()
                                _occ_show["start_date"] = _occ_show["start_date"].dt.strftime("%Y-%m-%d")
                                _occ_show["end_date"]   = _occ_show["end_date"].dt.strftime("%Y-%m-%d")
                                _occ_show = _occ_show.rename(columns={
                                    "start_date":    "Start Date",
                                    "end_date":      "End Date",
                                    "duration_days": "Days",
                                    "high_pct":      "High%",
                                    "low_pct":       "Low%",
                                    "return_pct":    "Close%",
                                })
                                def _bt_occ_pct_style(v: object) -> str:
                                    if not isinstance(v, (int, float)) or pd.isna(v):
                                        return "color: #6B7280"
                                    return ("color: #00C851; font-weight:500" if v > 0
                                            else "color: #FF4444; font-weight:500")
                                _occ_styled = (
                                    _occ_show.style
                                    .format({
                                        "Days": "{:.0f}",
                                        "High%": "{:+.2f}%",
                                        "Low%": "{:+.2f}%",
                                        "Close%": "{:+.2f}%",
                                    }, na_rep="—")
                                    .map(_bt_occ_pct_style, subset=["High%", "Low%", "Close%"])
                                    .set_properties(**{"font-size": "0.76rem", "padding": "2px 6px"})
                                    .set_table_styles([
                                        {"selector": "thead th",
                                         "props": "background:#1F2937;color:#9CA3AF;font-size:0.68rem;"
                                                  "text-transform:uppercase;padding:4px 6px"},
                                    ])
                                )
                                st.dataframe(
                                    _occ_styled, hide_index=True, width="stretch",
                                    height=min(400, 38 * len(_occ_show) + 45),
                                )
                        else:
                            st.info(f"No price data available for {_sel_ticker}.")

    # ════════════════════════════════════════════════════════════════════════
    # DEEP DIVE TAB
    # ════════════════════════════════════════════════════════════════════════
    elif active_tab == "DEEP DIVE":
        st.markdown("#### Ticker Deep Dive")
        st.caption("Edge heatmap across all 16 regime combos for a single ticker, "
                   "plus occurrence-level detail.")

        if periods_df.empty:
            st.error("Regime history unavailable.")
        else:
            prices_map_dd = _get_prices_from_cache()
            available_dd  = sorted(prices_map_dd.keys())

            if not available_dd:
                st.warning(
                    "Price data not loaded. Use **⬇ Load / Refresh All Price Data** "
                    "in the sidebar to fetch all tickers."
                )
            else:
                # Build grouped selectbox options
                _dd_seps: set = set()
                _dd_opts: List[str] = []
                _dd_avail = set(available_dd)
                for _gn in BACKTEST_GROUP_NAMES:
                    _gt = [
                        t for t in HUD_GROUPS[_gn][0]
                        if t in _dd_avail and TICKER_GROUP_MAP.get(t) == _gn
                    ]
                    if not _gt:
                        continue
                    _sep = f"─── {_gn} ───"
                    _dd_seps.add(_sep)
                    _dd_opts.append(_sep)
                    _dd_opts.extend(_gt)

                _dd_default = next(
                    (i for i, o in enumerate(_dd_opts) if o == "SPY"), 0
                )
                def _dd_fmt(t: str) -> str:
                    if t in _dd_seps:
                        return t
                    proxy = data_cache.BACKTEST_PROXIES.get(t)
                    return f"{t} (proxy: {proxy})" if proxy else t

                _dd_raw = st.selectbox(
                    "Select ticker", _dd_opts,
                    index=_dd_default, key="dd_ticker",
                    format_func=_dd_fmt,
                )

                if _dd_raw in _dd_seps:
                    st.info("Select a ticker (not a group header) to analyze.")
                    st.stop()

                dd_ticker = _dd_raw
                dd_group_name = TICKER_GROUP_MAP.get(dd_ticker, "")
                ticker_prices = prices_map_dd[dd_ticker]

                _dd_proxy = data_cache.BACKTEST_PROXIES.get(dd_ticker)
                _dd_proxy_note = (
                    f" <span style='color:#FCD34D'>(proxy: {_dd_proxy})</span>"
                    if _dd_proxy else ""
                )
                st.markdown(
                    f"<div style='font-size:0.68rem;color:#6B7280;margin-bottom:8px'>"
                    f"<b style='color:#9CA3AF'>{dd_ticker}</b>{_dd_proxy_note} — {dd_group_name}</div>",
                    unsafe_allow_html=True,
                )

                # ── 4×4 Edge grid ────────────────────────────────────────
                with st.spinner(f"Building 4×4 edge grid for {dd_ticker}…"):
                    grid_4x4 = backtest_engine.deep_dive_grid(ticker_prices, periods_df, min_count=3)

                # Render as Plotly heatmap
                z_vals = grid_4x4.values.tolist()
                text_vals = [
                    [f"{v:.2f}" if not pd.isna(v) else "—" for v in row]
                    for row in z_vals
                ]
                fig_dd = go.Figure(go.Heatmap(
                    z=z_vals,
                    x=["C1", "C2", "C3", "C4"],
                    y=["G1", "G2", "G3", "G4"],
                    text=text_vals,
                    texttemplate="%{text}",
                    textfont={"size": 14, "color": "white"},
                    colorscale=[
                        [0.00, "#67000d"],
                        [0.30, "#cc3300"],
                        [0.48, "#374151"],
                        [0.52, "#374151"],
                        [0.70, "#005500"],
                        [1.00, "#00C851"],
                    ],
                    zmin=0, zmax=3, zmid=1,
                    showscale=True,
                    colorbar=dict(title="Edge", thickness=14, tickfont=dict(size=9)),
                ))
                fig_dd.update_layout(
                    height=300,
                    margin=dict(l=50, r=60, t=40, b=40),
                    paper_bgcolor="#0E1117", plot_bgcolor="#0E1117",
                    font=dict(color="#E5E7EB", size=11),
                    xaxis=dict(title="Compass Q", side="top", showgrid=False,
                               tickfont=dict(size=11)),
                    yaxis=dict(title="Grid Q", autorange="reversed", showgrid=False,
                               tickfont=dict(size=11)),
                    title=dict(text=f"{dd_ticker} — Edge by Regime", font=dict(size=13),
                               x=0.5, xanchor="center"),
                )
                st.plotly_chart(fig_dd, use_container_width=True)

                st.markdown("---")
                # ── Combo selector ────────────────────────────────────────
                dd_c1, dd_c2 = st.columns(2)
                with dd_c1:
                    dd_cq = st.selectbox(
                        "Compass Q", [1, 2, 3, 4], key="dd_cq",
                        format_func=lambda q: COMPASS_Q_LABELS[q],
                    )
                with dd_c2:
                    dd_gq = st.selectbox(
                        "Grid Q", [1, 2, 3, 4], key="dd_gq",
                        format_func=lambda q: GRID_Q_LABELS[q],
                    )

                occ_df = backtest_engine.get_occurrences(ticker_prices, periods_df, dd_gq, dd_cq)
                stats  = backtest_engine.compute_stats(occ_df)

                if occ_df.empty:
                    st.info(f"No occurrences of C{dd_cq}×G{dd_gq} found for {dd_ticker}.")
                else:
                    # Summary metrics
                    if stats:
                        mc1, mc2, mc3, mc4, mc5 = st.columns(5)
                        mc1.metric("Occurrences", stats["count"])
                        mc2.metric("Avg High%", f"{stats['avg_high_pct']:+.2f}%")
                        mc3.metric("Avg Low%",  f"{stats['avg_low_pct']:+.2f}%")
                        mc4.metric("Hit Rate",  f"{stats['hit_rate']:.1f}%")
                        mc5.metric("Edge",
                                   f"{stats['edge']:.2f}" if not pd.isna(stats["edge"]) else "—")

                    # ── Occurrence bar chart ──────────────────────────────
                    occ_sorted = occ_df.sort_values("start_date").reset_index(drop=True)
                    bar_x = [str(d.date()) for d in occ_sorted["start_date"]]

                    fig_bars = go.Figure()
                    fig_bars.add_trace(go.Bar(
                        name="High%", x=bar_x, y=occ_sorted["high_pct"],
                        marker_color="#00C851", opacity=0.8,
                    ))
                    fig_bars.add_trace(go.Bar(
                        name="Low%", x=bar_x, y=occ_sorted["low_pct"],
                        marker_color="#FF4444", opacity=0.8,
                    ))
                    fig_bars.add_trace(go.Scatter(
                        name="Return%", x=bar_x, y=occ_sorted["return_pct"],
                        mode="markers",
                        marker=dict(color="#FCD34D", size=7, symbol="diamond"),
                    ))
                    fig_bars.update_layout(
                        barmode="overlay",
                        height=260,
                        margin=dict(l=50, r=20, t=30, b=40),
                        paper_bgcolor="#0E1117", plot_bgcolor="#111827",
                        font=dict(color="#E5E7EB", size=9),
                        legend=dict(orientation="h", y=1.1),
                        xaxis=dict(tickangle=-45, showgrid=False),
                        yaxis=dict(title="% vs entry", showgrid=True,
                                   gridcolor="#1F2937", zeroline=True, zerolinecolor="#374151"),
                        title=dict(
                            text=f"{dd_ticker} C{dd_cq}×G{dd_gq} — High / Low / Return per occurrence",
                            font=dict(size=11), x=0.5, xanchor="center",
                        ),
                    )
                    st.plotly_chart(fig_bars, use_container_width=True)

                    # ── Occurrence table with row selection ───────────────
                    st.markdown(
                        "<div style='font-size:0.68rem;color:#6B7280;margin-bottom:4px'>"
                        "Click a row to view that period on the chart below.</div>",
                        unsafe_allow_html=True,
                    )
                    occ_display = occ_sorted.copy()
                    occ_display["start_date"] = occ_display["start_date"].dt.strftime("%Y-%m-%d")
                    occ_display["end_date"]   = occ_display["end_date"].dt.strftime("%Y-%m-%d")
                    occ_display = occ_display.rename(columns={
                        "start_date":    "Start",
                        "end_date":      "End",
                        "duration_days": "Days",
                        "entry_close":   "Entry",
                        "exit_close":    "Exit",
                        "high_pct":      "High%",
                        "low_pct":       "Low%",
                        "return_pct":    "Return%",
                    })

                    def _occ_pct_style(v: object) -> str:
                        if not isinstance(v, (int, float)) or pd.isna(v): return "color: #6B7280"
                        return "color: #00C851; font-weight:500" if v > 0 else "color: #FF4444; font-weight:500"

                    occ_styled = (
                        occ_display.style
                        .format({
                            "Entry": "{:.4g}", "Exit": "{:.4g}", "Days": "{:.0f}",
                            "High%": "{:+.2f}%", "Low%": "{:+.2f}%", "Return%": "{:+.2f}%",
                        }, na_rep="—")
                        .map(_occ_pct_style, subset=["High%", "Low%", "Return%"])
                        .set_properties(**{"font-size": "0.76rem", "padding": "2px 6px"})
                        .set_table_styles([
                            {"selector": "thead th",
                             "props": "background:#1F2937;color:#9CA3AF;font-size:0.68rem;"
                                      "text-transform:uppercase;padding:4px 6px"},
                        ])
                    )
                    sel = st.dataframe(
                        occ_styled,
                        hide_index=True,
                        width="stretch",
                        height=min(400, 38 * len(occ_display) + 45),
                        on_select="rerun",
                        selection_mode="single-row",
                        key="dd_occ_table",
                    )

                    # ── TradingView chart for selected occurrence ──────────
                    selected_rows = sel.selection.get("rows", []) if sel.selection else []
                    if selected_rows:
                        row_idx    = selected_rows[0]
                        sel_start  = occ_display.iloc[row_idx]["Start"]
                        sel_end    = occ_display.iloc[row_idx]["End"]
                        new_dd_sym = to_tv_symbol(dd_ticker)
                        if new_dd_sym != st.session_state["dd_tv_symbol"]:
                            st.session_state["dd_tv_symbol"] = new_dd_sym
                            st.session_state["dd_tv_render_count"] += 1
                        st.session_state["dd_tv_label"] = (sel_start, sel_end)
                    else:
                        new_dd_sym = to_tv_symbol(dd_ticker)
                        if new_dd_sym != st.session_state["dd_tv_symbol"]:
                            st.session_state["dd_tv_symbol"] = new_dd_sym
                            st.session_state["dd_tv_render_count"] += 1
                        st.session_state["dd_tv_label"] = None

                    # Chart label — show date range if a row was selected
                    if st.session_state["dd_tv_label"]:
                        s, e = st.session_state["dd_tv_label"]
                        st.markdown(
                            f"<div style='font-size:0.78rem;color:#FCD34D;"
                            f"background:#1a1f35;padding:5px 10px;border-radius:4px;"
                            f"border-left:3px solid #FCD34D;margin-bottom:4px'>"
                            f"Viewing: <b>{dd_ticker}</b> during "
                            f"<b>{s}</b> → <b>{e}</b> "
                            f"&nbsp;(TradingView free embed does not support date ranges — "
                            f"chart shows current view; range is shown above for reference)</div>",
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            f"<div style='font-size:0.68rem;color:#6B7280;margin-bottom:2px'>"
                            f"Chart: <b style='color:#9CA3AF'>{st.session_state['dd_tv_symbol']}</b>"
                            f" · select a row above to view its date range</div>",
                            unsafe_allow_html=True,
                        )

                    components.html(
                        tradingview_widget(
                            st.session_state["dd_tv_symbol"],
                            st.session_state["dd_tv_render_count"],
                        ),
                        height=480, scrolling=False,
                    )

    # ════════════════════════════════════════════════════════════════════════
    # SCENARIO TAB
    # ════════════════════════════════════════════════════════════════════════
    elif active_tab == "SCENARIO":
        st.markdown("#### Scenario — Regime Transition (PreQuad)")
        st.caption(
            "How have ETFs performed in the first N trading days after a specific "
            "regime change? Select the 'From' and 'To' regime combo and a lookforward window."
        )

        if periods_df.empty:
            st.error("Regime history unavailable.")
        else:
            sc1, sc2, sc3, sc4, sc5 = st.columns([1, 1, 1, 1, 2])
            with sc1:
                sc_from_cq = st.selectbox(
                    "From Compass", [1, 2, 3, 4], key="sc_from_cq",
                    format_func=lambda q: COMPASS_Q_LABELS[q],
                )
            with sc2:
                sc_from_gq = st.selectbox(
                    "From Grid", [1, 2, 3, 4], key="sc_from_gq",
                    format_func=lambda q: GRID_Q_LABELS[q],
                )
            with sc3:
                sc_to_cq = st.selectbox(
                    "To Compass", [1, 2, 3, 4], key="sc_to_cq",
                    format_func=lambda q: COMPASS_Q_LABELS[q],
                )
            with sc4:
                sc_to_gq = st.selectbox(
                    "To Grid", [1, 2, 3, 4], key="sc_to_gq",
                    format_func=lambda q: GRID_Q_LABELS[q],
                )
            with sc5:
                sc_n_days = st.slider("Lookforward (trading days)", 5, 120, 30, key="sc_n_days")

            if sc_from_gq == sc_to_gq and sc_from_cq == sc_to_cq:
                st.warning("'From' and 'To' regimes are identical — select a different target.")
            else:
                # Count transitions
                trans_count = 0
                for i in range(1, len(periods_df)):
                    prev = periods_df.iloc[i - 1]
                    curr = periods_df.iloc[i]
                    if (int(prev["grid_q"]) == sc_from_gq and int(prev["compass_q"]) == sc_from_cq
                            and int(curr["grid_q"]) == sc_to_gq and int(curr["compass_q"]) == sc_to_cq):
                        trans_count += 1

                st.caption(
                    f"Transition **C{sc_from_cq}×G{sc_from_gq}** → "
                    f"**C{sc_to_cq}×G{sc_to_gq}** has occurred "
                    f"**{trans_count}** time{'s' if trans_count != 1 else ''} in the DB."
                )

                prices_map_sc = _get_prices_from_cache()
                if not prices_map_sc:
                    st.warning(
                        "Price data not loaded. Use **⬇ Load / Refresh All Price Data** "
                        "in the sidebar."
                    )
                elif trans_count == 0:
                    st.info("No historical transitions found for this regime combo.")
                else:
                    with st.spinner("Computing scenario stats…"):
                        sc_table = backtest_engine.build_scenario_table(
                            BACKTEST_UNIVERSE, prices_map_sc, periods_df,
                            sc_from_gq, sc_from_cq, sc_to_gq, sc_to_cq,
                            n_days=sc_n_days, min_count=2,
                        )

                    st.caption(
                        f"First {sc_n_days} trading days after each transition · "
                        f"sorted by Edge descending · min 2 occurrences"
                    )

                    # Build display copy with proxy annotations
                    sc_display = sc_table.copy()
                    sc_display["Ticker"] = sc_display["Ticker"].apply(
                        lambda t: f"{t} (proxy: {data_cache.BACKTEST_PROXIES[t]})"
                        if t in data_cache.BACKTEST_PROXIES else t
                    )

                    # Grouped display by asset class
                    _sc_total = 0
                    for gname in BACKTEST_GROUP_NAMES:
                        _grp_tickers = {t for t, g in TICKER_GROUP_MAP.items() if g == gname}
                        _grp_mask = sc_table["Ticker"].isin(_grp_tickers)
                        _grp_df = sc_display[_grp_mask].reset_index(drop=True)
                        if _grp_df.empty:
                            continue
                        st.markdown(
                            f'<div style="background:#1a1f35;color:#8b9dc3;font-size:0.65rem;'
                            f'font-weight:700;letter-spacing:2px;padding:4px 10px;'
                            f'text-transform:uppercase;border-left:3px solid #3b4f8a;'
                            f'margin-top:14px;margin-bottom:4px">{gname}</div>',
                            unsafe_allow_html=True,
                        )
                        _render_backtest_table(_grp_df)
                        _sc_total += len(_grp_df)

                    if _sc_total == 0:
                        st.info("No tickers met the minimum occurrences threshold for this transition.")

    # ════════════════════════════════════════════════════════════════════════
    # ════════════════════════════════════════════════════════════════════════
    # RRG TAB — Relative Rotation Graph
    # ════════════════════════════════════════════════════════════════════════
    elif active_tab == "RRG":
        _DARK_RRG = dict(
            paper_bgcolor="#0E1117",
            plot_bgcolor="#0E1117",
            font=dict(color="#9CA3AF"),
            legend=dict(bgcolor="rgba(0,0,0,0)"),
            margin=dict(t=40, b=30, l=10, r=10),
        )

        def _rrg_quadrant(rs_ratio: float, rs_mom: float) -> str:
            if rs_ratio >= 100 and rs_mom >= 100:
                return "Leading"
            elif rs_ratio >= 100:
                return "Weakening"
            elif rs_mom >= 100:
                return "Improving"
            return "Lagging"

        force_rrg = st.session_state.pop("_rrg_force", False)

        # ── Controls row ───────────────────────────────────────────────────
        ctrl_c1, ctrl_c2, ctrl_c3, ctrl_c4 = st.columns([2, 2, 2, 2])
        with ctrl_c1:
            st.markdown("**Universe**")
            show_sectors     = st.checkbox("Sectors (11)",     value=True,  key="rrg_sectors")
            show_subsectors  = st.checkbox("Subsectors (30)",  value=False, key="rrg_subsectors")
            show_commodities = st.checkbox("Commodities (10)", value=False, key="rrg_commodities")
            show_regions     = st.checkbox("Regions (10)",     value=False, key="rrg_regions")
        with ctrl_c2:
            _tail_preset = st.radio(
                "Tail",
                ["1w", "2w", "5w"],
                index=0,
                horizontal=True,
                key="rrg_tail_preset",
            )
            tail_weeks = {"1w": 1, "2w": 2, "5w": 5}[_tail_preset]
        with ctrl_c3:
            eq_benchmark = st.selectbox(
                "Equity benchmark", rrg_data.BENCHMARKS, index=0, key="rrg_benchmark"
            )
        with ctrl_c4:
            st.markdown("**Commodity benchmark**")
            st.markdown("`DBC` — Invesco DB Commodity (fixed)")
            try:
                _ci       = rrg_data.rrg_cache_info()
                _eqk      = f"equity_{eq_benchmark}"
                _eq_mtime = _ci.get(_eqk, {}).get("mtime", "—")
                _cm_mtime = _ci.get("commodity_DBC", {}).get("mtime", "—")
                st.caption(f"Equity cache: {_eq_mtime}")
                st.caption(f"Commodity cache: {_cm_mtime}")
            except Exception:
                pass

        # ── Data load ──────────────────────────────────────────────────────
        rrg_equity_df    = pd.DataFrame()
        rrg_commodity_df = pd.DataFrame()
        _need_equity = show_sectors or show_subsectors or show_regions

        if _need_equity:
            with st.spinner(f"Loading equity RRG vs {eq_benchmark}…"):
                try:
                    rrg_equity_df = rrg_data.compute_rrg_equity(eq_benchmark, force=force_rrg)
                except Exception as exc_eq:
                    st.warning(f"Equity RRG unavailable: {exc_eq}")

        if show_commodities:
            with st.spinner("Loading commodity RRG vs DBC…"):
                try:
                    rrg_commodity_df = rrg_data.compute_rrg_commodity(force=force_rrg)
                except Exception as exc_cm:
                    st.warning(f"Commodity RRG unavailable: {exc_cm}")

        # ── Filter to active universe groups ───────────────────────────────
        visible: list[str] = []
        if show_sectors:
            visible += rrg_data.SECTORS
        if show_subsectors:
            visible += rrg_data.SUBSECTORS
        if show_commodities:
            visible += rrg_data.COMMODITIES
        if show_regions:
            visible += rrg_data.REGIONS

        _eq_universe  = rrg_data.SECTORS + rrg_data.SUBSECTORS + rrg_data.REGIONS
        _cm_universe  = rrg_data.COMMODITIES

        frames_to_combine: list[pd.DataFrame] = []
        if not rrg_equity_df.empty and _need_equity:
            eq_vis = [t for t in visible if t in _eq_universe]
            if eq_vis:
                frames_to_combine.append(
                    rrg_equity_df[rrg_equity_df["ticker"].isin(eq_vis)]
                )
        if not rrg_commodity_df.empty and show_commodities:
            cm_vis = [t for t in visible if t in _cm_universe]
            if cm_vis:
                frames_to_combine.append(
                    rrg_commodity_df[rrg_commodity_df["ticker"].isin(cm_vis)]
                )

        if not frames_to_combine or not visible:
            st.info("Select at least one universe group — data loads on first selection.")
        else:
            rrg_all = pd.concat(frames_to_combine).sort_values(["ticker", "date"])

            # ── Auto-scale axes centred on 100 ────────────────────────────
            _latest_snap = (
                rrg_all.sort_values("date")
                .groupby("ticker")[["RS_Ratio", "RS_Momentum"]]
                .last()
                .dropna()
            )
            _x_spread = ((_latest_snap["RS_Ratio"]    - 100.0).abs().max() if not _latest_snap.empty else 5.0)
            _y_spread = ((_latest_snap["RS_Momentum"]  - 100.0).abs().max() if not _latest_snap.empty else 5.0)
            _x_pad    = max(_x_spread * 1.18, 5.0)
            _y_pad    = max(_y_spread * 1.18, 5.0)
            x_range   = [100.0 - _x_pad, 100.0 + _x_pad]
            y_range   = [100.0 - _y_pad, 100.0 + _y_pad]

            # ── Figure ────────────────────────────────────────────────────
            fig_rrg = go.Figure()

            # Quadrant fills using large data coords (auto-scale safe)
            _BIG = 500.0
            for _fx0, _fx1, _fy0, _fy1, _fc in [
                (100.0,  _BIG, 100.0,  _BIG, "rgba(34,197,94,0.08)"),    # Leading
                (100.0,  _BIG, -_BIG, 100.0, "rgba(234,179,8,0.08)"),    # Weakening
                (-_BIG, 100.0, -_BIG, 100.0, "rgba(239,68,68,0.08)"),    # Lagging
                (-_BIG, 100.0, 100.0,  _BIG, "rgba(96,165,250,0.08)"),   # Improving
            ]:
                fig_rrg.add_shape(
                    type="rect", xref="x", yref="y",
                    x0=_fx0, x1=_fx1, y0=_fy0, y1=_fy1,
                    fillcolor=_fc, line_width=0, layer="below",
                )

            # Quadrant labels (paper coords — always in visual corners)
            for _ql, _qx, _qy, _qxa, _qya, _qc in [
                ("LEADING",   0.97, 0.97, "right", "top",    "#22C55E"),
                ("WEAKENING", 0.97, 0.03, "right", "bottom", "#EAB308"),
                ("LAGGING",   0.03, 0.03, "left",  "bottom", "#EF4444"),
                ("IMPROVING", 0.03, 0.97, "left",  "top",    "#60A5FA"),
            ]:
                fig_rrg.add_annotation(
                    text=_ql, xref="paper", yref="paper",
                    x=_qx, y=_qy,
                    xanchor=_qxa, yanchor=_qya,
                    font=dict(size=11, color=_qc, family="monospace"),
                    showarrow=False, opacity=0.65,
                )

            # Center crosshairs
            fig_rrg.add_vline(x=100, line_color="#374151", line_width=1, line_dash="dot")
            fig_rrg.add_hline(y=100, line_color="#374151", line_width=1, line_dash="dot")

            # Traces: one tail line + one latest dot per ticker
            for ticker in visible:
                sub = rrg_all[rrg_all["ticker"] == ticker].sort_values("date")
                if sub.empty:
                    continue
                latest   = sub.iloc[-1]
                tail     = sub.tail(tail_weeks)
                color    = rrg_data.ALL_COLORS.get(ticker, "#9CA3AF")
                is_short = ticker in rrg_data.SHORT_HISTORY_TICKERS
                dot_size = 9 if is_short else 11
                label    = f"{ticker}*" if is_short else ticker

                # Tail line (opacity 0.25 — subtler than before)
                if len(tail) > 1:
                    fig_rrg.add_trace(go.Scatter(
                        x=tail["RS_Ratio"].tolist(),
                        y=tail["RS_Momentum"].tolist(),
                        mode="lines",
                        line=dict(color=color, width=1.5),
                        opacity=0.25,
                        hoverinfo="skip",
                        showlegend=False,
                        name=ticker,
                    ))

                # Latest dot + label
                quad = _rrg_quadrant(float(latest["RS_Ratio"]), float(latest["RS_Momentum"]))
                fig_rrg.add_trace(go.Scatter(
                    x=[float(latest["RS_Ratio"])],
                    y=[float(latest["RS_Momentum"])],
                    mode="markers+text",
                    marker=dict(
                        size=dot_size, color=color, opacity=0.92,
                        line=dict(width=1.5, color="#111827"),
                    ),
                    text=[label],
                    textfont=dict(size=9, color=color),
                    textposition="top center",
                    customdata=[[
                        ticker, quad,
                        round(float(latest["RS_Ratio"]),    2),
                        round(float(latest["RS_Momentum"]), 2),
                        str(latest["date"])[:10],
                    ]],
                    hovertemplate=(
                        "<b>%{customdata[0]}</b>  [%{customdata[1]}]<br>"
                        "RS-Ratio:   %{customdata[2]:.2f}<br>"
                        "RS-Mom:     %{customdata[3]:.2f}<br>"
                        "Week of:    %{customdata[4]}<extra></extra>"
                    ),
                    showlegend=False,
                    name=ticker,
                ))

            fig_rrg.update_layout(
                **_DARK_RRG,
                height=700,
                xaxis=dict(
                    title="RS-Ratio  (>100 = outperforming benchmark)",
                    range=x_range,
                    gridcolor="#1F2937",
                    zeroline=False,
                ),
                yaxis=dict(
                    title="RS-Momentum  (>100 = accelerating outperformance)",
                    range=y_range,
                    gridcolor="#1F2937",
                    zeroline=False,
                    scaleanchor="x",
                    scaleratio=1,
                ),
                showlegend=False,
            )
            st.plotly_chart(fig_rrg, use_container_width=True)

            # Captions
            _eq_groups: list[str] = []
            if show_sectors:
                _eq_groups.append("Sectors")
            if show_subsectors:
                _eq_groups.append("Subsectors")
            if show_regions:
                _eq_groups.append("Regions")

            _cap_lines: list[str] = []
            if _eq_groups:
                _cap_lines.append(
                    f"{' & '.join(_eq_groups)} benchmarked vs **{eq_benchmark}**"
                )
            if show_commodities:
                _cap_lines.append(
                    "Commodities benchmarked vs **DBC** (Invesco DB Commodity Index)"
                )
            if _cap_lines:
                st.caption("  ·  ".join(_cap_lines))
            st.caption(
                f"Tail = {_tail_preset}  ·  "
                "\\* = limited history (<5yr)  ·  "
                "RS-Ratio = 100 × raw\\_rs / SMA₁₀wk  ·  "
                "RS-Mom = 100 × RS-Ratio / SMA₅wk  ·  "
                "Axes auto-scaled to data"
            )

    # ════════════════════════════════════════════════════════════════════════
    # CLOCK TAB
    # ════════════════════════════════════════════════════════════════════════
    elif active_tab == "CLOCK":
        st.markdown("#### CLOCK — Bull / Bear Trend per Index")
        st.caption(
            "Q2 = Bullish Trend (Strong Bull) · Q3 = Bullish Sentiment (Weak Bull) · "
            "Q4 = Bearish Trend (Strong Bear) · Q1 = Bearish Sentiment (Weak Bear)"
        )

        if not clock_data or not clock_data.get("quadrants"):
            st.warning("No CLOCK data found in this file.")
        else:
            # ── Helper: quadrant label → color ──────────────────────────
            def _qlabel_color(label: str) -> str:
                m = re.match(r"^(\d+)", str(label))
                if m:
                    return CLOCK_Q_COLOR.get(int(m.group(1)), "#9CA3AF")
                return "#9CA3AF"

            def _qlabel_style(v: object) -> str:
                return f"color: {_qlabel_color(str(v))}; font-weight:600"

            # ── Helper: indicator remark → color ─────────────────────────
            _REMARK_COLORS = {
                "bullish":                    "#00C851",
                "loose lending expectation":  "#00C851",
                "stable bonds":               "#00C851",
                "oversold":                   "#00C851",
                "range":                      "#FCD34D",
                "neutral":                    "#FCD34D",
                "bearish":                    "#FF4444",
                "tight lending expectation":  "#FF4444",
                "overbought":                 "#FF4444",
            }
            def _remark_style(v: object) -> str:
                key = str(v).lower().strip()
                color = _REMARK_COLORS.get(key, "#9CA3AF")
                return f"color: {color}; font-weight:500"

            cl1, cl2 = st.columns(2, gap="large")

            # ── SECTION 1: Key Indicator Values ──────────────────────────
            with cl1:
                st.markdown(
                    '<div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;'
                    'text-transform:uppercase;margin-bottom:6px">KEY INDICATORS</div>',
                    unsafe_allow_html=True,
                )
                ki_rows = clock_data.get("key_indicators", [])
                if ki_rows:
                    ki_df = pd.DataFrame(ki_rows)
                    ki_styled = (
                        ki_df.style
                        .format({"Cur Value": "{:.4g}", "Ref Value": "{:.4g}"}, na_rep="—")
                        .set_properties(**{"font-size": "0.76rem", "padding": "2px 6px"})
                        .set_table_styles([
                            {"selector": "thead th",
                             "props": "background:#1F2937;color:#9CA3AF;font-size:0.66rem;"
                                      "text-transform:uppercase;padding:3px 6px"},
                        ])
                    )
                    st.dataframe(ki_styled, hide_index=True, width="stretch",
                                 height=min(420, 35 * len(ki_df) + 40))

            # ── SECTION 4: Indicator Signals ─────────────────────────────
            with cl2:
                st.markdown(
                    '<div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;'
                    'text-transform:uppercase;margin-bottom:6px">INDICATOR SIGNALS</div>',
                    unsafe_allow_html=True,
                )
                sig_rows = clock_data.get("signal_remarks", [])
                if sig_rows:
                    sig_df = pd.DataFrame(sig_rows)
                    sig_styled = (
                        sig_df.style
                        .map(_remark_style, subset=["Remarks"])
                        .set_properties(**{"font-size": "0.80rem", "padding": "3px 8px"})
                        .set_table_styles([
                            {"selector": "thead th",
                             "props": "background:#1F2937;color:#9CA3AF;font-size:0.66rem;"
                                      "text-transform:uppercase;padding:3px 8px"},
                        ])
                    )
                    st.dataframe(sig_styled, hide_index=True, width="stretch",
                                 height=min(320, 38 * len(sig_df) + 40))

            st.markdown("---")

            # ── SECTION 2: Quadrants ─────────────────────────────────────
            cq1, cq2 = st.columns(2, gap="large")
            with cq1:
                st.markdown(
                    '<div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;'
                    'text-transform:uppercase;margin-bottom:6px">QUADRANTS (Clock Model)</div>',
                    unsafe_allow_html=True,
                )
                q_rows = clock_data.get("quadrants", [])
                if q_rows:
                    q_df = pd.DataFrame([{
                        "Symbol":   r["Symbol"],
                        "Quadrant": r["Q Label"],
                        "Since":    r["Since"],
                    } for r in q_rows])
                    q_styled = (
                        q_df.style
                        .map(_qlabel_style, subset=["Quadrant"])
                        .set_properties(**{"font-size": "0.80rem", "padding": "3px 8px"})
                        .set_table_styles([
                            {"selector": "thead th",
                             "props": "background:#1F2937;color:#9CA3AF;font-size:0.66rem;"
                                      "text-transform:uppercase;padding:3px 8px"},
                        ])
                    )
                    st.dataframe(q_styled, hide_index=True, width="stretch",
                                 height=min(280, 38 * len(q_df) + 40))

            # ── SECTION 3: EMA-derived Quadrants ─────────────────────────
            with cq2:
                st.markdown(
                    '<div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;'
                    'text-transform:uppercase;margin-bottom:6px">EMA-DERIVED QUADRANTS</div>',
                    unsafe_allow_html=True,
                )
                ema_rows = clock_data.get("ema_quadrants", [])
                if ema_rows:
                    ema_df = pd.DataFrame([{
                        "Symbol":    r["Symbol"],
                        "EMA-100D":  r["EMA100"],
                        "EMA-200D":  r["EMA200"],
                    } for r in ema_rows])
                    ema_styled = (
                        ema_df.style
                        .map(_qlabel_style, subset=["EMA-100D", "EMA-200D"])
                        .set_properties(**{"font-size": "0.80rem", "padding": "3px 8px"})
                        .set_table_styles([
                            {"selector": "thead th",
                             "props": "background:#1F2937;color:#9CA3AF;font-size:0.66rem;"
                                      "text-transform:uppercase;padding:3px 8px"},
                        ])
                    )
                    st.dataframe(ema_styled, hide_index=True, width="stretch",
                                 height=min(280, 38 * len(ema_df) + 40))

    # ════════════════════════════════════════════════════════════════════════
    # MACRO TAB
    # ════════════════════════════════════════════════════════════════════════
    elif active_tab == "MACRO":
        _DARK = dict(
            paper_bgcolor="#0E1117",
            plot_bgcolor="#0E1117",
            font=dict(color="#9CA3AF"),
            legend=dict(bgcolor="rgba(0,0,0,0)"),
            margin=dict(t=30, b=20, l=0, r=0),
        )

        force = st.session_state.pop("_macro_force", False)

        # Staleness banner (read-only, no button — button is in sidebar)
        staleness    = macro_data.macro_staleness()
        stale_names  = [k for k, v in staleness.items() if v]
        if stale_names:
            st.warning(f"Stale caches: {', '.join(stale_names)} — use ⬇ Refresh Macro Data in the sidebar.")

        st.markdown("---")

        # ══════════════════════════════════════════════════════════════════════
        # PANEL 1 — Fed Funds Rate Range
        # ══════════════════════════════════════════════════════════════════════
        st.markdown(
            '<div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;'
            'text-transform:uppercase;margin-bottom:4px">FED FUNDS TARGET RANGE</div>',
            unsafe_allow_html=True,
        )
        try:
            ffr_df = macro_data.fetch_fed_funds_range(force=force)
            if not ffr_df.empty:
                ffr_df["date"] = pd.to_datetime(ffr_df["date"])
                latest_ffr = ffr_df.sort_values("date").dropna(subset=["upper", "lower"]).iloc[-1]
                upper_val  = float(latest_ffr["upper"])
                lower_val  = float(latest_ffr["lower"])
                as_of_ffr  = pd.Timestamp(latest_ffr["date"])
                st.metric(
                    "Current Target Range",
                    f"{lower_val:.2f}% – {upper_val:.2f}%",
                    help=f"DFEDTARU (DynamoDB) & DFEDTARL (FRED) · as of {as_of_ffr:%Y-%m-%d}",
                )
                # Step chart
                ffr_plot = ffr_df.dropna(subset=["upper", "lower"])
                fig_ffr = go.Figure()
                fig_ffr.add_trace(go.Scatter(
                    x=ffr_plot["date"], y=ffr_plot["upper"],
                    name="Upper Target", line=dict(color="#F59E0B", width=2, shape="hv"),
                    fill=None,
                ))
                fig_ffr.add_trace(go.Scatter(
                    x=ffr_plot["date"], y=ffr_plot["lower"],
                    name="Lower Target", line=dict(color="#60A5FA", width=2, shape="hv"),
                    fill="tonexty", fillcolor="rgba(96,165,250,0.10)",
                ))
                fig_ffr.update_layout(
                    **_DARK, height=260,
                    xaxis=dict(gridcolor="#1F2937"),
                    yaxis=dict(gridcolor="#1F2937", title="Rate (%)"),
                )
                st.plotly_chart(fig_ffr, use_container_width=True)
            else:
                st.info("No Fed Funds Range data — add FRED_API_KEY to .streamlit/secrets.toml.")
        except Exception as exc:
            st.warning(f"Fed Funds Range unavailable: {exc}")

        st.markdown("---")

        # ══════════════════════════════════════════════════════════════════════
        # PANEL 2 — FOMC Probabilities + Dot Plot
        # ══════════════════════════════════════════════════════════════════════
        st.markdown(
            '<div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;'
            'text-transform:uppercase;margin-bottom:4px">FOMC RATE PROBABILITIES (CME FedWatch)</div>',
            unsafe_allow_html=True,
        )
        try:
            meetings   = fomc_data.get_upcoming_meetings()
            effr_df    = fomc_data.fetch_effr(force=force)
            ffr_range  = macro_data.fetch_fed_funds_range(force=False)  # already fetched

            if not ffr_range.empty:
                _lr = ffr_range.dropna(subset=["upper", "lower"]).sort_values("date").iloc[-1]
                _upper_t = float(_lr["upper"])
                _lower_t = float(_lr["lower"])
            else:
                _upper_t = 3.75
                _lower_t = 3.50

            futures = fomc_data.fetch_futures_for_meetings(meetings, force=force)
            probs   = fomc_data.compute_fomc_probs(meetings, effr_df, _upper_t, _lower_t, futures)

            if probs:
                next_mtg = probs[0]
                # Horizontal bar for next meeting
                outcome_labels = ["Hike 25bp", "Hold", "Cut 25bp"]
                outcome_vals   = [
                    next_mtg["p_hike"] * 100,
                    next_mtg["p_hold"] * 100,
                    next_mtg["p_cut"]  * 100,
                ]
                bar_colors_fomc = ["#EF4444", "#6B7280", "#22C55E"]
                fig_prob = go.Figure(go.Bar(
                    x=outcome_vals,
                    y=outcome_labels,
                    orientation="h",
                    marker_color=bar_colors_fomc,
                    text=[f"{v:.1f}%" for v in outcome_vals],
                    textposition="outside",
                    textfont=dict(color="#9CA3AF"),
                ))
                fig_prob.update_layout(
                    **_DARK,
                    height=200,
                    title=dict(
                        text=f"Next FOMC: {next_mtg['date'].strftime('%b %d, %Y')}  "
                             f"(implied avg {next_mtg['implied_avg']:.3f}%)",
                        font=dict(size=12, color="#9CA3AF"),
                    ),
                    xaxis=dict(range=[0, 110], gridcolor="#1F2937", title="Probability (%)"),
                    yaxis=dict(gridcolor="rgba(0,0,0,0)"),
                )
                st.plotly_chart(fig_prob, use_container_width=True)

                # Table of next 3-4 meetings
                if len(probs) > 1:
                    tbl_rows = []
                    for p in probs[:4]:
                        tbl_rows.append({
                            "Date":        p["date"].strftime("%b %d, %Y"),
                            "Futures":     p["ticker"],
                            "Implied Avg": f"{p['implied_avg']:.3f}%",
                            "Most Likely": p["most_likely"],
                            "Probability": f"{p['prob_most_likely']*100:.1f}%",
                        })
                    tbl_df = pd.DataFrame(tbl_rows)
                    st.dataframe(
                        tbl_df.style.set_properties(**{"font-size": "0.80rem", "padding": "3px 8px"})
                        .set_table_styles([{
                            "selector": "thead th",
                            "props": "background:#1F2937;color:#9CA3AF;font-size:0.66rem;"
                                     "text-transform:uppercase;padding:3px 8px",
                        }]),
                        hide_index=True, use_container_width=True,
                    )
            else:
                st.info("No upcoming meetings with futures data found.")
        except Exception as exc:
            st.warning(f"FOMC probabilities unavailable: {exc}")

        # Dot Plot
        st.markdown(
            '<div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;'
            'text-transform:uppercase;margin-top:16px;margin-bottom:4px">SEP DOT PLOT'
            ' — PROJECTED APPROPRIATE POLICY RATE</div>',
            unsafe_allow_html=True,
        )
        try:
            dot_df = fomc_data.load_dot_plot()
            if dot_df.empty:
                st.info("No dot plot data — populate data_manual/dot_plot.csv from the latest SEP PDF.")
            else:
                horizon_order  = ["2026", "2027", "2028", "LR"]
                avail_horizons = [h for h in horizon_order if h in dot_df["year"].values]
                _x_labels      = {"2026": "End-2026", "2027": "End-2027", "2028": "End-2028", "LR": "Longer Run"}
                dot_colors     = {"2026": "#60A5FA", "2027": "#34D399", "2028": "#F59E0B", "LR": "#A78BFA"}
                medians        = dot_df.groupby("year")["projected_rate"].median()

                fig_dot = go.Figure()
                for i, horizon in enumerate(avail_horizons):
                    sub = dot_df[dot_df["year"] == horizon].copy()
                    # Per-rate-level jitter: group dots at same rate, spread evenly ±0.15
                    x_pos: list[float] = []
                    for _rate, grp in sub.groupby("projected_rate"):
                        n = len(grp)
                        offsets = [0.0] if n == 1 else [k * 0.30 / (n - 1) - 0.15 for k in range(n)]
                        x_pos.extend(i + o for o in offsets)
                    fig_dot.add_trace(go.Scatter(
                        x=x_pos,
                        y=sub["projected_rate"].tolist(),
                        mode="markers",
                        name=horizon,
                        marker=dict(
                            size=10, color=dot_colors.get(horizon, "#9CA3AF"),
                            opacity=0.75, line=dict(width=1, color="#0E1117"),
                        ),
                        showlegend=False,
                    ))
                    if horizon in medians.index:
                        fig_dot.add_shape(
                            type="line",
                            x0=i - 0.35, x1=i + 0.35,
                            y0=medians[horizon], y1=medians[horizon],
                            xref="x", yref="y",
                            line=dict(color=dot_colors.get(horizon, "#9CA3AF"), width=3),
                        )

                fig_dot.update_layout(
                    **_DARK,
                    height=320,
                    xaxis=dict(
                        gridcolor="#1F2937",
                        tickvals=list(range(len(avail_horizons))),
                        ticktext=[_x_labels.get(h, h) for h in avail_horizons],
                        range=[-0.6, len(avail_horizons) - 0.4],
                    ),
                    yaxis=dict(gridcolor="#1F2937", title="Target Rate (%)"),
                    showlegend=False,
                )
                st.plotly_chart(fig_dot, use_container_width=True)
                n_participants = len(dot_df[dot_df["year"] == avail_horizons[0]])
                st.caption(
                    f"March 2026 SEP · {n_participants} participants · Thick bars = median. "
                    "Source: FOMC Projection Materials fomcprojtabl20260318.pdf"
                )
        except Exception as exc:
            st.warning(f"Dot plot unavailable: {exc}")

        st.markdown("---")

        # ══════════════════════════════════════════════════════════════════════
        # PANEL 3 — Treasury Yield Curve (existing, unchanged)
        # ══════════════════════════════════════════════════════════════════════
        st.markdown(
            '<div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;'
            'text-transform:uppercase;margin-bottom:4px">TREASURY YIELD CURVE (FRED DGS)</div>',
            unsafe_allow_html=True,
        )
        try:
            curve_df = macro_data.fetch_treasury_curve(force=force)
            if not curve_df.empty:
                TENOR_ORDER = ["1M", "3M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]
                avail = [t for t in TENOR_ORDER if t in curve_df.columns]
                fig_curve = make_subplots(
                    rows=1, cols=2,
                    subplot_titles=["Curve Snapshot", "10Y Historical (20Y)"],
                    column_widths=[0.38, 0.62],
                )
                non_null    = curve_df.dropna(how="all", subset=avail)
                latest_row  = non_null.iloc[-1]
                latest_date = pd.Timestamp(latest_row["date"])
                ago_6m_cut  = latest_date - pd.DateOffset(months=6)
                ago_1y_cut  = latest_date - pd.DateOffset(months=12)
                ago_6m_rows = non_null[non_null["date"] <= ago_6m_cut]
                ago_1y_rows = non_null[non_null["date"] <= ago_1y_cut]
                for snap_row, label, color in [
                    (latest_row, "Latest", "#60A5FA"),
                    (ago_6m_rows.iloc[-1] if not ago_6m_rows.empty else None, "6M Ago", "#F59E0B"),
                    (ago_1y_rows.iloc[-1] if not ago_1y_rows.empty else None, "1Y Ago", "#9CA3AF"),
                ]:
                    if snap_row is None:
                        continue
                    fig_curve.add_trace(go.Scatter(
                        x=avail, y=[snap_row.get(t) for t in avail],
                        mode="lines+markers", name=label,
                        line=dict(color=color, width=2),
                    ), row=1, col=1)
                if "10Y" in curve_df.columns:
                    hist_10y = curve_df[["date", "10Y"]].dropna()
                    fig_curve.add_trace(go.Scatter(
                        x=hist_10y["date"], y=hist_10y["10Y"],
                        name="10Y Yield", line=dict(color="#60A5FA", width=1.5), showlegend=False,
                    ), row=1, col=2)
                fig_curve.update_layout(**_DARK, height=340)
                fig_curve.update_xaxes(gridcolor="#1F2937")
                fig_curve.update_yaxes(gridcolor="#1F2937", title_text="Yield (%)", col=1)
                fig_curve.update_yaxes(gridcolor="#1F2937", title_text="Yield (%)", col=2)
                st.plotly_chart(fig_curve, use_container_width=True)
            else:
                st.info("No yield curve data — add FRED_API_KEY to .streamlit/secrets.toml.")
        except Exception as exc:
            st.warning(f"Yield curve data unavailable: {exc}")

        st.markdown("---")

        # ══════════════════════════════════════════════════════════════════════
        # PANEL 4 — Spreads (existing, unchanged)
        # ══════════════════════════════════════════════════════════════════════
        st.markdown(
            '<div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;'
            'text-transform:uppercase;margin-bottom:4px">SPREADS — 10Y–2Y & HY OAS</div>',
            unsafe_allow_html=True,
        )
        try:
            spread_df = macro_data.fetch_spreads(force=force)
            if not spread_df.empty:
                fig_sp = make_subplots(specs=[[{"secondary_y": True}]])
                if "T10Y2Y" in spread_df.columns:
                    t10y2y = spread_df[["date", "T10Y2Y"]].dropna()
                    fig_sp.add_trace(go.Scatter(
                        x=t10y2y["date"], y=t10y2y["T10Y2Y"],
                        name="10Y–2Y (%)", line=dict(color="#60A5FA", width=1.5),
                    ), secondary_y=False)
                    fig_sp.add_hline(y=0, line_color="#4B5563", line_width=1)
                if "HY_Spread" in spread_df.columns:
                    hy = spread_df[["date", "HY_Spread"]].dropna()
                    fig_sp.add_trace(go.Scatter(
                        x=hy["date"], y=hy["HY_Spread"],
                        name="HY OAS (bp)", line=dict(color="#F87171", width=1.5),
                    ), secondary_y=True)
                fig_sp.update_layout(
                    **_DARK, height=320, xaxis=dict(gridcolor="#1F2937"),
                )
                fig_sp.update_yaxes(title_text="10Y–2Y (%)", gridcolor="#1F2937", secondary_y=False)
                fig_sp.update_yaxes(title_text="HY OAS (bp)", gridcolor="#1F2937", secondary_y=True)
                st.plotly_chart(fig_sp, use_container_width=True)
            else:
                st.info("No spread data — add FRED_API_KEY to .streamlit/secrets.toml.")
        except Exception as exc:
            st.warning(f"Spread data unavailable: {exc}")

        st.markdown("---")

        # ══════════════════════════════════════════════════════════════════════
        # PANEL 5 — Bank Lending Standards
        # ══════════════════════════════════════════════════════════════════════
        st.markdown(
            '<div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;'
            'text-transform:uppercase;margin-bottom:4px">BANK LENDING STANDARDS — C&amp;I LOANS'
            ' (Net % Tightening)</div>',
            unsafe_allow_html=True,
        )
        try:
            lend_df = macro_data.fetch_lending_standards(force=force)
            if not lend_df.empty:
                lend_sorted = lend_df.sort_values("date").copy()
                latest_lend = lend_sorted.iloc[-1]
                prior_lend  = lend_sorted.iloc[-2] if len(lend_sorted) > 1 else None
                delta_lend  = (
                    round(float(latest_lend["value"]) - float(prior_lend["value"]), 1)
                    if prior_lend is not None else None
                )
                st.metric(
                    "Latest Reading",
                    f"{float(latest_lend['value']):.1f}%",
                    delta=f"{delta_lend:+.1f}pp vs prior quarter" if delta_lend is not None else None,
                    help=f"FRED DRTSCILM · as of {pd.Timestamp(latest_lend['date']):%Y-%m-%d}",
                )

                # Top sub-chart: level as a step line
                fig_lend_lvl = go.Figure(go.Scatter(
                    x=lend_sorted["date"],
                    y=lend_sorted["value"],
                    mode="lines",
                    line=dict(color="#60A5FA", width=2, shape="hv"),
                    fill="tozeroy",
                    fillcolor="rgba(96,165,250,0.08)",
                    name="Net % Tightening",
                ))
                fig_lend_lvl.add_hline(y=0, line_color="#4B5563", line_width=1)
                fig_lend_lvl.update_layout(
                    **_DARK, height=220,
                    title=dict(text="Level — Net % Tightening C&I Loans (Senior Loan Officer Survey)",
                               font=dict(size=11, color="#9CA3AF")),
                    xaxis=dict(gridcolor="#1F2937"),
                    yaxis=dict(gridcolor="#1F2937", title="Net % Tightening"),
                    showlegend=False,
                )
                st.plotly_chart(fig_lend_lvl, use_container_width=True)

                # Bottom sub-chart: QoQ change bars  (red=more tightening, green=more loosening)
                lend_sorted["qoq_chg"] = lend_sorted["value"].diff()
                chg_colors = ["#EF4444" if v > 0 else "#22C55E" for v in lend_sorted["qoq_chg"]]
                fig_lend_chg = go.Figure(go.Bar(
                    x=lend_sorted["date"],
                    y=lend_sorted["qoq_chg"],
                    marker_color=chg_colors,
                    name="QoQ Change (pp)",
                ))
                fig_lend_chg.add_hline(y=0, line_color="#4B5563", line_width=1)
                fig_lend_chg.update_layout(
                    **_DARK, height=200,
                    title=dict(text="QoQ Change (pp) — Red = More Tightening · Green = More Loosening",
                               font=dict(size=11, color="#9CA3AF")),
                    xaxis=dict(gridcolor="#1F2937"),
                    yaxis=dict(gridcolor="#1F2937", title="pp change"),
                    showlegend=False,
                )
                st.plotly_chart(fig_lend_chg, use_container_width=True)
            else:
                st.info("No lending standards data — add FRED_API_KEY to .streamlit/secrets.toml.")
        except Exception as exc:
            st.warning(f"Lending standards unavailable: {exc}")

        st.markdown("---")

        # ══════════════════════════════════════════════════════════════════════
        # PANEL 6 — GDP / Growth
        # ══════════════════════════════════════════════════════════════════════
        st.markdown(
            '<div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;'
            'text-transform:uppercase;margin-bottom:4px">REAL GDP GROWTH — QoQ ANNUALIZED (%)</div>',
            unsafe_allow_html=True,
        )
        try:
            gdp_df    = macro_data.fetch_gdp(force=force)
            gdpnow_df = macro_data.fetch_gdpnow(force=force)

            # Metric
            if not gdp_df.empty:
                _lat = gdp_df.sort_values("date").iloc[-1]
                _prv = gdp_df.sort_values("date").iloc[-2] if len(gdp_df) > 1 else None
                st.metric(
                    "Latest QoQ (ann.)",
                    f"{_lat['gdp_pct']:.1f}%",
                    delta=f"{_lat['gdp_pct'] - _prv['gdp_pct']:.1f}pp vs prior quarter" if _prv is not None else None,
                    help=f"BEA NIPA T10101 · {pd.Timestamp(_lat['date']):%Y-%m-%d}",
                )

            # ── 3a: BEA bars at quarter-end + GDPNow continuous overlay ────────
            cutoff_3a = pd.Timestamp.now() - pd.DateOffset(years=5)
            gdp_3a    = gdp_df[gdp_df["date"] >= cutoff_3a].copy()

            # Map quarter-start → quarter-end for bar positions
            gdp_3a["qend"] = (
                gdp_3a["date"] + pd.DateOffset(months=2) + pd.offsets.MonthEnd(0)
            )

            bar_colors_3a = ["#EF4444" if v < 0 else "#22C55E" for v in gdp_3a["gdp_pct"]]
            fig_3a = go.Figure()
            fig_3a.add_trace(go.Bar(
                x=gdp_3a["qend"], y=gdp_3a["gdp_pct"],
                marker_color=bar_colors_3a, name="BEA (latest vintage)",
                width=60 * 24 * 3600 * 1000 * 70,  # ~70-day wide bars in ms
            ))
            if not gdpnow_df.empty:
                gdpnow_3a = gdpnow_df[gdpnow_df["date"] >= cutoff_3a].sort_values("date")
                fig_3a.add_trace(go.Scatter(
                    x=gdpnow_3a["date"], y=gdpnow_3a["gdpnow"],
                    mode="lines", name="GDPNow (Atlanta Fed)",
                    line=dict(color="#F59E0B", width=2, dash="dot"),
                ))
            fig_3a.add_hline(y=0, line_color="#4B5563", line_width=1)
            fig_3a.update_layout(
                **_DARK, height=300,
                title=dict(text="3a. BEA Quarterly GDP (bars, quarter-end) vs GDPNow Real-Time Nowcast (line)",
                           font=dict(size=11, color="#9CA3AF")),
                xaxis=dict(gridcolor="#1F2937"),
                yaxis=dict(gridcolor="#1F2937", title="% annualized"),
                legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
            )
            st.plotly_chart(fig_3a, use_container_width=True)

            # ── 3b: Three sequential BEA estimates per quarter (ALFRED) ─────────
            try:
                vint_df = macro_data.fetch_gdp_vintages(force=force)
                if not vint_df.empty:
                    vint_df = vint_df.sort_values(["quarter", "vintage"]).copy()
                    # Quarter-end labels for x-axis
                    vint_df["qend"] = (
                        vint_df["quarter"] + pd.DateOffset(months=2) + pd.offsets.MonthEnd(0)
                    )
                    vintage_colors = {"Advance": "#60A5FA", "Second": "#F59E0B", "Third": "#A78BFA"}
                    fig_3b = go.Figure()
                    for vname in ["Advance", "Second", "Third"]:
                        sub_v = vint_df[vint_df["vintage"] == vname]
                        if sub_v.empty:
                            continue
                        fig_3b.add_trace(go.Bar(
                            x=sub_v["qend"],
                            y=sub_v["value"],
                            name=vname,
                            marker_color=vintage_colors[vname],
                        ))
                    fig_3b.add_hline(y=0, line_color="#4B5563", line_width=1)
                    fig_3b.update_layout(
                        **_DARK, height=280, barmode="group",
                        title=dict(
                            text="3b. Three BEA Estimates per Quarter (ALFRED) — Advance / Second / Third",
                            font=dict(size=11, color="#9CA3AF"),
                        ),
                        xaxis=dict(gridcolor="#1F2937"),
                        yaxis=dict(gridcolor="#1F2937", title="% annualized"),
                        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
                    )
                    st.plotly_chart(fig_3b, use_container_width=True)
                    # Warn if ALFRED gaps detected
                    expected_vintages = 3
                    quarters_with_all = (
                        vint_df.groupby("quarter")["vintage"].nunique() == expected_vintages
                    ).sum()
                    total_quarters = vint_df["quarter"].nunique()
                    if quarters_with_all < total_quarters:
                        st.caption(
                            f"Note: {total_quarters - quarters_with_all} of {total_quarters} recent quarters "
                            "are missing one or more ALFRED vintages — ALFRED does not always capture the BEA "
                            "Advance estimate for very recent quarters."
                        )
                else:
                    st.caption("ALFRED vintage data not yet available — add FRED_API_KEY and refresh.")
            except Exception as exc_vint:
                st.caption(f"GDP vintage chart unavailable: {exc_vint}")

            # ── 3c: GDPNow time series (standalone) ─────────────────────────────
            if not gdpnow_df.empty:
                gdpnow_12m = gdpnow_df[
                    gdpnow_df["date"] >= (pd.Timestamp.now() - pd.DateOffset(months=12))
                ].sort_values("date")
                if not gdpnow_12m.empty:
                    fig_3c = go.Figure(go.Scatter(
                        x=gdpnow_12m["date"],
                        y=gdpnow_12m["gdpnow"],
                        mode="lines+markers",
                        line=dict(color="#F59E0B", width=2),
                        marker=dict(size=4, color="#F59E0B"),
                        name="GDPNow",
                    ))
                    fig_3c.add_hline(y=0, line_color="#4B5563", line_width=1)
                    _now_latest = gdpnow_12m.iloc[-1]
                    fig_3c.update_layout(
                        **_DARK, height=240,
                        title=dict(
                            text=f"3c. GDPNow Time Series — Current Estimate: {_now_latest['gdpnow']:.1f}% "
                                 f"(as of {pd.Timestamp(_now_latest['date']):%b %d})",
                            font=dict(size=11, color="#9CA3AF"),
                        ),
                        xaxis=dict(gridcolor="#1F2937"),
                        yaxis=dict(gridcolor="#1F2937", title="% annualized"),
                        showlegend=False,
                    )
                    st.plotly_chart(fig_3c, use_container_width=True)

        except Exception as exc:
            st.warning(f"GDP data unavailable: {exc}")

        st.markdown("---")

        # ══════════════════════════════════════════════════════════════════════
        # PANEL 7 — Inflation
        # ══════════════════════════════════════════════════════════════════════
        st.markdown(
            '<div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;'
            'text-transform:uppercase;margin-bottom:4px">INFLATION — YoY %</div>',
            unsafe_allow_html=True,
        )
        inf_range = st.radio(
            "Inflation range",
            ["Last 5Y", "Last 10Y", "Last 20Y", "All"],
            horizontal=True,
            index=0,
            key="macro_inf_range",
            label_visibility="collapsed",
        )
        try:
            inf_df = macro_data.fetch_inflation(force=force)
            range_map = {"Last 5Y": 5, "Last 10Y": 10, "Last 20Y": 20}
            if inf_range in range_map:
                inf_cutoff = pd.Timestamp.now() - pd.DateOffset(years=range_map[inf_range])
                inf_df = inf_df[inf_df["date"] >= inf_cutoff]

            fig_inf = go.Figure()
            for col, color in [
                ("CPI",      "#60A5FA"),
                ("Core CPI", "#34D399"),
                ("PPI",      "#F87171"),
            ]:
                if col in inf_df.columns:
                    fig_inf.add_trace(go.Scatter(
                        x=inf_df["date"], y=inf_df[col],
                        name=col, line=dict(color=color, width=1.5),
                    ))
            fig_inf.add_hline(
                y=2, line_color="#6B7280", line_dash="dot", line_width=1,
                annotation_text="2%", annotation_font_color="#6B7280",
            )
            fig_inf.update_layout(
                **_DARK,
                height=320,
                xaxis=dict(gridcolor="#1F2937"),
                yaxis=dict(gridcolor="#1F2937", title="YoY %"),
            )
            st.plotly_chart(fig_inf, use_container_width=True)
        except Exception as exc:
            st.warning(f"Inflation data unavailable: {exc}")

        st.markdown("---")

        # ── Core PCE (Panel 7b) ───────────────────────────────────────────────
        st.markdown(
            '<div style="font-size:0.62rem;color:#9CA3AF;letter-spacing:2px;'
            'text-transform:uppercase;margin-bottom:4px">CORE PCE — YoY % (Fed Target)</div>',
            unsafe_allow_html=True,
        )
        try:
            pce_df = macro_data.fetch_pce(force=force)
            if not pce_df.empty:
                pce_df = pce_df.sort_values("date").reset_index(drop=True)
                pce_df["pce_core_yoy"] = (
                    (1 + pce_df["pce_core_pct"] / 100)
                    .rolling(12)
                    .apply(lambda x: x.prod() - 1, raw=True)
                ) * 100
                pce_cutoff  = pd.Timestamp.now() - pd.DateOffset(years=10)
                pce_plot    = pce_df[pce_df["date"] >= pce_cutoff].dropna(subset=["pce_core_yoy"])
                fig_pce = go.Figure()
                fig_pce.add_trace(go.Scatter(
                    x=pce_plot["date"], y=pce_plot["pce_core_yoy"],
                    name="Core PCE YoY", line=dict(color="#A78BFA", width=1.5),
                ))
                fig_pce.add_hline(
                    y=2, line_color="#6B7280", line_dash="dot", line_width=1,
                    annotation_text="2%", annotation_font_color="#6B7280",
                )
                fig_pce.update_layout(
                    **_DARK,
                    height=270,
                    xaxis=dict(gridcolor="#1F2937"),
                    yaxis=dict(gridcolor="#1F2937", title="YoY %"),
                )
                st.plotly_chart(fig_pce, use_container_width=True)
            else:
                st.info("No PCE data available — add BEA_API_KEY to .streamlit/secrets.toml.")
        except Exception as exc:
            st.warning(f"Core PCE data unavailable: {exc}")

        st.markdown("---")

        # FedWatch escape hatch (link-only, per spec)
        st.caption(
            "For full market-implied probabilities across all meetings: "
        )
        st.link_button(
            "Open CME FedWatch ↗",
            "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html",
        )


if __name__ == "__main__":
    main()
