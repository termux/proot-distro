# proot-distro test suite

End-to-end and unit tests for `proot-distro`. The suite is **offline and
deterministic** by default: no network, no real `proot` execution, and no
access to a real proot-distro installation.

## Running

```sh
# from the repo root
pip install -e '.[test]'      # or just have pytest available
python -m pytest -q           # full offline suite
python -m pytest tests/security -q   # just the containment tests
RUN_LIVE_TESTS=1 python -m pytest -q tests/live   # opt-in network/proot tests
```

## Layout

- `unit/` — pure-function correctness (names, paths, arch/ELF detection,
  Dockerfile parsing + `${VAR}` expansion, digest validation, cache keys,
  layer diff/whiteouts, OCI writer, quoting, env injection, argparse, ...).
- `security/` — containment of malformed/malicious input. Proves a hostile
  third-party tar / OCI image / backup archive cannot escape the destination
  rootfs or the containers directory (path traversal via member names,
  hard-link / symlink linknames, crafted `index.json` digests, COPY/ADD
  sources, `--bind` specs).
- `integration/` — the real `command_*` pipelines driven with
  `types.SimpleNamespace` args, against the sandboxed runtime tree
  (install / backup→restore / build→install / copy / sync / rename / reset /
  clear-cache / CLI dispatch / `login --get-proot-cmd`).
- `live/` — opt-in smoke tests behind `@pytest.mark.live`, skipped unless
  `RUN_LIVE_TESTS=1` (real Docker Hub pull, real proot run).

## How isolation works

`proot_distro.constants` computes all runtime paths and `IS_TERMUX` **once at
import** and most modules bind those by value. `conftest.py` therefore points
`XDG_DATA_HOME` / `XDG_CACHE_HOME` / `HOME` at a throwaway sandbox and clears
the Termux/auth environment *before* importing any proot_distro module, then
wipes the runtime/cache trees between tests. A guard refuses to run if the
resolved paths are not inside the sandbox or if `IS_TERMUX` is True.

Several tests build trees deeper than the interpreter's recursion limit on
purpose, so two rules keep one from taking the rest of the run with it:

- Nothing in the harness removes a tree with `shutil.rmtree()`, which
  recurses and raises `RecursionError` — not an `OSError`, so
  `ignore_errors=True` does not catch it. The sandbox is reset with
  `dirfd.remove_tree()`, and a reset that leaves something behind fails the
  next test's setup, naming what is left, rather than letting it run on an
  earlier test's leftovers. A deep tree under `tmp_path` has to be removed
  by the test itself, since pytest's own reaper is `shutil.rmtree()`.
- A helper that builds or walks a deep tree holds one descriptor at a time.
  One per level runs out at the 1024 soft limit most systems default to,
  before the code under test has done anything.

Shared builders live in `_builders.py`: `make_tar` (arbitrary, incl. hostile,
members), `make_oci_archive`, `make_layer_blob` / `seed_cached_layer`,
`elf_bytes`, `make_rootfs`, `make_container`, `tree_snapshot`.

## Known coverage limits

- Real `login` / `run` `os.execvpe` into proot + a distro shell is not
  unit-testable; covered via `--get-proot-cmd` (asserts the generated proot
  argv) plus the opt-in live test.
- `run_step.do_run`'s actual proot invocation is exercised only by the live
  build test; its separable helpers are covered directly.
- Termux-only (`IS_TERMUX=True`) branches are covered by calling pure
  functions directly and by targeted per-module `monkeypatch` of the module's
  `IS_TERMUX` attribute, since the test host is non-Termux.
