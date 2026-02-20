import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


DEFAULT_SCOPE = "Files.Read offline_access"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def text_or_default(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    trimmed = value.strip()
    return trimmed if trimmed else fallback


def required_env(name: str, default: str = "") -> str:
    value = os.getenv(name, default).strip()
    if not value:
        raise ValueError(f"Missing environment variable: {name}")
    return value


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text or "photo"


def to_title(file_name: str, fallback: str) -> str:
    stem = Path(file_name).stem.replace("-", " ").replace("_", " ").strip()
    return stem if stem else fallback


def encode_sharing_url(url: str) -> str:
    import base64

    base64_encoded = base64.b64encode(url.encode("utf-8")).decode("utf-8")
    return (
        "u!"
        + base64_encoded
        .replace("=", "")
        .replace("+", "-")
        .replace("/", "_")
    )


def parse_trip_map(raw_json: str) -> dict[str, str]:
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON for trip map: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Trip map must be a JSON object: {\"trip\":\"share_url\"}")

    normalized: dict[str, str] = {}
    for trip_key, share_url in parsed.items():
        if not isinstance(trip_key, str) or not isinstance(share_url, str):
            raise ValueError("Trip map keys and values must be strings.")

        trip = slugify(trip_key)
        link = share_url.strip()
        if not link:
            raise ValueError(f"Trip '{trip_key}' has empty share URL.")
        normalized[trip] = link

    if not normalized:
        raise ValueError("Trip map is empty.")

    return normalized


def http_post_form_json(url: str, form: dict[str, str]) -> dict[str, Any]:
    body = urlencode(form).encode("utf-8")
    request = Request(
        url=url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {raw}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc}") from exc


def http_get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = Request(url=url, method="GET", headers=headers or {})

    try:
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {raw}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc}") from exc


def try_http_get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any] | None:
    try:
        return http_get_json(url, headers=headers)
    except Exception:
        return None


def unique_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def extract_thumbnail_urls(item: dict[str, Any]) -> list[str]:
    raw = item.get("thumbnails")
    if not isinstance(raw, list):
        return []

    urls: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        for key in ("large", "medium", "small", "source", "c200x200_Crop"):
            candidate = entry.get(key)
            if not isinstance(candidate, dict):
                continue
            url = text_or_default(candidate.get("url"), "")
            if url:
                urls.append(url)
    return unique_values(urls)


def download_with_candidate_urls(
    urls: list[str],
    access_token: str,
    suffix: str,
) -> tuple[Path | None, Exception | None]:
    last_error: Exception | None = None
    for candidate_url in unique_values(urls):
        try:
            headers = (
                {"Authorization": f"Bearer {access_token}"}
                if candidate_url.startswith(f"{GRAPH_BASE}/")
                else None
            )
            temp_path = fetch_binary_to_tempfile(candidate_url, headers=headers, suffix=suffix)
            return temp_path, None
        except Exception as exc:
            last_error = exc
    return None, last_error


def fetch_binary_to_tempfile(url: str, headers: dict[str, str] | None, suffix: str) -> Path:
    request = Request(url=url, method="GET", headers=headers or {})
    tmp_path: Path | None = None

    try:
        with urlopen(request) as response:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                while True:
                    chunk = response.read(1024 * 64)
                    if not chunk:
                        break
                    tmp.write(chunk)
                tmp_path = Path(tmp.name)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} while downloading content: {raw}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error while downloading content: {exc}") from exc

    if not tmp_path or not tmp_path.exists():
        raise RuntimeError("Download succeeded but temp file was not created.")

    return tmp_path


def is_image_item(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("image"):
        return True
    mime_type = text_or_default(item.get("file", {}).get("mimeType"), "").lower()
    return mime_type.startswith("image/")


def should_skip_existing_upload(error: Exception) -> bool:
    message = str(error).lower()
    return "already exists" in message or "duplicate" in message


def is_cloudinary_auth_error(error: Exception) -> bool:
    message = str(error).lower()
    return (
        "invalid signature" in message
        or "api key" in message and "invalid" in message
        or "authorization required" in message
        or "must supply api_key" in message
    )


def get_access_token() -> str:
    client_id = required_env("MS_CLIENT_ID")
    refresh_token = required_env("MS_REFRESH_TOKEN")
    tenant = os.getenv("MS_TENANT", "consumers").strip() or "consumers"
    scope = os.getenv("MS_SCOPE", DEFAULT_SCOPE).strip() or DEFAULT_SCOPE
    client_secret = os.getenv("MS_CLIENT_SECRET", "").strip()

    form = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": scope,
    }
    if client_secret:
        form["client_secret"] = client_secret

    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    payload = http_post_form_json(token_url, form)
    access_token = text_or_default(payload.get("access_token"), "")
    if not access_token:
        raise RuntimeError("Token response missing access_token.")
    return access_token


def fetch_share_children(share_url: str, access_token: str, max_items: int) -> list[dict[str, Any]]:
    share_id = encode_sharing_url(share_url)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    next_url = (
        f"{GRAPH_BASE}/shares/{share_id}/driveItem/children"
        f"?$top={min(max_items, 200)}"
        "&$select=id,name,file,image,webUrl,parentReference,remoteItem,thumbnails,@microsoft.graph.downloadUrl"
    )
    page_count = 0
    items: list[dict[str, Any]] = []

    while next_url:
        payload = http_get_json(next_url, headers=headers)
        page_items = payload.get("value", [])
        if isinstance(page_items, list):
            items.extend([row for row in page_items if isinstance(row, dict)])

        if len(items) >= max_items:
            break

        next_link = payload.get("@odata.nextLink")
        next_url = next_link if isinstance(next_link, str) and next_link.strip() else ""
        page_count += 1
        if page_count > 20:
            break

    return items[:max_items]


def load_cloudinary_sdk():
    try:
        import cloudinary
        import cloudinary.uploader
        import cloudinary.utils
        return cloudinary
    except ImportError as exc:
        raise RuntimeError(
            "Cloudinary SDK is not installed. Run: pip install -r scripts/requirements-cloudinary.txt"
        ) from exc


def write_manifest(trip: str, photos: list[dict[str, str]]) -> Path:
    project_root = Path(__file__).resolve().parent.parent
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = data_dir / f"{trip}.json"
    with manifest_path.open("w", encoding="utf-8") as file_obj:
        json.dump(photos, file_obj, indent=2)
    return manifest_path


def cloudinary_upload_from_graph_items(
    cloudinary: Any,
    items: list[dict[str, Any]],
    share_url: str,
    folder: str,
    access_token: str,
    overwrite: bool,
) -> list[dict[str, str]]:
    share_id = encode_sharing_url(share_url)
    manifest: list[dict[str, str]] = []

    image_items = [item for item in items if is_image_item(item)]
    if not image_items:
        return manifest

    for index, item in enumerate(image_items, start=1):
        remote_item = item.get("remoteItem") if isinstance(item.get("remoteItem"), dict) else {}
        remote_item_id = text_or_default(remote_item.get("id"), "")
        primary_item_id = text_or_default(item.get("id"), "")
        item_ids = unique_values([remote_item_id, primary_item_id])
        item_id_for_slug = remote_item_id or primary_item_id
        if not item_ids:
            continue

        drive_id = text_or_default(remote_item.get("parentReference", {}).get("driveId"), "")
        if not drive_id:
            drive_id = text_or_default(item.get("parentReference", {}).get("driveId"), "")

        file_name = text_or_default(item.get("name"), f"photo-{index}")
        extension = Path(file_name).suffix.lower()
        if not extension:
            extension = ".jpg"

        encoded_item_ids = [quote(value, safe="") for value in item_ids]
        encoded_name = quote(file_name, safe="")
        encoded_drive_id = quote(drive_id, safe="")
        graph_headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        temp_path: Path | None = None
        last_error: Exception | None = None
        try:
            download_url_candidates = unique_values(
                [
                    text_or_default(item.get("@microsoft.graph.downloadUrl"), ""),
                    text_or_default(remote_item.get("@microsoft.graph.downloadUrl"), ""),
                    *extract_thumbnail_urls(item),
                    *extract_thumbnail_urls(remote_item),
                ]
            )
            temp_path, last_error = download_with_candidate_urls(
                download_url_candidates,
                access_token=access_token,
                suffix=extension,
            )

            metadata_candidates: list[str] = []
            for encoded_item_id in encoded_item_ids:
                if drive_id:
                    metadata_candidates.append(
                        f"{GRAPH_BASE}/drives/{encoded_drive_id}/items/{encoded_item_id}"
                        "?$select=id,name,remoteItem,thumbnails,@microsoft.graph.downloadUrl"
                    )
                metadata_candidates.extend(
                    [
                        f"{GRAPH_BASE}/shares/{share_id}/driveItem/children/{encoded_item_id}"
                        "?$select=id,name,remoteItem,thumbnails,@microsoft.graph.downloadUrl",
                        f"{GRAPH_BASE}/shares/{share_id}/driveItem/items/{encoded_item_id}"
                        "?$select=id,name,remoteItem,thumbnails,@microsoft.graph.downloadUrl",
                        f"{GRAPH_BASE}/shares/{share_id}/items/{encoded_item_id}/driveItem"
                        "?$select=id,name,remoteItem,thumbnails,@microsoft.graph.downloadUrl",
                    ]
                )
            for metadata_url in unique_values(metadata_candidates):
                payload = try_http_get_json(metadata_url, headers=graph_headers)
                if not payload:
                    continue
                download_url_candidates.extend(
                    [
                        text_or_default(payload.get("@microsoft.graph.downloadUrl"), ""),
                        *extract_thumbnail_urls(payload),
                    ]
                )
                nested_remote_item = payload.get("remoteItem") if isinstance(payload.get("remoteItem"), dict) else {}
                download_url_candidates.extend(
                    [
                        text_or_default(nested_remote_item.get("@microsoft.graph.downloadUrl"), ""),
                        *extract_thumbnail_urls(nested_remote_item),
                    ]
                )

            if temp_path is None:
                temp_path, last_error = download_with_candidate_urls(
                    download_url_candidates,
                    access_token=access_token,
                    suffix=extension,
                )

            if temp_path is None:
                content_candidates = [f"{GRAPH_BASE}/shares/{share_id}/driveItem:/{encoded_name}:/content"]
                for encoded_item_id in encoded_item_ids:
                    if drive_id:
                        content_candidates.append(
                            f"{GRAPH_BASE}/drives/{encoded_drive_id}/items/{encoded_item_id}/content"
                        )
                    content_candidates.extend(
                        [
                            f"{GRAPH_BASE}/shares/{share_id}/driveItem/children/{encoded_item_id}/content",
                            f"{GRAPH_BASE}/shares/{share_id}/driveItem/items/{encoded_item_id}/content",
                            f"{GRAPH_BASE}/shares/{share_id}/items/{encoded_item_id}/driveItem/content",
                        ]
                    )
                temp_path, last_error = download_with_candidate_urls(
                    content_candidates,
                    access_token=access_token,
                    suffix=extension,
                )

            if temp_path is None:
                thumbnail_content_candidates: list[str] = []
                for encoded_item_id in encoded_item_ids:
                    for size in ("large", "medium", "small"):
                        if drive_id:
                            thumbnail_content_candidates.append(
                                f"{GRAPH_BASE}/drives/{encoded_drive_id}/items/{encoded_item_id}/thumbnails/0/{size}/content"
                            )
                        thumbnail_content_candidates.extend(
                            [
                                f"{GRAPH_BASE}/shares/{share_id}/driveItem/children/{encoded_item_id}/thumbnails/0/{size}/content",
                                f"{GRAPH_BASE}/shares/{share_id}/driveItem/items/{encoded_item_id}/thumbnails/0/{size}/content",
                                f"{GRAPH_BASE}/shares/{share_id}/items/{encoded_item_id}/driveItem/thumbnails/0/{size}/content",
                            ]
                        )
                temp_path, last_error = download_with_candidate_urls(
                    thumbnail_content_candidates,
                    access_token=access_token,
                    suffix=extension,
                )

            if temp_path is None:
                if last_error is not None:
                    raise last_error
                raise RuntimeError("Could not resolve a valid download URL for item.")

            base_id = slugify(Path(file_name).stem)
            item_slug = slugify(item_id_for_slug)[:12]
            public_leaf = f"{base_id}-{item_slug}" if item_slug else base_id
            public_id = f"{folder.strip('/')}/{public_leaf}" if folder.strip("/") else public_leaf

            try:
                result = cloudinary.uploader.upload(
                    str(temp_path),
                    public_id=public_id,
                    overwrite=overwrite,
                    resource_type="image",
                    use_filename=False,
                    unique_filename=False,
                )
                print(f"Uploaded {index}/{len(image_items)}: {file_name}")
            except Exception as exc:
                if overwrite or not should_skip_existing_upload(exc):
                    raise
                result = {"public_id": public_id}
                print(f"Reused existing {index}/{len(image_items)}: {file_name}")

            uploaded_public_id = result.get("public_id", public_id)
            optimized_url, _ = cloudinary.utils.cloudinary_url(
                uploaded_public_id,
                secure=True,
                resource_type="image",
                type="upload",
                fetch_format="auto",
                quality="auto",
                width=1800,
                crop="limit",
            )

            manifest.append(
                {
                    "src": optimized_url,
                    "title": to_title(file_name, f"Photo {index}"),
                    "name": file_name,
                }
            )
        except Exception as exc:
            if is_cloudinary_auth_error(exc):
                raise RuntimeError(f"Cloudinary authentication/signature failure: {exc}") from exc
            print(f"Skipped {index}/{len(image_items)} ({file_name}): {exc}")
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automate Microsoft Graph shared album -> Cloudinary -> data/<trip>.json"
    )
    parser.add_argument(
        "--map-json",
        default="",
        help='Trip map JSON, e.g. {"australia":"https://1drv.ms/..."}; falls back to TRIP_SHARE_URLS_JSON env var',
    )
    parser.add_argument(
        "--folder-prefix",
        default="wander-to-wonder",
        help="Cloudinary folder prefix. Final folder is <prefix>/<trip>.",
    )
    parser.add_argument(
        "--max",
        dest="max_files",
        type=int,
        default=50,
        help="Max images to list per trip. Default: 50.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing Cloudinary assets with same public IDs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw_map = args.map_json.strip() or os.getenv("TRIP_SHARE_URLS_JSON", "").strip()
    if not raw_map:
        print("ERROR: Provide --map-json or set TRIP_SHARE_URLS_JSON.")
        return 1

    trip_map = parse_trip_map(raw_map)

    cloudinary = load_cloudinary_sdk()
    cloudinary.config(
        cloud_name=required_env("CLOUDINARY_CLOUD_NAME"),
        api_key=required_env("CLOUDINARY_API_KEY"),
        api_secret=required_env("CLOUDINARY_API_SECRET"),
        secure=True,
    )

    try:
        access_token = get_access_token()
    except Exception as exc:
        print(f"ERROR obtaining Graph access token: {exc}")
        return 1

    failures: list[str] = []
    for trip in sorted(trip_map.keys()):
        share_url = trip_map[trip]
        folder = f"{args.folder_prefix.strip('/')}/{trip}"
        print(f"\n=== Syncing trip: {trip} ===")

        try:
            items = fetch_share_children(share_url, access_token, max_items=args.max_files)
            photos = cloudinary_upload_from_graph_items(
                cloudinary=cloudinary,
                items=items,
                share_url=share_url,
                folder=folder,
                access_token=access_token,
                overwrite=args.overwrite,
            )

            if not photos:
                raise RuntimeError("No image files found in shared folder.")

            manifest_path = write_manifest(trip, photos)
            print(f"Manifest updated: {manifest_path}")
        except Exception as exc:
            failures.append(f"{trip}: {exc}")
            print(f"ERROR syncing {trip}: {exc}")

    if failures:
        print("\nSome trips failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("\nAll trips synced successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
