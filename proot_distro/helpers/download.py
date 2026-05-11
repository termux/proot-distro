#
# Proot-Distro - manage proot containers on Termux.
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

# Architecture: Generic HTTP download utilities with progress bars and retry
# logic. Kept separate from docker.py so the download primitives can be
# reused independently. Progress output goes to stderr to stay out of data
# pipelines. sha256_file() is provided for integrity verification.

import hashlib
import os
import sys
import time
import urllib.error
import urllib.request

from proot_distro.constants import PROGRAM_VERSION
from proot_distro.colors import C, msg


def fmt_size(n_bytes: int) -> str:
    """Return a human-readable size string (B, KiB, or MiB)."""
    if n_bytes >= 1 << 20:
        return f"{n_bytes / (1 << 20):.1f} MiB"
    if n_bytes >= 1 << 10:
        return f"{n_bytes / (1 << 10):.1f} KiB"
    return f"{n_bytes} B"


def sha256_file(path: str) -> str:
    """Compute and return the SHA-256 hex digest of *path*, with a progress bar."""
    h = hashlib.sha256()
    total = os.path.getsize(path)
    processed = 0
    use_tty = sys.stderr.isatty()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            processed += len(chunk)
            if use_tty:
                pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                if total:
                    pct = processed * 100 // total
                    bar_filled = pct // 5
                    bar = "#" * bar_filled + "-" * (20 - bar_filled)
                    line = (f"\r{pfx}[{bar}] {pct:3d}%"
                            f"  {fmt_size(processed)} / {fmt_size(total)}"
                            f"\033[K{C['RST']}")
                else:
                    line = f"\r{pfx}{fmt_size(processed)} processed...\033[K{C['RST']}"
                sys.stderr.write(line)
                sys.stderr.flush()
    if use_tty:
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()
    return h.hexdigest()


def download_file(
    url: str, dest: str, max_retries: int = 5, retry_delay: int = 5
) -> None:
    """Download *url* to *dest* with progress output, redirects, and retries."""
    tmp = dest + ".tmp"
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": f"proot-distro/{PROGRAM_VERSION}"},
            )
            with urllib.request.urlopen(req) as resp, open(tmp, "wb") as fh:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                use_tty = sys.stderr.isatty()
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if use_tty:
                        pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                        if total:
                            pct = downloaded * 100 // total
                            bar_filled = pct // 5
                            bar = "#" * bar_filled + "-" * (20 - bar_filled)
                            line = (f"\r{pfx}[{bar}] {pct:3d}%"
                                    f"  {fmt_size(downloaded)}"
                                    f" / {fmt_size(total)}\033[K{C['RST']}")
                        else:
                            line = (f"\r{pfx}"
                                    f"{fmt_size(downloaded)} downloaded..."
                                    f"\033[K{C['RST']}")
                        sys.stderr.write(line)
                        sys.stderr.flush()
                if use_tty:
                    sys.stderr.write("\r\033[K")
                    sys.stderr.flush()
            os.replace(tmp, dest)
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Finished downloading ({fmt_size(downloaded)}).{C['RST']}")
            return
        except KeyboardInterrupt:
            if sys.stderr.isatty():
                sys.stderr.write("\r\033[K")
                sys.stderr.flush()
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        except (urllib.error.URLError, OSError) as exc:
            if sys.stderr.isatty():
                sys.stderr.write("\r\033[K")
                sys.stderr.flush()
            try:
                os.remove(tmp)
            except OSError:
                pass
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                msg()
                msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
                    f"Download failure, please check your network "
                    f"connection.{C['RST']}")
                raise RuntimeError(
                    f"Failed to download {url}: {exc}"
                ) from exc
