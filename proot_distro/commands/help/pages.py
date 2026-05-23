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

# Architecture: All help-page content as plain Python data. Each entry
# in HELP_PAGES is a dict consumed by render.render_page. Termux-only
# options are gated with `*([...] if IS_TERMUX else [])` so the help
# stays in sync with what argparse actually accepts on the current host.

from proot_distro.constants import (
    IS_TERMUX, PROGRAM_NAME, CANONICAL_PROGRAM_NAME, TERMUX_APP_PACKAGE,
)


HELP_PAGES = {
    "build": {
        "usage": "build [OPTIONS] [PATH]",
        "summary": (
            "Build an OCI/Docker-compatible image from a Dockerfile."
            "\n\n"
            "PATH is the build context directory containing the "
            "Dockerfile (default: '.'). All COPY/ADD source paths "
            "resolve relative to it. A '.dockerignore' file in the "
            "context excludes patterns from COPY/ADD."
            "\n\n"
            "By default the image is stored in the local manifest "
            "cache under the tag given by --tag (default: the "
            "basename of PATH plus ':latest'). Once stored, "
            f"'{PROGRAM_NAME} install <tag>' resolves the tag against "
            "the cache first and installs entirely offline."
            "\n\n"
            "Use --output FILE to additionally write a standalone "
            "OCI image-layout tarball that 'docker load' or "
            f"'{PROGRAM_NAME} install FILE' also understands."
            "\n\n"
            "Use --install-as NAME to turn the freshly built image "
            "into a container in one step."
        ),
        "options": [
            ("-h, --help", "Show this help."),
            ("-f, --file [PATH]",
             "Use a Dockerfile at PATH instead of <PATH>/Dockerfile. "
             "Pass '-' to read the Dockerfile from standard input."),
            ("-t, --tag [REF]",
             "Image reference to assign. Repeatable. Defaults to "
             "'<basename(PATH)>:latest'."),
            ("--build-arg [K=V]",
             "Set a build-time ARG. Only ARGs declared in the "
             "Dockerfile are honoured. Repeatable."),
            ("-a, --architecture [ARCH]",
             "Target CPU architecture (default: host architecture). "
             f"Accepts {PROGRAM_NAME} names (aarch64, arm, i686, "
             "riscv64, x86_64) or Docker platform strings "
             "(linux/arm64, linux/amd64, ...)."),
            ("--target [STAGE]",
             "Stop after the named stage of a multi-stage build."),
            ("--emulator [PATH]",
             "Override the QEMU user-mode binary used for "
             "cross-architecture builds."),
            ("-o, --output [FILE]",
             "Write the built image as an OCI tarball to FILE. "
             "Compression is inferred from the extension "
             "(.oci.tar, .oci.tar.gz, .oci.tar.xz). Repeatable."),
            ("--install-as [NAME]",
             "Install the built image as a container named NAME "
             "after the build completes."),
            ("--no-cache",
             "Disable build-step caching. Each instruction is "
             "executed fresh."),
            ("-v, --verbose",
             "Echo each instruction and stream RUN output to the "
             "terminal."),
            ("-q, --quiet",
             "Suppress non-error output. Mutually exclusive "
             "with --verbose."),
        ],
        "examples": [
            f"{PROGRAM_NAME} build -t myapp:1.0 .",
            f"{PROGRAM_NAME} build -t myapp:1.0 --output myapp.oci.tar.gz .",
            f"{PROGRAM_NAME} build -t myapp --install-as myapp .",
            f"{PROGRAM_NAME} build -f Dockerfile.arm "
                f"--architecture aarch64 .",
        ],
        "footer": [
            {
                "title": "PROOT REQUIREMENT",
                "intro": (
                    "If the Dockerfile contains any RUN (or "
                    "ONBUILD RUN) instruction, proot must be "
                    "installed on the host because RUN executes the "
                    "given command against the in-progress rootfs "
                    "under proot. Dockerfiles composed only of FROM, "
                    "COPY, ADD, ENV, ARG, LABEL, USER, WORKDIR, "
                    "CMD, ENTRYPOINT, EXPOSE, VOLUME, STOPSIGNAL, "
                    "HEALTHCHECK, SHELL, MAINTAINER, and "
                    "ONBUILD<non-RUN> build in pure-Python mode and "
                    "do not require proot."
                ),
            },
            {
                "title": "AFTER BUILD",
                "intro": (
                    "Without --output and --install-as, the image is "
                    "stored only in the local cache. "
                    f"'{PROGRAM_NAME} install <tag>' resolves the "
                    "tag against the cache first; install proceeds "
                    "without network access when the manifest and "
                    "all layers are cached."
                ),
            },
            {
                "title": "LIMITATIONS",
                "intro": (
                    "RUN steps run under proot, not a real container "
                    "runtime. No PID, network or IPC isolation, no "
                    "cgroups, no seccomp profiles. BuildKit-only "
                    "features (RUN --mount, --network, --security; "
                    "COPY --link, --parents) are rejected with an "
                    "error. Multi-platform manifest lists are not "
                    "produced — build once per architecture."
                ),
            },
        ],
    },

    "push": {
        "usage": "push [OPTIONS] IMAGE",
        "summary": (
            "Push a locally built image to a Docker/OCI registry. The "
            "image must have been produced by '"
            f"{PROGRAM_NAME} build -t IMAGE' first; the manifest and "
            "blobs are read straight from the local cache."
            "\n\n"
            "IMAGE is the same reference passed to 'build -t', for "
            "example 'myuser/myapp:1.0' (Docker Hub) or "
            "'ghcr.io/myorg/myapp:1.0' (custom registry). When no tag "
            "component is present, ':latest' is appended."
            "\n\n"
            "By default the architecture matches the host. Use "
            "--architecture to push an image built for a different "
            "target arch (the manifest cache is keyed by IMAGE+arch)."
            "\n\n"
            "Layers and the image config blob that are already present "
            "on the registry are detected via HEAD requests and "
            "skipped, so re-pushing an unchanged image transfers only "
            "the small manifest."
            "\n\n"
            "Private repositories require authentication. Set "
            "PD_DOCKER_AUTH=\"user:password\" (or "
            "\"user:personal-access-token\") before running push. "
            "Self-hosted registries that allow anonymous push do not "
            "need PD_DOCKER_AUTH set."
        ),
        "options": [
            ("-h, --help", "Show this help."),
            ("-a, --architecture [ARCH]",
             "Push the manifest built for the given architecture. "
             f"Accepts {PROGRAM_NAME} names (aarch64, arm, i686, "
             "riscv64, x86_64) or Docker platform strings "
             "(linux/arm64, linux/amd64, ...). Default: host "
             "architecture."),
            ("-q, --quiet", "Suppress non-error output."),
        ],
        "examples": [
            f"{PROGRAM_NAME} push myuser/myapp:1.0",
            f"{PROGRAM_NAME} push ghcr.io/myorg/myapp:1.0",
            f"{PROGRAM_NAME} push --architecture aarch64 myuser/myapp:1.0",
        ],
        "footer": [
            {
                "title": "AUTHENTICATION",
                "intro": (
                    "Set PD_DOCKER_AUTH in 'username:password' format "
                    "before running push. The colon is mandatory; "
                    "bare tokens without a username cannot be used "
                    "because registry auth requires a token exchange "
                    "with Basic credentials. For GitHub Container "
                    "Registry, use a personal access token with the "
                    "'write:packages' scope as the password."
                ),
                "examples": [
                    "export PD_DOCKER_AUTH=user:password",
                    f"{PROGRAM_NAME} push ghcr.io/myorg/myapp:1.0",
                ],
            },
            {
                "title": "NOTES",
                "intro": (
                    "Multi-architecture manifest lists are not "
                    "produced. To publish a multi-arch image, build "
                    "and push each architecture under the same tag — "
                    "the registry overwrites the tag with the "
                    "most-recently pushed manifest. Producing a "
                    "manifest index that points at multiple "
                    "single-arch manifests is out of scope."
                ),
            },
        ],
    },

    "backup": {
        "usage": "backup [OPTIONS] CONTAINER",
        "aliases": ("bak", "bkp"),
        "summary": (
            "Back up a specified container into a TAR archive. "
            "Compression is determined by the output file extension or "
            "by the --compress option. Output to stdout is "
            "uncompressed by default."
        ),
        "options": [
            ("-h, --help", "Show this help."),
            ("-c, --compress [TYPE]",
             "Force a specific compression algorithm, overriding the "
             "file extension. Supported values: gzip, bzip2, xz, none."),
            ("-o, --output [FILE]",
             "Write the archive to FILE instead of stdout. When "
             "--compress is not given, compression is inferred from "
             "the file extension like tar.gz or txz."),
            ("-v, --verbose",
             "Log each file name as it is added to the archive."),
            ("-q, --quiet",
             "Suppress non-error output. Mutually exclusive "
             "with --verbose."),
        ],
        "examples": [
            f"{PROGRAM_NAME} backup ubuntu --output ~/ubuntu.tar.xz",
        ],
    },

    "clear-cache": {
        "usage": "clear-cache",
        "aliases": ("clear", "cl"),
        "summary": (
            "Remove all files from downloads cache (e.g. Docker image "
            "layers)."
        ),
        "options": [
            ("-h, --help", "Show this help."),
            ("-v, --verbose", "Log each removed file."),
            ("-q, --quiet",
             "Suppress non-error output. Mutually exclusive "
             "with --verbose."),
        ],
    },

    "copy": {
        "usage": "copy [OPTIONS] [DIST:]SRC [DIST:]DEST",
        "aliases": ("cp",),
        "summary": (
            "Copy files between the host filesystem and a proot "
            "container. Both source and destination may be a local "
            "path or a 'container:path' reference."
        ),
        "options": [
            ("-h, --help", "Show this help."),
            ("-m, --move",
             "Delete source file after a successful copy."),
            ("-r, --recursive", "Recursive mode for copying directories."),
            ("-v, --verbose", "Log each copied file."),
            ("-q, --quiet",
             "Suppress non-error output. Mutually exclusive "
             "with --verbose."),
        ],
        "examples": [
            f"{PROGRAM_NAME} copy ./file.txt ubuntu:/root/file.txt",
        ],
        "footer": [
            {
                "title": "NOTES",
                "intro": (
                    "Directories '.' or '..' are only accepted as "
                    "source, not as destination. Glob patterns are "
                    "not supported."
                ),
            },
        ],
    },

    "install": {
        "usage": "install [OPTIONS] (IMAGE:TAG or URL or FILE)",
        "aliases": ("add", "i", "in", "ins"),
        "summary": (
            "Create a proot container from a given source: Docker image, "
            "OCI image archive, rootfs tarball or a web URL providing "
            "either of supported archive file formats."
            "\n\n"
            "Installation from Docker image require specifying a reference, "
            "for example 'ubuntu:24.04'. Official images can be specified by "
            "name alone ('ubuntu'), while user images require the "
            "'user/image' form. If no tag (version) specified, the 'latest' "
            "will be used instead."
            "\n\n"
            "By default Docker images will be pulled from Docker Hub. Custom "
            "registry needs to be specified as part of image reference. "
            "Example: 'ghcr.io/foo/bar:tag'."
            "\n\n"
            "Layers are cached locally and reused on subsequent "
            "installs of the same image."
            "\n\n"
            "Container name is being determined from name of Docker image "
            "or rootfs archive file. To be able install multiple instances "
            "of same distribution, you need to override name using a command "
            "line option."
            "\n\n"
            "It is possible to install distribution with architecture "
            "that differs from your host CPU. In such cases you will need "
            "a QEMU user mode emulator to be able run it."
            "\n\n"
            "Private images require authentication. Set the environment "
            "variable PD_DOCKER_AUTH=\"user:password\" before running "
            "the install command. Some registries use a personal access "
            "token instead of password."
        ),
        "options": [
            ("-h, --help", "Show this help."),
            ("-n, --name [NAME]",
             "Set a custom name for the container. Must start with "
             "alphanumeric character and then may contain only latin "
             "letters, numbers and special symbols dot, minus, underscore. "
             "Default equals to image name without tag and registry prefix."),
            ("-a, --architecture [ARCH]",
             "Override the target CPU architecture. Accepts native "
             "names (aarch64, arm, i686, riscv64, x86_64) or Docker "
             "platform strings (linux/arm64, linux/amd64, linux/arm/v7, "
             "linux/386, linux/riscv64)."),
            ("-q, --quiet", "Suppress non-error output."),
        ],
        "examples": [
            f"{PROGRAM_NAME} install ubuntu:24.04",
            f"{PROGRAM_NAME} install -a x86_64 debian",
            f"{PROGRAM_NAME} install -n dist https://example.com/rootfs.tar",
            f"{PROGRAM_NAME} install -n dist ~/rootfs.tgz"
        ],
    },

    "list": {
        "usage": "list [OPTIONS]",
        "aliases": ("li", "ls"),
        "summary": "List all installed proot containers.",
        "options": [
            ("-h, --help", "Show this help."),
            ("-q, --quiet", "Print only container names, one per line."),
        ],
    },

    "login": {
        "usage": "login [OPTIONS] CONTAINER [-- COMMAND]",
        "aliases": ("sh",),
        "summary": (
            "Start interactive shell configured for a given account "
            "configured in /etc/passwd. Alternatively user can specify "
            "a custom command to use instead of default shell after "
            "command line separator ('--')."
            + (
                "\n\n"
                "By default container is not isolated from the host file"
                "system. It is highly discouraged to run destructive commands "
                "unless isolated mode enabled."
                if IS_TERMUX else ""
            )
        ),
        "options": [
            ("-h, --help", "Show this help."),
            ("-u, --user [USER]",
             "User identity to switch to instead of root. Accepted forms: "
             "'name' (username from /etc/passwd), "
             "'name:group' (username and group name from /etc/passwd and /etc/group), "
             "'uid' (numeric UID), "
             "'uid:gid' (numeric UID and GID)."),
            ("-P, --redirect-ports",
             "Replace privileged port bindings with higher port numbers "
              "(22 -> 2022, 80 -> 2080, etc). Port shift offset is "
              "hardcoded into proot executable itself and can't be "
              f"configured through {PROGRAM_NAME}."),
            *([("--isolated",
                "Enable Isolated Mode. No host file system bindings created "
                "unless using QEMU user mode emulation or user manually "
                "requested specific directories to be bound.")] if IS_TERMUX else []),
            *([("--minimal",
                "Enable Isolated Mode with bare minimum proot configuration. "
                "Only /dev, /proc and /sys are bound. All proot extensions "
                "except link2symlink are disabled. No /proc system data "
                "workarounds, no kernel release override. Specific features "
                "may only be enabled through command line options. Could show "
                "higher performance than in other modes.")] if IS_TERMUX else []),
            ("--shared-home",
             "Bind host home directory into the container."
             + (" Takes priority over Isolated Mode."
                " Already included in default mode." if IS_TERMUX else "")),
            ("--shared-tmp",
             "Bind host tmp directory to /tmp."
             + (" Takes priority over Isolated Mode."
                " Already included in default mode." if IS_TERMUX else "")),
            ("--shared-x11",
             "Bind host X11 socket directory to /tmp/.X11-unix."
             + (" Takes priority over Isolated Mode."
                " Inherited by --shared-tmp."
                " Already included in default mode." if IS_TERMUX else "")),
            ("-b, --bind [SRC:DEST]",
             "Custom filesystem binding. Can be specified multiple times."
             + (" Takes priority over Isolated Mode." if IS_TERMUX else "")),
            *([("--no-link2symlink",
                "Disable hardlink emulation by proot. Recommended only for "
                "devices with SELinux in permissive mode.")] if IS_TERMUX else []),
            *([("--no-sysvipc",
                "Disable System V IPC emulation by proot. Recommended only "
                "for devices where kernel has this feature enabled and "
                "SELinux set to permissive mode.")] if IS_TERMUX else []),
            *([("--no-kill-on-exit",
                "Hang indefinitely until all session processes exit.")] if IS_TERMUX else []),
            ("--emulator [FILE]",
             "Override the QEMU emulator binary for cross-arch "
             "execution. Only QEMU user mode and Blink emulators are "
             "supported. FILE must be executable."),
            ("--kernel [TEXT]",
             "Customize the kernel release string reported by uname."),
            ("--hostname [TEXT]", "Customize the system hostname."),
            ("-w, --work-dir [PATH]", "Set the initial working directory."),
            ("-e, --env VAR=VALUE",
             "Set an environment variable. Can be specified multiple "
             "times."),
            ("--get-proot-cmd",
             "Print the fully assembled proot command line and exit "
             "without running it. The output is ready to copy and "
             "paste into a terminal."),
        ],
        "footer": [
            *([{
                "title": "HOST BINDINGS",
                "intro": (
                    "Without --isolated, the following host paths "
                    "are bound inside the container:"
                ),
                "bullets": [
                    ("/apex", None),
                    ("/data/dalvik-cache", None),
                    (f"/data/data/{TERMUX_APP_PACKAGE}", None),
                    ("/linkerconfig/ld.config.txt", None),
                    ("/linkerconfig/com.android.art/ld.config.txt", None),
                    ("/mnt/sdcard", None),
                    ("/odm", None),
                    ("/product", None),
                    ("/sdcard", None),
                    ("/storage/emulated/0", None),
                    ("/storage/self/primary", None),
                    ("/system", None),
                    ("/system_ext", None),
                    ("/vendor", None),
                ],
            }] if IS_TERMUX else []),
            {
                "title": "NOTES",
                "intro": (
                    (
                        "If host utilities like termux-api do not work, "
                        "ensure that PATH includes Termux bin directory as "
                        "well as special environment variables such as "
                        "ANDROID_ART_ROOT, ANDROID_DATA, ANDROID_I18N_ROOT, "
                        "ANDROID_ROOT, ANDROID_TZDATA_ROOT, BOOTCLASSPATH, "
                        "EXTERNAL_STORAGE. Valid values can be retrieved "
                        "through Termux shell."
                        "\n\n"
                        "Host storage bindings such as /sdcard may be "
                        "disabled if Termux app does not have necessary "
                        "permissions."
                        "\n\n"
                        if IS_TERMUX else ""
                    ) +
                    f"{CANONICAL_PROGRAM_NAME} comes without any guarantee "
                    "that any user-selected distribution image will work "
                    "properly. Any kind of observed bugs could happen "
                    "either because of proot (third party dependency) "
                    "design flaws or fundamental incompatibility with "
                    "given container runtime. For example is not possible "
                    "to deliver access to restricted interfaces under "
                    "/dev, /proc and /sys required by udev; cgroups "
                    "or Linux namespaces required by bwrap."
                    "\n\n"
                    "Devices with ARMv9 CPUs require QEMU user mode "
                    "emulator to be able execute 32-bit programs because "
                    "this architecture no longer include necessary "
                    "instruction set."
                ),
            },
        ],
    },

    "remove": {
        "usage": "remove [OPTIONS] CONTAINER",
        "aliases": ("rm",),
        "summary": (
            "Permanently delete the specified proot container. "
            "No confirmation is requested, be careful."
        ),
        "options": [
            ("-h, --help", "Show this help."),
            ("-v, --verbose", "Log each deleted file."),
            ("-q, --quiet",
             "Suppress non-error output. Mutually exclusive "
             "with --verbose."),
        ],
    },

    "rename": {
        "usage": "rename OLDNAME NEWNAME",
        "summary": "Rename the installed proot container.",
        "options": [
            ("-h, --help", "Show this help."),
            ("-q, --quiet", "Suppress non-error output."),
        ],
        "footer": [
            {
                "title": "NOTES",
                "intro": (
                    "Renaming updates all proot link2symlink "
                    "entries inside the container, which may take a "
                    "while for large rootfs trees. For data integrity "
                    "reasons user may not terminate process by CTRL-C."
                ),
            },
        ],
    },

    "reset": {
        "usage": "reset CONTAINER",
        "summary": (
            "Rebuild the specified container from scratch using the "
            "stored Docker image manifest. All current data inside "
            "the container will be lost."
            "\n\n"
            "Works only with containers created from Docker images."
        ),
        "options": [
            ("-h, --help", "Show this help."),
            ("-q, --quiet", "Suppress non-error output."),
        ],
    },

    "restore": {
        "usage": "restore [OPTIONS] [BACKUP_FILE]",
        "summary": (
            "Restore container from a backup archive. When backup file "
            "is not specified, archive data is read from stdin."
        ),
        "options": [
            ("-h, --help", "Show this help."),
            ("-v, --verbose", "Log each extracted file."),
            ("-q, --quiet",
             "Suppress non-error output. Mutually exclusive "
             "with --verbose."),
        ],
        "footer": [
            {
                "title": "NOTES",
                "intro": (
                    "Compression is detected automatically from the "
                    "file header. Supported: gzip, bzip2, xz, "
                    "uncompressed tar. Applies to both file and "
                    "stdin input."
                ),
            },
        ],
    },

    "run": {
        "usage": "run [OPTIONS] CONTAINER [-- ARG ...]",
        "summary": (
            "Run the Entrypoint and/or Cmd defined in the "
            "container's Docker image manifest. Arguments given "
            "after '--' are appended to Entrypoint (replacing the "
            "image-defined Cmd). If neither Entrypoint nor Cmd is "
            "defined and no arguments are given, an error is "
            "reported."
            "\n\n"
            "Primarily intended to be used with server images."
        ),
        "options": [
            ("-h, --help", "Show this help."),
            ("-u, --user [USER]",
             "User identity to switch to instead of root. Accepted forms: "
             "'name' (username from /etc/passwd), "
             "'name:group' (username and group name from /etc/passwd and /etc/group), "
             "'uid' (numeric UID), "
             "'uid:gid' (numeric UID and GID)."),
            ("-P, --redirect-ports",
             "Replace privileged port bindings with higher port numbers "
              "(22 -> 2022, 80 -> 2080, etc). Port shift offset is "
              "hardcoded into proot executable itself and can't be "
              f"configured through {PROGRAM_NAME}."),
            *([("--isolated",
                "Enable Isolated Mode. No host file system bindings created "
                "unless using QEMU user mode emulation or user manually "
                "requested specific directories to be bound.")] if IS_TERMUX else []),
            *([("--minimal",
                "Enable Isolated Mode with bare minimum proot configuration. "
                "Only /dev, /proc and /sys are bound. All proot extensions "
                "except link2symlink are disabled. No /proc system data "
                "workarounds, no kernel release override. Specific features "
                "may only be enabled through command line options. Could show "
                "higher performance than in other modes.")] if IS_TERMUX else []),
            ("--shared-home",
             "Bind host home directory into the container."
             + (" Takes priority over Isolated Mode."
                " Already included in default mode." if IS_TERMUX else "")),
            ("--shared-tmp",
             "Bind host tmp directory to /tmp."
             + (" Takes priority over Isolated Mode."
                " Already included in default mode." if IS_TERMUX else "")),
            ("--shared-x11",
             "Bind host X11 socket directory to /tmp/.X11-unix."
             + (" Takes priority over Isolated Mode."
                " Inherited by --shared-tmp."
                " Already included in default mode." if IS_TERMUX else "")),
            ("-b, --bind [SRC:DEST]",
             "Custom filesystem binding. Can be specified multiple times."
             + (" Takes priority over Isolated Mode." if IS_TERMUX else "")),
            *([("--no-link2symlink",
                "Disable hardlink emulation by proot. Recommended only for "
                "devices with SELinux in permissive mode.")] if IS_TERMUX else []),
            *([("--no-sysvipc",
                "Disable System V IPC emulation by proot. Recommended only "
                "for devices where kernel has this feature enabled and "
                "SELinux set to permissive mode.")] if IS_TERMUX else []),
            *([("--no-kill-on-exit",
                "Hang indefinitely until all session processes exit.")] if IS_TERMUX else []),
            ("--emulator [FILE]",
             "Override the QEMU emulator binary for cross-arch "
             "execution. Only QEMU user mode and Blink emulators are "
             "supported. FILE must be executable."),
            ("--kernel [TEXT]",
             "Customize the kernel release string reported by uname."),
            ("--hostname [TEXT]", "Customize the system hostname."),
            ("-w, --work-dir [PATH]", "Set the initial working directory."),
            ("-e, --env VAR=VALUE",
             "Set an environment variable. Can be specified multiple "
             "times."),
            ("--get-proot-cmd",
             "Print the fully assembled proot command line and exit "
             "without running it. The output is ready to copy and "
             "paste into a terminal."),
        ],
        "examples": [
            f"{PROGRAM_NAME} run nextcloud --redirect-ports",
            f"{PROGRAM_NAME} run ubuntu --isolated -- /bin/echo hi",
        ],
        "footer": [
            {
                "title": "NOTES",
                "intro": (
                    f"{CANONICAL_PROGRAM_NAME} comes without any guarantee "
                    "that any user-selected distribution image will work "
                    "properly. Any kind of observed bugs could happen "
                    "either because of proot (third party dependency) "
                    "design flaws or fundamental incompatibility with "
                    "given container runtime. For example is not possible "
                    "to deliver access to restricted interfaces under "
                    "/dev, /proc and /sys required by udev; cgroups "
                    "or Linux namespaces required by bwrap."
                    "\n\n"
                    "Devices with ARMv9 CPUs require QEMU user mode "
                    "emulator to be able execute 32-bit programs because "
                    "this architecture no longer include necessary "
                    "instruction set."
                ),
            },
        ],
    },

    "sync": {
        "usage": "sync [OPTIONS] [DIST:]SRC [DIST:]DEST",
        "summary": (
            "Efficiently synchronize directory between host and container "
            "by copying only modified files and deleting those which "
            "absent in the source. Files compared by size and modification "
            "timestamp, however it is possible to use more strict "
            "verification by checksum."
            "\n\n"
            "Both source and destination may be a local path or a "
            "'container:path' reference."
        ),
        "options": [
            ("-h, --help", "Show this help."),
            ("-c, --checksum",
             "Compare files by size and CRC32 checksum instead of "
             "size and modification time. Slower but with high precision."),
            ("-d, --delete",
             "After syncing, remove destination files and "
             "directories that have no counterpart in the source. "
             "Only effective when source is a directory."),
            ("-v, --verbose", "Log each synced or deleted entry."),
            ("-q, --quiet",
             "Suppress non-error output. Mutually exclusive "
             "with --verbose."),
        ],
        "examples": [
            f"{PROGRAM_NAME} sync ./dotfiles/ ubuntu:/root/",
            f"{PROGRAM_NAME} sync --delete ./app/ ubuntu:/opt/app/"
        ],
    },
}


# Top-level command table for the no-args help screen.
TOP_COMMANDS = [
    ("help", "Show this help."),
    ("install", "Install distribution from OCI image or rootfs archive."),
    ("list", "List created containers."),
    ("login", "Start interactive shell inside a container."),
    ("run", "Run container entrypoint in server or distroless images."),
    ("remove", "Delete a container.", "Destroys data!"),
    ("rename", "Rename a container."),
    ("reset", "Reinstall a container from scratch.", "Destroys data!"),
    ("backup", "Save container as a TAR archive."),
    ("restore", "Restore container from a TAR archive.", "Destroys data!"),
    ("clear-cache", "Delete cached downloads."),
    ("copy", "Copy files from/to container."),
    ("sync", "Sync files from/to container."),
    ("build", "Build an OCI image from a Dockerfile."),
    ("push", "Push a locally built image to a registry."),
]
