# Project structure

A map of the code base for contributors. For usage, see `README.md`; for the
reasoning behind a module, read the `# Architecture:` comment that opens it.
Most of them are long on purpose: they record the attack or bug that shaped
the code.

## Top-level layout

```
proot-distro.py          Checkout launcher -> cli.main()
pyproject.toml           Packaging, console scripts, pytest config
README.md                User reference (not technical docs)
SECURITY.md              Points to Termux's vulnerability policy
proot_distro/            The package (see below)
  completions/           bash / zsh / fish completion scripts
tests/                   unit/, integration/, security/, live/ (see Tests)
.github/workflows/       tests.yml (CI), publish.yml (PyPI on `v*` tags)
```

## Package layout

```
proot_distro/
  cli.py  parser.py        Entry point, argparse construction
  commands/                One module (or subpackage) per subcommand
  helpers/                 Registry client, Dockerfile build engine, tar/layer I/O
  constants.py  message.py  names.py  progress.py  execenv.py   Small shared pieces
  dirfd.py  statedir.py  atomic.py  locking.py  paths.py        Safe filesystem layer
  guestfile.py  l2s.py  shm.py  sysdata.py  session.py         Container runtime state
  arch.py  compress.py                                          CPU/emulator and zstd knowledge
```

### Entry and dispatch

| Module | Role |
| --- | --- |
| `cli.py` | `main()`: routes SIGQUIT to `KeyboardInterrupt`, refuses nested proot, probes for `proot`, parses, validates required positionals, enables quiet mode, calls the handler from `_COMMAND_HANDLERS`. |
| `parser.py` | `build_parser()` plus `ALIAS_TO_CANONICAL` and `required_args_for()`. Help is handled manually (`add_help=False`) so a missing positional renders the command's help page instead of an argparse error. Termux-only options exist only when `IS_TERMUX`. |
| `commands/help/` | `pages.py` holds every help page as plain data, `render.py` lays them out (stacked on narrow terminals, two columns on wide ones), `__init__.py` exposes `HELP_COMMANDS`. |

### Commands

Every command module exports `command_<name>(args)`. The lock column shows
what the command takes (see `locking.py`).

| Command (aliases) | Module | Does | Lock |
| --- | --- | --- | --- |
| `install` (`add`, `i`, `in`, `ins`) | `install.py`, `install_local.py` | Pull a reference, or unpack a local/URL OCI layout or rootfs tarball, into `containers/<name>/`. Writes `manifest.json`. | container, exclusive |
| `remove` (`rm`) | `remove.py` | Delete a container, or with `--image` a cached image. | container, exclusive; `--image`: build lock |
| `rename` | `rename.py` | Rename a container and rewrite its l2s symlinks. | exclusive |
| `reset` | `reset.py` | Reinstall from the image recorded in `manifest.json`. | exclusive |
| `login` (`sh`) | `login/` | Start a shell or command inside a container. | container, shared |
| `run` | `run.py` | Run the image's Entrypoint/Cmd; delegates to `command_login`. | shared |
| `list` (`li`, `ls`) | `list.py` | Installed containers, or `--image` for cached images. | none |
| `search` (`s`, `se`) | `search.py` | Docker Hub repository search (presentation only). | none |
| `backup` (`bak`, `bkp`) | `backup.py` | Tar a container, optionally compressed. | shared |
| `restore` | `restore.py` | Recreate a container from a backup. | exclusive |
| `copy` (`cp`) | `copy.py` | Copy/move between host and containers. | shared on source, exclusive on destination |
| `sync` | `sync.py` | Mirror a tree (size+mtime or CRC32), optional `--delete`. | same as `copy` |
| `build` | `build.py` | Orchestrate a Dockerfile build, write outputs. | build lock |
| `push` | `push.py` | Upload a built image to a registry. | build lock |
| `ps` | `ps.py` | List live sessions from the session registry. | none |
| `kill` | `kill.py` | Tear down a session's whole process tree. | none |
| `clear-cache` (`clear`, `cl`) | `clear_cache.py` | Delete the download cache, or only orphan blobs / the build index. The orphan and build-index sweeps refuse while another command holds an exclusive lock. | none taken; sweeps probe for held locks |

`login/` is split by concern:

| Module | Role |
| --- | --- |
| `__init__.py` | `command_login`: resolve rootfs, user, env, binds, then exec proot. |
| `proot_cmd.py` | `build_proot_args()`: assembles the proot argv in a fixed order. |
| `passwd.py` | uid/gid/home/shell lookup, read through `guestfile`. |
| `env.py` | Image `Env`, identity variables, `/etc/profile.d` injection. |
| `bindings.py` | Android storage and system `--bind` lists. |
| `detach.py` | `--detach`: double-fork daemonisation. |
| `migrate.py` | One-shot move from the legacy `installed-rootfs/` layout. |
| `quoting.py` | POSIX double-quoting for `--get-proot-cmd`. |

### Helpers

| Module | Role |
| --- | --- |
| `helpers/docker/` | Pure-Python registry client. `refs.py` (Docker's reference grammar, no I/O), `transport.py` (auth, redirects, JSON caps), `pull.py`, `push.py`, `layers.py` (download + apply a layer), `cache.py` (manifest and layer caches, digest checks), `search.py` (Hub only), `media.py` (media types, canonical JSON). |
| `helpers/dockerfile.py` | Pure Dockerfile parser. No execution and no variable expansion. |
| `helpers/build_engine/` | Dockerfile interpreter. `engine.py` (`BuildEngine`: FROM, dispatch, history), `stage.py` (per-FROM state and descriptors), `handlers.py` (metadata-only instructions and the dispatch table), `run_step.py` (RUN under proot), `copy_step.py` (COPY/ADD), `users.py`, `dockerignore.py`, `parsing.py`, `constants.py`, `errors.py`. |
| `helpers/layer_diff.py` | Snapshot the rootfs before and after a RUN, diff, write a gzipped OCI layer with whiteouts. |
| `helpers/build_cache.py` | Recipe-hash index so unchanged steps replay a cached layer. |
| `helpers/oci_writer.py` | Final `build` outputs: manifest cache entry and OCI image-layout tarball. |
| `helpers/tar_extract.py` | The single streaming tar -> rootfs extractor, used for layers and plain tarballs. |
| `helpers/rootfs.py` | Post-extraction fixups: `resolv.conf`, `/etc/hosts`, Android uid/gid entries. |
| `helpers/download.py` | HTTP opener, redirect policy, retries, `download_file`, `sha256_file`. |

### Foundation modules

Roughly bottom-up. Higher entries import lower ones.

| Module | Role |
| --- | --- |
| `constants.py` | `IS_TERMUX`, `RUNTIME_DIR`, `BASE_CACHE_DIR` and the paths derived from them. Computed once at import. |
| `message.py` | Colours, `msg`/`log_*`/`crit_error`, quiet flag, `quote_path` (escapes terminal control bytes in any name read from disk or network). |
| `names.py` | The one container-name rule. |
| `progress.py` | Shared stderr progress bar. |
| `execenv.py` | Which environment variables may reach a proot exec (`LD_*` and `PROOT_*` from an image are refused). |
| `dirfd.py` | `openat(2)`-based file operations: every call takes `(dir_fd, name)` and opens with `O_NOFOLLOW`. Also copy, remove, and tree walks that hold one fd per level. |
| `statedir.py` | Walks down from a trust root (`RUNTIME_DIR`, `BASE_CACHE_DIR`) one `O_NOFOLLOW` step at a time and returns the descriptor. |
| `atomic.py` | Temp file created `O_EXCL` off a directory fd, written through its descriptor, renamed into place. |
| `locking.py` | `flock(2)` locks: `ContainerLock` (shared/exclusive, per name) and `BuildLock` (per image and arch). Non-blocking. |
| `paths.py` | Container layout helpers, `[container:]path` specs, `pin_path()`, overlap checks. |
| `guestfile.py` | Read a file the way the guest sees it (chroot-style symlink following, capped, FIFO-safe). |
| `l2s.py` | Rewrite proot link2symlink targets after a rootfs moves. |
| `shm.py`, `sysdata.py` | Host-side directories bound into the guest as `/dev/shm` and fake `/proc` / `/sys` files. `sysdata` rebuilds the fake `stat` and `uptime` from the host's `/proc/cpuinfo` at every login, so their CPU list matches it. |
| `session.py` | Session registry: one JSON file per PID, liveness by inherited `flock`. |
| `arch.py` | CPU detection, ELF probing, QEMU emulator selection, proot binary lookup. |
| `compress.py` | Everything zstd (usable on Python >= 3.14 only) and the diagnostics when it is missing. |

## Runtime data layout

Paths come from `constants.py`. On Termux the roots sit under
`$TERMUX__PREFIX`. Elsewhere they follow XDG.

```
RUNTIME_DIR                    Termux: <prefix>/var/lib/proot-distro
                               Linux:  $XDG_DATA_HOME/proot-distro
  containers/<name>/
    manifest.json              image_ref, arch, manifest, image_config
    rootfs/                    The container filesystem
    shm/                       Bound as /dev/shm (sibling of rootfs on purpose)
    sysdata/                   Fake /proc and /sys files
  sessions/<pid>.json          Active proot sessions
  locks/<name>.lock            Container locks
  locks/build/<key>.lock       Build locks
  build-tmp/                   Scratch roots for running builds

BASE_CACHE_DIR                 Termux: <RUNTIME_DIR>/cache
                               Linux:  $XDG_CACHE_HOME/proot-distro
  oci_layers/<algo>_<hex>      Layer and config blobs, named by digest
  oci_manifests/<key>.json     One entry per (reference, arch)
  build_cache_index.json       Recipe hash -> cached layer digests
```

A manifest-cache entry plus its blobs is what the commands call an *image*.
Installed containers are independent copies and do not reference the cache.

## Key flows

**install.** `cli` -> `command_install` -> exclusive `ContainerLock` -> create
`containers/<name>/rootfs` as a descriptor. Then either `pull_image` (cache
check, manifest and index resolution, per-layer download and digest check,
`apply_layer` -> `tar_extract`) or `install_from_local_file`
(OCI layout or plain tar). Then `manifest.json`, the `rootfs.py` fixups and
the `sysdata` stubs. A failed install removes what it made.

**login / run.** Shared lock, then pin the rootfs as a descriptor. `passwd`
reads the user through `guestfile`. `env`, `bindings`, `sysdata` and `shm`
prepare binds and environment. `proot_cmd.build_proot_args` builds the argv.
The session is recorded in `session.py`, and the process either `execvpe`s
into proot (the lock fd is inherited, so the lock lives as long as the
session) or is daemonised by `detach`. `run` reads Entrypoint/Cmd from
`manifest.json`, validates their shape, and calls `command_login`.

**build.** Parse the Dockerfile (`dockerfile.py`) and decide if proot is
needed (only RUN-family instructions). `BuildEngine` creates a scratch root
under `build-tmp/` and one `Stage` per FROM. Each instruction becomes a
history entry and, if it changes files, a layer. RUN looks up the recipe hash
in `build_cache`; on a miss it snapshots, runs under proot, snapshots again,
and `layer_diff` writes the layer. COPY/ADD build a file map and write the
layer straight from it. `oci_writer` then stores the manifest in the cache, and
optionally writes an OCI tarball (`--output`) or installs a container
(`--install-as`).

**push.** Load the built image from the caches, verify the config digest,
exchange `PD_DOCKER_AUTH` for a token, HEAD-probe and upload missing blobs,
upload the config, `PUT` the manifest.

**copy / sync.** Resolve each `[container:]path` with chroot semantics, take
locks, `pin_path` both ends, and then work only through `dirfd`.

**backup / restore.** Backup archives `<name>/manifest.json` and
`<name>/rootfs/...`; restore also accepts the legacy `installed-rootfs/<name>`
layout. Restore commits only on success.

**ps / kill.** Both read `session.active_sessions()`. `kill` signals only
tracked sessions, finds members by the inherited registry fd, and escalates
to SIGQUIT because proot ignores most other signals.

## Design rules that run through the code

These follow from handling untrusted images and from Termux binding the
runtime tree read-write into non-isolated containers. A guest can therefore
rewrite anything under `RUNTIME_DIR` and `BASE_CACHE_DIR`.

- **Descriptors, not paths.** State directories are walked with `O_NOFOLLOW`
  (`statedir`) and every entry is addressed as `(dir_fd, name)` (`dirfd`).
  Resolving a name and then using it is a race; holding the inode is not.
  New files are created `O_EXCL`, so a hardlink planted under a name is never
  written through.
- **Archive content is hostile.** `tar_extract` drops `..`, resolves parent
  symlinks with every hop clamped inside the rootfs, defers hard links, and
  skips device nodes. Guest files are read through `guestfile`.
- **A name is not evidence.** Blobs are re-hashed before use, pinned
  references are verified top to bottom, and registry JSON is size-capped and
  shape-checked (`decode_json_object`).
- **Credentials go where they were addressed.** `same_auth_origin` guards
  redirects and upload locations. TLS is the default and plaintext needs
  `--allow-insecure`. Redirects never leave HTTPS or change scheme.
- **Exceptions end in a message.** Network and parse failures are converted to
  a reported error. Everything read from a filesystem or the network goes
  through `quote_path` before it reaches a terminal.
- **Locks and atomic writes.** Container operations take `flock` locks.
  Shared files are written with `atomic.py`.
- **Interrupts are handled.** SIGINT and SIGQUIT both become
  `KeyboardInterrupt`, and long rewrites such as `l2s` finish before exiting.

## Tests

The suite (about 1900 tests) is offline and deterministic by default. See
`tests/README.md` for running it and for how isolation works.

| Directory | Covers |
| --- | --- |
| `tests/unit/` | Pure functions: parsing, naming, digests, caches, quoting, locking, session registry. |
| `tests/integration/` | Whole `command_*` pipelines driven with `SimpleNamespace` args in a sandboxed runtime tree. |
| `tests/security/` | Exploit attempts for each closed hole: traversal, symlink and hardlink escapes, TOCTOU swaps, cache poisoning, forged records. |
| `tests/live/` | Opt-in (`RUN_LIVE_TESTS=1`): real registry pulls and real proot. |

`conftest.py` redirects XDG and `HOME` into a sandbox before importing the
package and refuses to run outside it. `_builders.py` holds shared fixtures
for tars, OCI archives, ELF stubs and rootfs trees.

CI (`tests.yml`) runs unit, integration, security and live tests on Python
3.9, 3.11 and 3.13, plus an end-to-end shell script on pushes that installs
Alpine, Debian and Ubuntu and exercises each command.

## Adding things

- **A command.** Add `commands/<name>.py` with `command_<name>`, a builder in
  `parser.py`, an entry in `_COMMAND_HANDLERS` (`cli.py`), a page in
  `help/pages.py`, shell completions, and a `README.md` section.
- **A Dockerfile instruction.** Add a handler in `build_engine/handlers.py`
  (or its own step module) and register it in the `HANDLERS` table.
- **A network call.** Build the request with `download.new_request`, use an
  opener from `http_opener()`, catch `NETWORK_ERRORS`, and use
  `require_complete_body` so a truncated body is an error.
- **Anything that writes under a state directory.** Go through `statedir`,
  `dirfd` and `atomic`, never `os.makedirs` or `open` on a composed path.
- **A bug or security fix.** Add a regression test, or for security an
  exploit attempt under `tests/security/`.
