import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from proot_distro.api import ProotDistro, RuntimeConfig


def _rootfs_archive(tmp_path, builders, name, marker):
    archive = tmp_path / name
    builders.make_tar(str(archive), builders.rootfs_members([
        {
            "name": "etc/api-concurrency",
            "type": "file",
            "data": marker.encode(),
        },
    ]))
    return archive


def _installed_marker(config, container):
    path = os.path.join(
        config.runtime_dir,
        "containers",
        container,
        "rootfs",
        "etc",
        "api-concurrency",
    )
    with open(path) as stream:
        return stream.read()


def test_instances_sharing_runtime_install_in_parallel(
        tmp_path, builders):
    archive = _rootfs_archive(
        tmp_path, builders, "shared-runtime.tar", "shared"
    )
    config = RuntimeConfig(
        tmp_path / "shared-state", tmp_path / "shared-cache"
    )
    first = ProotDistro(config)
    second = ProotDistro(config)
    barrier = threading.Barrier(2)

    def install(pd, name):
        barrier.wait(timeout=10)
        return pd.install(archive, name=name)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(install, first, "parallel-first"),
            executor.submit(install, second, "parallel-second"),
        )
        results = tuple(future.result(timeout=30) for future in futures)

    assert {result.name for result in results} == {
        "parallel-first", "parallel-second",
    }
    assert {item.name for item in first.list_containers()} == {
        "parallel-first", "parallel-second",
    }
    assert _installed_marker(config, "parallel-first") == "shared"
    assert _installed_marker(config, "parallel-second") == "shared"


def test_independent_runtime_roots_install_same_name_in_parallel(
        tmp_path, builders):
    first_archive = _rootfs_archive(
        tmp_path, builders, "first-root.tar", "first"
    )
    second_archive = _rootfs_archive(
        tmp_path, builders, "second-root.tar", "second"
    )
    first_config = RuntimeConfig(
        tmp_path / "first-state", tmp_path / "first-cache"
    )
    second_config = RuntimeConfig(
        tmp_path / "second-state", tmp_path / "second-cache"
    )
    first_events = []
    second_events = []
    first = ProotDistro(first_config, event_handler=first_events.append)
    second = ProotDistro(second_config, event_handler=second_events.append)
    barrier = threading.Barrier(2)

    def install(pd, archive):
        barrier.wait(timeout=10)
        return pd.install(archive, name="same-name")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(install, first, first_archive),
            executor.submit(install, second, second_archive),
        )
        tuple(future.result(timeout=30) for future in futures)

    assert [item.name for item in first.list_containers()] == ["same-name"]
    assert [item.name for item in second.list_containers()] == ["same-name"]
    assert _installed_marker(first_config, "same-name") == "first"
    assert _installed_marker(second_config, "same-name") == "second"
    assert first_events and second_events
    assert all(event.operation == "install" for event in first_events)
    assert all(event.operation == "install" for event in second_events)


_PROCESS_INSTALL = r"""
import json
import os
import sys
import time

from proot_distro.api import ProotDistro, ProotDistroError, RuntimeConfig

runtime, cache, archive, gate = sys.argv[1:]
deadline = time.monotonic() + 20
while not os.path.exists(gate):
    if time.monotonic() >= deadline:
        print(json.dumps({"status": "gate-timeout"}))
        raise SystemExit(2)
    time.sleep(0.01)

pd = ProotDistro(RuntimeConfig(runtime, cache))
try:
    result = pd.install(archive, name="process-race")
except ProotDistroError as exc:
    print(json.dumps({
        "status": "error",
        "type": type(exc).__name__,
        "operation": exc.operation,
    }))
else:
    print(json.dumps({"status": "ok", "name": result.name}))
"""


def test_processes_sharing_runtime_serialize_conflicting_install(
        tmp_path, builders):
    archive = _rootfs_archive(
        tmp_path, builders, "process-race.tar", "process"
    )
    config = RuntimeConfig(
        tmp_path / "process-state", tmp_path / "process-cache"
    )
    gate = tmp_path / "process-start"
    command = [
        sys.executable,
        "-c",
        _PROCESS_INSTALL,
        config.runtime_dir,
        config.cache_dir,
        str(archive),
        str(gate),
    ]
    processes = [
        subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _index in range(2)
    ]
    try:
        gate.touch()
        completed = [process.communicate(timeout=30) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert [process.returncode for process in processes] == [0, 0]
    assert all(stderr == "" for _stdout, stderr in completed)
    records = [json.loads(stdout) for stdout, _stderr in completed]

    assert sum(record["status"] == "ok" for record in records) == 1
    refused = next(record for record in records if record["status"] == "error")
    assert refused["type"] in ("AlreadyExistsError", "BusyError")
    assert refused["operation"] == "install"
    assert [
        item.name for item in ProotDistro(config).list_containers()
    ] == ["process-race"]
    assert _installed_marker(config, "process-race") == "process"
