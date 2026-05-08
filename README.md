# PRoot-Distro

PRoot-Distro is a utility for managing rootless Linux containers in
[Termux](https://termux.dev). It uses [proot](https://proot-me.github.io/)
to provide a chroot-like environment without requiring root access on the
Android device.

Containers are created by pulling Docker/OCI images directly from Docker Hub
or any compatible public registry. The container filesystem is assembled from
the image layers and stored locally, ready to be entered at any time.

---

## How to install

PRoot-Distro requires Termux. Install it from
[F-Droid](https://f-droid.org/en/packages/com.termux/) or
[GitHub Releases](https://github.com/termux/termux-app/releases).

Inside Termux:

```sh
pkg install proot-distro
```

Or install the latest development version directly from PyPI:

```sh
pip install proot-distro
```

Or clone this repository and install in editable mode:

```sh
git clone https://github.com/termux/proot-distro
cd proot-distro
pip install -e .
```

**Dependency:** Only `proot` is required at runtime. Install it with:

```sh
pkg install proot
```

---

## Quick start

```sh
# Install Ubuntu 24.04
proot-distro install ubuntu:24.04

# Start a shell inside the container
proot-distro login ubuntu

# List all installed containers
proot-distro list

# Remove a container
proot-distro remove ubuntu
```

---

## Commands

### `install` — Install a container

```
proot-distro install [OPTIONS] IMAGE
Aliases: add, i, in, ins
```

Pull a Docker/OCI image and create a container from it.
`IMAGE` is a standard Docker image reference:

| Form | Example |
|---|---|
| Official image | `ubuntu:24.04` |
| Official, latest | `alpine` |
| User image | `myuser/myimage:tag` |
| Custom registry | `ghcr.io/foo/bar:latest` |

When no tag is given, `latest` is used.

**Options:**

| Option | Description |
|---|---|
| `--name NAME` | Set a custom local name for the container (default: image name without tag) |
| `--architecture ARCH` | Override target CPU architecture (`aarch64`, `arm`, `i686`, `riscv64`, `x86_64`) |

**Examples:**

```sh
proot-distro install ubuntu:24.04
proot-distro install alpine:3.21 --name my-alpine
proot-distro install debian:bookworm --architecture aarch64
proot-distro install ghcr.io/myorg/myimage:latest
```

Layers are cached in `$TERMUX_PREFIX/var/lib/proot-distro/dlcache/` and
reused on subsequent installs. If all layers are already cached, installation
runs fully offline.

---

### `login` — Start a shell inside a container

```
proot-distro login [OPTIONS] CONTAINER [-- COMMAND]
Aliases: sh
```

Spawn an interactive shell (or a custom command) inside an installed
container. The `--` separator is used to pass a command to run without
an interactive shell.

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
```

**Options:**

| Option | Description |
|---|---|
| `--user USER` | Log in as USER (default: root) |
| `--redirect-ports` | Redirect privileged ports 1–1023 to higher numbers |
| `--isolated` | Only bind mandatory host paths (no `/sdcard`, etc.) |
| `--termux-home` | Bind Termux home directory into the container |
| `--shared-tmp` | Bind Termux tmp to `/tmp` inside the container |
| `--bind SRC[:DST]` | Bind a custom path (repeatable). Source is resolved to an absolute path |
| `--no-link2symlink` | Disable proot's hardlink emulation |
| `--no-sysvipc` | Disable System V IPC emulation |
| `--no-kill-on-exit` | Wait for all child processes before exiting |
| `--no-arch-warning` | Suppress the 32-bit CPU capability warning |
| `--emulator PATH` | Override the QEMU emulator binary for cross-arch containers |
| `--kernel TEXT` | Customize the kernel release string reported by `uname` |
| `--hostname TEXT` | Customize the hostname inside the container |
| `--work-dir PATH` | Set the initial working directory |
| `--env VAR=VALUE` | Set an environment variable (repeatable) |

**Legacy migration:** if the container was created by an older version of
PRoot-Distro and its rootfs is still at the legacy path
(`installed-rootfs/<name>`), `login` automatically migrates it to the new
location (`containers/<name>/rootfs`) on first use.

---

### `list` — List installed containers

```
proot-distro list
Aliases: li, ls
```

Show all installed containers. When none are installed, an install
suggestion is printed.

---

### `remove` — Delete a container

```
proot-distro remove [OPTIONS] CONTAINER
Aliases: rm
```

Permanently delete the specified container and all its data. **This
cannot be undone.**

| Option | Description |
|---|---|
| `--verbose` | Log each deleted file |

---

### `rename` — Rename a container

```
proot-distro rename OLDNAME NEWNAME
```

Rename a container from `OLDNAME` to `NEWNAME`. Also updates all
proot link2symlink (l2s) entries inside the container so hardlinks
remain valid. This may take a while for large containers.

---

### `reset` — Reinstall a container from scratch

```
proot-distro reset CONTAINER
```

Remove the container rootfs and reinstall it from the Docker image
recorded at install time. **All data inside the container is lost.**

The image reference and target architecture are read from
`containers/<name>/manifest.json`. If that file is missing, the reset
falls back to pulling `<name>:latest` from Docker Hub.

---

### `backup` — Archive a container

```
proot-distro backup [OPTIONS] CONTAINER
Aliases: bak, bkp
```

Create a TAR archive of the container. The archive includes
`<name>/manifest.json` and `<name>/rootfs/`.

**Options:**

| Option | Description |
|---|---|
| `--output FILE` | Write to FILE instead of stdout |
| `--compress TYPE` | Force compression: `gzip`, `bzip2`, `xz`, or `none` |
| `--verbose` | Log each archived file |

When `--output` is given, the compression algorithm is inferred from the
file extension (`.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tar`) unless
`--compress` overrides it. Without `--output`, the archive is written to
stdout uncompressed by default.

**Examples:**

```sh
# Create a compressed backup
proot-distro backup ubuntu --output ubuntu.tar.xz

# Pipe to another command
proot-distro backup ubuntu | gzip > ubuntu.tar.gz

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

Compression is detected automatically from the file header. Supported
formats: gzip, bzip2, xz, and uncompressed tar.

**Options:**

| Option | Description |
|---|---|
| `--verbose` | Log each extracted file |

**Archive format requirements:**

- Files must be stored under a subdirectory named after the container
  (e.g. `ubuntu/rootfs/...`). Bare-root archives are rejected.
- If `manifest.json` is not present in the archive, the container is
  restored without it (login still works).
- Legacy archives (`installed-rootfs/<name>/...`) are accepted and
  automatically re-rooted to the new layout.

**Examples:**

```sh
# Restore from a file
proot-distro restore ubuntu.tar.xz

# Restore from stdin
cat ubuntu.tar.xz | proot-distro restore
```

---

### `clear-cache` — Delete the download cache

```
proot-distro clear-cache
Aliases: clear, cl
```

Remove all cached Docker image layers and manifests from
`$TERMUX_PREFIX/var/lib/proot-distro/dlcache/`. Disk space freed is
reported after the operation.

| Option | Description |
|---|---|
| `--verbose` | Log each deleted file |

---

### `copy` — Copy files to or from a container

```
proot-distro copy [OPTIONS] [CONTAINER:]SRC [CONTAINER:]DEST
Aliases: cp
```

Copy files between the host filesystem and a container rootfs (or
between two containers). Paths inside a container are prefixed with
the container name and a colon: `ubuntu:/etc/resolv.conf`.

**Options:**

| Option | Description |
|---|---|
| `--recursive` | Copy directories recursively (like `cp -a`) |
| `--move` | Move instead of copying (deletes source after success) |
| `--verbose` | Log each copied file |

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

Directories `.` and `..` are accepted only as source, not as
destination.

---

### `sync` — Synchronize files to or from a container

```
proot-distro sync [OPTIONS] [CONTAINER:]SRC [CONTAINER:]DEST
```

Synchronize SRC to DEST, copying only files that differ. Both paths may
be plain host paths or `container:path` references. Always recursive —
no flag needed.

**Comparison method:**

| Mode | What is compared |
|---|---|
| Default | File size and modification time |
| `--checksum` | File size and CRC32 checksum |

**Behavior:**

- Symlinks are copied as-is (not followed).
- Hard links become independent file copies.
- Block/char devices, FIFOs, and sockets are silently skipped.
- File ownership is never changed.
- Access modes and modification timestamps are preserved.
- If a source file is not readable, a warning is printed and it is skipped.
- If the destination lacks write permission, `sync` attempts to chmod it.
  If that also fails, the command exits with an error.

**Options:**

| Option | Description |
|---|---|
| `--checksum` | Compare by size + CRC32 instead of size + mtime |
| `--verbose` | Log each synced entry |

**Examples:**

```sh
# Sync a local directory into a container
proot-distro sync ./app ubuntu:/opt/app

# Sync a directory out of a container
proot-distro sync ubuntu:/etc ./backup/etc

# Use checksum-based comparison
proot-distro sync --checksum ./data ubuntu:/data
```

---

### `run` — Run the image-defined entrypoint

```
proot-distro run [OPTIONS] CONTAINER [-- ARG ...]
```

Run the `Entrypoint` and/or `Cmd` defined in the container's Docker
image manifest. This is equivalent to `docker run` in non-interactive
mode: the container starts, executes the image-defined command, and
exits.

**Entrypoint and Cmd resolution:**

| Manifest | Command after `--` | Executed |
|---|---|---|
| `Entrypoint` + `Cmd` defined | _(none)_ | `Entrypoint` + `Cmd` |
| `Entrypoint` + `Cmd` defined | `ARG ...` | `Entrypoint` + `ARG ...` |
| Only `Cmd` defined | _(none)_ | `Cmd` |
| Only `Cmd` defined | `ARG ...` | `ARG ...` |
| Neither defined | _(none)_ | Error |

Accepts the same options as `login`. See `proot-distro login --help`.

**Examples:**

```sh
# Run the image's default entrypoint
proot-distro run ubuntu

# Pass arguments to the entrypoint (override Cmd)
proot-distro run ubuntu -- --version

# Run isolated with a custom environment variable
proot-distro run ubuntu --isolated --env MY_VAR=hello
```

---

## Storage layout

All runtime data is stored under:

```
$TERMUX_PREFIX/var/lib/proot-distro/
```

Where `$TERMUX_PREFIX` defaults to `/data/data/com.termux/files/usr`
and can be overridden by setting the `TERMUX_PREFIX` environment variable.

| Path | Contents |
|---|---|
| `containers/<name>/rootfs/` | Container root filesystem |
| `containers/<name>/manifest.json` | Image reference and OCI manifest (used by `reset`) |
| `dlcache/layers/` | Cached OCI image layers |
| `dlcache/manifests/` | Cached OCI image manifests |

---

## Limitations

### proot limitations

- **Performance:** proot intercepts every system call via `ptrace`. This
  makes filesystem-heavy workloads (compilation, package managers) noticeably
  slower than native execution.
- **Kernel features:** some kernel interfaces are not emulated. Features that
  depend on Linux kernel modules (FUSE, specific iptables targets, etc.) will
  not work.
- **No root privileges:** proot uses UID/GID remapping to fake root. Programs
  that genuinely need kernel-level root (e.g. `sudo`, `mount`) will fail.
- **No background services:** starting system services (systemd, OpenRC,
  daemons with socket activation) is generally not possible in proot.
- **seccomp:** some Android kernels restrict `ptrace` calls used by proot via
  seccomp policies. If login fails, try setting `PROOT_NO_SECCOMP=1`.

### PRoot-Distro limitations

- **Public registries only:** this version does not implement registry
  authentication. Only public Docker Hub images and public OCI registries
  are supported.
- **No zstd-compressed layers:** Python's `tarfile` module does not support
  zstd. Images using zstd-compressed layers (some newer Docker Hub images)
  will fail to install. Use an older tag or a different image.
- **Backup/restore:** only containers in the new layout
  (`containers/<name>/rootfs`) can be backed up. Legacy containers are
  migrated on first `login`.
