# Budgeting: Bank of America Statements → Categorized CSV + Spending Analysis

A local [Streamlit](https://streamlit.io) app that turns your Bank of America statement PDFs into a categorized CSV, then charts where your money goes.

**Everything runs on your own computer.** Your statements and transaction data are never uploaded anywhere, and the folders that hold them are excluded from Git.

## Features

- **Convert statements:** upload one or more text-based BofA statement PDFs (checking, savings, or credit card). Transactions are parsed into a table with Date, Description, Type, Amount, Section, Account Type, and Source File.
- **Smart categorization:** the first time you see an unfamiliar merchant, the app asks you to categorize it. Save it as a keyword rule (e.g. `chipotle` → `Eating Out`) and every matching transaction, now and in future statements, is categorized automatically. Or apply a choice to just one transaction.
- **Account detection:** each statement is matched to one of your accounts using the last 4 digits of the account number printed on it.
- **Save to disk:** save your statements and the combined CSV into the project's `storage/` folder. CSVs are named by the date range they cover, like `2024-01-05_to_2024-03-14.csv`.
- **Analysis tab:** reads every saved CSV and shows an overview, a spending breakdown by Type, and a monthly trend, with a date range picker.

## Limitations

- **Bank of America only.** The parser is built around BofA's statement layout. Other banks will need changes to the parsing code.
- **Text-based PDFs only.** Scanned or image-only PDFs can't be read. If you can select and copy text in the PDF, it works.
- BofA occasionally changes its statement format. If a statement parses badly, tick **Show raw extracted text** in the app to see what the parser sees.

## Requirements

- Python 3.9 or newer
- Streamlit 1.30 or newer (installed by the steps below)

## Setup

### 1. Get the code

```bash
git clone https://github.com/<your-username>/<repo-name>.git
cd <repo-name>
```

### 2. Create a virtual environment and install dependencies

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell)**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Windows (Command Prompt)**
```bat
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
```

### 3. Create your config files

The repo ships example config files. Copy each one without `.example` in the name. Your copies are private and ignored by Git.

**macOS / Linux**
```bash
for f in config/*.example.json; do cp "$f" "${f/.example/}"; done
```

**Windows (PowerShell)**
```powershell
Get-ChildItem config\*.example.json | ForEach-Object { Copy-Item $_.FullName ($_.FullName -replace '\.example','') }
```

You should now have these four files in `config/`, ready to edit (see [Configuration](#configuration)):

```
config/accounts.json
config/categories.json
config/category_rules.json
config/default-excluded.json
```

### 4. Run the app

From the project root:

```bash
streamlit run scripts/app.py
```

Your browser opens at `http://localhost:8501`. If it doesn't, paste that address into your browser. Press `Ctrl+C` in the terminal to stop the app.

Next time, just activate the virtual environment again and rerun the command above.

> If `streamlit` isn't recognized, use `python -m streamlit run scripts/app.py`.

## Configuration

All config lives in `config/` as plain JSON files, and the app re-reads them on every interaction, so changes show up without a restart.

### `accounts.json`: your accounts

A list of labels. **Each label must end with the last 4 digits of the account number** as printed on your statements. That's how the app figures out which account a statement belongs to.

```json
[
  "Checking - 1234",
  "Savings - 5678",
  "Credit Card - 9012"
]
```

If the app can't match a statement, it shows a warning listing the last-4 numbers it found. Add the right label here, or pick the account manually in the table.

### `categories.json`: your categories

A list of category names. The order here is the order in the dropdown.

```json
["Housing", "Groceries", "Eating Out", "Travel", "Utilities", "Income", "Transfer", "Other"]
```

**Keep a category named exactly `Transfer`.** The Analysis tab leaves transfers out of money in/out so that moving money between your own accounts, or paying a credit card from checking, isn't counted twice. You'll probably want an `Income` category too.

### `category_rules.json`: keyword rules

Created and updated by the app when you save a rule, so you normally never edit it by hand. It maps a lowercase keyword to a category:

```json
{
  "amtrak": "Travel",
  "chipotle": "Eating Out"
}
```

Matching is case-insensitive and checks whether the keyword appears anywhere in the transaction description. If several rules match, the longest keyword wins (so `uber eats` beats `uber`). You can edit or delete rules from the **Manage saved rules** section in the app.

### `default-excluded.json`: hidden by default in the Monthly chart

A list of Types that start switched off on the Analysis tab's Monthly view (for example, big one-off categories that would squash the chart). You can still turn them on in the app. Leave it as `[]` if you don't need this.

```json
["Housing"]
```

## How to use it

1. Download your statement PDFs from Bank of America.
2. Open the **Convert statements** tab and upload them.
3. Categorize any unrecognized transactions when the app asks. Choose a category, then pick **All entries containing a keyword (saves a rule)** or **This entry only**. Edit the suggested keyword if you like.
4. Check the table. You can fix a Type or Account Type directly in it. Edits made in the table affect the CSV only and don't create rules.
5. Click **Save Statements**, **Save CSV**, or **Save Both**.
6. Open the **Analysis** tab to explore everything you've saved.

### About "Save Statements"

Statements are copied into `storage/bank-statements/`. After saving, the app also looks in your `Downloads` folder (and `OneDrive/Downloads`) for a file with the **same name and identical contents** and **deletes that copy**, so the statement is effectively moved. It never deletes a file whose contents differ. If you don't want this, don't use **Save Statements** / **Save Both**, or remove the cleanup call in `scripts/storage.py`.

### Sign convention

Amounts below zero are money out; amounts above zero are money in.

## Project layout

```
.
├── scripts/
│   ├── app.py           # PDF parsing, categorization, and the Convert tab
│   ├── analysis.py      # Analysis tab (reads saved CSVs)
│   ├── storage.py       # saving statements and CSVs
│   └── txn_keys.py      # stable per-transaction identity
├── config/              # *.example.json templates (your copies are git-ignored)
├── storage/             # created on first save; git-ignored
│   ├── bank-statements/ # saved PDFs
│   └── parsed-data/     # saved CSVs
└── requirements.txt
```

## Privacy

`storage/` (your statements and CSVs) and your personal `config/*.json` files are listed in `.gitignore`, so they won't be committed if you fork this repo or make changes. Before pushing, it's worth running `git status` to confirm no statements or personal data are staged.

## Troubleshooting

| Problem | What to try |
|---|---|
| "No transactions found" for a statement | Tick **Show raw extracted text**. If it's empty, the PDF is probably scanned. Otherwise the layout may differ from what the parser expects. |
| Warning about `config/…json` missing or invalid | Complete step 3 of setup, and check the file is valid JSON (no trailing commas). |
| "Couldn't match an account" | Add the account to `config/accounts.json` with a label ending in its last 4 digits, or pick it in the table. |
| Analysis tab says no CSVs found | Use **Save CSV** on the first tab. The Analysis tab reads from `storage/parsed-data/`. |
| `streamlit` not recognized | Activate your virtual environment, or use `python -m streamlit run scripts/app.py`. |

## Contributing

Issues and pull requests are welcome. Support for other banks would be a great addition. Please don't include real statements or personal data in issues or pull requests; anonymize any text samples first.

## License

Add a license file (for example [MIT](https://choosealicense.com/licenses/mit/)) before publishing so others know how they can use this project.