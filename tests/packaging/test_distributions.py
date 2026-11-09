import configparser
from email.parser import Parser
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import zipfile

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPATIBILITY_TEST = PROJECT_ROOT / "tests/compatibility/test_public_api.py"


def _copy_source(destination):
    def ignore(_directory, names):
        ignored = {
            ".git",
            ".pytest_cache",
            "API_TASKS_PLAN.md",
            "build",
            "dist",
        }
        ignored.update(
            name for name in names
            if name == "__pycache__" or name.endswith(".egg-info")
        )
        return ignored

    shutil.copytree(PROJECT_ROOT, destination, ignore=ignore)


def _run(command, *, cwd, env=None):
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture(scope="module")
def distributions(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("distributions")
    source = workspace / "source"
    output = workspace / "dist"
    _copy_source(source)
    output.mkdir()

    environment = os.environ.copy()
    environment["PIP_CONFIG_FILE"] = os.devnull
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    _run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--skip-dependency-check",
            "--outdir",
            str(output),
            str(source),
        ],
        cwd=workspace,
        env=environment,
    )

    wheels = tuple(output.glob("*.whl"))
    source_distributions = tuple(output.glob("*.tar.gz"))
    assert len(wheels) == 1
    assert len(source_distributions) == 1
    return {
        "wheel": wheels[0],
        "sdist": source_distributions[0],
        "workspace": workspace,
    }


def _expected_package_files():
    package = PROJECT_ROOT / "proot_distro"
    expected = {
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in package.rglob("*.py")
    }
    expected.add("proot_distro/py.typed")
    expected.update(
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (package / "completions").iterdir()
        if path.is_file()
    )
    return expected


def test_wheel_contains_typed_package_and_entry_points(distributions):
    with zipfile.ZipFile(distributions["wheel"]) as archive:
        names = set(archive.namelist())
        assert _expected_package_files() <= names
        metadata_name = next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        entry_points_name = next(
            name for name in names
            if name.endswith(".dist-info/entry_points.txt")
        )
        metadata = Parser().parsestr(
            archive.read(metadata_name).decode("utf-8")
        )
        entry_points = configparser.ConfigParser()
        entry_points.read_string(
            archive.read(entry_points_name).decode("utf-8")
        )

    requirements = metadata.get_all("Requires-Dist", [])
    assert requirements
    assert all(
        ";" in requirement
        and "extra" in requirement.split(";", 1)[1]
        and "test" in requirement.split(";", 1)[1]
        for requirement in requirements
    )
    assert dict(entry_points["console_scripts"]) == {
        "pd": "proot_distro.cli:main",
        "proot-distro": "proot_distro.cli:main",
    }


def test_source_distribution_contains_typed_package(distributions):
    with tarfile.open(distributions["sdist"], mode="r:gz") as archive:
        names = set(archive.getnames())

    for expected in _expected_package_files():
        assert any(name.endswith("/" + expected) for name in names)
    assert any(name.endswith("/pyproject.toml") for name in names)
    assert any(name.endswith("/README.md") for name in names)


def _installation_paths(prefix):
    variables = {"base": str(prefix), "platbase": str(prefix)}
    return (
        Path(sysconfig.get_path("purelib", vars=variables)),
        Path(sysconfig.get_path("scripts", vars=variables)),
    )


def _install(artifact, prefix, workspace):
    environment = os.environ.copy()
    environment["PIP_CONFIG_FILE"] = os.devnull
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--ignore-installed",
            "--no-build-isolation",
            "--no-compile",
            "--no-deps",
            "--no-index",
            "--no-warn-script-location",
            "--prefix",
            str(prefix),
            str(artifact),
        ],
        cwd=workspace,
        env=environment,
    )
    return _installation_paths(prefix)


def _assert_installed_contract(purelib, workspace):
    code = f"""
import importlib.metadata
import importlib.resources
import json
import os
from pathlib import Path
import runpy
import sys

purelib = Path({str(purelib)!r}).resolve()
sys.path.insert(0, str(purelib))
import proot_distro.api as api

origin = Path(api.__file__).resolve()
assert os.path.commonpath((str(origin), str(purelib))) == str(purelib)
assert (importlib.resources.files("proot_distro") / "py.typed").is_file()
distribution = importlib.metadata.distribution("proot-distro")
requirements = distribution.requires or []
assert all(
    ";" in requirement
    and "extra" in requirement.split(";", 1)[1]
    and "test" in requirement.split(";", 1)[1]
    for requirement in requirements
)
scripts = {{
    (entry.name, entry.value)
    for entry in distribution.entry_points
    if entry.group == "console_scripts"
}}
assert scripts == {{
    ("pd", "proot_distro.cli:main"),
    ("proot-distro", "proot_distro.cli:main"),
}}
module = runpy.run_path({str(COMPATIBILITY_TEST)!r})
with open(module["_SNAPSHOT"], encoding="utf-8") as stream:
    expected = json.load(stream)
assert module["_public_api_snapshot"]() == expected
print("installed contract matches")
"""
    _run(
        [sys.executable, "-S", "-c", code],
        cwd=workspace,
    )


def _assert_installed_scripts(scripts, purelib, workspace):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(purelib)
    environment["PD_FORCE_NO_COLORS"] = "1"
    for name in ("proot-distro", "pd"):
        result = _run(
            [str(scripts / name), "search", "--help"],
            cwd=workspace,
            env=environment,
        )
        output = result.stdout + result.stderr
        assert "USAGE" in output
        assert "proot-distro search" in output


@pytest.mark.parametrize("artifact_name", ["wheel", "sdist"])
def test_installed_artifact_exposes_typed_contract_and_scripts(
    distributions, tmp_path, artifact_name,
):
    prefix = tmp_path / "prefix"
    workspace = tmp_path / "work"
    workspace.mkdir()
    purelib, scripts = _install(
        distributions[artifact_name], prefix, workspace
    )

    _assert_installed_contract(purelib, workspace)
    _assert_installed_scripts(scripts, purelib, workspace)
