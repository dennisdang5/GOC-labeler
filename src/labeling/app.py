"""Local GOC labeling app (Streamlit). Runs on this computer only; never upload note text.

Run from the repo root:  streamlit run src/labeling/app.py

Reads an ID-only assignment file from data/assignments/, pulls the note text from YOUR OWN
local MIMIC-III copy (data/parquet/noteevents.parquet, built from data/raw/noteevents/ on first
run), and appends labels to data/labels/<assignment>_<initials>.jsonl. Label files hold IDs,
the yes/no/unsure choice and timing only -- no note text -- so they can be shared.

Set GOC_DATA_DIR to point at a different data folder (used by the tests with synthetic notes).
"""

import html
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # `streamlit run` doesn't put the repo root on the path
    sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from src.ingest.load import EXPECTED_ROWS, convert  # noqa: E402
from src.labeling.schema import GOCLabel  # noqa: E402

# Paths: GOC_DATA_DIR (tests) > labeling-kit repo (marker file; assignments/ and labels/ are
# committed at the repo root) > main project repo (everything under data/).
if "GOC_DATA_DIR" in os.environ:
    DATA = Path(os.environ["GOC_DATA_DIR"])
    ASSIGN_DIR, LABEL_DIR = DATA / "assignments", DATA / "labels"
elif (ROOT / ".labeling-kit").exists():
    DATA = ROOT / "data"
    ASSIGN_DIR, LABEL_DIR = ROOT / "assignments", ROOT / "labels"
else:
    DATA = ROOT / "data"
    ASSIGN_DIR, LABEL_DIR = DATA / "assignments", DATA / "labels"
NOTES = DATA / "parquet" / "noteevents.parquet"

YNU = ["yes", "no", "unclear"]  # "unclear" is shown as "unsure"
DEFINITION = ("Yes if the note records an interaction between clinician(s) and the patient "
              "and/or family or surrogate in which goals, values, prognosis, or preferences for "
              "future treatment in serious illness were discussed. No if it's only an order "
              "(e.g. DNR), a consult, a planned meeting, or clinicians talking among themselves.")

st.set_page_config(page_title="GOC labeling", layout="wide")


# ---------- data ----------

def setup_notes() -> None:
    """One-time NOTEEVENTS csv -> parquet conversion, from the labeler's own download."""
    st.title("GOC labeling — one-time setup")
    st.write("Put your own PhysioNet download of `NOTEEVENTS.csv.gz` (or `.csv`) in "
             f"`{(DATA / 'raw' / 'noteevents').as_posix()}/`, then convert it once.")
    if st.button("Convert NOTEEVENTS (takes a few minutes)"):
        NOTES.parent.mkdir(parents=True, exist_ok=True)
        try:
            with st.spinner("Converting..."):
                n = convert(duckdb.connect(), "noteevents", DATA / "raw", DATA / "parquet")
        except Exception as e:
            st.error(f"Conversion failed ({type(e).__name__}). Check the file location.")
            return
        if n != EXPECTED_ROWS["noteevents"]:
            NOTES.unlink(missing_ok=True)
            st.error(f"Row count {n:,} != expected {EXPECTED_ROWS['noteevents']:,}. "
                     "Re-download NOTEEVENTS from PhysioNet.")
            return
        st.success(f"Done: {n:,} notes.")
        st.rerun()


@st.cache_data(show_spinner="Loading your assigned notes...")
def load_notes(assignment_path: str) -> pd.DataFrame:
    read = pd.read_csv if assignment_path.endswith(".csv") else pd.read_parquet
    a = read(assignment_path).sort_values("label_order", ignore_index=True)
    con = duckdb.connect()
    con.register("a", a)
    notes = con.execute(f"""
        SELECT a.ROW_ID, a.CATEGORY, a.n_chars, a.label_order, n.TEXT
        FROM a LEFT JOIN '{NOTES.as_posix()}' n USING (ROW_ID)
        ORDER BY a.label_order
    """).df()
    return notes


def labels_path(assignment: str, annotator: str) -> Path:
    return LABEL_DIR / f"{assignment}_{annotator}.jsonl"


def read_own_labels(path: Path) -> dict[int, dict]:
    """Latest record per ROW_ID from this annotator's own file only (blind to others)."""
    latest = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                latest[rec["row_id"]] = rec
    return latest


# ---------- UI ----------

def show(value: str) -> str:
    return "unsure" if value == "unclear" else value


def pick(options: list, value) -> int | None:
    """Radio index for a saved value; None (nothing selected) for a new note."""
    return options.index(value) if value in options else None


def note_html(text: str) -> str:
    return ("<div style='height:70vh;overflow-y:auto;white-space:pre-wrap;font-family:monospace;"
            f"font-size:0.85rem;border:1px solid #8884;padding:0.75rem'>{html.escape(text)}</div>")


def main() -> None:
    if not NOTES.exists():
        setup_notes()
        return

    files = {p.stem: p for p in sorted(ASSIGN_DIR.glob("*")) if p.suffix in {".parquet", ".csv"}}
    with st.sidebar:
        st.header("GOC labeling")
        annotator = st.text_input("Your initials", key="annotator").strip().upper()
        if not files:
            st.warning(f"Put your assignment file in `{ASSIGN_DIR.as_posix()}/`.")
            return
        assignment = st.selectbox("Assignment", list(files), key="assignment")
    if not annotator.isalpha() or len(annotator) > 5:
        st.info("Enter your initials (letters only) in the sidebar to start.")
        return

    notes = load_notes(files[assignment].as_posix())
    missing = notes["TEXT"].isna()
    wrong_len = ~missing & (notes["TEXT"].str.len() != notes["n_chars"])
    if missing.any() or wrong_len.any():
        st.error(f"Your local NOTEEVENTS doesn't match the assignment: {int(missing.sum())} notes "
                 f"missing, {int(wrong_len.sum())} with a different length. Re-download "
                 "NOTEEVENTS (MIMIC-III v1.4) from PhysioNet and convert again before labeling.")
        return

    out = labels_path(assignment, annotator)
    done = read_own_labels(out)
    n = len(notes)
    if "idx" not in st.session_state or st.session_state.get("idx_for") != (assignment, annotator):
        unlabeled = [i for i, r in enumerate(notes["ROW_ID"]) if r not in done]
        st.session_state.idx = unlabeled[0] if unlabeled else 0
        st.session_state.idx_for = (assignment, annotator)

    with st.sidebar:
        st.progress(len(done) / n, text=f"{len(done)} of {n} labeled")
        if len(done) == n:
            st.success("All notes labeled. You can still go back and change any answer.")

        def saved(i: int) -> str:
            rec = done.get(notes.at[i, "ROW_ID"])
            return show(rec["goc_present"]) if rec else "—"

        st.session_state.idx = st.selectbox(
            "Go to note (your saved answer)", list(range(n)), index=st.session_state.idx,
            format_func=lambda i: f"{i + 1}. {saved(i)}")
        c1, c2 = st.columns(2)
        if c1.button("◀ Prev", disabled=st.session_state.idx == 0):
            st.session_state.idx -= 1
            st.rerun()
        if c2.button("Next ▶", disabled=st.session_state.idx == n - 1):
            st.session_state.idx += 1
            st.rerun()

    i = st.session_state.idx
    row = notes.iloc[i]
    row_id, text = int(row["ROW_ID"]), row["TEXT"]
    prev = done.get(row_id, {})
    timer_key = f"opened_{row_id}"
    st.session_state.setdefault(timer_key, time.time())

    def k(name: str) -> str:  # widget keys are per note so answers don't leak between notes
        return f"{name}_{row_id}"

    left, right = st.columns([3, 2])
    with right:
        st.subheader(f"Note {i + 1} of {n}")
        st.caption(f"ROW_ID {row_id} · {row['CATEGORY'].strip()} · {len(text):,} chars")
        goc = st.radio("GOC present?", YNU, index=pick(YNU, prev.get("goc_present")),
                       format_func=show, horizontal=True, key=k("goc"))
        st.caption(DEFINITION)
        if prev and goc == prev["goc_present"]:
            st.info(f"Saved answer: **{show(goc)}**. To change it, pick another answer and save.")
        elif prev and goc is not None:
            st.warning(f"Changed from **{show(prev['goc_present'])}** to **{show(goc)}**, "
                       "not saved yet. Click Save label to keep the change.")

        if st.button("Save label", type="primary", key=k("save")):
            elapsed = (time.time() - st.session_state[timer_key]) / 60
            record = {
                "row_id": row_id, "assignment": assignment, "goc_present": goc,
                "annotator": annotator,
                "minutes_spent": round(prev.get("minutes_spent", 0) + elapsed, 1),
                "labeled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            if goc is None:
                st.error("Choose yes, no or unsure first.")
            else:
                try:
                    label = GOCLabel.model_validate(record)
                except ValidationError as e:
                    for err in e.errors():
                        st.error(err["msg"].removeprefix("Value error, "))
                else:
                    LABEL_DIR.mkdir(parents=True, exist_ok=True)
                    with out.open("a", encoding="utf-8") as f:
                        f.write(label.model_dump_json() + "\n")
                    st.session_state.pop(timer_key, None)
                    if i < n - 1:
                        st.session_state.idx = i + 1
                    st.rerun()

    with left:
        st.markdown(note_html(text), unsafe_allow_html=True)


main()
