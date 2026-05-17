# PRoot-Distro

PRoot-Distro is a utility for managing rootless Linux containers in
[Termux](https://termux.dev) and on regular Linux hosts. It uses
[proot](https://proot-me.github.io/) to provide a chroot-like
environment without requiring root access on the device.

Containers are created by pulling Docker/OCI images directly from
Docker Hub or any compatible public registry — or by extracting a local
tarball / OCI image archive. The container filesystem is assembled from
the image layers and stored locally, ready to be entered at any time.

---

## Table of contents

1. [Introduction](#introduction)
2. [Installation](#installation)
3. [Quick start](#quick-start)
4. [Commands](#commands-reference)
   * [`install`](#install--install-a-container)
   * [`login`](#login--start-a-shell-inside-a-container)
   * [`run`](#run--run-the-image-defined-entrypoint)
   * [`list`](#list--list-installed-containers)
   * [`remove`](#remove--delete-a-container)
   * [`rename`](#rename--rename-a-container)
   * [`reset`](#reset--reinstall-a-container-from-scratch)
   * [`backup`](#backup--archive-a-container)
   * [`restore`](#restore--restore-a-container-from-a-backup)
   * [`copy`](#copy--copy-files-to-or-from-a-container)
   * [`sync`](#sync--synchronize-files-to-or-from-a-container)
   * [`clear-cache`](#clear-cache--delete-the-download-cache)
5. [How PRoot-Distro works](#how-proot-distro-works)
6. [Storage layout](#storage-layout)
7. [Environment variables](#environment-variables)
8. [Shell completions](#shell-completions)
9. [Limitations](#limitations)
10. [Donate](#donate)

---

## Introduction

PRoot-Distro lets you run a full Linux userland — Ubuntu, Debian,
Alpine, Arch, openSUSE, distroless server images, anything available as
a Docker/OCI image — on top of Termux on an Android device, or on top
of a regular Linux distribution, **without** root, **without** a kernel
module, and **without** a Docker daemon.

Typical use cases:

- Running a desktop-class Linux distribution on a phone or tablet.
- Cross-compiling for a different CPU architecture using QEMU user-mode.
- Spinning up server software (Nginx, Nextcloud, PostgreSQL, etc.) on
  Android by reusing the same OCI images you'd run on a server.
- Trying a distribution non-destructively: install, mess around,
  `proot-distro remove` when done.

The CLI is exposed both as `proot-distro` and the shorter alias `pd`.

![Screenshot image](https://raw.githubusercontent.com/termux/proot-distro/master/screenshot.png)

### Installation

PRoot-Distro is primarily distributed via Termux's `pkg` package
manager and via PyPI. Python 3.9 or newer is required. The only runtime
dependency is `proot` (and, optionally, a `qemu-user-*` package for
cross-architecture containers).

#### On Termux (Android)

Install Termux from
[F-Droid](https://f-droid.org/en/packages/com.termux/) or the
[Termux GitHub Releases](https://github.com/termux/termux-app/releases).
Then, inside Termux:

```sh
pkg install proot-distro
```

This pulls in `proot` automatically as a dependency.

To install the latest published version from PyPI instead:

```sh
pkg install python proot
pip install proot-distro
```

#### On a regular Linux host

```sh
# Install proot via your distro's package manager, e.g. on Debian/Ubuntu:
sudo apt install proot python3-pip

pip install proot-distro          # from PyPI
# or
git clone https://github.com/termux/proot-distro
cd proot-distro
pip install .                     # from a local checkout
pip install -e .                  # editable install for development
```

### First-run check

On startup the tool verifies that `proot` is available. If it isn't:

- On **Termux**, with an interactive terminal, you are prompted to
  install it via `pkg install -y -q proot`.
- Otherwise, an install hint is printed and the program exits.

PRoot-Distro also refuses to run inside another `proot` (nested proot
is not supported by `proot` itself) and prints a yellow warning if
launched as the `root` user.

### Quick start

```sh
# Install Ubuntu 24.04 from Docker Hub
proot-distro install ubuntu:24.04

# Start a shell inside the container
proot-distro login ubuntu

# Same thing, but using the short command alias
pd sh ubuntu

# Run a single command and exit
proot-distro login ubuntu -- /bin/uname -a

# List all installed containers
proot-distro list

# Rebuild from scratch (loses all in-container data)
proot-distro reset ubuntu

# Permanently remove a container
proot-distro remove ubuntu
```

---

## Commands reference

The `pd` short alias works everywhere `proot-distro` does.

Every command supports `--help` (also `-h`, `--usage`), which prints
help text laid out for the current terminal width.

### `install` — Install a container

```
proot-distro install [OPTIONS] (IMAGE or PATH)
Aliases: add, i, in, ins
```

Pull a Docker/OCI image and create a container from it, or extract one
from a local archive file.

**Options:**

| Option | Description |
|---|---|
| `--name NAME` | Set a custom local name for the container. Defaults to the image name without tag/registry, or the archive filename without extension. Must start with a letter or digit; may contain only letters, digits, `_`, `.`, `-`. Empty name is rejected. |
| `--architecture ARCH` | Override the target CPU architecture. Accepts native names (`aarch64`, `arm`, `i686`, `riscv64`, `x86_64`) or Docker platform strings (`linux/arm64`, `linux/amd64`, `linux/arm/v7`, `linux/386`, `linux/riscv64`). Defaults to the host CPU. |

#### From a Docker/OCI registry

`IMAGE` is a standard Docker image reference:

| Form | Example |
|---|---|
| Official image | `ubuntu:24.04` |
| Official, no tag (uses `latest`) | `alpine` |
| User image | `myuser/myimage:tag` |
| Custom registry | `ghcr.io/foo/bar:latest` |

Custom registries are detected by the first path component containing
`.` or `:` (i.e. a hostname). Public images on `ghcr.io`,
`quay.io`, `registry.gitlab.com`, etc. are pulled with an anonymous
Bearer token discovered from each registry's `/v2/` challenge.

Layers are cached in `$DOWNLOAD_CACHE_DIR/layers/` and reused on
subsequent installs. If both the resolved manifest and all of its
layers are already in the cache, installation runs fully offline.

**Examples:**

```sh
proot-distro install ubuntu:24.04
proot-distro install alpine:3.21 --name my-alpine
proot-distro install debian:bookworm --architecture aarch64
proot-distro install ghcr.io/myorg/myimage:latest
proot-distro install nextcloud:32
```

#### From a local archive

`IMAGE` can also be a path to a local archive file. A path is
recognised only when it starts with `/`, `./`, `../`, or `~` — a bare
token like `ubuntu` is always treated as a Docker image, even if a file
by that name happens to exist in the current directory.

Two archive formats are supported, auto-detected by reading the first
500 entries of the archive:

- **Plain rootfs tarball** — a tar archive whose top-level entries form
  a standard Linux filesystem (`bin/`, `etc/`, `usr/`, …). The tool
  scores strip levels 0–4 and picks the one that lands the most
  recognised rootfs directories at the root. Supported compression:
  gzip, bzip2, xz, lzma, or uncompressed. No `manifest.json` is written
  for this format (so `reset` and `run` are not available).
- **OCI image layout** — a tar archive that contains an `oci-layout`
  file at its root (as produced by `docker save`, `skopeo copy
  oci-archive:`, or similar tools). Layers are applied in order with
  full whiteout semantics, layer blobs are cached, and `manifest.json`
  is written so `reset` and `run` work like with a registry-pulled
  image.

The container name is derived from the archive filename by stripping
known extensions (`.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`, `.tbz2`,
`.tar.xz`, `.txz`, `.tar.lzma`, `.tlzma`, `.oci.tar`, `.oci.tar.gz`,
`.oci.tar.xz`) and sanitising the result. Use `--name` to set an
explicit name.

**Examples:**

```sh
# Plain rootfs tarball
proot-distro install ./alpine-rootfs.tar.gz

# OCI image layout saved with docker
docker save myimage:latest -o myimage.oci.tar
proot-distro install ./myimage.oci.tar --name myimage

# Explicit name and architecture override
proot-distro install /tmp/debian-arm.tar.xz --name debian --architecture arm
```

After installation the resolved image tag is shown (e.g.
`Installing 'ubuntu:24.04'`). If the image defines an `Entrypoint`, a
`Run entrypoint: proot-distro run <name>` hint is printed alongside
`Start shell: proot-distro login <name>`.

---

### `login` — Start a shell inside a container

```
proot-distro login [OPTIONS] CONTAINER [-- COMMAND ...]
Aliases: sh
```

Spawn an interactive shell (or a custom command) inside an installed
container. The `--` separator passes a command to run inside the
container's login shell (the shell still wraps it, so quoting and
redirection inside `COMMAND` work as expected).

**Examples:**

```sh
# Interactive shell as root
proot-distro login ubuntu

# Interactive shell as a non-root user
proot-distro login ubuntu --user myuser

# Run a single command
proot-distro login ubuntu -- /bin/ls /etc

# Run a shell command string
proot-distro login ubuntu -- bash -c "echo hello"

# Use the short alias
pd login ubuntu

# Inspect the full proot command line without running it
proot-distro login ubuntu --get-proot-cmd
```

**Options always available:**

| Option | Description |
|---|---|
| `--user USER` | Log in as USER (default: `root`). For containers without `/etc/passwd`, only a numeric UID or the literal `root` is accepted. |
| `--redirect-ports` | Redirect privileged ports 1–1023 to higher numbers (`22 → 2022`, `80 → 2080`, …). The offset is hardcoded inside `proot`. |
| `--shared-home` | Bind the host home directory into the container (mounted at `/root` for the root user, at the user's home otherwise; for termux-type containers it goes to `/data/data/com.termux/files/home`). |
| `--termux-home` | Synonym for `--shared-home` (mutually exclusive with it). |
| `--shared-tmp` | Bind the host `$PREFIX/tmp` to `/tmp` inside the container (Termux only; skipped for termux-type containers). |
| `--shared-x11` | Bind `$PREFIX/tmp/.X11-unix` to `/tmp/.X11-unix` (Termux only; skipped for termux-type containers). |
| `--bind SRC[:DST]` | Bind a custom path (repeatable). `SRC` is resolved via `os.path.abspath`. `DST`, when given, must be an absolute path (relative destinations are rejected). Overlap with an existing destination emits a warning but the bind is still added. |
| `--emulator PATH` | Override the QEMU emulator binary for cross-arch containers. PATH must be executable. Only QEMU user-mode and Blink are known to work. |
| `--kernel STRING` | Customize the kernel release string reported by `uname -r`. Default: `6.17.0-PRoot-Distro`. |
| `--hostname STRING` | Customize the hostname inside the container. Default: `localhost`. |
| `--work-dir PATH` | Set the initial working directory. Default: the user's home directory. |
| `--env VAR=VALUE` | Set an environment variable in the guest (repeatable). Wins over image-defined `Env` and the baseline defaults. |
| `--get-proot-cmd` | Print the fully assembled `env` + `proot` command line (escaped, with line continuations) and exit without running. |

**Options available only on Termux (Android):**

| Option | Description |
|---|---|
| `--isolated` | Skip non-essential host bindings (Android system dirs, Termux `$HOME`, `/sdcard`, Termux app paths). Keeps the link2symlink/sysvipc/kill-on-exit proot extensions and the kernel-release override. Mutually exclusive with `--minimal`. |
| `--minimal` | Bare-minimum proot: only `/dev`, `/proc`, `/sys` are bound, `--sysvipc` is disabled, no fake `/proc` stubs, no `--kernel-release`. Guest env contains only your `--env` entries plus `TERM`/`COLORTERM`. Mutually exclusive with `--isolated`. |
| `--no-link2symlink` | Disable proot's hard-link emulation. Only safe on devices with SELinux in permissive mode. |
| `--no-sysvipc` | Disable System V IPC emulation. Only useful on kernels that already implement it. |
| `--no-kill-on-exit` | Wait for all child processes before exiting the session. |

#### Host bindings (Termux, default mode)

Without `--isolated` or `--minimal`, the following host paths are
mounted inside the container when present and readable:

```
/apex
/data/app
/data/dalvik-cache
/data/data/<termux-app-package>
/linkerconfig/com.android.art/ld.config.txt
/linkerconfig/ld.config.txt
/odm
/product
/sdcard
/storage/emulated/0
/storage/self/primary
/system
/system_ext
/vendor
```

Plus, for normal-type containers, the Termux `$PREFIX` is bound at its
original path inside the guest so Termux utilities (`termux-api`,
`pkg`, etc.) are reachable.

#### Guest environment

The host's environment is **not** carried into the guest. PRoot-Distro
builds a clean environment dict and passes it to `os.execvpe("proot",
…)`. Precedence (later entries win):

1. Baseline: `PATH` (from `DEFAULT_PATH_ENV`), `MOZ_FAKE_NO_SANDBOX=1`,
   `PULSE_SERVER=127.0.0.1` (Termux only).
2. Image-defined `Env` from `manifest.json`. Cannot override Android
   system vars, `MOZ_FAKE_NO_SANDBOX`, `PULSE_SERVER`, `TERM`, or
   `COLORTERM`.
3. Android system vars (`ANDROID_*`, `BOOTCLASSPATH`, etc.), Termux
   only, when not `--isolated` and not `--minimal`.
4. Your `--env VAR=VALUE` entries.
5. `HOME`, `USER`, `TERM` (defaulting to `xterm-256color`),
   `COLORTERM` (only when set on the host).

After the precedence pass, `$PREFIX/bin` is appended to `PATH` so
Termux host tools stay reachable inside the guest. A snippet at
`/etc/profile.d/termux-profile.sh` re-applies every login-time
environment variable (PATH, image Env, Android system vars, `--env`
flags) after the distro's `/etc/profile` resets the environment on
login — without it, running `su - someuser` inside the container
would silently drop those values. Per-session vars (`HOME`, `USER`,
`TERM`, `COLORTERM`) and proot-internal vars are excluded.

In `--minimal` mode steps 1–3 and the `PATH` post-processing are
skipped; only your `--env` entries plus `TERM`/`COLORTERM` are
exported.

#### Legacy migration

If a container was created by an older version of PRoot-Distro and its
rootfs is still at the legacy path
(`$RUNTIME_DIR/installed-rootfs/<name>`), `login` automatically migrates
it to the new layout (`$RUNTIME_DIR/containers/<name>/rootfs`) on first
use, including rewriting any internal proot link2symlink (l2s)
symlinks. This may take a while on large containers.

---

### `run` — Run the image-defined entrypoint

```
proot-distro run [OPTIONS] CONTAINER [-- ARG ...]
```

Run the `Entrypoint` and/or `Cmd` defined in the container's Docker
image manifest. This is equivalent to `docker run`: the container
starts, executes the image-defined command, and exits when it
finishes.

`run` requires that the container was installed from an OCI image
(plain tarball installs have no `manifest.json` and therefore no
recorded Entrypoint/Cmd).

**Entrypoint and Cmd resolution:**

| Image | Args after `--` | Inner command |
|---|---|---|
| `Entrypoint` + `Cmd` | _(none)_ | `Entrypoint + Cmd` |
| `Entrypoint` + `Cmd` | `ARGS` | `Entrypoint + ARGS` (Cmd replaced) |
| Only `Cmd` | _(none)_ | `Cmd` |
| Only `Cmd` | `ARGS` | `ARGS` (Cmd replaced) |
| Only `Entrypoint` | _(none)_ | `Entrypoint` |
| Only `Entrypoint` | `ARGS` | `Entrypoint + ARGS` |
| Neither | _(none)_ | Error |
| Neither | `ARGS` | `ARGS` |

When `--work-dir` is not given, `run` uses the image's `WorkingDir`
(falling back to `/` if it is empty).

`run` accepts the same options as `login` (`--user`, `--bind`,
`--isolated`, `--minimal`, `--env`, `--shared-tmp`, `--shared-x11`,
`--emulator`, `--get-proot-cmd`, etc.). See
`proot-distro login --help`.

**Examples:**

```sh
# Run the image's default entrypoint
proot-distro run hello-world

# Run with port redirection (so 80 → 2080)
proot-distro run nextcloud --redirect-ports

# Pass arguments to the entrypoint (overrides image Cmd)
proot-distro run ubuntu -- /bin/echo hi

# Print the proot command line without executing
proot-distro run nextcloud --get-proot-cmd
```

---

### `list` — List installed containers

```
proot-distro list
Aliases: li, ls
```

Show all installed containers (subdirectories of `containers/` that
have a `rootfs/`). When none are installed, an install suggestion is
printed.

---

### `remove` — Delete a container

```
proot-distro remove [OPTIONS] CONTAINER
Aliases: rm
```

Permanently delete the specified container and all its data. **This
cannot be undone and is not confirmed.** Permissions of chmod-000'd
files are fixed on the fly so the rootfs can always be cleared.

| Option | Description |
|---|---|
| `-v`, `--verbose` | Log each deleted file. |

---

### `rename` — Rename a container

```
proot-distro rename OLDNAME NEWNAME
```

Rename a container from `OLDNAME` to `NEWNAME`. Also rewrites every
proot link2symlink (l2s) symlink whose target is still pointing into
the old rootfs path, so hardlinks remain valid. This may take a while
on large containers.

For data-integrity reasons, **CTRL-C and CTRL-\\ are intercepted**
during the l2s rewrite. The signal is replaced with a one-line warning;
the rewrite continues until done.

---

### `reset` — Reinstall a container from scratch

```
proot-distro reset CONTAINER
```

Remove the container rootfs and reinstall it from the Docker image
recorded at install time. **All data inside the container is lost.**

The image reference and target architecture are read from
`containers/<name>/manifest.json`. If that file is missing, the command
exits with an error — reset is supported for OCI image installs only
(plain rootfs tarballs cannot be re-pulled).

---

### `backup` — Archive a container

```
proot-distro backup [OPTIONS] CONTAINER
Aliases: bak, bkp
```

Create a TAR archive of the container. The archive contains
`<name>/manifest.json` (when present) and `<name>/rootfs/`.

**Options:**

| Option | Description |
|---|---|
| `--output FILE` | Write to FILE instead of stdout. Refuses to overwrite an existing file. |
| `--compress TYPE` | Force compression: `gzip`, `bzip2`, `xz`, or `none`. Overrides extension-based detection. |
| `-v`, `--verbose` | Log each archived file. |

When `--output` is given, the compression algorithm is inferred from
the file extension (`.tar.gz`, `.tgz`, `.tar.bz2`, `.tbz2`, `.tar.xz`,
`.txz`, `.tar.lzma`, `.tlzma`, or plain `.tar`) unless `--compress`
overrides it. Unsupported extensions (`.tar.zst`, `.tzst`, `.tar.lz4`,
`.tar.lz`) are rejected.

Without `--output`, the archive is written to stdout, uncompressed by
default. Stdout cannot be a TTY (you must redirect or pipe).

File ownership in the archive is **zeroed** (uid=gid=0, no
uname/gname). Block devices, character devices, FIFOs, and sockets are
silently skipped. Symlinks to directories are stored as single entries.
Before archiving, the rootfs permissions are fixed up so chmod-000'd
subtrees become at least readable by the owner.

`backup` is **TTY-safe** when piping into an interactive consumer
(e.g. `gpg -c` with a pinentry prompt): all progress output is
suppressed while the downstream process holds the TTY in
non-canonical/no-echo mode, then resumes once the TTY returns to
normal.

**Examples:**

```sh
# Create a compressed backup
proot-distro backup ubuntu --output ubuntu.tar.xz

# Pipe to another command
proot-distro backup ubuntu | gzip > ubuntu.tar.gz

# Encrypt with GPG (pinentry-safe)
proot-distro backup ubuntu | gpg -c > ubuntu.tar.gpg

# Verbose listing while archiving
proot-distro backup ubuntu --output ubuntu.tar --verbose
```

---

### `restore` — Restore a container from a backup

```
proot-distro restore [OPTIONS] [BACKUP_FILE]
```

Restore a container from a TAR archive. When `BACKUP_FILE` is omitted,
archive data is read from stdin.

Compression is detected automatically — `tarfile`'s `r|*` auto-detect
handles file input; for stdin, the first 6 magic bytes are peeked to
identify gzip / bzip2 / xz / lzma streams.

**Options:**

| Option | Description |
|---|---|
| `-v`, `--verbose` | Log each extracted file. |

**Archive format requirements:**

- Files must be stored under a subdirectory named after the container
  (`<name>/manifest.json`, `<name>/rootfs/...`). Bare-root archives
  (e.g. `tar` of a rootfs without any leading directory) are rejected.
- If `manifest.json` is not present in the archive, the container is
  restored without it (login still works, but `reset` and `run` will
  not).
- Legacy archives (`installed-rootfs/<name>/...`) are accepted and
  automatically re-rooted to the new layout.

The existing rootfs for each container in the archive is cleared
recursively on the first entry seen for that container. Hard links are
resolved using the archive's own paths and recreated with `os.link`.

`restore` is **TTY-safe** when reading from a pipe that involves an
interactive producer (`gpg -d archive.gpg | proot-distro restore`):
progress output stays silent while the upstream pinentry holds the
TTY, then resumes once the TTY returns to normal.

**Examples:**

```sh
# Restore from a file
proot-distro restore ubuntu.tar.xz

# Restore from stdin
cat ubuntu.tar.xz | proot-distro restore

# Decrypt + restore in one pipeline
gpg -d ubuntu.tar.gpg | proot-distro restore
```

---

### `copy` — Copy files to or from a container

```
proot-distro copy [OPTIONS] [CONTAINER:]SRC [CONTAINER:]DEST
Aliases: cp
```

Copy files between the host filesystem and a container rootfs, or
between two containers. Paths inside a container are prefixed with the
container name and a colon: `ubuntu:/etc/resolv.conf`.

| Option | Description |
|---|---|
| `-r`, `--recursive` | Copy directories recursively (preserves symlinks). |
| `-m`, `--move` | Move instead of copying (deletes source after success). |
| `-v`, `--verbose` | Log each copied file. |

Directories `.` and `..` are accepted only as source, not as
destination. Glob patterns are not supported (rely on the shell).

**Examples:**

```sh
# Copy a local file into a container
proot-distro copy ./file.txt ubuntu:/root/file.txt

# Copy a file out of a container
proot-distro copy ubuntu:/etc/resolv.conf ./resolv.conf.bak

# Copy between two containers
proot-distro copy arch:/etc/pacman.conf ubuntu:/tmp/pacman.conf

# Recursive copy of a local directory
proot-distro copy --recursive ./myapp ubuntu:/opt/myapp
```

---

### `sync` — Synchronize files to or from a container

```
proot-distro sync [OPTIONS] [CONTAINER:]SRC [CONTAINER:]DEST
```

Synchronize SRC to DEST, copying only files that differ. Both paths
may be plain host paths or `container:path` references. Always
recursive — no flag needed.

**Comparison method:**

| Mode | What is compared |
|---|---|
| Default | File size and integer modification time |
| `--checksum` | File size and CRC32 checksum |

**Behavior:**

- Symlinks are copied as-is (target not followed).
- Hard links become independent file copies (no inode tracking).
- Block/char devices, FIFOs, and sockets are silently skipped.
- File ownership is never changed (`chown` is not called).
- Access modes and modification timestamps are preserved.
- Regular files are written atomically (`.~pd_sync` temp file →
  `os.replace`) so a partial copy never leaves a corrupt destination.
- If a source file is not readable, a warning is printed and it is
  skipped.
- If the destination lacks write permission, `sync` first attempts to
  `chmod` it. If that also fails, the command exits with an error.

| Option | Description |
|---|---|
| `--checksum` | Compare by size + CRC32 instead of size + mtime (slower, more strict). |
| `--delete` | Remove destination files and directories that have no counterpart in the source. Applied after the sync pass; only effective when source is a directory. |
| `-v`, `--verbose` | Log each synced or deleted entry. Suppresses the progress bar. |

**Examples:**

```sh
# Sync a local directory into a container
proot-distro sync ./app ubuntu:/opt/app

# Sync a directory out of a container
proot-distro sync ubuntu:/etc ./backup/etc

# Use checksum-based comparison
proot-distro sync --checksum ./data ubuntu:/data

# Make destination match source exactly (delete extras)
proot-distro sync --delete ./app ubuntu:/opt/app
```

---

### `clear-cache` — Delete the download cache

```
proot-distro clear-cache
Aliases: clear, cl
```

Remove all cached Docker image layers and resolved manifests from
`$DOWNLOAD_CACHE_DIR`. Disk space freed is reported after the operation
in human-readable units.

| Option | Description |
|---|---|
| `-v`, `--verbose` | Log each deleted file. |

After `clear-cache`, the next `install` (or `reset`) of an image
requires network access again, and layers must be re-downloaded and
re-verified.

---

## How PRoot-Distro works

PRoot-Distro is a thin orchestration layer around two primary building
blocks:

### 1. OCI registry client

The `install` command speaks the standard OCI Distribution protocol
directly over `urllib`:

- Public images on **Docker Hub** require no flags
  (e.g. `ubuntu:24.04`).
- Public images on **other registries** are addressed by full reference
  (e.g. `ghcr.io/myorg/myimage:tag`). PRoot-Distro probes
  `https://<registry>/v2/`, follows the OCI Bearer auth challenge, and
  pulls an anonymous token.
- Manifest lists are resolved to the platform that matches your CPU
  (or the `--architecture` override).
- Each layer blob is downloaded with its **SHA-256 verified** before
  being promoted into the cache. Cross-host redirects (Docker Hub →
  CDN) have their `Authorization` header stripped to satisfy CDNs that
  reject bearer tokens.
- Layer blobs and the resolved single-arch manifest are cached locally.
  Subsequent installs of the same image are fully offline.

Layers are applied in order on top of an empty rootfs directory, with
full OCI whiteout semantics (`.wh..wh..opq` and `.wh.<name>` markers).
Hard links are materialised as copies for self-containment. Block
devices, character devices, and FIFOs are skipped.

After all layers are applied, PRoot-Distro adds three small fixups when
the image has `/etc/`:

- `/etc/resolv.conf` is replaced with Google DNS (8.8.8.8 / 8.8.4.4).
- `/etc/hosts` is populated with a minimal localhost mapping.
- The host's Termux/Android user is registered as `aid_<name>` in
  `/etc/passwd`, `/etc/shadow`, `/etc/group`, and `/etc/gshadow` so
  Android's UID-based permissions work inside the container.

The full OCI manifest and image config are saved to
`containers/<name>/manifest.json`. This lets `reset` re-pull the
exact same image later, and `run` know what to execute by default.

A **local archive** also can be used with `install` command:

- A **plain rootfs tarball** (`alpine.tar.gz`, `debian.tar.xz`, etc.):
  the leading path component count is detected automatically by
  scoring directory names like `bin`, `usr`, `etc`, `var`.
- An **OCI image layout** (as produced by `docker save` or
  `skopeo copy oci-archive:`): detected by the presence of an
  `oci-layout` file at the archive root. The selected platform's layers
  are extracted into the cache and applied with the same code path used
  for registry pulls.

### 2. The proot utility

[proot](https://proot-me.github.io/) is a user-space implementation of
`chroot`, `mount --bind`, and `binfmt_misc`. It uses Linux's `ptrace`
mechanism to intercept system calls made by the guest process and
rewrite filesystem paths on the fly. The result is a chroot-like
environment that does not need root privileges.

When you run `proot-distro login ubuntu`, PRoot-Distro `exec`s into a
`proot` command line that looks roughly like:

```sh
env PATH=… HOME=/root … \
  proot --kill-on-exit --link2symlink --sysvipc \
        --kernel-release=… -L \
        --change-id=0:0 \
        --rootfs=/…/containers/ubuntu/rootfs --cwd=/root \
        --bind=/dev --bind=/proc --bind=/sys \
        --bind=/storage --bind=/system --bind=/apex … \
        /bin/sh -l
```

You can see this exact command for any container by adding the
`--get-proot-cmd` flag to `login` or `run`.

#### Cross-architecture support

Architectures supported as containers: `aarch64`, `arm`, `i686`,
`x86_64`, `riscv64`. The host CPU is detected via `os.uname().machine`.
Cross-architecture execution uses **QEMU user-mode** via proot's `-q`
flag. The matching `qemu-user-<arch>` package must be installed on the
host. 32-bit guests run natively on 64-bit hosts when the kernel
supports `PER_LINUX32` (probed via `ctypes`).

Container architecture is detected automatically at every login by
reading ELF headers of common shell binaries inside the rootfs — there
is no separate config file to remember.

---

## Storage layout

All runtime data is stored under `$RUNTIME_DIR`:

- **Termux**: `$TERMUX__PREFIX/var/lib/proot-distro/`, where
  `TERMUX__PREFIX` defaults to `/data/data/com.termux/files/usr`.
- **Regular Linux**: `$XDG_DATA_HOME/proot-distro/` (default
  `~/.local/share/proot-distro/`).

The OCI download cache (`$DOWNLOAD_CACHE_DIR`) is under `$RUNTIME_DIR`
on Termux, and under `$XDG_CACHE_HOME/proot-distro/` (default
`~/.cache/proot-distro/`) on a regular Linux host.

| Path | Contents |
|---|---|
| `containers/<name>/rootfs/` | Container root filesystem |
| `containers/<name>/manifest.json` | Image reference, arch, full OCI manifest, full image config |
| `containers/<name>/rootfs/.l2s/` | Proot link2symlink (l2s) backing store (created on first login) |
| `dlcache/layers/` (Termux) or `$XDG_CACHE_HOME/proot-distro/layers/` | Cached OCI layer blobs |
| `dlcache/manifests/` (Termux) or `…/manifests/` | Cached resolved single-arch manifests |
| `installed-rootfs/<name>/` | **Legacy** layout; auto-migrated by `login`. |

---

## Environment variables

| Variable | Effect |
|---|---|
| `TERMUX__PREFIX` | Override Termux prefix path; drives `PREFIX` and `RUNTIME_DIR` on Termux. Defaults to `/data/data/com.termux/files/usr`. |
| `TERMUX__HOME` | Override the Termux home path used for `--shared-home` and the default storage bindings. Defaults to `/data/data/com.termux/files/home`. |
| `TERMUX_APP__PACKAGE_NAME` | Override the Termux app package (default `com.termux`); used for `--bind=/data/data/<pkg>/...`. |
| `TERMUX_APP__APP_VERSION_NAME`, `TERMUX_VERSION` | Either one (when set) counts as one of the indicators that flips on Termux mode in `_detect_termux()`. |
| `XDG_DATA_HOME` | On non-Termux hosts, base for `$XDG_DATA_HOME/proot-distro/`. Defaults to `~/.local/share`. |
| `XDG_CACHE_HOME` | On non-Termux hosts, base for `$XDG_CACHE_HOME/proot-distro/`. Defaults to `~/.cache`. |
| `PD_FORCE_NO_COLORS` | When set to any value, disables ANSI colors in PRoot-Distro's own output. |
| `PROOT_NO_SECCOMP` | Inherited and forwarded to `proot`. Set to `1` if `login` fails with seccomp-related errors on the host kernel. Skipped in `--minimal` mode. |
| `PROOT_VERBOSE` | Inherited and forwarded to `proot` for debugging. Skipped in `--minimal` mode. |
| `COLUMNS` | Fallback terminal width for `--help` rendering. |
| `TERM`, `COLORTERM` | Inherited from the host and exported into the guest (always; even in `--minimal`). In `normal`-type containers, `TERM` defaults to `xterm-256color` when unset on the host. |

---

## Shell completions

The packaged distribution installs completion scripts for Bash, Zsh,
and Fish to the standard locations:

- `share/bash-completion/completions/proot-distro`
- `share/zsh/site-functions/_proot-distro`
- `share/fish/vendor_completions.d/proot-distro.fish`

All three scripts complete both `proot-distro` and the short alias `pd`.

If your shell does not pick them up automatically, copy them by hand:

```sh
# Bash, current user
mkdir -p ~/.local/share/bash-completion/completions
cp proot_distro/completions/proot-distro.bash \
   ~/.local/share/bash-completion/completions/proot-distro

# Zsh, current user
mkdir -p ~/.zsh/completions
cp proot_distro/completions/_proot-distro ~/.zsh/completions/_proot-distro
# and add 'fpath=(~/.zsh/completions $fpath)' to .zshrc before compinit

# Fish, current user
mkdir -p ~/.config/fish/completions
cp proot_distro/completions/proot-distro.fish \
   ~/.config/fish/completions/proot-distro.fish
```

---

## Limitations

### PRoot limitations

- **Performance**: `proot` intercepts every system call via `ptrace`.
  Filesystem-heavy workloads (compilation, package managers) are
  noticeably slower than native execution.
- **Kernel features**: features that depend on Linux kernel modules
  (FUSE, specific iptables targets, custom cgroup hierarchies, etc.)
  do not work.
- **No real root**: proot uses UID/GID remapping to fake root. Programs
  that genuinely need kernel-level root (`sudo`, `mount` of real
  filesystems, `iptables`, etc.) will fail.
- **No background services**: starting service supervisors (`systemd`,
  `OpenRC`, socket-activated daemons) is generally not possible. You
  can run individual long-running processes, but a full init system is
  out of scope.
- **No cgroups / namespaces**: features that need real Linux kernel
  namespaces (`unshare`, container-in-container, network namespaces)
  do not work — proot is path translation, not kernel isolation.
- **seccomp**: some Android kernels restrict `ptrace` calls used by
  proot via seccomp policies. If `login` fails with seccomp-related
  errors, set `PROOT_NO_SECCOMP=1`.
- **No nesting**: PRoot-Distro refuses to run inside another `proot`,
  because `proot` itself cannot trace a process already being traced.

### PRoot-Distro limitations

- **Public registries only**: registry authentication is not
  implemented. Only public Docker Hub images and public OCI registries
  are supported. Private images return an explanatory error from
  `install`.
- **No zstd-compressed layers**: Python's `tarfile` module does not
  support zstd. Images using zstd-compressed layers (some newer Docker
  Hub images) fail to install with an explicit error. Try a different
  image or an older tag.
- **No image building**: PRoot-Distro consumes existing OCI images; it
  does not build them. Use `docker build` / `buildah` / `nerdctl` on a
  full Linux host to produce one, then `docker save -o myimage.oci.tar
  myimage:tag` and `proot-distro install ./myimage.oci.tar`.
- **No live state migration**: `backup`/`restore` archive the rootfs
  and the OCI manifest, but in-memory state of running processes is
  not preserved.
- **Cross-architecture Termux-type containers are not supported**: the
  host and the container share the same Termux prefix path, so QEMU
  emulation cannot hide the host's architecture-specific binaries.
- **Termux-only flags on non-Termux hosts**: `--isolated`, `--minimal`,
  `--no-link2symlink`, `--no-sysvipc`, and `--no-kill-on-exit` are not
  exposed by argparse when running outside Termux. Most are
  Android-specific in spirit, and on a regular Linux host the default
  behavior is already isolated in the sense that there are no Android
  bindings to drop.

---

## Donate

Support is important to keep the project up in a long term. I'm grateful for any amount of tip in cryptocurrency:

**Bitcoin**:
```
bc1qxuwtc0sfjt43n3sufck6s0gaeand8eaeguajxs
```

**Ethereum**:
```
0x1F5196A5b0120D4a66FCAABBe71728239B06EC12
```

**Tron**:
```
TEJiwRMMGV1JXvRYDRVJ1qw7kFgskEk3sJ
```

Recipient: the author of PRoot-Distro, [@sylirre](https://github.com/sylirre)

## Issues and contributing

- **Bug reports**: https://github.com/termux/proot-distro/issues
- **License**: GPL-3.0-only. See `LICENSE`.
