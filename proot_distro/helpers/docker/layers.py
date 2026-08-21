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

# Architecture: Blob-level operations — download a single layer to the
# local cache, and apply a cached layer to a rootfs directory. The
# applier handles OCI whiteouts (§6.1.2), defers hard-link copies until
# all regular files are written, and stamps directory mtimes last so
# they don't get clobbered by intermediate file writes.

import hashlib
import os
import urllib.request

from proot_distro.atomic import atomic_replace
from proot_distro.progress import clear_bar, draw_bytes_bar
from proot_distro.helpers.download import retry_http
from proot_distro.helpers.docker.cache import (
    layer_cache_path, open_verified_layer, split_digest,
)
from proot_distro.helpers.docker.transport import (
    opener, _ua,
)
from proot_distro.helpers.tar_extract import extract_tar_fd_to_rootfs


def download_blob(
    repo: str, digest: str, token: str, base: str,
    insecure: bool = False,
) -> int:
    """Download a blob to the layer cache; return an open descriptor on it.

    Streams the bytes through sha256 and verifies the result against the
    expected *digest* before promoting the .tmp file. The descriptor is a
    duplicate of the one the bytes were *written* through, so it names
    the very inode that was just hashed — os.replace() carries the inode
    across, and re-opening the destination by name afterwards would have
    cost a second full hash to prove the same thing. The caller closes
    it.

    A blob already in the cache is re-hashed rather than taken at its
    name (open_verified_layer): the file may have been written by
    something other than this function, so its name is not evidence of
    its content. One that fails is dropped and downloaded again.
    """
    dest = layer_cache_path(digest)
    cached = open_verified_layer(digest)
    if cached is not None:
        return cached

    _algo, expected_hex = split_digest(digest)

    url = f"{base}/v2/{repo}/blobs/{digest}"
    headers = {**_ua()}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)

    def _attempt():
        # Fresh hasher per attempt: a retry re-downloads the whole blob, so
        # state from a failed partial download must not carry over.
        hasher = hashlib.sha256()
        try:
            with atomic_replace(dest) as tmp_fd:
                with opener(insecure).open(req) as resp, \
                        open(tmp_fd, "wb", closefd=False) as fh:
                    total = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        fh.write(chunk)
                        hasher.update(chunk)
                        downloaded += len(chunk)
                        draw_bytes_bar(downloaded, total, noun="downloaded")
                actual_hex = hasher.hexdigest()
                if actual_hex != expected_hex.lower():
                    raise RuntimeError(
                        f"Layer integrity check failed for digest '{digest}': "
                        f"expected {expected_hex}, got {actual_hex}."
                    )
                # A duplicate of the descriptor the bytes were written
                # through, so what the caller reads is the inode they
                # went into. atomic_replace closes its own once the
                # rename has promoted it; nothing that later happens to
                # the name changes what this names.
                fd = os.dup(tmp_fd)
                os.lseek(fd, 0, os.SEEK_SET)
        finally:
            clear_bar()
        return fd

    short_id = digest.split(":")[-1][:12]
    return retry_http(_attempt, what=f"Downloading layer {short_id}")


def apply_layer(layer_fd: int, rootfs_fd: int, *, digest: str = "") -> None:
    """Apply one OCI/Docker layer (gzipped tar) into the *rootfs_fd* tree.

    Takes the **descriptor** the verification handed back, not a path, so
    the inode read is the one that was hashed (see
    cache.open_verified_layer). With *digest* the extraction re-hashes as
    it consumes and refuses a total that does not match, which is what
    covers the remaining case a descriptor cannot: the same inode
    truncated and rewritten in place. The rootfs is a descriptor for the
    same reason the blob is: every member goes in as (dir_fd, name)
    beneath it. Thin wrapper around
    extract_tar_fd_to_rootfs that turns on OCI whiteout handling
    (.wh.<name> deletes sibling, .wh..wh..opq clears the parent dir). See
    that function for the full set of invariants enforced during
    extraction.
    """
    short = digest.split(":")[-1][:12] if digest else ""
    expected = split_digest(digest)[1] if digest else ""
    extract_tar_fd_to_rootfs(
        layer_fd, rootfs_fd, handle_whiteouts=True,
        subject=f"layer {short}" if short else "layer",
        expected_sha256=expected,
    )
