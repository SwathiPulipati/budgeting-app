"""Analysis tab: analysis of every CSV saved in storage/parsed-data (read-only).

CSVs use one sign convention: Amount < 0 is money out, Amount > 0 is money in.
"""
import altair as alt  # installed with Streamlit
import pandas as pd
import streamlit as st

import json

from storage import PARSED_DIR, PROJECT_DIR
from txn_keys import row_keys

DEFAULT_EXCLUDED_FILE = PROJECT_DIR / "config" / "default_excluded.json"
REQUIRED = ["Date", "Description", "Amount", "Section", "Source File"]


# ---------------------------------------------------------------- data loading
def _signature() -> tuple:
    files = sorted(PARSED_DIR.glob("*.csv")) if PARSED_DIR.is_dir() else []
    return tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files)


@st.cache_data(show_spinner=False)
def load_all(sig: tuple):
    """Read every CSV in the signature and drop transactions that appear in more than
    one file (same source statement, date, description, amount). Returns (df, info)."""
    frames, problems, rows_read = [], [], 0
    for name, _, _ in sig:
        try:
            df = pd.read_csv(PARSED_DIR / name)
        except Exception as e:  # unreadable / empty file
            problems.append(f"Skipped {name}: {e}")
            continue
        missing = [c for c in REQUIRED if c not in df.columns]
        if missing:
            problems.append(f"Skipped {name}: missing columns {', '.join(missing)}")
            continue
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce")
        df = df.dropna(subset=["Amount"]).reset_index(drop=True)
        df["Source File"] = df["Source File"].fillna("").astype(str)
        df["Description"] = df["Description"].fillna("").astype(str)
        df["Section"] = df["Section"].fillna("").astype(str)
        df["Type"] = (df["Type"] if "Type" in df.columns else pd.Series("Uncategorized", index=df.index))
        df["Type"] = df["Type"].fillna("Uncategorized").astype(str).str.strip().replace("", "Uncategorized")
        if "Account Type" not in df.columns:
            df["Account Type"] = "Unknown"
        df["Account Type"] = df["Account Type"].fillna("Unknown").astype(str).str.strip().replace("", "Unknown")
        df["_key"] = row_keys(df).map(repr).values
        df["CSV File"] = name
        rows_read += len(df)
        frames.append(df)
    if not frames:
        return pd.DataFrame(), {"files": 0, "rows_read": 0, "duplicates": 0, "problems": problems}
    out = pd.concat(frames, ignore_index=True)
    deduped = out.drop_duplicates("_key").reset_index(drop=True)
    info = {"files": len(frames), "rows_read": rows_read,
            "duplicates": rows_read - len(deduped), "problems": problems}
    return deduped, info


# ------------------------------------------------------------ period selection
def available_months(df: pd.DataFrame) -> list:
    """Months that have transactions, most recent first (pandas Periods)."""
    return sorted(df["Date"].dropna().dt.to_period("M").unique(), reverse=True)


def available_years(df: pd.DataFrame) -> list:
    """Years that have transactions, most recent first."""
    return sorted((int(y) for y in df["Date"].dropna().dt.year.unique()), reverse=True)


def filter_period(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Transactions dated from start to end, both inclusive. Undated rows are excluded."""
    return df[(df["Date"] >= start) & (df["Date"] <= end)]


# ------------------------------------------------------------------ calculations
def totals(view: pd.DataFrame):
    """(money_in, money_out, net), leaving out Type "Transfer".
    money_out is a positive number; net = in - out."""
    view = view[view["Type"] != "Transfer"]
    money_in = float(view.loc[view["Amount"] > 0, "Amount"].sum())
    money_out = float(-view.loc[view["Amount"] < 0, "Amount"].sum())
    return money_in, money_out, money_in - money_out


def breakdown(view: pd.DataFrame):
    """(money_in_by_type, money_out_by_type), leaving out Type "Transfer".
    Each is a DataFrame with Type, Amount (positive) and Share (fraction of that total),
    largest first. Same rules as totals(): in = positive amounts, out = negative amounts."""
    view = view[view["Type"] != "Transfer"]

    def by_type(amounts: pd.Series) -> pd.DataFrame:
        g = amounts.groupby(view.loc[amounts.index, "Type"]).sum().sort_values(ascending=False)
        out = g.rename("Amount").reset_index()
        total = out["Amount"].sum()
        out["Share"] = out["Amount"] / total if total else 0.0
        return out

    money_in = by_type(view.loc[view["Amount"] > 0, "Amount"])
    money_out = by_type(-view.loc[view["Amount"] < 0, "Amount"])
    return money_in, money_out


def load_default_excluded() -> list:
    """Types switched off by default on the Monthly tab: a JSON array of Type names in
    config/default_excluded.json (matched ignoring case)."""
    try:
        with open(DEFAULT_EXCLUDED_FILE) as f:
            data = json.load(f)
        return [str(t).strip() for t in data if str(t).strip()]
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        st.warning("config/default_excluded.json is missing or invalid; no types are excluded by default.")
        return []


def monthly_by_type(df: pd.DataFrame):
    """Net money per Type per month over ALL the data, leaving out Type "Transfer".
    Money out counts as positive and money in as negative (the opposite of the CSV's sign),
    so the amounts of all Types in a month add up to minus that month's net. Returns (months, long): months is every month (pandas
    Period) from the earliest to the latest transaction with no gaps, and long has Month,
    Type, Amount rows for every month x Type, with 0 where nothing happened."""
    months = pd.period_range(df["Date"].min().to_period("M"), df["Date"].max().to_period("M"), freq="M")
    d = df[df["Type"] != "Transfer"]
    if d.empty:
        return months, pd.DataFrame(columns=["Month", "Type", "Amount"])
    g = (-d["Amount"]).groupby([d["Date"].dt.to_period("M"), d["Type"]]).sum().unstack(fill_value=0.0)
    g = g.reindex(months, fill_value=0.0)
    g.index.name = "Month"
    long = g.stack().rename("Amount").reset_index()
    return months, long


def fmt_money(x: float) -> str:
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


# ------------------------------------------------------------------------- UI
MONEY = st.column_config.NumberColumn(format="$%.2f")


def render_overview(view: pd.DataFrame):
    """Default view: money in / money out / net, then the table of entries."""
    money_in, money_out, net = totals(view)
    m1, m2, m3 = st.columns(3)
    m1.metric("Money in", fmt_money(money_in))
    m2.metric("Money out", fmt_money(money_out))
    m3.metric("Net", fmt_money(net))
    st.caption(f"{len(view):,} transactions. Money in/out/net leave out Transfers.")
    st.dataframe(
        view[["Date", "Description", "Type", "Amount", "Account Type", "Section"]],
        hide_index=True,
        column_config={"Date": st.column_config.DateColumn(format="YYYY-MM-DD"), "Amount": MONEY},
    )


def _pie(data: pd.DataFrame, types: list):
    """Pie chart of Amount by Type. 'types' fixes the color order so a Type has the same
    color in both pies."""
    return (
        alt.Chart(data)
        .mark_arc(outerRadius=130)
        .encode(
            theta=alt.Theta("Amount:Q", stack=True),
            color=alt.Color("Type:N", scale=alt.Scale(domain=types, scheme="category20"),
                            legend=alt.Legend(title="Type")),
            order=alt.Order("Amount:Q", sort="descending"),
            tooltip=[alt.Tooltip("Type:N"),
                     alt.Tooltip("Amount:Q", format="$,.2f"),
                     alt.Tooltip("Share:Q", format=".1%")],
        )
        .properties(height=320)
    )


def render_by_type(view: pd.DataFrame):
    """Pie charts of money in and money out by Type (Transfers left out)."""
    money_in, money_out = breakdown(view)
    types = sorted(set(money_in["Type"]) | set(money_out["Type"]))
    c1, c2 = st.columns(2)
    with c1:
        st.subheader(f"Money in: {fmt_money(money_in['Amount'].sum())}")
        if money_in.empty:
            st.info("No money in for this period.")
        else:
            st.altair_chart(_pie(money_in, types))
    with c2:
        st.subheader(f"Money out: {fmt_money(money_out['Amount'].sum())}")
        if money_out.empty:
            st.info("No money out for this period.")
        else:
            st.altair_chart(_pie(money_out, types))
    st.caption("Transfers are left out. Hover a slice for the amount and share.")


# category20 colors, so a Type keeps its color when other Types are switched off
PALETTE = ["#1f77b4", "#aec7e8", "#ff7f0e", "#ffbb78", "#2ca02c", "#98df8a", "#d62728", "#ff9896",
           "#9467bd", "#c5b0d5", "#8c564b", "#c49c94", "#e377c2", "#f7b6d2", "#7f7f7f", "#c7c7c7",
           "#bcbd22", "#dbdb8d", "#17becf", "#9edae5"]


def render_monthly(df: pd.DataFrame):
    """Stacked bar and multi-line charts of net money per Type per month (money out positive,
    money in negative). Uses all saved data (not the period chosen above); a range slider
    scrolls back to the earliest month."""
    st.caption("Uses all saved data, not the period selected above. Money out is positive and "
               "money in is negative. Transfers are left out.")
    months, long = monthly_by_type(df)
    if long.empty:
        st.info("No transactions in the saved data (other than Transfers).")
        return

    # Stable color per Type for any on/off combination
    all_types = sorted(t for t in df["Type"].unique() if t != "Transfer")
    color_of = {t: PALETTE[i % len(PALETTE)] for i, t in enumerate(all_types)}

    ranked = long.groupby("Type")["Amount"].apply(lambda s: s.abs().sum()).sort_values(ascending=False).index.tolist()
    excluded = {t.lower() for t in load_default_excluded()}
    off = [t for t in ranked if t.lower() in excluded]
    # The key includes the exclusion list, so editing default_excluded.json resets the selection
    chosen = st.multiselect("Types shown (turn types on or off)", ranked,
                            default=[t for t in ranked if t not in off],
                            key="monthly_types_" + "|".join(sorted(excluded)))
    if off:
        st.caption("Off by default (add them in the box above): " + ", ".join(off))

    keys = [str(p) for p in months]
    show_month = lambda s: pd.Period(s, "M").strftime("%b %Y")
    if len(keys) > 1:
        first = keys[max(0, len(keys) - 12)]  # default window: the latest 12 months
        lo, hi = st.select_slider("Months shown (drag the left handle back to scroll to earlier months)",
                                  options=keys, value=(first, keys[-1]), format_func=show_month)
    else:
        lo = hi = keys[0]
    window = [p for p in months if lo <= str(p) <= hi]
    labels = [p.strftime("%b %Y") for p in window]

    if not chosen:
        st.info("Select at least one type to show.")
        return
    data = long[long["Month"].isin(window) & long["Type"].isin(chosen)].copy()
    data["Label"] = data["Month"].map(lambda p: p.strftime("%b %Y"))
    data = data[["Label", "Type", "Amount"]]  # plain columns only (Period dtype can't be charted)

    color = alt.Color("Type:N", legend=alt.Legend(title="Type"),
                      scale=alt.Scale(domain=chosen, range=[color_of[t] for t in chosen]))
    x = alt.X("Label:O", sort=labels, title=None, axis=alt.Axis(labelAngle=-45))
    y = alt.Y("Amount:Q", title="Amount ($)")
    tip = [alt.Tooltip("Label:O", title="Month"), alt.Tooltip("Type:N"),
           alt.Tooltip("Amount:Q", format="$,.2f")]
    st.subheader("Stacked by Type")
    st.altair_chart(alt.Chart(data).mark_bar().encode(x=x, y=y, color=color, tooltip=tip).properties(height=340))
    st.subheader("Trend by Type")
    st.altair_chart(alt.Chart(data).mark_line(point=True).encode(x=x, y=y, color=color, tooltip=tip)
                    .properties(height=340))


def _reset_period():
    """Bump a counter that is part of every period widget's key, so on the next run
    the widgets are recreated at their defaults (Date range, full span of the data)."""
    st.session_state["analysis_rev"] = st.session_state.get("analysis_rev", 0) + 1


def render():
    st.header("Analysis")
    sig = _signature()
    if not sig:
        st.info("No CSVs found in storage/parsed-data yet. Use **Save CSV** on the first tab.")
        return
    raw, info = load_all(sig)
    for p in info["problems"]:
        st.warning(p)
    if raw.empty:
        st.info("No usable transactions found in the saved CSVs.")
        return
    df = raw.dropna(subset=["Date"])
    undated = len(raw) - len(df)
    if undated:
        st.caption(f"{undated} transaction(s) have no date and are left out.")
    if df.empty:
        st.warning("None of the saved transactions have a usable date.")
        return

    # ---- choose the time period (selector sits to the right of the radio buttons)
    rev = st.session_state.get("analysis_rev", 0)
    try:
        c1, c2, c3 = st.columns([2, 3, 1], vertical_alignment="bottom")
    except TypeError:  # older Streamlit without vertical_alignment
        c1, c2, c3 = st.columns([2, 3, 1])
    mode = c1.radio("View by", ["Date range", "Month", "Year"], horizontal=True, key=f"analysis_mode_{rev}")
    c3.button("Reset", on_click=_reset_period, help="Show all data")
    with c2:
        if mode == "Date range":
            lo, hi = df["Date"].min().date(), df["Date"].max().date()
            rng = st.date_input("Date range", value=(lo, hi), min_value=lo, max_value=hi,
                                key=f"analysis_range_{rev}_{lo}_{hi}")
            if len(rng) != 2:
                st.info("Pick an end date to continue.")
                return
            start, end = pd.Timestamp(rng[0]), pd.Timestamp(rng[1])
            label = f"{start:%b %d, %Y} to {end:%b %d, %Y}"
        elif mode == "Month":
            month = st.selectbox("Month", available_months(df), format_func=lambda p: p.strftime("%B %Y"),
                                 key=f"analysis_month_{rev}")
            start, end = month.start_time, month.end_time
            label = month.strftime("%B %Y")
        else:
            year = st.selectbox("Year", available_years(df), key=f"analysis_year_{rev}")
            start, end = pd.Timestamp(year, 1, 1), pd.Timestamp(year, 12, 31)
            label = str(year)

    view = filter_period(df, start, end).sort_values("Date")
    st.subheader(label)
    tab_overview, tab_by_type, tab_monthly = st.tabs(["Overview", "By Type", "Monthly"])
    with tab_overview:
        render_overview(view)
    with tab_by_type:
        render_by_type(view)
    with tab_monthly:
        render_monthly(df)