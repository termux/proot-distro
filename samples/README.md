# Python API samples

These scripts demonstrate the stable `proot_distro.api` interface. They use
only Python's standard library and the `proot-distro` package.

Run the examples from the project root in a source checkout. You can also copy
them into another project after installing `proot-distro`. Python 3.9 or newer
is required. Starting a container also requires a working `proot` executable.

## Start here

The first three scripts intentionally do one thing each. Their comments explain
the API objects and error handling as they appear.

1. List local containers. This operation is read-only:

   ```sh
   python samples/list_containers.py
   ```

2. Install a small image as a new container. This may download image data:

   ```sh
   python samples/install_container.py alpine:latest beginner-alpine
   ```

3. Run a command in that container. Its input and output use your terminal:

   ```sh
   python samples/run_command.py beginner-alpine uname -a
   ```

The API uses the same containers and cache as the `proot-distro` command-line
utility unless a script supplies a `RuntimeConfig`. The installed container is
therefore visible to both interfaces. Remove the tutorial container when you
no longer need it:

```sh
proot-distro remove beginner-alpine
```

## Beginner examples

| Script | Demonstrates |
|---|---|
| `list_containers.py` | Creating `ProotDistro`, reading `ContainerInfo` fields, empty results, and basic error handling. |
| `install_container.py` | Installing one public OCI image and using the returned container metadata. |
| `run_command.py` | Starting one foreground command, inheriting terminal streams, using `SessionProcess` as a context manager, and checking its exit status. |

## More complete examples

These scripts combine several API features. Read their module docstrings and
inline comments before adapting them for an application.

| Script | Demonstrates |
|---|---|
| `list_resources.py` | Container, image, and session inventories; optional Docker Hub search; immutable result records; typed errors. |
| `install_and_run.py` | Image installation, command inspection, foreground sessions, captured output, timeouts, and optional cleanup. |
| `stream_archives.py` | Caller-owned binary streams, streamed backup and restore, checksums, and partial-output cleanup. |
| `build_image.py` | Builds, structured events, cancellation, build arguments, isolated storage, credential providers, and optional pushes. |
| `container_manager_tui.py` | A complete interactive terminal interface for listing, installing, removing, and opening shells in containers. |

The TUI sample uses Python's standard-library `curses` module and has no extra
package dependencies. Start it in a Linux or Termux terminal:

```sh
python samples/container_manager_tui.py
```

Press `?` inside the interface to see its controls. Removal always requires
confirmation. The interface restores normal terminal settings while a guest
shell is running and redraws itself when that shell exits.

Every script that accepts arguments has built-in help. For example:

```sh
python samples/install_container.py --help
python samples/run_command.py --help
python samples/list_resources.py --help
python samples/install_and_run.py --help
python samples/stream_archives.py --help
python samples/build_image.py --help
```

## Safety and errors

`list_containers.py` is fully read-only. `list_resources.py` reads local state,
but its optional `--search` request accesses Docker Hub. The remaining scripts
can create or change containers, cached images, or archive files.

All documented API failures inherit from `ProotDistroError`. The examples catch
that base class at their application boundary so users receive a short message
instead of a traceback. Larger applications can catch specific subclasses such
as `NotFoundError`, `AlreadyExistsError`, or `NetworkError` when recovery
depends on the failure type.

Registry secrets used by `build_image.py` are read from environment variables,
not command-line arguments. This keeps them out of process listings and shell
history. See that script's module docstring for the variable names.
