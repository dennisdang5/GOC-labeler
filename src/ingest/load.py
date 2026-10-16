"""Convert MIMIC-III CSVs to Parquet (one file per table).

Run from anywhere:  python -m src.ingest.load

Prints only table names and row counts. On failure prints the table name and the
exception class -- never str(e), since DuckDB CSV errors can include the raw line.
"""

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
PARQUET = ROOT / "data" / "parquet"

TABLES = ["noteevents", "admissions", "patients", "diagnoses_icd"]
EXPECTED_ROWS = {"noteevents": 2_083_180}

# NOTEEVENTS.TEXT has embedded newlines and long notes; a line here is a full record.
MAX_LINE_SIZE = 64 * 1024 * 1024


def convert(con: duckdb.DuckDBPyConnection, table: str,
            raw: Path = RAW, parquet: Path = PARQUET) -> int:
    src = (raw / table).as_posix() + "/*.csv*"
    dst = (parquet / f"{table}.parquet").as_posix()
    con.execute(
        f"""
        COPY (
            SELECT * FROM read_csv(
                '{src}',
                header = true,
                quote = '"',
                escape = '"',
                max_line_size = {MAX_LINE_SIZE},
                sample_size = -1
            )
        ) TO '{dst}' (FORMAT parquet, COMPRESSION zstd)
        """
    )
    return con.execute(f"SELECT count(*) FROM '{dst}'").fetchone()[0]


def main() -> int:
    PARQUET.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    failed = False
    for table in TABLES:
        try:
            n = convert(con, table)
        except Exception as e:
            print(f"{table}: FAILED ({type(e).__name__})")
            failed = True
            continue
        print(f"{table}: {n:,} rows")
        expected = EXPECTED_ROWS.get(table)
        if expected is not None and n != expected:
            print(f"{table}: row count mismatch, expected {expected:,}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
