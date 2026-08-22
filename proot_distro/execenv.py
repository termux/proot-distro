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

# Architecture: which environment variables belong to the *host side* of
# a proot session rather than to the guest.
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


def is_host_exec_var(key: str) -> bool:
    """True when *key* changes what the host-side proot exec itself does.

    Callers use it to refuse a value that came out of a file describing
    an image -- its config, or a Dockerfile's ENV line. Nothing here
    filters the user's own environment or their `--env` flags: see the
    note at the top of this module for why the two are not the same
    question.
    """
    return key.startswith(_HOST_EXEC_PREFIXES)


__all__ = ("is_host_exec_var",)
