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
#   refs.py        — parse_reference (Docker's reference grammar),
#                    parse_image_ref, derive_alias, ARCH_TO_DOCKER.
#                    No I/O.
#   cache.py       — manifest/layer cache paths + save/load helpers,
#                    and the digest checks every blob consumer runs
#                    before trusting a cached file's name.
#   media.py       — OCI/Docker media-type constants + canonical_json.
#   transport.py   — User-Agent, AuthStrippingRedirectHandler, the
#                    PD_DOCKER_AUTH check and token exchange.
#   layers.py      — download_blob + apply_layer (delegates to
#                    helpers/tar_extract for the per-member loop).
#   pull.py        — pull_image: full pipeline from cache check
#                    through manifest fetch, layer download, apply.
#   search.py      — search_images: Docker Hub's repository search,
#                    the one registry API that is Hub-only.

from proot_distro.lazy_exports import lazy_exports


# Each name and the submodule defining it, imported on first use. Any
# `proot_distro.helpers.docker.<module>` import runs this file, and the
# reference parser alone must not bring in the registry client, the
# layer cache and the TLS stack with it.
_EXPORTS = {
    "ARCH_TO_DOCKER": "refs",
    "AuthStrippingRedirectHandler": "transport",
    "DOCKER_TO_ARCH": "refs",
    "InvalidReference": "refs",
    "Reference": "refs",
    "SEARCH_DEFAULT_LIMIT": "search",
    "SEARCH_LIMIT_MAX": "search",
    "SEARCH_PAGE_MAX": "search",
    "annotate_manifest_cache": "cache",
    "apply_layer": "layers",
    "data_matches_digest": "cache",
    "auth_denied_msg": "transport",
    "auth_note": "transport",
    "auth_opener": "transport",
    "canonical_ref": "refs",
    "credentials_rejected_msg": "transport",
    "derive_alias": "refs",
    "download_blob": "layers",
    "env_basic_auth": "transport",
    "get_auth_token": "transport",
    "image_cache_entry": "cache",
    "insecure_for": "transport",
    "insecure_registry_msg": "transport",
    "iter_cached_images": "cache",
    "layer_cache_name": "cache",
    "blob_present": "cache",
    "layer_cache_path": "cache",
    "load_manifest_cache": "cache",
    "manifest_cache_path": "cache",
    "manifest_config_digest": "cache",
    "open_layer_cache_dir": "cache",
    "open_manifest_cache_dir": "cache",
    "parse_image_ref": "refs",
    "parse_reference": "refs",
    "parse_tag_reference": "refs",
    "pull_image": "pull",
    "push_denied_msg": "transport",
    "referenced_blob_digests": "cache",
    "registry_base_url": "transport",
    "same_auth_origin": "transport",
    "require_data_digest": "cache",
    "open_required_layer": "cache",
    "save_manifest_cache": "cache",
    "search_images": "search",
    "split_digest": "cache",
    "validate_digest": "cache",
    "open_verified_layer": "cache",
    "with_explicit_tag": "refs",
}

__all__ = tuple(_EXPORTS)

__getattr__, __dir__ = lazy_exports(__name__, _EXPORTS)
