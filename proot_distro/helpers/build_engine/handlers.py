#
# Proot-Distro - manage proot containers.
#
# Created by Sylirre <sylirre@termux.dev> for Termux project.
# Development assisted by Claude Code (https://claude.ai/code).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

# Architecture: Metadata-only handlers — the Dockerfile instructions
# whose only side effect is to mutate the in-progress image config.
# Each handler receives the live BuildEngine and the parsed instruction
# record. The HANDLERS dispatch table at the bottom of this module
# binds every Dockerfile name to its function (including the RUN and
# COPY/ADD handlers that live in run_step.py and copy_step.py).

import json
import os
import shlex

from proot_distro.helpers.build_engine.copy_step import do_add, do_copy
from proot_distro.helpers.build_engine.errors import BuildError
from proot_distro.helpers.build_engine.constants import PREDEFINED_ARGS
from proot_distro.helpers.build_engine.parsing import (
    parse_kv_list, split_arg, to_argv,
)
from proot_distro.helpers.build_engine.run_step import do_run
from proot_distro.helpers.docker import layer_cache_path
from proot_distro.helpers.layer_diff import write_files_layer


def do_arg(engine, instr):
    """ARG NAME[=DEFAULT]: declare a build-time variable for this stage.

    Resolution order: --build-arg from the CLI, then the Dockerfile
    default, then the global-ARG value re-exposed by a bare `ARG NAME`,
    then a host env var when NAME is one of the predefined ARGs.
    Falls back to the empty string.
    """
    key, default = split_arg(instr["value"])
    if not key:
        raise BuildError(
            f"Invalid ARG at line {instr['lineno']}: {instr['value']!r}"
        )
    stage = engine.current
    stage.declared_args.add(key)
    if key in engine.user_build_args:
        stage.args[key] = engine.user_build_args[key]
    elif default is not None:
        stage.args[key] = default
    elif key in engine.global_args and key in engine.declared_global:
        # Bare `ARG NAME` re-exposes the global value inside the stage.
        stage.args[key] = engine.global_args[key]
    elif key in PREDEFINED_ARGS:
        stage.args[key] = os.environ.get(key, "")
    else:
        stage.args[key] = ""


def do_env(engine, instr):
    """ENV KEY=VALUE [KEY=VALUE...]: persist env vars in the image config.

    Mirrors the value into the stage's live ENV scope so subsequent
    instructions (including RUN) can expand `${KEY}` references.
    """
    value = instr["value"]
    if instr["exec_form"]:
        # ENV does not have an exec form in the spec; treat the
        # parsed list as space-joined raw value.
        value = " ".join(value)
    pairs = parse_kv_list(value)
    cfg = engine.current.image_config.setdefault("config", {})
    env_list = cfg.get("Env") or []
    env_map = {
        e.split("=", 1)[0]: e.split("=", 1)[1]
        for e in env_list
        if isinstance(e, str) and "=" in e
    }
    for k, v in pairs:
        env_map[k] = v
        engine.current.env[k] = v
    cfg["Env"] = [f"{k}={v}" for k, v in env_map.items()]


def do_label(engine, instr):
    """LABEL k=v [k=v...]: add OCI-style annotation labels."""
    value = instr["value"]
    if instr["exec_form"]:
        value = " ".join(value)
    pairs = parse_kv_list(value)
    cfg = engine.current.image_config.setdefault("config", {})
    labels = dict(cfg.get("Labels") or {})
    for k, v in pairs:
        labels[k] = v
    cfg["Labels"] = labels


def do_maintainer(engine, instr):
    """MAINTAINER "Name <addr>": legacy form of LABEL maintainer=."""
    cfg = engine.current.image_config.setdefault("config", {})
    labels = dict(cfg.get("Labels") or {})
    labels["maintainer"] = str(instr["value"]).strip()
    cfg["Labels"] = labels


def do_user(engine, instr):
    """USER name[:group]: set the identity that future RUN steps use."""
    engine.current.user = str(instr["value"]).strip()
    cfg = engine.current.image_config.setdefault("config", {})
    cfg["User"] = engine.current.user


def do_workdir(engine, instr):
    """WORKDIR PATH: set the cwd and create the directory on disk.

    Emits a thin layer covering any newly-created ancestor directories
    so the path still exists when the image is later applied to a
    fresh rootfs by `install`.
    """
    path = str(instr["value"]).strip()
    if not path:
        raise BuildError(
            f"WORKDIR with empty path at line {instr['lineno']}."
        )
    if not path.startswith("/"):
        path = os.path.normpath(
            os.path.join(engine.current.workdir or "/", path)
        )
    engine.current.workdir = path
    cfg = engine.current.image_config.setdefault("config", {})
    cfg["WorkingDir"] = path

    # Create the directory inside the rootfs and emit a thin layer that
    # captures every newly-created ancestor, so the path also exists
    # when the image is applied to a fresh rootfs by `install`.
    host_path = os.path.join(engine.current.rootfs_dir, path.lstrip("/"))
    new_dirs = []
    cur = host_path
    while cur and cur != engine.current.rootfs_dir:
        if not os.path.lexists(cur):
            new_dirs.append(cur)
        cur = os.path.dirname(cur)
    try:
        os.makedirs(host_path, exist_ok=True)
        os.chmod(host_path, 0o755)
    except OSError:
        return

    if not new_dirs:
        return

    file_map = {}
    for d in sorted(new_dirs):
        arc = os.path.relpath(d, engine.current.rootfs_dir)
        file_map[arc] = {
            "kind": "dir", "mode": 0o755, "uid": 0, "gid": 0, "mtime": 0,
        }

    tmp_layer_path = os.path.join(
        engine.tmp_root,
        f"layer-{engine.current.index}-{len(engine.current.layers)}.tar.gz",
    )
    digest, size, diff_id = write_files_layer(file_map, tmp_layer_path)
    final_path = layer_cache_path(digest)
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    os.replace(tmp_layer_path, final_path)
    engine.current.layers.append(
        {"digest": digest, "size": size, "diff_id": diff_id}
    )
    engine.current.parent_layer_digest = digest


def do_cmd(engine, instr):
    """CMD [argv]/CMD command: default argv for `proot-distro run`."""
    cfg = engine.current.image_config.setdefault("config", {})
    cfg["Cmd"] = to_argv(instr, engine.current.shell)


def do_entrypoint(engine, instr):
    """ENTRYPOINT [argv]: fixed argv that CMD/run-args are appended to."""
    cfg = engine.current.image_config.setdefault("config", {})
    cfg["Entrypoint"] = to_argv(instr, engine.current.shell)
    # Docker semantics: setting ENTRYPOINT resets CMD (typically
    # inherited from the base image). Users who want both put CMD
    # *after* ENTRYPOINT in the Dockerfile, which our linear
    # interpreter already handles correctly.
    cfg["Cmd"] = None


def do_expose(engine, instr):
    """EXPOSE port[/proto]: record container ports in image config."""
    cfg = engine.current.image_config.setdefault("config", {})
    ports = dict(cfg.get("ExposedPorts") or {})
    for token in shlex.split(str(instr["value"])):
        if "/" not in token:
            token = token + "/tcp"
        ports[token] = {}
    cfg["ExposedPorts"] = ports


def do_volume(engine, instr):
    """VOLUME PATH [PATH...]: record volume mount points in image config."""
    cfg = engine.current.image_config.setdefault("config", {})
    vols = dict(cfg.get("Volumes") or {})
    if instr["exec_form"]:
        paths = list(instr["value"])
    else:
        paths = shlex.split(str(instr["value"]))
    for p in paths:
        vols[p] = {}
    cfg["Volumes"] = vols


def do_stopsignal(engine, instr):
    """STOPSIGNAL signal: signal sent to stop the container (metadata only)."""
    cfg = engine.current.image_config.setdefault("config", {})
    cfg["StopSignal"] = str(instr["value"]).strip()


def do_shell(engine, instr):
    """SHELL ["/path", "-flag"]: argv used as the prefix for shell-form RUN."""
    if not instr["exec_form"]:
        raise BuildError(
            f"SHELL must be in JSON exec form at line {instr['lineno']}."
        )
    engine.current.shell = list(instr["value"])
    cfg = engine.current.image_config.setdefault("config", {})
    cfg["Shell"] = list(instr["value"])


def do_healthcheck(engine, instr):
    """HEALTHCHECK [NONE|CMD ...]: record healthcheck cmd in image config.

    Accepted forms are HEALTHCHECK NONE (clears any inherited check)
    or HEALTHCHECK [opts] CMD ... — opts like --interval are parsed
    but not enforced under proot-distro.
    """
    value = str(instr["value"]).strip()
    cfg = engine.current.image_config.setdefault("config", {})
    upper = value.split(None, 1)[0].upper() if value else ""
    if upper == "NONE":
        cfg["Healthcheck"] = {"Test": ["NONE"]}
        return
    # We parse the inner CMD only; HEALTHCHECK flags like --interval
    # are accepted but not enforced under proot-distro.
    if not upper.startswith("CMD"):
        raise BuildError(
            f"HEALTHCHECK must be 'NONE' or 'CMD ...' at line "
            f"{instr['lineno']}."
        )
    rest = value[len("CMD"):].strip()
    argv = None
    try:
        parsed = json.loads(rest)
        if isinstance(parsed, list):
            argv = ["CMD"] + list(parsed)
    except (json.JSONDecodeError, ValueError):
        pass
    if argv is None:
        argv = ["CMD-SHELL", rest]
    cfg["Healthcheck"] = {"Test": argv}


def do_onbuild(engine, instr):
    """ONBUILD <instr>: queue an instruction to run when this image is FROM-ed."""
    inner = instr["value"]
    if not isinstance(inner, dict):
        raise BuildError(
            f"ONBUILD is malformed at line {instr['lineno']}."
        )
    if engine.current is None:
        raise BuildError(
            f"ONBUILD before FROM at line {instr['lineno']}."
        )
    cfg = engine.current.image_config.setdefault("config", {})
    triggers = list(cfg.get("OnBuild") or [])
    triggers.append(inner["raw"])
    cfg["OnBuild"] = triggers


HANDLERS = {
    "ADD":         do_add,
    "ARG":         do_arg,
    "CMD":         do_cmd,
    "COPY":        do_copy,
    "ENTRYPOINT":  do_entrypoint,
    "ENV":         do_env,
    "EXPOSE":      do_expose,
    "HEALTHCHECK": do_healthcheck,
    "LABEL":       do_label,
    "MAINTAINER":  do_maintainer,
    "RUN":         do_run,
    "SHELL":       do_shell,
    "STOPSIGNAL":  do_stopsignal,
    "USER":        do_user,
    "VOLUME":      do_volume,
    "WORKDIR":     do_workdir,
}
