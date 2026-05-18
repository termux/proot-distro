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

# Architecture: Stateful interpreter that walks the parsed Dockerfile
# instructions, mutates an in-memory image config, and produces a
# sequence of layer blobs. Each instruction handler either touches
# only metadata (no layer) or produces exactly one new layer in the
# canonical OCI cache location (LAYER_CACHE_DIR/<digest_with_colon_as_underscore>).
#
# RUN handlers exec `proot` against the in-progress rootfs, snapshot
# the filesystem before/after, and write a diff layer. COPY/ADD
# handlers pack the affected source files directly into a layer tar
# (no snapshot needed since we know the changeset up-front). The
# remaining instructions (ENV, ARG, LABEL, WORKDIR, USER, CMD,
# ENTRYPOINT, EXPOSE, VOLUME, STOPSIGNAL, SHELL, HEALTHCHECK,
# ONBUILD) only mutate the image config.
#
# Multi-stage builds keep every stage's rootfs alive under tmp_root
# until the build finishes so COPY --from=<stage> can read from
# earlier stages. The final (or --target) stage's layers and config
# are returned to the caller for output.

import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request

from proot_distro.constants import (
    DEFAULT_PATH_ENV,
    DEFAULT_FAKE_KERNEL_RELEASE,
    DEFAULT_FAKE_KERNEL_VERSION,
    IS_TERMUX,
    LAYER_CACHE_DIR,
    PREFIX,
    PROGRAM_VERSION,
)
from proot_distro.colors import C, msg
from proot_distro.arch import (
    detect_installed_arch,
    get_device_cpu_arch,
    get_emulator_args,
    normalize_arch,
)
from proot_distro.helpers.docker import (
    _AuthStrippingRedirectHandler,
    _ARCH_TO_DOCKER,
    _layer_cache_path,
    pull_image,
)
from proot_distro.helpers.dockerfile import expand_vars, DockerfileSyntaxError
from proot_distro.helpers.layer_diff import (
    diff_snapshots,
    snapshot,
    write_files_layer,
    write_layer_tar,
)
from proot_distro.helpers.rootfs import write_hosts, write_resolv_conf
from proot_distro.sysdata import setup_fake_sysdata, fake_proc_bindings


# Architecture mapping for /proc/sys/kernel/uname's "machine" field
# (mirrors commands/login.py).
_ARCH_UNAME_M = {
    "aarch64": "aarch64",
    "arm":     "armv7l",
    "i686":    "i686",
    "x86_64":  "x86_64",
    "riscv64": "riscv64",
}

# Predefined ARG keys that are always visible without explicit
# declaration in the Dockerfile (subset of Docker's "predefined"
# build args).
_PREDEFINED_ARGS = frozenset({
    "TARGETPLATFORM", "TARGETOS", "TARGETARCH", "TARGETVARIANT",
    "BUILDPLATFORM", "BUILDOS", "BUILDARCH", "BUILDVARIANT",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "FTP_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "ftp_proxy", "all_proxy",
})

# Instructions whose argument values undergo variable expansion before
# dispatch (everything except CMD/ENTRYPOINT/RUN exec-form payloads).
_EXPANDS_VARS = frozenset({
    "ADD", "ARG", "ENV", "EXPOSE", "FROM", "LABEL", "STOPSIGNAL",
    "USER", "VOLUME", "WORKDIR", "COPY",
})

# Instructions that require executing `proot` against the rootfs.
PROOT_REQUIRED_INSTRUCTIONS = frozenset({"RUN"})


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------

class Stage:
    __slots__ = (
        "index", "name", "rootfs_dir", "image_config", "layers",
        "parent_layer_digest", "env", "args", "declared_args",
        "workdir", "user", "shell", "onbuild", "target_arch_pd",
        "target_arch_docker", "base_ref",
    )

    def __init__(self, index, name, rootfs_dir, target_arch_pd):
        self.index = index
        self.name = name
        self.rootfs_dir = rootfs_dir
        self.image_config = {"config": {}}
        self.layers = []
        self.parent_layer_digest = ""
        self.env = {}
        self.args = {}
        self.declared_args = set()
        self.workdir = "/"
        self.user = ""
        self.shell = ["/bin/sh", "-c"]
        self.onbuild = []
        self.target_arch_pd = target_arch_pd
        self.target_arch_docker = _ARCH_TO_DOCKER.get(
            target_arch_pd, (target_arch_pd, "")
        )[0]
        self.base_ref = ""


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class BuildError(Exception):
    """Raised by handlers when the build cannot proceed."""


class BuildEngine:
    def __init__(self,
                 build_dir,
                 tmp_root,
                 target_arch_pd,
                 user_build_args,
                 target_stage,
                 verbose,
                 quiet,
                 no_cache,
                 force_pull,
                 emulator):
        self.build_dir = os.path.abspath(build_dir)
        self.tmp_root = tmp_root
        self.target_arch_pd = target_arch_pd
        self.target_arch_docker = _ARCH_TO_DOCKER.get(
            target_arch_pd, (target_arch_pd, "")
        )[0]
        self.user_build_args = dict(user_build_args)
        self.target_stage = target_stage
        self.verbose = verbose
        self.quiet = quiet
        self.no_cache = no_cache
        self.force_pull = force_pull
        self.emulator = emulator
        self.stages = {}        # name → Stage
        self.stages_by_idx = []
        self.current = None
        self.global_args = {}   # ARG declared before first FROM
        self.declared_global = set()
        self.ignore_patterns = self._load_dockerignore()
        self._stop_after = False
        self._step_no = 0
        self._step_total = 0

    # ----- public entry point ----------------------------------------------

    def run(self, instructions):
        """Walk the instruction list and return the chosen stage."""
        self._prescan(instructions)

        self._step_total = len(instructions)
        for instr in instructions:
            self._step_no += 1
            self._announce(instr)
            self._dispatch(instr)
            if self._stop_after:
                break

        if self.current is None:
            raise BuildError("Dockerfile contains no FROM instruction.")

        return self._target()

    # ----- pre-scan --------------------------------------------------------

    def _prescan(self, instructions):
        seen_from = False
        named_stages = []
        for instr in instructions:
            if instr["name"] == "FROM":
                seen_from = True
                value = self._expand_for_from(instr["value"], pre_scan=True)
                m = re.search(r"\s+AS\s+(\S+)\s*$", value, re.IGNORECASE)
                if m:
                    named_stages.append(m.group(1))
            elif instr["name"] == "ARG" and not seen_from:
                key, default = _split_arg(instr["value"])
                if not key:
                    continue
                self.declared_global.add(key)
                value = self.user_build_args.get(key, default or "")
                self.global_args[key] = value

        if self.target_stage and self.target_stage not in named_stages:
            raise BuildError(
                f"--target stage '{self.target_stage}' is not defined in "
                f"the Dockerfile (known stages: "
                f"{', '.join(named_stages) or 'none'})."
            )

    def _expand_for_from(self, value, pre_scan=False):
        """Expand variables for a FROM line using global ARGs only."""
        scope = {}
        if pre_scan:
            # During pre-scan we only have CLI-supplied build args
            # for global keys declared *so far*. Pre-scan happens
            # before the user-arg merge; resolve from the raw map.
            scope.update(self.user_build_args)
        else:
            scope.update(self.global_args)
        # Predefined arch args are always available.
        scope.setdefault("TARGETARCH", self.target_arch_docker)
        scope.setdefault("BUILDARCH", _ARCH_TO_DOCKER.get(
            get_device_cpu_arch(), (get_device_cpu_arch(), "")
        )[0])
        scope.setdefault("TARGETOS", "linux")
        scope.setdefault("BUILDOS", "linux")
        scope.setdefault("TARGETPLATFORM",
                         f"linux/{self.target_arch_docker}")
        scope.setdefault("BUILDPLATFORM",
                         f"linux/{scope.get('BUILDARCH', 'amd64')}")
        return expand_vars(value, scope)

    # ----- step banner -----------------------------------------------------

    def _announce(self, instr):
        if self.quiet:
            return
        raw = instr.get("raw", "")
        if len(raw) > 120:
            raw = raw[:117] + "..."
        # Highlight the leading instruction token; the raw line already
        # begins with it, so we render it in colour rather than print it
        # twice as a separate label.
        parts = raw.split(None, 1)
        if len(parts) == 2:
            rendered = f"{C['YELLOW']}{parts[0]}{C['RST']} {parts[1]}"
        elif parts:
            rendered = f"{C['YELLOW']}{parts[0]}{C['RST']}"
        else:
            rendered = ""
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Step {self._step_no}/{self._step_total}: "
            f"{C['RST']}{rendered}")

    # ----- dispatcher ------------------------------------------------------

    def _dispatch(self, instr):
        name = instr["name"]
        if name == "FROM":
            self._do_from(instr)
            return
        if self.current is None:
            if name == "ARG":
                # Global ARG: already captured by _prescan; no-op here.
                return
            raise BuildError(
                f"Instruction '{name}' before any FROM at line "
                f"{instr['lineno']}."
            )
        if name == "ONBUILD":
            self._do_onbuild(instr)
            return
        if name in _EXPANDS_VARS and not instr["exec_form"]:
            instr = self._expand_instruction(instr)

        handler = _HANDLERS.get(name)
        if handler is None:
            raise BuildError(
                f"Unsupported instruction '{name}' at line {instr['lineno']}."
            )
        handler(self, instr)

    def _expand_instruction(self, instr):
        """Return a copy of instr with its `value` variable-expanded."""
        env = self._expansion_scope()
        new = dict(instr)
        value = instr.get("value", "")
        if isinstance(value, str):
            new["value"] = expand_vars(value, env)
        new["flags"] = {
            k: expand_vars(v, env) if isinstance(v, str) else v
            for k, v in instr.get("flags", {}).items()
        }
        return new

    def _expansion_scope(self):
        scope = {}
        for k in _PREDEFINED_ARGS:
            v = os.environ.get(k, "")
            if v:
                scope[k] = v
        scope.setdefault("TARGETARCH", self.target_arch_docker)
        scope.setdefault("TARGETOS", "linux")
        scope.setdefault("TARGETPLATFORM",
                         f"linux/{self.target_arch_docker}")
        scope.setdefault("BUILDARCH", _ARCH_TO_DOCKER.get(
            get_device_cpu_arch(), (get_device_cpu_arch(), "")
        )[0])
        scope.setdefault("BUILDOS", "linux")
        scope.setdefault("BUILDPLATFORM",
                         f"linux/{scope.get('BUILDARCH', 'amd64')}")
        # ARG values that have been declared in this stage so far.
        for k, v in self.current.args.items():
            scope[k] = v
        # ENV always wins over ARG.
        for k, v in self.current.env.items():
            scope[k] = v
        return scope

    # ----- FROM ------------------------------------------------------------

    def _do_from(self, instr):
        # If --target was set and the current stage is the target, the
        # next FROM marks the end of the build for this invocation.
        if (
            self.target_stage
            and self.current is not None
            and self.current.name == self.target_stage
        ):
            self._stop_after = True
            return

        value = self._expand_for_from(
            instr["value"] if isinstance(instr["value"], str) else "",
        )
        m = re.match(
            r"^\s*(\S+)(?:\s+AS\s+(\S+))?\s*$", value, re.IGNORECASE,
        )
        if not m:
            raise BuildError(
                f"Invalid FROM at line {instr['lineno']}: {value!r}"
            )
        base_ref = m.group(1)
        stage_name = m.group(2)

        idx = len(self.stages_by_idx)
        stage_dir = os.path.join(self.tmp_root, f"stage-{idx}")
        rootfs_dir = os.path.join(stage_dir, "rootfs")
        os.makedirs(rootfs_dir, exist_ok=True)
        stage = Stage(
            index=idx, name=stage_name, rootfs_dir=rootfs_dir,
            target_arch_pd=self.target_arch_pd,
        )
        stage.base_ref = base_ref

        # Resolve base.
        if base_ref.lower() == "scratch":
            stage.image_config = {"config": {}}
        elif base_ref in self.stages:
            self._inherit_from_stage(stage, self.stages[base_ref])
        else:
            self._pull_base_image(stage, base_ref)

        # Apply image-config defaults.
        cfg = stage.image_config.get("config") or {}
        env_list = cfg.get("Env") or []
        for entry in env_list:
            if isinstance(entry, str) and "=" in entry:
                k, _, v = entry.partition("=")
                stage.env[k] = v
        if cfg.get("WorkingDir"):
            stage.workdir = cfg["WorkingDir"] or "/"
        if cfg.get("User"):
            stage.user = cfg["User"]
        if cfg.get("Shell"):
            stage.shell = list(cfg["Shell"])

        # Re-declare global ARGs as available (no value unless declared
        # again in this stage). They start unset; the user has to use
        # `ARG NAME` (no default) to make them visible inside the stage.
        # (Docker semantics.)
        stage.args = {}
        stage.declared_args = set()

        self.stages_by_idx.append(stage)
        if stage_name:
            self.stages[stage_name] = stage
        # Implicit "by index" lookup for COPY --from=0, --from=1, etc.
        self.stages[str(idx)] = stage
        self.current = stage

        # Configure DNS so RUN apt-get etc. work.
        if os.path.isdir(os.path.join(rootfs_dir, "etc")):
            try:
                write_resolv_conf(rootfs_dir)
                write_hosts(rootfs_dir)
            except OSError:
                pass

        # Fire ONBUILD triggers from the base image's config.
        base_onbuild = (
            (stage.image_config.get("config") or {}).get("OnBuild") or []
        )
        if base_onbuild:
            # We feed each trigger string back through a one-line
            # parser by faking instruction records.
            from proot_distro.helpers.dockerfile import parse_dockerfile
            for trig in base_onbuild:
                _, trig_instrs = parse_dockerfile(trig + "\n")
                for ti in trig_instrs:
                    self._step_no += 1  # re-numbered to show progress
                    self._announce(ti)
                    if ti["name"] in _EXPANDS_VARS and not ti["exec_form"]:
                        ti = self._expand_instruction(ti)
                    h = _HANDLERS.get(ti["name"])
                    if h is None:
                        raise BuildError(
                            f"ONBUILD trigger uses unsupported "
                            f"instruction '{ti['name']}'."
                        )
                    h(self, ti)

    def _inherit_from_stage(self, new_stage, parent):
        """Apply parent's layers to new_stage.rootfs_dir, copy config."""
        from proot_distro.helpers.docker import _apply_layer
        new_stage.image_config = json.loads(
            json.dumps(parent.image_config)
        )
        for layer in parent.layers:
            cache_path = _layer_cache_path(layer["digest"])
            if not os.path.isfile(cache_path):
                raise BuildError(
                    f"Layer {layer['digest']} of stage "
                    f"'{parent.name or parent.index}' is missing from "
                    f"the cache."
                )
            _apply_layer(cache_path, new_stage.rootfs_dir)
        new_stage.layers = list(parent.layers)
        new_stage.parent_layer_digest = parent.parent_layer_digest

    def _pull_base_image(self, stage, image_ref):
        """Use helpers.docker.pull_image to populate the stage rootfs."""
        if not self.quiet:
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Pulling base image '{C['YELLOW']}{image_ref}{C['CYAN']}' "
                f"({self.target_arch_pd})...{C['RST']}")
        try:
            meta = pull_image(image_ref, stage.rootfs_dir,
                              self.target_arch_pd)
        except RuntimeError as exc:
            raise BuildError(f"FROM {image_ref}: {exc}") from exc

        stage.image_config = meta.get("image_config") or {"config": {}}
        manifest = meta.get("manifest") or {}
        # Build a per-layer record with diff_id when available.
        config_diff_ids = (
            (stage.image_config.get("rootfs") or {}).get("diff_ids") or []
        )
        stage.layers = []
        for i, layer in enumerate(manifest.get("layers", [])):
            digest = layer.get("digest", "")
            size = layer.get("size", 0)
            diff_id = (
                config_diff_ids[i]
                if i < len(config_diff_ids) else digest
            )
            stage.layers.append(
                {"digest": digest, "size": size, "diff_id": diff_id}
            )
        if stage.layers:
            stage.parent_layer_digest = stage.layers[-1]["digest"]

    # ----- ARG -------------------------------------------------------------

    def _do_arg(self, instr):
        key, default = _split_arg(instr["value"])
        if not key:
            raise BuildError(
                f"Invalid ARG at line {instr['lineno']}: {instr['value']!r}"
            )
        self.current.declared_args.add(key)
        if key in self.user_build_args:
            self.current.args[key] = self.user_build_args[key]
        elif default is not None:
            self.current.args[key] = default
        elif key in self.global_args and key in self.declared_global:
            # Bare `ARG NAME` re-exposes the global value inside the stage.
            self.current.args[key] = self.global_args[key]
        elif key in _PREDEFINED_ARGS:
            self.current.args[key] = os.environ.get(key, "")
        else:
            self.current.args[key] = ""

    # ----- ENV -------------------------------------------------------------

    def _do_env(self, instr):
        value = instr["value"]
        if instr["exec_form"]:
            # ENV does not have an exec form in the spec; treat the
            # parsed list as space-joined raw value.
            value = " ".join(value)
        pairs = _parse_kv_list(value)
        cfg = self.current.image_config.setdefault("config", {})
        env_list = cfg.get("Env") or []
        env_map = {
            e.split("=", 1)[0]: e.split("=", 1)[1]
            for e in env_list
            if isinstance(e, str) and "=" in e
        }
        for k, v in pairs:
            env_map[k] = v
            self.current.env[k] = v
        cfg["Env"] = [f"{k}={v}" for k, v in env_map.items()]

    # ----- LABEL -----------------------------------------------------------

    def _do_label(self, instr):
        value = instr["value"]
        if instr["exec_form"]:
            value = " ".join(value)
        pairs = _parse_kv_list(value)
        cfg = self.current.image_config.setdefault("config", {})
        labels = dict(cfg.get("Labels") or {})
        for k, v in pairs:
            labels[k] = v
        cfg["Labels"] = labels

    def _do_maintainer(self, instr):
        cfg = self.current.image_config.setdefault("config", {})
        labels = dict(cfg.get("Labels") or {})
        labels["maintainer"] = str(instr["value"]).strip()
        cfg["Labels"] = labels

    # ----- USER ------------------------------------------------------------

    def _do_user(self, instr):
        self.current.user = str(instr["value"]).strip()
        cfg = self.current.image_config.setdefault("config", {})
        cfg["User"] = self.current.user

    # ----- WORKDIR ---------------------------------------------------------

    def _do_workdir(self, instr):
        path = str(instr["value"]).strip()
        if not path:
            raise BuildError(
                f"WORKDIR with empty path at line {instr['lineno']}."
            )
        if not path.startswith("/"):
            path = os.path.normpath(
                os.path.join(self.current.workdir or "/", path)
            )
        self.current.workdir = path
        cfg = self.current.image_config.setdefault("config", {})
        cfg["WorkingDir"] = path

        # Create the directory inside the rootfs and emit a thin layer
        # that contains every newly-created ancestor so the path also
        # exists when the image is applied to a fresh rootfs (install).
        host_path = os.path.join(self.current.rootfs_dir, path.lstrip("/"))
        new_dirs = []
        cur = host_path
        while cur and cur != self.current.rootfs_dir:
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
            arc = os.path.relpath(d, self.current.rootfs_dir)
            file_map[arc] = {
                "kind": "dir", "mode": 0o755, "uid": 0, "gid": 0, "mtime": 0,
            }

        tmp_layer_path = os.path.join(
            self.tmp_root,
            f"layer-{self.current.index}-{len(self.current.layers)}.tar.gz",
        )
        digest, size, diff_id = write_files_layer(file_map, tmp_layer_path)
        final_path = _layer_cache_path(digest)
        os.makedirs(os.path.dirname(final_path), exist_ok=True)
        os.replace(tmp_layer_path, final_path)
        self.current.layers.append(
            {"digest": digest, "size": size, "diff_id": diff_id}
        )
        self.current.parent_layer_digest = digest

    # ----- CMD / ENTRYPOINT ------------------------------------------------

    def _do_cmd(self, instr):
        cfg = self.current.image_config.setdefault("config", {})
        cfg["Cmd"] = _to_argv(instr, self.current.shell)

    def _do_entrypoint(self, instr):
        cfg = self.current.image_config.setdefault("config", {})
        cfg["Entrypoint"] = _to_argv(instr, self.current.shell)
        # Docker semantics: setting ENTRYPOINT resets CMD (typically
        # inherited from the base image). Users who want both put CMD
        # *after* ENTRYPOINT in the Dockerfile, which our linear
        # interpreter already handles correctly.
        cfg["Cmd"] = None

    # ----- EXPOSE / VOLUME / STOPSIGNAL / SHELL / HEALTHCHECK --------------

    def _do_expose(self, instr):
        cfg = self.current.image_config.setdefault("config", {})
        ports = dict(cfg.get("ExposedPorts") or {})
        for token in shlex.split(str(instr["value"])):
            if "/" not in token:
                token = token + "/tcp"
            ports[token] = {}
        cfg["ExposedPorts"] = ports

    def _do_volume(self, instr):
        cfg = self.current.image_config.setdefault("config", {})
        vols = dict(cfg.get("Volumes") or {})
        if instr["exec_form"]:
            paths = list(instr["value"])
        else:
            paths = shlex.split(str(instr["value"]))
        for p in paths:
            vols[p] = {}
        cfg["Volumes"] = vols

    def _do_stopsignal(self, instr):
        cfg = self.current.image_config.setdefault("config", {})
        cfg["StopSignal"] = str(instr["value"]).strip()

    def _do_shell(self, instr):
        if not instr["exec_form"]:
            raise BuildError(
                f"SHELL must be in JSON exec form at line "
                f"{instr['lineno']}."
            )
        self.current.shell = list(instr["value"])
        cfg = self.current.image_config.setdefault("config", {})
        cfg["Shell"] = list(instr["value"])

    def _do_healthcheck(self, instr):
        value = str(instr["value"]).strip()
        cfg = self.current.image_config.setdefault("config", {})
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
        # Try JSON array first.
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

    # ----- ONBUILD ---------------------------------------------------------

    def _do_onbuild(self, instr):
        inner = instr["value"]      # already an instruction record
        if not isinstance(inner, dict):
            raise BuildError(
                f"ONBUILD is malformed at line {instr['lineno']}."
            )
        if self.current is None:
            raise BuildError(
                f"ONBUILD before FROM at line {instr['lineno']}."
            )
        cfg = self.current.image_config.setdefault("config", {})
        triggers = list(cfg.get("OnBuild") or [])
        triggers.append(inner["raw"])
        cfg["OnBuild"] = triggers

    # ----- RUN -------------------------------------------------------------

    def _do_run(self, instr):
        stage = self.current

        # Build the command list and stdin.
        stdin_input = None
        if instr["exec_form"]:
            command = list(instr["value"])
        else:
            heredocs = instr.get("heredocs") or []
            if heredocs:
                # Concatenate all heredoc bodies and pass them as the
                # argument to the SHELL -c. (Some Dockerfiles use the
                # form `RUN <<EOF cat`, which would imply piping into
                # an interpreter named on the value line — we don't
                # parse that variant in v1; the body is always handed
                # to the default SHELL.)
                body = "\n".join(hd["body"] for hd in heredocs)
                command = list(stage.shell) + [body]
            else:
                command = list(stage.shell) + [str(instr["value"])]

        # Cache lookup.
        extra = self._run_extra_inputs(stage)
        from proot_distro.helpers.build_cache import (
            compute_recipe_hash, lookup as cache_lookup, record as cache_record
        )
        recipe = compute_recipe_hash(
            stage.parent_layer_digest, instr, extra_inputs=extra
        )
        if not self.no_cache:
            hit = cache_lookup(recipe)
            if hit is not None:
                cached_path = _layer_cache_path(hit["layer_digest"])
                if os.path.isfile(cached_path):
                    self._info(f"Cache hit: applying layer "
                               f"{hit['layer_digest'][:19]}.")
                    from proot_distro.helpers.docker import _apply_layer
                    _apply_layer(cached_path, stage.rootfs_dir)
                    stage.layers.append({
                        "digest": hit["layer_digest"],
                        "size": hit["size"],
                        "diff_id": hit["diff_id"],
                    })
                    stage.parent_layer_digest = hit["layer_digest"]
                    return

        self._info("Indexing rootfs state...")
        before = snapshot(stage.rootfs_dir)
        exit_code = self._exec_proot(stage, command, stdin_input)
        if exit_code != 0:
            raise BuildError(
                f"RUN command failed at line {instr['lineno']} "
                f"with exit code {exit_code}."
            )

        self._info("Capturing filesystem changes...")
        after = snapshot(stage.rootfs_dir)
        added, modified, deleted = diff_snapshots(before, after)
        paths_to_pack = added + modified

        if not (paths_to_pack or deleted):
            self._info("No filesystem changes; emitting an empty layer.")
        else:
            self._info(
                f"Packing layer: {len(added)} added, "
                f"{len(modified)} modified, {len(deleted)} deleted..."
            )

        # Write the layer tar to a temp file with a placeholder name,
        # then move it into the layer cache under its content digest.
        tmp_layer_path = os.path.join(
            self.tmp_root, f"layer-{stage.index}-{len(stage.layers)}.tar.gz"
        )
        digest, size, diff_id = write_layer_tar(
            stage.rootfs_dir, paths_to_pack, deleted, tmp_layer_path,
        )
        final_path = _layer_cache_path(digest)
        os.makedirs(os.path.dirname(final_path), exist_ok=True)
        os.replace(tmp_layer_path, final_path)

        stage.layers.append(
            {"digest": digest, "size": size, "diff_id": diff_id}
        )
        stage.parent_layer_digest = digest
        cache_record(recipe, digest, diff_id, size, {})

    def _run_extra_inputs(self, stage):
        """Encode env + ARG state for the recipe hash."""
        scope = self._expansion_scope()
        items = sorted(scope.items())
        return "\n".join(f"{k}={v}" for k, v in items)

    # ----- COPY / ADD ------------------------------------------------------

    def _do_copy(self, instr):
        self._do_copy_or_add(instr, allow_url=False, auto_extract=False)

    def _do_add(self, instr):
        self._do_copy_or_add(instr, allow_url=True, auto_extract=True)

    def _do_copy_or_add(self, instr, allow_url, auto_extract):
        stage = self.current
        flags = instr.get("flags") or {}

        if instr["exec_form"]:
            tokens = list(instr["value"])
        else:
            tokens = shlex.split(str(instr["value"]))
        if len(tokens) < 2:
            raise BuildError(
                f"{instr['name']} requires at least one source and a "
                f"destination at line {instr['lineno']}."
            )

        sources = tokens[:-1]
        dest = tokens[-1]

        # Reject BuildKit-only flags loudly.
        for k in flags:
            if k in ("link", "parents"):
                raise BuildError(
                    f"{instr['name']} --{k} is a BuildKit-only flag and "
                    f"is not supported (line {instr['lineno']})."
                )

        chown = flags.get("chown")
        chmod = flags.get("chmod")
        from_stage = flags.get("from")
        from_image = None
        from_rootfs = None
        if from_stage:
            ref_stage = self.stages.get(from_stage)
            if ref_stage is None:
                # Treat as an image reference: pull into a throwaway
                # rootfs and use its filesystem as the source.
                from_image = from_stage
                from_rootfs = self._pull_throwaway_image(from_image)
            else:
                from_rootfs = ref_stage.rootfs_dir

        # Resolve sources.
        resolved = []
        if from_rootfs is None:
            # Sources are relative to the build context.
            for src in sources:
                if allow_url and _looks_like_url(src):
                    resolved.append(("url", src))
                else:
                    resolved.append(("ctx", src))
        else:
            for src in sources:
                resolved.append(("rootfs", src))

        # Build dest semantics.
        is_dir_dest = dest.endswith("/") or len(sources) > 1
        if not dest.startswith("/"):
            dest = os.path.normpath(
                os.path.join(stage.workdir or "/", dest)
            )

        # Resolve --chown to UID/GID using the rootfs's /etc/passwd.
        uid, gid = self._resolve_chown(chown) if chown else (0, 0)
        mode_override = (
            int(chmod, 8) if chmod and re.match(r"^[0-7]+$", chmod) else None
        )

        # Compute the file_map for the layer + simultaneously write
        # the files into the rootfs.
        file_map = {}
        for kind, src in resolved:
            if kind == "url":
                self._copy_url(src, dest, file_map, uid, gid,
                               mode_override, stage)
            elif kind == "ctx":
                self._copy_from_context(
                    src, dest, is_dir_dest, file_map,
                    uid, gid, mode_override,
                    stage, auto_extract,
                )
            elif kind == "rootfs":
                self._copy_from_rootfs(
                    from_rootfs, src, dest, is_dir_dest, file_map,
                    uid, gid, mode_override, stage,
                )

        if not file_map:
            return

        # Apply to rootfs.
        self._materialise_files(stage.rootfs_dir, file_map)

        # Write a single-purpose layer.
        tmp_layer_path = os.path.join(
            self.tmp_root,
            f"layer-{stage.index}-{len(stage.layers)}.tar.gz",
        )
        digest, size, diff_id = write_files_layer(file_map, tmp_layer_path)
        final_path = _layer_cache_path(digest)
        os.makedirs(os.path.dirname(final_path), exist_ok=True)
        os.replace(tmp_layer_path, final_path)
        stage.layers.append(
            {"digest": digest, "size": size, "diff_id": diff_id}
        )
        stage.parent_layer_digest = digest

    def _pull_throwaway_image(self, image_ref):
        """Pull an external image into a tmp rootfs for COPY --from."""
        slot = hashlib.sha256(image_ref.encode()).hexdigest()[:16]
        rootfs = os.path.join(self.tmp_root, "copyfrom-" + slot)
        if os.path.isdir(rootfs) and os.listdir(rootfs):
            return rootfs
        os.makedirs(rootfs, exist_ok=True)
        if not self.quiet:
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"COPY --from='{image_ref}': fetching external image..."
                f"{C['RST']}")
        try:
            pull_image(image_ref, rootfs, self.target_arch_pd)
        except RuntimeError as exc:
            raise BuildError(
                f"COPY --from={image_ref}: {exc}"
            ) from exc
        return rootfs

    def _copy_from_context(self, src, dest, is_dir_dest, file_map,
                           uid, gid, mode_override, stage, auto_extract):
        # Per Docker semantics, a leading '/' on a COPY/ADD source is
        # equivalent to no leading slash: both forms resolve relative
        # to the build context root.
        src_rel_raw = src.lstrip("/")

        # We still forbid escaping the build context with ".." segments
        # after path normalisation.
        full = os.path.normpath(os.path.join(self.build_dir, src_rel_raw))
        if (full != self.build_dir
                and not full.startswith(self.build_dir + os.sep)):
            raise BuildError(
                f"COPY source '{src}' escapes the build context."
            )
        if not os.path.exists(full):
            # Try glob expansion.
            matches = sorted(_simple_glob(self.build_dir, src_rel_raw))
            matches = [m for m in matches if not self._ignored(m)]
            if not matches:
                raise BuildError(
                    f"COPY/ADD source '{src}' not found in build context."
                )
            for m in matches:
                full_m = os.path.join(self.build_dir, m)
                self._add_to_file_map(
                    full_m, dest, is_dir_dest=True, file_map=file_map,
                    uid=uid, gid=gid, mode_override=mode_override,
                    auto_extract=auto_extract, src_rel=m,
                )
            return
        rel = os.path.relpath(full, self.build_dir)
        if self._ignored(rel):
            return
        self._add_to_file_map(
            full, dest, is_dir_dest=is_dir_dest, file_map=file_map,
            uid=uid, gid=gid, mode_override=mode_override,
            auto_extract=auto_extract, src_rel=rel,
        )

    def _copy_from_rootfs(self, from_rootfs, src, dest, is_dir_dest,
                          file_map, uid, gid, mode_override, stage):
        full = os.path.normpath(os.path.join(from_rootfs, src.lstrip("/")))
        if not full.startswith(os.path.abspath(from_rootfs)):
            raise BuildError(
                f"COPY --from source '{src}' escapes the source rootfs."
            )
        if not os.path.lexists(full):
            raise BuildError(
                f"COPY --from source '{src}' not found in stage."
            )
        self._add_to_file_map(
            full, dest, is_dir_dest=is_dir_dest, file_map=file_map,
            uid=uid, gid=gid, mode_override=mode_override,
            auto_extract=False, src_rel=src,
        )

    def _copy_url(self, url, dest, file_map, uid, gid, mode_override, stage):
        # ADD URL: download the file to dest.
        if dest.endswith("/"):
            name = os.path.basename(urllib.parse.urlparse(url).path) or "index"
            arcname = (dest.lstrip("/") + name)
        else:
            arcname = dest.lstrip("/")
        opener = urllib.request.build_opener(_AuthStrippingRedirectHandler)
        try:
            with opener.open(url) as resp:
                data = resp.read()
        except (urllib.error.URLError, OSError) as exc:
            raise BuildError(f"ADD {url}: {exc}") from exc
        file_map[arcname] = {
            "kind": "content",
            "data": data,
            "mode": mode_override if mode_override is not None else 0o644,
            "uid": uid, "gid": gid, "mtime": int(time.time()),
        }

    # --- file_map population -----------------------------------------------

    def _add_to_file_map(self, src_full, dest, is_dir_dest, file_map,
                         uid, gid, mode_override, auto_extract, src_rel):
        if os.path.islink(src_full):
            self._add_symlink(src_full, dest, is_dir_dest, file_map,
                              uid, gid, src_rel)
            return
        if os.path.isdir(src_full):
            self._add_directory_tree(
                src_full, dest, file_map, uid, gid, mode_override, src_rel,
            )
            return
        if os.path.isfile(src_full):
            # Auto-extract tar archives for ADD.
            if auto_extract and _is_tar_archive(src_full):
                self._extract_tar_into_dest(src_full, dest, file_map,
                                            uid, gid)
                return
            self._add_regular(src_full, dest, is_dir_dest, file_map,
                              uid, gid, mode_override, src_rel)
            return

    def _add_regular(self, src_full, dest, is_dir_dest, file_map,
                     uid, gid, mode_override, src_rel):
        arcname = self._dest_arcname(src_full, dest, is_dir_dest, src_rel)
        try:
            mode = stat.S_IMODE(os.lstat(src_full).st_mode)
        except OSError:
            mode = 0o644
        if mode_override is not None:
            mode = mode_override
        file_map[arcname] = {
            "kind": "file", "src": src_full,
            "mode": mode, "uid": uid, "gid": gid,
            "mtime": int(os.lstat(src_full).st_mtime),
        }

    def _add_symlink(self, src_full, dest, is_dir_dest, file_map,
                     uid, gid, src_rel):
        arcname = self._dest_arcname(src_full, dest, is_dir_dest, src_rel)
        try:
            target = os.readlink(src_full)
        except OSError:
            return
        file_map[arcname] = {
            "kind": "symlink", "target": target,
            "mode": 0o777, "uid": uid, "gid": gid,
            "mtime": int(os.lstat(src_full).st_mtime),
        }

    def _add_directory_tree(self, src_full, dest, file_map,
                            uid, gid, mode_override, src_rel):
        # When source is a directory, the entries themselves go into
        # dest. The destination is treated as a directory.
        if not dest.endswith("/"):
            dest = dest + "/"
        for dirpath, dirnames, filenames in os.walk(
            src_full, followlinks=False,
        ):
            rel = os.path.relpath(dirpath, src_full)
            for d in list(dirnames):
                full = os.path.join(dirpath, d)
                if os.path.islink(full):
                    arc = self._make_subpath(dest, rel, d).lstrip("/")
                    try:
                        tgt = os.readlink(full)
                    except OSError:
                        continue
                    file_map[arc] = {
                        "kind": "symlink", "target": tgt,
                        "mode": 0o777, "uid": uid, "gid": gid,
                        "mtime": 0,
                    }
                    dirnames.remove(d)
            # Add the directory itself (except the root).
            if rel != ".":
                arc = self._make_subpath(dest, rel, "").rstrip("/").lstrip("/")
                if arc:
                    try:
                        mode = stat.S_IMODE(os.lstat(dirpath).st_mode)
                    except OSError:
                        mode = 0o755
                    file_map[arc] = {
                        "kind": "dir",
                        "mode": mode_override if mode_override is not None else mode,
                        "uid": uid, "gid": gid, "mtime": 0,
                    }
            for f in filenames:
                full = os.path.join(dirpath, f)
                src_relpath = os.path.relpath(full, src_full)
                if self._ignored_in_subdir(src_rel, src_relpath):
                    continue
                arc = self._make_subpath(dest, rel, f).lstrip("/")
                if os.path.islink(full):
                    try:
                        tgt = os.readlink(full)
                    except OSError:
                        continue
                    file_map[arc] = {
                        "kind": "symlink", "target": tgt,
                        "mode": 0o777, "uid": uid, "gid": gid,
                        "mtime": int(os.lstat(full).st_mtime),
                    }
                else:
                    try:
                        mode = stat.S_IMODE(os.lstat(full).st_mode)
                    except OSError:
                        mode = 0o644
                    if mode_override is not None:
                        mode = mode_override
                    file_map[arc] = {
                        "kind": "file", "src": full,
                        "mode": mode, "uid": uid, "gid": gid,
                        "mtime": int(os.lstat(full).st_mtime),
                    }

    def _make_subpath(self, dest, rel, name):
        parts = [dest.rstrip("/")]
        if rel and rel != ".":
            parts.append(rel)
        if name:
            parts.append(name)
        return "/".join(p.strip("/") for p in parts if p is not None)

    def _dest_arcname(self, src_full, dest, is_dir_dest, src_rel):
        if is_dir_dest or dest.endswith("/"):
            base = os.path.basename(src_full.rstrip("/"))
            arc = (dest.rstrip("/") + "/" + base).lstrip("/")
        else:
            arc = dest.lstrip("/")
        return arc

    def _extract_tar_into_dest(self, src_full, dest, file_map, uid, gid):
        """ADD auto-extract: stream the tar into dest as a tree."""
        if not dest.endswith("/"):
            dest = dest + "/"
        with tarfile.open(src_full, "r|*") as tf:
            for m in tf:
                if m.isblk() or m.ischr() or m.isfifo():
                    continue
                rel = m.name.lstrip("./").lstrip("/")
                if any(p in ("..", "") for p in rel.split("/")):
                    continue
                arc = (dest + rel).lstrip("/")
                if m.isdir():
                    file_map[arc] = {
                        "kind": "dir",
                        "mode": stat.S_IMODE(m.mode) or 0o755,
                        "uid": uid, "gid": gid, "mtime": int(m.mtime),
                    }
                elif m.issym():
                    file_map[arc] = {
                        "kind": "symlink", "target": m.linkname,
                        "mode": 0o777, "uid": uid, "gid": gid,
                        "mtime": int(m.mtime),
                    }
                elif m.isreg():
                    fobj = tf.extractfile(m)
                    if fobj is None:
                        continue
                    data = fobj.read()
                    file_map[arc] = {
                        "kind": "content", "data": data,
                        "mode": stat.S_IMODE(m.mode) or 0o644,
                        "uid": uid, "gid": gid, "mtime": int(m.mtime),
                    }
                # Hard links and other types ignored in tar auto-extract.

    def _materialise_files(self, rootfs_dir, file_map):
        """Apply file_map entries to rootfs_dir on disk."""
        # Sort so directories come before their children.
        for arcname in sorted(file_map.keys()):
            entry = file_map[arcname]
            host = os.path.join(rootfs_dir, arcname.lstrip("/"))
            parent = os.path.dirname(host)
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError:
                pass
            kind = entry["kind"]
            try:
                if kind == "dir":
                    os.makedirs(host, exist_ok=True)
                    try:
                        os.chmod(host, entry.get("mode", 0o755))
                    except OSError:
                        pass
                elif kind == "symlink":
                    if os.path.lexists(host):
                        try:
                            os.remove(host)
                        except OSError:
                            pass
                    os.symlink(entry["target"], host)
                elif kind == "content":
                    if os.path.lexists(host):
                        try:
                            os.remove(host)
                        except OSError:
                            pass
                    with open(host, "wb") as fh:
                        fh.write(entry["data"])
                    try:
                        os.chmod(host, entry.get("mode", 0o644))
                    except OSError:
                        pass
                elif kind == "file":
                    if os.path.lexists(host):
                        try:
                            os.remove(host)
                        except OSError:
                            pass
                    shutil.copyfile(entry["src"], host)
                    try:
                        os.chmod(host, entry.get("mode", 0o644))
                    except OSError:
                        pass
            except OSError as exc:
                raise BuildError(
                    f"Failed to write '{arcname}' into rootfs: {exc}"
                ) from exc

    def _resolve_chown(self, chown):
        """Resolve --chown=user[:group] against the rootfs /etc/passwd."""
        if ":" in chown:
            user, group = chown.split(":", 1)
        else:
            user, group = chown, ""
        uid = self._resolve_id(user, is_group=False, default=0)
        gid = (
            self._resolve_id(group, is_group=True, default=uid)
            if group else uid
        )
        return uid, gid

    def _resolve_id(self, name, is_group, default):
        if not name:
            return default
        if name.isdigit():
            return int(name)
        # Look up in rootfs's passwd / group.
        path = os.path.join(
            self.current.rootfs_dir,
            "etc", "group" if is_group else "passwd",
        )
        try:
            with open(path) as fh:
                for line in fh:
                    parts = line.split(":")
                    if parts and parts[0] == name and len(parts) > 2:
                        try:
                            return int(parts[2])
                        except ValueError:
                            return default
        except OSError:
            pass
        return default

    # ----- .dockerignore ---------------------------------------------------

    def _load_dockerignore(self):
        path = os.path.join(self.build_dir, ".dockerignore")
        patterns = []
        try:
            with open(path) as fh:
                for line in fh:
                    s = line.rstrip("\n").rstrip("\r").strip()
                    if not s or s.startswith("#"):
                        continue
                    patterns.append(s)
        except OSError:
            pass
        return patterns

    def _ignored(self, rel_path):
        if not self.ignore_patterns:
            return False
        # `Dockerfile` and `.dockerignore` themselves are never ignored.
        if rel_path in ("Dockerfile", ".dockerignore"):
            return False
        ignored = False
        for pat in self.ignore_patterns:
            negate = pat.startswith("!")
            p = pat[1:] if negate else pat
            if _match_dockerignore(rel_path, p):
                ignored = not negate
        return ignored

    def _ignored_in_subdir(self, parent_src_rel, sub_rel):
        # Combine parent + subpath into the path used for matching.
        rel = (
            (parent_src_rel + "/" + sub_rel)
            if parent_src_rel and parent_src_rel != "."
            else sub_rel
        )
        return self._ignored(rel)

    # ----- proot exec ------------------------------------------------------

    def _exec_proot(self, stage, command, stdin_input):
        rootfs = stage.rootfs_dir
        proot_bin = shutil.which("proot") or "proot"
        proot_args = [proot_bin]

        emu_args = get_emulator_args(
            stage.target_arch_pd, get_device_cpu_arch(), self.emulator or "",
        )
        need_emu = bool(emu_args)
        proot_args += emu_args

        if IS_TERMUX:
            proot_args += ["--kill-on-exit", "--link2symlink", "--sysvipc"]
            uname_m = _ARCH_UNAME_M.get(
                stage.target_arch_pd, os.uname().machine,
            )
            proot_args.append(
                f"--kernel-release=\\Linux\\proot-distro-build"
                f"\\{DEFAULT_FAKE_KERNEL_RELEASE}"
                f"\\{DEFAULT_FAKE_KERNEL_VERSION}\\{uname_m}\\localdomain\\-1\\"
            )
            proot_args.append("-L")

        # Resolve current user against rootfs /etc/passwd.
        uid, gid = self._resolve_user_for_proot(rootfs, stage.user)
        proot_args.append(f"--change-id={uid}:{gid}")
        proot_args.append(f"--rootfs={rootfs}")
        proot_args.append(f"--cwd={stage.workdir or '/'}")
        proot_args += ["--bind=/dev", "--bind=/proc", "--bind=/sys"]

        if IS_TERMUX:
            proot_args += [
                "--bind=/dev/urandom:/dev/random",
                "--bind=/proc/self/fd:/dev/fd",
            ]
            for i, name in ((0, "stdin"), (1, "stdout"), (2, "stderr")):
                if os.path.exists(f"/proc/self/fd/{i}"):
                    proot_args.append(f"--bind=/proc/self/fd/{i}:/dev/{name}")
            setup_fake_sysdata(rootfs)
            proot_args.append(f"--bind={rootfs}/sys/.empty:/sys/fs/selinux")
            proot_args += fake_proc_bindings(rootfs)
            tmp_dir = os.path.join(rootfs, "tmp")
            os.makedirs(tmp_dir, exist_ok=True)
            try:
                os.chmod(tmp_dir, 0o1777)
            except OSError:
                pass
            proot_args.append(f"--bind={tmp_dir}:/dev/shm")

        if need_emu and IS_TERMUX:
            for path in (
                "/apex", "/odm", "/product", "/system",
                "/system_ext", "/vendor",
                "/linkerconfig/ld.config.txt",
                "/plat_property_contexts", "/property_contexts",
            ):
                if os.path.exists(path):
                    proot_args.append(f"--bind={path}")
            proot_args.append(f"--bind={PREFIX}")

        proot_args.extend(command)

        # Build child env.
        child_env = self._build_child_env(stage)

        # Run.
        if not self.quiet and not self.verbose:
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Running step (user={stage.user or 'root'}, "
                f"cwd={stage.workdir or '/'})..."
                f"{C['RST']}")

        try:
            stdin_arg = (
                subprocess.PIPE if stdin_input is not None
                else subprocess.DEVNULL
            )
            proc = subprocess.Popen(
                proot_args,
                env=child_env,
                stdin=stdin_arg,
            )
            if stdin_input is not None:
                proc.communicate(input=stdin_input.encode())
            else:
                proc.wait()
            return proc.returncode
        except FileNotFoundError as exc:
            raise BuildError(f"proot binary not available: {exc}") from exc

    def _resolve_user_for_proot(self, rootfs, user_spec):
        if not user_spec:
            return (0, 0)
        spec = str(user_spec).strip()
        if ":" in spec:
            u, g = spec.split(":", 1)
        else:
            u, g = spec, ""
        uid = self._resolve_id(u, is_group=False, default=0)
        gid = self._resolve_id(g, is_group=True, default=uid) if g else uid
        return uid, gid

    def _build_child_env(self, stage):
        env = {}
        env["PATH"] = (
            stage.env.get("PATH") or DEFAULT_PATH_ENV
        )
        env["HOME"] = stage.env.get("HOME", "/root")
        env["TERM"] = os.environ.get("TERM", "") or "xterm-256color"
        host_colorterm = os.environ.get("COLORTERM", "")
        if host_colorterm:
            env["COLORTERM"] = host_colorterm

        # Predefined ARGs from the host environment (proxies etc.) —
        # passed through even if the Dockerfile didn't declare them.
        for k in _PREDEFINED_ARGS:
            v = os.environ.get(k, "")
            if v:
                env[k] = v

        # Declared ARGs in this stage.
        for k in stage.declared_args:
            if k in stage.args:
                env[k] = stage.args[k]

        # ENVs always win.
        for k, v in stage.env.items():
            env[k] = v

        # proot toggles inherited from host.
        for var in ("PROOT_NO_SECCOMP", "PROOT_VERBOSE"):
            v = os.environ.get(var, "")
            if v:
                env[var] = v
        if IS_TERMUX:
            l2s_dir = os.path.join(stage.rootfs_dir, ".l2s")
            os.makedirs(l2s_dir, exist_ok=True)
            env["PROOT_L2S_DIR"] = l2s_dir
        env.pop("LD_PRELOAD", None)
        return env

    # ----- target stage resolution ----------------------------------------

    def _target(self):
        if self.target_stage:
            stage = self.stages.get(self.target_stage)
            if stage is None:
                raise BuildError(
                    f"--target stage '{self.target_stage}' was not built."
                )
            return stage
        return self.current

    # ----- helpers ---------------------------------------------------------

    def _info(self, text):
        if self.quiet:
            return
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"{text}{C['RST']}")


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _split_arg(value):
    """Parse `ARG K[=V]` value text. Returns (key, default_or_None)."""
    if isinstance(value, list):
        value = " ".join(value)
    s = str(value).strip()
    if not s:
        return ("", None)
    if "=" in s:
        k, _, v = s.partition("=")
        return (k.strip(), v)
    return (s, None)


def _parse_kv_list(value):
    """Parse ENV/LABEL key=value pairs (with shell-like quoting)."""
    s = str(value).strip()
    if "=" not in s:
        # Legacy ENV form: `ENV KEY value` (no equals). Single pair.
        toks = s.split(None, 1)
        if len(toks) == 2:
            return [(toks[0], toks[1])]
        return [(s, "")]
    try:
        lex = shlex.shlex(s, posix=True)
        lex.whitespace_split = True
        lex.commenters = ""
        tokens = list(lex)
    except ValueError as exc:
        raise BuildError(f"Cannot parse key=value list: {exc}") from exc
    pairs = []
    for t in tokens:
        if "=" not in t:
            continue
        k, _, v = t.partition("=")
        pairs.append((k, v))
    return pairs


def _to_argv(instr, default_shell):
    """Convert a CMD/ENTRYPOINT instruction into an argv list.

    Exec form: the value is already a list.
    Shell form: wrap the value with the default shell.
    """
    if instr["exec_form"]:
        return list(instr["value"])
    raw = str(instr["value"])
    return list(default_shell) + [raw]


def _looks_like_url(s):
    return s.startswith(("http://", "https://"))


def _is_tar_archive(path):
    """Cheap signature-only check for tar/tar.gz/tar.bz2/tar.xz."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(265)
    except OSError:
        return False
    if len(head) < 265:
        return False
    if head[257:263] == b"ustar\x00" or head[257:265] == b"ustar  \x00":
        return True
    if head[:3] == b"\x1f\x8b\x08":
        return True
    if head[:3] == b"BZh":
        return True
    if head[:6] == b"\xfd7zXZ\x00":
        return True
    return False


def _simple_glob(base, pattern):
    """Tiny glob: supports * and ? only (no ** recursion).

    Returns rel paths under `base` that match.
    """
    import glob as _glob
    abs_pat = os.path.join(base, pattern)
    matches = _glob.glob(abs_pat)
    return [os.path.relpath(p, base) for p in matches]


def _match_dockerignore(rel_path, pattern):
    """fnmatch-based .dockerignore matcher with leading-`**` support."""
    pat = pattern.replace(os.sep, "/").strip("/")
    rel = rel_path.replace(os.sep, "/").strip("/")
    # Translate `**` segments to fnmatch-friendly form.
    if "**" in pat:
        pat = pat.replace("**", "*")
    if fnmatch.fnmatchcase(rel, pat):
        return True
    # Match prefix (so a pattern like `node_modules` ignores
    # `node_modules/foo`).
    for i in range(1, len(rel.split("/")) + 1):
        prefix = "/".join(rel.split("/")[:i])
        if fnmatch.fnmatchcase(prefix, pat):
            return True
    return False


# ---------------------------------------------------------------------------
# Dispatcher table
# ---------------------------------------------------------------------------

_HANDLERS = {
    "ADD":         BuildEngine._do_add,
    "ARG":         BuildEngine._do_arg,
    "CMD":         BuildEngine._do_cmd,
    "COPY":        BuildEngine._do_copy,
    "ENTRYPOINT":  BuildEngine._do_entrypoint,
    "ENV":         BuildEngine._do_env,
    "EXPOSE":      BuildEngine._do_expose,
    "HEALTHCHECK": BuildEngine._do_healthcheck,
    "LABEL":       BuildEngine._do_label,
    "MAINTAINER":  BuildEngine._do_maintainer,
    "RUN":         BuildEngine._do_run,
    "SHELL":       BuildEngine._do_shell,
    "STOPSIGNAL":  BuildEngine._do_stopsignal,
    "USER":        BuildEngine._do_user,
    "VOLUME":      BuildEngine._do_volume,
    "WORKDIR":     BuildEngine._do_workdir,
}


# ---------------------------------------------------------------------------
# Proot-requirement detection (used by command_build before any work)
# ---------------------------------------------------------------------------

def needs_proot(instructions):
    """True if any instruction (or ONBUILD inner) requires proot."""
    for instr in instructions:
        if instr["name"] in PROOT_REQUIRED_INSTRUCTIONS:
            return True
        if instr["name"] == "ONBUILD":
            inner = instr.get("value")
            if isinstance(inner, dict) and inner.get("name") in PROOT_REQUIRED_INSTRUCTIONS:
                return True
    return False
