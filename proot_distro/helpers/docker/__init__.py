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

# Architecture: Pure-Python OCI/Docker registry client, split by concern:
#
#   refs.py        — parse_image_ref, derive_alias, ARCH_TO_DOCKER.
#                    No I/O.
#   cache.py       — manifest/layer cache paths + save/load helpers.
#   media.py       — OCI/Docker media-type constants + canonical_json.
#   transport.py   — User-Agent, AuthStrippingRedirectHandler, the
#                    PD_DOCKER_AUTH token exchange.
#   layers.py      — download_blob + apply_layer (delegates to
#                    helpers/tar_extract for the per-member loop).
#   pull.py        — pull_image: full pipeline from cache check
#                    through manifest fetch, layer download, apply.
#   push.py        — push_image: blob existence probe, upload session,
#                    monolithic PUT, manifest PUT.
#   search.py      — search_images: Docker Hub's repository search,
#                    the one registry API that is Hub-only.

from proot_distro.helpers.docker.refs import (
    ARCH_TO_DOCKER,
    DOCKER_TO_ARCH,
    canonical_ref,
    derive_alias,
    parse_image_ref,
    with_explicit_tag,
)
from proot_distro.helpers.docker.cache import (
    all_layers_cached,
    annotate_manifest_cache,
    image_cache_entry,
    iter_cached_images,
    layer_cache_path,
    load_manifest_cache,
    manifest_cache_path,
    referenced_blob_digests,
    save_manifest_cache,
    validate_digest,
)
from proot_distro.helpers.docker.transport import (
    AuthStrippingRedirectHandler,
    auth_denied_msg,
    auth_note,
    auth_opener,
    env_basic_auth,
    get_auth_token,
    insecure_registry_msg,
    push_denied_msg,
    registry_base_url,
)
from proot_distro.helpers.docker.layers import (
    apply_layer,
    download_blob,
)
from proot_distro.helpers.docker.pull import pull_image
from proot_distro.helpers.docker.push import push_image
from proot_distro.helpers.docker.search import (
    SEARCH_DEFAULT_LIMIT,
    SEARCH_LIMIT_MAX,
    SEARCH_PAGE_MAX,
    search_images,
)

__all__ = (
    "ARCH_TO_DOCKER",
    "AuthStrippingRedirectHandler",
    "DOCKER_TO_ARCH",
    "SEARCH_DEFAULT_LIMIT",
    "SEARCH_LIMIT_MAX",
    "SEARCH_PAGE_MAX",
    "all_layers_cached",
    "annotate_manifest_cache",
    "apply_layer",
    "auth_denied_msg",
    "auth_note",
    "auth_opener",
    "canonical_ref",
    "derive_alias",
    "download_blob",
    "env_basic_auth",
    "get_auth_token",
    "image_cache_entry",
    "insecure_registry_msg",
    "iter_cached_images",
    "layer_cache_path",
    "load_manifest_cache",
    "manifest_cache_path",
    "parse_image_ref",
    "pull_image",
    "push_denied_msg",
    "push_image",
    "referenced_blob_digests",
    "registry_base_url",
    "save_manifest_cache",
    "search_images",
    "validate_digest",
    "with_explicit_tag",
)
