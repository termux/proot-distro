#
# Proot-Distro - manage proot containers.
#
# Created by Sylirre <sylirre@termux.dev> for Termux project.
# Development assisted by Claude Code (https://claude.ai/code).
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

# Architecture: everything the program knows about zstd. The format is
# usable only from Python 3.14 (PEP 784 added `compression.zstd` and
# taught `tarfile` the 'zst' mode) and only in an interpreter actually
# built against libzstd, so a single probe here answers "can this
# interpreter do zstd?" for every caller.
#
# Reading needs no help beyond that probe: tarfile's `r|*` / `r:*`
# auto-detect recognises the zstd magic from 3.14 on, so every archive
# this program opens by auto-detect — OCI layers, plain rootfs
# tarballs, backup archives — reads zstd for free. What it does need is
# a decent diagnosis on an interpreter that cannot: the same archive
# otherwise dies deep inside tarfile as ReadError('truncated header')
# for a stream, or a four-line "file could not be opened successfully"
# dump for random access, neither of which mentions zstd or the Python
# version. `require_read_support()` sniffs the four magic bytes before
# tarfile is handed the file and raises a RuntimeError that says so.
#
# Writing needs an actual workaround. `tarfile.open(mode='w|zst')`
# rejects a compression level outright — "compresslevel is only valid
# for w|gz and w|bz2 modes" — while the seekable `w:zst` spelling takes
# `level=`. Left alone that would make `backup -o out.tar.zst` and
# `backup > out.tar.zst` compress differently, the piped form stuck at
# libzstd's default level 3. `open_tar_writer()` builds the ZstdFile
# itself and gives tarfile a plain `w|` stream to write into, so both
# spellings go through one code path at one level.

import sys
import tarfile

from contextlib import contextmanager, ExitStack

try:
    from compression import zstd as _zstd
except ImportError:     # Python < 3.14, or a build without libzstd.
    _zstd = None

# Both halves have to be there. tarfile grew its 'zst' mode in the same
# release as the module, but they fail independently: an interpreter
# without libzstd still carries `TarFile.zstopen`, which raises
# CompressionError when called.
ZSTD_AVAILABLE = _zstd is not None and "zst" in tarfile.TarFile.OPEN_METH

# Magic number of a zstandard frame (RFC 8878 section 3.1.1).
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# Level used for the archives this program writes. libzstd's own
# default of 3 is tuned for data read back immediately; a backup is
# written once and kept, so it is worth more time. Level 10 sits at the
# knee of the curve — on 101 MiB of filesystem it beat gzip level 9 (the
# default `.tar.gz` backups already pay for) on both axes, 22 MiB in
# 3.8s against 27 MiB in 23s, where level 15 bought one further MiB for
# six times the time. xz still wins on size (18 MiB) and still costs
# 61s to do it, so it stays the option for whoever wants that trade.
ZSTD_LEVEL = 10


def _unavailable_detail() -> str:
    """Return the reason clause explaining why zstd is unavailable."""
    ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    if sys.version_info >= (3, 14):
        return (f"this Python {ver} interpreter was built without "
                f"libzstd support")
    return f"Python 3.14 or newer is required (running Python {ver})"


def unsupported_msg(subject: str) -> str:
    """Return a sentence explaining that *subject*, being zstd, cannot be read.

    For data that turned out to be zstd — an archive, a layer blob, an
    output format implied by a file extension.
    """
    return f"{subject} uses zstd compression: {_unavailable_detail()}."


def unavailable_msg(subject: str) -> str:
    """Return a sentence explaining that *subject* cannot be provided.

    For something the user asked for by name, where repeating that it
    is zstd would only say the same thing twice.
    """
    return f"{subject} is unavailable: {_unavailable_detail()}."


def header_is_zstd(header: bytes) -> bool:
    """Return True if *header* starts a zstandard frame."""
    return header.startswith(ZSTD_MAGIC)


def file_is_zstd(path: str) -> bool:
    """Return True if the file at *path* starts a zstandard frame.

    An unreadable file answers False: the caller is about to open it
    anyway and will report the real error with better context.
    """
    try:
        with open(path, "rb") as fh:
            return header_is_zstd(fh.read(len(ZSTD_MAGIC)))
    except OSError:
        return False


def require_read_support(path: str, subject: str = "") -> None:
    """Raise RuntimeError if *path* is a zstd archive we cannot read.

    Called before handing a file to tarfile so an unsupported archive
    is named as such instead of surfacing as a corrupt-tar error.
    """
    if ZSTD_AVAILABLE:
        return
    if file_is_zstd(path):
        raise RuntimeError(unsupported_msg(subject or f"'{path}'"))


def require_write_support() -> None:
    """Raise RuntimeError if zstd archives cannot be written."""
    if not ZSTD_AVAILABLE:
        raise RuntimeError(unavailable_msg("zstd compression"))


@contextmanager
def open_tar_writer(output_path, fileobj=None, level: int = ZSTD_LEVEL):
    """Yield a TarFile writing zstd-compressed output at *level*.

    Exactly one of *output_path* (a file to create) and *fileobj* (an
    already-open binary stream) is used; the file is opened here so both
    cases share the single `w|` code path that lets *level* apply — see
    the module docstring for why tarfile's own `w|zst` cannot.
    """
    require_write_support()
    with ExitStack() as stack:
        if output_path is not None:
            fileobj = stack.enter_context(open(output_path, "wb"))
        # Closing order is the reverse of entry: the tar's end-of-archive
        # blocks are written first, then the zstd frame is finished, then
        # the file is closed. ZstdFile does not close what it wraps.
        zfh = stack.enter_context(_zstd.ZstdFile(fileobj, "w", level=level))
        yield stack.enter_context(tarfile.open(fileobj=zfh, mode="w|"))
