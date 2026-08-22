# Containment tests for the environment handed to the host-side proot exec.
#
# proot has no way to set the guest's environment on its own: the dict
# passed to os.execvpe(proot_bin, ...) is proot's own, and proot passes it
# on to the tracee. So a name that means "a setting for the container" to
# whoever wrote it means "a setting for the process that has not confined
# anything yet" to the dynamic loader. An image that gets to set
# LD_LIBRARY_PATH (or LD_AUDIT, or LD_PRELOAD) ships a libtalloc.so.2 at a
# path it also controls and runs its own code as the invoking user,
# outside any container — no race, no concurrent session, and in every
# mode, `--isolated` and `--minimal` included. PROOT_* is the same story
# for proot's own knobs.
#
# The rule is about provenance, not about the name: the user's own
# environment and their own --env flags still reach proot, because they
# already control the command line.

import json
from types import SimpleNamespace

import pytest

from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands.build import command_build
from proot_distro.commands.login import command_login
from proot_distro.execenv import is_host_exec_var
from proot_distro.helpers.build_engine.engine import _adopt_image_config
from proot_distro.helpers.build_engine.handlers import do_env


HOST_ARCH = get_device_cpu_arch()

HOSTILE_ENV = [
    "LD_LIBRARY_PATH=/evil/lib",
    "LD_PRELOAD=/evil/pre.so",
    "LD_AUDIT=/evil/audit.so",
    "PROOT_NO_SECCOMP=1",
    "PROOT_L2S_DIR=/evil/l2s",
    "LANG=C.UTF-8",
]


# --- the predicate ---------------------------------------------------------

@pytest.mark.parametrize("key", [
    "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "LD_BIND_NOW",
    "LD_SOMETHING_LIBC_ADDS_LATER",
    "PROOT_NO_SECCOMP", "PROOT_VERBOSE", "PROOT_L2S_DIR", "PROOT_TMP_DIR",
])
def test_host_exec_vars_are_matched_by_family(key):
    # A list would go stale: LD_AUDIT was already missing from the one
    # that existed, so the namespaces are matched by prefix.
    assert is_host_exec_var(key)


@pytest.mark.parametrize("key", [
    "LANG", "PATH", "HOME", "TERM", "LDAP_URI", "PROOTLESS",
])
def test_ordinary_vars_are_not(key):
    assert not is_host_exec_var(key)


# --- login -----------------------------------------------------------------

def _login_env(container_name, capsys, *, extra_env=(), **over):
    """Return the child environment login would exec proot with."""
    args = dict(
        container_name=container_name, get_proot_cmd=True, user="root",
        kernel=None, hostname="localhost", work_dir="",
        redirect_ports=False, isolated=False, minimal=False,
        shared_home=False, shared_tmp=False, shared_x11=False,
        no_link2symlink=False, no_sysvipc=False, no_kill_on_exit=False,
        detach=False, bind=[], env=list(extra_env), login_cmd=[],
        emulator=None,
    )
    args.update(over)
    with pytest.raises(SystemExit) as exc:
        command_login(SimpleNamespace(**args))
    assert exc.value.code == 0
    out = capsys.readouterr().out
    # `env -i K=V ... proot ...`: the assignments precede the binary.
    env = {}
    for token in out.replace("\\\n", " ").split():
        if token.endswith("proot") or token.startswith("--"):
            break
        if "=" in token and not token.startswith("env"):
            key, _, val = token.partition("=")
            env[key] = val.strip('"')
    return env


@pytest.fixture
def hostile_image(builders):
    builders.make_container(
        "box", arch=HOST_ARCH,
        manifest=builders.simple_image_manifest(env=HOSTILE_ENV),
    )


def test_image_env_cannot_reach_the_exec(hostile_image, capsys, monkeypatch):
    for var in ("PROOT_NO_SECCOMP", "PROOT_VERBOSE"):
        monkeypatch.delenv(var, raising=False)
    env = _login_env("box", capsys)
    assert not [k for k in env if is_host_exec_var(k)]
    # The rest of the image's Env still applies — this is a filter, not a
    # refusal to read the manifest.
    assert env["LANG"] == "C.UTF-8"


@pytest.mark.parametrize("isolated,minimal", [(True, False), (False, True)])
def test_image_env_is_blocked_in_every_mode(hostile_image, capsys,
                                            monkeypatch, isolated, minimal):
    # Image Env applies in isolated and minimal too, so the filter has to.
    for var in ("PROOT_NO_SECCOMP", "PROOT_VERBOSE"):
        monkeypatch.delenv(var, raising=False)
    env = _login_env("box", capsys, isolated=isolated, minimal=minimal)
    assert not [k for k in env if is_host_exec_var(k)]


def test_the_users_own_environment_still_reaches_proot(hostile_image, capsys,
                                                       monkeypatch):
    """`PROOT_NO_SECCOMP=1 proot-distro login debian` keeps working."""
    monkeypatch.setenv("PROOT_NO_SECCOMP", "1")
    monkeypatch.setenv("PROOT_VERBOSE", "2")
    env = _login_env("box", capsys)
    assert env["PROOT_NO_SECCOMP"] == "1"
    assert env["PROOT_VERBOSE"] == "2"
    # The image's own attempt at the same name did not decide it.
    assert "LD_LIBRARY_PATH" not in env


def test_the_users_own_env_flags_still_apply(hostile_image, capsys,
                                             monkeypatch):
    monkeypatch.delenv("PROOT_NO_SECCOMP", raising=False)
    env = _login_env("box", capsys, extra_env=[
        "LD_LIBRARY_PATH=/opt/lib", "PROOT_NO_SECCOMP=1",
    ])
    assert env["LD_LIBRARY_PATH"] == "/opt/lib"
    assert env["PROOT_NO_SECCOMP"] == "1"


def test_ld_preload_stays_dropped_from_every_source(hostile_image, capsys):
    env = _login_env("box", capsys, extra_env=["LD_PRELOAD=/x.so"])
    assert "LD_PRELOAD" not in env


# --- build -----------------------------------------------------------------

def test_pulled_base_config_is_filtered_at_adoption():
    """Filtered where a stranger's config is adopted, not where it is used.

    Everything downstream reads this list — the stage's env is seeded
    from it at FROM, `FROM <earlier stage>` deep-copies it, and the
    built image's own config comes from it — so one filter keeps all of
    them clean.
    """
    cfg = _adopt_image_config({"config": {"Env": list(HOSTILE_ENV)}}, "img")
    assert cfg["config"]["Env"] == ["LANG=C.UTF-8"]


def test_adopt_tolerates_the_shapes_a_registry_really_sends():
    for weird in ({}, {"config": None}, {"config": {}},
                  {"config": {"Env": None, "Cmd": None, "Labels": None}}):
        adopted = _adopt_image_config(json.loads(json.dumps(weird)), "img")
        assert isinstance(adopted["config"], dict)


class _Stage:
    def __init__(self):
        self.image_config = {"config": {}}
        self.env = {}


class _Engine:
    def __init__(self, firing):
        self.current = _Stage()
        self._firing_onbuild = firing


def _run_env(firing, value="LD_LIBRARY_PATH=/opt/lib SAFE=1"):
    engine = _Engine(firing)
    do_env(engine, {"value": value, "exec_form": False})
    return engine.current


def test_onbuild_env_from_the_base_image_is_filtered():
    """A trigger that fires is the image's line, never the author's."""
    stage = _run_env(firing=True)
    assert stage.env == {"SAFE": "1"}
    # Dropped from the produced config too, so it is not carried on to
    # whoever runs the built image next.
    assert stage.image_config["config"]["Env"] == ["SAFE=1"]


def test_an_authors_own_env_line_is_left_alone():
    """`ENV LD_LIBRARY_PATH=/opt/lib` is ordinary in a Dockerfile."""
    stage = _run_env(firing=False)
    assert stage.env["LD_LIBRARY_PATH"] == "/opt/lib"
    assert "LD_LIBRARY_PATH=/opt/lib" in stage.image_config["config"]["Env"]


def test_build_keeps_an_authors_env_in_the_produced_image(tmp_path, builders):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text(
        "FROM scratch\n"
        "ENV LD_LIBRARY_PATH=/opt/lib\n"
        "ENV SAFE=1\n"
    )
    command_build(SimpleNamespace(
        path=str(ctx), dockerfile=None, tags=["envimg:1"], build_args=[],
        override_arch=None, target_stage=None, emulator=None, outputs=[],
        install_as=None, no_cache=False, verbose=False, quiet=True,
    ))
    from proot_distro.helpers.docker.cache import load_manifest_cache
    _m, _r, cfg = load_manifest_cache("envimg:1", HOST_ARCH)
    assert "LD_LIBRARY_PATH=/opt/lib" in (cfg.get("config") or {}).get("Env")
