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

# Architecture: Post-extraction rootfs fixup helpers. Each function targets a
# single aspect of Termux/Android integration (resolv.conf, /etc/hosts,
# Android UIDs). Kept separate from the install command so the same fixups
# can be applied by other entry points (e.g. restore). No subprocess calls
# here — only Python standard-library filesystem operations.
#
# Everything here runs against a rootfs that was just unpacked from an
# image the user named but did not write, so every entry these functions
# touch is attacker-chosen content. Nothing below addresses a file by
# path: `etc` is opened once with O_NOFOLLOW (open_etc) and each file is
# named as (fd, name) from there, because os.chmod() and open() both
# follow symlinks and the four id files are exactly the names an image
# would ship as links. See open_etc and _append_at.
#
# Each fixup comes in two forms. The *_at form takes an open descriptor on
# `etc`, which is what a caller running more than one of them uses -- the
# rootfs is then resolved once for the whole set, by whoever pinned it. The
# path form is for a caller that holds only a name (optionally with the
# rootfs descriptor to start from) and does a single fixup.

import grp
import os
import pwd
import stat

from proot_distro import dirfd
from proot_distro.constants import (
    DEFAULT_PRIMARY_NS,
    DEFAULT_SECONDARY_NS,
)


# What the id files are chmod'ed to: readable by all, writable by owner.
_ID_FILE_MODE = (
    stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH
)


def open_etc(root_fd: int):
    """Open `etc` under a pinned rootfs descriptor, or None if there is none.

    O_NOFOLLOW, because `etc` is image content like everything else below
    it: an image shipping it as a symlink aimed every write in this module
    at whatever directory the link named — a host directory, since the
    write happens outside proot. Callers close the fd.

    None covers a missing `etc`, one that is a symlink, and one that is not
    a directory. Every call site already skips the fixups when there is no
    `etc` to fix up, so there is nothing new to report here.

    A caller doing more than one fixup opens this once and passes the
    descriptor to the *_at functions below, rather than asking for `etc`
    again per fixup: the writes then all land in the one directory this
    answered about, whatever happens to the name in between.
    """
    try:
        return dirfd.opendir_at(root_fd, "etc")
    except OSError:
        return None


def _open_etc(rootfs: str, root_fd=None):
    """open_etc() for a caller holding a path rather than a descriptor.

    *root_fd* is the rootfs when the caller has pinned it. `build` has
    one for every stage, and the rootfs there is a name inside the
    build's scratch tree, which anything running as the invoking user
    can re-point -- including whatever a previous RUN step left behind.
    """
    own_fd = None
    try:
        if root_fd is None:
            own_fd = root_fd = os.open(rootfs, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return None
    try:
        return open_etc(root_fd)
    finally:
        if own_fd is not None:
            os.close(own_fd)


def _replace_at(etc_fd: int, name: str, content: str) -> None:
    """Replace <etc>/<name> with a plain file holding *content*.

    The old entry is unlinked rather than truncated, so a symlink standing
    under the name is removed instead of written through, and the create is
    O_EXCL (dirfd.open_new_at), so whatever reappears under the name is not
    adopted either — a hard link to a host file being the case nothing about
    the entry could reveal.
    """
    dirfd.unlink_quietly(etc_fd, name)
    try:
        fd, _st = dirfd.open_new_at(etc_fd, name, 0o644)
    except OSError:
        return
    try:
        os.write(fd, content.encode())
    except OSError:
        pass
    finally:
        os.close(fd)


def _append_at(etc_fd: int, name: str, line: str, *,
               create: bool = False) -> bool:
    """Append *line* to <etc>/<name>. True when it was written.

    O_NOFOLLOW so a link shipped under the name is refused rather than
    followed, and dirfd.open_regular_at's fstat refuses every remaining
    type as well: a FIFO would otherwise block the append waiting for a
    reader the image never provides.

    With create=True a missing file is made, which is what `open(path,
    "a")` did for passwd, shadow and group — a minimal image may ship
    none of the three. The create is O_EXCL, so one that appeared in
    between is left alone rather than written into; gshadow keeps its
    original create=False, having always been guarded by an exists check.
    """
    flags = os.O_WRONLY | os.O_APPEND
    try:
        fd, _st = dirfd.open_regular_at(etc_fd, name, flags)
    except FileNotFoundError:
        if not create:
            return False
        try:
            fd, _st = dirfd.open_regular_at(
                etc_fd, name, flags | os.O_CREAT | os.O_EXCL, _ID_FILE_MODE
            )
        except OSError:
            return False
    except OSError:
        return False
    try:
        os.write(fd, line.encode())
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def _chmod_at(etc_fd: int, name: str) -> None:
    """Give <etc>/<name> the id-file mode, if it is there and is a file.

    Through the descriptor: Linux has no AT_SYMLINK_NOFOLLOW for
    fchmodat(2), so naming the entry in os.chmod() handed the mode change
    to whatever a link under it pointed at. An image shipping
    `etc/shadow -> ~/.ssh/id_rsa` had the host's key relaxed to 0644 on a
    plain install, before a word of the rootfs was ever run.
    """
    try:
        fd, _st = dirfd.open_regular_at(etc_fd, name, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fchmod(fd, _ID_FILE_MODE)
    except OSError:
        pass
    finally:
        os.close(fd)


_RESOLV_CONF = (
    f"nameserver {DEFAULT_PRIMARY_NS}\n"
    f"nameserver {DEFAULT_SECONDARY_NS}\n"
)

_HOSTS = (
    "# IPv4.\n"
    "127.0.0.1   localhost.localdomain localhost\n\n"
    "# IPv6.\n"
    "::1         localhost.localdomain localhost"
    " ip6-localhost ip6-loopback\n"
    "fe00::0     ip6-localnet\n"
    "ff00::0     ip6-mcastprefix\n"
    "ff02::1     ip6-allnodes\n"
    "ff02::2     ip6-allrouters\n"
    "ff02::3     ip6-allhosts\n"
)


def write_resolv_conf_at(etc_fd: int) -> None:
    """write_resolv_conf() against an open descriptor on `etc`."""
    _replace_at(etc_fd, "resolv.conf", _RESOLV_CONF)


def write_resolv_conf(rootfs: str, *, root_fd=None) -> None:
    """Replace /etc/resolv.conf with a plain file containing default DNS servers."""
    etc_fd = _open_etc(rootfs, root_fd)
    if etc_fd is None:
        return
    try:
        write_resolv_conf_at(etc_fd)
    finally:
        os.close(etc_fd)


def write_hosts_at(etc_fd: int) -> None:
    """write_hosts() against an open descriptor on `etc`."""
    _replace_at(etc_fd, "hosts", _HOSTS)


def write_hosts(rootfs: str, *, root_fd=None) -> None:
    """Write a minimal /etc/hosts into the rootfs.

    Some images ship /etc/hosts as a symlink (to a runtime-provided path,
    say), so the name is unlinked and recreated rather than opened for
    write — see _replace_at.
    """
    etc_fd = _open_etc(rootfs, root_fd)
    if etc_fd is None:
        return
    try:
        write_hosts_at(etc_fd)
    finally:
        os.close(etc_fd)


def register_android_ids_at(etc_fd: int) -> None:
    """Add the Termux Android UID/GID entries to passwd/shadow/group/gshadow.

    Every one of the four files is addressed as (etc fd, name) with
    O_NOFOLLOW rather than by path. They are the names an image is most
    likely to ship as symlinks, and both operations this does — a chmod
    and an append — follow one: an image shipping `etc/shadow` as a link
    to a file in the invoking user's home had that file relaxed to 0644
    and a passwd line appended to it, on a plain `install`, from nothing
    but the image's own content. See _chmod_at and _append_at.
    """
    for name in ("passwd", "shadow", "group", "gshadow"):
        _chmod_at(etc_fd, name)

    try:
        uid = os.getuid()
        gid = os.getgid()
        username_result = pwd.getpwuid(uid).pw_name
    except Exception:
        return

    if not _append_at(
        etc_fd, "passwd",
        f"aid_{username_result}:x:{uid}:{gid}:Termux:/:/sbin/nologin\n",
        create=True,
    ):
        # passwd is the one file that must be there (install checks for it
        # before calling), so a failure on it means the rest is pointless.
        return
    _append_at(etc_fd, "shadow",
               f"aid_{username_result}:*:18446:0:99999:7:::\n", create=True)

    seen: set[int] = set()
    all_gids: list[int] = []
    for g in [gid] + os.getgroups():
        if g not in seen:
            seen.add(g)
            all_gids.append(g)

    for g in all_gids:
        try:
            gname = grp.getgrgid(g).gr_name
        except KeyError:
            continue
        _append_at(
            etc_fd, "group", f"aid_{gname}:x:{g}:root,aid_{username_result}\n",
            create=True,
        )
        # gshadow is optional; _append_at declines a name that is not there.
        _append_at(
            etc_fd, "gshadow", f"aid_{gname}:*::root,aid_{username_result}\n"
        )

