"""Command line interface for :mod:`xboxiso`.

Example usage::

    python -m xboxiso.cli my_game.iso ./my_game

The module can also be installed as a script via ``python -m pip install`` and
executed with ``python -m xboxiso`` thanks to the entry point defined in
``pyproject.toml``.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

from .image import IsoEntry, XboxIso


def _print_listing(entries: Iterable[IsoEntry]) -> None:
    for entry in entries:
        path = entry.path.as_posix() or "."
        if entry.is_directory:
            print(f"<DIR> {path}")
        else:
            print(f"{entry.size:>10} {path}")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Xbox / Xbox 360 ISO extractor")
    parser.add_argument("image", help="path to the Xbox ISO image")
    parser.add_argument(
        "destination",
        nargs="?",
        help="directory where the files should be extracted (defaults to the ISO name)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list the contents instead of extracting them",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    with XboxIso.open(args.image) as iso:
        if args.list:
            _print_listing(iso.iter_entries())
            return 0

        destination = Path(args.destination) if args.destination else Path(args.image).with_suffix("")
        count = iso.extract_all(destination)
        print(f"Extracted {count} entries into {destination}")
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
