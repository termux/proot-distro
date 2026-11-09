#!/usr/bin/env python3
#
# Proot-Distro - manage proot containers.
#
# Created by Sylirre <sylirre@termux.dev> for Termux project.
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

"""Build an image with events, cancellation, credentials, and custom storage.

At least one ``--tag`` is required. OCI archives can be exported with repeated
``--output`` options, and ``--install-as`` also installs the result as a
container. ``--push`` explicitly publishes every requested tag after a
successful build.

Examples::

    python samples/build_image.py ./context --tag example/app:dev
    python samples/build_image.py ./context --tag example/app:1 \
        --build-arg VERSION=1 --output app.oci.tar --install-as app-test
    python samples/build_image.py ./context --tag registry.example/app:1 \
        --push --cancel-after 300

Optional registry credentials are loaded only when all of these variables are
set: ``PD_SAMPLE_REGISTRY``, ``PD_SAMPLE_REGISTRY_USERNAME``, and
``PD_SAMPLE_REGISTRY_TOKEN``. The provider returns the secret only for an exact
registry-name match. Secrets are not accepted as command-line arguments.

Supplying ``--runtime-dir`` and ``--cache-dir`` creates an isolated
``RuntimeConfig``. Both are required together. Without them, the script uses
the same state as the PRoot-Distro CLI.

Cancellation is cooperative. If its deadline expires, an operation can have
already changed state or written partial stream output. Path outputs retain the
API's atomic publication behavior.
"""

import argparse
import math
import os
from pathlib import Path
import sys
import threading
from typing import Dict, Optional, Sequence

# Make direct execution from a source checkout use that exact checkout. An
# installed copy needs no adjustment because these samples are repository files.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proot_distro.api import (
    CredentialRequest,
    EventKind,
    OperationCancelledError,
    OperationEvent,
    ProotDistro,
    ProotDistroError,
    RegistryCredentials,
    RuntimeConfig,
)


def show_event(event: OperationEvent) -> None:
    """Render structured events without parsing their display messages."""
    context = "/".join(value for value in (event.operation, event.phase) if value)
    if event.kind is EventKind.PROGRESS and event.current is not None:
        progress = str(event.current)
        if event.total is not None:
            progress += f"/{event.total}"
        if event.unit:
            progress += f" {event.unit}"
        print(f"[{context}] {progress}: {event.message}", flush=True)
    else:
        print(f"[{context}] {event.kind.value}: {event.message}", flush=True)


def credentials_from_environment():
    """Create a one-registry credential provider, or return ``None``."""
    registry = os.environ.get("PD_SAMPLE_REGISTRY")
    username = os.environ.get("PD_SAMPLE_REGISTRY_USERNAME")
    token = os.environ.get("PD_SAMPLE_REGISTRY_TOKEN")
    configured = (registry, username, token)
    if not any(configured):
        return None
    if not all(configured):
        raise ValueError(
            "PD_SAMPLE_REGISTRY, PD_SAMPLE_REGISTRY_USERNAME, and "
            "PD_SAMPLE_REGISTRY_TOKEN must be set together"
        )

    credentials = RegistryCredentials(username, token)

    def provide(request: CredentialRequest) -> Optional[RegistryCredentials]:
        if request.registry == registry:
            return credentials
        return None

    return provide


def _build_args(values: Sequence[str]) -> Dict[str, str]:
    result = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key:
            raise ValueError(f"build argument must use NAME=VALUE syntax: {value!r}")
        if key in result:
            raise ValueError(f"build argument was specified more than once: {key}")
        result[key] = item
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("context", type=Path, help="Dockerfile build context")
    parser.add_argument(
        "--tag",
        action="append",
        required=True,
        help="image tag; repeat to create multiple tags",
    )
    parser.add_argument("--dockerfile", type=Path, help="alternate Dockerfile")
    parser.add_argument(
        "--build-arg",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Dockerfile build argument; repeat as needed",
    )
    parser.add_argument(
        "--output",
        action="append",
        type=Path,
        default=[],
        help="new OCI archive path; repeat as needed",
    )
    parser.add_argument("--install-as", help="also install this container name")
    parser.add_argument("--architecture", help="target image architecture")
    parser.add_argument("--target", help="target Dockerfile stage")
    parser.add_argument("--no-cache", action="store_true", help="disable build cache")
    parser.add_argument(
        "--push",
        action="store_true",
        help="push every tag after a successful build",
    )
    parser.add_argument(
        "--allow-insecure",
        action="store_true",
        help="allow plain HTTP for an explicitly configured registry",
    )
    parser.add_argument("--runtime-dir", type=Path, help="isolated runtime root")
    parser.add_argument("--cache-dir", type=Path, help="isolated cache root")
    parser.add_argument("--proot-path", type=Path, help="specific proot executable")
    parser.add_argument(
        "--cancel-after",
        type=float,
        help="cooperatively cancel after this many seconds",
    )
    return parser


def _config(args: argparse.Namespace) -> Optional[RuntimeConfig]:
    if (args.runtime_dir is None) != (args.cache_dir is None):
        raise ValueError("--runtime-dir and --cache-dir must be supplied together")
    if args.runtime_dir is not None:
        return RuntimeConfig(args.runtime_dir, args.cache_dir, args.proot_path)
    if args.proot_path is not None:
        default = RuntimeConfig.default()
        return RuntimeConfig(default.runtime_dir, default.cache_dir, args.proot_path)
    return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Build and optionally push, returning a process-style status code."""
    args = _parser().parse_args(argv)
    if args.cancel_after is not None and (
        not math.isfinite(args.cancel_after) or args.cancel_after < 0
    ):
        print("--cancel-after must be finite and not negative")
        return 2

    try:
        build_args = _build_args(args.build_arg)
        config = _config(args)
        provider = credentials_from_environment()
        pd = ProotDistro(
            config,
            event_handler=show_event,
            credential_provider=provider,
        )
    except (ProotDistroError, ValueError) as exc:
        print(f"configuration failed: {exc}")
        return 2

    cancel = threading.Event()
    timer = None
    if args.cancel_after is not None:
        timer = threading.Timer(args.cancel_after, cancel.set)
        timer.daemon = True
        timer.start()

    try:
        result = pd.build(
            args.context,
            dockerfile=args.dockerfile,
            tags=args.tag,
            build_args=build_args,
            architecture=args.architecture,
            target=args.target,
            outputs=args.output,
            install_as=args.install_as,
            no_cache=args.no_cache,
            cancel_event=cancel,
        )
        for image in result.images:
            print(
                f"Built {image.reference or image.identifier} "
                f"({image.size_bytes} bytes, {image.layer_count} layers)."
            )
        for output in result.outputs:
            if output is not None:
                print(f"Exported {output}.")
        if result.installed_container is not None:
            print(f"Installed {result.installed_container.name}.")

        if args.push:
            for tag in args.tag:
                pushed = pd.push(
                    tag,
                    architecture=args.architecture,
                    allow_insecure=args.allow_insecure,
                    cancel_event=cancel,
                )
                digest = pushed.manifest_digest or "digest unavailable"
                print(
                    f"Pushed {pushed.registry}/{pushed.repository}:{pushed.tag} "
                    f"as {digest}; uploaded {pushed.bytes_uploaded} bytes."
                )
    except OperationCancelledError as exc:
        print(f"{exc.operation or 'operation'} cancelled: {exc}")
        return 2
    except ProotDistroError as exc:
        print(f"{exc.operation or 'operation'} failed: {exc}")
        return 1
    except KeyboardInterrupt:
        print("Interrupted after PRoot-Distro cleanup.")
        return 130
    finally:
        if timer is not None:
            timer.cancel()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
