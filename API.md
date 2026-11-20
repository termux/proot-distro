# PRoot-Distro Python API

Use PRoot-Distro from your own Python programs. The `proot_distro.api` module
manages containers, images, files, backups, builds and sessions. It uses the
same containers, image cache and session list as the `proot-distro` command.

The API runs inside your program. It is not a client for a server and starts
no background service. It needs Python 3.9 or newer.

Each call except `search` runs its work in a short-lived Python process
(a *worker*) and waits for it. So:

* Your program's working directory, environment and settings stay as they
  are, and several `ProotDistro` objects can use different storage.
* Each call costs one interpreter start: a fraction of a second, more on
  a slow phone. Group work instead of calling in a tight loop.
* `sys.executable` must be a working Python interpreter. Frozen or
  embedded applications whose `sys.executable` is not one cannot use the
  API.

Ready-to-run scripts are in [`samples/`](samples/README.md).

## Contents

1. [Quick start](#quick-start)
2. [Compatibility](#compatibility)
3. [Containers and images](#containers-and-images)
4. [Files and backups](#files-and-backups)
5. [Builds and registries](#builds-and-registries)
6. [Running commands](#running-commands)
7. [Detached sessions](#detached-sessions)
8. [Inspecting a session command](#inspecting-a-session-command)
9. [Events](#events)
10. [Cancelling operations](#cancelling-operations)
11. [Registry credentials](#registry-credentials)
12. [Separate storage](#separate-storage)
13. [Errors](#errors)
14. [Result types](#result-types)

## Quick start

```python
from proot_distro.api import ProotDistro, ProotDistroError

pd = ProotDistro()

try:
    pd.install("alpine:3.21", name="my-alpine")
    with pd.start("my-alpine", ["uname", "-a"]) as session:
        session.wait()
    pd.remove_container("my-alpine")
except ProotDistroError as exc:
    print(f"Failed: {exc}")
```

Methods return immutable records (or tuples of them). `start` and `run`
return a `SessionProcess` handle. Methods never print messages and never
call `sys.exit()`. All failures inherit from `ProotDistroError`.

Text in results, events and error messages can come from third parties:
image metadata, a container's own files (which the container can change),
registries and Docker Hub. Error and event messages have their control and
format characters escaped, as `\e`, `\x9b` or `\u202e`. Result fields and
build output (events with phase `build-output`) are passed on as they came.
Remove control characters from those before you print them to a terminal.

## Compatibility

`API_VERSION` is the interface version as a `"MAJOR.MINOR"` string. It is
independent of the PRoot-Distro package version.

```python
from proot_distro.api import API_VERSION

major, minor = (int(part) for part in API_VERSION.split("."))
```

The current version is `1.0`.

* New features that keep old code working raise the minor number.
* Removing or changing an exported name, method signature, field, enum value
  or exception relationship raises the major number.
* Bug and security fixes that keep the contract do not change the version.
* Deprecated names stay available for the rest of their major version.

Only names in `proot_distro.api.__all__` are covered. Everything below
`proot_distro.api` and all other modules are internal. The package ships a
`py.typed` marker, so type checkers use the public annotations.

## Containers and images

| Method | Action |
|---|---|
| `install(SOURCE, name=, architecture=, allow_insecure=)` | Install from an image reference, URL or local archive. Returns `ContainerInfo`. |
| `list_containers()` | Installed containers, as a tuple of `ContainerInfo`. |
| `list_images()` | Cached images, as a tuple of `ImageInfo`. |
| `list_sessions()` | Active sessions, as a tuple of `SessionInfo`. |
| `remove_container(NAME)` | Permanently remove a container. Returns its former `ContainerInfo`. |
| `rename_container(OLD, NEW)` | Rename a container. Returns the new `ContainerInfo`. |
| `reset_container(NAME)` | Reinstall an OCI container from its image. All changes in it are lost. |
| `remove_image(IDENTIFIER, architecture=)` | Remove cached images by reference, ID prefix or cache identifier. |
| `clear_cache(orphan=False, build_cache=False)` | Clear the whole cache, only orphan layers, or only the build cache. Also removes the scratch files of killed builds, which the byte counts leave out. |
| `search(QUERY, limit=25)` | Search public Docker Hub repositories. Returns `SearchResult`. |

```python
pd = ProotDistro()

container = pd.install("alpine:3.21", name="my-alpine")
for item in pd.list_containers():
    print(item.name, item.image_reference)

for image in pd.list_images():
    print(image.identifier, image.architecture)

for session in pd.list_sessions():
    print(session.pid, session.container, session.command)

result = pd.search("alpine", limit=5)
for hit in result.hits:
    print(hit.name, hit.stars)
```

A string `SOURCE` is a local archive only when it starts with `/`, `./`,
`../` or `~`, and a URL when it starts with `http://` or `https://`.
Anything else is an image reference, so `rootfs.tar.gz` is looked up in a
registry; write `./rootfs.tar.gz` or pass a `pathlib.Path`.

`search` runs in your process and has no `cancel_event`. It never sends
registry credentials. Docker Hub refusals (for example HTTP 429 or 5xx) raise
`NetworkError`.

## Files and backups

Paths use the same `container:path` syntax as the command line. A
`pathlib.Path` always means a host path. A plain string is a container path
only when text before the first colon has no `/` (`box:/etc`); anything else
is a host path. Host paths are made absolute and `~` is expanded, so a
relative path means relative to your program's working directory. A trailing
`/` is kept, because `copy` and `sync` treat `dir/` and `dir` differently. To
name a host file called `a:b` in the current directory, write `./a:b`.

| Method | Action |
|---|---|
| `copy(SOURCE, DESTINATION, recursive=False)` | Copy a file, or a directory with `recursive=True`. |
| `move(SOURCE, DESTINATION)` | Move a file or directory. |
| `sync(SOURCE, DESTINATION, checksum=False, delete=False)` | Make the destination match the source. |
| `backup(NAME, OUTPUT, compression=None)` | Write a backup to a new file or a writable binary stream. |
| `restore(ARCHIVE)` | Restore one container from a backup file or readable binary stream. |

```python
pd.copy("./settings.ini", "my-container:/etc/myapp/settings.ini")
pd.copy("./assets", "my-container:/opt/assets", recursive=True)
pd.move("my-container:/tmp/result", "./result")
pd.sync("./site", "my-container:/srv/site", checksum=True, delete=True)

backup = pd.backup("my-container", "./my-container.tar.gz")
pd.remove_container("my-container")
pd.restore(backup.output)
```

Backups can use memory buffers, sockets or pipes instead of files:

```python
import io

archive = io.BytesIO()
pd.backup("my-container", archive, compression="gzip")
archive.seek(0)
pd.restore(archive)
```

For a path, compression is chosen from the file name. For a stream, the
default is no compression. Pass `"gzip"`, `"bzip2"`, `"xz"` or `"zstd"`
explicitly when you want it.

Restoring over an existing container replaces it in place. If that restore
fails or is cancelled, the container is left half-replaced: restore the same
archive again to repair it. A restore that created the container removes it
again when it fails or is cancelled, so no incomplete container is left
behind (see [Cancelling operations](#cancelling-operations)).

**Stream rules**

* Streams are read from their current position. They need not be seekable.
* Give each call an input stream of its own. It is read in chunks until
  its end, possibly past the end of the archive. A `read()` that is still
  blocked when the call returns finishes in the background, and its data
  is discarded. No further `read()` is made after the call returns.
* The API never closes, seeks or flushes your streams.
* `write()` should return the number of bytes it took; short writes are
  continued. `None` means "took everything", except on a raw unbuffered
  stream (`io.RawIOBase`), where it means "would block" and stops the
  call.
* A successful call means every output byte was passed to `write()`.
* After a failure an output stream may hold a partial archive. Path outputs
  are written atomically and are not left half-written.
* An exception raised by your `read()` or `write()` stops the operation and
  reaches you unchanged.
* `BackupResult.output`, `RestoreResult.archive` and each streamed entry of
  `BuildResult.outputs` are `None` for streams.

## Builds and registries

| Method | Action |
|---|---|
| `build(PATH, ...)` | Build a Dockerfile. Can export OCI archives and install a container. |
| `push(IMAGE, architecture=None, allow_insecure=False)` | Push a cached image tag. Returns `PushResult` with the digest and bytes uploaded. |

```python
import io

pd.build(
    "./context",
    tags=["myuser/myapp:1"],
    build_args={"VERSION": "1"},
    outputs=["./myapp.oci.tar"],
    install_as="myapp",
)

dockerfile = io.BytesIO(b"FROM scratch\nCOPY rootfs/ /\n")
oci_archive = io.BytesIO()
pd.build("./context", dockerfile=dockerfile, outputs=[oci_archive])

pd.push("myuser/myapp:1")
```

* `build_args` is a mapping of strings.
* `tags` and `outputs` take iterables. A single path or stream also works.
* `dockerfile` can be a path or a readable binary stream.
* Streamed outputs are plain tar archives. Path suffixes choose compression.
* Other options: `architecture`, `target`, `emulator`, `no_cache`.
* `PushResult.manifest_digest` is the `sha256:` digest of the manifest that
  `push` sent, not text taken from the registry. If the registry reports a
  different digest, `push` raises `IntegrityError`. The tag may still have
  changed on the registry.

Build arguments are visible to `RUN` steps and may end up in the image
history. Do not pass secrets as build arguments.

## Running commands

`start` opens a login session. `run` runs the image's Entrypoint and Cmd.
Both return a `SessionProcess`. They never replace your own process.

```python
import subprocess

process = pd.start(
    "my-container",
    ["uname", "-a"],
    environment={"APP_MODE": "test"},
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
stdout, stderr = process.communicate(timeout=30)

# The block stops and reaps the session however it ends.
with pd.start("my-container", ["long-running-task"]) as process:
    ...

# Use the image's Entrypoint and replace its Cmd.
server = pd.run("my-container", ["--port", "8080"])
server.terminate()
server.wait(timeout=10)
```

Without `stdin`, `stdout` or `stderr`, the session uses your own streams.
Other values follow `subprocess.Popen`: `subprocess.PIPE`,
`subprocess.DEVNULL`, open files and file descriptors.

**Options** (both methods): `user`, `environment`, `binds`, `work_dir`,
`redirect_ports`, `shared_home`, `shared_tmp`, `shared_x11`, `isolated`,
`minimal`, `emulator`, `kernel`, `hostname`, and the Termux switches
`no_link2symlink`, `no_sysvipc`, `no_kill_on_exit`. `start` wraps its
arguments in a shell like `proot-distro login`. `run` follows the image's
Entrypoint and Cmd rules.

**Startup limits.** `startup_timeout` is a number of seconds, or `None` for
no limit. It and `cancel_event` apply only while the session is being set up.
A timeout raises `SessionStartupTimeoutError`. Setup warnings reach your
event handler before the method returns.

**`SessionProcess`** has `pid`, `returncode`, `stdin`, `stdout`, `stderr`,
`container`, `kind`, `poll()`, `wait(timeout=)`, `communicate(input=,
timeout=)`, `terminate()`, `kill()` and `close(timeout=10)`.

* `terminate()` and `kill()` signal the whole session tree, not just the
  `proot` parent. They do nothing to a session that has already exited.
* Leaving a `with` block (or calling `close()`) terminates the tree, waits
  up to ten seconds, kills it if needed, reaps it and closes pipes.
  `close()` always reaps the session and never replaces an exception that
  is leaving the block. Ctrl-C while it waits kills the session at once.
* Ctrl-C while `start` or `run` is still setting up stops the setup. It
  is killed if it has not stopped after five seconds.

**Signalling by PID.** `kill_session(PID_OR_CONTAINER, signal_name="TERM")`
signals one session, or every session of a container. `kill_all_sessions(
signal_name="TERM")` signals every session in the current storage. Both
return a `SessionSignalResult`.

* A target made only of digits is a PID, even when a container has that
  name.
* `signal_name` takes a name (`"TERM"`, `"SIGTERM"`), a number or a
  `signal.Signals` value. The result names it as `"SIGTERM"`.

## Detached sessions

A detached session keeps running after your program exits. Its input is
`/dev/null`. Its output goes to `/dev/null` or to append-only host paths.

```python
session = pd.run_detached(
    "my-container",
    ["--port", "8080"],
    stdout="./server.log",
    stderr=subprocess.STDOUT,
)
pd.kill_session(session.pid)
pd.kill_all_sessions(signal_name="KILL")
```

| Method | Action |
|---|---|
| `start_detached(CONTAINER, ...)` | Detached login session. Returns `DetachedSession`. |
| `run_detached(CONTAINER, ...)` | Detached image entrypoint. Returns `DetachedSession`. |

They return after `proot` has started. `subprocess.DEVNULL` and `None` select
`/dev/null`. `subprocess.STDOUT` is accepted for `stderr`. Pipes and
in-memory streams are not accepted, because nothing is left to read them.
A log path that cannot be opened (a folder, a missing parent folder, a FIFO
nobody reads) raises `InvalidArgumentError` and nothing is started.

## Inspecting a session command

`inspect_start` and `inspect_run` prepare a session without running it:

```python
command = pd.inspect_run("my-container", ["--port", "8080"])
print(command.executable)
print(command.argv)
print(command.environment_dict)
print(command.inner_command)
print(command.shell_command)
```

They accept the same options as `start` and `run`, except the stream options.
The result is a `SessionCommand`.

The command shows `--rootfs=<path>`, like `--get-proot-cmd`. Real sessions
pin the container directory instead, which a copied command line cannot
reproduce. Do not run `shell_command` yourself where that matters.
Inspection does the same preparation as a real start, such as migrating an
old layout and updating profile files.

## Events

Pass an `EventHandler` to receive `OperationEvent` records while an operation
runs:

```python
from proot_distro.api import EventKind, ProotDistro

def show(event):
    if event.kind in (EventKind.INFO, EventKind.PROGRESS):
        print(event.message)

pd = ProotDistro(event_handler=show)
```

* Kinds: `INFO`, `WARNING`, `PROGRESS`, `RETRY`.
* Progress events also give `current`, `total` and `unit` when known.
* `operation` names the API method. `resource` names the main object, such
  as a container, image, layer or path. URLs never include user names,
  queries or fragments.
* `phase` is a machine-readable stage. Known values: `authentication`,
  `archive`, `build`, `build-install`, `build-output`, `clear-cache`, `copy`,
  `download`, `extract`, `install`, `layer-download`, `layer-pack`, `move`,
  `network`, `push`, `remove`, `remove-image`, `rename`, `reset`,
  `session-setup`, `signal-session`, `sync`, `upload`, `verify`. New phases
  may appear in later releases, so accept unknown values.
* Messages are for people. Use exceptions and result records for logic.
* Very long messages are shortened and end with `... [truncated]`.
* Build `RUN` output arrives as `INFO` events with `phase="build-output"`.
  Retried requests use `RETRY` with `phase="network"`.

The handler runs in the thread that called the method, and the operation
waits for it. Keep it quick.

If the handler raises, the operation is stopped and the exception reaches
you. The work may already be partly done. Catch errors inside the handler if
you cannot accept that. `KeyboardInterrupt` is handled gracefully: cleanup
finishes, then the interrupt is raised again.

## Cancelling operations

Methods that start work accept `cancel_event`: a `threading.Event`, or any
object with an `is_set()` method that returns a `bool` (`CancellationToken`).
Every method except `search` accepts it.

```python
import threading
from proot_distro.api import OperationCancelledError

cancel = threading.Event()

# Another thread calls cancel.set().
try:
    pd.build("./context", tags=["example:1"], cancel_event=cancel)
except OperationCancelledError:
    print("Build cancelled")
```

* A token that is already set stops the call before any work and before
  any credential lookup.
* The token is checked about ten times a second.
* The worker is asked to stop and clean up. If it has not finished after ten
  seconds it is killed.
* If the operation finished before the cancel was noticed, you get its normal
  result instead of `OperationCancelledError`.
* Ctrl-C (`KeyboardInterrupt`) behaves the same way: the worker is asked to
  stop and clean up, then the interrupt is raised again in your thread. Press
  Ctrl-C a second time to kill the worker at once. A killed worker cannot
  clean up, so check the state before retrying. A build `RUN` step that was
  running is still stopped: its processes end with the worker, and the next
  build, or `clear_cache()`, removes the scratch files the killed one left.
* `install`, and a `restore` that created its container, remove that
  container when they fail or are cancelled. This holds when the cancel
  arrives during the removal itself, or while the failure is reported to
  your event handler. A worker killed before the removal ends leaves the
  container behind. If the container could not be removed, an event names
  it, and `remove_container()` removes it.
* A running callback, credential provider or stream method must return
  before the token can be checked again.

Cancelling is not a rollback. Files may be copied, uploads sent or a detached
session started. Output streams may hold partial data. Check the state
before retrying a destructive operation.

## Registry credentials

The API does not read `PD_DOCKER_AUTH`. Pass credentials explicitly, and
name the registry they belong to:

```python
from proot_distro.api import ProotDistro, RegistryCredentials

pd = ProotDistro(credentials=RegistryCredentials(
    "myuser", "my-token", registry="ghcr.io",
))
pd.install("ghcr.io/myuser/private-image:1", name="private-image")
pd.install("alpine:3.21")  # Docker Hub gets no credentials
```

Credentials go only to the registry they are for:

* With `registry=`, only to that registry. Use `docker.io` for Docker Hub.
  A port is part of the name (`localhost:5000`).
* Without `registry=`, only to the registry that an `install` or `push`
  argument names.
* `build` and `reset_container` refuse credentials without `registry=`.
  A Dockerfile chooses which registries a build contacts, and a container
  can change the image its own metadata records.

The password is hidden from `repr()`, never placed in command arguments or
the environment, and sent to the worker only when it authenticates with
that registry.

For several registries, give a provider function:

```python
def credentials_for(request):
    if request.registry == "ghcr.io":
        return RegistryCredentials("myuser", "my-token")
    return None

pd = ProotDistro(credential_provider=credentials_for)
```

The provider (`CredentialProvider`) receives a `CredentialRequest` with
`registry`, `repository` and `operation`, and returns `RegistryCredentials`
or `None`. `registry` is lowercase, and Docker Hub is always `docker.io`.
The provider is asked when the operation is about to authenticate, in your
calling thread. Credentials it returns with a different `registry=` are not
used. Use either `credentials` or `credential_provider`, not both.
`build(credentials=...)` replaces both for one build. A refusal raises
`AuthenticationError` without the secret.

Registries answer a missing repository the same way as a private one, so a
mistyped image name is reported as `AuthenticationError`.

## Separate storage

By default the API shares storage with the command line. Give your
application its own with `RuntimeConfig`:

```python
from proot_distro.api import ProotDistro, RuntimeConfig

config = RuntimeConfig(
    runtime_dir="./app-state",
    cache_dir="./app-cache",
    proot_path="/path/to/proot",  # optional
)
pd = ProotDistro(config)
```

Paths may be strings or `pathlib.Path` objects. They are stored as absolute
strings (read them back as `config.runtime_dir`). The two folders must
differ, neither may be a filesystem root, and the runtime folder may not be
inside the cache folder. Each `ProotDistro` keeps its own configuration, so
one Python process can use several independent setups.

## Errors

| Exception | Meaning |
|---|---|
| `ProotDistroError` | Base class of every failure below. |
| `InvalidArgumentError` | A method argument is malformed. |
| `NotFoundError` | A container, image, source or archive is missing. |
| `AlreadyExistsError` | The operation refused to overwrite something. |
| `BusyError` | A container or image is locked by another operation or session. |
| `AuthenticationError` | The registry refused the credentials or permissions. |
| `NetworkError` | Talking to a remote service failed. |
| `IntegrityError` | Content failed a digest or structure check. |
| `UnsupportedOperationError` | Not possible for this resource, such as resetting a plain rootfs archive. |
| `PartialOperationError` | Some entries were processed but not all. |
| `OperationCancelledError` | A cancellation token stopped the operation. |
| `SessionStartupTimeoutError` | Session setup exceeded `startup_timeout`. |
| `OperationError` | Any other failure. |

Every exception has an `operation` attribute naming the API method.
`OperationCancelledError` and `SessionStartupTimeoutError` are subclasses of
`OperationError`. Choose handlers by class, never by message text.

### Errors by method

Beyond the common errors, these methods can raise these specific ones.
Every method can also raise `InvalidArgumentError`,
`OperationCancelledError` (when given a `cancel_event`) and
`OperationError`. `BusyError` is not rare: it means another program (or
another call) holds a lock on that container or image.

| Method | Specific errors |
|---|---|
| `backup` | AlreadyExistsError, BusyError, NotFoundError, UnsupportedOperationError |
| `build` | AlreadyExistsError, AuthenticationError, BusyError, NetworkError, NotFoundError |
| `clear_cache` | BusyError |
| `copy` | BusyError, NotFoundError, PartialOperationError |
| `inspect_run` | BusyError, NotFoundError |
| `inspect_start` | BusyError, NotFoundError |
| `install` | AlreadyExistsError, AuthenticationError, BusyError, IntegrityError, NetworkError, NotFoundError |
| `move` | BusyError, NotFoundError, PartialOperationError |
| `push` | AuthenticationError, BusyError, IntegrityError, NetworkError, NotFoundError |
| `remove_container` | BusyError, NotFoundError |
| `remove_image` | BusyError, NotFoundError |
| `rename_container` | AlreadyExistsError, BusyError, NotFoundError |
| `reset_container` | AuthenticationError, BusyError, IntegrityError, NetworkError, NotFoundError, UnsupportedOperationError |
| `restore` | BusyError, IntegrityError, NotFoundError, UnsupportedOperationError |
| `run` | BusyError, NotFoundError |
| `run_detached` | BusyError, NotFoundError |
| `search` | NetworkError |
| `start` | BusyError, NotFoundError |
| `start_detached` | BusyError, NotFoundError |
| `sync` | BusyError, NotFoundError, PartialOperationError |

This table is checked against real workers by the test suite. Methods not
listed (`list_containers`, `list_images`, `list_sessions`, `kill_session`,
`kill_all_sessions`) raise only the common errors.

## Result types

| Type | Fields |
|---|---|
| `ContainerInfo` | `name`, `image_reference`, `architecture`, `image_id` |
| `ImageInfo` | `identifier`, `reference`, `repository`, `architecture`, `image_id`, `size_bytes`, `layer_count`, `missing_layers`, `created`, `cached_at` |
| `SessionInfo` | `pid`, `container`, `kind`, `command`, `user`, `start_time`, `isolated`, `minimal`, `detached` |
| `SessionCommand` | `kind`, `container`, `executable`, `argv`, `environment`, `inner_command`, `rootfs`, `work_dir`, `user`, `shell_command` |
| `DetachedSession` | `pid`, `container`, `kind` |
| `SessionSignalResult` | `target`, `signal`, `pids` |
| `TransferResult` | `operation`, `source`, `destination` |
| `BackupResult` | `container`, `output`, `compression`, `size_bytes` |
| `RestoreResult` | `container`, `archive` |
| `BuildResult` | `images`, `outputs`, `installed_container` |
| `PushResult` | `registry`, `repository`, `tag`, `manifest_digest`, `bytes_uploaded` |
| `ImageRemovalResult` | `removed` |
| `CacheCleanupResult` | `mode`, `bytes_before`, `bytes_after`, `bytes_reclaimed` |
| `SearchResult` | `query`, `total`, `hits` |
| `SearchHit` | `name`, `description`, `stars`, `pulls`, `official`, `automated` |
| `OperationEvent` | `operation`, `kind`, `message`, `current`, `total`, `unit`, `phase`, `resource` |
| `RegistryCredentials` | `username`, `password` |
| `CredentialRequest` | `registry`, `repository`, `operation` |
| `RuntimeConfig` | `runtime_dir`, `cache_dir`, `proot_path` |

Records are frozen. Fields holding several values are tuples.
