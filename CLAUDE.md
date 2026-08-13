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
- `compress.py` — everything the program knows about zstd, which needs
  Python 3.14 (PEP 784) *and* an interpreter built against libzstd:
  `ZSTD_AVAILABLE` (both halves — `TarFile.zstopen` exists without
  libzstd and raises when called), `ZSTD_MAGIC`, `header_is_zstd` /
  `file_is_zstd`, `require_read_support` / `require_write_support`,
  `unsupported_msg` and `open_tar_writer`. **Reading** needs nothing
  else: `tarfile`'s `r|*` / `r:*` auto-detect covers zstd from 3.14 on,
  so OCI layers, rootfs tarballs and backups all read it for free —
  what the sniffing is for is the *diagnosis* on an interpreter that
  can't, where the same archive otherwise dies as
  `ReadError('truncated header')` or a four-line "file could not be
  opened successfully" dump naming neither zstd nor the Python version.
  **Writing** needs an actual workaround: `tarfile.open(mode='w|zst')`
  rejects a compression level ("compresslevel is only valid for w|gz
  and w|bz2 modes") while the seekable `w:zst` takes `level=`, so a
  piped backup would be stuck at libzstd's default 3 and differ from
  the same backup written to a file. `open_tar_writer()` builds the
  `ZstdFile` itself and hands tarfile a plain `w|` stream, so both
  spellings produce byte-identical archives at `ZSTD_LEVEL`.
- `l2s.py` — `--link2symlink` helpers (SIGINT/SIGQUIT shielded).
- `locking.py` — `ContainerLock`, `BuildLock` (POSIX flock), and
  `busy_locks()` (shared-flock probe over both namespaces, naming the
  exclusive holders — what `clear-cache --orphan` asks before sweeping).
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
  resolver, `container_locks_for_spec_pair`. A colon separates a container
  from a path only when nothing before it is a `/` — scp's rule, and the
  only spelling that lets a host path hold a colon at all (`./a:b`, since
  a bare `a:b` still names a container). The container side of a spec
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
  `copy --move f link` renames, as cp and mv each do. Comparing the two as
  strings needs them spelled alike, and `container_rootfs()` only composes
  its prefix lexically, so `_overlap_path` **realpaths the rootfs prefix**
  and joins the walked remainder (which realpath must not touch) back on:
  a symlinked `$HOME` or `~/.local/share` otherwise left a container-spelled
  source and a host-spelled destination inside it looking unrelated.
- `dirfd.py` — the openat(2) layer `copy`/`sync` walk with:
  `opendir_at`/`reopen`/`open_file_at`/`open_regular_at`/`open_new_at`
  (always `O_NOFOLLOW`), `listdir_at`/`lstat_at`/`exists_at`,
  `copy_file_at`/`copy_symlink_at`/`copy_tree_at`/`count_tree_at`,
  `rmtree_at`/`unlink_quietly`, `temp_name`, `close_frames`, and fd-based
  metadata (`copy_metadata`, `set_times_at`, `make_writable`).
  Nothing here takes a path below the root, so no component can be
  re-pointed mid-walk. `REFUSED` / `is_refusal()` cover both errnos a
  refused descent can raise — Linux reports `O_NOFOLLOW|O_DIRECTORY` on a
  symlink as **ENOTDIR**, not ELOOP.
  Every walk carries its open directories on an **explicit stack**, never
  Python recursion: how deep a tree goes is the guest's choice, and one
  past the interpreter's limit (~1000 levels, trivial to create) ended the
  command in a `RecursionError` traceback — not an `OSError`, so no
  caller's net caught it. Frames are laid out `[fd, second fd or None,
  …, owned]` so one `close_frames()` unwinds any of them on the way out;
  open fds still scale with tree *depth*, not size.
  `temp_name()` builds the sibling name a replacing write renames into
  place, trimming the stem so `<name>.~pd_copy` fits inside `NAME_MAX`
  — appending the suffix outright made ENAMETOOLONG out of any entry
  within nine bytes of the limit.
  `copy_data()` takes the source's `stat` and, when `st_blocks` says the
  file occupies less than its length, copies it **hole for hole**.
  Materialising every zero turned a rootfs's sparse `/var/log/lastlog`
  — whose length follows the highest uid — into a copy that could fill
  the device. The map comes from the filesystem (`_data_extents()`:
  `SEEK_DATA`/`SEEK_HOLE`, `pread`/`pwrite` over the extents), since
  reading cannot find a hole smaller than the copy buffer or unaligned
  to it — four bytes at either end of a 9 MiB file made 16 blocks into
  520. Extents are believed **only when they account for less than the
  file's length**: a filesystem with no support answers "it is all
  data", which is what a dense file looks like too, and both fall
  through to `_copy_skipping_zeros()` — the buffer-granular scan, still
  correct, just coarser.
  `copy_tree_at` takes an `on_error(rel, exc)` and **steps over** an entry
  it cannot copy instead of ending the transfer, which is `cp -r`'s
  behaviour; without the callback the exception still propagates.
  `merge=True` writes into a destination tree that **already exists** — a
  directory there is descended into rather than ending the entry on
  mkdir's EEXIST, a file goes through `copy_file_at(replace=True)`, a
  symlink through `copy_symlink_at(replace=True)` (unlink then recreate;
  `symlink(2)` has no O_TRUNC), and a pre-existing directory gets
  `make_writable` on the way down since it may carry a mode of the
  source's that is not writable. A destination whose **type** disagrees
  with the source's is still refused per entry, as `cp` refuses to
  overwrite a directory with a file or the reverse. `copy -r` passes it so
  a second run updates instead of dying; `--move`'s EXDEV fallback does
  not, since `rename(2)` would not have overwritten a populated directory
  either.
  `count_tree_at()` is the cheap pre-pass giving `copy -r` a denominator
  for its progress bar; it counts non-directories, because a directory is
  only "written" once its contents are in.
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
  planted since. A fresh `copy_tree_at` needs no `replace` — every
  directory it writes into was just made by `mkdir` — but `merge=True`
  passes it, since a destination that already exists quite legitimately
  holds entries. The rule covers **metadata** as well: sync's
  `_refresh_file_metadata` is the up-to-date path, which by definition
  rewrites nothing, so it declines to `fchmod`/`utime` a destination with
  `st_nlink != 1` and asks for a full rewrite instead. That call was the
  one write in either command aimed at an inode it had not created, and a
  planted hardlink handed the guest the mode (and, under `--checksum`, the
  timestamps) of any host file within its reach — no race required, and on
  Termux `$TERMUX_PREFIX` is bound into every non-isolated container by
  default with `RUNTIME_DIR` underneath it.
- `sysdata.py` — `setup_fake_sysdata`, `fake_proc_bindings`.
- `cli.py` — `main()`: SIGQUIT routing, root warn, nested-proot
  reject, proot probe, parse, dispatch.

Commands (`commands/`): `backup`, `build`, `clear_cache`, `copy`,
`install` (+`install_local`), `kill`, `list`, `ps`, `push`, `remove`,
`rename`, `reset`, `restore`, `run`, `search`, `sync`; subpackages
`help/{pages,render}` and
`login/{bindings,detach,env,migrate,passwd,proot_cmd,quoting}`.

Helpers (`helpers/`): `build_cache`, `dockerfile`, `download`,
`layer_diff`, `oci_writer`, `rootfs`, `tar_extract`; subpackages
`build_engine/{constants,copy_step,dockerignore,engine,errors,handlers,
parsing,run_step,stage,users}` and `docker/{cache,layers,media,pull,
push,refs,search,transport}`.

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
| `search` | `se`, `s` | none (network only, touches nothing on disk) |
| `remove` | `rm` | container exclusive; `--image` ⇒ `BuildLock` per removed `(ref, arch)` |
| `rename`, `reset` | — | container exclusive |
| `login` | `sh` | container shared (fd inherited by proot) |
| `run` | — | container shared (fd inherited by proot) |
| `list` | `li`, `ls` | none (`--image` reads the manifest cache) |
| `ps` | — | none (reads session registry, prunes dead entries) |
| `kill` | — | none (reads session registry, signals PIDs) |
| `backup` | `bak`, `bkp` | container shared |
| `restore` | — | container exclusive, lazy per first TarInfo |
| `clear-cache` | `clear`, `cl` | none (`--orphan` refuses while any lock is held) |
| `copy` | `cp` | shared src, exclusive dest |
| `sync` | — | shared src, exclusive dest |
| `build`, `push` | — | `BuildLock` keyed on `(image_ref, arch)` |
| `help` | `h`, `he`, `hel` | none |

`install` accepts an image reference, a local path (must start with
`/`, `./`, `../`, or `~`), or an `http(s)://` URL. `--user` takes name,
numeric uid, or `user:group`.

`search` is `docker search`: `helpers/docker/search.py` queries Docker
Hub's `index.docker.io/v1/search` — the one registry API that is **Hub
only**, since searching is not part of the OCI distribution protocol, so
no other registry is reachable this way. Credentials are deliberately
**not** forwarded (Hub ignores Basic auth here — a bogus `user:password`
still answers 200 with the same public results — so sending them would
hand the user's registry password to a third endpoint for nothing);
private repositories therefore never appear. The response is other
users' text, so `_normalize()` is a trust boundary: a repository name
that fails Docker's own name grammar is **dropped** (nothing could
install it, and `--quiet` prints names bare), and the description is
collapsed to one line and run through `quote_path`. Hub caps `n` at 100,
so `-l/--limit` above that walks `page` with a **constant** page size —
the page number multiplies the page size, so shrinking `n` for the last
request would re-fetch rows already held. `--limit` is capped at 1000
(ten requests). `commands/search.py` is presentation only: a
NAME/DESCRIPTION/STARS/PULLS/OFFICIAL table laid out like
`list --image` (DESCRIPTION takes the leftover width; below 20 columns
of it the rows stack), or bare names on stdout under `--quiet`.

`copy`/`sync` resolve both endpoints through `resolve_container_path()`,
pin them with `pin_path()`, and then address the filesystem **only**
through `dirfd` — no path below the roots is ever resolved by name, so
a symlink planted mid-transfer cannot redirect anything. No `shutil`
path API is left in either command: `copytree`/`copy2`/`move` gave way
to `dirfd.copy_tree_at` / `copy_file_at` / `renameat` (`move` falls back
to copy+`rmtree_at` on `EXDEV`), and sync's walk is three fd-carrying
passes — `_collect_rels` (count + rel set), `_mirror_at` (write), and
`_collect_extras_at`/`_remove_extras_at` for `--delete` — each an
explicit stack, like `dirfd`'s own. Missing destination parents are made
by the pinning walk itself (`pin_path(create=True)`), never by
`os.makedirs()` beforehand — for `copy` always, for `sync` only when the
source is a directory, which is rsync's rule (a single file does not
invent the parents it is addressed through; rsync wants `--mkpath`).

Anything copied or moved onto an **existing directory** lands inside it,
as `cp` and `mv` both do: the source's base name is appended through
`resolve_container_child()`. That covers `copy -r` too, which used to
append only for a file source and so died on the `mkdir`'s EEXIST.

A recursive copy **merges** into a destination tree that already exists
(`copy_tree_at(merge=True)`), as `cp -a` does, so running the same copy
twice updates it rather than dying on the top-level `mkdir`. `--move`
keeps `rename(2)`'s rule instead and refuses a populated destination
directory, so a move means the same thing on either side of an `EXDEV`.

Both commands treat an entry they cannot read or write as **per-entry**:
reported, stepped over, and counted, with the command exiting 1 at the
end (`_Ctx.failures` for sync, `_copy_tree_pinned`'s return for copy).
`note_failure()` is keyed on the relative path and **idempotent**, since
both of sync's passes meet the same tree and counting one bad entry twice
made a single unreadable file report as "2 entries".
`copy -r` used to stop at the first locked directory and `sync` used to
come back 0 after skipping one. In sync that rule reaches **every** kind
of entry, not just files: `_sync_dir`, `_sync_symlink` and
`_unlink_robust` raise `OSError` for their caller to report rather than
exiting where they stand, which is what let a destination filesystem with
no symlinks at all (vfat, i.e. `/sdcard`) end the whole transfer on the
first link it met and leave the rest untransferred behind one line of
output.

`copy --move` reads that count before it removes anything — an EXDEV
fallback whose copy half skipped a file must not delete the only
remaining copy of it. **Deliberate** skips count too: no tree this module
writes carries a device/FIFO/socket, which is a warning during a copy and
silent data loss during a move, and on Termux the common move (a rootfs
directory onto `/sdcard`) is exactly the cross-device one.

Directory **modes and timestamps** are preserved by both. sync's
`_apply_dir_metadata` is `copy_metadata` on the descended fds (it used to
set only the mode, so a synced tree was stamped with the moment of the
sync), and `_sync_directory` applies the source root's metadata last of
all — after the mirror *and* the prune, both of which bump the mtime, and
because the mode may take the write bit off the directory. **Every other**
directory gets that from `_remove_extras_at`, which snapshots each level's
`fstat` before touching it and restores mode and times as the frame
unwinds: the prune runs after `_apply_dir_metadata` settled them, so a
directory that happened to hold an orphan came out stamped with the moment
of the sync and, if it was read-only, wearing `make_writable`'s `0755`.
A regular file whose **metadata alone** drifted is fixed in place by
`_refresh_file_metadata()` without rewriting the content: `_needs_update`
compares type, size and mtime (or content), never permissions, so a
`chmod +x` was invisible for good — and under `--checksum`, where matching
content means no rewrite, so was a changed timestamp. Times are compared
at whole-second granularity, the same as `_needs_update`, or a filesystem
storing less precision than the source is found wanting on every run.

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

Every `_sync_file` failure is per-entry that way — a failed **write**
included, which used to end the command outright and so let a container
stop a transfer dead by planting a *directory* under the temp name
(EISDIR, not a leftover to unlink). Its temp file is removed on
`BaseException`, not just `OSError`, so Ctrl-C no longer leaves a
`.~pd_sync` half-copy next to the real file.

Three things decide what `--delete` may remove. Anything the mirror pass
did not write goes into `_Ctx.skipped_rels`, which the prune treats as off
limits: the name is in `src_rels`, so without it the prune walked into
whatever the destination held there and emptied it — a source file that
could not replace a destination directory took the directory's whole
contents with it, and so did a source FIFO, which is never mirrored at
all. `_mirror_entries` **also adds every name it sees** to `src_rels`,
because the counting pass ran earlier and a live source moves on: an entry
created in between was transferred and then pruned as an orphan of the
first pass. And `_prune()` **declines entirely** when `ctx.root_unreadable`
— a failed listing of the root leaves `src_rels` empty and `skipped_rels`
cannot say "all of it", so every destination entry looked like an orphan
and the pass emptied the lot (rsync disables `--delete` on an I/O error
for the same reason). `--delete` also now **requires a directory source**;
with a single file nothing is enumerated, so the flag was accepted and
silently did nothing.

A device/FIFO/socket named as the **whole source** is refused by `sync`
with a message, as `copy` already did — it used to return from
`_sync_single` without a word and report "Finished synchronizing". One met
*inside* a tree is skipped with a warning by both, sync included: it used
to go quietly into `skipped_rels`, leaving the user to diff the trees to
find out it had not arrived.

Two counters have to stay honest. `_Ctx.saw()` is the single way a source
entry is recorded, and it recomputes `total` from `src_rels`, because the
mirror pass adds entries the counting pass never saw — a fixed total left
the display reading "(5/1)" and drew a bar past its twenty cells (now
also clamped in `draw_count_bar`, as `draw_bytes_bar` already was). And
`_mirror_entries` no longer assumes a listing failure was "already
reported by `_collect_rels`": one that pass never met left the
destination stale, said nothing, and exited 0. `copy`'s counting walk
(`count_tree_at`) is skipped entirely under `--verbose` or when
`progress_active()` is False — it is a whole extra pass over the source,
and there is no bar to put a denominator on.

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

`clear-cache --orphan` sweeps only the blobs in `LAYER_CACHE_DIR` that
nothing references, leaving the manifest cache and the build index in
place. Two sources are roots, and both are read **strictly**:
`docker.referenced_blob_digests()` (every digest a manifest names —
layers *and* the config descriptor) and
`build_cache.recorded_layer_digests()`. Neither may fall back to "no
references" on a read failure, which is why the first exists next to
`iter_cached_images()` rather than being derived from it: that function
**skips** an entry it cannot parse, which is right for an inventory and
would be data loss here (a truncated manifest entry would make its whole
image collectable). Either source failing aborts the sweep with nothing
deleted. Digests map **forward** into file names — a name in the cache
is garbage exactly when no live reference produces it — so a leftover
`.tmp` from a killed download is collected for free, and a digest too
malformed for `layer_cache_path()` is simply skipped (no writer could
have created a file for it).
The build index is a root **on purpose**: its layers appear in no
manifest (a multi-stage intermediate, or a step whose image was rebuilt
under another tag), so collecting them would silently empty the build
cache — a plain `clear-cache` is the way to drop that too. Note
`remove --image` answers this differently, computing its keep set from
`iter_cached_images()` alone and so unlinking blobs the index still
pins; that is deliberate (an explicitly named image is not an automatic
sweep) and harmless (`run_step` re-checks `isfile` on a hit).
Containers are not roots either, matching `remove --image`.
The sweep **refuses to run** while `busy_locks()` reports an exclusive
holder: a build in progress has already written its COPY/ADD layers
(only `do_run` records into the index) and stores the manifest naming
them last of all, so mid-build those blobs are indistinguishable from
orphans. It is a snapshot, not a lock — it cannot see a build that
starts a moment later — and shared holders (`login`, `backup`) are
deliberately ignored, since they never write to the cache. Names read
back out of the layer cache go through `quote_path`: on Termux
`RUNTIME_DIR` sits under the bound `$TERMUX_PREFIX`, so a guest can
create a file there and `--verbose` prints it.

## CLI flow (`cli.main()`)

1. SIGQUIT → `KeyboardInterrupt` so every existing `except` handles
   Ctrl-\ like Ctrl-C (progress cleanup, partial-file removal,
   "Aborted by user").
2. Root warn (non-fatal); nested-proot reject (reads
   `/proc/<pid>/status`, follows one TracerPid hop).
3. proot probe; on Termux + TTY, offers `pkg install`. **`build`,
   `push`, `kill`, `ps`, and `search` are exempt** (`kill`/`ps` only
   signal or read running sessions, `search` only queries Hub — refusing
   to look an image up because its runtime is not installed yet would be
   backwards); `build` runs its own gate via
   `build_engine.needs_proot()` (True only with a RUN-family).
4. Per-command `-h`/`--help`/`--usage` intercepted **before** argparse
   so missing positionals never produce errors instead of help. Unknown
   subcommand also rejected pre-parse.
5. `parse_known_args()` + manual handling of tokens after literal `--`
   (`login`/`run` inner command).
6. `REQUIRED_ARGS` check. `restore` intentionally absent — it decides
   from stdin TTY state.
7. `--quiet`: `set_quiet(True)` before dispatch unless command is
   `list` or `ps` (their `--quiet` is different: container names / PIDs
   only). `search` means both — bare names *and* silence, including its
   helper's retry notices — so it keeps the global flag and re-checks
   `args.quiet` itself. `log_info()` becomes no-op; errors/warns/`msg()`
   always show.

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

`busy_locks()` reads that state from the outside: `*.lock` in both
directories, each probed with a **shared** non-blocking flock (the
`session._session_alive` idiom — a refusal means an exclusive holder,
success means unheld), returning `(path, read_lock_info(path))` per
holder. Shared holders answer the probe and so never appear, which is
what `clear-cache --orphan` wants: `login`/`backup` hold shared locks
and write nothing to the cache, while every cache writer (`install`,
and `reset` through it; `build`/`push`) holds an exclusive one. An
errno other than EACCES/EAGAIN counts as unheld, the same rule
`acquire()` uses so a filesystem ignoring flock cannot wedge the
caller. The hint may be empty: `acquire()` opens the lock file `"w"`
*before* it flocks, so a process that lost the race has already
truncated the holder's PID line.

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
`../foo:bar` can't escape the cache root. A `zstd` mediaType is refused
only when `compress.ZSTD_AVAILABLE` is False; with support present the
blob rides the same `r|*` auto-detect a gzip layer does. Whiteouts (`.wh..wh..opq` clears parent
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

Search (`search.py`) is the odd one out: Docker Hub only, anonymous, no
token exchange, no cache — see the `search` notes under "Commands and
locks" for why the credentials stay home and how paging works.
`search_images(query, limit)` returns `(hits, total)`; every field is
re-typed out of the JSON before it leaves the module.

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
the index; `clear-cache --orphan` keeps it and treats
`recorded_layer_digests()` as roots (unlocked read — `_save_index`
publishes through `atomic_replace`, so a reader sees one whole index or
the other, the same reason `lookup()` takes no lock).

## Backup / restore

Pure `tarfile`. Archive shape: `<name>/manifest.json` +
`<name>/rootfs/...`. Backup applies `_fix_permissions()` (chmod-000
subtrees become readable), filters devices/FIFOs/sockets, zeros
uid/gid/uname/gname; refuses to write to a TTY without `--output`.
Restore auto-detects compression (`tarfile r|*` files; magic-byte peek
for stdin), routes members through `_dest_path()` into
`containers/<name>/...`, re-rooting legacy `installed-rootfs/<name>`.
Both ends speak zstd (`.tar.zst`/`.tzst`, `--compress zstd`) where
`compress.ZSTD_AVAILABLE` says the interpreter can — written through
`compress.open_tar_writer` so `-o file.tar.zst` and a piped backup
compress alike, and refused *by name* where it cannot, on both the
extension and the flag, so nothing surfaces as a corrupt archive.
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
