"""Backup modes and constants shared by pure logic and the Django layer."""
SCHEMA_ONLY = "SCHEMA_ONLY"
DATA_ONLY = "DATA_ONLY"
FULL = "FULL"
LAST_24_HOURS = "LAST_24_HOURS"
DB_MODES = (SCHEMA_ONLY, DATA_ONLY, FULL, LAST_24_HOURS)
NATIVE_MODES = (SCHEMA_ONLY, DATA_ONLY, FULL)
FILES_MODE = "FILES"

KIND_DATABASE = "database"
KIND_FILE = "file"

LAST_24_HOURS_NOTICE = (
    "LAST_24_HOURS is a PARTIAL data extract, not a complete backup: it contains only the rows created in the preceding 24 hours, "
    "tables without a usable creation timestamp are skipped (listed in the manifest), updates and deletions are not captured, and it "
    "cannot be restored over a database. Use FULL for a restorable backup."
)
MODE_LABELS = {
    SCHEMA_ONLY: "Schema only (structure, no rows)",
    DATA_ONLY: "Data only (rows, no structure)",
    FULL: "Full (structure and data)",
    LAST_24_HOURS: "Last 24 hours (PARTIAL data extract)",
    FILES_MODE: "Files",
}
