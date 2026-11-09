# Python API samples

These scripts show how applications can use the stable `proot_distro.api`
interface. Run them from a PRoot-Distro source checkout, or after installing
the `proot-distro` Python package.

The examples progress from read-only calls to state-changing workflows:

| Script | Demonstrates |
|---|---|
| `list_resources.py` | Container, image, and session inventories; optional Docker Hub search; immutable result records; typed errors. |
| `install_and_run.py` | Image installation, command inspection, foreground sessions, captured output, timeouts, and optional cleanup. |
| `stream_archives.py` | Caller-owned binary streams, streamed backup and restore, checksums, and partial-output cleanup. |
| `build_image.py` | Builds, structured events, cancellation, build arguments, optional isolated storage, credential providers, and optional pushes. |

Every script has built-in help and a detailed module docstring:

```sh
python samples/list_resources.py --help
python samples/install_and_run.py --help
python samples/stream_archives.py --help
python samples/build_image.py --help
```

`list_resources.py` is read-only unless `--search` is used, which accesses
Docker Hub. The other scripts modify container or image state. Read their help
before running them against important data.

Registry secrets used by `build_image.py` are read from environment variables,
not command-line arguments. This keeps them out of process listings and shell
history. See that script's docstring for the variable names.
