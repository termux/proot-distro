# CLAUDE.md

Guidance for Claude Code when working on this repository.

## Overview

`proot-distro` is a Python utility for managing rootless proot-based
Linux containers. Primary target is Termux on Android; also runs on
regular Linux hosts (XDG base dirs, no Android-specific bindings). It
pulls Docker/OCI images and assembles container filesystems locally.

**No third-party Python dependencies.** Published on PyPI at
https://pypi.org/project/proot-distro/. `pyproject.toml` is the single
source of truth for the version; `PROGRAM_VERSION` in `constants.py`
reads it at runtime via `importlib.metadata.version("proot-distro")`,
falling back to `"rolling"` when not installed.

The entry point `proot-distro.py` is a thin shim; all logic lives in
`proot_distro/`. Console scripts `proot-distro` and `pd` both resolve
to `proot_distro.cli:main`. Shell completion files (Bash, Zsh, Fish)
are installed as package data to standard `share/` locations.

## Module layout (`proot_distro/`)

| Module | Contents |
|---|---|
| `constants.py` | All global path/default constants. `IS_TERMUX`, `PREFIX`, `TERMUX_HOME`, `TERMUX_APP_PACKAGE`, `_detect_termux()`, XDG fallbacks, default kernel strings. |
| `colors.py` | ANSI escape constants, `C` (color dict), `msg()`, `tty_safe_for_writes()`, `show_version()`. |
| `arch.py` | CPU arch detection, ELF-based installed-arch detection, `normalize_arch()`, QEMU emulator selection. |
| `sysdata.py` | Fake `/proc`/`/sys` constants, `setup_fake_sysdata()`, `fake_proc_bindings()`. |
| `helpers/download.py` | `fmt_size()`, `sha256_file()`, `download_file()` with TTY progress bars. |
| `helpers/rootfs.py` | `write_resolv_conf()`, `write_hosts()`, `register_android_ids()`. |
| `helpers/docker.py` | Pure-Python OCI registry client: `pull_image()`, `parse_image_ref()`, `derive_alias()`, `_AuthStrippingRedirectHandler`, cache and layer application helpers. |
| `helpers/dockerfile.py` | Dockerfile parser: `parse_dockerfile()` → `(directives, instructions)`, `expand_vars()` for `$VAR`/`${VAR…}` substitution, `DockerfileSyntaxError`. |
| `helpers/layer_diff.py` | Layer snapshot/diff/tar writers: `snapshot()`, `diff_snapshots()`, `write_layer_tar()`, `write_files_layer()`, `_resolve_l2s_target()` (resolves proot `--link2symlink` symlinks to file content so layers stay portable). |
| `helpers/build_cache.py` | Recipe-hash → layer-digest index: `lookup()`, `record()`, `compute_recipe_hash()`. |
| `helpers/oci_writer.py` | OCI output writers: `build_manifest_and_config()`, `store_in_cache()` (Variant A — manifest cache), `write_oci_archive()` (Variant B — tarball with OCI layout + Docker-legacy `manifest.json`), `_build_docker_manifest()` for `docker load` compatibility. |
| `helpers/build_engine.py` | Dockerfile interpreter: `BuildEngine`, `Stage`, `BuildError`, per-instruction handlers, `needs_proot()`, `_PREDEFINED_ARGS`, `_HANDLERS` table. |
| `locking.py` | Advisory POSIX `flock(2)` locking: `ContainerLock`, `LOCKS_DIR`, `_held_exclusive`, `_lock_path()`, `_read_lock_info()`. |
| `commands/install.py` | `command_install()`, `_validate_name()`, `_is_local_path()`, `_derive_local_name()`, `_detect_strip_count()`, `_extract_plain_tar()`, `_extract_oci()`, `_install_from_local_file()`. |
| `commands/build.py` | `command_build()`, `_derive_tag_from_path()`, `_is_valid_tag()`, `_install_as_container()`, `_prompt_proot_install()`. |
| `commands/login.py` | `command_login()`, `_command_login_inner()`, `_migrate_legacy_rootfs()`, `_inject_termux_profile()`, `_resolve_rootfs_path()`, `_read_manifest_env()`, `_IMAGE_ENV_BLOCKED`, `_storage_bindings()`, `_system_bindings()`, `_dq()`. |
| `commands/run.py` | `command_run()`, `_read_image_config()`. |
| `commands/remove.py` | `command_remove()`, `_remove_path()`. |
| `commands/rename.py` | `command_rename()`, `_do_rename()`. |
| `commands/reset.py` | `command_reset()`. |
| `commands/list.py` | `command_list()`. |
| `commands/backup.py` | `command_backup()`, `_run_backup()`, `_compression_mode()`, `_COMPRESSION_ARG_MAP`, `_UNSUPPORTED_EXTS`, `_iter_entries()`, `_add_path()`, `_fix_permissions()`, `_ReadCounter`. |
| `commands/restore.py` | `command_restore()`, `_detect_compression()`, `_dest_path()`, `_ByteCounter`. |
| `commands/clear_cache.py` | `command_clear_cache()`, `_ensure_readable()`. |
| `commands/copy.py` | `command_copy()`, `_do_copy()`, `_container_from_spec()`, `_resolve_copy_path()`. |
| `commands/sync.py` | `command_sync()`, `_do_sync()`, `_container_from_spec()`, `_resolve_sync_path()`, `_collect_entries()`, `_collect_extras()`, `_needs_update()`, `_sync_dir()`, `_sync_symlink()`, `_sync_file()`, `_file_checksum()`, `_unlink_robust()`, `_rmtree_robust()`. |
| `commands/help.py` | `command_help()`, `_HELP_PAGES`, `_HELP_COMMANDS`, `_render_page()`, `_term_width()`, layout primitives. |
| `cli.py` | `build_parser()`, `_ALIAS_TO_CANONICAL`, `_COMMAND_HANDLERS`, `_REQUIRED_ARGS`, `main()`. |
| `completions/` | Bash, Zsh, Fish completion scripts (package data). |

## Termux vs non-Termux host detection

`constants._detect_termux()` returns `True` when **at least two of three**
indicators are present:

1. Android signal: `"android"` in `platform.platform().lower()`, OR
   `/system/build.prop` exists, OR `/data/app` exists.
2. Termux env var present: `TERMUX_APP__APP_VERSION_NAME` or
   `TERMUX_VERSION` is set.
3. `PREFIX` is readable + executable (`os.access(PREFIX, R_OK | X_OK)`).

The Termux env var names are double-underscore: `TERMUX__PREFIX`,
`TERMUX__HOME`, `TERMUX_APP__PACKAGE_NAME` (and the version vars above).
The `PREFIX`, `TERMUX_HOME`, and `TERMUX_APP_PACKAGE` constants read
these and fall back to `/data/data/com.termux/files/...` paths.

`IS_TERMUX` is computed once at import time and drives:

- Path selection (Termux prefix vs XDG base dirs).
- `DEFAULT_PATH_ENV` (Termux adds `PREFIX/bin`, `/system/bin`, etc.).
- Argparse availability of `--isolated`, `--minimal`,
  `--no-link2symlink`, `--no-sysvipc`, `--no-kill-on-exit` (these are
  only added on Termux, for both `login` and `run`).
- `command_login` skips proot extensions (`--link2symlink`, `--sysvipc`,
  `--kill-on-exit`, `-L`, fake `--kernel-release`) and Android-specific
  bindings on non-Termux hosts.

## Key paths

| Constant | Termux | Non-Termux Linux |
|---|---|---|
| `RUNTIME_DIR` | `$PREFIX/var/lib/proot-distro` | `$XDG_DATA_HOME/proot-distro` (or `~/.local/share/...`) |
| `DOWNLOAD_CACHE_DIR` | `$RUNTIME_DIR/dlcache` | `$XDG_CACHE_HOME/proot-distro` (or `~/.cache/...`) |
| `CONTAINERS_DIR` | `$RUNTIME_DIR/containers` | same |
| `LEGACY_ROOTFS_DIR` | `$RUNTIME_DIR/installed-rootfs` (migration only) | same |
| `LAYER_CACHE_DIR` | `$DOWNLOAD_CACHE_DIR/layers` | same |
| `MANIFEST_CACHE_DIR` | `$DOWNLOAD_CACHE_DIR/manifests` | same |

Defaults: `DEFAULT_FAKE_KERNEL_RELEASE = "6.17.0-PRoot-Distro"`,
`DEFAULT_FAKE_KERNEL_VERSION = "#1 SMP PREEMPT_DYNAMIC Fri, 10 Oct 2025
00:00:00 +0000"`. DNS: `8.8.8.8`, `8.8.4.4`.

## Container storage layout

```
containers/<name>/
containers/<name>/manifest.json   ← image_ref, arch, OCI manifest, image_config
containers/<name>/rootfs/         ← assembled filesystem
```

`manifest.json` records `image_ref`, `arch`, the full OCI manifest,
and `image_config` (the full image config blob) so that `reset` can
re-pull the same image and `login`/`run` can read image-defined Env,
Entrypoint, Cmd, and WorkingDir. Plain tarball local installs **do
not** write `manifest.json`.

The **container directory name** under `CONTAINERS_DIR` is the sole
identifier; there are no YAML config files at runtime. `list` scans for
subdirectories that have a `rootfs/`. All other commands check for
`containers/<name>/rootfs` to validate existence.

### Legacy format migration

The old layout placed the rootfs at `installed-rootfs/<name>` (no
`containers/` wrapper, no manifest). `login` detects this on first use
and `os.rename`s it to `containers/<name>/rootfs` via
`_migrate_legacy_rootfs()`, then walks the new tree rewriting any l2s
symlinks whose targets still point at the old path. `LEGACY_ROOTFS_DIR`
is kept only for this path — no other command reads from it.

During the l2s rewrite, SIGINT and SIGQUIT are intercepted and replaced
with a warning (same pattern as `rename`) so the user cannot leave the
container in an inconsistent state by Ctrl-C.

## Distribution type: `normal` vs `termux`

Detected from the rootfs at login time:

- **`termux`**: `rootfs/data/data/com.termux/files/usr/bin/login` **file**
  exists (file check, not dir, to avoid false positives when proot
  creates the bind-mount target directory during a concurrent session).
- **`normal`**: anything else.

`termux`-type differences in `login`:

- No `--link2symlink`, no `--change-id`.
- Default `cwd` is `/data/data/com.termux/files/home`.
- Inner command is `/data/data/com.termux/files/usr/bin/login` exec'd
  directly (no `/usr/bin/env` wrapper).
- Guest env is hardcoded to `HOME`, `PATH`, `PREFIX`, `TMPDIR` for the
  Termux layout, plus host-inherited `TERM`/`COLORTERM` and any `--env`.
  Under `--minimal`, only `--env` and terminal vars are set.
- Android system bindings are always enabled; Termux `PREFIX` is not
  bound (the guest's own Termux prefix lives at the same path inside
  its rootfs).
- **Cross-architecture not supported**: if the container's arch differs
  from the host and QEMU emulation would be needed, login exits with an
  error. Host and container share the same Termux prefix path (`PREFIX`),
  so host binaries at that path would shadow the container's own.

## Commands and aliases

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
| `sync` | — |
| `run` | — |
| `build` | — |
| `help` | `h`, `he`, `hel` |

## `main()` flow

1. **SIGQUIT routing**: `signal.signal(SIGQUIT, …)` raises
   `KeyboardInterrupt` so every `except KeyboardInterrupt` block treats
   Ctrl-\ exactly like Ctrl-C (default SIGQUIT would otherwise terminate
   with a core dump and skip cleanup).
2. **Root warning**: when `os.getuid() == 0`, a yellow warning is
   printed (does not exit).
3. **Nested-proot rejection**: reads `/proc/<pid>/status`, follows
   `TracerPid` one level; if the tracer's `Name:` contains `"proot"`,
   exits with error.
4. **proot availability**: `shutil.which("proot")` is called. If absent
   and `IS_TERMUX` and stdin is a TTY, the user is prompted
   `Would you like to install it now? [y/N]`; answering `y`/`yes` runs
   `pkg install -y -q proot`. Otherwise the install hint is printed and
   the process exits. **The `build` command is exempt** from this
   probe — a Dockerfile without `RUN` builds in pure-Python mode and
   does not need proot. `command_build()` runs its own check after
   parsing the Dockerfile (see the `build` section).
5. **Top-level `--help`/`-h`/`help` short-circuit** → `command_help()`.
6. **Per-subcommand `--help`/`-h`/`--usage` intercept** runs **before**
   argparse parses positionals (so missing required positionals never
   produce errors instead of help).
7. **Unknown-subcommand check** runs **before** `parse_known_args()` so
   argparse's own `_SubParsersAction.error()` cannot fire first.
8. `parser.parse_known_args()` is used so unrecognised tokens do not
   terminate parsing. After `args.command` is determined, the remaining
   tokens are checked manually:
   - For `login` and `run`, anything after a literal `--` separator is
     the inner command and must not be flagged; the unknown list is
     recomputed from the slice before `--`.
   - Anything still unknown is reported as "unrecognized option"
     (starts with `-`) or "unexpected argument", with per-command help.
9. **Required-positional check** (`_REQUIRED_ARGS`): on failure, blank
   line, error, then the command's help text. `restore` is intentionally
   absent — it handles its own missing-input error inside the command
   because the decision depends on stdin TTY state (a piped archive is
   a valid invocation without a positional).
10. **`--` separator splitting**: populates `args.login_cmd` (for
    `login`) and `args.run_args` (for `run`).

## Container name validation

`_NAME_RE = ^[A-Za-z0-9][A-Za-z0-9_.\-]*$`. Applied to every container
identifier — `install`, `remove`, `rename` (both orig and new), `reset`,
`login`, `run`, `backup` — before any path is joined onto
`CONTAINERS_DIR`. `restore` applies the same regex to the container
name embedded in each archive member.

## Locking

`locking.py` provides advisory POSIX `flock(2)` locking so that
conflicting operations on the same container cannot run simultaneously.

**Lock file location**: `RUNTIME_DIR/locks/<name>.lock`. The directory
is created on first use. Lock files are never explicitly deleted — the
OS drops the flock automatically when the last fd to the file closes.

**Lock types**:

| Operation | Lock type |
|---|---|
| `install`, `restore`, `remove`, `rename`, `reset` | Exclusive |
| `copy`, `sync` (destination is a container) | Exclusive on dest |
| `copy`, `sync` (source is a container) | Shared on source |
| `backup`, `login`, `run` | Shared |

Multiple shared locks on the same name coexist freely (e.g. concurrent
`login` sessions, or `backup` while a session is open). An exclusive
lock blocks all other locks on the same name.

**Behaviour on conflict**: non-blocking (`LOCK_NB`). If the lock cannot
be acquired, the command exits immediately with a descriptive error that
includes the PID and operation of the holder (read from the lock file).
The command never waits.

**Filesystem fallback**: if the lock file cannot be created (e.g. the
filesystem does not support `flock`), `acquire()` returns `True` and
proceeds unlocked rather than refusing to run.

**Re-entrancy**: `_held_exclusive` (module-level `set`) tracks container
names this process currently holds exclusively. `command_reset` acquires
an exclusive lock and then calls `command_install` for the same name;
`install`'s `ContainerLock.acquire()` detects the name in
`_held_exclusive`, sets `self._reentrant = True`, and skips the flock
call. The `release()` of a re-entrant instance is a no-op.

**Login / run fd inheritance**: Python 3.4+ sets `O_CLOEXEC` on all
new fds (PEP 446), which would close the lock fd on `os.execvpe()`.
`ContainerLock(…, inheritable=True)` calls
`os.set_inheritable(fd.fileno(), True)` to clear `O_CLOEXEC` so proot
inherits the fd and holds the shared lock for the entire session.

**Multiple locks (copy / sync / rename)**: locks are built into a list
and acquired through `contextlib.ExitStack` in sorted name order. The
consistent ordering prevents theoretical lock ordering deadlocks.

**Restore lazy acquisition**: streaming archives (including stdin)
cannot be pre-scanned for container names. `command_restore` acquires
the exclusive lock per container on the first `TarInfo` header
encountered for that container, before any write. Locks are tracked in
a `pending_locks` dict and released in a `finally` block.

## Command-specific options

### `install`

Accepts either a Docker image reference (`ubuntu:24.04`, `alpine:3.21`,
`myuser/img:tag`, `ghcr.io/org/img:tag`) or a local archive path.
`_is_local_path()` returns True only when the arg starts with `/`,
`./`, `../`, or `~` — a bare token like `ubuntu` is always treated as
a Docker image, even if a file by that name exists in the cwd.

- `--name ALIAS` (stored as `args.custom_dist_name`) — custom local
  name. Hidden alias `--override-alias` shares the dest and is mutually
  exclusive. Empty `--name ''` is rejected.
- `--architecture ARCH` (stored as `args.override_arch`) — override
  target arch. `normalize_arch()` accepts native names (`aarch64`,
  `arm`, `i686`, `riscv64`, `x86_64`) or Docker platform strings
  (`linux/arm64`, `linux/amd64`, `linux/arm/v7`, `linux/386`,
  `linux/riscv64`).

### `login` (and `run`)

`login` always detects the architecture automatically from the
installed rootfs ELF binaries; falls back to `get_device_cpu_arch()` if
detection returns `"unknown"`. There is no architecture override for
login. `--emulator PATH` overrides the emulator binary (must exist and
be executable).

- `--user NAME` — guest user (default `root`).
- `--bind PATH[:PATH]` (repeatable) — source is resolved to absolute
  path via `os.path.abspath`. Destination, when given, **must be an
  absolute path** (relative dests are rejected, not just `.`/`..`). On
  overlap with an existing binding destination, a yellow warning is
  emitted but the bind is still added.
- `--shared-home` (alias `--termux-home`, mutually exclusive) — bind
  `TERMUX_HOME` into the container. For `termux`-type → 
  `/data/data/com.termux/files/home`. For `normal`-type → `/root` when
  user is root, else the user's `login_home`.
- `--shared-tmp` — bind `PREFIX/tmp:/tmp`. Skipped on non-Termux hosts
  and for `termux`-type.
- `--shared-x11` — bind `PREFIX/tmp/.X11-unix:/tmp/.X11-unix`. Skipped
  for `termux`-type and on non-Termux hosts. Independent of
  `--shared-tmp`. Active even under `--isolated` (skipped under
  `--minimal` because it sits inside the `not minimal` block).
- `--redirect-ports` (alias `--fix-low-ports`, mutually exclusive) —
  pass `-p` to proot.
- `--isolated`/`--minimal` (mutually exclusive, Termux only).
- `--no-link2symlink`/`--no-sysvipc`/`--no-kill-on-exit` (Termux only).
  `--no-kill-on-exit` emits a warning at runtime.
- `--kernel STRING`, `--hostname STRING`, `--work-dir PATH`,
  `--env VAR=VALUE` (repeatable).
- `--get-proot-cmd` — print the assembled `env` + proot command as a
  copy-pasteable shell command (backslash continuations, `_dq()`
  double-quoting) and exit 0.

### `remove`

`-v`/`--verbose` prints each removed entry in real time. Removes the
entire `containers/<name>` directory (rootfs + manifest).

### `backup`

- `--compress TYPE` (`gzip`/`bzip2`/`xz`/`none`) overrides extension
  detection. Unsupported compression extensions (`.tar.zst`, `.tzst`,
  `.tar.lz4`, `.tar.lz`) raise an error before any work.
- `--output FILE` writes to FILE instead of stdout.
- `-v`/`--verbose` logs each entry.

### `restore`

- `-v`/`--verbose` logs each extracted entry.
- No positional + non-TTY stdin → reads archive from stdin.
- No positional + TTY stdin → error.

### `copy`

- `-r`/`--recursive` allows directory copying (move mode ignores this).
- `-m`/`--move` uses `shutil.move`.
- `-v`/`--verbose`.

### `sync`

- `--checksum` — CRC32 (via `zlib.crc32`) instead of mtime+size.
- `--delete` — remove destination entries absent from source (only
  meaningful when source is a directory).
- `-v`/`--verbose` — also suppresses the progress bar.

### `clear-cache`

- `-v`/`--verbose` lists each removed entry.

### `build`

Builds an OCI/Docker image from a Dockerfile. Positional `PATH` is the
build context directory (default: `.`). The Dockerfile defaults to
`<PATH>/Dockerfile`; override with `-f/--file PATH` (use `-` to read
from stdin).

- `-t`/`--tag REF` — image reference to assign. Repeatable. Defaults
  to `<basename(PATH)>:latest`. Rejected when the derived basename
  doesn't satisfy `_NAME_RE`.
- `--build-arg K=V` — set a build-time `ARG`. Only `ARG`s declared in
  the Dockerfile are honoured; bare `--build-arg K` (no equals) reads
  the value from the host env. Repeatable.
- `--architecture ARCH` — target arch (proot-distro names or Docker
  `linux/...` strings). Default: host arch.
- `--target STAGE` — stop after the named stage of a multi-stage
  build. Validated against named stages found in the pre-scan.
- `--emulator PATH` — override the QEMU user-mode binary for
  cross-arch builds. Same semantics as in `login`.
- `--output FILE` — write a Variant B OCI tarball to FILE. Repeatable
  for multiple compressions; format inferred from extension
  (`.oci.tar`, `.oci.tar.gz`, `.oci.tar.xz`, …).
- `--install-as NAME` — after the build completes, install the image
  as a container named NAME. Calls `command_install()` with the
  primary tag; pull_image's offline-cache branch picks up the just-
  written manifest cache without a network probe.
- `--no-cache` — disable per-instruction build cache (lookups skipped,
  recordings still happen).
- `--pull` — re-pull base images even when the manifest cache is hit.
  Currently records the flag but has no effect (no force-pull path in
  pull_image yet); reserved for future use.
- `-v`/`--verbose` — echo each instruction's raw text and stream `RUN`
  output to the terminal.
- `-q`/`--quiet` — suppress non-error output.

## Isolation modes (`login` / `run`)

Three mutually exclusive modes on Termux:

| Mode | Behaviour |
|---|---|
| _(default)_ | Android system bindings, `/sdcard`, `/data/data/<termux-app>/...`, Termux `$HOME`, Termux `PREFIX` for `normal`-type, full proot extensions, fake `/proc` stubs. |
| `--isolated` | Drops Android system bindings, `/sdcard`, Termux app paths. Keeps `--link2symlink`, `--sysvipc`, `--kill-on-exit`, kernel-release, fake `/proc` stubs, baseline guest env, Termux bin in PATH. |
| `--minimal` | Only `/dev`, `/proc`, `/sys` are bound. No Android data, no `/sdcard`, no Termux PREFIX. `--sysvipc` disabled. No `setup_fake_sysdata()` or `fake_proc_bindings()`. No `--kernel-release`. Guest env: only `--env` + `TERM` (defaulting to `xterm-256color`) + inherited `COLORTERM`. Proot debug vars (`PROOT_NO_SECCOMP`, etc.) skipped; only `PROOT_L2S_DIR` is exported for `normal`-type on Termux. |

On non-Termux hosts neither flag is exposed by argparse.

## Colors

`colors.py` defines ANSI constants composed into `_COLORS`. `C` is set
to `_COLORS` when `sys.stderr.isatty()` and `PD_FORCE_NO_COLORS` is
unset; otherwise `_EMPTY`. Every color entry starts with `_RST` so
transitions implicitly reset.

`tty_safe_for_writes()` returns `False` when stderr is a TTY whose
termios state has `ECHO` or `ICANON` cleared — the signature of a
password prompt or curses UI on the same TTY (e.g. pinentry from
`proot-distro backup ubuntu | gpg -c …`). Returns `True` for non-TTY
stderr (file/pipe) and for TTYs in canonical+ECHO mode. Detection is
program-agnostic; no process names are matched. `msg()` consults this
and silently drops writes when False; `backup` and `restore` also gate
their progress-bar `sys.stderr.write` calls and `\r\033[K` clears on it
so destructive escapes never reach a sibling pinentry/curses display.

## Architecture and emulation

- Device arch detected via `os.uname().machine`. `armv7l`/`armv8l` are
  collapsed to `arm`.
- Installed arch detected by `detect_installed_arch(rootfs_path)` in
  `arch.py`: reads first 20 bytes of candidate ELF binaries, checks
  magic, reads endianness from `EI_DATA`, unpacks `e_machine` with
  `struct`, maps via `_ELF_MACHINE_MAP`. Accepts either a rootfs path
  or a bare container name (resolved as `CONTAINERS_DIR/<name>/rootfs`).
- 32-bit support on AArch64 probed via
  `ctypes.CDLL(None).personality(PER_LINUX32)`. Returns True on x86_64
  and on unknown machines.
- Cross-arch: proot `-q qemu-*`. `get_emulator_args()` binds Android
  system paths (`/apex`, `/linkerconfig/ld.config.txt`, `$PREFIX`,
  `/system`, `/vendor`, etc.) when present so QEMU can find loader/libs.
- 32-bit guests on 64-bit hosts run natively when supported
  (`arm` on `aarch64`, `i686` on `x86_64`).
- Proot's `--kernel-release` `uname_m` field is derived from the
  container's `target_arch` via `_ARCH_UNAME_M` in `login.py` (not from
  `os.uname().machine`), so emulated containers report the correct
  machine type. Falls back to `os.uname().machine` for unknown arches.

## Install: local archive

When the arg is a local path, `command_install()` calls
`_install_from_local_file(archive_path, rootfs_dir, dist_arch)`.

1. Opens the archive in streaming mode (`tarfile.open(..., 'r|*')`)
   and **probes the first 500 member names** to detect an `oci-layout`
   entry and feed `_detect_strip_count()`. Compressed archives only
   decompress the leading portion — fast even on multi-GB images.
2. If `oci-layout` is encountered → OCI path; otherwise → plain tar path.

**Plain tar** (`_extract_plain_tar`):

- Strip count: scores levels 0–4 by counting members whose first
  remaining component is in `_ROOTFS_DIRS` (standard rootfs dirs).
- Streaming extraction (`'r|*'`) — no in-memory member list.
- Hard links deferred and copied via `shutil.copy2`. Block/char devices
  and FIFOs silently skipped. Symlinks whose dest is already a real dir
  cause the dir to be `rmtree`'d first.
- Members whose post-strip path contains `..` or an empty component are
  dropped (anti-traversal).
- Directory mtimes set in reverse order after all writes. Directories
  get at least `S_IRWXU`. Progress via `_ByteCounter` on compressed
  bytes, total from `os.path.getsize()`.
- No `manifest.json` is written.

**OCI image layout** (`_extract_oci`):

- Re-opens the archive seekable (`'r:*'`) since layer blobs are
  accessed by digest in arbitrary order. Logs "Indexing OCI archive..."
  while `getmembers()` runs.
- Reads `index.json` → `_oci_find_manifest_entry()` (tries `platform`
  field; falls back to reading each config blob).
- Caches layer blobs into `LAYER_CACHE_DIR` via `_oci_cache_layer()`
  (atomic `.tmp` → `os.replace`).
- Applies layers in order via `_apply_layer()`.
- Returns a metadata dict (`manifest`, `image_config`, `image_ref`,
  `arch`, `env`). `command_install()` writes `manifest.json` from it.

Container name for local installs: `_derive_local_name()` strips known
archive extensions (`_ARCHIVE_EXTS` includes `.oci.tar`, `.oci.tar.gz`,
`.oci.tar.xz`, etc.), lowercases, sanitizes. Invalid result without
`--name` → error.

## Install: Docker/OCI registry pull

`command_install()` calls `pull_image()` from `helpers/docker.py`.

`pull_image(image_ref, rootfs_dir, arch)`:

1. Check manifest cache (`dlcache/manifests/<sha256-prefix>.json`).
   - Cached + all layers present → fully offline install.
   - Cached but some layers missing → fetch auth token, download
     missing layers.
   - Not cached → auth → manifest list → arch manifest → image config
     blob; saved to cache atomically.
2. Apply layers in order via `_apply_layer()`.
3. Whiteout semantics (OCI §6.1.2): `.wh..wh..opq` clears parent dir;
   `.wh.<name>` deletes the named sibling. Hard links copied
   (`shutil.copy2`), deferred until all regular files are written.
   Block/char devices and FIFOs silently skipped. Members containing
   `..` or empty components are dropped.

After `pull_image()` returns, `command_install()`:
- Always runs `setup_fake_sysdata`.
- If `/etc/` exists: `write_resolv_conf`, `write_hosts`.
- If `/etc/passwd` exists: `register_android_ids`.
- Writes `containers/<name>/manifest.json` with `image_ref`, `arch`,
  `manifest`, `image_config`.
- When the image config defines a non-empty `Entrypoint`, the install
  summary additionally prints `Run entrypoint: proot-distro run <name>`.

These post-install steps are silently skipped for images without
`/etc/` (distroless, scratch-based).

**Auth stripping**: Docker Hub blob endpoints redirect to CDN
pre-signed URLs that reject Bearer tokens (HTTP 400).
`_AuthStrippingRedirectHandler` drops `Authorization` on cross-host
redirects.

**Custom registry auth**: `_get_auth_token()` probes
`https://<registry>/v2/`. On `401` with `WWW-Authenticate: Bearer`,
`_parse_bearer_challenge()` extracts `realm` and `service` and fetches
an anonymous token from the realm URL. On `200`, an empty token is
used.

**Layer integrity**: `_download_blob()` streams through `hashlib.sha256`
and verifies the digest before `os.replace` promotes the temp file.
Only `sha256` digests are accepted. Cached layers are trusted (they
were verified on entry).

**zstd rejection**: layers whose `mediaType` contains `zstd` raise
`RuntimeError` before any download — Python's `tarfile` has no zstd
support.

`parse_image_ref(image_ref)` returns `(registry, repo, tag)`. Custom
registry is detected when the first path component contains `.` or `:`
(e.g. `ghcr.io/foo/bar:tag` → registry `ghcr.io`).

`derive_alias(image_ref)` picks the last path component, strips the
tag: `ghcr.io/foo/bar:latest` → `bar`.

Cache layout in `DOWNLOAD_CACHE_DIR`:

- `layers/<digest_with_colon_as_underscore>` — one file per blob.
- `manifests/<sha256-prefix>.json` — `{manifest, repo, image_config}`.
  Key is the first 16 hex chars of `sha256(<canonical_ref>_<arch>)`.

Architecture mapping (`_ARCH_TO_DOCKER` in `docker.py`):

| proot-distro | Docker | Variant |
|---|---|---|
| `aarch64` | `arm64` | — |
| `arm` | `arm` | `v7` |
| `i686` | `386` | — |
| `x86_64` | `amd64` | — |
| `riscv64` | `riscv64` | — |

## Login: passwd resolution

`_resolve_rootfs_path(rootfs, guest_path)` resolves an absolute guest
path to its host path by following symlinks within the rootfs
namespace. Absolute symlink targets are re-rooted under `rootfs` via
`os.path.normpath(target)` (prevents `..` escapes and handles Nix-style
images where `/etc/passwd` points to an absolute store path). The loop
runs at most 40 times; exceeding raises `OSError(ELOOP)`.

**Optional `/etc/passwd`**: for `normal`-type, when absent, `--user`
accepts only a numeric UID or the literal `root` (mapped to UID 0).
Other non-numeric names are rejected. UID = GID = the supplied number;
home defaults to `/root` (UID 0) or `/home/<uid>`.

**Shell availability check**: before `os.execvpe`, `login` resolves
`login_shell` within the rootfs and confirms it is a regular file. If
missing and the image config defines a non-empty `Entrypoint` or `Cmd`,
the error directs the user to `proot-distro run`. If neither shell nor
Entrypoint/Cmd exists, the error states so. Bypassed when
`args._run_inner` is set (i.e. invoked via `command_run`).

## Login: environment delivery

`command_login()` builds a clean `child_env` dict and passes it to
`os.execvpe("proot", proot_args, child_env)`. Proot inherits this dict
and propagates it to the spawned shell. There is **no `/usr/bin/env -i`
wrapper** — the shell is exec'd directly. The host environment is
**not** carried into the guest; only the entries below are exported.

**Precedence for `normal`-type, non-minimal** (later wins):

1. `PATH=<DEFAULT_PATH_ENV>`. On Termux, additionally
   `MOZ_FAKE_NO_SANDBOX=1`, `PULSE_SERVER=127.0.0.1`.
2. Image `Env` from `manifest.json` →
   `image_config["config"]["Env"]` via `_read_manifest_env()`. Skipped
   when `manifest.json` is absent (plain tarball install, legacy
   container). Keys in `_IMAGE_ENV_BLOCKED` are silently dropped.
3. Android system vars (`ANDROID_ART_ROOT`, `ANDROID_DATA`,
   `ANDROID_I18N_ROOT`, `ANDROID_ROOT`, `ANDROID_RUNTIME_ROOT`,
   `ANDROID_TZDATA_ROOT`, `BOOTCLASSPATH`, `DEX2OATBOOTCLASSPATH`,
   `EXTERNAL_STORAGE`) — only when `IS_TERMUX` and **not** `--isolated`.
4. User `--env` flags.
5. `HOME`, `USER`, `TERM` (defaulting to `xterm-256color`), `COLORTERM`
   (only if set on host) — always last.

`_IMAGE_ENV_BLOCKED` prevents image Env from overriding the Android
vars above, `MOZ_FAKE_NO_SANDBOX`, `PULSE_SERVER`, `TERM`, `COLORTERM`.
`PATH` is **not** blocked — images may define a base `PATH` but
`PREFIX/bin` is unconditionally appended afterward in non-isolated mode.

After building `child_env`, when `IS_TERMUX` and not `--isolated` and
not `--minimal`:

- `PREFIX/bin` is appended to `PATH` (deduplicating any existing
  occurrence) so Termux host tools are reachable from the guest.
- `_inject_termux_profile(rootfs, child_env)` writes a snippet to
  `rootfs/etc/profile.d/termux-profile.sh` that re-applies every
  login-time env var to login shells inside the container. The legacy
  filename `termux-prefix.sh` (PATH-only era) is unlinked first.
  Without this, `su - someuser` (which re-runs `/etc/profile` from a
  clean environment) would silently drop the proot-distro-set vars.

The snippet:

- POSIX `case`-guard appends `PREFIX/bin` to PATH only when missing (so
  the system PATH from `/etc/profile` keeps priority).
- Then emits `export KEY='VALUE'` for every entry in `child_env` except
  per-session vars (`HOME`, `USER`, `TERM`, `COLORTERM`), `PATH`, and
  proot-internal vars (`PROOT_NO_SECCOMP`, `PROOT_VERBOSE`, `PROOT_L2S_DIR`,
  `LD_PRELOAD`, `LD_LIBRARY_PATH`).
- Values single-quoted with the `'\''` idiom for safe round-tripping.

No-op when `/etc/profile.d/` does not exist.

**Minimal mode** clears almost all of the above: only `--env` entries
plus `TERM` (with `xterm-256color` fallback) and inherited `COLORTERM`
are exported. Image Env, Android system vars, the `PREFIX/bin` append,
and `_inject_termux_profile()` are all skipped.

**Proot toggle vars**: `PROOT_NO_SECCOMP`, `PROOT_VERBOSE` are inherited
from the host environment when non-empty. Skipped in `--minimal`.
`PROOT_L2S_DIR` is set to `rootfs/.l2s` for `normal`-type when
`IS_TERMUX` — the directory is always created upfront so concurrent
sessions agree on the same path. `LD_PRELOAD` is removed from `child_env`
before the exec.

## Login: proot command line assembly

Built in order in `command_login()`:

1. `proot` binary path (`shutil.which("proot")` or `"proot"`).
2. Emulator args from `get_emulator_args()` (empty when native).
3. On Termux: `--kill-on-exit` (unless `--no-kill-on-exit`, which also
   prints a warning).
4. On Termux + `normal`-type: `--link2symlink` (unless
   `--no-link2symlink`).
5. On Termux: `--sysvipc` (unless `--no-sysvipc` or `--minimal`).
6. On Termux (non-minimal):
   `--kernel-release=\Linux\<hostname>\<release>\<version>\<uname_m>\localdomain\-1\`.
7. On Termux: `-L` (fix lstat for dpkg warnings).
8. `normal`-type: `--change-id=<uid>:<gid>`.
9. `--rootfs=<rootfs>`, `--cwd=<wd>`.
10. `--bind=/dev --bind=/proc --bind=/sys`.
11. Non-minimal, `normal`-type, on Termux: `/dev/random` (urandom),
    `/dev/fd`, `/dev/std{in,out,err}`, fake `/sys/fs/selinux`, fake
    `/proc/*` bindings via `fake_proc_bindings()`, `/dev/shm` →
    `rootfs/tmp` (chmod 1777).
12. Non-minimal, not isolated, on Termux: `/data/app`,
    `/data/dalvik-cache`,
    `/data/misc/apexdata/com.android.art/dalvik-cache`,
    `/data/data/<TERMUX_APP_PACKAGE>/files/apps`, Termux app cache,
    `TERMUX_HOME`, storage bindings (`_storage_bindings()` —
    `/storage/...`, `/sdcard`).
13. Non-minimal, on Termux, when `termux`-type **or** not isolated
    **or** emulating: Android system bindings (`_system_bindings()` —
    `/apex`, `/odm`, `/product`, `/system`, `/system_ext`, `/vendor`,
    linker config files). Plus `--bind=$PREFIX` for `normal`-type.
14. Optional shared-home / shared-tmp / shared-x11 binds.
15. Custom `--bind` entries. `src` runs through `os.path.abspath`.
    Destination must be absolute (or omitted, in which case `src` is
    re-used). Overlap with an existing destination → yellow warning.
16. `-p` when `--redirect-ports`.
17. Inner command (shell or `_run_inner`).

`--get-proot-cmd` short-circuits exec: the assembled `env` + proot
command is printed (backslash continuations, `_dq()`-quoted) and exits 0.

## Run

`command_run()` runs the Entrypoint/Cmd from
`containers/<name>/manifest.json` → `image_config.config`. Delegates
entirely to `command_login()` after injecting the inner command via
`args._run_inner`. Every login option (`--user`, `--isolated`,
`--minimal`, `--bind`, etc.) is exposed for `run` and works unchanged.

| Manifest | After `--` | Inner command |
|---|---|---|
| `Entrypoint` + `Cmd` | _(none)_ | `Entrypoint + Cmd` |
| `Entrypoint` + `Cmd` | `ARGS` | `Entrypoint + ARGS` |
| Only `Cmd` | _(none)_ | `Cmd` |
| Only `Cmd` | `ARGS` | `ARGS` |
| Only `Entrypoint` | _(none)_ | `Entrypoint` |
| Only `Entrypoint` | `ARGS` | `Entrypoint + ARGS` |
| Neither | _(none)_ | Error |
| Neither | `ARGS` | `ARGS` |

`command_login()` checks `args._run_inner` (via `getattr`) in both the
`termux` and `normal` branches, bypassing shell wrapping when set.

**Working directory in `run`**: `command_run()` injects `args.work_dir`
before calling `command_login()` — uses the user's `--work-dir` if
given, else `WorkingDir` from image config, else `/` (not the user's
home, which is the `login`-mode default).

## Backup / Restore

Both use pure-Python `tarfile`. Archive structure: `<name>/`,
`<name>/manifest.json` (when present), `<name>/rootfs/...`.

**Backup** (`command_backup`):

- Source: `containers/<name>/` directory.
- `_fix_permissions()` is applied to `rootfs/` first so chmod-000'd
  subtrees become readable by owner (executables: `+rx`; others: `+r`).
- Compression: `--compress` first, then extension. Unsupported
  extensions (`.tar.zst`, `.tzst`, `.tar.lz4`, `.tar.lz`) raise
  `ValueError`.
- Tar mode: seekable file → `w:{comp}`; streaming stdout → `w|{comp}`.
- Entries filtered: block/char devices, FIFOs, sockets are silently
  skipped. Symlinks to directories are stored as single entries
  (`os.walk` is prevented from descending).
- uid/gid/uname/gname are zeroed.
- Progress bar on stderr (TTY only), throttled to 256 KiB via
  `_ReadCounter`. Total = sum of regular-file sizes (one pre-pass).
- Refuses to run when `--output` is absent and stdout is a TTY.
- CTRL-C: progress bar cleared, partial output removed.

**Restore** (`command_restore`):

- Source: file path arg **or** stdin (when no arg and stdin is
  non-TTY).
- File input: `tarfile.open(fileobj=counter, mode='r|*')` —
  auto-detects gzip/bzip2/xz/uncompressed. Wrapped in `_ByteCounter`
  for compressed-bytes-consumed progress (total = `os.path.getsize()`).
- Stdin input: `sys.stdin.buffer.peek(6)` for magic bytes;
  `_detect_compression()` maps to a mode suffix; `tarfile.open(...,
  mode='r|<x>')` is used. Progress shows a byte counter (no total).
- Archive routing via `_dest_path()`:
  - `<name>/manifest.json` → `containers/<name>/manifest.json`.
  - `<name>/rootfs/...` → `containers/<name>/rootfs/...`.
  - `<name>/anything_else` → treated as inside rootfs.
  - `installed-rootfs/<name>/...` (legacy) → `containers/<name>/rootfs/...`.
  - No subdir (bare root) → rejected with explicit error.
- Path traversal defence: any member containing `..`, `.`, or empty
  components is dropped; container name must match `_NAME_RE`.
- On first entry for a container, the existing rootfs is cleared with
  a `topdown=False` walk and a "Removing old rootfs... N files" counter.
- Hard links: resolved via `_dest_path()` and recreated with `os.link`.
- CTRL-C: progress bar cleared, no data removed.

**TTY-contention safety**: both commands gate every stderr write —
`msg()` calls, progress bars, "Removing old rootfs..." line, and every
`\r\033[K` clear — on `tty_safe_for_writes()`. When piping into an
interactive consumer (e.g. `gpg -c` showing pinentry), output is
suppressed entirely while the consumer holds the TTY in no-echo/raw,
then resumes when control returns. Piping into non-TTY consumers
(`gzip`, `cat`) is unaffected.

## Reset

`command_reset()` reads `image_ref` and `arch` from
`containers/<name>/manifest.json`, removes only the `rootfs/`
subdirectory (preserving the manifest), and calls `command_install()`
with a synthetic args object. Missing manifest or missing `image_ref`
→ error (reset is supported for OCI images only; plain tarball installs
lack a manifest).

## Clear cache

`command_clear_cache()` walks `DOWNLOAD_CACHE_DIR` to fix
read/execute permissions on every entry (`_ensure_readable()`), sums
sizes, then removes each top-level entry (`shutil.rmtree` for dirs,
`os.remove` for files). Reports total freed space. Per-entry failures
are logged but do not abort. `-v`/`--verbose` lists each removal.

## Rename

`command_rename()` validates both names with `_validate_name()`,
refuses a no-op (`orig == new`), `os.rename`s the container directory,
then walks the new rootfs and rewrites any l2s symlinks whose target
starts with the old `containers/<old>/rootfs` prefix.

During the rewrite, SIGINT and SIGQUIT are intercepted and replaced
with a warning ("Terminating now will leave link2symlink symlinks
broken. Please wait for the operation to complete.") so the user
cannot leave the container in an inconsistent state by Ctrl-C.

## Copy

`command_copy()` copies or moves files between host paths and container
rootfs paths (resolved via `dist:path` notation by
`_resolve_copy_path()`). Container paths resolve to
`containers/<name>/rootfs/<path>`.

- `-r`/`--recursive` is required for directory copying (move mode
  ignores it — `shutil.move` handles directories natively).
- `-m`/`--move` uses `shutil.move`.
- `-v`/`--verbose` logs each file.

## Sync

`command_sync()` synchronizes source → destination, skipping
already-up-to-date entries. Both paths support `dist:path` notation.
Always recursive.

- Comparison: size + integer mtime by default; size + CRC32 with
  `--checksum`. CRC32 is for speed (not security).
- Symlinks: copied as-is via `os.symlink`.
- Hard links: treated as independent regular files (no inode tracking).
- Block/char devices, FIFOs, sockets silently skipped.
- Ownership: never changed (`chown` is never called).
- Modes/mtime preserved (`os.chmod`, `os.utime`).
- Permission errors on source: warn and skip. On destination: `chmod`
  retry, then exit if it still fails.
- Atomic writes: regular files written to `.~pd_sync` temp then
  `os.replace`d.
- Progress bar (TTY-only stderr, `[*] [####----] XX%  N / Total files`)
  suppressed in `--verbose` mode (per-file log lines would clog output).
- `--delete`: after the sync pass, removes destination entries absent
  from source. Only active when source is a directory. Whole extra
  subtrees are captured as single `is_tree=True` entries (no descent).
  `_rmtree_robust()` for dirs, `_unlink_robust()` for files/symlinks
  (both retry with chmod on `PermissionError`).

## Build

`command_build()` is split across `commands/build.py` (orchestration)
and four helper modules:

| Module | Responsibility |
|---|---|
| `helpers/dockerfile.py` | Lex + parse Dockerfile text into instruction records. Variable expansion (`expand_vars()`) is a separate pass run by the engine, not by the parser. |
| `helpers/build_engine.py` | `BuildEngine` walks instructions, mutates an in-memory `image_config`, and emits layers via the diff helper. |
| `helpers/layer_diff.py` | `snapshot()` walks the rootfs and records fingerprints; `diff_snapshots()` returns added/modified/deleted; `write_layer_tar()` / `write_files_layer()` produce gzipped OCI layer tars. |
| `helpers/build_cache.py` | Recipe-hash → layer-digest index at `dlcache/buildcache/index.json`. |
| `helpers/oci_writer.py` | `store_in_cache()` (Variant A, manifest-cache write) and `write_oci_archive()` (Variant B, OCI image-layout tarball). |

### Parser (`helpers/dockerfile.py`)

`parse_dockerfile(text) → (directives, instructions)`. Pipeline:

1. Strip BOM, normalise CRLF/CR.
2. Collect `# key=value` parser directives at the top until the first
   non-directive line. Only `syntax`, `escape`, and `check` are
   recognised; only `escape` (`\` or `` ` ``) changes lexer behaviour.
3. For each logical line: skip blank/comment lines, join continuations
   on the trailing escape character (an odd number of trailing escapes
   is a continuation; an even number is literal).
4. Tokenise: first whitespace-delimited token is the instruction name
   (uppercased; rejected if not in `_INSTRUCTIONS`). Remaining text is
   the value. Leading `--flag[=value]` tokens before the value are
   parsed into `flags` dict, with quoted values stripped via `shlex`.
5. For `ADD`/`COPY`/`RUN`, scan the value for `<<TAG` / `<<-TAG`
   here-doc openers and consume body lines until the closing tag.
6. Try `json.loads` on the value to detect JSON exec form
   (`["echo", "hi"]`) — only when the parse yields a `list[str]`.
7. `ONBUILD` wraps an inner instruction record stored in `value`.

Each emitted record: `{name, flags, value, exec_form, heredocs,
lineno, raw}`.

`expand_vars(text, env)` handles `$VAR`, `${VAR}`, `${VAR:-default}`,
`${VAR-default}`, `${VAR:+value}`, `${VAR+value}`. `None` in `env`
means "unset" (relevant for `:-` vs `-`). A leading `\` escapes the
following character (so `\$FOO` is literal).

### Engine (`helpers/build_engine.py`)

`BuildEngine.run(instructions)`:

1. **Pre-scan** for global ARGs (declared before the first FROM) and
   for named stages. Validates `--target` against the named-stage set
   *before* any work.
2. **Per-instruction dispatch** via `_HANDLERS`. Variable expansion
   runs only for instructions in `_EXPANDS_VARS` and only when not in
   exec form. `FROM` is handled separately because its value expands
   against `global_args` (not stage-local ARGs).
3. **Stop after target**: when a new `FROM` is encountered and the
   *current* stage matches `--target`, `_stop_after=True` and the loop
   breaks before the new stage's rootfs is created.

`Stage` holds: index, optional name, `rootfs_dir` (under `tmp_root`),
in-progress `image_config`, ordered `layers` list, `parent_layer_digest`,
live `env`/`args`/`declared_args`/`workdir`/`user`/`shell` state, and
pending `onbuild` list.

#### FROM

- `FROM scratch`: empty rootfs, empty `image_config`.
- `FROM <named-stage>`: re-apply that stage's layers via `_apply_layer`
  to a fresh rootfs (fast — blobs are already in `LAYER_CACHE_DIR`),
  inherit its `image_config`.
- `FROM <image-ref>`: delegate to `helpers/docker.pull_image()`. The
  returned manifest's layer list is converted into `stage.layers` with
  digest+size+diff_id (diff_ids come from `image_config.rootfs.diff_ids`,
  falling back to digest when missing).
- After the rootfs is populated, `write_resolv_conf` and `write_hosts`
  fill in DNS so RUN can reach the network.
- Base image's `OnBuild` triggers fire here: each trigger string is
  re-parsed via `parse_dockerfile()` and dispatched as if it were a
  Dockerfile instruction in the new stage.

#### RUN

1. Build the command list:
   - exec form → bare `instr["value"]`.
   - shell form → `stage.shell + [value]`.
   - shell form with here-doc(s) → `stage.shell + [body]` where body
     is the concatenation of every here-doc body (passed as a single
     `-c` argument to the SHELL).
2. Compute the recipe hash (`build_cache.compute_recipe_hash`) over
   `parent_layer_digest + instr + extra_inputs` where `extra_inputs`
   is the sorted env+ARG state.
3. On cache hit (unless `--no-cache`), apply the cached layer via
   `_apply_layer` and append to `stage.layers`; skip the proot exec.
4. On miss: snapshot the rootfs, exec proot, snapshot again, diff
   (`diff_snapshots`), write the gzipped layer tar
   (`write_layer_tar`), move it to `LAYER_CACHE_DIR/<digest>`,
   append to `stage.layers`, record in the build cache.

The proot command line mirrors `commands/login` for `normal`-type
non-Termux-host containers, with two intentional differences:

- **Always isolated**: no Android storage bindings, no `--shared-tmp`,
  no `--shared-home`. The host's filesystem must not leak into the
  built image.
- **Non-interactive**: stdin is `/dev/null` (or the here-doc body),
  stdout/stderr stream to the parent. Exit code is propagated; non-
  zero raises `BuildError` and the build aborts.

Cross-arch builds re-apply the same QEMU-binding logic from
`helpers/arch.get_emulator_args()` and bind Android system paths so
QEMU can find its loader.

**link2symlink handling**: on Termux, RUN runs with proot's
`--link2symlink` extension, which emulates a `link()` syscall by
stashing the original file under `<rootfs>/.l2s/<hash>` and leaving
symlinks at the original locations pointing at the stash. Those
symlink targets are absolute paths into the build's tmp rootfs and
would dangle the moment the image is applied somewhere else. To keep
the produced layer self-contained:

- `snapshot()` in `helpers/layer_diff.py` skips the `.l2s/` directory
  entirely, so it never appears in any layer.
- `_add_entry()` detects symlinks whose target resolves into
  `<rootfs>/.l2s/` (via `_resolve_l2s_target`) and packs the backing
  file's content as a regular file instead of the symlink. Multiple
  endpoints of the same hard link become independent regular files —
  the hard-link semantics are lost but file content is preserved and
  the layer is portable.

#### COPY / ADD

Both go through `_do_copy_or_add`. Sources resolve relative to
`build_dir`. A leading `/` on the source is **stripped** before
joining (`COPY /foo` and `COPY foo` are equivalent, matching Docker
semantics). After normalisation, any path that escapes `build_dir`
via `..` segments is rejected with a "escapes the build context"
error. Glob expansion is supported via `_simple_glob`
(`fnmatch`-based). `.dockerignore` patterns are applied before each
source is packed.

`--from=<stage>` swaps the source root to that stage's `rootfs_dir`.
`--from=<external image>` (any value not matching a named stage) is
handled by `_pull_throwaway_image()`, which pulls the image into a
unique `tmp_root/copyfrom-<hash>` directory and uses it as the source
rootfs.

`--chown=user[:group]` is resolved against the target rootfs's
`/etc/passwd` / `/etc/group` (not the host's). Numeric IDs pass
through directly. `--chmod=octal` overrides file modes.

For `ADD`: local file sources that pass `_is_tar_archive` (uses tar
magic at offset 257, plus gzip/bzip2/xz magic at offset 0) are
auto-extracted into the destination directory. URL sources
(`http://`, `https://`) are downloaded via the shared
`_AuthStrippingRedirectHandler` and stored as a single file.

Each `COPY`/`ADD` emits exactly one layer, built from a
`file_map` (`arcname → entry dict`) via `write_files_layer`. The same
file_map is materialised into the rootfs by `_materialise_files`, so
the on-disk state and the layer stay in sync.

#### Metadata-only handlers

`ENV`, `LABEL`, `MAINTAINER`, `USER`, `EXPOSE`, `VOLUME`,
`STOPSIGNAL`, `SHELL`, `HEALTHCHECK`, `ONBUILD` mutate
`image_config["config"]` only — no layer is produced. `WORKDIR` is
the exception: it both mutates `WorkingDir` *and* emits a thin layer
holding any newly-created ancestor directories, so the path also
exists after the layer is applied to a fresh rootfs by `install`.

`ARG` is build-time only — values are tracked in `stage.args` (with
fallback chain: `--build-arg` → declared default → global ARG when
re-declared bare → `_PREDEFINED_ARGS` from host env → empty string).
They are **not** written to `image_config.Env`, but they *are*
visible to `RUN` commands via `_build_child_env`.

### Proot-requirement gate

`build_engine.needs_proot(instructions)` returns `True` iff any
instruction (or any `ONBUILD <inner>`) has `name in
PROOT_REQUIRED_INSTRUCTIONS` (currently just `{"RUN"}`).
`command_build()` calls this after parsing and before any work; if
the result is `True` and `shutil.which("proot") is None`, the build
is refused.

The CLI startup probe in `cli.py:main()` is bypassed for `build` so a
no-RUN Dockerfile can build on a host without proot. The bypass is
implemented as a single-line condition that consults
`_ALIAS_TO_CANONICAL` early.

### Build cache

`helpers/build_cache.compute_recipe_hash(parent, instr, extra_inputs)`
hashes the parent layer digest plus the canonical form of the
instruction plus per-instruction extras (the sorted env+ARG scope for
RUN; not used by COPY/ADD in v1). The index file lives at
`$DOWNLOAD_CACHE_DIR/buildcache/index.json` and stores
`{recipe_hash → {layer_digest, diff_id, size, image_config_patch,
created}}`. `clear-cache` does **not** automatically delete the build
cache index — it lives next to `layers/` and `manifests/` and is
cleaned up by the same operation if the user passes `clear-cache`,
because the index entries become orphans once the layer blobs are
gone.

### Output

`commands/build.py` always writes Variant A (manifest cache) via
`oci_writer.store_in_cache()`. This is cheap — the layer blobs are
already in `LAYER_CACHE_DIR`, so only the small `manifests/<key>.json`
file is added. `pull_image()`'s existing manifest-cache-first ordering
guarantees that a subsequent `install <tag>` returns offline.

When `--output FILE` is given, `oci_writer.write_oci_archive()` emits
a tarball that contains **both** the standard OCI image layout
(`oci-layout`, `index.json`, `blobs/sha256/<hex>`) **and** a
Docker-legacy `manifest.json` at the archive root (with `Config`,
`RepoTags`, `Layers`, and `LayerSources` fields pointing at the same
`blobs/sha256/<hex>` paths). The legacy entry is required because
`docker load` falls back to a per-directory legacy-import loop when
only OCI files are present and then mis-treats the `blobs/`
directory as an image folder. `oci-layout` is still written first so
our own install probe in `commands/install.py:_install_from_local_file`
detects the OCI format on the first tar member it reads. Layer
blobs are copied from `LAYER_CACHE_DIR` into the archive. The format
is consumable by both `docker load` and `proot-distro install
/path/to/file.oci.tar`.

`--install-as NAME` invokes `command_install` with the primary tag as
the image reference. Because the manifest cache was just written,
`pull_image()` resolves the tag offline and applies the cached
layers without a network probe.

### Interrupt handling

`command_build` wraps the whole post-setup flow — `engine.run()`,
`store_in_cache()`, every `write_oci_archive()`, and any
`--install-as` install — in a single `try / except KeyboardInterrupt
/ finally` block. On Ctrl-C (or SIGQUIT, since `cli.py:main()`
re-routes SIGQUIT to `KeyboardInterrupt`), the user sees
`Error: Aborted by user.` instead of a Python traceback and the
`tmp_root` staging directory is always removed via the `finally`
clause. Partial output files are taken care of one level lower:

- `write_oci_archive()` writes to `<out>.tmp` and removes it on
  `BaseException`.
- `write_layer_tar()` / `write_files_layer()` use a `NamedTemporaryFile`
  for the uncompressed tar and a `<out>.tmp` for the gzipped output;
  both are removed on `BaseException`.
- `_download_blob()` in `helpers/docker.py` follows the same
  `<dest>.tmp` + `BaseException`-cleanup pattern for layer pulls.

The result: nothing under the user's destination paths is left in a
half-written state after an interrupt, and the local cache only
ever contains content-verified, fully-written blobs.

### Default tag

When no `-t/--tag` is given, the tag defaults to
`<basename(PATH)>:latest` (or the parent of `--file`'s argument when
PATH was not supplied). The basename is lowercased and sanitised; if
it cannot be made to satisfy `_NAME_RE`, the user is required to
pass `--tag` explicitly.

## Fake sysdata

On install and on every login of a `normal`-type container (except
`--minimal`), fake `/proc` and `/sys` stub files are written **inside
the container's own rootfs** (`rootfs/proc/.loadavg`, `…/.stat`,
`…/.uptime`, `…/.version`, `…/.vmstat`, `…/.sysctl_entry_cap_last_cap`,
`…/.sysctl_inotify_max_user_watches`, `…/sys/.empty`) and bind-mounted
by proot over `/proc/*` and `/sys/fs/selinux`. Storing them inside the
rootfs keeps them co-located so `remove` cleans them up automatically.

Constants `_FAKE_LOADAVG`, `_FAKE_STAT`, `_FAKE_UPTIME`, `_FAKE_VMSTAT`
are hardcoded in `sysdata.py`. `_write_if_missing()` avoids overwriting
existing files. `fake_proc_bindings()` probes each real `/proc/*` path
on the host and emits a fake binding only for entries that fail to
read (Android-restricted). `termux`-type containers do not get fake
sysdata — they share the host's `/proc`/`/sys` via `--bind=/proc` /
`--bind=/sys`.

## Subcommand help

Help content lives in `_HELP_PAGES` (structured page data) in
`commands/help.py`. `_render_page()` formats it against the detected
terminal width:

- `_term_width()` tries stderr/stdout/stdin in turn, then `$COLUMNS`,
  then `shutil.get_terminal_size((72, 24))`, clamped to
  `[_MIN_WIDTH=32, _MAX_WIDTH=92]`.
- Layout switches at `_NARROW_BREAKPOINT=60`: below, option names and
  descriptions stack vertically (phones running Termux); above, a
  two-column grid is used.
- Sections rendered when present: USAGE, DESCRIPTION, OPTIONS,
  EXAMPLES, plus footer blocks (HOST BINDINGS, NOTES, etc.).
- Top-level `command_help()` adds QUICK START, DATA LOCATION (prints
  `RUNTIME_DIR`), and TROUBLESHOOTING (mentions `PD_FORCE_NO_COLORS`
  and `PROOT_NO_SECCOMP`).

`_HELP_COMMANDS = {name: _make_help_fn(name) for name in _HELP_PAGES}`
gives every command a renderable callable. Subcommand
`--help`/`-h`/`--usage` is intercepted in `main()` before argparse
parses positional arguments. Conditional option entries in
`_HELP_PAGES` (e.g. `*([(...)] if IS_TERMUX else [])`) keep help text
in sync with the available flags on the current host.

## Pure Python policy

`proot_distro/` avoids spawning subprocesses for system queries:

- Colors: ANSI constants, no `tput`.
- User/group info: `pwd.getpwuid()` / `grp.getgrgid()`, no `id`.
- ELF arch detection: `struct.unpack` on raw bytes, no `file`.
- 32-bit support: `ctypes` `personality()`, no `lscpu`.
- OCI registry access: `urllib.request`, no `docker`/`curl`.
- Layer extraction / backup / restore: `tarfile`, no `tar`.
- Terminal width: `os.get_terminal_size()` / `shutil.get_terminal_size()`,
  no `stty`.

The only external commands invoked at runtime are `proot` (via
`os.execvpe`) and — at install time, only when prompted —
`pkg install -y -q proot` on Termux.
