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
  `quote_path` (C-style escapes for control characters) and
  `quote_error`. Names inside a rootfs are the guest's to choose, and a
  name inside a **backup archive** is whoever built the archive's, so
  every filesystem-derived name passes through `quote_path` before it
  reaches the terminal. Only the untrusted text, never a whole message:
  the log helpers' own colour codes are control characters by definition.
  That covers every `--verbose` path — `copy`/`sync` (which log an entry
  each), `backup` (`Adding:`), `remove` (`Removed:`), `restore`
  (`Extracting:`, the worst of them, since the name is off an archive the
  user was handed and no container need exist yet) and `clear-cache`.
  `quote_error(exc)` is the reason half: an `OSError`'s `strerror` is the
  message *without* the filename, which is what a caller naming the entry
  itself wants — `str()` on one already `repr`s the path, so quoting that
  would double-escape it — while a `TarError`, a `BuildError` or a
  `RuntimeError` raised mid-unpack has no `strerror` and interpolates
  names into its message raw, so the whole string is untrusted.
  `build` reports through it because `copy_step._materialise_files` names
  the arcname it could not write, and for an ADD'd archive that name is
  the archive's to choose.
- `progress.py` — `fmt_size`, `ByteCounter`, `draw_bytes_bar`,
  `draw_count_bar`, `clear_bar`, `progress_active`.
- `arch.py` — `get_device_cpu_arch`, `detect_installed_arch` (ELF
  magic), `normalize_arch`, `get_emulator_args`, `ARCH_UNAME_M`.
- `statedir.py` — the one way to reach a directory of the program's own
  state tree, and it is not by name: `STATE_ROOTS`,
  `split_state_path()`, `is_state_path()`, `open_state_dir()`,
  `open_state_parent()`, `remove_state_tree()`. `RUNTIME_DIR` and
  `BASE_CACHE_DIR` are the trust roots — named once, and created by
  name when missing, since a first run must not fail because the
  program's own directory does not exist yet. Everything *below* a root
  is guest content on Termux, where both sit under the `$TERMUX_PREFIX`
  bound read-write into every non-isolated container, so `containers/
  <name>`, `cache` and `build-tmp` are names a session can leave behind
  as symlinks. Each component is therefore opened `O_NOFOLLOW` off the
  descriptor of the level above (`create=True` makes the missing ones
  the same way), and one that is not a plain directory raises `ENOTDIR`
  instead of being followed; a missing one without `create` is
  `FileNotFoundError`, so a caller can tell "nothing installed" from
  "refusing to follow that". The root is the outer one, which matters on
  Termux: `BASE_CACHE_DIR` lives under `RUNTIME_DIR`, so matching it
  first would leave `cache` itself in the part taken on trust. A path
  outside both roots is not this module's business — `open_state_dir()`
  raises `ValueError` for one, since only the trust root makes the walk
  mean anything. `remove_state_tree()` is `dirfd.remove_tree()` with the
  parent reached that way rather than opened by name, for a tree this
  program keeps (a container directory, a rootfs being replaced, a
  build's scratch root). What none of it settles is what happens to a
  *name* afterwards: a caller that keeps addressing entries as
  `(dir_fd, name)` is proof against a later swap, while one that goes
  back to composing paths — an extractor, proot resolving a bind source
  — is proof against the persistent case, which is the one a guest can
  arrange at leisure.
- `atomic.py` — `atomic_replace()`, `atomic_write()` and
  `publish_file()`: temp file +
  `os.replace`; cleans up
  on `BaseException` (Ctrl-C never leaves half-written sentinels). A
  destination inside `RUNTIME_DIR`/`BASE_CACHE_DIR` is reached by
  `statedir.open_state_dir()`'s `O_NOFOLLOW` walk, the temp is
  `open_new_at`'d off the descriptor it validated, and the rename runs
  `src_dir_fd`/`dst_dir_fd` on it — `os.makedirs(exist_ok=True)` plus
  `mkstemp(dir=…)` both resolve the name, so a planted
  `oci_layers -> <host dir>` had every blob written *and published*
  there. A component that is not a plain directory raises `ENOTDIR`
  rather than being followed. A path outside those roots is the user's
  own (`build --output`, `backup -o`) and keeps the plain behaviour.
  What the caller writes through is the temporary's **descriptor**,
  never its name. Handing back a path and letting the caller open it
  again left a window, and an unpredictable name is not a secret: a
  process sharing the directory reads it out of `readdir()`, unlinks it
  and leaves a symlink under it, and the caller's `open()` then wrote
  the file's bytes into whatever that named — the rename afterwards
  publishing the link, so the cache entry pointed at the host file it
  had just overwritten. The temp is created `O_EXCL`, `O_RDWR`
  (`open_new_at(readable=True)`, so a writer that must read its own
  bytes back — a layer blob handed straight to the extractor — never
  reopens the name) and never named again for the write.
  `atomic_write(path, mode)` is the same thing with the descriptor
  already wrapped in a file object, which is what most callers want.
  The publishing rename is still by name, so `_publish_at()` compares
  the entry against the descriptor's `(st_dev, st_ino)` first and
  raises `ESTALE` on a mismatch: the bytes can no longer be redirected,
  but publishing a link this module did not write is not a thing to do
  quietly either, and the check leaves the instant before the rename in
  place of the whole duration of the write — for a layer blob, however
  long the download takes.
  `publish_file(src, dest)` is that ending without the beginning, for a
  writer whose destination name is not known until its bytes exist — a
  build's layer blob, named by the digest of its own content — and it is
  what the three layer-publishing sites use instead of
  `os.makedirs(dirname)` + `os.replace`.
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
  `rewrite_l2s_targets(rootfs_fd, rootfs, old_prefix)` takes the
  **descriptor** of the tree that has just moved, not its path: both
  callers (`rename`, the legacy migration) have one, and this walk
  *writes* — it unlinks an entry and creates a symlink in its place — so
  re-resolving `containers/<name>` would hand those writes to whatever a
  planted name led to. It walks on an explicit stack with `O_NOFOLLOW`
  per directory, and decides by `lstat`: `os.walk()` classified a symlink
  to a directory *as* a directory, so one was neither descended nor
  listed among the filenames, and its stale target was never rewritten.
  `resolve_l2s_target()` decides whether a symlink is one of proot's
  hard-link stand-ins and where its content really is. An l2s chain is
  two hops — the entry points at an intermediate, the intermediate at the
  backing file — and `backup`/`layer_diff` must follow both, so
  containment is decided on the **fully resolved** end (`realpath` on
  target and rootfs alike, the latter because `container_rootfs()`
  composes its prefix lexically). A lexical `normpath` + `startswith`
  was not enough: two `ln -s` calls inside a guest (`x ->
  .proot.l2s.a0001`, `.proot.l2s.a0001 -> /host/path`) put a first hop
  inside the rootfs and a second outside it, and the host file's bytes
  went into the backup archive and into layers `push` uploads.
  `open_l2s_backing()` is what the callers actually read through: it
  re-walks the resolved path from a rootfs fd with `O_NOFOLLOW` and hands
  back `(fd, stat)`, closing the resolve→read window (`backup` holds only
  a shared lock, so a `login` session can run alongside it) and refusing
  a FIFO planted under the name.
- `locking.py` — `ContainerLock`, `BuildLock` (POSIX flock), and
  `busy_locks()` (shared-flock probe over both namespaces, naming the
  exclusive holders — what `clear-cache --orphan`/`--build-cache` ask
  before sweeping). Lock files are opened as `(dir_fd, name)` with
  `O_NOFOLLOW`, since the directory holding them is guest-writable and
  the names in it are predictable — see "Locking".
- `session.py` — active-session registry for `ps`: `register_session`
  (inheritable flock survives `execvpe`, like the container lock; records
  a `detach` flag among the per-session metadata), `active_sessions`
  (reads `SESSIONS_DIR`, prunes dead via a shared flock probe),
  `session_file`/`session_is_live` (that probe for one PID) and
  `session_holders` (scans `/proc/*/fd` for the registry file's inode —
  the members `kill` walks from). `SESSIONS_DIR` is opened once as a
  descriptor (`_sessions_dir_fd`, an `O_NOFOLLOW` walk down from
  `RUNTIME_DIR`) and every entry is named as `(dir_fd, name)`:
  `os.makedirs(exist_ok=True)` accepted a `sessions -> <host dir>`
  symlink and `login` then wrote its record there, and — worse —
  `active_sessions` unlinks every `*.json` whose flock probe answers,
  so one `ps` emptied that host directory of files ending in `.json`.
  Entries open through `open_regular_at`, so a planted symlink or FIFO
  is not a record and is pruned by name only; the publishing
  `os.replace` runs `src_dir_fd`/`dst_dir_fd` on the same descriptor.
- `guestfile.py` — reading a file out of a container the way the *guest*
  sees it: `open_guest_file`, `read_guest_file` (capped),
  `guest_file_exists`, `MAX_ID_FILE_BYTES`. Two commands need it and
  both read the same kind of file — `login` takes a user's uid, gid,
  home and shell out of `/etc/passwd` and `/etc/group` before it exec's
  proot, `build` resolves `USER` and `COPY --chown` against a stage
  rootfs's copies — and in both the file *and every directory component
  leading to it* are image or guest content. The rule is a chroot's: an
  existing symlink is followed, because a legitimate image ships one
  (Nix points `/etc/passwd` at an absolute store path), but an absolute
  target restarts at the rootfs, a relative one continues from the
  directory holding the link, and `..` stops at the rootfs. The one
  target *not* re-anchored is an l2s stand-in (`_l2s_parts`), whose
  target is a host path into `<rootfs>/.l2s` by construction — followed
  to the file holding the content, and only when the whole chain lands
  back inside the rootfs.
  The walk that resolves is the walk that opens. Composing
  `<rootfs><guest path>` and handing the string to `open()` was wrong
  twice over: the host kernel resolves the *middle* of that name, so an
  image (or a guest, between sessions) shipping `etc -> /etc` had
  `login` read the **host's** passwd file and take a host user's uid,
  gid, home and shell from it — persistent, not a race — and `login`
  holds only a shared lock, so even with every component checked a live
  session of the same container could swap one between the check and the
  open. `open_regular_at` refuses a FIFO planted under either name,
  which used to block the command for as long as no peer turned up, and
  `read_capped` bounds the read, since nothing bounds how large an image
  makes the file or how long it makes a single line.
  Two descriptors are open at a time however deep the path goes — the
  rootfs and the level being looked at — because how many components a
  *symlink target* names is the image's choice: one fd per level made
  that decide how many the process holds. `..` is therefore reopened
  through the current level rather than held, and the level it lands on
  is checked against the `(st_dev, st_ino)` recorded on the way down: a
  directory a guest moves elsewhere mid-walk has a different parent, and
  following one would leave the rootfs. `_MAX_PATH_COMPONENTS` bounds
  the total walk, since forty hops of an image's own symlinks can name
  any number of components between them.
- `shm.py` — the directory proot binds into the guest as `/dev/shm`:
  `SHM_DIR_NAME`, `shm_dir`, `make_shm_dir`, `make_guest_tmp`. It used to
  be `<rootfs>/tmp`, and that is the one place it must not be. A bind
  source is a **name**, resolved by proot when it mounts it — long after
  every check here has run — and the rootfs root is writable by every
  session of the container, so a guest need only flip `/tmp` from a
  directory to a symlink in the window before the `execvpe` to have the
  *next* session mount a host directory of its choosing, read-write,
  inside the container. Under `--isolated` that undoes the whole of the
  mode's promise, since nothing else of the host is bound there at all —
  and `login` holds a **shared** lock on purpose, so a session of the
  same container really can be running while the argv is assembled.
  The store is therefore a sibling of the rootfs — `containers/<name>/
  shm`, or a build stage's own directory next to its rootfs, the same
  place `sysdata/` sits. Swapping it means writing to its *parent*, and
  no session confined to the rootfs can reach that: the guest sees the
  directory only through the bind, which gives no way up (proot
  canonicalises the guest path first, so `/dev/shm/..` is `/dev`). What
  is left is a session that already has `$TERMUX_PREFIX` bound
  read-write — one that is already outside the container and can rewrite
  this program itself. The persistent case is still refused: a planted
  `shm` symlink means no bind at all rather than a followed one.
  `make_guest_tmp()` keeps making `<rootfs>/tmp`, since containers have
  always had one made for them; it is simply no longer what `/dev/shm`
  is. The two being one directory is also why shared-memory files showed
  up in the guest's `/tmp` — and, during a build, in the layer the step
  produced, which Docker's tmpfs never does. `reset` and `restore` clear
  the store along with the rootfs, so a container wiped back to its image
  does not come back carrying the scratch of the one before it; `remove`
  and `rename` take it with the container directory, and `backup` never
  sees it (it archives `manifest.json` and `rootfs/` only).
- `names.py` — `_NAME_RE`, `is_valid_name`, `require_valid_name`.
- `parser.py` — argparse, `ALIAS_TO_CANONICAL`, `REQUIRED_ARGS`,
  `required_args_for()` (refines the message when a positional changes
  meaning — `remove --image` wants a reference, not a container),
  `_PdArgumentParser` (per-command help on error).
- `paths.py` — `container_dir/_rootfs/_manifest`, `[name:]path` spec
  resolver, `container_locks_for_spec_pair`. `open_container_dir()` /
  `open_container_rootfs()` / `container_is_installed()` are those first
  three paths as a **descriptor**, through `statedir`'s `O_NOFOLLOW`
  walk: composing `containers/<name>` is not the same as trusting it,
  since that directory is guest-writable on Termux and a session can
  leave the name behind as a symlink. `FileNotFoundError` is left to the
  caller — "no such container" and "no such container *yet*" are both
  ordinary — while a component that must not be followed is fatal, since
  every caller is about to write into that directory or remove it. A
  colon separates a container
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
  `rmtree_at`/`remove_tree`/`unlink_quietly`, `temp_name`,
  `close_frames`, and fd-based metadata (`copy_metadata`, `set_times_at`,
  `make_writable`, `chmod_at`).
  `rmtree_at` is the **only** tree removal in the program. It takes the
  same `on_error(rel, exc)` bargain `copy_tree_at` does — with a callback
  an entry that will not go is reported and stepped over so the rest of
  the tree still goes, without one the `OSError` propagates and the walk
  stops where it stands — plus an `on_remove(rel)` for a caller counting
  or listing what went, and it returns whether anything is left. A
  directory whose contents did not all go is not `rmdir`'ed, since the
  `ENOTEMPTY` says nothing the failure below it has not.
  `remove_tree(path)` is its path-taking front door for the cleanup paths
  that hold a path rather than a descriptor: it names only the *parent* —
  a directory this program owns — walks everything below as
  `(dir_fd, name)`, forces the chmod, and never raises. Every
  `shutil.rmtree()` in the program is gone in its favour, because rmtree
  was wrong twice over on a tree an image or a guest wrote: it **recurses**,
  and a `RecursionError` is not an `OSError`, so neither the
  `except OSError` nor the `ignore_errors=True` those calls all sat behind
  caught one — `install`'s failed-install cleanup, `build`'s `tmp_root`,
  `restore`'s old-rootfs clear and its abort path, `clear-cache`, and the
  tar extractor's whiteout handling each died on a traceback instead. And
  it cannot chmod its way into a directory the image sealed, so a
  `chmod 000` subtree of a *previous* rootfs survived `restore` into the
  restored container.
  `makedirs_under(root, parts, mode)` is the one entry point taking a
  *path* — `os.makedirs()`'s replacement for a directory whose components
  are guest or image content — and `opendir_under(root, parts, create=,
  mode=)` is the same walk handing back the descriptor instead, for a
  caller that must keep addressing entries under a directory the guest
  could otherwise re-point (the lock files, the session registry, a
  container's `sysdata/`). `descend_at(dir_fd, parts, create=)` is that
  walk starting from a descriptor the caller already holds — what
  `opendir_under` is built on, and what a caller with a *pinned*
  directory (`restore`'s container directory) must use, since going back
  to the path re-resolves the very components the pin validated. It
  raises rather than answering `None`, so a missing level and a refused
  one stay distinguishable. Each level is `mkdirat`'ed off the
  descriptor of the level above and reopened `O_NOFOLLOW`, and the mode
  goes on through `_chmod_fd`; `None` means "a component is a symlink or
  is not a directory", which callers treat as *do not use this path*
  rather than falling back to the name. `login` needs it because it makes
  several directories on the **host** side, before proot is exec'd and so
  with nothing confining the write: the container's `shm` store (chmod
  1777 and bound in as `/dev/shm`), `<rootfs>/tmp` (the guest's own),
  `<rootfs>/.l2s` (handed to proot as `PROOT_L2S_DIR`) and a termux-type
  guest's `data/data/<pkg>/cache`. `makedirs(exist_ok=True)` accepts a
  symlink to a directory and `chmod` follows one, so naming them was
  enough to have a host directory relaxed to 1777 and mounted into the
  container. `build`'s RUN step makes the same ones for every step and
  needs it for the same reason, one remove closer: the rootfs is
  assembled from an image the Dockerfile named, so the link is shipped
  rather than left behind, and nothing has run inside it yet.
  proot still resolves the bind source by name when
  it mounts it, so a session running against the same container can race
  the check; what this removes is the persistent case. For a name a
  guest can *write*, that is not enough on its own, which is why the
  `/dev/shm` source lives outside the rootfs — see `shm.py`.
  Nothing here takes a path below the root, so no component can be
  re-pointed mid-walk. `REFUSED` / `is_refusal()` cover both errnos a
  refused descent can raise — Linux reports `O_NOFOLLOW|O_DIRECTORY` on a
  symlink as **ENOTDIR**, not ELOOP.
  Every walk carries its open directories on an **explicit stack**, never
  Python recursion: how deep a tree goes is the guest's choice, and one
  past the interpreter's limit (~1000 levels, trivial to create) ended the
  command in a `RecursionError` traceback — not an `OSError`, so no
  caller's net caught it. Frames are laid out `[fd, second fd or None,
  …, owned]` so one `close_frames()` unwinds any of them on the way out.
  `Levels` bounds what those frames *hold*: an explicit stack fixed the
  recursion and left one descriptor open per level, which the same tree
  exhausts just as surely — the soft limit is 1024 on Android and on most
  distributions. Past `MAX_OPEN_LEVELS` (64, far beyond any real tree, so
  nothing ordinary ever pays for this) the shallowest live level is
  **parked**: its descriptors are closed and its `(st_dev, st_ino)` kept,
  and it is reopened through its child's `..` when the walk pops back down
  to it. `..` is answered from the directory's own parent link rather than
  by resolving a name, so nothing a guest *plants* can redirect it — but a
  directory it **moves** has a different parent, so the level is checked
  against the recorded identity and a mismatch raises `ESTALE` instead of
  being walked. The top two levels are never parked, since a walk that
  abandons a half-pushed frame (`copy_tree_at`'s error path,
  `_mirror_at`'s) resumes on the one below it with no descendant left to
  reopen it through. Every walk in the program goes through it —
  `count_tree_at`, `copy_tree_at`, `rmtree_at`, `backup`'s `_walk_tree`,
  sync's three passes, `clear-cache`'s `_tree_size`, `layer_diff.snapshot`,
  `copy_step`'s context enumeration and `l2s`'s rewrite — because an
  EMFILE partway down is not a walk that can finish and each answered
  differently and badly: `backup` left the deepest members out of the
  archive **without a word**, a build's layer came out missing whatever was
  below the limit, `clear-cache` reported the space it had *not* reclaimed,
  and `remove` could not delete the container at all. `rmtree_at` reports a
  refused revive through `on_error` and stops (there is no directory left
  to remove the level *from*, and nothing below it can be addressed
  either); the read-only walks raise, and every command that runs one has
  a net that turns it into a message.
  `rmtree_at` takes the directory it removes a level *from* off the stack
  rather than from a copy kept in the frame — a parked level has closed its
  descriptors, so a copy made on the way down would name a closed fd, or,
  once the number is reused, some other file entirely.
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
- `sysdata.py` — `setup_fake_sysdata`, `fake_sysdata_bindings`. The
  container's `sysdata/` directory is guest-writable (on Termux it sits
  under the bound `$TERMUX_PREFIX`), so every entry is made, chmod'ed
  and type-checked as `(dir_fd, name)` with `O_NOFOLLOW`: naming them
  let `open(path, "w")` create the host file a planted symlink pointed
  at, and let the resulting `--bind` mount that host file into the guest
  as `/proc/loadavg`. An entry that is not of the type this module
  writes is dropped and remade — nothing else writes here — and one that
  cannot be validated is left unbound rather than followed. "Of the
  type this module writes" counts the **links**: a hardlink is a regular
  file, so `S_ISREG` alone accepted one a session had made to a host
  file, `setup_fake_sysdata` then kept it and `fake_sysdata_bindings`
  named it as the source proot mounts at `/proc/loadavg` — readable
  *and* writable, proot having no read-only bind. Nothing here ever
  makes a second link to what it writes, so `st_nlink != 1` means the
  entry was planted, and both halves judge it that way rather than the
  second trusting the first. A directory needs no such test: it cannot
  be hardlinked.
  `fake_sysdata_bindings` emits the `sys_empty:/sys/fs/selinux` bind its
  callers used to append themselves, so validating and naming happen in
  one place.
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
containers/<name>/sysdata/        ← fake /proc and /sys content to bind
containers/<name>/shm/            ← what the guest's /dev/shm is (see shm.py)
```

Directory name is the sole identifier — composed lexically, but never
*trusted* lexically: `install` checks and creates `containers/<name>`
and its `rootfs` through `open_container_rootfs()`, `reset` asks
`container_is_installed()`, both removals go through
`statedir.remove_state_tree()`, and `rename` opens `CONTAINERS_DIR`
once and moves the entry with `src_dir_fd`/`dst_dir_fd` (a planted
`containers/<old>` used to be moved *as the link*, after which the l2s
rewrite wrote into whatever it pointed at). The legacy migration does
the same across the two directories: `installed-rootfs/<name>` is only
a legacy rootfs when `lstat` under that directory's descriptor says
directory, and the move is `rename(name, "rootfs", src_dir_fd=,
dst_dir_fd=)`. `os.path.isdir()` answered "not
installed" for a `containers/<name> -> <host dir>` a guest had left
behind and `os.makedirs(exist_ok=True)` then accepted it, so the image
was unpacked, the sysdata stubs written and the manifest published
inside that host directory. `login`, `run`, `backup`, `build --install-as` and the `[name:]path`
spec resolver ask the same question the same way, and `pin_path()`
starts its `O_NOFOLLOW` descent from `open_container_rootfs()` rather
than from `os.open(rootfs)` — the rootfs is the one directory that
descent cannot vouch for itself. `remove` is the deliberate exception at
the far end: the walk unlinks a planted entry rather than traversing it,
which is how the user gets rid of one. It asks a question of its own to
get there — `paths.container_entry_lstat()`, an `lstat` of
`containers/<name>` off that directory's descriptor, so the answer
describes the **entry** and not what a link under it names. Neither of
the other two spellings could: `container_is_installed()` refuses to
walk a planted name (rightly — every other caller is about to run
against it), and `os.path.isdir(container_rootfs(name))` followed the
link and found no rootfs at the far end. So an entry a session left
behind was "not installed" from the one command whose job is to delete
it, while `_open_container_path` was telling the user to remove it, and
nothing in the program could. A half-installed `containers/<name>` with
no `rootfs` was stuck the same way. An entry that is not a plain
directory is named in a warning before it goes, since what goes is the
entry and not the directory it points at.

The `manifest.json` sentinel inside that directory is read the same
way. `paths.open_container_manifest()` walks down to the container
directory and opens the entry with `dirfd.open_regular_at`, so a
symlink left under the name is refused (`login` takes the image's Env
from this file and `run` takes the Entrypoint/Cmd it executes, so one
would decide both) and so is a FIFO, which used to hang the command
waiting for a writer that never comes. `read_container_manifest()` is
that plus the JSON parse, raising for a caller that must report the
difference (`run`), and `container_image_config()` is the forgiving
form — `{}` for anything unreadable — for `login`'s Env and its
Entrypoint check. Nothing there exits the command on a re-pointed
container directory, because every caller has already asked
`container_is_installed()`, which does. Plain-tarball installs do **not**
write `manifest.json`. Legacy `installed-rootfs/<name>` layout is
migrated on first `login` (`commands/login/migrate.py`), which then
rewrites l2s symlink targets through the descriptor of the tree it just
moved.

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
| `clear-cache` | `clear`, `cl` | none (`--orphan`/`--build-cache` refuse while any lock is held) |
| `copy` | `cp` | shared src, exclusive dest |
| `sync` | — | shared src, exclusive dest |
| `build`, `push` | — | `BuildLock` keyed on `(image_ref, arch)` |
| `help` | `h`, `he`, `hel` | none |

`install` accepts an image reference, a local path (must start with
`/`, `./`, `../`, or `~`), or an `http(s)://` URL. `--user` takes name,
numeric uid, or `user:group`.

A local **OCI archive** is a stranger's file (`install ./img.tar`, or a
URL), so how much memory reading it costs must not be the archive's to
choose. Two things bound it. `_index_oci_members()` replaces
`tf.getmembers()`, which held a `TarInfo` for every member the archive
declared: the scan stops at `_MAX_OCI_MEMBERS` — a refusal, not a silent
truncation, since a half-built index surfaces as a missing blob — and
keeps only the names `_oci_open_member()` can ever be asked for
(`index.json` and the `blobs/<algo>/<hex>` form `_oci_blob_path()`
builds), later members winning as tar itself means them. And
`_oci_read_capped()` bounds the JSON reads at `_MAX_JSON_BYTES`, applied
to the bytes actually drawn rather than to `member.size`, which is the
archive's to declare either way. Both limits are orders of magnitude
above any real image.

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

`remove` (and `reset`, which reuses `_remove_path`) deletes a container
tree through `statedir.remove_state_tree()`, so how deep a rootfs goes is
the container's business: it used to recurse, and one past the
interpreter's limit ended both commands in a `RecursionError` — not an
`OSError`, so nothing caught it. The *parent* is walked down to rather
than opened by name, which is what keeps a planted `containers` (or
`containers/<name>`, for the rootfs `reset` discards) from aiming the
removal at a host directory. `_remove_path` is now only the wrapper that
turns the walk's relative names back into the full paths
`remove --verbose` prints. `reset` no longer falls back to `shutil.rmtree()` when the walk
reports a failure: rmtree does strictly less (it cannot relax a sealed
directory), so it could only fail where the walk already had, and being
plain recursion it reintroduced the very crash one line below the fix.
`install` refusing a rootfs that is still there is the report the user
gets.

Enumerating containers asks the walk the same way.
`paths.installed_container_names()` lists `CONTAINERS_DIR` off its own
descriptor and decides each entry by opening `<name>/rootfs`
`O_NOFOLLOW`; `os.listdir()` plus `os.path.isdir(container_rootfs(e))`
followed whatever stood in the way, so a planted `containers/<name> ->
<host dir>` holding a `rootfs` listed as installed — a container every
other command then refuses to touch. A name this program would not
accept is skipped as well, since nothing it creates carries one and the
listing goes to a terminal. Unlike the other walks this one **skips**
rather than stops: listing is how the user finds out a planted entry is
there, and `remove` is what gets rid of it. `list`, `remove --image`'s
"installed from this image" note and the manifest cache's
`_ref_hints()` all go through it, each reading the container's
`manifest.json` with `read_container_manifest()`.

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

Neither cache directory is reached by name for any of that.
`oci_manifests` and `oci_layers` sit below `BASE_CACHE_DIR`, which on
Termux is under the bound `$TERMUX_PREFIX`, so both names are guest
content — and `os.listdir()`, `open()` and `os.remove()` all follow a
symlink. With `oci_manifests -> <host dir>` planted, `list --image`
inventoried that directory's JSON files and `remove --image` unlinked
the one it matched. Every read now goes through
`cache.open_manifest_cache_dir()` / `open_layer_cache_dir()`
(`statedir.open_state_dir`'s `O_NOFOLLOW` walk) and every entry is
opened or unlinked as `(dir_fd, name)`; `_load_entry()` uses
`open_regular_at`, so a symlink or FIFO left under an entry's name is
not an entry (nothing but this program writes here) and a blob's size
comes from an `lstat` of the name rather than of its target. A
component that must not be followed is an error, never an empty cache:
`remove --image` refuses before deleting anything, and
`clear-cache --orphan` already treated an unreadable reference set as a
reason to stop — which it could not do while the walk was answering
with a host directory's contents instead.

A plain `clear-cache` measures the tree before emptying it, and both
passes address entries as `(dir_fd, name)`. The cache root is walked
down to (`statedir.open_state_dir`), not named: on Termux it *is* a
component below `RUNTIME_DIR`, and `os.path.isdir()` said "yes" to a
planted `cache -> <host dir>` while `dirfd.opendir()` then followed it
and handed the command a host directory to empty. A component that is
not a plain directory ends the command; a missing root is still
"Cache is empty." `os.walk()` plus
`os.stat()`/`os.chmod()` on each name was the shape of it, and every one
of those calls follows a symlink: the cache is guest-writable — on
Termux it sits under the bound `$TERMUX_PREFIX` — so a planted
`oci_layers/x -> ~/.bashrc` had its target measured into the total and
**chmod'ed u+rw** on the way past. `_tree_size()` walks on an explicit
stack (the cache's depth is not this program's choice either) and only
ever relaxes a *directory* it cannot descend, through
`dirfd.chmod_at`; nothing needs relaxing to be measured or unlinked
otherwise, so the per-file chmod is gone rather than made safe. Removal
is one `rmtree_at(force=True)` per top-level entry, which covers a plain
file at that level too.

`clear-cache --orphan` sweeps only the blobs in `LAYER_CACHE_DIR` that
nothing references, leaving the manifest cache and the build index in
place. That directory is reached by the same walk, one component at a
time from the trust root, and every blob is unlinked as
`(dir_fd, name)` —
`os.listdir()` on a planted `oci_layers -> <host dir>` handed the sweep
a directory of host files to delete, and a component that is not a plain
directory now ends the command the way any other unreadable layer cache
does. Two sources are roots, and both are read **strictly**:
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
cache — `--build-cache` is the way to drop that deliberately. Note
`remove --image` answers this differently, computing its keep set from
`iter_cached_images()` alone and so unlinking blobs the index still
pins; that is deliberate (an explicitly named image is not an automatic
sweep) and harmless (`run_step` re-verifies the blob on a hit and
re-runs the step when it is gone or does not match).
Containers are not roots either, matching `remove --image`.
The sweep **refuses to run** while `busy_locks()` reports an exclusive
holder: a build in progress has already written its COPY/ADD layers
(only `do_run` records into the index) and stores the manifest naming
them last of all, so mid-build those blobs are indistinguishable from
orphans. It is a snapshot, not a lock — it cannot see a build that
starts a moment later — and shared holders (`login`, `backup`) are
deliberately ignored, since they never write to the cache.

`clear-cache --build-cache` is that same sweep with the build index no
longer a root (`_sweep_layers(drop_build_index=True)`; `--orphan`
alongside it is redundant, not a conflict). It is the only way to
reclaim the index and its layers without also discarding the downloaded
base images, which matters because nothing evicts a build-cache entry —
every Dockerfile edit strands the ones before it, and `--orphan` is
*required* to keep them all. It **never reads** the index, only unlinks
it (`build_cache.discard_index()`), and computes the keep set from
`referenced_blob_digests()` alone: an index too corrupt to parse is one
of the reasons to reach for the flag, so deriving the delete set from
`recorded_layer_digests()` would fail exactly where it is needed. What
survives is what a cached image lists — including every layer of an
image a build produced — so the collection is the build's bookkeeping
plus intermediates no image kept. The index goes **first** and a failure
to remove it exits before any blob is deleted, since unpinned-then-kept
is a broken promise while pinned-then-deleted would be the reverse. The
lock refusal is the same call with a sharper reason (a build's recorded
steps name blobs its finished image will list) and so carries its own
message. `build --no-cache` is not a substitute in either direction: it
skips lookups for one invocation and still calls `cache_record()`, so it
grows the index rather than replacing it. Names read
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

Every lock file is addressed as `(dir_fd, name)`, never as a path.
`RUNTIME_DIR/locks` is guest-writable — on Termux it sits under the
`$TERMUX_PREFIX` bound read-write into every non-isolated container —
and the names in it are derived from the container name, so
`open(path, "w")` followed a planted `<name>.lock -> /host/file` and
**truncated that host file** before flocking anything. `_locks_dir_fd()`
walks `locks[/build]` off a descriptor per level and
`_open_lock_file()` opens the entry through `dirfd.open_regular_at`
(`O_NOFOLLOW` plus the type check, so a FIFO planted under the name
cannot block the command waiting for a peer either). Nothing but this
module writes here, so an entry that is not a plain file was planted:
`_drop_planted()` removes it (`rmdir` for a directory, which therefore
only goes while empty) and the real lock file is made in its place.
One that will **not** go is the single case that fails closed
(`_HostileLockPath` ⇒ `acquire()` returns False, `__enter__` names the
path): a filesystem that cannot hold a lock file at all still proceeds
unlocked as it always has, but a lock this program is being *prevented*
from taking must not pass for one it merely could not create. The
`create=True` walk is the only one that heals; the read paths
(`busy_locks`, `holder_hint`) touch nothing.

The lock file is truncated **after** the flock, not by opening it `"w"`
before one: a process that loses the race used to blank the holder's
PID line on its way to reporting a conflict that then named nobody.
`read_lock_info(path)` is gone with it — the hint is
`ContainerLock.holder_hint()`, off the same validated descriptor, which
is what `restore` (acquiring lazily, per archive member) asks.

`busy_locks()` reads that state from the outside: `*.lock` in both
directories, each probed with a **shared** non-blocking flock (the
`session._session_alive` idiom — a refusal means an exclusive holder,
success means unheld), returning `(path, hint)` per holder. Shared
holders answer the probe and so never appear, which is what
`clear-cache --orphan` wants: `login`/`backup` hold shared locks and
write nothing to the cache, while every cache writer (`install`, and
`reset` through it; `build`/`push`) holds an exclusive one. An errno
other than EACCES/EAGAIN counts as unheld, the same rule `acquire()`
uses so a filesystem ignoring flock cannot wedge the caller.

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

Pull is manifest-cache-first: cached + all layers present *and*
verified ⇒ fully offline; cached + missing layers ⇒ fetch token +
missing only; otherwise full pipeline (token → manifest → arch unwrap
→ config blob → layers). Cache writes use `atomic_replace`. Layer
digests are stream-verified via `hashlib.sha256` before promotion.

Verification is **on use, not only on entry**: a blob's file name is
not evidence of its content, because `download_blob()` is not the only
writer of `LAYER_CACHE_DIR`. On Termux that directory sits under the
`$TERMUX_PREFIX` bound read-write into every non-isolated container, so
a guest can plant `sha256_<hex>` for any digest it likes (public
images' digests are public), and `install <archive|URL>` deposits blobs
a remote party chose the digests for. Taken at its name, either one
gets applied into the *next* image that references that digest — with
no download, since a present blob short-circuits the fetch. So
`cache.py` owns the choke points and every consumer goes through one:
`open_verified_layer()` where the blob can be obtained again (pull,
`download_blob`'s own cache hit, `install`'s OCI path, a `RUN`
build-cache hit) — mismatch ⇒ warn, unlink, refetch or re-run — and
`open_required_layer()` where it cannot (`push`, `write_oci_archive`,
`FROM <stage>` re-apply) — mismatch ⇒ refuse, and *leave the file*, a
locally built layer existing nowhere else. `pull_image` runs
`_usable_cached_layers()` once per pull so the "all cached?" question
and each "download or reuse?" question share one hash per blob.

Both hand back an open **descriptor**, never a path, and every consumer
reads through it: `apply_layer`, `_add_fd` (OCI archive) and
`_upload_blob_fd` (push) all take one, and `download_blob` opens its
temporary *before* `atomic_replace` promotes it, so the fd is bound to
the inode those bytes went into. Hashing a name and then reading that
name are two acts on two possibly-different files, and the window is
not theoretical — a session can be running against a container while an
install proceeds, and on Termux it can reach the cache.

A descriptor settles which *inode* is read, not which *bytes*: the same
inode can be truncated and rewritten in place. So `apply_layer` also
passes the expected hex to `extract_tar_fd_to_rootfs`, which hashes the
stream **as it is consumed** (`_HashingReader`, plus a `drain()` — tarfile
stops at the end-of-archive marker, and a digest over a prefix is no
digest at all) and raises if the total does not match. That check is
after the fact by nature: the members are on the rootfs by the time the
last byte proves the archive wrong, which is why every caller discards
the tree on error (`install` removes the container directory, `build`
its temporary stage). The pre-hash upstream is what decides whether to
*use* a cache entry — evict and refetch, or refuse; the streaming hash
is what covers the read. `expected_sha256` is passed as hex rather than
a digest string so `tar_extract` stays clear of `helpers.docker`, which
imports it.
`split_digest()` refuses an algorithm the program cannot compute
(anything but sha256): a digest that cannot be checked must not be a
digest a blob is trusted under. Blobs written by the build engine take
their digest from the bytes just written (`layer_diff`), so name and
content agree by construction; a plain (non-OCI) rootfs tarball carries
no digest at all and is the one archive nothing can check.

The same rule covers everything else addressed by a digest rather than
a name: the arch-specific manifest fetched out of an index and the
image config blob are both checked against the digest that requested
them (`require_data_digest`), the config because `run`/`login` execute
its Entrypoint/Cmd/Env and it is persisted into the manifest cache; and
inside a local OCI archive, `index.json` is the root of trust while the
manifest and config blobs below it are verified (`_oci_read_blob_json`)
and each layer is hashed as it is copied into the cache
(`_oci_cache_layer`), so a mismatched blob never reaches disk. Digests
pass through `validate_digest()` before being converted to filesystem
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
`remove --image`, plus the `name` the entry was read under, which is
what a deletion addresses it by. `refs.py` owns the string forms: `canonical_ref()`
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

`build_engine/users.py` resolves `USER` and `COPY --chown` against the
rootfs's own `/etc/passwd` and `/etc/group` through `guestfile`, the
same walk `login` reads those two files with.

Destination paths are normalised **whether or not they are absolute**
(`do_workdir`, `_do_copy_or_add`). Only the relative branch used to be,
so an absolute one carried its `..` onward: `WORKDIR /../../../x`
composed `<rootfs>/../../../x` and `os.makedirs()` created that
directory as many levels above the rootfs as asked for — anywhere the
invoking user can write, `chmod 0755` behind it — and a base image's
`ONBUILD WORKDIR` reaches it, so the Dockerfile need not contain the
line. `..` is resolved against the guest's `/`, clamping at the image
root the way a chroot does and the way Docker reads it. COPY/ADD
restores the **trailing slash** `normpath` strips, since `_copy_url` /
`_dest_arcname` / `_add_directory_tree` read it back and `COPY x
/opt/app/` would otherwise change meaning.

Normalising is lexical, so it says nothing about a **symlink** standing
in the path, and `do_workdir` then created and chmod'ed every level by
name: an image shipping `/x -> /tmp/victim` had `WORKDIR /x/sub` make —
and `chmod 0755` — a host directory, again reachable through a base
image's `ONBUILD WORKDIR`. Refusing links is not an option (`/var/run
-> /run` is in nearly every distro image), so the path goes through
`tar_extract._safe_resolve` first — the same clamped walk the extractor
resolves a member's parent with, following each link but re-anchoring an
absolute target at the rootfs — and `dirfd.makedirs_under()` then builds
the result off a descriptor per level, refusing a component planted
after the resolve. The layer's arcnames come from the **resolved** path,
which is where the directories really landed.

A COPY/ADD's `file_map` covers the **whole instruction** and is consumed
only once it ends — the tree is materialised from it, then the layer is
packed from it — so an entry that carries bytes carries them that long,
and every entry of an auto-extracted archive carries them at the same
time. ADD read an entire URL response, and then every regular tar member,
straight into that dict, so one instruction pointed at a large archive
took the build process out. There is deliberately no in-memory `content`
kind left: `_spool_stream()` parks such content in
`<tmp_root>/add-spool/` and `_spool_entry()` records an ordinary `file`
entry naming it. The bytes were headed for disk anyway (the instruction
writes them into the rootfs *and* into a layer) and `tmp_root` goes when
the build ends. The timestamp is stamped onto the spool file rather than
kept in the entry, because that is where `_add_file_map_entry` reads a
`file`'s mtime from — through an `except (OSError, OverflowError,
ValueError)`, since the value comes out of an archive header and
`os.utime()` raises `OverflowError`, not `OSError`, on one `time_t`
cannot hold.

Where a COPY/ADD's sources may come *from* is decided by the same kind
of walk, and read through descriptors for the same reason.
`copy_step._SourceTree` wraps the tree one instruction reads out of —
the build context, another stage's rootfs, an image pulled for
`--from` — and resolves each source spec beneath it with
`tar_extract.safe_resolve_parts`: a symlink component is followed but an
absolute target re-anchors at the tree's root and `..` can never climb
out of it, which is both the confinement and what a path means inside an
image (`/usr/bin/python -> /usr/local/bin/python` still resolves). A
`..` written in the spec itself is refused outright rather than clamped,
the way the `[name:]path` resolver refuses one and the way Docker
refuses a source outside the context. The **final** component is left
unresolved, since COPY copies a symlink as a symlink instead of reading
through it. What this replaces is a lexical `normpath` + prefix check,
which decided containment on the spelling of the composed path and so
said nothing about the links standing in it: a context holding `escape
-> /` let `COPY escape/etc/passwd /leaked` read the host's file, and a
source image shipping the same link let `COPY --from` do it with nothing
unusual in the Dockerfile or the context at all — in both cases the
bytes went into the layer `push` uploads. `simple_glob()`'s matches go
through the walk too, since `glob` answers on spelling the same way; a
source whose every match resolves outside the tree is reported missing
rather than silently copying nothing.

Nothing that walk finds is recorded as a path to open. A `file` entry
carries the `root` it was found under and the `rel` components below it
(`src` is the joined form, for a message), and **both** consumers open
it through `layer_diff.MapSources`, which re-walks those components from
the root with `O_NOFOLLOW` and hands back a descriptor — a one-entry
directory cache, so a whole directory's worth of sorted entries costs
about one `openat` apiece. Enumerating a whole instruction and consuming
the map afterwards, twice, leaves a long window in which a component can
be replaced with a symlink; resolving the name again then reads whatever
it leads to now. The enumeration itself is descriptor-borne for the same
reason (`_add_directory_tree` walks an explicit stack of `O_NOFOLLOW`
directory fds, recording a symlink as a symlink and never descending
one, which is what `os.walk(followlinks=False)` gave minus the name
resolution), ADD's auto-extract sniffs and unpacks the archive through
one descriptor on the file (`parsing.is_tar_header` takes the bytes, not
a name), and the progress denominator is the size the enumeration
measured rather than a `getsize` of a name. A `file` entry without
`root`/`rel` is a programming error and raises `KeyError` rather than
falling back to a path.

`layer_diff.layer_path_parts()` is the one rule for what a name may be,
applied by both halves of a COPY/ADD — `_materialise_files` (the tree)
and `write_files_layer` (the tar), which used to filter separately, so
only the tree did. Nothing here would extract a `..` member
(`tar_extract` drops it), but a layer is the artefact that leaves the
machine: `push` uploads it, and what `..` means is then decided by
whatever loads it. The packer's synthesised-ancestor loop goes through
the same rule, or it invents a `..` directory entry above the bad name.

`_materialise_files` resolves each entry's **parents** with
`safe_resolve_parts` and leaves the final component alone on purpose, so
the entry itself is replaced rather than written through — which means
every kind has to drop a link standing there first. The three that write
data already unlinked whatever was in the way; the `dir` branch did not,
so an image shipping `etc -> <host dir>` plus an ADD'd tar carrying an
`etc/` member had `os.makedirs(exist_ok=True)` accept the link and the
chmod behind it land on the host directory — and the tree then disagreed
with the layer, which records a plain directory at that name. It now
drops the link the way `tar_extract` does when the same layer is applied.

That resolve says where the entry *belongs*; it does not make writing
there safe, because it decides by name and everything after it used the
answer by name too — `os.makedirs`, `os.remove`, `shutil.copyfile`,
`os.chmod` each resolve the path again. So the resolved components are
re-walked off a descriptor (`dirfd.opendir_under`, `O_NOFOLLOW` per
level, creating what is missing) and the entry is written as
`(dir_fd, name)`: `mkdir`+`chmod_at` for a directory, `symlink` after an
unlink, and `open_new_at` for a file — `O_EXCL`, so the bytes land in a
new inode and never through a hardlink to somewhere else, which is the
one thing `O_NOFOLLOW` cannot refuse. The mode goes on with an explicit
`fchmod`, since the one `open()` creates with is umask-masked. A
resolved parent that is not a directory by then raises `BuildError`
rather than being followed. `safe_resolve_parts` is `_safe_resolve`
returning the components instead of the joined path, for exactly that
re-walk; `_safe_resolve` is now a join on top of it.

The packer has the same shape in reverse: `snapshot()` lists the tree
and `_add_entry()` reads it afterwards, and both named every entry.
`snapshot()` now walks on directory descriptors (an explicit stack, so
only the current path's fds are open) and fingerprints a file through
`open_regular_at`, and `_add_entry()` takes its parent from
`_ParentFds` — a one-entry cache over the same `O_NOFOLLOW` walk, which
costs about one `openat` per entry since the rels arrive sorted — and
sizes a regular file from the **fstat of the descriptor it is about to
read**, not from the earlier `lstat` of the name. `_add_file_map_entry`
opens its source before measuring it for the same reason. What could
move underneath either of them is a process an earlier RUN left running
(nothing kills one off Termux, `--kill-on-exit` being a Termux-only
proot extension) or, on Termux, a session of another container, since
the stage tree lives under the bound `$TERMUX_PREFIX`. A layer is the
worst place for that to go unchecked: `push` uploads it.

A step is over when nothing of it is still running, which is not the
same as its command having exited. proot's event loop ends when its
last **tracee** does, so anything a step leaves behind keeps proot
alive: off Termux `RUN service x start` never returned at all, and the
build waited on the daemon for as long as it ran. Termux's proot has
`--kill-on-exit` for exactly this; upstream proot has no equivalent, so
`run_step` watches the step instead of waiting on it.
`_wait_for_step()` polls two cheap things — proot's own children
(`/proc/<pid>/task/<pid>/children`), whose only member is the root
tracee, and this process's children, which is where the step's orphans
land because `_become_subreaper()` set `PR_SET_CHILD_SUBREAPER` before
the step spawned anything. An empty first list plus a non-empty second
means the command has finished and what is left is leftovers;
`_stop_step()` then SIGTERMs them (each one plus any group it leads,
since a daemon setsids into its own), waits out `_STRAY_GRACE_SECONDS`,
SIGKILLs the rest and reaps them. proot is **skipped** by name until it
has reported the step's exit status, which it still does correctly —
the root tracee's status is what it returns, however the rest of them
go. This process and the group it is in are never targets: a step runs
in a session of its own, so its group id can only be proot's, and one
that is this program's own group means the caller got the pgid wrong —
which must not come out as a SIGTERM to everything sharing the
build's terminal. An orphan created while the command is still running is left alone,
since proot's children list is not empty then. A kernel that refuses
the prctl leaves the wait exactly as it was. `_reap()` names the pids
it collects rather than calling `waitpid(-1)`, which could take proot's
status out from under `Popen.wait()` and turn a failed step into a
passing one. A here-doc body is handed to the step as an unlinked
temp file rather than through a pipe, since watching the step leaves no
`communicate()` to feed one and a body past the pipe buffer would
deadlock.

RUN under Termux uses `--link2symlink`. To keep produced layers
portable, `layer_diff.snapshot()` skips `<rootfs>/.l2s/`, and
`_add_entry()` follows symlinks pointing into it to pack the backing
file's content as a regular file (hard-link semantics lost, content
preserved). Build steps run isolated and non-interactive
(`stdin=/dev/null` unless here-doc).

A build's scratch root is made the same way: `RUNTIME_DIR/build-tmp` is
a predictable name and `mkdtemp(dir=…)` resolved it, so a planted
`build-tmp -> <host dir>` had every stage rootfs, spooled ADD and packed
layer assembled inside that host directory — and the cleanup at the end
remove what it found there. `_make_build_tmp()` walks down to it and
creates the run's directory with `mkdirat` off the validated descriptor
(falling back to the system temp dir, as it always did when the runtime
tree could not hold one); `statedir.remove_state_tree()` discards it.
Everything *inside* is this process's own — a fresh 0700 name — so the
stage trees below it need no walk of their own.

Build cache: `compute_recipe_hash(parent_digest, instr, extra)` keys
into `build_cache_index.json`. Hit ⇒ apply cached layer, skip proot.
`build_cache.record()` holds its own flock over the index.
`clear-cache` removes top-level entries under `BASE_CACHE_DIR` including
the index; `clear-cache --orphan` keeps it and treats
`recorded_layer_digests()` as roots (unlocked read — `_save_index`
publishes through `atomic_replace`, so a reader sees one whole index or
the other, the same reason `lookup()` takes no lock);
`clear-cache --build-cache` unlinks it via `discard_index()` — also
unlocked, since an unlink is not a read-modify-write — and leaves the
`.lock` file, which is empty and recreated on demand.

The index and its lock are named as `(dir_fd, entry)` like every other
file this program keeps, through `statedir.open_state_parent()`. Both
names sit in the guest-writable download cache and both are entirely
predictable: `os.makedirs(dirname)` + `os.open(O_RDWR|O_CREAT)` created
whatever a planted `build_cache_index.json.lock -> <host path>` named,
or opened an existing host file and held a lock on it, and a FIFO under
either name blocked the build until a peer that never comes. The lock
goes through `locking.open_lock_file_at()` — `_open_lock_file`'s public
form, same rules (`O_NOFOLLOW` + type check, a planted entry dropped and
remade), different policy: a name that cannot be cleared here means
"carry on unlocked", which is what a filesystem without flock already
gives, while a container lock fails closed. `_read_index()` reads
through `open_regular_at`, so an unreadable index stays distinguishable
from an absent one — `recorded_layer_digests()` answers `readable=False`
and the layer sweep stops rather than collecting.

## Backup / restore

Pure `tarfile`. Archive shape: `<name>/manifest.json` +
`<name>/rootfs/...`. Backup applies `_fix_permissions()` (chmod-000
subtrees become readable), filters devices/FIFOs/sockets, zeros
uid/gid/uname/gname; refuses to write to a TTY without `--output`.
Restore auto-detects compression (`tarfile r|*` files; magic-byte peek
for stdin), routes members through `_dest_path()` into
`containers/<name>/...`, re-rooting legacy `installed-rootfs/<name>`.
`_dest_path()` answers in **components below the container directory**
(`("rootfs", "etc", …)`), never a composed path, because that directory
is what the extraction descends from.
Both ends speak zstd (`.tar.zst`/`.tzst`, `--compress zstd`) where
`compress.ZSTD_AVAILABLE` says the interpreter can — written through
`compress.open_tar_writer` so `-o file.tar.zst` and a piped backup
compress alike, and refused *by name* where it cannot, on both the
extension and the flag, so nothing surfaces as a corrupt archive.
Traversal blocked (`..`/`.`/empty dropped; container name must match
`_NAME_RE`). First entry per container triggers rootfs clear + lock.

Backup walks the rootfs through **directory descriptors** (`_walk_tree`,
over `dirfd`), never by path, and every one of its three passes — relax
permissions, measure, archive — is driven by the same walk. It holds only
a *shared* lock, deliberately, so a `login` session can be running while
the archive is written; every path-based step was therefore two acts on
two possibly-different files. `_fix_permissions` stat'ed a name and then
`chmod`'ed it, and the archiver `lstat`'ed a name and then `open`'ed it,
so a guest swapping either for a symlink had the mode change land on a
host file and the host file's bytes packed into the archive under an
innocent name. Each entry is now named as `(dir_fd, name)`: the chmod
goes through `dirfd.chmod_at` (O_PATH|O_NOFOLLOW plus `_chmod_fd`, since
`fchmodat` has no `AT_SYMLINK_NOFOLLOW`) and the read through
`open_regular_at`, which also refuses a FIFO planted under the name
rather than blocking on a peer that never comes.
`_walk_tree` visits an entry **before** descending into it, which is what
makes `_fix_permissions` work at all: `os.walk()` lists a directory
before handing it over, so a chmod-000 one was skipped outright and its
whole subtree stayed out of the archive despite the pass that exists to
prevent exactly that. Directories ride an explicit stack, so only the
fds along the current path are open and tree depth is the guest's
business. `_add_path` takes the walk's `lstat` as the member's type and
builds the `TarInfo` itself for a directory, a symlink and an l2s
inline; a regular file goes through `tf.gettarinfo(fileobj=…)`, off the
open descriptor, which keeps tarfile's `(dev, ino)` table so a second
name for a file already in the archive stays a hard-link member instead
of a second copy. A hardlink to a host file is still the file itself
under a second name and no descriptor can tell it apart — the one thing
the walk does not close.

Restore writes nothing by path either. `containers/<name>` is opened
once — at the **commit point**, so an archive that never produces a
rootfs member still leaves no trace — through
`paths.open_container_dir()`, and every member goes in as
`(dir_fd, name)` under that descriptor (`_Destinations`, a one-entry
parent cache, since a backup's members arrive in walk order). Two
separate things needed it. The container directory is guest-writable on
Termux, and `_safe_dest()` clamped every member "inside the container
directory" — which is exactly where a planted
`containers/<name> -> <host dir>` led, with the rootfs-is-not-a-symlink
check running only *after* the writes. And a member's parents are
archive content: they are resolved with
`tar_extract.safe_resolve_parts` — which follows a symlink an earlier
member shipped but re-anchors it, now at the **rootfs** rather than at
the container directory, so a `lib -> /usr/lib` no longer lands members
in `containers/<name>/usr/lib` beside the rootfs — and the resolved
components are then re-walked with `O_NOFOLLOW` (`dirfd.descend_at`),
since resolving by name and writing by name are two acts and the member
after the resolve can be the one that plants the link. The writers are
`open_new_at` (O_EXCL, so a hardlinked name is never written through),
`mkdir`+`chmod_at`, `os.symlink(…, dir_fd=)` and `copy_data` for a
hard-link member; the old rootfs is cleared with
`rmtree_at(root_fd, "rootfs")`, the deferred directory modes are
replayed through the same walk, and the manifest is published with
`atomic_replace` instead of `open(path, 'wb')`.

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
- Every cache writer must use `atomic.atomic_replace()`, which is also
  what keeps a write inside the state tree from being redirected by a
  planted symlink — see its entry above.
- New commands plug into `cli._COMMAND_HANDLERS`, `parser` (with
  `_pd_command` stamped), `REQUIRED_ARGS` if positional,
  `commands/help/pages.HELP_PAGES`, and `ALIAS_TO_CANONICAL` for aliases.
