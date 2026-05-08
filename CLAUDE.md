# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working
with code in this repository.

## Overview

This repository contains `proot-distro`, a Python utility for managing
rootless proot-based Linux containers on Termux (Android). It pulls
Docker/OCI images and assembles container filesystems locally.

**No third-party Python dependencies.** `proot-distro` is pip-installable
(published on PyPI at https://pypi.org/project/proot-distro/).

---

## proot-distro

Manages proot-based Linux containers on Termux. The entry point
`proot-distro.py` is a thin shim; all logic lives in `proot_distro/`.

### Installation

```bash
pip install proot-distro        # from PyPI
pip install .                   # from local source
pip install -e .                # editable install for development
```

The `proot-distro` console script entry point resolves to
`proot_distro.cli:main`. `pyproject.toml` is the single source of truth
for the version string; `PROGRAM_VERSION` in `constants.py` reads it at
runtime via `importlib.metadata.version("proot-distro")`.

### Running directly (without pip)

```bash
./proot-distro.py list
./proot-distro.py install ubuntu:24.04
./proot-distro.py login ubuntu
./proot-distro.py login ubuntu --user myuser -- bash -c "echo hello"
./proot-distro.py remove ubuntu
./proot-distro.py --help
./proot-distro.py login --help
```

### Module layout (`proot_distro/`)

| Module | Contents |
|---|---|
| `constants.py` | All global path and default constants. `PREFIX` reads `TERMUX_PREFIX` env var. |
| `colors.py` | ANSI escape constants, `C` (color dict), `msg()`, `show_version()` |
| `arch.py` | CPU arch detection, ELF-based installed arch detection, QEMU/emulator helpers |
| `sysdata.py` | Fake `/proc`/`/sys` constants, `setup_fake_sysdata()`, `fake_proc_bindings()` |
| `helpers/download.py` | `fmt_size()`, `sha256_file()`, `download_file()` — with TTY progress bars |
| `helpers/rootfs.py` | Install helpers: `write_resolv_conf()`, `write_hosts()`, `register_android_ids()` |
| `helpers/docker.py` | Pure-Python OCI registry client: `pull_image()`, `parse_image_ref()`, `derive_alias()`, cache helpers, layer application |
| `commands/install.py` | `command_install()`, `_validate_alias()` |
| `commands/remove.py` | `command_remove()`, `_remove_path()` |
| `commands/rename.py` | `command_rename()` |
| `commands/reset.py` | `command_reset()` |
| `commands/login.py` | `command_login()`, `_migrate_legacy_rootfs()`, `_resolve_rootfs_path()`, `_read_image_env()`, and other private helpers |
| `commands/list.py` | `command_list()` |
| `commands/backup.py` | `command_backup()`, `_compression_mode()`, `_COMPRESSION_ARG_MAP`, `_iter_entries()`, `_add_path()`, `_fix_permissions()` |
| `commands/restore.py` | `command_restore()`, `_detect_compression()`, `_remove_existing()`, `_dest_path()` |
| `commands/clear_cache.py` | `command_clear_cache()`, `_ensure_readable()` |
| `commands/copy.py` | `command_copy()`, `_resolve_copy_path()` |
| `commands/help.py` | `command_help()`, `_HELP_COMMANDS` |
| `cli.py` | `build_parser()`, `_ALIAS_TO_CANONICAL`, `_COMMAND_HANDLERS`, `_REQUIRED_ARGS`, `main()` |

### Key paths (Termux defaults)

All paths are rooted under `TERMUX_PREFIX` (env var), which defaults to
`/data/data/com.termux/files/usr`. Set `TERMUX_PREFIX` to override.

| Constant | Default value |
|---|---|
| `PREFIX` | `/data/data/com.termux/files/usr` |
| `RUNTIME_DIR` | `$PREFIX/var/lib/proot-distro` |
| `CONTAINERS_DIR` | `$RUNTIME_DIR/containers` |
| `LEGACY_ROOTFS_DIR` | `$RUNTIME_DIR/installed-rootfs` (migration only) |
| `DOWNLOAD_CACHE_DIR` | `$RUNTIME_DIR/dlcache` |
| `LAYER_CACHE_DIR` | `$RUNTIME_DIR/dlcache/layers` |
| `MANIFEST_CACHE_DIR` | `$RUNTIME_DIR/dlcache/manifests` |

### Container storage layout

Each container is stored as a directory under `CONTAINERS_DIR`:

```
containers/<name>/
containers/<name>/manifest.json   ← image_ref, arch, OCI manifest
containers/<name>/rootfs/         ← assembled filesystem
containers/<name>/rootfs/etc/
containers/<name>/rootfs/...
```

`manifest.json` records `image_ref`, `arch`, and the full OCI manifest so
that `reset` can re-pull the exact same image later.

### Legacy format migration

The old storage layout placed the rootfs at
`installed-rootfs/<name>` (without the `containers/` wrapper and without
`manifest.json`). `login` detects this on first use and automatically moves
the rootfs to `containers/<name>/rootfs` via `_migrate_legacy_rootfs()`.

The constant `LEGACY_ROOTFS_DIR` is kept exclusively for this migration path.
No command other than `login` reads from it.

### Container identity

The **container directory name** under `CONTAINERS_DIR` is the sole
identifier. There are no YAML config files at runtime.

`list` scans `CONTAINERS_DIR` for subdirectories that have a `rootfs/`
inside. All other commands validate existence by checking for
`containers/<name>/rootfs`.

### Distribution type: `normal` vs `termux`

Detected from the rootfs filesystem layout at login time:

- **`termux`**: `rootfs/data/data/com.termux/files/usr/` exists inside rootfs.
- **`normal`**: all other rootfs layouts.

**`termux` type** differences in `login`:
- No `--link2symlink`, no `--change-id`.
- Working directory defaults to `/data/data/com.termux/files/home`.
- Inner command is `/data/data/com.termux/files/usr/bin/login` (exec'd directly; no `/usr/bin/env` wrapper).
- Guest env is hardcoded to `HOME`, `PATH`, `PREFIX`, `TMPDIR` for the Termux layout, plus host-inherited `TERM`/`COLORTERM` and any user `--env` entries.
- Android system bindings are always enabled; Termux `PREFIX` is not bound into the guest.

### Commands and aliases

| Command | Aliases |
|---|---|
| `install` | `add`, `i`, `in`, `ins` |
| `remove` | `rm` |
| `rename` | — |
| `reset` | — |
| `login` | `sh` |
| `list` | `li`, `ls` |
| `backup` | `bak`, `bkp` |
| `restore` | — |
| `clear-cache` | `clear`, `cl` |
| `copy` | `cp` |
| `help` | `h`, `he`, `hel` |

Note: the `mv` alias for `rename` was removed.

### proot availability check

`main()` calls `shutil.which("proot")` early. If proot is absent and stdin
is a TTY, the user is prompted `Would you like to install it now? [y/N]`;
answering `y`/`yes` runs `pkg install -y -q proot` via `subprocess.run`. On
a non-TTY stdin the install hint is printed and the process exits.

### Argument validation

All required positional arguments are declared `nargs='?'` in argparse so
that missing arguments are caught manually rather than by argparse.
`_REQUIRED_ARGS` in `cli.py` maps each canonical command to a list of
`(arg_name, error_message)` pairs checked in order before dispatch. On
failure: blank line, error message, then the command's help text (via
`_HELP_COMMANDS`). `restore` handles its own missing-input error inside
`command_restore()` (depends on stdin TTY state).

`install` treats `args.alias` as a Docker image reference (e.g.
`ubuntu:24.04`, `alpine:3.21`, `myuser/myimage:tag`,
`ghcr.io/org/image:tag`). Options: `--name ALIAS` (stored as
`args.custom_dist_name`) installs under a custom local name; an empty
`--name ''` is rejected immediately. `--architecture ARCH` (stored as
`args.override_arch`; choices: `aarch64`, `arm`, `i686`, `riscv64`,
`x86_64`) overrides the target CPU architecture (defaults to device arch).

`login` always detects architecture automatically from the installed rootfs
ELF binaries; there is no architecture override for login. `login --emulator
PATH` (stored as `args.emulator`) overrides the emulator binary; the path
must exist and be executable.

`remove` accepts `-v`/`--verbose` to print each removed file in real time.

`login` accepts `--bind PATH[:PATH]` (repeatable). The source component is
always resolved to an absolute path via `os.path.abspath`. The destination
must not be `.` or `..`.

`backup` accepts `--compress TYPE` (choices: `gzip`, `bzip2`, `xz`, `none`;
stored as `args.compression`) to force a specific compression algorithm.

`copy` accepts `--recursive` (`-r`) to allow directory copying.

### Colors

`proot_distro/colors.py` defines ANSI escape sequence constants composed into
a `_COLORS` dict. `C` is set to `_COLORS` when `sys.stderr.isatty()` and
`PD_FORCE_NO_COLORS` is unset, otherwise `_EMPTY` (all-empty strings). Every
color entry starts with `_RST` so color transitions implicitly reset
attributes.

### Architecture and emulation

- Device arch detected via `os.uname().machine`
- Installed arch detected by `detect_installed_arch(rootfs_path)` in
  `arch.py`: reads the first 20 bytes of candidate ELF binaries, checks
  magic, reads endianness from `EI_DATA`, unpacks `e_machine` with `struct`,
  and maps via `_ELF_MACHINE_MAP`. Accepts either a full rootfs path or a
  bare container name (resolved as `CONTAINERS_DIR/<name>/rootfs`).
- 32-bit support on AArch64 probed via `ctypes.CDLL(None).personality(PER_LINUX32)`
- Cross-arch: proot `-q qemu-*` (QEMU user-mode)
- 32-bit guests on 64-bit hosts run natively when supported

### Install: Docker Hub OCI pull

`command_install()` calls `pull_image()` from `helpers/docker.py`.

**`pull_image(image_ref, rootfs_dir, arch)`** flow:

1. **Check manifest cache** (`dlcache/manifests/<safe_key>.json`) first:
   - If cached **and** all layers present in `dlcache/layers/` → fully offline install.
   - If cached but some layers missing → fetch auth token, download missing layers.
   - If not cached → full online resolution: auth → manifest list → arch manifest → image config blob; saved to cache.
2. **Apply layers** in order via `_apply_layer()`. Cached layers skip download.
3. **Whiteout semantics** (OCI spec §6.1.2): `.wh..wh..opq` clears parent dir; `.wh.<name>` deletes the named sibling. Hard links are copied (`shutil.copy2`), deferred until all regular files are written. Block/character devices and FIFOs are silently skipped.
4. **After layer application**: `write_resolv_conf`, `write_hosts`, `register_android_ids`, `setup_fake_sysdata` are run. `/etc/environment` is **not** created — env vars are set at login time.
5. Returns dict with `name`, `version`, `description`, `env`, `manifest`, `image_config`.

After `pull_image()` returns, `command_install()` writes:
- `containers/<name>/manifest.json` with `image_ref`, `arch`, `manifest`, `image_config`
- `containers/<name>/rootfs/.proot-distro/image-env` with image `Env` lines

**Auth stripping:** Docker Hub blob endpoints redirect to CDN pre-signed
URLs. CDN hosts reject `Bearer` tokens (HTTP 400).
`_AuthStrippingRedirectHandler` strips the `Authorization` header on
cross-host redirects.

**Layer integrity:** `_download_blob()` streams the blob through a
`hashlib.sha256` hasher while writing it. After the body is fully read the
computed digest is compared to the expected one from the manifest; on
mismatch the temp file is unlinked and `RuntimeError` is raised before any
data is promoted into the cache. Only `sha256` digests are accepted (the
only algorithm currently used by Docker Hub and the OCI distribution
spec). Cached layers are trusted because the cache only ever contains
verified blobs — verification happens before `os.replace` moves the temp
file to its final location.

**`parse_image_ref(image_ref)`** returns `(registry, repo, tag)` where
`registry` is empty for Docker Hub images.

**Custom registry detection:** if the first path component of the image ref
contains `.` or `:`, it is treated as the registry host
(e.g. `ghcr.io/foo/bar:tag` → registry=`ghcr.io`, repo=`foo/bar`,
tag=`tag`).

**Cache layout in `dlcache/`:**
- `layers/<digest_with_colon_as_underscore>` — one file per layer blob.
- `manifests/<safe_image_ref>_<arch>.json` — resolved single-arch manifest JSON plus image config.

**Architecture mapping** (`_ARCH_TO_DOCKER`):

| proot-distro arch | Docker arch | Variant |
|---|---|---|
| `aarch64` | `arm64` | — |
| `arm` | `arm` | `v7` |
| `i686` | `386` | — |
| `x86_64` | `amd64` | — |
| `riscv64` | `riscv64` | — |

### Login: passwd resolution and environment variables

**`/etc/passwd` symlink resolution** — `_resolve_rootfs_path(rootfs, guest_path)` resolves an absolute guest path to its real host path by following symlinks within the rootfs namespace. Absolute symlink targets are re-rooted under `rootfs` via `os.path.normpath(target)` (prevents `..` escapes past `/`). The loop runs at most 40 times; exceeding this raises `OSError(ELOOP)`.

**Environment delivery** — `command_login()` builds a clean `child_env` dict and passes it to `os.execvpe("proot", ...)`. Proot inherits this dict and propagates it to the spawned shell. There is **no `/usr/bin/env -i` wrapper** in the inner command — the shell is exec'd directly. The host's environment is **not** carried into the guest; only the entries below are exported.

**Environment variable precedence** (later entries win):

1. `PATH=<DEFAULT_PATH_ENV>`, `MOZ_FAKE_NO_SANDBOX=1`, `PULSE_SERVER=127.0.0.1` — baseline always exported
2. Image `Env` — reads `rootfs/.proot-distro/image-env`
3. Android system vars (`ANDROID_ART_ROOT`, `ANDROID_DATA`, `ANDROID_I18N_ROOT`, `ANDROID_ROOT`, `ANDROID_RUNTIME_ROOT`, `ANDROID_TZDATA_ROOT`, `BOOTCLASSPATH`, `DEX2OATBOOTCLASSPATH`, `EXTERNAL_STORAGE`) — exported only when `--isolated` is **not** set
4. `--env` flags from the command line
5. `HOME`, `USER`, `TERM`, `COLORTERM` — always set last; `TERM` and `COLORTERM` inherit from host (`COLORTERM` only when set on the host; `TERM` falls back to `xterm-256color` when the host has none)

Proot's own toggle env vars (`PROOT_NO_SECCOMP`, `PROOT_DUMP`, `PROOT_VERBOSE`, plus `PROOT_L2S_DIR` for normal dists with an `.l2s` directory) are added to `child_env` after the user-facing precedence above so proot can read them. `LD_PRELOAD` is removed from `child_env` before the exec.

### Remove

`command_remove()` removes the entire `containers/<name>` directory (rootfs
+ manifest.json). Uses `_remove_path()` with on-the-fly permission fixing.

**`_remove_path(path, on_remove=None)`**: recursive removal with permission
fixing. Directories receive at least `S_IRWXU`; regular files receive at
least `S_IRUSR | S_IWUSR`; symlinks are unlinked directly. Returns `True` on
full success.

### Copy

`command_copy()` copies or moves files between host paths and container
rootfs paths (resolved via `dist:path` notation by `_resolve_copy_path()`).
Container paths resolve to `containers/<name>/rootfs/<path>`.

- **`--recursive`** (`-r`): required for directory copying.
- **`--move`** (`-m`): uses `shutil.move`.
- **`--verbose`** (`-v`): logs each file.

### Backup

`command_backup()` uses a pure-Python tar implementation.

- **Archive structure**: `<name>/`, `<name>/manifest.json`, `<name>/rootfs/...`
- **Source**: `containers/<name>/` directory.
- **Permission fix**: only applied to the `rootfs/` subdirectory before archiving.
- **Compression**: determined by `--compress` flag first, then file extension.
- **tar mode**: seekable file uses `w:{comp}`; streaming stdout uses `w|{comp}`.
- **Entry filtering**: block/character devices, FIFOs, and sockets are silently skipped.
- **Ownership**: uid/gid/uname/gname are zeroed.
- **Progress bar**: TTY-only stderr, format `[*] [####----] XX%  N / Total files`.
- **CTRL-C**: progress bar cleared, partial output file removed.

### Restore

`command_restore()` uses a pure-Python tar implementation.

- **Compression**: detected from file header magic bytes. File: `r:{comp}`; stdin: `r|{comp}`.
- **Archive format routing** (via `_dest_path()`):
  - New: `<name>/manifest.json` → `containers/<name>/manifest.json`
  - New: `<name>/rootfs/...` → `containers/<name>/rootfs/...`
  - Legacy: `installed-rootfs/<name>/...` → `containers/<name>/rootfs/...`
  - No subdir (bare root): rejected with an error.
- **Recursive-unlink**: on the first entry for a container, the existing rootfs is cleared.
- **Progress bar**: seekable file shows accurate bar; stdin shows counter.
- **CTRL-C**: progress bar cleared, no data removed.

### Reset

`command_reset()` reads `image_ref` and `arch` from
`containers/<name>/manifest.json`, removes only the `rootfs/` subdirectory,
and calls `command_install()` with a synthetic args object. If `manifest.json`
is missing, falls back to pulling `<name>:latest`.

### Clear cache

`command_clear_cache()` removes all contents of `DOWNLOAD_CACHE_DIR`.
Uses `os.scandir` for iteration, `shutil.rmtree` for subdirectories,
`os.remove` for stray files. Reports total freed space.

### Fake sysdata

On install and on every login of a `normal`-type container, fake `/proc`
and `/sys` stub files are written **inside the container's own rootfs**
(`containers/<name>/rootfs/proc/.loadavg`, `…/.stat`, `…/.uptime`,
`…/.version`, `…/.vmstat`, `…/.sysctl_entry_cap_last_cap`,
`…/.sysctl_inotify_max_user_watches`, plus `…/sys/.empty`) and then
bind-mounted by proot over the corresponding `/proc/*` and
`/sys/fs/selinux` paths. Storing them inside the rootfs keeps them
co-located with the container, so `remove` cleans them up automatically.
Both `setup_fake_sysdata(rootfs)` and `fake_proc_bindings(rootfs)` take
the absolute rootfs path as their argument. Constants `_FAKE_LOADAVG`,
`_FAKE_STAT`, `_FAKE_UPTIME`, `_FAKE_VMSTAT` are hardcoded in
`proot_distro/sysdata.py`. `termux`-type containers do not get fake
sysdata (they share the host's `/proc` and `/sys` via the existing
`--bind=/proc` / `--bind=/sys`).

### Subcommand help

Per-command help text is in `_HELP_COMMANDS` dict (lambdas) in
`proot_distro/commands/help.py`. All text is limited to 66 display columns.
Subcommand `--help`/`-h` is intercepted in `main()` before argparse parses
positional arguments to avoid "required argument" errors.

### Pure Python policy

`proot_distro/` avoids spawning subprocesses for system queries:
- Colors: ANSI constants, no `tput`
- User/group info in `helpers/rootfs.py`: `pwd.getpwuid()` and `grp.getgrgid()`, no `id`
- ELF arch detection in `arch.py`: `struct.unpack` on raw bytes, no `file`
- 32-bit support in `arch.py`: `ctypes` `personality()` syscall, no `lscpu`
- OCI registry access: `urllib.request`, no `docker` or `curl`
- Layer extraction: `tarfile` module, no `tar` subprocess
- Backup archiving: `tarfile` module, no `tar` subprocess
- Restore extraction: `tarfile` module, no `tar` subprocess
