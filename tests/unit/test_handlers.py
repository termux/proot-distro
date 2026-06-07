# Tests for proot_distro.helpers.build_engine.handlers — the metadata-only
# Dockerfile instruction handlers and the needs_proot gate.

import os
from types import SimpleNamespace

import pytest

from proot_distro.helpers.build_engine import handlers
from proot_distro.helpers.build_engine.constants import needs_proot
from proot_distro.helpers.build_engine.errors import BuildError
from proot_distro.helpers.build_engine.stage import Stage


@pytest.fixture
def engine(tmp_path):
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    tmp_root = tmp_path / "tmp"
    tmp_root.mkdir()
    stage = Stage(index=0, name=None, rootfs_dir=str(rootfs),
                  target_arch_pd="x86_64")
    return SimpleNamespace(
        current=stage,
        tmp_root=str(tmp_root),
        user_build_args={},
        global_args={},
        declared_global=set(),
    )


def _instr(name, value, *, exec_form=False, flags=None, lineno=1):
    return {"name": name, "value": value, "exec_form": exec_form,
            "flags": flags or {}, "heredocs": [], "lineno": lineno}


def _cfg(engine):
    return engine.current.image_config["config"]


def test_do_env(engine):
    handlers.do_env(engine, _instr("ENV", "A=1 B=2"))
    assert "A=1" in _cfg(engine)["Env"]
    assert "B=2" in _cfg(engine)["Env"]
    assert engine.current.env["A"] == "1"


def test_do_label(engine):
    handlers.do_label(engine, _instr("LABEL", "k=v owner=me"))
    assert _cfg(engine)["Labels"] == {"k": "v", "owner": "me"}


def test_do_entrypoint_resets_cmd(engine):
    _cfg(engine)["Cmd"] = ["old"]
    handlers.do_entrypoint(engine, _instr("ENTRYPOINT", ["/bin/app"],
                                          exec_form=True))
    assert _cfg(engine)["Entrypoint"] == ["/bin/app"]
    assert _cfg(engine)["Cmd"] is None


def test_do_cmd_shell_form(engine):
    handlers.do_cmd(engine, _instr("CMD", "echo hi"))
    assert _cfg(engine)["Cmd"] == ["/bin/sh", "-c", "echo hi"]


def test_do_expose(engine):
    handlers.do_expose(engine, _instr("EXPOSE", "80 443/udp"))
    assert _cfg(engine)["ExposedPorts"] == {"80/tcp": {}, "443/udp": {}}


def test_do_volume(engine):
    handlers.do_volume(engine, _instr("VOLUME", "/data /cache"))
    assert _cfg(engine)["Volumes"] == {"/data": {}, "/cache": {}}


def test_do_stopsignal(engine):
    handlers.do_stopsignal(engine, _instr("STOPSIGNAL", "SIGTERM"))
    assert _cfg(engine)["StopSignal"] == "SIGTERM"


def test_do_healthcheck_none(engine):
    handlers.do_healthcheck(engine, _instr("HEALTHCHECK", "NONE"))
    assert _cfg(engine)["Healthcheck"] == {"Test": ["NONE"]}


def test_do_healthcheck_cmd_shell(engine):
    handlers.do_healthcheck(engine, _instr("HEALTHCHECK", "CMD curl -f localhost"))
    assert _cfg(engine)["Healthcheck"]["Test"] == ["CMD-SHELL", "curl -f localhost"]


def test_do_healthcheck_cmd_exec(engine):
    handlers.do_healthcheck(
        engine, _instr("HEALTHCHECK", 'CMD ["curl", "-f", "localhost"]'))
    assert _cfg(engine)["Healthcheck"]["Test"] == ["CMD", "curl", "-f", "localhost"]


def test_do_healthcheck_invalid(engine):
    with pytest.raises(BuildError):
        handlers.do_healthcheck(engine, _instr("HEALTHCHECK", "bogus"))


def test_do_user(engine):
    handlers.do_user(engine, _instr("USER", "tester:staff"))
    assert engine.current.user == "tester:staff"
    assert _cfg(engine)["User"] == "tester:staff"


def test_do_shell_requires_exec_form(engine):
    with pytest.raises(BuildError):
        handlers.do_shell(engine, _instr("SHELL", "/bin/bash -c"))
    handlers.do_shell(engine, _instr("SHELL", ["/bin/bash", "-c"], exec_form=True))
    assert engine.current.shell == ["/bin/bash", "-c"]


def test_do_workdir_creates_dir_and_layer(engine):
    handlers.do_workdir(engine, _instr("WORKDIR", "/app/sub"))
    assert _cfg(engine)["WorkingDir"] == "/app/sub"
    assert os.path.isdir(os.path.join(engine.current.rootfs_dir, "app", "sub"))
    # A thin layer covering the new dirs was produced.
    assert len(engine.current.layers) == 1
    assert engine.current.layers[0]["digest"].startswith("sha256:")


def test_do_workdir_empty_raises(engine):
    with pytest.raises(BuildError):
        handlers.do_workdir(engine, _instr("WORKDIR", ""))


# ----- ARG resolution precedence ------------------------------------------

def test_do_arg_build_arg_wins(engine):
    engine.user_build_args = {"FOO": "cli"}
    handlers.do_arg(engine, _instr("ARG", "FOO=default"))
    assert engine.current.args["FOO"] == "cli"


def test_do_arg_default(engine):
    handlers.do_arg(engine, _instr("ARG", "FOO=default"))
    assert engine.current.args["FOO"] == "default"


def test_do_arg_reexposes_global(engine):
    engine.global_args = {"G": "gv"}
    engine.declared_global = {"G"}
    handlers.do_arg(engine, _instr("ARG", "G"))
    assert engine.current.args["G"] == "gv"


def test_do_arg_predefined_from_env(engine, monkeypatch):
    monkeypatch.setenv("TARGETARCH", "amd64")
    handlers.do_arg(engine, _instr("ARG", "TARGETARCH"))
    assert engine.current.args["TARGETARCH"] == "amd64"


def test_do_arg_unknown_empty(engine):
    handlers.do_arg(engine, _instr("ARG", "UNKNOWN"))
    assert engine.current.args["UNKNOWN"] == ""


def test_do_arg_invalid_raises(engine):
    with pytest.raises(BuildError):
        handlers.do_arg(engine, _instr("ARG", ""))


# ----- needs_proot --------------------------------------------------------

def test_needs_proot():
    assert needs_proot([{"name": "RUN", "value": "x"}]) is True
    assert needs_proot([{"name": "COPY"}, {"name": "ENV"}]) is False
    onbuild_run = {"name": "ONBUILD", "value": {"name": "RUN", "value": "x"}}
    assert needs_proot([onbuild_run]) is True
    onbuild_copy = {"name": "ONBUILD", "value": {"name": "COPY"}}
    assert needs_proot([onbuild_copy]) is False
