import argparse
import json
import shutil
from pathlib import Path


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def to_title(file_name: str, fallback: str) -> str:
    stem = Path(file_name).stem.replace("-", " ").replace("_", " ").strip()
    return stem if stem else fallback


def collect_images(source_dir: Path) -> list[Path]:
    files = [p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]
    files.sort(key=lambda p: p.name.lower())
    return files


def sync_gallery(source_dir: Path, trip: str, max_files: int | None = None) -> tuple[int, Path, Path]:
    project_root = Path(__file__).resolve().parent.parent
    target_image_dir = project_root / "images" / trip
    target_json_path = project_root / "data" / f"{trip}.json"

    target_image_dir.mkdir(parents=True, exist_ok=True)
    target_json_path.parent.mkdir(parents=True, exist_ok=True)

    files = collect_images(source_dir)
    if max_files is not None:
        files = files[:max_files]
    if not files:
        raise ValueError(
            "No supported image files found. Expected one of: "
            + ", ".join(sorted(SUPPORTED_EXTENSIONS))
        )

    photos = []
    for index, source_path in enumerate(files, start=1):
        ext = source_path.suffix.lower()
        new_name = f"{trip}-{index}{ext}"
        destination_path = target_image_dir / new_name
        shutil.copy2(source_path, destination_path)

        photos.append(
            {
                "src": f"images/{trip}/{new_name}",
                "title": to_title(source_path.name, f"Photo {index}"),
                "name": source_path.name,
            }
        )

    with target_json_path.open("w", encoding="utf-8") as f:
        json.dump(photos, f, indent=2)

    return len(photos), target_image_dir, target_json_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync local photos into website gallery cache.")
    parser.add_argument("--source", required=True, help="Local folder containing photos.")
    parser.add_argument("--trip", required=True, help="Trip slug, e.g. australia or dubai.")
    parser.add_argument("--max", dest="max_files", type=int, default=None, help="Optional max number of photos to copy.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_dir = Path(args.source).expanduser().resolve()
    trip = args.trip.strip().lower().replace(" ", "-")

    if not source_dir.exists() or not source_dir.is_dir():
        print(f"ERROR: Source folder not found: {source_dir}")
        return 1

    try:
        count, image_dir, manifest_path = sync_gallery(source_dir, trip, args.max_files)
    except Exception as exc:
        print(f"ERROR: Sync failed: {exc}")
        return 1

    print(f"Synced {count} photo(s).")
    print(f"Images: {image_dir}")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
