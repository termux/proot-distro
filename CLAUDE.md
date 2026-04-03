# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This repository contains two Python tools for the [proot-distro](https://github.com/termux/proot-distro) project:

- **`bootstrap-rootfs.py`** — builds Linux rootfs tarballs from YAML build recipes (requires root)
- **`proot-distro.py`** — manages installed proot distributions on Termux (runs as normal user)

**Dependency:** both scripts require PyYAML. `proot-distro` is pip-installable (published on PyPI at https://pypi.org/project/proot-distro/); `bootstrap-rootfs.py` requires a manual `pip install pyyaml`.

---

## bootstrap-rootfs.py

Builds rootfs tarballs suitable for use with proot-distro. Requires root (uses `unshare`, bind mounts, `chroot`).

```bash
sudo python3 bootstrap-rootfs.py build-recipes/alpine.yaml
sudo python3 bootstrap-rootfs.py build-recipes/alpine.yaml --arch aarch64
sudo python3 bootstrap-rootfs.py build-recipes/alpine.yaml -o /output/dir
sudo python3 bootstrap-rootfs.py build-recipes/alpine.yaml --generate-pd-config --pd-config-base-url https://example.com/proot-distro/v5.0.0
```

`--help` works without root. `--generate-pd-config` writes a ready-to-use proot-distro config YAML alongside the tarballs, including SHA-256 checksums.

### Build recipe YAML schema

| Field | Required | Notes |
|---|---|---|
| `version` | yes | Version string; supports `${version:offset:length}` Bash-style slices in URLs |
| `architectures` | yes | List of arch entries (see below) |
| `name` | no | Human-readable name |
| `name_slug` | no | Used in output filename; defaults to `rootfs` |
| `description` | no | Used in generated proot-distro config |
| `bootstrap_method` | no | `rootfs` (default), `mmdebstrap`, or `oci_container` |
| `chroot_script` | no | Shell script run inside the built rootfs |
| `post_install_automation` | no | Passed through to generated proot-distro config |
| `mmdebstrap_variant` | no | Default: `minbase` |
| `mmdebstrap_components` | no | Default: `main` |
| `mmdebstrap_include_pkgs` | no | Omit to skip `--include` entirely |

**`architectures` format by bootstrap method:**

- `rootfs` / `oci_container`: list of single-key dicts `{arch: url_template}`
- `mmdebstrap`: list of plain strings (no URLs needed)

URL templates support `${version}`, `${version:offset:length}`, and `${architecture}` placeholders. `${architecture}` expands to the YAML architecture name (e.g. `aarch64`, `armv7`, `x86_64`). Inside `chroot_script`, the same value is available as the `DISTRIBUTION_ARCH` env var.

### Build pipeline

1. **Bootstrap** — method-specific (download tarball / mmdebstrap / OCI layers)
2. **resolv.conf** — symlinks replaced with a plain file containing `nameserver 1.1.1.1`
3. **chroot script** — runs under `unshare -mpf` (single namespace session; mounts cleaned on exit); rootfs is bind-mounted onto itself (required by pacman)
4. **Repack** — output: `{name_slug}_{version}_{deployed_arch}_rootfs.tar.xz`

Temp dir: `/tmp/distro_build/{name_slug}-{arch}-{version}/`, removed on completion.

### Architecture name mapping

Raw YAML arch names are used as-is internally (URLs, mmdebstrap, OCI matching, `DISTRIBUTION_ARCH`). Output filenames use canonical deployed names via `_DEPLOYED_ARCH_MAP`:

| Canonical | Raw names accepted |
|---|---|
| `aarch64` | `aarch64`, `arm64`, `arm64v8` |
| `arm` | `arm`, `armel`, `armhf`, `armhfp`, `armv7`, `armv7l`, `armv7a`, `armv8l` |
| `i686` | `386`, `i386`, `i686`, `x86` |
| `x86_64` | `amd64`, `x86_64` |
| `riscv64` | `riscv64` |

OCI architecture matching uses a separate `_OCI_ARCH_MAP` (e.g. `aarch64`→`arm64`, `x86_64`→`amd64`).

### Build recipes

Located in `build-recipes/`. Current recipes: `alpine.yaml` (rootfs), `archlinuxarm.yaml` (rootfs), `debian.yaml` (mmdebstrap), `fedora.yaml` (oci_container), `rocky.yaml` (oci_container), `ubuntu.yaml` (mmdebstrap).

### CI: GitHub Actions

`.github/workflows/build-rootfs.yml` builds rootfs tarballs on GitHub-hosted runners.

**Triggers:**

- `workflow_dispatch` — manual run; required string input `distributions` is a space-separated list of recipe names without extension (e.g. `alpine debian rocky`).
- `push` — automatic; fires when any `build-recipes/*.yaml` file is modified.

**Jobs:**

1. `prepare` — determines which distributions to build and emits a JSON matrix. For `workflow_dispatch` it parses the input; for `push` it diffs `github.event.before`..`github.sha` over `build-recipes/*.yaml` and extracts the changed names (builds everything on first push to a branch). Skips `build` entirely when the matrix is empty.
2. `build` — runs once per distribution in parallel (`fail-fast: false`). Installs `pyyaml` and `mmdebstrap`, runs `sudo python3 bootstrap-rootfs.py build-recipes/<distro>.yaml -o rootfs`, then uploads `./rootfs/` as artifact `rootfs-<distro>`.

---

## proot-distro.py

Python rewrite of the legacy `proot-distro` Bash script. Manages proot-based Linux distribution containers on Termux. The entry point `proot-distro.py` is a thin shim; all logic lives in `proot_distro/`.

### Installation

```bash
pip install proot-distro        # from PyPI (https://pypi.org/project/proot-distro/)
pip install .                   # from local source
pip install -e .                # editable install for development
```

The package requires Python ≥ 3.9 and PyYAML (declared in `pyproject.toml`). The `proot-distro` console script entry point resolves to `proot_distro.cli:main`. `pyproject.toml` is the single source of truth for the version string; `PROGRAM_VERSION` in `constants.py` reads it at runtime via `importlib.metadata.version("proot-distro")`.

The wheel is built with `setuptools>=77` and targets Metadata-Version 2.4. Uploading to PyPI requires twine ≥ 6.2.0 (`pip install "twine>=6.2.0"`).

### Running directly (without pip)

```bash
./proot-distro.py list
./proot-distro.py install alpine
./proot-distro.py login alpine
./proot-distro.py login alpine --user myuser -- bash -c "echo hello"
./proot-distro.py remove alpine
./proot-distro.py --help
./proot-distro.py login --help
```

### Module layout (`proot_distro/`)

| Module | Contents |
|---|---|
| `constants.py` | `PROGRAM_VERSION` (read from installed package metadata via `importlib.metadata`; falls back to `"rolling"`), `PROGRAM_NAME` (`"proot-distro"`, hardcoded), all path/default constants |
| `colors.py` | ANSI escape constants, `C` (color dict), `msg()`, `show_version()` |
| `config.py` | `ArchEntry`, `DistroConfig`, `load_config()`, `discover_configs()`, `_ensure_config()`, `_ensure_all_configs()`, `is_bundled_config()`, `config_file_for_alias()`, `_write_yaml()`, `expand_url()`; `_BUNDLED_CONFIGS_DIR` points to `proot_distro/configs/` |
| `arch.py` | CPU arch detection, ELF-based installed arch detection, QEMU/emulator helpers |
| `sysdata.py` | Fake `/proc`/`/sys` constants, `setup_fake_sysdata()`, `fake_proc_bindings()` |
| `download.py` | `sha256_file()`, `download_file()` — both with TTY progress bars |
| `rootfs.py` | Install helpers: `_write_environment()`, `_write_resolv_conf()`, `_write_hosts()`, `_register_android_ids()`, `_run_post_install()` |
| `commands/install.py` | `command_install()`, `_validate_alias()`, `_detect_archive_type()`, `_extract_zip()`, `_extract_tar()`, `_stripped_name()`, `_make_dir()`, `_make_symlink()`, `_install_termux_rootfs()` |
| `commands/remove.py` | `command_remove()`, `_remove_path()` |
| `commands/rename.py` | `command_rename()` |
| `commands/reset.py` | `command_reset()` |
| `commands/login.py` | `command_login()` and its private helpers |
| `commands/list.py` | `command_list()` |
| `commands/backup.py` | `command_backup()`, `_compression_mode()`, `_COMPRESSION_ARG_MAP`, `_iter_entries()`, `_add_path()` |
| `commands/restore.py` | `command_restore()`, `_detect_compression()`, `_route()`, `_remove_existing()` |
| `commands/clear_cache.py` | `command_clear_cache()` |
| `commands/copy.py` | `command_copy()`, `_resolve_copy_path()` |
| `commands/help.py` | `command_help()`, `_HELP_COMMANDS` |
| `cli.py` | `build_parser()`, `_ALIAS_TO_CANONICAL`, `_COMMAND_HANDLERS`, `_REQUIRED_ARGS`, `main()` |

### Key paths (Termux defaults)

| Variable | Default |
|---|---|
| `PREFIX` | `/data/data/com.termux/files/usr` |
| Config dir | `$PREFIX/etc/proot-distro/` |
| Installed rootfs | `$PREFIX/var/lib/proot-distro/installed-rootfs/` |
| Download cache | `$PREFIX/var/lib/proot-distro/dlcache/` |

### Distribution config YAML schema (proot-distro format)

Generated by `bootstrap-rootfs.py --generate-pd-config`. Also written manually for `proot_distro/configs/`.

```yaml
name: "Alpine Linux"
description: "..."
version: "3.23.3"
type: "normal"          # optional; "normal" (default) or "termux"
tarball_strip: 0        # optional; strip leading path components from archive

architectures:
  - aarch64: "https://...${version}...${architecture}..."
    checksum: "sha256hexdigest"
  - arm: "https://..."
    checksum: "sha256hexdigest"

post_install_automation: |
  echo "Hello World"
```

URL templates support `${version}`, `${version:offset:length}`, and `${architecture}` placeholders (expanded by `expand_url()` in `config.py`).

### Config discovery and on-demand population

`PD_CONFIGS_DIR` (`$PREFIX/etc/proot-distro/`) is the **sole** runtime source for distribution configs. Only `*.yaml` files are recognised; no override mechanism exists. Bundled configs in `proot_distro/configs/` are never read directly at runtime; instead they are copied into `PD_CONFIGS_DIR` on demand:

- `_ensure_config(alias)` — copies `proot_distro/configs/{alias}.yaml` to `PD_CONFIGS_DIR` if absent. Called by `cli.py` before `discover_configs()` for any command that takes a distro alias.
- `_ensure_all_configs()` — copies every bundled config that is not yet present. Called before `list` and `help` (which need all configs).
- `is_bundled_config(alias)` — returns `True` if `proot_distro/configs/{alias}.yaml` exists. Used by `remove` and `rename` to decide config file handling.

`install --name ALIAS` creates `ALIAS.yaml` in `PD_CONFIGS_DIR` as a plain copy of the source distribution's config (no name mangling). An empty `--name ''` is rejected immediately with an error. On failure during install, the created config file is removed. `remove` deletes `alias.yaml` only for non-bundled distributions (bundled configs remain for future installs), and only after the rootfs has been fully removed. `rename` copies `orig.yaml` → `new.yaml` for bundled originals (preserving the bundled file), and moves (copy + delete) for non-bundled ones.

### Distribution type: `normal` vs `termux`

The optional `type` field in a config YAML selects the distribution type; it defaults to `"normal"`.

**`type: termux`** — used for the official Termux bootstrap. Differences from `normal`:

- **Install** (`_install_termux_rootfs`): archive is extracted into `rootfs/data/data/com.termux/files/usr/` (not rootfs root) using `_extract_zip` or `_extract_tar`; `SYMLINKS.txt` is processed (separator U+2190 `←`); PS1 is appended to `bash.bashrc`.
- **Archive format**: Termux bootstrap ships as a ZIP (detected by `PK\x03\x04` magic); regular distros use tar. Both formats are supported for both types. No `proot --link2symlink` or `.l2s` directory is involved; extraction is pure Python.
- **Login**: no `--link2symlink`, no `--change-id`; working directory defaults to `/data/data/com.termux/files/home`; inner command is `/data/data/com.termux/files/usr/bin/login`; Android system bindings are always enabled; Termux `PREFIX` is not bound into the guest.

### Commands and aliases

| Command | Aliases |
|---|---|
| `install` | `add`, `i`, `in`, `ins` |
| `remove` | `rm` |
| `rename` | `mv` |
| `reset` | — |
| `login` | `sh` |
| `list` | `li`, `ls` |
| `backup` | `bak`, `bkp` |
| `restore` | — |
| `clear-cache` | `clear`, `cl` |
| `copy` | `cp` |
| `help` | `h`, `he`, `hel` |

### proot availability check

`main()` calls `shutil.which("proot")` early. If proot is absent and stdin is a TTY, the user is prompted `Would you like to install it now? [y/N]`; answering `y`/`yes` runs `pkg install -y -q proot` via `subprocess.run`. On a non-TTY stdin the install hint is printed and the process exits. A `FileNotFoundError` from `pkg` (i.e. not running on Termux) is caught and reported.

### Argument validation

All required positional arguments are declared `nargs='?'` in argparse so that missing arguments are caught manually rather than by argparse. `_REQUIRED_ARGS` in `cli.py` maps each canonical command to a list of `(arg_name, error_message)` pairs checked in order before dispatch. On failure: blank line, error message, then the command's help text (via `_HELP_COMMANDS`). `restore` handles its own missing-input error inside `command_restore()` (depends on stdin TTY state).

`install` accepts `--name ALIAS` (stored as `args.custom_dist_name`) to install a distribution under a different alias. An empty string is rejected before any other validation. Additional options: `--architecture ARCH` (stored as `args.override_arch`; choices: `aarch64`, `arm`, `i686`, `riscv64`, `x86_64`) overrides the architecture selected for install (defaults to device arch); `--url URL` overrides the tarball download URL (stored as `args.override_url`); `--checksum SHA256` overrides the integrity checksum (stored as `args.override_checksum`; when `--url` is given without `--checksum` the checksum is left empty, disabling verification); `--strip-path-components N` overrides `tarball_strip` from the config YAML (stored as `args.override_strip`), equivalent to tar's `--strip-components`, must be non-negative. `login` always detects architecture automatically from the installed rootfs ELF binaries; there is no architecture override for login. `login --emulator PATH` (stored as `args.emulator`) overrides the emulator binary used for cross-arch execution; the path must exist and be executable, bypassing default QEMU selection and native-run checks.

`remove` accepts `-v`/`--verbose` to print each removed file and directory in real time as they are deleted.

`login` accepts `--bind PATH[:PATH]` (repeatable). The source component is always resolved to an absolute path via `os.path.abspath` (so `.` and `..` are expanded). The destination component must not be `.` or `..` — such values are rejected with an error before proot is invoked. Options like `--bind` are parsed correctly regardless of whether `alias` appears before or after them on the command line (`login_cmd` uses `nargs='*'`, not `REMAINDER`).

`list` accepts `--detailed` to show full per-distribution info (name, alias, version, installed status, description, architectures); without it, only `name < alias >` lines are printed.

`backup` accepts `--compress TYPE` (choices: `gzip`, `bzip2`, `xz`, `none`; stored as `args.compression`) to force a specific compression algorithm, overriding extension-based detection. Applies to both file and stdout output.

### Colors

`proot_distro/colors.py` defines ANSI escape sequence constants (`_RST`, `_BOLD`, `_ITALIC`, `_RED`, `_GREEN`, `_YELLOW`, `_BLUE`, `_CYAN`) composed into a `_COLORS` dict. No external tools are used. `C` is set to `_COLORS` when `sys.stderr.isatty()` and `PD_FORCE_NO_COLORS` is unset, otherwise `_EMPTY` (all-empty strings). Every color entry starts with `_RST` so color transitions implicitly reset attributes.

### Architecture and emulation

- Device arch detected via `os.uname().machine`
- Installed distro arch detected by `_elf_arch()` in `arch.py`: reads the first 20 bytes of candidate ELF binaries, checks the magic, reads endianness from `EI_DATA` (byte 5), unpacks `e_machine` (bytes 18–19) with `struct`, and maps it via `_ELF_MACHINE_MAP`; candidate paths include `/data/data/com.termux/files/usr/bin/bash` for termux-type distros
- 32-bit support on AArch64 probed via `ctypes.CDLL(None).personality(PER_LINUX32)` (same technique as lscpu); x86-64 always returns `True`
- Cross-arch: proot `-q qemu-*` (QEMU user-mode); a custom emulator binary can be forced via `login --emulator PATH`
- 32-bit guests on 64-bit hosts (arm on aarch64, i686 on x86_64) run natively when supported

### Downloads

`download_file()` uses `urllib.request` (no external tools). Retries up to 5 times with 5-second delay. On a TTY, shows an in-place progress bar (`\r`-based, cleared with `\r\033[K`) in the format `[*] [####----] 20%  1.3 MiB / 6.4 MiB`, followed by `[*] Finished downloading (N MiB).` on completion. On CTRL-C, clears the TTY progress bar, removes the partial `.tmp` file, and re-raises `KeyboardInterrupt` for the caller to handle.

`sha256_file()` shows the same style of progress bar during integrity verification. The install flow messages are: `[*] Verifying rootfs checksum...` → progress → `[*] Integrity check passed.` (or `[!] Verification failure: downloaded file is corrupted.` on mismatch).

### Remove

`command_remove()` wipes a distribution's rootfs and, for non-bundled distributions, its config file.

- **Order**: rootfs is removed first; the config file is only deleted after the rootfs removal succeeds completely.
- **`_remove_path(path, on_remove=None)`**: recursive removal with on-the-fly permission fixing. Directories receive at least `S_IRWXU` before `listdir` is attempted (handles subtrees that were recursively `chmod 000`). Regular files receive at least `S_IRUSR | S_IWUSR` before `unlink`. Symlinks are unlinked directly without `chmod`. `rmdir` on a directory is only attempted after all its children are successfully removed. Returns `True` on full success; any failure returns `False` and the partial state is left on disk. The optional `on_remove` callback is called with the path of each successfully removed entry (file, symlink, or directory).
- **`--verbose`**: passes an `on_remove` callback that logs each removed path with the standard `[*]` prefix.

### Copy

`command_copy()` copies or moves a file or directory tree between host paths and/or paths inside installed rootfs directories (resolved via `dist:path` notation by `_resolve_copy_path()`).

- **`--verbose`** (`-v`): logs each copied file as `[*] Copying: 'src' -> 'dst'` in real time (directory trees use a `_verbose_copy2` closure passed as `copy_function` to `copytree`; single-file copies log before the call). For move mode, the source tree is pre-walked and all paths are logged before `shutil.move` is called.
- **`--move`** (`-m`): uses `shutil.move` instead of `shutil.copy2`/`copytree`.
- **CTRL-C**: TTY line is cleared, `[!] Aborted by user.` is printed, and the process exits. Partial destination is not removed.

### Backup

`command_backup()` uses a pure-Python tar implementation (no `tar` subprocess).

- **Compression** is determined in this priority order: (1) `--compress TYPE` CLI argument (`gzip`, `bzip2`, `xz`, `none`) stored in `_COMPRESSION_ARG_MAP`; (2) output filename extension via `_compression_mode()` (`.tar.gz`/`.tgz` → gzip, `.tar.bz2`/`.tbz2` → bzip2, `.tar.xz`/`.txz` → xz, `.tar.lzma`/`.tlzma` → xz, `.tar` → uncompressed; recognised-but-unsupported formats raise a clear error; unknown extensions fall back to uncompressed). `--compress` applies to both file and stdout output. Without `--compress`, stdout defaults to uncompressed.
- **tar mode**: seekable file output uses `w:{comp}` (e.g. `w:gz`); streaming stdout uses `w|{comp}` (e.g. `w|gz`). Both handle `comp=''` correctly as uncompressed (`w:` / `w|`).
- **Archive contents**: `proot-distro/{config_file}` followed by `installed-rootfs/{distro_name}/...`.
- **Entry filtering**: block/character devices, FIFOs, and sockets are silently skipped. Symlinks are stored as symlinks (not followed). Symlinks to subdirectories are stored as single entries; `os.walk` is prevented from descending into them.
- **Ownership**: uid/gid/uname/gname are zeroed for all entries. Extended attributes are not stored.
- **Permission fix**: before archiving, a sequential walk ensures all dirs have `r-x` and all files have at least `r--` (executables keep their `x` bit) so tar can read them.
- **Progress bar**: TTY-only, written to stderr in the format `[*] [####----] XX%  N / Total files`, updated after each entry is written. Bar is cleared with `\r\033[K` on both success and error.
- **CTRL-C**: `KeyboardInterrupt` is caught, the progress bar is cleared, the partial output file is removed (file output only), and `[!] Aborted by user.` is printed.

### Restore

`command_restore()` uses a pure-Python tar implementation (no `tar` subprocess), mirroring the backup approach.

- **Compression** is detected from file header magic bytes via `_detect_compression()`: gzip (`\x1f\x8b`), bzip2 (`BZh`), xz (`\xfd7zXZ\x00`), lzma legacy (`\x5d\x00` → `'xz'` mode; Python's `lzma.open` handles both XZ and LZMA formats transparently), or uncompressed (no match). For file input, the first 6 bytes are read before opening; detected mode opens as `r:{comp}`, unknown headers fall back to `r:*` (tarfile auto-detect). For stdin, `sys.stdin.buffer.peek(6)` is used (non-consuming on `BufferedReader`) and the stream is opened as `r|{comp}` — supporting `r|gz`, `r|bz2`, `r|xz`, or `r|`. Extension-based detection is not used.
- **Entry routing**: each member's path is matched against `proot-distro/` (→ `configs_parent`) or `installed-rootfs/` (→ `rootfs_parent`); anything else is silently skipped.
- **Recursive-unlink**: on the first archive entry for a given distro's rootfs, the entire existing rootfs directory is cleared before any extraction begins. This ensures the restored rootfs contains exactly what the archive contains — no leftover files. The pre-clear walks bottom-up (`os.walk topdown=False`), unlinking files and `os.rmdir`-ing empty directories, with `shutil.rmtree` as a final safety net. Memory cost: one string per distro being restored (`cleared_rootfs` set), regardless of file count. On TTY, shows `[*] Removing old rootfs... N files` progress: `\r\033[K` is issued once before the loop (to clear any prior progress bar), then plain `\r` updates the counter in-place, then `\r\033[K` clears the line when done.
- **Conflict handling**: within each extracted entry, existing files and symlinks at the destination are removed by `_remove_existing()` before writing; existing directories are merged when the incoming entry is also a directory, or removed with `shutil.rmtree` otherwise.
- **Entry types**: regular files written with mode applied via `os.chmod`; symlinks created with `os.symlink`; hard links resolved within the same dest root via `os.link`; directories created with `os.makedirs` + `os.chmod`; block/character devices and FIFOs are silently skipped.
- **Progress bar**: TTY-only, written to stderr. Seekable file: shows `[*] Estimating progress...` before pre-collecting members (cleared with `\r\033[K`), then accurate `[####----] XX%  N / Total files` bar. Streaming stdin: total unknown → `N files extracted...` counter. Progress is updated after each entry is fully written. Bar is cleared with `\r\033[K` on both success and error.
- **CTRL-C**: `KeyboardInterrupt` is caught, the progress bar is cleared, no data is removed, and `[!] Aborted by user.` is printed.
- **Errors**: `EOFError` (truncated/incomplete stream), `OSError`, and `tarfile.TarError` are caught; the progress bar is cleared and `[!] Failed to restore distribution: <exc>` is printed, followed by a note that the tarball may be corrupted.

### Fake sysdata

On login, fake `/proc` and `/sys` entries are written to `$PREFIX/var/lib/proot-distro/` and bind-mounted read-only. This satisfies apps that read kernel info on Android where real `/proc/stat` etc. are unreadable. Constants `_FAKE_LOADAVG`, `_FAKE_STAT`, `_FAKE_UPTIME`, `_FAKE_VMSTAT` are hardcoded in `proot_distro/sysdata.py`.

### Subcommand help

Per-command help text is in `_HELP_COMMANDS` dict (lambdas) in `proot_distro/commands/help.py`. Subcommand `--help`/`-h` is intercepted in `main()` before argparse parses positional arguments (avoids "required argument" errors on `login --help`).

### Install extraction (`_extract_tar`, `_extract_zip`)

Both functions live in `commands/install.py` and share two module-level helpers: `_make_dir`, `_make_symlink`. Archive format is detected from file magic bytes by `_detect_archive_type()` (`PK\x03\x04` → zip, otherwise tar).

`command_install()` has three exception handlers wrapping the entire install flow:
- **`KeyboardInterrupt`**: clears TTY progress bar, prints `[!] Aborted by user.`, calls `_cleanup()`, exits.
- **`(EOFError, OSError, tarfile.TarError)`**: clears TTY progress bar, prints `[!] Failed to install distribution: <exc>` and a note that the archive may be corrupted, calls `_cleanup()`, exits.
- **`Exception`**: calls `_cleanup()` and re-raises (preserves traceback for unexpected errors).

`_cleanup()` removes the partial rootfs dir and the custom config file if `--name` was used.

**`_extract_tar`** — extracts tar archives (any compression, auto-detected via `r:*`):

- **Hard links** are copied as regular files via `shutil.copy2` rather than actual hard links, keeping the rootfs self-contained under proot. They are **deferred** to a post-loop phase so that `shutil.copy2` always finds the source file already written.
- **Directories** always receive at least owner `rwx` (`mode | stat.S_IRWXU`) so proot can always traverse and write into them regardless of what the archive stores.
- **Symlinks** stored as-is. Block/character devices, FIFOs, sockets silently skipped.
- **Path stripping**: `_stripped_name(name, strip)` removes the first `strip` components from both entry names and hard-link target names.
- **`exclude_dev=True`**: skips any entry whose top-level component is `dev` (used for normal-type installs).
- **Progress bar**: TTY-only, `[####----] XX%  N / Total files` format on stderr. Before pre-collecting members, shows `[*] Estimating progress...` on stderr (cleared with `\r\033[K` once the total is known). `_on_entry` is called once per member immediately after it is processed (skipped entries and unresolvable hard links counted inline; deferred hard links counted after the copy). Cleared with `\r\033[K` on completion.

**`_extract_zip`** — extracts ZIP archives (used for Termux bootstraps and any ZIP-format rootfs):

- Uses `_make_dir` and `_make_symlink` helpers. `chmod` is skipped when the computed mode is 0 (ZIP entries without Unix attributes have `external_attr=0`; `stat.S_IMODE(0)==0` would otherwise set permissions to `----------`).
- **Symlinks** detected via `stat.S_ISLNK(external_attr >> 16)`; target read via `zf.read()`.
- **Directories** detected via trailing `/` or `stat.S_ISDIR`; guaranteed owner `rwx` via `_make_dir`.
- **Regular files** extracted by streaming via `zf.open()` + `shutil.copyfileobj`.
- **Path stripping and `dev` exclusion**: same logic as `_extract_tar`.
- **Progress bar**: TTY-only, same format as `_extract_tar`. `_on_entry` is called once per entry at the bottom of the loop (skipped entries use an early `_on_entry` + `continue`). Cleared with `\r\033[K` on completion.

### Pure Python policy

`proot_distro/` avoids spawning subprocesses for system queries. Specifically:
- Colors: ANSI constants, no `tput`
- User/group info in `rootfs.py`: `pwd.getpwuid()` and `grp.getgrgid()` / `os.getgroups()`, no `id`
- ELF arch detection in `arch.py`: `struct.unpack` on raw bytes, no `file`
- 32-bit support in `arch.py`: `ctypes` `personality()` syscall, no `lscpu`
- Downloads: `urllib.request`, no `curl`
- Rootfs extraction: `tarfile` module, no `tar` or `proot` subprocess
- Backup archiving: `tarfile` module, no `tar` subprocess
- Restore extraction: `tarfile` module, no `tar` subprocess
