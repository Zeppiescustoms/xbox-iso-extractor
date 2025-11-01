from __future__ import annotations

import io
import struct
from pathlib import Path, PurePosixPath

from xboxiso import FILE_ATTRIBUTE_DIRECTORY, XboxIso
from xboxiso.cli import main as cli_main


SECTOR_SIZE = 2048
MAGIC = b"MICROSOFT*XBOX*MEDIA"


def _pad4(data: bytes) -> bytes:
    if len(data) % 4 == 0:
        return data
    return data + b"\x00" * (4 - (len(data) % 4))


def _make_entry(
    name: str,
    *,
    sector: int,
    size: int,
    attributes: int,
    left: int = 0,
    right: int = 0,
    unicode: bool = False,
    timestamp: int = 0,
) -> bytes:
    if unicode:
        name_bytes = name.encode("utf-16le")
        name_length = len(name)
        flags = 1
    else:
        name_bytes = name.encode("ascii")
        name_length = len(name_bytes)
        flags = 0

    header = struct.pack(
        "<IIIIIIHH",
        left,
        right,
        sector,
        size,
        attributes,
        timestamp,
        name_length,
        flags,
    )
    return _pad4(header + name_bytes)


def build_sample_iso() -> tuple[bytes, dict[PurePosixPath, bytes], set[PurePosixPath]]:
    content_data = b"Hello from root!\n"
    nested_data = b"NESTED" * 3
    unicode_data = "unicod\u00e9".encode("utf-8")

    root_sector = 24
    subdir_sector = 25
    content_sector = 26
    nested_sector = 27
    unicode_sector = 28

    # Sub directory entries -------------------------------------------------
    nested_entry_temp = _make_entry(
        "nested.bin",
        sector=nested_sector,
        size=len(nested_data),
        attributes=0,
    )
    empty_entry = _make_entry(
        "empty",
        sector=0,
        size=0,
        attributes=FILE_ATTRIBUTE_DIRECTORY,
    )
    unicode_entry = _make_entry(
        "unicod\u00e9.txt",
        sector=unicode_sector,
        size=len(unicode_data),
        attributes=0,
        unicode=True,
    )

    empty_offset = len(nested_entry_temp)
    unicode_offset = len(nested_entry_temp) + len(empty_entry)
    nested_entry = _make_entry(
        "nested.bin",
        sector=nested_sector,
        size=len(nested_data),
        attributes=0,
        left=empty_offset,
        right=unicode_offset,
    )

    subdir_table = nested_entry + empty_entry + unicode_entry

    # Root directory entries ------------------------------------------------
    content_entry_temp = _make_entry(
        "content.txt",
        sector=content_sector,
        size=len(content_data),
        attributes=0,
    )
    subdir_entry = _make_entry(
        "subdir",
        sector=subdir_sector,
        size=len(subdir_table),
        attributes=FILE_ATTRIBUTE_DIRECTORY,
    )

    subdir_offset = len(content_entry_temp)
    content_entry = _make_entry(
        "content.txt",
        sector=content_sector,
        size=len(content_data),
        attributes=0,
        right=subdir_offset,
    )

    root_table = content_entry + subdir_entry

    # Assemble ISO image ----------------------------------------------------
    highest_sector = max(root_sector, subdir_sector, content_sector, nested_sector, unicode_sector)
    image = bytearray((highest_sector + 1) * SECTOR_SIZE)

    def write_sector(sector: int, payload: bytes) -> None:
        start = sector * SECTOR_SIZE
        image[start : start + len(payload)] = payload

    header = bytearray(SECTOR_SIZE)
    header[: len(MAGIC)] = MAGIC
    struct.pack_into("<III", header, len(MAGIC), SECTOR_SIZE, root_sector, len(root_table))
    header[len(MAGIC) + 12 : len(MAGIC) + 12 + len("TESTISO")] = b"TESTISO"
    write_sector(0, header)
    write_sector(root_sector, root_table)
    write_sector(subdir_sector, subdir_table)
    write_sector(content_sector, content_data)
    write_sector(nested_sector, nested_data)
    write_sector(unicode_sector, unicode_data)

    expected_files = {
        PurePosixPath("content.txt"): content_data,
        PurePosixPath("subdir/nested.bin"): nested_data,
        PurePosixPath("subdir/unicod\u00e9.txt"): unicode_data,
    }
    expected_dirs = {PurePosixPath("subdir"), PurePosixPath("subdir/empty")}

    return bytes(image), expected_files, expected_dirs


def test_iter_entries_lists_expected_structure() -> None:
    data, expected_files, expected_dirs = build_sample_iso()

    iso = XboxIso(io.BytesIO(data))
    entries = list(iso.iter_entries())

    paths = {entry.path: entry for entry in entries}

    for directory in expected_dirs:
        assert directory in paths
        assert paths[directory].is_directory

    for path, payload in expected_files.items():
        assert path in paths
        entry = paths[path]
        assert entry.is_file
        assert entry.size == len(payload)


def test_extract_all_writes_files(tmp_path) -> None:
    data, expected_files, expected_dirs = build_sample_iso()

    iso_path = tmp_path / "image.iso"
    iso_path.write_bytes(data)

    with XboxIso.open(iso_path) as iso:
        count = iso.extract_all(tmp_path / "out")

    # We expect to see all directories plus files.
    assert count == len(expected_dirs) + len(expected_files)

    for directory in expected_dirs:
        assert (tmp_path / "out" / Path(*directory.parts)).is_dir()

    for path, payload in expected_files.items():
        assert (tmp_path / "out" / Path(*path.parts)).read_bytes() == payload


def test_cli_list_and_extract(tmp_path, capsys) -> None:
    data, expected_files, expected_dirs = build_sample_iso()
    iso_path = tmp_path / "test.iso"
    iso_path.write_bytes(data)

    # Listing -----------------------------------------------------------------
    exit_code = cli_main(["--list", str(iso_path)])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "<DIR> subdir" in output
    assert "content.txt" in output

    # Extraction --------------------------------------------------------------
    out_dir = tmp_path / "extracted"
    exit_code = cli_main([str(iso_path), str(out_dir)])
    assert exit_code == 0
    for path, payload in expected_files.items():
        assert (out_dir / Path(*path.parts)).read_bytes() == payload

    for directory in expected_dirs:
        assert (out_dir / Path(*directory.parts)).is_dir()
