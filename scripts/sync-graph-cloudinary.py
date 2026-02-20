import argparse
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


DEFAULT_SCOPE = "Files.Read offline_access"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TIMESTAMP_FILE_RE = re.compile(
    r"^(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})_(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})"
)


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
    stem = Path(file_name).stem.strip()
    match = TIMESTAMP_FILE_RE.match(stem)
    if match:
        bits = match.groupdict()
        dt = datetime(
            int(bits["year"]),
            int(bits["month"]),
            int(bits["day"]),
            int(bits["hour"]),
            int(bits["minute"]),
            int(bits["second"]),
        )
        return dt.strftime("%d %b %Y, %I:%M %p")

    pretty = stem.replace("-", " ").replace("_", " ").strip()
    return pretty if pretty else fallback


def to_description(title: str, trip_label: str) -> str:
    normalized_title = text_or_default(title, "")
    normalized_trip = text_or_default(trip_label, "")
    if not normalized_title:
        return f"Captured during {normalized_trip or 'this trip'}."

    prefix = f"{normalized_trip} - " if normalized_trip else ""
    detail = normalized_title
    if prefix and normalized_title.lower().startswith(prefix.lower()):
        detail = normalized_title[len(prefix):].strip()

    if re.search(r"\d{1,2}\s[A-Za-z]{3}\s\d{4}", detail):
        return f"Captured on {detail}."
    return f"{detail}."


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


def parse_trip_map(raw_json: str) -> dict[str, dict[str, str]]:
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON for trip map: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Trip map must be a JSON object: {\"trip\":\"share_url\"}")

    normalized: dict[str, dict[str, str]] = {}
    for trip_key, raw_value in parsed.items():
        if not isinstance(trip_key, str):
            raise ValueError("Trip map keys must be strings.")

        trip = slugify(trip_key)
        if isinstance(raw_value, str):
            link = raw_value.strip()
            if not link:
                raise ValueError(f"Trip '{trip_key}' has empty share URL.")
            normalized[trip] = {
                "mode": "single",
                "share_url": link,
                "trip_label": trip_key.strip() or trip,
            }
            continue

        if not isinstance(raw_value, dict):
            raise ValueError(
                "Trip map values must be either a share URL string or an object config."
            )

        share_url = text_or_default(raw_value.get("share_url"), "")
        if not share_url:
            share_url = text_or_default(raw_value.get("url"), "")
        if not share_url:
            raise ValueError(f"Trip '{trip_key}' object config is missing share_url.")

        children_flag = str(raw_value.get("children_as_trips", "")).strip().lower()
        expand_children = children_flag in ("1", "true", "yes", "on")
        if not children_flag and isinstance(raw_value.get("children_as_trips"), bool):
            expand_children = bool(raw_value.get("children_as_trips"))

        if expand_children:
            normalized[trip] = {
                "mode": "children",
                "share_url": share_url,
                "trip_prefix": text_or_default(raw_value.get("trip_prefix"), ""),
            }
            continue

        normalized[trip] = {
            "mode": "single",
            "share_url": share_url,
            "trip_label": text_or_default(raw_value.get("trip_label"), trip_key.strip() or trip),
        }

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
    if mime_type.startswith("image/"):
        return True

    remote_item = item.get("remoteItem") if isinstance(item.get("remoteItem"), dict) else {}
    if remote_item.get("image"):
        return True
    remote_mime_type = text_or_default(remote_item.get("file", {}).get("mimeType"), "").lower()
    if remote_mime_type.startswith("image/"):
        return True

    candidate_name = text_or_default(item.get("name"), "") or text_or_default(remote_item.get("name"), "")
    extension = Path(candidate_name).suffix.lower()
    return extension in {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
        ".heic",
        ".heif",
        ".avif",
    }


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
        "&$select=id,name,file,folder,image,webUrl,parentReference,remoteItem,thumbnails,@microsoft.graph.downloadUrl"
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


def fetch_drive_children(
    drive_id: str,
    item_id: str,
    access_token: str,
    max_items: int,
) -> list[dict[str, Any]]:
    encoded_drive_id = quote(drive_id, safe="")
    encoded_item_id = quote(item_id, safe="")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    next_url = (
        f"{GRAPH_BASE}/drives/{encoded_drive_id}/items/{encoded_item_id}/children"
        f"?$top={min(max_items, 200)}"
        "&$select=id,name,file,folder,image,webUrl,parentReference,remoteItem,thumbnails,@microsoft.graph.downloadUrl"
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


def fetch_share_item_children(
    share_id: str,
    item_id: str,
    access_token: str,
    max_items: int,
) -> list[dict[str, Any]]:
    encoded_share_id = quote(share_id, safe="")
    encoded_item_id = quote(item_id, safe="")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    endpoint_candidates = [
        f"{GRAPH_BASE}/shares/{encoded_share_id}/driveItem/items/{encoded_item_id}/children"
        f"?$top={min(max_items, 200)}"
        "&$select=id,name,file,folder,image,webUrl,parentReference,remoteItem,thumbnails,@microsoft.graph.downloadUrl",
        f"{GRAPH_BASE}/shares/{encoded_share_id}/items/{encoded_item_id}/driveItem/children"
        f"?$top={min(max_items, 200)}"
        "&$select=id,name,file,folder,image,webUrl,parentReference,remoteItem,thumbnails,@microsoft.graph.downloadUrl",
    ]

    last_error: Exception | None = None
    for first_url in endpoint_candidates:
        next_url = first_url
        page_count = 0
        items: list[dict[str, Any]] = []
        try:
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
        except Exception as exc:
            last_error = exc

    if last_error:
        raise last_error
    return []


def is_folder_item(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    if isinstance(item.get("folder"), dict):
        return True
    remote_item = item.get("remoteItem") if isinstance(item.get("remoteItem"), dict) else {}
    return isinstance(remote_item.get("folder"), dict)


def resolve_item_ids(item: dict[str, Any]) -> tuple[str, str]:
    remote_item = item.get("remoteItem") if isinstance(item.get("remoteItem"), dict) else {}
    item_id = text_or_default(remote_item.get("id"), "") or text_or_default(item.get("id"), "")

    drive_id = text_or_default(
        remote_item.get("parentReference", {}).get("driveId"),
        "",
    )
    if not drive_id:
        drive_id = text_or_default(item.get("parentReference", {}).get("driveId"), "")

    return item_id, drive_id


def expand_trip_targets(
    trip_map: dict[str, dict[str, str]],
    access_token: str,
    max_items: int,
) -> list[dict[str, str]]:
    trip_targets: list[dict[str, str]] = []
    seen_trips: set[str] = set()

    for trip in sorted(trip_map.keys()):
        config = trip_map[trip]
        mode = text_or_default(config.get("mode"), "single")
        share_url = text_or_default(config.get("share_url"), "")
        if not share_url:
            raise ValueError(f"Trip config '{trip}' is missing share_url.")

        if mode == "children":
            child_items = fetch_share_children(share_url, access_token, max_items=200)
            folder_items = [row for row in child_items if is_folder_item(row)]
            folder_items.sort(key=lambda row: text_or_default(row.get("name"), "").lower())
            if not folder_items:
                raise RuntimeError(
                    f"Trip '{trip}' is configured with children_as_trips but no subfolders were found."
                )

            prefix = text_or_default(config.get("trip_prefix"), "")
            for folder_item in folder_items:
                folder_name = text_or_default(folder_item.get("name"), "")
                if not folder_name:
                    continue

                base_trip = slugify(folder_name)
                expanded_trip = f"{slugify(prefix)}-{base_trip}" if prefix else base_trip
                if expanded_trip in seen_trips:
                    raise ValueError(
                        f"Duplicate trip key '{expanded_trip}' derived from folder '{folder_name}'."
                    )

                folder_item_id, folder_drive_id = resolve_item_ids(folder_item)
                if not folder_item_id or not folder_drive_id:
                    raise RuntimeError(
                        f"Could not resolve drive/item ids for subfolder '{folder_name}'."
                    )

                trip_targets.append(
                    {
                        "trip": expanded_trip,
                        "trip_label": folder_name,
                        "mode": "drive_item",
                        "share_url": share_url,
                        "drive_id": folder_drive_id,
                        "item_id": folder_item_id,
                    }
                )
                seen_trips.add(expanded_trip)
            continue

        if trip in seen_trips:
            raise ValueError(f"Duplicate trip key '{trip}'.")
        trip_targets.append(
            {
                "trip": trip,
                "trip_label": text_or_default(config.get("trip_label"), trip.replace("-", " ").title()),
                "mode": "share",
                "share_url": share_url,
                "drive_id": "",
                "item_id": "",
            }
        )
        seen_trips.add(trip)

    return trip_targets


def collect_image_items(
    mode: str,
    share_url: str,
    drive_id: str,
    item_id: str,
    access_token: str,
    max_items: int,
    max_depth: int,
) -> list[dict[str, Any]]:
    # Crawl nested folders because many OneDrive shares keep photos inside subfolders.
    initial_mode = "drive_item" if mode == "drive_item" else "share_root"
    initial_share_id = encode_sharing_url(share_url) if share_url else ""
    queue: list[dict[str, Any]] = [
        {
            "mode": initial_mode,
            "share_url": share_url,
            "share_id": initial_share_id,
            "drive_id": drive_id,
            "item_id": item_id,
            "depth": 0,
        }
    ]
    visited_drive_items: set[tuple[str, str]] = set()
    visited_share_items: set[tuple[str, str]] = set()
    image_items: list[dict[str, Any]] = []
    list_limit = max(200, max_items)

    while queue and len(image_items) < max_items:
        node = queue.pop(0)
        node_mode = text_or_default(node.get("mode"), "share_root")
        node_depth = int(node.get("depth", 0))
        node_share_id = text_or_default(node.get("share_id"), "")

        if node_mode == "drive_item":
            node_drive_id = text_or_default(node.get("drive_id"), "")
            node_item_id = text_or_default(node.get("item_id"), "")
            if not node_drive_id or not node_item_id:
                continue

            visit_key = (node_drive_id, node_item_id)
            if visit_key in visited_drive_items:
                continue
            visited_drive_items.add(visit_key)

            children = fetch_drive_children(
                drive_id=node_drive_id,
                item_id=node_item_id,
                access_token=access_token,
                max_items=list_limit,
            )
        elif node_mode == "share_item":
            node_item_id = text_or_default(node.get("item_id"), "")
            if not node_share_id or not node_item_id:
                continue

            visit_key = (node_share_id, node_item_id)
            if visit_key in visited_share_items:
                continue
            visited_share_items.add(visit_key)

            children = fetch_share_item_children(
                share_id=node_share_id,
                item_id=node_item_id,
                access_token=access_token,
                max_items=list_limit,
            )
        else:
            node_share_url = text_or_default(node.get("share_url"), "")
            if not node_share_url:
                continue
            if not node_share_id:
                node_share_id = encode_sharing_url(node_share_url)
            children = fetch_share_children(
                share_url=node_share_url,
                access_token=access_token,
                max_items=list_limit,
            )

        for child in children:
            if is_image_item(child):
                image_items.append(child)
                if len(image_items) >= max_items:
                    break

            if node_depth >= max_depth:
                continue

            if not is_folder_item(child):
                continue

            child_item_id, child_drive_id = resolve_item_ids(child)
            if not child_item_id:
                continue

            if child_drive_id:
                queue.append(
                    {
                        "mode": "drive_item",
                        "share_url": "",
                        "share_id": node_share_id,
                        "drive_id": child_drive_id,
                        "item_id": child_item_id,
                        "depth": node_depth + 1,
                    }
                )
                continue

            if node_share_id:
                queue.append(
                    {
                        "mode": "share_item",
                        "share_url": "",
                        "share_id": node_share_id,
                        "drive_id": "",
                        "item_id": child_item_id,
                        "depth": node_depth + 1,
                    }
                )

    return image_items[:max_items]


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


def load_existing_manifest(trip: str) -> list[dict[str, Any]]:
    project_root = Path(__file__).resolve().parent.parent
    manifest_path = project_root / "data" / f"{trip}.json"
    if not manifest_path.exists():
        return []

    try:
        with manifest_path.open("r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except Exception:
        return []

    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("photos"), list):
        return [row for row in payload["photos"] if isinstance(row, dict)]
    return []


def build_existing_metadata_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    metadata_map: dict[str, dict[str, str]] = {}
    for row in rows:
        file_name = text_or_default(row.get("name"), "")
        title = text_or_default(row.get("title"), "")
        description = text_or_default(row.get("description"), "")
        if not file_name:
            continue
        metadata_map[file_name] = {
            "title": title,
            "description": description,
        }
    return metadata_map


def cloudinary_upload_from_graph_items(
    cloudinary: Any,
    items: list[dict[str, Any]],
    share_url: str,
    folder: str,
    access_token: str,
    overwrite: bool,
    trip_label: str,
    existing_metadata: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    share_id = encode_sharing_url(share_url) if share_url else ""
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
                if share_id:
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
                content_candidates = []
                if share_id:
                    content_candidates.append(
                        f"{GRAPH_BASE}/shares/{share_id}/driveItem:/{encoded_name}:/content"
                    )
                for encoded_item_id in encoded_item_ids:
                    if drive_id:
                        content_candidates.append(
                            f"{GRAPH_BASE}/drives/{encoded_drive_id}/items/{encoded_item_id}/content"
                        )
                    if share_id:
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
                        if share_id:
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

            preserved_title = ""
            preserved_description = ""
            if isinstance(existing_metadata, dict):
                row = existing_metadata.get(file_name)
                if isinstance(row, dict):
                    preserved_title = text_or_default(row.get("title"), "")
                    preserved_description = text_or_default(row.get("description"), "")
            resolved_title = preserved_title or to_title(file_name, f"Photo {index}")
            resolved_description = preserved_description or to_description(resolved_title, trip_label)
            manifest.append(
                {
                    "src": optimized_url,
                    "title": resolved_title,
                    "description": resolved_description,
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
        help=(
            'Trip map JSON. Examples: {"australia":"https://1drv.ms/..."} '
            'or {"india":{"share_url":"https://1drv.ms/...","children_as_trips":true}}; '
            "falls back to TRIP_SHARE_URLS_JSON env var"
        ),
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
    parser.add_argument(
        "--max-depth",
        type=int,
        default=5,
        help="Max subfolder depth to crawl while searching for images. Default: 5.",
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

    try:
        trip_targets = expand_trip_targets(trip_map, access_token, max_items=args.max_files)
    except Exception as exc:
        print(f"ERROR while expanding trip targets: {exc}")
        return 1

    failures: list[str] = []
    for target in trip_targets:
        trip = target["trip"]
        share_url = target.get("share_url", "")
        mode = target.get("mode", "share")
        trip_label = text_or_default(target.get("trip_label"), trip.replace("-", " ").title())
        folder = f"{args.folder_prefix.strip('/')}/{trip}"
        print(f"\n=== Syncing trip: {trip} ===")

        try:
            drive_id = text_or_default(target.get("drive_id"), "")
            item_id = text_or_default(target.get("item_id"), "")
            items = collect_image_items(
                mode=mode,
                share_url=share_url,
                drive_id=drive_id,
                item_id=item_id,
                access_token=access_token,
                max_items=args.max_files,
                max_depth=max(0, args.max_depth),
            )

            existing_manifest_rows = load_existing_manifest(trip)
            existing_metadata_map = build_existing_metadata_map(existing_manifest_rows)
            photos = cloudinary_upload_from_graph_items(
                cloudinary=cloudinary,
                items=items,
                share_url=share_url,
                folder=folder,
                access_token=access_token,
                overwrite=args.overwrite,
                trip_label=trip_label,
                existing_metadata=existing_metadata_map,
            )

            if not photos:
                debug_items: list[dict[str, Any]] = []
                try:
                    if mode == "drive_item":
                        drive_id = text_or_default(target.get("drive_id"), "")
                        item_id = text_or_default(target.get("item_id"), "")
                        if drive_id and item_id:
                            debug_items = fetch_drive_children(
                                drive_id=drive_id,
                                item_id=item_id,
                                access_token=access_token,
                                max_items=20,
                            )
                    else:
                        debug_items = fetch_share_children(
                            share_url=share_url,
                            access_token=access_token,
                            max_items=20,
                        )
                except Exception as debug_exc:
                    print(f"Debug listing failed for {trip}: {debug_exc}")

                if not debug_items:
                    print(f"Debug listing for {trip}: no children returned.")
                else:
                    folder_count = sum(1 for row in debug_items if is_folder_item(row))
                    image_count = sum(1 for row in debug_items if is_image_item(row))
                    file_count = 0
                    for row in debug_items:
                        remote_item = row.get("remoteItem") if isinstance(row.get("remoteItem"), dict) else {}
                        if isinstance(row.get("file"), dict) or isinstance(remote_item.get("file"), dict):
                            file_count += 1

                    print(
                        f"Debug listing for {trip}: total={len(debug_items)} "
                        f"folders={folder_count} files={file_count} image_detected={image_count}"
                    )
                    for row in debug_items[:10]:
                        remote_item = row.get("remoteItem") if isinstance(row.get("remoteItem"), dict) else {}
                        name = text_or_default(row.get("name"), "") or text_or_default(remote_item.get("name"), "")
                        mime_type = text_or_default(row.get("file", {}).get("mimeType"), "-")
                        remote_mime_type = text_or_default(remote_item.get("file", {}).get("mimeType"), "-")
                        has_folder = bool(isinstance(row.get("folder"), dict) or isinstance(remote_item.get("folder"), dict))
                        has_image = bool(row.get("image") or remote_item.get("image"))
                        print(
                            f"- {name or '[unnamed]'} | folder={has_folder} "
                            f"image={has_image} mime={mime_type} remote_mime={remote_mime_type}"
                        )
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
