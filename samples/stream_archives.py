#!/usr/bin/env python3
#
# Proot-Distro - manage proot containers.
#
# Created by Sylirre <sylirre@termux.dev> for Termux project.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""Back up or restore a container through caller-owned binary streams.

Passing an open binary object instead of a path is useful for sockets,
in-memory buffers, hashing pipelines, and application-managed files. This
sample wraps a host file to calculate its SHA-256 digest while the API writes
or reads it.

Examples::

    python samples/stream_archives.py backup my-container backup.tar.gz
    python samples/stream_archives.py restore backup.tar.gz

Because a stream has no filename, backup compression cannot be inferred from
the suffix. The ``backup`` command therefore defaults to gzip and passes it
explicitly. It opens the destination exclusively and removes partial output if
the operation fails. The API does not close, seek, or flush caller-owned
streams; this script owns those responsibilities through ``with`` blocks.

Restore installs the container name stored in the archive and refuses to
replace an existing container.
"""

import argparse
import hashlib
import os
from pathlib import Path
import sys
from typing import BinaryIO, Optional, Sequence

# Make direct execution from a source checkout use that exact checkout. An
# installed copy needs no adjustment because these samples are repository files.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proot_distro.api import ProotDistro, ProotDistroError


class HashingWriter:
    """Writable binary stream adapter that counts and hashes accepted bytes."""

    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.digest = hashlib.sha256()
        self.bytes_written = 0

    def writable(self) -> bool:
        return True

    def write(self, data: bytes) -> int:
        written = self.stream.write(data)
        if written is None:
            # BinaryIO.write normally returns an int. Keep the adapter's
            # contract explicit because the API validates short writes.
            raise OSError("archive stream did not report its write count")
        self.digest.update(bytes(data[:written]))
        self.bytes_written += written
        return written

    def hexdigest(self) -> str:
        return self.digest.hexdigest()


class HashingReader:
    """Readable binary stream adapter that hashes bytes consumed by restore."""

    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.digest = hashlib.sha256()
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        data = self.stream.read(size)
        self.digest.update(data)
        self.bytes_read += len(data)
        return data

    def hexdigest(self) -> str:
        return self.digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)

    backup = commands.add_parser("backup", help="stream a container backup")
    backup.add_argument("container", help="installed container name")
    backup.add_argument("archive", type=Path, help="new host archive path")
    backup.add_argument(
        "--compression",
        choices=("none", "gzip", "bzip2", "xz", "zstd"),
        default="gzip",
        help="stream compression (default: gzip)",
    )

    restore = commands.add_parser("restore", help="restore a streamed backup")
    restore.add_argument("archive", type=Path, help="existing host archive path")
    return parser


def _backup(pd: ProotDistro, container: str, archive: Path, compression: str) -> int:
    created = False
    try:
        with archive.open("xb") as raw:
            created = True
            stream = HashingWriter(raw)
            result = pd.backup(
                container,
                stream,
                compression=None if compression == "none" else compression,
            )
            raw.flush()
            os.fsync(raw.fileno())
        print(
            f"Wrote {stream.bytes_written} bytes for {result.container} "
            f"using {result.compression}."
        )
        print(f"SHA-256: {stream.hexdigest()}")
        return 0
    except (OSError, ProotDistroError) as exc:
        if created:
            try:
                archive.unlink()
            except OSError:
                pass
        operation = getattr(exc, "operation", "") or "backup"
        print(f"{operation} failed: {exc}")
        return 1


def _restore(pd: ProotDistro, archive: Path) -> int:
    try:
        with archive.open("rb") as raw:
            stream = HashingReader(raw)
            result = pd.restore(stream)
        print(
            f"Restored {result.container.name} from {stream.bytes_read} bytes."
        )
        print(f"SHA-256: {stream.hexdigest()}")
        return 0
    except (OSError, ProotDistroError) as exc:
        operation = getattr(exc, "operation", "") or "restore"
        print(f"{operation} failed: {exc}")
        return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Dispatch one streamed archive operation."""
    args = _parser().parse_args(argv)
    try:
        pd = ProotDistro()
    except ProotDistroError as exc:
        print(f"{exc.operation or 'initialize'} failed: {exc}")
        return 1
    if args.command == "backup":
        return _backup(pd, args.container, args.archive, args.compression)
    return _restore(pd, args.archive)


if __name__ == "__main__":
    raise SystemExit(main())
