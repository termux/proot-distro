# What an API worker imports before it does any work. Every public call
# starts one, so its import graph is the floor under every call's
# latency: a list_containers worker used to import the whole public API
# facade, the registry client, the TLS stack, the build engine and
# importlib.metadata -- about three times what listing needs.
#
# Pinned as module sets rather than timings, which a loaded test host
# would make flaky.

import importlib
import subprocess
import sys

import pytest

from proot_distro._bootstrap import private_module_command

import proot_distro.helpers.docker as docker_index
import proot_distro.operations as operations_index


# None of these is needed to read local state. Each was loaded by every
# worker before (the facade by the package the worker lived in, the rest
# through eager package indexes and the version lookup).
_NOT_FOR_LOCAL_WORK = (
    "proot_distro.api",
    "importlib.metadata",
    "http.client",
    "ssl",
    "proot_distro.helpers.docker.transport",
    "proot_distro.helpers.docker.pull",
    "proot_distro.helpers.build_engine",
    "proot_distro.operations.build",
    "proot_distro.operations.install",
)

# Runs a private module exactly as the bootstrap does, then reports what
# the interpreter ended up importing.
_REPORTING_BOOTSTRAP = (
    "import runpy,sys\n"
    "sys.path.append(sys.argv[1])\n"
    "code=0\n"
    "try:\n"
    "    runpy.run_module(sys.argv[2],run_name='__main__')\n"
    "except SystemExit as exc:\n"
    "    code=exc.code\n"
    "open(sys.argv[3],'w').write('\\n'.join(sorted(sys.modules)))\n"
    "sys.exit(code)\n"
)


def _worker_modules(tmp_path, module, request):
    command = private_module_command(module)
    parent = command[-2]
    report = tmp_path / "modules.txt"
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", _REPORTING_BOOTSTRAP,
         parent, module, str(report)],
        input=request, capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return set(report.read_text().split("\n"))


def _fresh_modules(statement):
    command = private_module_command("proot_distro._api_worker")
    script = (
        f"import sys\nsys.path.append({command[-2]!r})\n{statement}\n"
        "print('\\n'.join(sorted(sys.modules)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return set(result.stdout.split("\n"))


@pytest.mark.parametrize("operation", [
    "list_containers", "list_images", "list_sessions",
])
def test_a_local_worker_loads_neither_the_facade_nor_the_network(
        tmp_path, operation):
    loaded = _worker_modules(
        tmp_path, "proot_distro._api_worker",
        f'{{"operation":"{operation}","options":{{}}}}'.encode(),
    )
    assert "proot_distro._api_worker" not in loaded  # ran as __main__
    assert "proot_distro.operations.inventory" in loaded
    assert loaded.isdisjoint(_NOT_FOR_LOCAL_WORK), (
        sorted(loaded & set(_NOT_FOR_LOCAL_WORK))
    )


def test_the_session_worker_loads_neither_the_facade_nor_the_network():
    loaded = _fresh_modules(
        "import proot_distro._api_session_worker\n"
        "import proot_distro.operations.session_prepare\n"
        "import proot_distro.session_runtime"
    )
    assert loaded.isdisjoint(_NOT_FOR_LOCAL_WORK), (
        sorted(loaded & set(_NOT_FOR_LOCAL_WORK))
    )


def test_one_service_does_not_import_its_siblings():
    loaded = _fresh_modules("import proot_distro.operations.copy")
    assert "proot_distro.operations.copy" in loaded
    assert loaded.isdisjoint((
        "proot_distro.operations.sync",
        "proot_distro.operations.backup",
        "proot_distro.operations.push",
        "proot_distro.helpers.docker.transport",
    ))


def test_the_reference_parser_does_not_import_the_registry_client():
    # The public credentials type normalises registry names with it, so
    # every application importing the API used to load the client too.
    loaded = _fresh_modules("import proot_distro.helpers.docker.refs")
    assert loaded.isdisjoint((
        "proot_distro.helpers.docker.cache",
        "proot_distro.helpers.docker.transport",
        "proot_distro.helpers.docker.pull",
        "http.client",
    ))


def test_the_version_is_looked_up_only_when_asked_for():
    loaded = _fresh_modules("import proot_distro.constants")
    assert "importlib.metadata" not in loaded

    from proot_distro.constants import program_version

    version = program_version()
    assert isinstance(version, str) and version
    assert program_version() is version


@pytest.mark.parametrize("index", [operations_index, docker_index],
                         ids=["operations", "helpers.docker"])
def test_a_lazy_index_still_exports_every_name(index):
    exports = index._EXPORTS
    assert tuple(index.__all__) == tuple(exports)
    for name, submodule in exports.items():
        module = importlib.import_module(f"{index.__name__}.{submodule}")
        assert getattr(index, name) is getattr(module, name)
        # Cached on the package: the hook is not consulted again.
        assert name in vars(index)
    assert set(index.__all__) <= set(dir(index))


@pytest.mark.parametrize("index", [operations_index, docker_index],
                         ids=["operations", "helpers.docker"])
def test_a_lazy_index_refuses_unknown_names(index):
    with pytest.raises(AttributeError, match="no_such_name"):
        index.no_such_name  # noqa: B018
    with pytest.raises(ImportError):
        exec(f"from {index.__name__} import no_such_name", {})


def test_a_lazy_index_still_imports_submodules_by_name():
    from proot_distro.operations import inventory
    from proot_distro.helpers.docker import refs

    assert inventory.__name__ == "proot_distro.operations.inventory"
    assert refs.__name__ == "proot_distro.helpers.docker.refs"


def test_the_auth_error_is_one_class_wherever_it_is_named():
    from proot_distro.helpers.docker import transport
    from proot_distro.operation_errors import RegistryAuthenticationError
    from proot_distro.operations.errors import BuildAuthenticationFailure

    assert transport.RegistryAuthenticationError is RegistryAuthenticationError
    assert issubclass(BuildAuthenticationFailure, RegistryAuthenticationError)
