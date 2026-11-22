import io
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import pdfplumber
import streamlit as st

from analysis import render as render_analysis
from storage import save_csv, save_statements
from txn_keys import row_keys

st.set_page_config(page_title="BofA Statement to CSV", layout="wide")

AMT = r"-?\$?\(?[\d,]+\.\d{2}\)?-?"

# Checking-style line: 01/05/24 DESCRIPTION  -123.45
RE_CHECKING = re.compile(rf"^(\d{{2}}/\d{{2}}(?:/\d{{2,4}})?)\s+(.+?)\s+({AMT})$")
# Credit-card line: 01/05 01/06 MERCHANT ... 25.00
RE_CARD = re.compile(rf"^(\d{{2}}/\d{{2}})\s+(\d{{2}}/\d{{2}})\s+(.+?)(?:\s+\d{{4}}\s+\d{{4}})?\s+({AMT})$")
# Check register entry: 01/05/24 1234 -100.00 (may repeat on one line)
RE_CHECK = re.compile(rf"(\d{{2}}/\d{{2}}/\d{{2,4}})\s+(\d+)\s*\*?\s+({AMT})")

# Account number label followed by digits, e.g. "Account number: 1234 5678 7654",
# "Account # 1234 1234 1234 6318" or masked "XXXX XXXX 6318"
RE_ACCOUNT = re.compile(r"(?:account|acct)\s*(?:number|no\.?|#)\s*[:#]?\s*([\dXx*•][\dXx*•\- \t]{2,40})", re.I)

SECTIONS = {
    "deposits and other additions": "Deposit",
    "withdrawals and other subtractions": "Withdrawal",
    "checks": "Check",
    "service fees": "Fee",
    "purchases and adjustments": "Purchase",
    "payments and other credits": "Payment/Credit",
    "interest charged": "Interest",
    "fees charged": "Fee",
}
# Card statements print charges as positive numbers and payments/credits as negative
# (balance owed). These sections are flipped so "-" always means money leaving the account.
CARD_SECTIONS = {"Purchase", "Interest", "Payment/Credit"}
PARSE_VERSION = 4  # bump when parse output changes, so Streamlit's parse cache is refreshed
STOP_HEADERS = ("daily ledger balances", "important information")

SKIP_LINE = re.compile(
    r"^(page \d+ of \d+|continued on the next page|your checking account|"
    r"your bank of america|account number|total |subtotal|date description|"
    r"transaction date|posting date|reference number|"
    r"bank of america, n\.a\.|p\.o\. box)",
    re.I,
)


def to_float(s: str) -> float:
    neg = s.startswith("-") or s.endswith("-") or ("(" in s and ")" in s)
    val = float(re.sub(r"[^\d.]", "", s))
    return -val if neg else val


def find_period(text: str):
    """Return (start_date, end_date) of the statement, or (None, None)."""
    m = re.search(r"for\s+([A-Z][a-z]+ \d{1,2}, \d{4})\s+to\s+([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    if m:
        f = "%B %d, %Y"
        return datetime.strptime(m.group(1), f), datetime.strptime(m.group(2), f)
    m = re.search(r"([A-Z][a-z]+ \d{1,2})\s*-\s*([A-Z][a-z]+ \d{1,2}), (\d{4})", text)
    if m:
        end = datetime.strptime(f"{m.group(2)} {m.group(3)}", "%B %d %Y")
        start = datetime.strptime(f"{m.group(1)} {m.group(3)}", "%B %d %Y")
        if start > end:
            start = start.replace(year=start.year - 1)
        return start, end
    m = re.search(r"Statement Closing Date\s+(\d{2}/\d{2}/\d{2,4})", text)
    if m:
        d = m.group(1)
        end = datetime.strptime(d, "%m/%d/%y" if len(d.split("/")[2]) == 2 else "%m/%d/%Y")
        return end.replace(month=1, day=1), end
    return None, None


def parse_date(raw: str, start, end):
    parts = raw.split("/")
    if len(parts) == 3:
        fmt = "%m/%d/%y" if len(parts[2]) == 2 else "%m/%d/%Y"
        return datetime.strptime(raw, fmt)
    if not end:
        return None
    month = int(parts[0])
    year = end.year
    # Statement spanning Dec -> Jan: December transactions belong to the earlier year
    if start and start.year != end.year and month >= start.month:
        year = start.year
    try:
        return datetime(year, month, int(parts[1]))
    except ValueError:
        return None


def parse_statement(text: str, filename: str):
    start, end = find_period(text)
    rows, section, last = [], None, None

    def add(d, desc, amt):
        row = [parse_date(d, start, end), desc, to_float(amt), section, filename]
        if row[2] == 0:
            # Ignore zero-amount entries; return a throwaway row so any wrapped
            # description lines that follow are discarded, not merged elsewhere.
            return [None, "", 0, "", ""]
        rows.append(row)
        return row

    for line in (l.strip() for l in text.split("\n")):
        if not line:
            continue
        low = line.lower()

        # Section headers (also matches "... - continued")
        header = next((v for k, v in SECTIONS.items() if low.startswith(k)), None)
        if header:
            section, last = header, None
            continue
        if low.startswith(STOP_HEADERS):
            section, last = None, None
            continue
        if low.startswith("total "):
            last = None
            continue
        if section is None:
            continue

        if section == "Check":
            found = RE_CHECK.findall(line)
            if found:
                for d, num, amt in found:
                    add(d, f"Check #{num}", amt)
                last = None
                continue

        m = RE_CARD.match(line)
        if m:
            d, _, desc, amt = m.groups()
            last = add(d, desc, amt)
            continue

        m = RE_CHECKING.match(line)
        if m:
            d, desc, amt = m.groups()
            last = add(d, desc, amt)
            continue

        # Wrapped description line: append to previous transaction
        if last is not None and not SKIP_LINE.match(line):
            last[1] += " " + line

    df = pd.DataFrame(rows, columns=["Date", "Description", "Amount", "Section", "Source File"])

    if not df.empty:
        # Withdrawals/fees/checks should be negative if printed unsigned
        neg_sections = {"Withdrawal", "Fee", "Check"}
        mask = df["Section"].isin(neg_sections) & (df["Amount"] > 0)
        df.loc[mask, "Amount"] *= -1
        # Card sections: flip the printed sign (purchases become negative, payments/credits positive)
        df.loc[df["Section"].isin(CARD_SECTIONS), "Amount"] *= -1
    return df


def extract_text(file) -> str:
    with pdfplumber.open(file) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


CONFIG_DIR = Path(__file__).parent.parent / "config"
RULES_FILE = CONFIG_DIR / "category_rules.json"
UNCAT = "Uncategorized"
NEW_OPT = "➕ New category…"
CATEGORIES_FILE = CONFIG_DIR / "categories.json"

ACCOUNTS_FILE = CONFIG_DIR / "accounts.json"
UNKNOWN_ACCOUNT = "Unknown"

PREFIX = re.compile(r"^(checkcard|purchase|pos|debit card|recurring)\s+", re.I)


def load_categories() -> list:
    """Category list from categories.json (a JSON array of strings; order is kept)."""
    try:
        with open(CATEGORIES_FILE) as f:
            data = json.load(f)
        seen, out = set(), []
        for c in data:
            c = str(c).strip()
            if c and c not in seen:
                seen.add(c)
                out.append(c)
        return out
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        st.warning("config/categories.json is missing or invalid; only categories from saved rules will be offered.")
        return []


def load_accounts() -> list:
    """Account options from accounts.json: a JSON array of labels that each end in the
    account's last 4 digits, e.g. "Checking - 7654"."""
    try:
        with open(ACCOUNTS_FILE) as f:
            data = json.load(f)
        out = []
        for a in data:
            a = str(a).strip()
            if a and a not in out:
                out.append(a)
        for a in out:
            if not re.search(r"\d{4}\s*$", a):
                st.warning(f"Account option '{a}' in config/accounts.json doesn't end in 4 digits, so it can't be auto-detected.")
        return out
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        st.warning("config/accounts.json is missing or invalid; Account Type will be 'Unknown'.")
        return []


def detect_account(text: str, accounts) -> tuple:
    """Find the statement's account number(s) in the text and match the last 4 digits
    to an account option. Returns (label or UNKNOWN_ACCOUNT, [last-4s found in the text])."""
    found = []
    for m in RE_ACCOUNT.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) >= 4 and digits[-4:] not in found:
            found.append(digits[-4:])
    by_last4 = {}
    for a in accounts:
        m = re.search(r"(\d{4})\s*$", a)
        if m:
            by_last4.setdefault(m.group(1), a)
    for last4 in found:
        if last4 in by_last4:
            return by_last4[last4], found
    return UNKNOWN_ACCOUNT, found


def load_rules() -> dict:
    try:
        with open(RULES_FILE) as f:
            data = json.load(f)
        return {str(k).strip().lower(): v for k, v in data.items() if str(k).strip()}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_rules(rules: dict):
    CONFIG_DIR.mkdir(exist_ok=True)
    with open(RULES_FILE, "w") as f:
        json.dump(dict(sorted(rules.items())), f, indent=2)


def match_rule(desc: str, rules: dict):
    """Longest matching keyword wins (so 'uber eats' beats 'uber')."""
    d = desc.lower()
    hits = [k for k in rules if k in d]
    return rules[max(hits, key=len)] if hits else None


def suggest_keyword(desc: str) -> str:
    s = PREFIX.sub("", desc.strip())
    s = re.sub(r"^\d{2}/\d{2}\s+", "", s).replace("*", " ")
    words = []
    for tok in s.split():
        if any(c.isdigit() for c in tok) or ":" in tok:
            break
        words.append(re.sub(r"[^A-Za-z&']", "", tok))
    words = [w for w in words if w][:2]
    if words:
        return " ".join(words).lower()
    return s.split()[0].lower() if s.split() else ""


@st.cache_data(show_spinner=False)
def parse_bytes(data: bytes, name: str, version: int = PARSE_VERSION, accounts: tuple = ()):
    text = extract_text(io.BytesIO(data))
    df = parse_statement(text, name)
    label, found = detect_account(text, accounts)
    df.insert(3, "Account Type", label)  # after Amount (Type is inserted at 2 later)
    return text, df, (label, found)


ss = st.session_state
ss.setdefault("overrides", {})   # one-off categorizations (session only), keyed by row_keys()
ss.setdefault("skipped", set())  # skipped entries (session only), keyed by row_keys()
ss.setdefault("custom_cats", [])
ss.setdefault("uploader_rev", 0)  # bumping this gives the file uploader a new key, which empties it


def clear_uploads():
    ss.uploader_rev += 1


tab_convert, tab_analysis = st.tabs(["Convert statements", "Analysis"])

with tab_convert:
    st.title("Bank of America Statement → CSV")
    st.caption("Files are processed in memory and are not saved to disk. Category rules are saved in config/category_rules.json.")

    files = st.file_uploader("Upload statement PDFs", type="pdf", accept_multiple_files=True,
                             key=f"uploader_{ss.uploader_rev}")
    if files:
        st.button("Clear all uploaded files", on_click=clear_uploads)
    show_raw = st.checkbox("Show raw extracted text (for debugging)")
    rules = load_rules()
    accounts = load_accounts()

    # Drop one-off choices / skips belonging to statements that are no longer uploaded
    # (key[0] is the source file name). Choices for files still uploaded are kept.
    current_names = {f.name for f in files} if files else set()
    ss.overrides = {k: v for k, v in ss.overrides.items() if k[0] in current_names}
    ss.skipped = {k for k in ss.skipped if k[0] in current_names}

    if files:
        frames = []
        for f in files:
            text, df, (acct, found) = parse_bytes(f.getvalue(), f.name, PARSE_VERSION, tuple(accounts))
            if show_raw:
                with st.expander(f"Raw text: {f.name}"):
                    st.text(text)
            if df.empty:
                st.warning(f"No transactions found in {f.name}. Check the raw text and adjust the regexes.")
            if acct == UNKNOWN_ACCOUNT:
                seen = ", ".join(found) if found else "none"
                st.warning(f"{f.name}: couldn't match an account (account number endings found: {seen}). "
                           "Add it to config/accounts.json, or pick the Account Type in the table.")
            frames.append(df)

        result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not result.empty:
            result = result.sort_values("Date").reset_index(drop=True)
            keys = row_keys(result)  # stable per-transaction identity (not row position)
            result.insert(
                2, "Type",
                [ss.overrides.get(keys[i]) or match_rule(d, rules) or UNCAT for i, d in result["Description"].items()],
            )
            base_cats = load_categories()
            extras = sorted((set(rules.values()) | set(ss.custom_cats)) - set(base_cats))
            cats = base_cats + extras

            # ---- Categorize unknown entries one at a time ----
            pending = [i for i in result.index[result["Type"] == UNCAT] if keys[i] not in ss.skipped]
            if pending:
                i = pending[0]
                row = result.loc[i]
                date_str = row["Date"].strftime("%m/%d/%Y") if pd.notna(row["Date"]) else "?"
                with st.container(border=True):
                    st.subheader(f"Categorize ({len(pending)} uncategorized remaining)")
                    st.markdown(f"**{row['Description']}**  \n{date_str} · ${row['Amount']:,.2f} · {row['Section']}")
                    with st.form(f"cat_form_{i}"):
                        choice = st.selectbox("Category", cats + [NEW_OPT], index=None, placeholder="Choose a category…")
                        new_cat = st.text_input("New category name (only used if '➕ New category…' is selected)")
                        scope = st.radio(
                            "Apply to",
                            ["All entries containing a keyword (saves a rule)", "This entry only"],
                        )
                        keyword = st.text_input(
                            "Keyword", value=suggest_keyword(row["Description"]),
                            help="Must appear in the description. Matching is case-insensitive.",
                        )
                        c1, c2 = st.columns([1, 6])
                        save = c1.form_submit_button("Save", type="primary")
                        skip = c2.form_submit_button("Skip")

                    if skip:
                        ss.skipped.add(keys[i])
                        st.rerun()
                    if save:
                        cat = new_cat.strip() if choice == NEW_OPT else choice
                        kw = keyword.strip().lower()
                        use_rule = scope.startswith("All")
                        if not cat:
                            st.error("Please choose or enter a category.")
                        elif use_rule and (not kw or kw not in row["Description"].lower()):
                            st.error("The keyword must be non-empty and appear in this entry's description.")
                        else:
                            if use_rule:
                                rules[kw] = cat
                                save_rules(rules)
                            else:
                                ss.overrides[keys[i]] = cat
                                if cat not in ss.custom_cats:
                                    ss.custom_cats.append(cat)
                            st.rerun()

            # ---- Summary + table ----
            c1, c2, c3 = st.columns(3)
            c1.metric("Transactions", len(result))
            c2.metric("Total in", f"${result.loc[result.Amount > 0, 'Amount'].sum():,.2f}")
            c3.metric("Total out", f"${result.loc[result.Amount < 0, 'Amount'].sum():,.2f}")

            edited = st.data_editor(
                result,
                use_container_width=True,
                num_rows="dynamic",
                column_config={
                    "Type": st.column_config.SelectboxColumn(options=cats + [UNCAT]),
                    "Account Type": st.column_config.SelectboxColumn(options=accounts + [UNKNOWN_ACCOUNT]),
                },
            )
            st.caption("Edits to Type in this table affect the CSV only; use the form above to save rules.")

            # ---- Save buttons ----
            statement_data = [(f.name, f.getvalue()) for f in files]
            b1, b2, b3, _ = st.columns([1, 1, 1, 4])
            results = []
            if b1.button("Save Statements"):
                results = save_statements(statement_data)
            if b2.button("Save CSV"):
                results = save_csv(edited)
            if b3.button("Save Both"):
                results = save_statements(statement_data) + save_csv(edited)
            for ok, msg in results:
                (st.success if ok else st.warning)(msg)

    # ---- Rule manager ----
    with st.expander(f"Manage saved rules ({len(rules)})"):
        rules_df = pd.DataFrame(sorted(rules.items()), columns=["Keyword", "Category"])
        edited_rules = st.data_editor(rules_df, num_rows="dynamic", key="rules_editor", use_container_width=True)
        if st.button("Save rule changes"):
            new_rules = {
                str(k).strip().lower(): str(v).strip()
                for k, v in zip(edited_rules["Keyword"], edited_rules["Category"])
                if pd.notna(k) and pd.notna(v) and str(k).strip() and str(v).strip()
            }
            save_rules(new_rules)
            st.rerun()

with tab_analysis:
    render_analysis()