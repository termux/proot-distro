# CLAUDE.md

Guidance for Claude Code when working on this repository.

## Overview

`proot-distro` is a pure-Python utility for managing rootless,
proot-based Linux containers. Primary target is Termux on Android; also
runs on regular Linux hosts (XDG base dirs, no Android-specific
bindings). It speaks the OCI / Docker registry protocol directly and
assembles container filesystems locally.

**No third-party Python dependencies.** Published on PyPI at
https://pypi.org/project/proot-distro/. `pyproject.toml` is the version
source of truth; `PROGRAM_VERSION` reads it via `importlib.metadata`,
falling back to `"rolling"`. The shim `proot-distro.py` and console
scripts `proot-distro` / `pd` resolve to `proot_distro.cli:main`.
Bash/Zsh/Fish completions ship under `proot_distro/completions/`.

## Pure-Python policy

No subprocesses for system queries: ANSI vs `tput`, `pwd`/`grp` vs `id`,
`struct.unpack` on ELF bytes vs `file`, `ctypes.personality()` vs
`lscpu`, `urllib` vs `docker`/`curl`, `tarfile` vs `tar`. Only externals
ever run are `proot` (via `os.execvpe`) and — at install time on Termux,
only when prompted — `pkg install -y -q proot`.

## Module layout (`proot_distro/`)

Top-level utilities (each owns a focused concern):

- `constants.py` — `IS_TERMUX`, `TERMUX_PREFIX/HOME/APP_PACKAGE`,
  `RUNTIME_DIR`, `BASE_CACHE_DIR`, `CONTAINERS_DIR`, `SESSIONS_DIR`,
  `LAYER_CACHE_DIR`, `MANIFEST_CACHE_DIR`, `DEFAULT_PATH_ENV`,
  `DEFAULT_FAKE_KERNEL_*`.
- `message.py` — color dict `C`, `msg`, `log_info/error`, `warn`,
  `crit_error`, `set_quiet`/`is_quiet`, `tty_safe_for_writes`.
- `progress.py` — `fmt_size`, `ByteCounter`, `draw_bytes_bar`,
  `draw_count_bar`, `clear_bar`, `progress_active`.
- `arch.py` — `get_device_cpu_arch`, `detect_installed_arch` (ELF
  magic), `normalize_arch`, `get_emulator_args`, `ARCH_UNAME_M`.
- `atomic.py` — `atomic_replace()`: mkstemp + `os.replace`; cleans up
  on `BaseException` (Ctrl-C never leaves half-written sentinels).
- `l2s.py` — `--link2symlink` helpers (SIGINT/SIGQUIT shielded).
- `locking.py` — `ContainerLock`, `BuildLock` (POSIX flock).
- `session.py` — active-session registry for `ps`: `register_session`
  (inheritable flock survives `execvpe`, like the container lock; records
  a `detach` flag among the per-session metadata), `active_sessions`
  (reads `SESSIONS_DIR`, prunes dead via a shared flock probe).
- `names.py` — `_NAME_RE`, `is_valid_name`, `require_valid_name`.
- `parser.py` — argparse, `ALIAS_TO_CANONICAL`, `REQUIRED_ARGS`,
  `_PdArgumentParser` (per-command help on error).
- `paths.py` — `container_dir/_rootfs/_manifest`, `[name:]path` spec
  resolver, `container_locks_for_spec_pair`.
- `sysdata.py` — `setup_fake_sysdata`, `fake_proc_bindings`.
- `cli.py` — `main()`: SIGQUIT routing, root warn, nested-proot
  reject, proot probe, parse, dispatch.

Commands (`commands/`): `backup`, `build`, `clear_cache`, `copy`,
`install` (+`install_local`), `list`, `ps`, `push`, `remove`, `rename`,
`reset`, `restore`, `run`, `sync`; subpackages `help/{pages,render}`
and `login/{bindings,detach,env,migrate,passwd,proot_cmd,quoting}`.

Helpers (`helpers/`): `build_cache`, `dockerfile`, `download`,
`layer_diff`, `oci_writer`, `rootfs`, `tar_extract`; subpackages
`build_engine/{constants,copy_step,dockerignore,engine,errors,handlers,
parsing,run_step,stage,users}` and `docker/{cache,layers,media,pull,
push,refs,transport}`.

## Key paths

| Constant | Termux | Non-Termux |
|---|---|---|
| `RUNTIME_DIR` | `$TERMUX_PREFIX/var/lib/proot-distro` | `$XDG_DATA_HOME/proot-distro` |
| `BASE_CACHE_DIR` | `$RUNTIME_DIR/cache` | `$XDG_CACHE_HOME/proot-distro` |
| `CONTAINERS_DIR` | `$RUNTIME_DIR/containers` | same |
| `SESSIONS_DIR` | `$RUNTIME_DIR/sessions` | same |
| `LEGACY_ROOTFS_DIR` | `$RUNTIME_DIR/installed-rootfs` (migration only) | same |
| `LAYER_CACHE_DIR` | `$BASE_CACHE_DIR/oci_layers` | same |
| `MANIFEST_CACHE_DIR` | `$BASE_CACHE_DIR/oci_manifests` | same |
| Build cache index | `$BASE_CACHE_DIR/build_cache_index.json` | same |

## Termux detection (`constants._detect_termux`)

True when **two of three** hold: Android signal (`platform.platform()`
mentions android, or `/system/build.prop`/`/data/app` exist); Termux
env var (`TERMUX_APP__APP_VERSION_NAME` or `TERMUX_VERSION`);
`TERMUX_PREFIX` readable + executable. Computed once at import; drives
path selection, `DEFAULT_PATH_ENV`, argparse availability of the
Termux-only flags (`--isolated`, `--minimal`, `--no-link2symlink`,
`--no-sysvipc`, `--no-kill-on-exit`), and `login`/`build` skipping
proot extensions + Android bindings on non-Termux hosts.

## Container storage and types

```
containers/<name>/manifest.json   ← image_ref, arch, manifest, image_config
containers/<name>/rootfs/         ← assembled filesystem
```

Directory name is the sole identifier. Plain-tarball installs do **not**
write `manifest.json`. Legacy `installed-rootfs/<name>` layout is
migrated on first `login` (`commands/login/migrate.py`), which then
rewrites l2s symlink targets.

Distribution type is detected at login:
`rootfs/data/data/com.termux/files/usr/bin/login` existing **as a file**
(not dir — proot may materialise the bind-mount target during a
concurrent session) ⇒ `termux`; else `normal`. `termux`: no
`--link2symlink`, no `--change-id`; hardcoded HOME/PATH/PREFIX/TMPDIR;
image Env + Android host vars applied like `normal`; Android system
bindings + shared storage + Dalvik/ART caches (`/data/app`,
`/data/dalvik-cache`, `/data/misc/apexdata/com.android.art/dalvik-cache`)
on when non-isolated (off when isolated/minimal); the host's Termux app
dirs under `/data/data/com.termux` are **never** bound (the guest ships
its own, so only its `cache` dir is created inside the rootfs); Termux
prefix not bound (guest has its own at the same path). **Cross-arch is
refused** — host and guest share `TERMUX_PREFIX`, so host binaries
would shadow the container's.

## Commands and locks

| Command | Aliases | Lock |
|---|---|---|
| `install` | `add`, `i`, `in`, `ins` | container exclusive |
| `remove` | `rm` | container exclusive |
| `rename`, `reset` | — | container exclusive |
| `login` | `sh` | container shared (fd inherited by proot) |
| `run` | — | container shared (fd inherited by proot) |
| `list` | `li`, `ls` | none |
| `ps` | — | none (reads session registry, prunes dead entries) |
| `backup` | `bak`, `bkp` | container shared |
| `restore` | — | container exclusive, lazy per first TarInfo |
| `clear-cache` | `clear`, `cl` | none |
| `copy` | `cp` | shared src, exclusive dest |
| `sync` | — | shared src, exclusive dest |
| `build`, `push` | — | `BuildLock` keyed on `(image_ref, arch)` |
| `help` | `h`, `he`, `hel` | none |

`install` accepts an image reference, a local path (must start with
`/`, `./`, `../`, or `~`), or an `http(s)://` URL. `--user` takes name,
numeric uid, or `user:group`.

## CLI flow (`cli.main()`)

1. SIGQUIT → `KeyboardInterrupt` so every existing `except` handles
   Ctrl-\ like Ctrl-C (progress cleanup, partial-file removal,
   "Aborted by user").
2. Root warn (non-fatal); nested-proot reject (reads
   `/proc/<pid>/status`, follows one TracerPid hop).
3. proot probe; on Termux + TTY, offers `pkg install`. **`build` and
   `push` are exempt**; `build` runs its own gate via
   `build_engine.needs_proot()` (True only with a RUN-family).
4. Per-command `-h`/`--help`/`--usage` intercepted **before** argparse
   so missing positionals never produce errors instead of help. Unknown
   subcommand also rejected pre-parse.
5. `parse_known_args()` + manual handling of tokens after literal `--`
   (`login`/`run` inner command).
6. `REQUIRED_ARGS` check. `restore` intentionally absent — it decides
   from stdin TTY state.
7. `--quiet`: `set_quiet(True)` before dispatch unless command is
   `list` (its `--quiet` is different: container names only).
   `log_info()` becomes no-op; errors/warns/`msg()` always show.

## Locking

`ContainerLock` → `RUNTIME_DIR/locks/<name>.lock`. `BuildLock` →
`RUNTIME_DIR/locks/build/<sha256-prefix>.lock`, key = first 16 hex of
`sha256("<image_ref>_<arch>")` (same as the manifest-cache key).

Non-blocking `flock(2)`. Conflict ⇒ exit immediately, reporting the
holder's PID + command. Re-entrancy via `_held_exclusive` — `reset`
acquires the lock then calls `install` for the same name; install's
acquire detects the path and skips. Login/run pass `inheritable=True`
to clear `O_CLOEXEC` so the fd survives `os.execvpe`. `disown()` marks a
lock so `release()` closes the fd **without** `LOCK_UN` — used by
`--detach`, where a forked descendant (the daemon) inherits the same
open file description and must keep the lock held after the foreground
process exits its `with` block (flock releases on `LOCK_UN` of any
duplicate, or once all duplicates close). Multiple locks
acquired in sorted-path order via `ExitStack`. `BuildLock` covers only
the output `(image_ref, arch)`; concurrent builds with different tags
can still race on shared caches, safe because every writer uses
`atomic.atomic_replace()` and `build_cache` holds its own flock over
the index's RMW.

## Architecture

`detect_installed_arch(rootfs)` reads ELF e_machine from common shell
binaries. `normalize_arch()` accepts native names, bare Docker names
(`arm64`/`amd64`/`386`), and `linux/`-prefixed forms. Native 32-on-64:
`aarch64` runs `arm` when `personality(PER_LINUX32)` succeeds; `x86_64`
runs `i686` always. Otherwise `get_emulator_args()` selects
`qemu-<arch>` and binds Android system paths for QEMU's loader. proot's
`--kernel-release` `uname_m` field comes from `ARCH_UNAME_M`, not host
uname, so emulated containers self-report correctly.

## Docker / OCI registry (`helpers/docker/`)

Pull is manifest-cache-first: cached + all layers present ⇒ fully
offline; cached + missing layers ⇒ fetch token + missing only;
otherwise full pipeline (token → manifest → arch unwrap → config blob
→ layers). Cache writes use `atomic_replace`. Layer digests are
stream-verified via `hashlib.sha256` before promotion. Digests pass
through `validate_digest()` before being converted to filesystem
paths (layer cache, OCI blob layout) so a crafted reference like
`../foo:bar` can't escape the cache root. `zstd` mediaType is refused
(Python `tarfile` lacks zstd). Whiteouts (`.wh..wh..opq` clears parent
dir; `.wh.<name>` deletes sibling), hardlink linkname filtering, and
member-name traversal protection live in `helpers/tar_extract.py`.

Auth (`transport.py`): `PD_DOCKER_AUTH=user:pass` forwarded as HTTP
Basic to the token endpoint; colon is mandatory (bare tokens raise
`RuntimeError`). `AuthStrippingRedirectHandler` drops `Authorization`
on cross-host redirects (Docker Hub CDN blob URLs reject Bearer with
HTTP 400). `get_auth_token(repo, registry, actions)` takes `"pull"`
(default) or `"pull,push"`.

Push (`push.py`) loads `(manifest, repo, image_config)` from the local
cache, re-canonicalises and verifies SHA against `manifest.config.digest`,
HEAD-probes each blob, uploads the missing via POST-uploads + monolithic
PUT (no chunked, no cross-repo mount, no multi-arch index). 401/403 ⇒
`push_denied_msg`.

## Login env (`commands/login/`)

`child_env` is built explicitly and passed to `os.execvpe` — no
`env -i` wrapper, host env is **not** propagated. `normal`-type
precedence (later wins): PATH/MOZ_FAKE_NO_SANDBOX/PULSE_SERVER baseline
(non-minimal only) → image `Env` (filtered by `IMAGE_ENV_BLOCKED`:
Android vars, MOZ/PULSE, TERM/COLORTERM) → Android host vars
(`ANDROID_HOST_ENV_VARS`, Termux + neither isolated nor minimal) →
user `--env` → HOME/USER (non-minimal only) → TERM/COLORTERM. Image
`Env` and `--env` apply in **every** mode (isolated and minimal
included); only the Android host vars are gated on the default mode.
On non-Termux hosts no host vars are inherited. PATH is not blocked but
`TERMUX_PREFIX/bin` is deduped + appended after image Env (non-isolated,
non-minimal). `termux`-type uses the same image-Env + Android-host-var
logic on top of its hardcoded HOME/PATH/PREFIX/TMPDIR baseline.

`inject_termux_profile()` writes `/etc/profile.d/termux-profile.sh` so
`su - other` doesn't drop the proot-distro-set vars: POSIX case-guard
append for PATH; `export K='V'` (with `'\''` idiom) for everything
except per-session and proot-internal vars
(HOME/USER/TERM/COLORTERM/PATH/PROOT_*/LD_*). Keys are first matched
against the identifier regex `^[A-Za-z_][A-Za-z0-9_]*$`; anything that
would otherwise corrupt the sourced script (spaces, `;`, quotes …) is
dropped silently. Legacy `termux-prefix.sh` unlinked first.

`minimal` clears almost everything: image `Env` + `--env` + `TERM`
(default `xterm-256color`) + inherited `COLORTERM`; no baseline PATH,
no MOZ/PULSE, no Android host vars, no HOME/USER. `PROOT_L2S_DIR`
pinned to `rootfs/.l2s` (created upfront) for `normal` on Termux so
concurrent sessions agree. `LD_PRELOAD` stripped before exec.

## Run / build

`command_run()` reads `Entrypoint`/`Cmd`/`WorkingDir` from
`manifest.json`, builds `inner` per Docker semantics, delegates to
`command_login` via `args._run_inner`. `--work-dir` overrides
`WorkingDir`; default is `/` (not user home).

`-d`/`--detach` (login + run, via `_add_login_or_run_common`)
backgrounds the session: after all setup, `_command_login_inner`
delegates the final exec to `commands/login/detach.spawn_detached`
instead of `register_session` + `execvpe`. It is a double-fork daemon
(`setsid`, std fds → `/dev/null`); `register_session` runs in the
grandchild so `getpid()` already equals the future proot PID, and a
pipe relays that PID back so the foreground can print it. The grandchild
inherits the foreground's container-lock fd, so the foreground calls
`lock.disown()` (skip `LOCK_UN`) to leave the lock held by the daemon.
`--get-proot-cmd` short-circuits before the detach branch. The session
shows in `ps` with TYPE marked `login*`/`run*`; stop it with `kill PID`.

`command_build()` parses the Dockerfile, runs `BuildEngine`, writes
the manifest cache (Variant A — small JSON; layer blobs already in
`LAYER_CACHE_DIR`), and optionally writes OCI tarballs (Variant B —
both standard OCI layout **and** Docker-legacy `manifest.json` so
`docker load` works) and/or invokes `command_install` for `--install-as`.

`helpers/dockerfile.py` handles continuations, parser directives
(`syntax`/`escape`), here-docs in ADD/COPY/RUN, JSON exec form
detection, and `expand_vars()` for `$VAR`/`${VAR:-default}` family.

`BuildEngine` pre-scans for global ARGs and named stages (validates
`--target` early), then dispatches to `HANDLERS` (metadata), `do_run`,
or `do_copy_or_add`. FROM resolves `scratch`, named stages (re-apply
cached layers), or external images via `pull_image()`. Base image
`OnBuild` triggers fire after FROM.

RUN under Termux uses `--link2symlink`. To keep produced layers
portable, `layer_diff.snapshot()` skips `<rootfs>/.l2s/`, and
`_add_entry()` follows symlinks pointing into it to pack the backing
file's content as a regular file (hard-link semantics lost, content
preserved). Build steps run isolated and non-interactive
(`stdin=/dev/null` unless here-doc).

Build cache: `compute_recipe_hash(parent_digest, instr, extra)` keys
into `build_cache_index.json`. Hit ⇒ apply cached layer, skip proot.
`build_cache.record()` holds its own flock over the index.
`clear-cache` removes top-level entries under `BASE_CACHE_DIR` including
the index.

## Backup / restore

Pure `tarfile`. Archive shape: `<name>/manifest.json` +
`<name>/rootfs/...`. Backup applies `_fix_permissions()` (chmod-000
subtrees become readable), filters devices/FIFOs/sockets, zeros
uid/gid/uname/gname; refuses to write to a TTY without `--output`.
Restore auto-detects compression (`tarfile r|*` files; magic-byte peek
for stdin), routes members through `_dest_path()` into
`containers/<name>/...`, re-rooting legacy `installed-rootfs/<name>`.
Traversal blocked (`..`/`.`/empty dropped; container name must match
`_NAME_RE`). First entry per container triggers rootfs clear + lock.

Both gate every stderr write on `tty_safe_for_writes()` — when a
sibling pinentry/curses holds the TTY (ECHO or ICANON cleared in
termios), `msg()` and progress lines are dropped silently so
`backup | gpg -c` doesn't corrupt the passphrase prompt.

## Help system

Data in `commands/help/pages.py` (`HELP_PAGES`, `TOP_COMMANDS`);
`render.py` formats it. `term_width()` clamps to `[32, 92]`, stacks
options vertically below 60 cols (Termux on phones). `HELP_COMMANDS`
maps each name to a zero-arg renderer the CLI dispatches.

## Conventions

- License header on every Python file in the package.
- Container names: `^[A-Za-z0-9][A-Za-z0-9_.\-]*$`, enforced via
  `names.require_valid_name()` at every entry point (image-ref-derived
  alias, `--install-as`, archive members in `restore`).
- `--bind`: source ⇒ `os.path.abspath`; destination must be absolute
  (or omitted). Overlap with an existing dest ⇒ yellow warning, still
  added.
- Every cache writer must use `atomic.atomic_replace()`.
- New commands plug into `cli._COMMAND_HANDLERS`, `parser` (with
  `_pd_command` stamped), `REQUIRED_ARGS` if positional,
  `commands/help/pages.HELP_PAGES`, and `ALIAS_TO_CANONICAL` for aliases.
