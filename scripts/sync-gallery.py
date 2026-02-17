import os
import json
import shutil

# CONFIGURATION
SOURCE_FOLDER = r"C:\Users\Sumeet.Boob\OneDrive - Brillio\Pictures\Australia" # Update this path!
TARGET_IMAGE_DIR = "../images/australia"
TARGET_JSON_PATH = "../data/australia.json"

def sync_gallery():
    print("🚀 Starting Australia Gallery Sync...")
    
    # 1. Ensure target directory exists
    if not os.path.exists(TARGET_IMAGE_DIR):
        os.makedirs(TARGET_IMAGE_DIR)
        print(f"Created directory: {TARGET_IMAGE_DIR}")

    # 2. Get images from source
    if not os.path.exists(SOURCE_FOLDER):
        print(f"❌ ERROR: Source folder not found: {SOURCE_FOLDER}")
        print("Please edit 'scripts/sync-gallery.py' with the correct path to your photos.")
        return

    photos = []
    supported_ext = ('.jpg', '.jpeg', '.png', '.webp')
    
    files = [f for f in os.listdir(SOURCE_FOLDER) if f.lower().endswith(supported_ext)]
    print(f"📷 Found {len(files)} photos in source.")

    # 3. Process and Copy (limiting to top 30 as requested)
    for i, filename in enumerate(files[:30]):
        src_path = os.path.join(SOURCE_FOLDER, filename)
        ext = os.path.splitext(filename)[1]
        new_filename = f"australia-{i+1}{ext}"
        dest_path = os.path.join(TARGET_IMAGE_DIR, new_filename)
        
        # Copy file
        shutil.copy2(src_path, dest_path)
        
        # Add to manifest
        photos.append({
            "src": f"images/australia/{new_filename}",
            "title": filename.split('.')[0].replace('-', ' ').replace('_', ' '),
            "name": filename
        })
        print(f"✅ Cached: {new_filename}")

    # 4. Save manifest
    data_dir = os.path.dirname(TARGET_JSON_PATH)
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
        
    with open(TARGET_JSON_PATH, 'w') as f:
        json.dump(photos, f, indent=4)
        
    print(f"\n✨ Success! {len(photos)} photos cached.")
    print(f"📄 Manifest updated: {TARGET_JSON_PATH}")
    print("\nNext step: Run 'git add .' and push to see them on your live site!")

if __name__ == "__main__":
    sync_gallery()
