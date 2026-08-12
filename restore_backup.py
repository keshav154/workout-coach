"""
Restore a CoachxKeshav JSON backup (the file /cron/backup sends to Telegram,
produced by trust.export_all) into MongoDB.

DESTRUCTIVE: every collection present in the backup file is dropped and
replaced with the backup's contents. Collections not in the file are untouched.

Usage:
    MONGODB_URI='mongodb+srv://...' python restore_backup.py coachx_backup_20260812.json
    (add --yes to skip the confirmation prompt)
"""

import json
import os
import sys

import certifi
from bson import ObjectId
from pymongo import MongoClient


def _revive_id(v):
    """Backups stringify _id; turn 24-hex strings back into ObjectIds and
    leave fixed string ids ('user', 'log', dates, ...) alone."""
    if isinstance(v, str) and len(v) == 24:
        try:
            return ObjectId(v)
        except Exception:
            return v
    return v


def main() -> None:
    paths = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not paths:
        print(__doc__.strip())
        sys.exit(1)
    if not os.environ.get("MONGODB_URI"):
        print("Set MONGODB_URI to the target database first.")
        sys.exit(1)

    with open(paths[0]) as f:
        dump = json.load(f)
    collections = {k: v for k, v in dump.items()
                   if not k.startswith("_") and isinstance(v, list)}
    if not collections:
        print("No collections found in that file — is it a CoachxKeshav backup?")
        sys.exit(1)

    print(f"Backup exported at: {dump.get('_exported_at', 'unknown')}")
    for name, docs in collections.items():
        print(f"  {name}: {len(docs)} doc(s)")

    if "--yes" not in sys.argv:
        answer = input("\nThis REPLACES those collections in the target DB. "
                       "Type 'restore' to continue: ")
        if answer.strip().lower() != "restore":
            print("Aborted — nothing was changed.")
            sys.exit(1)

    client = MongoClient(os.environ["MONGODB_URI"], tlsCAFile=certifi.where(),
                         serverSelectionTimeoutMS=10000)
    db = client["workout_coach"]
    for name, docs in collections.items():
        db[name].drop()
        for d in docs:
            if "_id" in d:
                d["_id"] = _revive_id(d["_id"])
        if docs:
            db[name].insert_many(docs)
        print(f"Restored {name}: {len(docs)} doc(s)")
    print("Done.")


if __name__ == "__main__":
    main()
