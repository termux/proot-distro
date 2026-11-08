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

"""The public in-process interface to PRoot-Distro.

``ProotDistro`` is a programmatic facade, not a network client. It reads
the same state as the command-line utility and delegates to the same
descriptor-safe inventory and registry implementations.
"""

from typing import Callable, Optional, Tuple

from proot_distro.helpers.docker.cache import iter_cached_images
from proot_distro.helpers.docker.search import (
    SEARCH_DEFAULT_LIMIT,
    SEARCH_LIMIT_MAX,
    search_images,
)
from proot_distro.helpers.download import NETWORK_ERRORS
from proot_distro.paths import installed_container_names, read_container_manifest
from proot_distro.session import active_sessions

from proot_distro.api.errors import (
    InvalidArgumentError,
    NetworkError,
    OperationError,
)
from proot_distro.api.events import EventKind, OperationEvent
from proot_distro.api.models import (
    ContainerInfo,
    ImageInfo,
    SearchHit,
    SearchResult,
    SessionInfo,
)


EventHandler = Callable[[OperationEvent], None]


def _text(value) -> Optional[str]:
    """Return a non-empty string as-is, otherwise None."""
    return value if isinstance(value, str) and value else None


def _container_info(name: str) -> ContainerInfo:
    """Build public metadata for one already-validated container name."""
    try:
        data = read_container_manifest(name)
    except (OSError, ValueError):
        data = {}

    image_ref = _text(data.get("image_ref"))
    architecture = _text(data.get("arch"))
    manifest = data.get("manifest")
    if not isinstance(manifest, dict):
        manifest = {}
    config = manifest.get("config")
    if not isinstance(config, dict):
        config = {}
    image_id = _text(config.get("digest"))

    return ContainerInfo(
        name=name,
        image_reference=image_ref,
        architecture=architecture,
        image_id=image_id,
    )


class ProotDistro:
    """Standardized programmatic access to PRoot-Distro operations.

    The facade does not connect to a service and starts no persistent
    background process. Methods execute in the caller's process unless an
    operation's documented process semantics require a short-lived child.

    ``event_handler`` is optional. When provided, it is called synchronously
    for informational events such as network retries. Exceptions raised by
    the handler propagate to the caller.
    """

    def __init__(self, *, event_handler: Optional[EventHandler] = None) -> None:
        if event_handler is not None and not callable(event_handler):
            raise InvalidArgumentError(
                "event_handler must be callable or None.", operation="initialize"
            )
        self._event_handler = event_handler

    def _event(self, operation: str, kind: EventKind, message: str) -> None:
        if self._event_handler is not None:
            self._event_handler(OperationEvent(operation, kind, message))

    def list_containers(self) -> Tuple[ContainerInfo, ...]:
        """Return installed containers sorted by name."""
        return tuple(_container_info(name) for name in installed_container_names())

    def list_images(self) -> Tuple[ImageInfo, ...]:
        """Return locally cached OCI images in deterministic order."""
        images = []
        for record in iter_cached_images():
            reference = _text(record["image_ref"])
            image_hex = _text(record["image_id"])
            identifier = reference or (image_hex[:12] if image_hex else record["key"])
            images.append(ImageInfo(
                identifier=identifier,
                reference=reference,
                repository=_text(record["repo"]),
                architecture=_text(record["arch"]),
                image_id=_text(record.get("config_digest")),
                size_bytes=record["size"],
                layer_count=len(record["layers"]),
                missing_layers=record["missing"],
                created=_text(record["created"]),
                cached_at=float(record["cached_at"]),
            ))
        return tuple(images)

    def list_sessions(self) -> Tuple[SessionInfo, ...]:
        """Return validated records for every live PRoot-Distro session."""
        return tuple(
            SessionInfo(
                pid=record["pid"],
                container=record["container"],
                kind=record["kind"],
                command=tuple(record["command"]),
                user=record["user"],
                start_time=record["start_time"],
                isolated=record["isolated"],
                minimal=record["minimal"],
                detached=record["detach"],
            )
            for record in active_sessions()
        )

    def search(
        self, query: str, *, limit: int = SEARCH_DEFAULT_LIMIT,
    ) -> SearchResult:
        """Search public Docker Hub repositories.

        Search is the only method in this initial API surface that performs
        network I/O. Registry credentials are never sent to Docker Hub's
        public search endpoint.
        """
        if not isinstance(query, str):
            raise InvalidArgumentError(
                "query must be a string.", operation="search"
            )
        normalized = query.strip()
        if not normalized:
            raise InvalidArgumentError(
                "query must not be empty.", operation="search"
            )
        try:
            normalized.encode("utf-8")
        except UnicodeError:
            raise InvalidArgumentError(
                "query must contain valid UTF-8 text.", operation="search"
            ) from None
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise InvalidArgumentError(
                "limit must be a whole number.", operation="search"
            )
        if limit < 1 or limit > SEARCH_LIMIT_MAX:
            raise InvalidArgumentError(
                f"limit must be between 1 and {SEARCH_LIMIT_MAX}.",
                operation="search",
            )

        def on_retry(message: str) -> None:
            self._event("search", EventKind.RETRY, message)

        try:
            records, total = search_images(
                normalized,
                limit,
                on_retry=on_retry if self._event_handler is not None else None,
            )
        except NETWORK_ERRORS as exc:
            raise NetworkError(str(exc), operation="search") from exc
        except RuntimeError as exc:
            raise OperationError(str(exc), operation="search") from exc

        hits = tuple(SearchHit(
            name=record["name"],
            description=record["description"],
            stars=record["stars"],
            pulls=record["pulls"],
            official=record["official"],
            automated=record["automated"],
        ) for record in records)
        return SearchResult(query=normalized, total=total, hits=hits)


__all__ = ("EventHandler", "ProotDistro")
