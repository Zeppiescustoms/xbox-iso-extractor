# Xbox ISO Extractor

This repository contains a small, well tested Python library that can list and
extract files from Xbox and Xbox 360 ISO images.  It focuses on the XDVDFS file
system that is used by the retail discs and therefore works on the majority of
"xiso" dumps floating around the web.

## Features

* Parse the XDVDFS directory tree (binary search tree based structure).
* Read ASCII as well as UTF-16 directory entry names.
* Extract large files efficiently using streaming I/O.
* Simple, well documented Python API.
* Command line interface that can list and extract the contents of an image.

## Installation

The project is a regular Python package.  Install it into a virtual environment
using ``pip``::

    python -m pip install .

Running the module in editable/development mode works equally well.

## Command line usage

After installation an ``xboxiso`` command becomes available.  Pass an ISO image
and optionally an output directory.  When no output directory is specified, the
ISO name (without the ``.iso`` suffix) is used::

    xboxiso my_game.iso ./my_game

To merely inspect the contents without extracting anything use ``--list``::

    xboxiso --list my_game.iso

Each entry will be printed alongside its size.  Directories are shown with a
``<DIR>`` tag.

## Library usage

The :class:`xboxiso.XboxIso` class powers the command line interface and can be
used directly from Python code:

```python
from pathlib import Path
from xboxiso import XboxIso

with XboxIso.open("my_game.iso") as iso:
    for entry in iso.iter_entries():
        if entry.is_file:
            print(f"Extracting {entry.path} ({entry.size} bytes)")

    iso.extract_all(Path("./out"))
```

## Development

The project uses ``pytest`` for tests.  Run the suite with::

    pytest

The repository ships with a toy ISO generator used in the tests.  Real images
can be huge; do not add them to the repository.
