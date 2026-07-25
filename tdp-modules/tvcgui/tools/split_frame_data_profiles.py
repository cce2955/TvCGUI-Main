"""Split the legacy frame-data profile cache into per-character files."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from tvcgui.features.frame_data.profile_store import (
    PROFILE_DIRECTORY_NAME,
    split_legacy_profile_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Split frame_data_profiles.json into one JSON file per character."
    )
    parser.add_argument(
        "source",
        nargs="?",
        default=os.path.join("data", "frame_data", "frame_data_profiles.json"),
    )
    parser.add_argument(
        "--output",
        default=os.path.join("data", "frame_data", PROFILE_DIRECTORY_NAME),
    )
    parser.add_argument("--keep-existing", action="store_true")
    parser.add_argument("--delete-source", action="store_true")
    args = parser.parse_args()

    report = split_legacy_profile_file(
        args.source,
        args.output,
        overwrite=not args.keep_existing,
    )
    print(f"Profiles found: {report['profile_count']}")
    print(f"Profiles written: {len(report['written'])}")
    print(f"Profiles skipped: {len(report['skipped'])}")
    print(f"Profiles failed: {len(report['failed'])}")
    print(f"Output: {report['output_directory']}")

    if report["failed"]:
        for path in report["failed"]:
            print(f"FAILED: {path}")
        return 1

    if args.delete_source:
        Path(report["source"]).unlink()
        print(f"Deleted legacy source: {report['source']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
