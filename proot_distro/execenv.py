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

import os

# Architecture: which environment variables belong to the *host side* of
# a proot session rather than to the guest -- and, since both users of
# that rule (login's env builders and the build's RUN launcher) hand a
# dict to an exec, two more things they share: what a guest is told
# about where it is running (IDENTITY_ENV_KEYS), and what may be an
# environment string at all (is_exportable).
#
# proot has no way to set the guest's environment on its own: the dict
# handed to os.execvpe(proot_bin, ...) is proot's own environment, and
# proot passes it on to the tracee. So one dict serves two masters, and
# a name that means "a setting for the container" to whoever wrote it
# can mean "a setting for the process that has not confined anything
# yet" to the loader.
#
# Two namespaces are that: LD_* is the dynamic loader's (LD_PRELOAD,
# LD_LIBRARY_PATH, LD_AUDIT -- and whatever the next libc adds, which is
# why this is a prefix and not a list) and PROOT_* is proot's own. Both
# are read before proot has confined anything, and the rootfs sits at a
# path the image itself chose the contents of, so an image that gets to
# set one of them gets to run its own code as the invoking user, outside
# any container. That is not a race: `install` then `login` is enough.
#
# The rule this expresses is about *provenance*, not about the name. A
# value the invoking user set for *this invocation* --
# `PROOT_NO_SECCOMP=1 proot-distro login debian`, a `--env` flag -- is
# their own choice about their own command, and they could have set it
# on the command line anyway; those keep working and are applied from
# their own sources. A value that came out of a file describing an
# *image* is not that, whoever wrote the file: an image's config is a
# stranger's outright, and an ENV line is a statement about the image
# rather than about this command, carried in a Dockerfile as often
# copied as written. Three callers read one -- login's env builders, the
# build engine adopting a base image's config, and the build's RUN
# launcher assembling the environment proot is exec'd with -- and all
# three drop it here. The Dockerfile's line still reaches the image
# config it is a statement about; only the host-side exec is refused it.

_HOST_EXEC_PREFIXES = ("LD_", "PROOT_")

# What a guest may ask about where it is running. PD_CONTAINER is the
# container's name, PD_IMAGE the reference the rootfs was materialised
# from and PD_IMAGE_ID that image's config digest; `container` is the
# lowercase marker systemd, podman and lxc set (`container=podman`), for
# scripts that only want to know they are inside *something*. A
# container session answers all four; a build's RUN step has no
# container and answers the other three for the stage's base image. The
# PD_ prefix is the program's own (PD_DOCKER_AUTH, PD_PROOT_BIN);
# PROOT_* was not an option, since the rule above treats that whole
# namespace as host-side and nothing under it may reach the exec.
IDENTITY_ENV_KEYS = ("PD_CONTAINER", "PD_IMAGE", "PD_IMAGE_ID", "container")
CONTAINER_MARKER = "proot-distro"

# The longest `NAME=value` string execve(2) accepts: MAX_ARG_STRLEN, 32
# pages, on the 4 KiB pages every host this runs on has at least. One
# byte is left for the terminating NUL. The kernel answers a longer one
# with E2BIG, which is a failure of the *whole* exec -- so a single
# entry an image or a guest chose the length of used to decide whether
# the session started at all.
MAX_ENV_STRING_BYTES = 32 * 4096


def is_host_exec_var(key: str) -> bool:
    """True when *key* changes what the host-side proot exec itself does.

    Callers use it to refuse a value that came out of a file describing
    an image -- its config, or a Dockerfile's ENV line. Nothing here
    filters the user's own environment or their `--env` flags: see the
    note at the top of this module for why the two are not the same
    question.
    """
    return key.startswith(_HOST_EXEC_PREFIXES)


def is_exportable(key: str, value: str) -> bool:
    """Whether `key=value` can be one string of an exec's environment.

    Three things disqualify one, and each used to end the command in a
    traceback rather than a message, since neither os.execvpe() nor
    subprocess.Popen() is guarded against them anywhere: a NUL in
    either half (ValueError, "embedded null byte"); a code point the
    filesystem encoding cannot represent, which JSON's "\\ud800" is
    (UnicodeEncodeError); and a string past MAX_ENV_STRING_BYTES, which
    the kernel refuses with E2BIG. All three come out of files a
    stranger writes -- an image's config, and on Termux the container's
    own manifest.json, which sits under the bound $TERMUX_PREFIX -- so
    what such an entry decides is whether it is exported, never whether
    the session or the step runs.
    """
    if "\x00" in key or "\x00" in value:
        return False
    try:
        encoded = os.fsencode(key) + b"=" + os.fsencode(value)
    except UnicodeEncodeError:
        return False
    return len(encoded) < MAX_ENV_STRING_BYTES


__all__ = (
    "CONTAINER_MARKER", "IDENTITY_ENV_KEYS", "MAX_ENV_STRING_BYTES",
    "is_exportable", "is_host_exec_var",
)
