"""Xbox 360 XDVDFS image reader and extractor.

The implementation focuses on the bits of the XDVDFS ("Xbox DVD File System")
format that are required to list and extract the files from an Xbox or Xbox 360
ROM.  The goal is not to be the most feature complete parser out there but to
provide a clean, well documented and fully tested reference implementation.

The file system that ships on Xbox discs stores directory entries in a binary
search tree.  Each entry contains offsets (``left``/``right``) that point to
other entries within the directory table.  The order in which we visit entries
therefore needs to follow an in-order traversal.  The directory entry structure
is documented in a handful of open source projects.  The layout used here is
based on the information that Microsoft published in the original Xbox XDK and
reverse engineered from public tooling such as ``extract-xiso``:

* ``left`` and ``right`` are 32-bit offsets into the directory table.
* ``sector`` and ``size`` describe where the entry's payload lives.  For files
  this references the raw file data, for directories it references another
  directory table.
* ``attributes`` are the Windows style ``FILE_ATTRIBUTE_*`` flags.  Bit 0x10
  (``FILE_ATTRIBUTE_DIRECTORY``) marks directories.
* ``name_length`` stores the number of characters.  ``name_flags`` indicates
  whether the characters are stored as ASCII/UTF-8 or UTF-16LE.

The code below is intentionally defensive and validates offsets in the
structure.  This keeps the extractor from looping forever on malformed images
and makes it more robust against bad dumps.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator, Optional

__all__ = ["IsoEntry", "XboxIso", "FILE_ATTRIBUTE_DIRECTORY"]

# XDVDFS constants ---------------------------------------------------------

FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_NORMAL = 0x80

MAGIC = b"MICROSOFT*XBOX*MEDIA"
DEFAULT_SECTOR_SIZE = 2048
_HEADER_STRUCT_SIZE = 0x50  # large enough to contain the information we need


@dataclass(frozen=True)
class IsoEntry:
    """A single file-system entry inside an Xbox ISO image.

    Parameters
    ----------
    path:
        The path relative to the root of the image using POSIX style
        separators.
    start_sector:
        Sector containing the entry's payload.  For directories this is the
        sector of the nested directory table.
    size:
        Size of the entry's payload.  For directories it describes the size of
        the nested directory table in bytes.
    attributes:
        The raw Windows style attribute bitfield.
    timestamp_raw:
        32-bit timestamp value stored inside the XDVDFS entry.  Public
        documentation suggests that this contains the number of seconds since
        1/1/1970.  The value is exposed for callers that wish to interpret it
        further but is otherwise unused by the extractor.
    """

    path: PurePosixPath
    start_sector: int
    size: int
    attributes: int
    timestamp_raw: int

    @property
    def is_directory(self) -> bool:
        """Return ``True`` when this entry represents a directory."""

        return (self.attributes & FILE_ATTRIBUTE_DIRECTORY) != 0

    @property
    def is_file(self) -> bool:
        """Return ``True`` when this entry represents a regular file."""

        return not self.is_directory


@dataclass
class _DirectoryEntry:
    """Internal representation of a single directory record."""

    left: int
    right: int
    sector: int
    size: int
    attributes: int
    timestamp_raw: int
    name: str
    name_length: int
    name_flags: int

    @property
    def is_directory(self) -> bool:
        return (self.attributes & FILE_ATTRIBUTE_DIRECTORY) != 0


class XboxIso:
    """Reader for Xbox and Xbox 360 ISO images.

    ``XboxIso`` can be constructed from any seekable binary file object.  Use
    :meth:`XboxIso.open` for the convenient path based constructor.  Instances
    implement the context manager protocol so they can be used with ``with``.
    """

    def __init__(self, fp: BinaryIO, *, close_fp: bool = False) -> None:
        self._fp: Optional[BinaryIO] = fp
        self._close_fp = close_fp

        self.sector_size: int = DEFAULT_SECTOR_SIZE
        self.root_sector: int = 0
        self.root_size: int = 0
        self.volume_name: str = ""

        self._read_header()

    # -- basic file handling -------------------------------------------------
    def close(self) -> None:
        """Close the underlying file object if required."""

        if self._fp is not None and self._close_fp:
            self._fp.close()
        self._fp = None

    def __enter__(self) -> "XboxIso":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        self.close()

    # -- construction -------------------------------------------------------
    @classmethod
    def open(cls, path: str | Path) -> "XboxIso":
        """Open an ISO image from ``path`` and return a reader instance."""

        fp = open(path, "rb")
        return cls(fp, close_fp=True)

    # -- public API ---------------------------------------------------------
    def iter_entries(self) -> Iterator[IsoEntry]:
        """Yield :class:`IsoEntry` objects for every file and directory."""

        if self._fp is None:
            raise RuntimeError("the ISO image has been closed")

        yield from self._iterate_directory(self.root_sector, self.root_size, PurePosixPath(""), True)

    def extract_all(self, destination: str | Path) -> int:
        """Extract the entire ISO into ``destination``.

        Directories are created automatically.  Existing files are silently
        overwritten.  Extraction streams the file data and therefore scales to
        very large images.

        Returns
        -------
        int
            The number of entries (files *and* directories) that have been
            created.
        """

        if self._fp is None:
            raise RuntimeError("the ISO image has been closed")

        base_path = Path(destination)
        base_path.mkdir(parents=True, exist_ok=True)

        extracted = 0
        for entry in self.iter_entries():
            target_path = base_path.joinpath(*entry.path.parts)
            if entry.is_directory:
                target_path.mkdir(parents=True, exist_ok=True)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                self._extract_file(entry, target_path)
            extracted += 1

        return extracted

    # -- parsing helpers ----------------------------------------------------
    def _read_header(self) -> None:
        if self._fp is None:
            raise RuntimeError("file object is not available")

        self._fp.seek(0)
        header = self._fp.read(_HEADER_STRUCT_SIZE)
        if len(header) < len(MAGIC):
            raise ValueError("image too small to contain an XDVDFS header")

        if not header.startswith(MAGIC):
            raise ValueError("not an Xbox/Xbox 360 ISO image")

        # The volume descriptor immediately follows the magic string.
        # Layout (all little endian)::
        #
        #   uint32 sector_size
        #   uint32 root_sector
        #   uint32 root_size
        #   char   volume_name[0x28]
        offset = len(MAGIC)
        remaining = header[offset:]
        if len(remaining) < 0x34:
            raise ValueError("corrupt or truncated XDVDFS header")

        sector_size, root_sector, root_size = int.from_bytes(remaining[0:4], "little"), \
            int.from_bytes(remaining[4:8], "little"), int.from_bytes(remaining[8:12], "little")

        self.sector_size = sector_size or DEFAULT_SECTOR_SIZE
        self.root_sector = root_sector
        self.root_size = root_size

        raw_name = remaining[12:12 + 0x28]
        nul_pos = raw_name.find(b"\x00")
        if nul_pos != -1:
            raw_name = raw_name[:nul_pos]
        self.volume_name = raw_name.decode("ascii", errors="ignore")

    def _iterate_directory(
        self,
        sector: int,
        size: int,
        base_path: PurePosixPath,
        skip_root_entry: bool,
    ) -> Iterator[IsoEntry]:
        if size <= 0:
            return

        table = self._read_directory_table(sector, size)
        yield from self._visit_directory_table(table, base_path, skip_root_entry)

    def _visit_directory_table(
        self,
        table: bytes,
        base_path: PurePosixPath,
        skip_root_entry: bool,
    ) -> Iterator[IsoEntry]:
        visited: set[int] = set()

        if not table:
            return iter(())  # type: ignore[return-value]

        def visit(node_offset: int, skip_current: bool) -> Iterator[IsoEntry]:
            if node_offset in visited:
                raise ValueError("directory tree contains a cycle")
            if node_offset < 0 or node_offset >= len(table):
                raise ValueError("directory entry offset out of range")
            visited.add(node_offset)

            entry = self._parse_directory_entry(table, node_offset)

            if entry.left:
                if entry.left >= len(table):
                    raise ValueError("directory left pointer out of range")
                yield from visit(entry.left, False)

            placeholder = skip_current and entry.name == ""
            current_path = base_path / entry.name if entry.name else base_path
            if not placeholder:
                yield IsoEntry(
                    path=current_path,
                    start_sector=entry.sector,
                    size=entry.size,
                    attributes=entry.attributes,
                    timestamp_raw=entry.timestamp_raw,
                )

            if entry.is_directory:
                yield from self._iterate_directory(entry.sector, entry.size, current_path, False)

            if entry.right:
                if entry.right >= len(table):
                    raise ValueError("directory right pointer out of range")
                yield from visit(entry.right, False)

        return visit(0, skip_root_entry)

    def _read_directory_table(self, sector: int, size: int) -> bytes:
        if self._fp is None:
            raise RuntimeError("file object is not available")

        self._fp.seek(sector * self.sector_size)
        data = self._fp.read(size)
        if len(data) < size:
            raise ValueError("unexpected end of image while reading directory")
        return data

    def _parse_directory_entry(self, table: bytes, offset: int) -> _DirectoryEntry:
        if offset + 32 > len(table):
            raise ValueError("directory entry truncated")

        left = int.from_bytes(table[offset:offset + 4], "little")
        right = int.from_bytes(table[offset + 4:offset + 8], "little")
        sector = int.from_bytes(table[offset + 8:offset + 12], "little")
        size = int.from_bytes(table[offset + 12:offset + 16], "little")
        attributes = int.from_bytes(table[offset + 16:offset + 20], "little")
        timestamp_raw = int.from_bytes(table[offset + 20:offset + 24], "little")

        # First attempt: the name length/flags pair used by the Xbox 360 SDK.
        name_length = int.from_bytes(table[offset + 24:offset + 26], "little")
        name_flags = int.from_bytes(table[offset + 26:offset + 28], "little")
        name_offset = offset + 28

        name, actual_length, actual_flags = self._decode_name(
            table, name_offset, name_length, name_flags
        )
        if name is None:
            # Fall back to the original Xbox layout where only a single byte is
            # used for the length and no encoding flags are present.
            name_length = table[offset + 24]
            name_flags = 0
            name_offset = offset + 25
            name, actual_length, actual_flags = self._decode_name(
                table, name_offset, name_length, name_flags, legacy_format=True
            )
            if name is None:
                raise ValueError("failed to decode directory entry name")

        return _DirectoryEntry(
            left=left,
            right=right,
            sector=sector,
            size=size,
            attributes=attributes,
            timestamp_raw=timestamp_raw,
            name=name,
            name_length=actual_length,
            name_flags=actual_flags,
        )

    def _decode_name(
        self,
        table: bytes,
        name_offset: int,
        name_length: int,
        name_flags: int,
        legacy_format: bool = False,
    ) -> tuple[Optional[str], int, int]:
        if name_length == 0:
            return "", 0, name_flags

        if name_length < 0:
            return None, 0, name_flags

        is_unicode = (name_flags & 0x0001) != 0 and not legacy_format
        multiplier = 2 if is_unicode else 1
        byte_length = name_length * multiplier

        if name_offset + byte_length > len(table):
            return None, 0, name_flags

        raw = table[name_offset:name_offset + byte_length]
        encoding = "utf-16le" if is_unicode else "utf-8"
        try:
            text = raw.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            text = raw.decode(encoding, errors="ignore")

        return text.rstrip("\x00"), name_length, name_flags

    def _extract_file(self, entry: IsoEntry, target_path: Path) -> None:
        if self._fp is None:
            raise RuntimeError("file object is not available")

        offset = entry.start_sector * self.sector_size
        remaining = entry.size

        self._fp.seek(offset)
        with open(target_path, "wb") as out_file:
            while remaining > 0:
                chunk = self._fp.read(min(1 << 20, remaining))
                if not chunk:
                    raise ValueError("unexpected end of image while reading file data")
                out_file.write(chunk)
                remaining -= len(chunk)
