"""
Background removal pipeline for host photos.
Uses rembg (free, runs locally, no API key needed).

Usage:
  python remove_backgrounds.py              # Process all hosts
  python remove_backgrounds.py --host Sam   # Process one host
  python remove_backgrounds.py --list       # Preview without processing
  python remove_backgrounds.py --force      # Re-process already done images
"""

import sys
import argparse
from pathlib import Path
from PIL import Image
from rembg import remove, new_session

HOST_PHOTOS_DIR = Path(__file__).parent / "thumbnails" / "00_Host Pictures"
SKIP_EXTENSIONS = {".mp4", ".txt"}
SKIP_NAMES = {"Gemini_Generated_Image_6hy86b6hy86b6hy8.png"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def get_emotion_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    if " - " in stem:
        return stem.split(" - ", 1)[1].strip().lower()
    return ""


def get_cutout_path(image_path: Path) -> Path:
    cutouts_dir = image_path.parent / "cutouts"
    return cutouts_dir / (image_path.stem + ".png")


def process_image(image_path: Path, output_path: Path, session) -> bool:
    try:
        with open(image_path, "rb") as f:
            input_data = f.read()

        output_data = remove(input_data, session=session)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(output_data)

        emotion = get_emotion_from_filename(image_path.name)
        label = f" [{emotion}]" if emotion else ""
        print(f"  ✓ {image_path.name}{label}")
        return True

    except Exception as e:
        print(f"  ✗ {image_path.name} — {e}")
        return False


def get_images(host_dir: Path) -> list:
    return sorted([
        f for f in host_dir.iterdir()
        if f.is_file()
        and f.suffix.lower() in IMAGE_EXTENSIONS
        and f.name not in SKIP_NAMES
        and f.parent.name != "cutouts"
    ])


def process_host(host_dir: Path, session, force: bool = False) -> dict:
    host_name = host_dir.name
    images = get_images(host_dir)

    pending = [img for img in images if not get_cutout_path(img).exists() or force]
    done = [img for img in images if get_cutout_path(img).exists() and not force]

    print(f"\n{host_name} — {len(pending)} to process, {len(done)} already done")

    processed, failed = 0, 0
    for img in pending:
        success = process_image(img, get_cutout_path(img), session)
        if success:
            processed += 1
        else:
            failed += 1

    return {"processed": processed, "already_done": len(done), "failed": failed}


def main():
    parser = argparse.ArgumentParser(description="Remove backgrounds from host photos (free, local)")
    parser.add_argument("--host", help="Process only this host (e.g. Sam, Riley)")
    parser.add_argument("--force", action="store_true", help="Re-process already done images")
    parser.add_argument("--list", action="store_true", help="List all images without processing")
    args = parser.parse_args()

    if not HOST_PHOTOS_DIR.exists():
        print(f"ERROR: Not found: {HOST_PHOTOS_DIR}")
        sys.exit(1)

    host_dirs = sorted([
        d for d in HOST_PHOTOS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    ])

    if args.host:
        host_dirs = [d for d in host_dirs if d.name.lower() == args.host.lower()]
        if not host_dirs:
            available = [d.name for d in HOST_PHOTOS_DIR.iterdir() if d.is_dir()]
            print(f"Host '{args.host}' not found. Available: {available}")
            sys.exit(1)

    if args.list:
        print("Host photos:\n")
        for host_dir in host_dirs:
            images = get_images(host_dir)
            print(f"{host_dir.name} ({len(images)} photos):")
            for img in images:
                emotion = get_emotion_from_filename(img.name)
                cutout = get_cutout_path(img)
                status = "✓" if cutout.exists() else "○"
                label = f" [{emotion}]" if emotion else ""
                print(f"  {status} {img.name}{label}")
        return

    print("Loading AI model (downloads once, ~170MB on first run)...")
    session = new_session("u2net")  # Best general-purpose model, free

    all_results = {}
    for host_dir in host_dirs:
        all_results[host_dir.name] = process_host(host_dir, session, force=args.force)

    print(f"\n{'='*40}")
    total_new = sum(r["processed"] for r in all_results.values())
    total_done = sum(r["already_done"] for r in all_results.values())
    total_fail = sum(r["failed"] for r in all_results.values())
    print(f"Done: {total_new} new cutouts, {total_done} skipped, {total_fail} failed")
    if total_new > 0:
        print(f"Saved to: thumbnails/00_Host Pictures/<Host>/cutouts/")


if __name__ == "__main__":
    main()
