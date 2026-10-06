"""One-time migration: current SQLite database -> Render PostgreSQL.

Run this from the project folder on your laptop after:
1) creating the Render PostgreSQL database;
2) setting DATABASE_URL to the Render PostgreSQL *External Database URL*.

Windows PowerShell:
    $env:DATABASE_URL="PASTE_EXTERNAL_DATABASE_URL_HERE"
    python migrate_sqlite_to_postgres.py

The script preserves IDs so booking/pass-code/activity relationships remain intact.
It does not modify or delete the SQLite source database.
"""
import os
import sqlite3
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

SQLITE_PATH = Path(
    os.environ.get(
        "LC_SQLITE_PATH",
        Path(__file__).resolve().parent / "instance" / "last_chapter.db",
    )
)
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is not set.")
if not SQLITE_PATH.exists():
    raise SystemExit(f"SQLite database not found: {SQLITE_PATH}")

TABLES = [
    "organizer",
    "booking",
    "pass_code_inventory",
    "activity_log",
    "setting",
]

COLUMNS = {
    "organizer": ["id", "username", "password_hash", "created_at"],
    "booking": [
    "id",
    "pass_code",
    "pass_type",
    "person_1_name",
    "person_1_dob",
    "person_2_name",
    "person_2_dob",
    "contact",
    "payment_method",
    "payment_status",
    "amount",
    "created_at",
    "entry_status",
    "entry_time",
    "verified_by"],
    "pass_code_inventory": [
    "id",
    "code",
    "status",
    "booking_id",
    "created_at",
    "assigned_at",
    "used_at",
],
    "activity_log": [
        "id", 
        "booking_id", 
        "action", 
        "detail", 
        "created_at",
    ],
    "setting": ["key", "value"],
}


def qident(name):
    return '"' + name.replace('"', '""') + '"'


def main():
    print(f"SQLite source: {SQLITE_PATH}")

    src = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row

    dst = psycopg.connect(DATABASE_URL, row_factory=dict_row)

    try:
        # The destination schema is created by store.init() on Render.
        # We only insert data here.
        with dst.transaction():
            for table in TABLES:
                cols = COLUMNS[table]
                rows = src.execute(
                    f"SELECT {', '.join(qident(c) for c in cols)} "
                    f"FROM {qident(table)}"
                ).fetchall()

                if not rows:
                    print(f"{table}: 0 rows")
                    continue

                col_sql = ", ".join(qident(c) for c in cols)
                placeholders = ", ".join(["%s"] * len(cols))

                # Preserve IDs. This makes booking_id references remain valid.
                if table != "setting":
                    sql = (
                        f"INSERT INTO {qident(table)} ({col_sql}) "
                        f"VALUES ({placeholders}) "
                        f"ON CONFLICT DO NOTHING"
                    )
                else:
                    sql = (
                        f"INSERT INTO {qident(table)} ({col_sql}) "
                        f"VALUES ({placeholders}) "
                        f"ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value"
                    )

                inserted = 0
                for row in rows:
                    cur = dst.execute(sql, tuple(row[c] for c in cols))
                    inserted += cur.rowcount

                print(f"{table}: source={len(rows)}, inserted={inserted}")

            # Make future generated IDs continue after the imported IDs.
            for table in ("organizer", "booking", "pass_code_inventory", "activity_log"):
                dst.execute(
                    f"""SELECT setval(
                        pg_get_serial_sequence('{table}', 'id'),
                        COALESCE((SELECT MAX(id) FROM {qident(table)}), 1),
                        true
                    )"""
                )

        print("\nMigration completed successfully.")
        print("Your SQLite file was NOT changed or deleted.")
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    main()
