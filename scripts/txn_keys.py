"""Stable per-transaction identity, shared by the converter and analysis tabs."""
import pandas as pd


def row_keys(df: pd.DataFrame) -> pd.Series:
    """Stable identity for each transaction, independent of row position:
    (source file, date, description, amount, occurrence). 'occurrence' is 0 for the
    first row with those four values, 1 for the next, and so on, so genuinely
    identical transactions (e.g. two $5.00 coffees on the same day) stay separate."""
    dates = pd.to_datetime(df["Date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    seen, keys = {}, []
    for src, d, desc, amt in zip(df["Source File"], dates, df["Description"], df["Amount"]):
        base = (str(src), d, str(desc), f"{float(amt):.2f}")
        n = seen.get(base, 0)
        seen[base] = n + 1
        keys.append(base + (n,))
    return pd.Series(keys, index=df.index, dtype=object)