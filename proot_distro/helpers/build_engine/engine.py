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

# Architecture: The stateful interpreter at the core of `proot-distro
# build`. Walks the parsed instructions, dispatches each one to a
# handler (metadata-only handlers live in handlers.py, the RUN
# handler in run_step.py, COPY/ADD in copy_step.py), tracks the
# evolving image config + per-stage state in Stage instances, and
# appends an OCI history entry per dispatched instruction so the
# image_config["history"] array stays in sync with rootfs.diff_ids.

import json
import os
import re

from proot_distro.arch import get_device_cpu_arch
from proot_distro.message import C, log_info
from proot_distro.helpers.docker import (
    ARCH_TO_DOCKER, apply_layer, layer_cache_path, pull_image,
)
from proot_distro.helpers.dockerfile import expand_vars
from proot_distro.helpers.rootfs import write_hosts, write_resolv_conf
from proot_distro.helpers.build_engine.constants import (
    EXPANDS_VARS, PREDEFINED_ARGS,
)
from proot_distro.helpers.build_engine.dockerignore import load_dockerignore
from proot_distro.helpers.build_engine.errors import BuildError
from proot_distro.helpers.build_engine.handlers import HANDLERS, do_onbuild
from proot_distro.helpers.build_engine.parsing import split_arg
from proot_distro.helpers.build_engine.stage import Stage


_FROM_RE = re.compile(r"^\s*(\S+)(?:\s+AS\s+(\S+))?\s*$", re.IGNORECASE)
_FROM_AS_RE = re.compile(r"\s+AS\s+(\S+)\s*$", re.IGNORECASE)


class BuildEngine:
    """Walks a parsed Dockerfile and produces an OCI image in-place.

    The engine owns the cross-stage state (the global ARG scope, the
    map of named stages so COPY --from= can resolve them, the current
    Stage). Each instruction is dispatched to a handler module
    (handlers.py / copy_step.py / run_step.py) that mutates either
    the engine, the current stage, or the rootfs on disk.
    """

    def __init__(self,
                 build_dir,
                 tmp_root,
                 target_arch_pd,
                 user_build_args,
                 target_stage,
                 verbose,
                 quiet,
                 no_cache,
                 emulator):
        self.build_dir = os.path.abspath(build_dir)
        self.tmp_root = tmp_root
        self.target_arch_pd = target_arch_pd
        self.target_arch_docker = ARCH_TO_DOCKER.get(
            target_arch_pd, (target_arch_pd, "")
        )[0]
        host_arch = get_device_cpu_arch()
        self.host_arch_docker = ARCH_TO_DOCKER.get(
            host_arch, (host_arch, "")
        )[0]
        self.user_build_args = dict(user_build_args)
        self.target_stage = target_stage
        self.verbose = verbose
        self.quiet = quiet
        self.no_cache = no_cache
        self.emulator = emulator
        self.stages = {}        # name -> Stage
        self.stages_by_idx = []
        self.current = None
        self.global_args = {}   # ARG declared before first FROM
        self.declared_global = set()
        self.ignore_patterns = load_dockerignore(self.build_dir)
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
                m = _FROM_AS_RE.search(value)
                if m:
                    named_stages.append(m.group(1))
            elif instr["name"] == "ARG" and not seen_from:
                key, default = split_arg(instr["value"])
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
            # During pre-scan we only have CLI-supplied build args for
            # global keys declared so far. Pre-scan happens before the
            # user-arg merge; resolve from the raw map.
            scope.update(self.user_build_args)
        else:
            scope.update(self.global_args)
        self._set_arch_defaults(scope)
        return expand_vars(value, scope)

    def _set_arch_defaults(self, scope: dict) -> None:
        """Populate the TARGET*/BUILD* arch annotations (Docker defaults)."""
        scope.setdefault("TARGETARCH", self.target_arch_docker)
        scope.setdefault("BUILDARCH", self.host_arch_docker)
        scope.setdefault("TARGETOS", "linux")
        scope.setdefault("BUILDOS", "linux")
        scope.setdefault("TARGETPLATFORM", f"linux/{self.target_arch_docker}")
        scope.setdefault("BUILDPLATFORM", f"linux/{self.host_arch_docker}")

    # ----- step banner -----------------------------------------------------

    def _announce(self, instr):
        if self.quiet:
            return
        raw = instr.get("raw", "")
        if len(raw) > 120:
            raw = raw[:117] + "..."
        parts = raw.split(None, 1)
        if len(parts) == 2:
            rendered = f"{C['YELLOW']}{parts[0]}{C['RST']} {parts[1]}"
        elif parts:
            rendered = f"{C['YELLOW']}{parts[0]}{C['RST']}"
        else:
            rendered = ""
        log_info(f"Step {self._step_no}/{self._step_total}: "
                 f"{C['RST']}{rendered}")

    def log(self, text):
        """Emit *text* via log_info() unless `--quiet` is in effect."""
        if self.quiet:
            return
        log_info(text)

    # ----- dispatcher + history -------------------------------------------

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
            do_onbuild(self, instr)
            self._record_history(instr, layer_added=False)
            return
        if name in EXPANDS_VARS and not instr["exec_form"]:
            instr = self._expand_instruction(instr)

        handler = HANDLERS.get(name)
        if handler is None:
            raise BuildError(
                f"Unsupported instruction '{name}' at line {instr['lineno']}."
            )
        self._run_with_history(handler, instr)

    def _run_with_history(self, handler, instr):
        """Run handler, then append a history entry for the instruction.

        Whether the entry is marked empty_layer depends on whether the
        handler grew `stage.layers`. The OCI image-config spec requires
        the count of non-empty-layer history entries to equal
        len(rootfs.diff_ids); registries (notably Docker Hub) render
        this array in their "Image Layers" UI, so an out-of-date
        history makes built layers invisible.
        """
        layers_before = len(self.current.layers)
        handler(self, instr)
        layer_added = len(self.current.layers) > layers_before
        self._record_history(instr, layer_added=layer_added)

    def _record_history(self, instr, layer_added):
        """Append one entry to image_config["history"] for `instr`.

        `created_by` is the raw Dockerfile line (what Docker Hub
        displays under each step); `created` is fixed to the epoch so
        the image config is reproducible across builds. Entries that
        did not produce a filesystem layer carry empty_layer=true, so
        the count of non-empty entries always equals len(diff_ids).
        """
        entry = {
            "created": "1970-01-01T00:00:00Z",
            "created_by": instr.get("raw") or instr["name"],
        }
        if not layer_added:
            entry["empty_layer"] = True
        cfg = self.current.image_config
        cfg.setdefault("history", []).append(entry)

    def _expand_instruction(self, instr):
        """Return a copy of instr with its `value` variable-expanded."""
        env = self.expansion_scope()
        new = dict(instr)
        value = instr.get("value", "")
        if isinstance(value, str):
            new["value"] = expand_vars(value, env)
        new["flags"] = {
            k: expand_vars(v, env) if isinstance(v, str) else v
            for k, v in instr.get("flags", {}).items()
        }
        return new

    def expansion_scope(self):
        """Variable scope for `${VAR}` expansion inside the current stage.

        Composed in increasing precedence: PREDEFINED_ARGS from the
        host env, the build-time arch annotations (TARGETARCH /
        BUILDARCH / TARGET/BUILDPLATFORM / TARGET/BUILDOS), declared
        ARGs in this stage, and finally ENVs (which win over ARGs by
        Docker semantics).
        """
        scope = {}
        for k in PREDEFINED_ARGS:
            v = os.environ.get(k, "")
            if v:
                scope[k] = v
        self._set_arch_defaults(scope)
        # ARG values declared in this stage so far.
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
        m = _FROM_RE.match(value)
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

        if base_ref.lower() == "scratch":
            stage.image_config = {"config": {}}
        elif base_ref in self.stages:
            self._inherit_from_stage(stage, self.stages[base_ref])
        else:
            self._pull_base_image(stage, base_ref)

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
        # again in this stage). Docker semantics: they start unset and
        # become visible only after a bare `ARG NAME` re-declares them.
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
            from proot_distro.helpers.dockerfile import parse_dockerfile
            for trig in base_onbuild:
                _, trig_instrs = parse_dockerfile(trig + "\n")
                for ti in trig_instrs:
                    self._step_no += 1
                    self._announce(ti)
                    if ti["name"] in EXPANDS_VARS and not ti["exec_form"]:
                        ti = self._expand_instruction(ti)
                    h = HANDLERS.get(ti["name"])
                    if h is None:
                        raise BuildError(
                            f"ONBUILD trigger uses unsupported "
                            f"instruction '{ti['name']}'."
                        )
                    self._run_with_history(h, ti)

    def _inherit_from_stage(self, new_stage, parent):
        """Apply parent's layers to new_stage.rootfs_dir; copy config.

        The deep-copy via JSON round-trip carries the parent's
        `history` array along with the rest of image_config, so the
        new stage starts with the inherited entries and subsequent
        instructions append to the same list.
        """
        new_stage.image_config = json.loads(
            json.dumps(parent.image_config)
        )
        for layer in parent.layers:
            cache_path = layer_cache_path(layer["digest"])
            if not os.path.isfile(cache_path):
                raise BuildError(
                    f"Layer {layer['digest']} of stage "
                    f"'{parent.name or parent.index}' is missing from "
                    f"the cache."
                )
            apply_layer(cache_path, new_stage.rootfs_dir)
        new_stage.layers = list(parent.layers)
        new_stage.parent_layer_digest = parent.parent_layer_digest

    def _pull_base_image(self, stage, image_ref):
        """Use helpers.docker.pull_image to populate the stage rootfs."""
        log_info(f"Pulling base image '{image_ref}' ({self.target_arch_pd})...")
        try:
            meta = pull_image(image_ref, stage.rootfs_dir,
                              self.target_arch_pd)
        except RuntimeError as exc:
            raise BuildError(f"FROM {image_ref}: {exc}") from exc

        stage.image_config = meta.get("image_config") or {"config": {}}
        manifest = meta.get("manifest") or {}
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
