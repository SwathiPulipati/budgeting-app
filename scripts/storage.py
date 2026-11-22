"""File-saving helpers for the statement app.

Statements go to storage/bank-statements, parsed CSVs go to storage/parsed-data
(both inside the project directory, one level above this scripts/ folder).
"""
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).parent.parent
STATEMENTS_DIR = PROJECT_DIR / "storage" / "bank-statements"
PARSED_DIR = PROJECT_DIR / "storage" / "parsed-data"


def downloads_dirs() -> list:
    """Folders where a browser download of the statement might be sitting."""
    home = Path.home()
    return [d for d in (home / "Downloads", home / "OneDrive" / "Downloads") if d.is_dir()]


def _unique_path(directory: Path, name: str) -> Path:
    """Return directory/name, or directory/name_2, name_3... if already taken."""
    path = directory / name
    stem, suffix, n = path.stem, path.suffix, 2
    while path.exists():
        path = directory / f"{stem}_{n}{suffix}"
        n += 1
    return path


def _remove_download_copies(name: str, data: bytes, saved_to: Path) -> list:
    """Delete same-named files in Downloads, but only if byte-for-byte identical
    to the copy we just verified in the project folder."""
    messages = []
    if not (saved_to.exists() and saved_to.read_bytes() == data):
        return messages  # never delete unless the saved copy is verified
    for folder in downloads_dirs():
        candidate = folder / name
        if candidate.resolve() == saved_to.resolve() or not candidate.is_file():
            continue
        try:
            if candidate.read_bytes() == data:
                candidate.unlink()
                messages.append((True, f"Removed {name} from {folder}"))
            else:
                messages.append((False, f"Left {candidate} alone (contents differ from the upload)"))
        except OSError as e:
            messages.append((False, f"Couldn't remove {candidate}: {e}"))
    return messages


def save_statements(files: list) -> list:
    """files: list of (filename, bytes). Returns a list of (ok, message)."""
    STATEMENTS_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for raw_name, data in files:
        name = Path(raw_name).name
        dest = STATEMENTS_DIR / name
        if dest.exists() and dest.read_bytes() == data:
            results.append((True, f"{name}: already in storage/bank-statements"))
        else:
            dest = _unique_path(STATEMENTS_DIR, name)
            dest.write_bytes(data)
            results.append((True, f"Saved {dest.name} to storage/bank-statements"))
        results.extend(_remove_download_copies(name, data, dest))
    return results


def csv_filename(df: pd.DataFrame) -> str:
    dates = pd.to_datetime(df["Date"], errors="coerce").dropna()
    if dates.empty:
        return "undated_transactions.csv"
    return f"{dates.min():%Y-%m-%d}_to_{dates.max():%Y-%m-%d}.csv"


def save_csv(df: pd.DataFrame) -> list:
    """Save df to storage/parsed-data, named by its date range. Returns [(ok, message)]."""
    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    dest = _unique_path(PARSED_DIR, csv_filename(df))
    df.to_csv(dest, index=False)
    return [(True, f"Saved {dest.name} to storage/parsed-data")]