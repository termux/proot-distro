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
  `crit_error`, `set_quiet`/`is_quiet`, `tty_safe_for_writes`,
  `terminal_width` (column count for the `ps` / `list --image` tables),
  `quote_path` (C-style escapes for control characters). Names inside a
  rootfs are the guest's to choose and `copy`/`sync` print them, so every
  filesystem-derived name — and every `OSError` string, which carries
  one — passes through `quote_path` before it reaches the terminal. Only
  the untrusted text, never a whole message: the log helpers' own colour
  codes are control characters by definition.
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
  (reads `SESSIONS_DIR`, prunes dead via a shared flock probe),
  `session_file`/`session_is_live` (that probe for one PID) and
  `session_holders` (scans `/proc/*/fd` for the registry file's inode —
  the members `kill` walks from).
- `names.py` — `_NAME_RE`, `is_valid_name`, `require_valid_name`.
- `parser.py` — argparse, `ALIAS_TO_CANONICAL`, `REQUIRED_ARGS`,
  `required_args_for()` (refines the message when a positional changes
  meaning — `remove --image` wants a reference, not a container),
  `_PdArgumentParser` (per-command help on error).
- `paths.py` — `container_dir/_rootfs/_manifest`, `[name:]path` spec
  resolver, `container_locks_for_spec_pair`. The container side of a spec
  is resolved with **chroot semantics** (`_resolve_within_root`): each
  component is walked in turn, absolute symlink targets are re-anchored
  at the rootfs, relative ones follow from the link's directory, and `..`
  clamps at the rootfs (`_MAX_SYMLINK_HOPS` guards link cycles). A `..`
  written in the spec itself is still rejected outright rather than
  clamped. Lexical `normpath` alone is **not** sufficient: a guest-created
  `escape -> /` would pass a `startswith(rootfs)` check and let
  `copy`/`sync` read from or write to the host filesystem.
  `pin_path()` closes the TOCTOU that resolution alone cannot: it
  re-walks the resolved components with `O_NOFOLLOW` from a rootfs fd,
  so a component swapped to a symlink after the resolve fails (abort),
  and holds the directory fd open, so `PinnedPath.dir_fd` names the
  validated *inode* rather than a name a guest can re-point. Callers
  use `(dir_fd, leaf)` for every filesystem call; `str(pin)` stays the
  real path, for messages. `inside=True` walks the final component too
  — for a root only worked *underneath* — and so also refuses a root
  that became a symlink. `create=True` makes the missing components
  along that same walk (`_descend`: `mkdirat` off the validated fd, then
  the `O_NOFOLLOW` open); a caller must **not** `os.makedirs()` the
  parents first, since that addresses each level by path and builds the
  tree through a symlink planted after the resolve, before the pin can
  refuse. Host specs are not walked component by component but still
  yield a `(dir_fd, leaf)` pair, so callers need no special case.
  `resolve_container_child()` re-resolves a destination extended with
  the source's base name (`copy f box:/dir` ⇒ `box:/dir/f`), so the
  appended component gets the same chroot walk as one written in the
  spec instead of being joined on literally.
  `deref_leaf=False` resolves only the *parents*, for an operation that
  acts on the last component itself — `copy --move`, where `rename(2)`
  moves a link rather than its target — and both functions take it, since
  a name appended to a destination directory needs the same treatment as
  one written in the spec. A **host** spec is resolved to the same depth
  by `_host_path()` — `realpath` when `deref_leaf`, its parent chain only
  when not — so both ends of a transfer name the entry that will really be
  touched. Host paths get no `O_NOFOLLOW` walk (the host filesystem is not
  what the chroot walk defends against), so leaving their own final
  component a name was what let `sync <dir> <link>` with `link -> <dir>/sub`
  recurse to the interpreter's limit and `sync --delete` through a link to
  the source's parent prune the source itself. Running `realpath` over a
  *container* path instead would re-resolve it with host semantics and undo
  the walk, so `_host_path` is never applied to one.
  `refuse_src_dest_overlap()` compares the two *resolved* paths, the
  earliest point at which a planted link (`backup -> /data`) can no longer
  hide that the destination sits inside the source, or is it; it re-applies
  `_host_path` itself (idempotent for a caller that already resolved) so the
  guard stays sound on its own. The same-file test follows a final link
  exactly when the operation would, so `copy f link` is refused and
  `copy --move f link` renames, as cp and mv each do.
- `dirfd.py` — the openat(2) layer `copy`/`sync` walk with:
  `opendir_at`/`reopen`/`open_file_at`/`open_regular_at`/`open_new_at`
  (always `O_NOFOLLOW`), `listdir_at`/`lstat_at`/`exists_at`,
  `copy_file_at`/`copy_symlink_at`/`copy_tree_at`,
  `rmtree_at`/`unlink_quietly`, and fd-based metadata (`copy_metadata`,
  `set_times_at`, `make_writable`).
  Nothing here takes a path below the root, so no component can be
  re-pointed mid-walk. `REFUSED` / `is_refusal()` cover both errnos a
  refused descent can raise — Linux reports `O_NOFOLLOW|O_DIRECTORY` on a
  symlink as **ENOTDIR**, not ELOOP. Recursion closes each fd as it
  unwinds, so open fds scale with tree *depth*, not size.
  Three guarantees need more than `O_NOFOLLOW`, because the obvious call
  looks fd-based but is not. **chmod**: Linux has no `AT_SYMLINK_NOFOLLOW`
  for `fchmodat(2)`, so naming an entry hands the mode change to whatever
  a link planted since the `lstat` points at; every chmod goes through
  `_chmod_fd()` (`fchmod`, falling back to the fd's `/proc` alias, since
  `fchmod` is **EBADF** on the `O_PATH` fds `pin_path` yields) and
  `rmtree_at`'s force path pins with `_make_readable_at()` first.
  **File type**: `O_NOFOLLOW` says nothing about a FIFO, and opening one
  waits for a peer a hostile guest never supplies, so regular-file
  endpoints go through `open_regular_at()` — `O_NONBLOCK` plus an
  `fstat` that refuses every type but a regular file.
  **Hardlinks**: a hardlink is not a link as far as `openat(2)` is
  concerned — it *is* the file under a second name, and nothing tells one a
  guest made to a host file (same filesystem, same uid, no race needed)
  from an ordinary rootfs entry. Writing through a name can therefore always
  land outside the container, so every write creates a **new inode**:
  `open_new_at()` is `O_EXCL` and unlinks a leftover rather than adopting
  it, and `copy_file_at(replace=True)` writes `<name>.~pd_copy`
  (`TMP_SUFFIX`) and renames it into place — also making the write atomic,
  at the cost of a hardlinked destination losing its link. `replace=True`
  additionally **refuses** a destination that is not a regular file: the
  resolve already followed any link that stood there, so one there now was
  planted since. `copy_tree_at` needs no `replace` — every directory it
  writes into was just made by `mkdir`.
- `sysdata.py` — `setup_fake_sysdata`, `fake_proc_bindings`.
- `cli.py` — `main()`: SIGQUIT routing, root warn, nested-proot
  reject, proot probe, parse, dispatch.

Commands (`commands/`): `backup`, `build`, `clear_cache`, `copy`,
`install` (+`install_local`), `kill`, `list`, `ps`, `push`, `remove`,
`rename`, `reset`, `restore`, `run`, `sync`; subpackages
`help/{pages,render}` and
`login/{bindings,detach,env,migrate,passwd,proot_cmd,quoting}`.

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
| `remove` | `rm` | container exclusive; `--image` ⇒ `BuildLock` per removed `(ref, arch)` |
| `rename`, `reset` | — | container exclusive |
| `login` | `sh` | container shared (fd inherited by proot) |
| `run` | — | container shared (fd inherited by proot) |
| `list` | `li`, `ls` | none (`--image` reads the manifest cache) |
| `ps` | — | none (reads session registry, prunes dead entries) |
| `kill` | — | none (reads session registry, signals PIDs) |
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

`copy`/`sync` resolve both endpoints through `resolve_container_path()`,
pin them with `pin_path()`, and then address the filesystem **only**
through `dirfd` — no path below the roots is ever resolved by name, so
a symlink planted mid-transfer cannot redirect anything. No `shutil`
path API is left in either command: `copytree`/`copy2`/`move` gave way
to `dirfd.copy_tree_at` / `copy_file_at` / `renameat` (`move` falls back
to copy+`rmtree_at` on `EXDEV`), and sync's walk is three fd-carrying
recursions — `_collect_rels` (count + rel set), `_mirror_at` (write),
and `_collect_extras_at`/`_remove_extras_at` for `--delete`. Missing
destination parents are made by the pinning walk itself
(`pin_path(create=True)`), never by `os.makedirs()` beforehand.

Destination directories are created **writable** (`0o700`) and given the
source's mode only once their contents are in — `copy_tree_at` via
`copy_metadata`, sync via `_apply_dir_mode`, both `fchmod` on the
descended fd. `mkdir`'s mode argument is umask-masked and so cannot
preserve a mode on its own (a `1777` source landed as `1755`), and a
source directory that is not writable itself (`0555`) would otherwise
reject its own contents mid-copy.

An **endpoint** given as a symlink is dereferenced, on either side, so
`cp`/rsync semantics hold — `copy -r /sdcard box:/x` and `sync /sdcard
box:/x` are both ordinary on Termux, where `/sdcard` *is* a link, and a
destination link is written where it leads. Both sides get that from
`resolve_container_path()`/`resolve_container_child()`: the container side
from the chroot walk covering the final component, the host side from
`_host_path`'s `realpath`. Links *within* the tree are still recreated as
links, only the endpoints are followed. `copy --move` is exempt on both
sides — `rename(2)` moves a link rather than its target and replaces one
at the destination rather than writing through it, so both specs resolve
with `deref_leaf=False` and the `EXDEV` fallback recreates the link
verbatim (unlinking the destination name first, which is what `rename(2)`
did for it on the fast path).

Because move mode leaves that final component a name, nothing may ask
`os.path.isdir()` about it: on a container spec that resolves the guest's
link against the **host** tree. `copy` asks a separately resolved
`dest_target` instead, so `--move` moves *into* the directory a container
link names and leaves the link (as mv does) rather than inventing
`<rootfs>/tmp` for `/dir -> /tmp` or flattening
`current -> /opt/app/releases/v1` into a file. `src_is_dir` comes from the
`lstat` for the same reason.

Both commands refuse a destination that **is** the source (it would be
truncated while still being read) or sits **inside** it (a directory
copied into itself, which recursed until the interpreter's stack gave
out) — see `refuse_src_dest_overlap()`, called once both ends are final.
`sync --delete` additionally refuses the *reverse* containment: a source
inside the destination has no counterpart in itself, so the prune pass
deleted it (`sync --delete box:/a/b box:/a` removed `box:/a/b`).

`copy --move` accepts a **dangling** symlink as its source, since
`rename(2)` needs nothing to be there — `os.path.lexists` in move mode,
and the readability probe is skipped for a link, whose target is never
read.

Two guards remain specific to `sync`, which writes into a pre-existing
tree. `_sync_dir` **unlinks** whatever non-directory the destination holds
where the source has a real directory (rsync's behaviour) rather than
descending through it — a symlink there may lead out of the container, and
the whole subtree would follow it. In the other direction `_sync_file`
**refuses** a directory standing where the source has a regular file, as
rsync does without `--force`; per-entry and non-fatal, since one entry in
the way must not abandon the transfer (it used to surface as `EISDIR` on a
temp file and exit).

`sync` ends its transfer in `except OSError`, the net `copy` has always
had: every call the three passes make is guarded where warn-and-skip is
right (including `_sync_symlink`'s `readlink`, which a source-side swap
turns into `EINVAL`), and what reaches the top is a race that used to
leave a traceback in place of a message.

Two deliberate behaviour changes came with the rewrite: `copy -r` now
**skips** a device/FIFO/socket with a warning instead of aborting the
whole transfer the way `copytree` did (matching `backup`/`sync`), and a
source directory that cannot be read is still created at the
destination, empty. Such a file *named as an endpoint* is a different
matter and is refused outright, in `copy` for the message and in
`open_regular_at()` against the pinned fd for the race.

`-i`/`--image` switches `list` and `remove` from containers to **cached
images** (manifest-cache entry + its layer blobs). `list --image`
renders an IMAGE/ARCH/ID/SIZE/CREATED table that falls back to a
stacked two-line form when `terminal_width()` can't hold the columns;
`--quiet` prints one reference per line. `remove --image` resolves its
positional as a reference (`:latest` implied, matching **every** cached
arch unless `-a/--architecture` narrows), else an image-ID prefix (≥4
chars, ambiguity refused) or cache key; it unlinks the manifest entry
plus every layer blob no surviving entry references, reports reclaimed
bytes, and names containers installed from that image (unaffected —
only their `reset` needs it back). `-a` without `--image` is an error,
not a silent no-op.

## CLI flow (`cli.main()`)

1. SIGQUIT → `KeyboardInterrupt` so every existing `except` handles
   Ctrl-\ like Ctrl-C (progress cleanup, partial-file removal,
   "Aborted by user").
2. Root warn (non-fatal); nested-proot reject (reads
   `/proc/<pid>/status`, follows one TracerPid hop).
3. proot probe; on Termux + TTY, offers `pkg install`. **`build`,
   `push`, `kill`, and `ps` are exempt** (`kill`/`ps` only signal or
   read running sessions); `build` runs its own gate via
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

Manifest-cache payload (`cache.py`): `{image_ref, arch, manifest, repo,
image_config}`. The key is a hash, so the entry itself is the only
record of which image it holds — `save_manifest_cache()` (the single
writer; `oci_writer.store_in_cache` delegates to it) stores both, a
cache hit in `pull_image` calls `annotate_manifest_cache()` to backfill
entries written before those fields existed, and `iter_cached_images()`
falls back to naming the rest from installed containers'
`manifest.json` (same key derivation). Records expose `image_ref`,
`arch`, `image_id` (config digest hex), on-disk `size`, `missing` layer
count, `created`, `cached_at` — consumed by `list --image` /
`remove --image`. `refs.py` owns the string forms: `canonical_ref()`
(cache-key input), `with_explicit_tag()` (`:latest` default, shared by
`build`/`push`/`remove`), `DOCKER_TO_ARCH`.

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
shows in `ps` with TYPE marked `login*`/`run*`; stop it with
`proot-distro kill`.

`command_kill()` (`commands/kill.py`) stops sessions by signalling the
**whole guest process tree**, not just the root proot. Two proot facts
drive the design: its event loop sets **`SIG_IGN` on every signal except
`SIGQUIT`/`SIGILL`/`SIGABRT`/`SIGFPE`/`SIGSEGV` (→ `kill_all_tracees`),
`SIGUSR1`/`SIGUSR2` and the job-control set**, so `SIGTERM`/`INT`/`HUP`
aimed at the root are silent no-ops; and it sets no `PTRACE_O_EXITKILL`
(`--kill-on-exit` fires only on a graceful exit, and never off-Termux),
so `kill -9 <proot>` leaves the guest running under init.

Target is a PID, a container name (all its sessions), or `--all`, always
resolved against `active_sessions()` so only tracked sessions can be hit.
Session membership comes from `session.session_holders()` — a `/proc`
scan for the registry file's inode, which every guest inherits — so it
survives a dead root and cannot be fooled by PID reuse; `_forest_roots()`
keeps the topmost holders (this also catches double-forked guests whose
ppid is 1), and `_collect_tree()` (pure + cycle-safe, over
`_read_pid_ppid()`) expands each into its descendants. `_root_is_proot()`
is only the fallback when the fd scan comes up empty, and matches
`basename(PD_PROOT_BIN)` as well as `proot` (comm is 15 chars max).

Teardown is staged: deliver the requested signal (default `SIGTERM`,
`-s/--signal` takes a name or number) to the tree → poll
`session_is_live()` for `_GRACE_SECONDS` → `SIGQUIT` the proot roots
(`_escalate`, the one lever proot honors) → poll again → `SIGKILL` sweep
over freshly re-derived roots. Signals that are not terminations
(`STOP`/`CONT`/`TSTP`/`TTIN`/`TTOU`/`CHLD`/`URG`/`WINCH`/`USR1`/`USR2`)
are delivered as asked and **never escalated**. `_report()` verifies
against `/proc` and exits 1 if anything survived; `_is_alive()` counts
zombies as dead. No lock taken; pure-Python (no `pkill`/`pgrep`).

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
