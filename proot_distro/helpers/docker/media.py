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

# Architecture: All OCI / Docker mediaType strings used across the
# pull and push pipelines. Keeping them in one module avoids the
# silent divergence that would happen if oci_writer.py and push.py
# each defined their own copies.

import json


def canonical_json(obj) -> bytes:
    """Return canonical (sorted-keys, no-whitespace) JSON bytes.

    Used to hash and sign image manifests / configs. The OCI spec doesn't
    mandate a canonical form, but using one consistently ensures that
    re-hashing on push produces the same digest the build engine wrote
    into the cached manifest.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


OCI_MANIFEST_MEDIA = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG_MEDIA = "application/vnd.oci.image.config.v1+json"
OCI_LAYER_MEDIA = "application/vnd.oci.image.layer.v1.tar+gzip"
OCI_INDEX_MEDIA = "application/vnd.oci.image.index.v1+json"

DOCKER_MANIFEST_LIST_MEDIA = (
    "application/vnd.docker.distribution.manifest.list.v2+json"
)
DOCKER_MANIFEST_MEDIA = (
    "application/vnd.docker.distribution.manifest.v2+json"
)
