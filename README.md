# WanderToWonder

Personal travelogue sharing experiences from journeys across the globe.

## OneDrive Shared Album Integration

Each destination page now loads photos directly from a OneDrive shared album/folder link via `gallery.js`.

### How to configure a destination gallery

1. Open the destination page (`australia.html`, `dubai.html`, `srilanka.html`, etc.).
2. Replace the `PASTE_..._ONEDRIVE_SHARED_ALBUM_LINK_HERE` value with your OneDrive shared link.
3. Ensure the link permission is `Anyone with the link can view`.
4. Reload the page.

### Fallback behavior

- If OneDrive loading fails, the gallery tries local JSON from `data/<destination>.json`.
- If both fail, an error panel is shown with setup guidance.

### Reliable workaround (recommended)

OneDrive now often blocks anonymous browser API listing for shared albums.  
Use local sync once, then the website gallery works normally:

```bash
python scripts/sync-gallery.py --source "C:\path\to\Australia\photos" --trip australia
```

This command copies images into `images/australia/` and creates `data/australia.json`.
