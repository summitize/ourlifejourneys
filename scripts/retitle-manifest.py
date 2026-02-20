import argparse
import json
import re
from datetime import datetime
from pathlib import Path


TIMESTAMP_NAME_RE = re.compile(
    r"^(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})_(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})"
)


def text_or_default(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    trimmed = value.strip()
    return trimmed if trimmed else fallback


def title_from_name(name: str, trip_display_name: str, index: int) -> str:
    match = TIMESTAMP_NAME_RE.match(name)
    if match:
        date_bits = match.groupdict()
        dt = datetime(
            int(date_bits["year"]),
            int(date_bits["month"]),
            int(date_bits["day"]),
            int(date_bits["hour"]),
            int(date_bits["minute"]),
            int(date_bits["second"]),
        )
        return f"{trip_display_name} - {dt.strftime('%d %b %Y, %I:%M %p')}"

    return f"{trip_display_name} Moment {index:02d}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-time retitle of manifest entries to avoid file-name style titles."
    )
    parser.add_argument("--trip", required=True, help="Trip key, e.g. australia")
    parser.add_argument(
        "--title-prefix",
        default="",
        help="Display name prefix. Defaults to title-cased --trip.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    trip = text_or_default(args.trip, "").lower()
    if not trip:
        print("ERROR: --trip is required.")
        return 1

    title_prefix = text_or_default(args.title_prefix, trip.replace("-", " ").title())
    project_root = Path(__file__).resolve().parent.parent
    manifest_path = project_root / "data" / f"{trip}.json"

    if not manifest_path.exists():
        print(f"ERROR: Manifest not found: {manifest_path}")
        return 1

    with manifest_path.open("r", encoding="utf-8") as file_obj:
        payload = json.load(file_obj)

    if not isinstance(payload, list):
        print(f"ERROR: Expected a JSON array in {manifest_path}")
        return 1

    updated_count = 0
    for index, row in enumerate(payload, start=1):
        if not isinstance(row, dict):
            continue
        name = text_or_default(row.get("name"), "")
        if not name:
            continue

        next_title = title_from_name(name, title_prefix, index)
        if text_or_default(row.get("title"), "") != next_title:
            row["title"] = next_title
            updated_count += 1

    with manifest_path.open("w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, indent=2)

    print(f"Updated {updated_count} titles in {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
