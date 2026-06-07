# Dependency-free construction helpers for the proot-distro test suite.
#
# These build the raw artifacts the program ingests — tar archives (incl.
# deliberately malicious members that high-level APIs refuse to create), OCI
# image-layout tarballs, gzipped layer blobs, ELF header stubs and fake
# rootfs/container trees — so tests can drive the real code paths against
# both well-formed and hostile input.

import gzip
import hashlib
import io
import json
import os
import struct
import tarfile

# proot-distro arch name -> ELF e_machine value.
_ELF_MACHINE = {
    "i686": 3,       # EM_386
    "arm": 40,       # EM_ARM
    "x86_64": 62,    # EM_X86_64
    "aarch64": 183,  # EM_AARCH64
    "riscv64": 243,  # EM_RISCV
}


# ---------------------------------------------------------------------------
# ELF stubs
# ---------------------------------------------------------------------------

def elf_bytes(arch="x86_64", *, little_endian=True, truncated=False,
              valid_magic=True):
    """Return a 20-byte ELF identification block for *arch*.

    arch.py::_elf_arch reads exactly 20 bytes and pulls e_machine from a
    2-byte field at offset 18, honouring EI_DATA (byte 5) for endianness.
    """
    buf = bytearray(20)
    buf[0:4] = b"\x7fELF" if valid_magic else b"\x7fbad"
    buf[4] = 2  # EI_CLASS = ELFCLASS64 (value irrelevant to detection)
    buf[5] = 1 if little_endian else 2  # EI_DATA
    fmt = "<H" if little_endian else ">H"
    struct.pack_into(fmt, buf, 18, _ELF_MACHINE.get(arch, 0))
    data = bytes(buf)
    return data[:10] if truncated else data


def write_elf(path, arch="x86_64", **kw):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(elf_bytes(arch, **kw))


# ---------------------------------------------------------------------------
# Tar construction (arbitrary, including hostile, members)
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "file": tarfile.REGTYPE,
    "dir": tarfile.DIRTYPE,
    "symlink": tarfile.SYMTYPE,
    "hardlink": tarfile.LNKTYPE,
    "fifo": tarfile.FIFOTYPE,
    "chr": tarfile.CHRTYPE,
    "blk": tarfile.BLKTYPE,
}


def _add_members(tf, members):
    for m in members:
        info = tarfile.TarInfo(m["name"])
        kind = m.get("type", "file")
        info.type = _TYPE_MAP[kind]
        info.mode = m.get("mode", 0o755 if kind == "dir" else 0o644)
        info.mtime = m.get("mtime", 0)
        info.uid = m.get("uid", 0)
        info.gid = m.get("gid", 0)
        info.uname = m.get("uname", "")
        info.gname = m.get("gname", "")
        if kind in ("symlink", "hardlink"):
            info.linkname = m["linkname"]
            tf.addfile(info)
        elif kind in ("chr", "blk"):
            info.devmajor = m.get("devmajor", 1)
            info.devminor = m.get("devminor", 3)
            tf.addfile(info)
        elif kind == "file":
            data = m.get("data", b"")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        else:  # dir, fifo
            tf.addfile(info)


def make_tar_bytes(members, compression=""):
    bio = io.BytesIO()
    mode = f"w:{compression}" if compression else "w"
    with tarfile.open(fileobj=bio, mode=mode) as tf:
        _add_members(tf, members)
    return bio.getvalue()


def make_tar(path, members, compression=""):
    """Write a tar archive built from *members* specs.

    Each member dict: {"name", "type": file|dir|symlink|hardlink|fifo|chr|blk,
    "data": bytes, "linkname": str, "mode", "mtime", "uid", "gid"}.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = make_tar_bytes(members, compression)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def rootfs_members(extra=()):
    """A plausible plain-rootfs member set (etc/usr/... at the top level)."""
    members = [
        {"name": "etc/", "type": "dir"},
        {"name": "etc/hostname", "type": "file", "data": b"guest\n"},
        {"name": "usr/", "type": "dir"},
        {"name": "usr/bin/", "type": "dir"},
        {"name": "bin/", "type": "dir"},
        {"name": "bin/sh", "type": "file", "data": elf_bytes("x86_64"),
         "mode": 0o755},
    ]
    members.extend(extra)
    return members


# ---------------------------------------------------------------------------
# Layer blobs (gzipped tar) + OCI image-layout archives
# ---------------------------------------------------------------------------

def make_layer_blob(members):
    """Return (gzip_bytes, digest, diff_id) for an OCI/Docker layer."""
    raw = make_tar_bytes(members)
    gz = gzip.compress(raw, mtime=0)
    digest = "sha256:" + hashlib.sha256(gz).hexdigest()
    diff_id = "sha256:" + hashlib.sha256(raw).hexdigest()
    return gz, digest, diff_id


def seed_cached_layer(members):
    """Write a layer blob into LAYER_CACHE_DIR; return (digest, size, diff_id)."""
    from proot_distro.helpers.docker.cache import layer_cache_path
    gz, digest, diff_id = make_layer_blob(members)
    path = layer_cache_path(digest)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(gz)
    return digest, len(gz), diff_id


def _add_bytes(tf, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    info.mtime = 0
    tf.addfile(info, io.BytesIO(data))


def make_oci_archive(path, layers, *, arch="x86_64", image_ref="test:latest",
                     image_config=None, bad_index_digest=None):
    """Build a valid OCI image-layout tarball consumable by `install`.

    *layers* is a list of member-spec lists (one per layer). When
    *bad_index_digest* is given it replaces the index manifest descriptor's
    digest (used to prove crafted digests can't escape the blob path).
    Returns {"manifest", "config", "image_ref", "layer_digests"}.
    """
    from proot_distro.helpers.docker.refs import ARCH_TO_DOCKER
    from proot_distro.helpers.docker.media import (
        canonical_json, OCI_MANIFEST_MEDIA, OCI_CONFIG_MEDIA,
        OCI_LAYER_MEDIA, OCI_INDEX_MEDIA,
    )

    docker_arch = ARCH_TO_DOCKER.get(arch, (arch, ""))[0]
    blobs = {}            # hex -> bytes
    layer_descs = []
    diff_ids = []
    layer_digests = []
    for members in layers:
        gz, digest, diff_id = make_layer_blob(members)
        hexd = digest.split(":", 1)[1]
        blobs[hexd] = gz
        layer_descs.append(
            {"mediaType": OCI_LAYER_MEDIA, "digest": digest, "size": len(gz)}
        )
        diff_ids.append(diff_id)
        layer_digests.append(digest)

    config = {
        "architecture": docker_arch,
        "os": "linux",
        "config": (image_config or {}),
        "rootfs": {"type": "layers", "diff_ids": diff_ids},
        "history": [{"created": "1970-01-01T00:00:00Z"} for _ in layers],
    }
    config_bytes = canonical_json(config)
    config_hex = hashlib.sha256(config_bytes).hexdigest()
    blobs[config_hex] = config_bytes

    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA,
        "config": {
            "mediaType": OCI_CONFIG_MEDIA,
            "digest": "sha256:" + config_hex,
            "size": len(config_bytes),
        },
        "layers": layer_descs,
    }
    manifest_bytes = canonical_json(manifest)
    manifest_hex = hashlib.sha256(manifest_bytes).hexdigest()
    blobs[manifest_hex] = manifest_bytes

    index = {
        "schemaVersion": 2,
        "mediaType": OCI_INDEX_MEDIA,
        "manifests": [{
            "mediaType": OCI_MANIFEST_MEDIA,
            "digest": bad_index_digest or ("sha256:" + manifest_hex),
            "size": len(manifest_bytes),
            "annotations": {"org.opencontainers.image.ref.name": image_ref},
        }],
    }

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with tarfile.open(path, "w") as tf:
        # oci-layout first so the install probe detects the OCI format on the
        # first member it reads.
        _add_bytes(tf, "oci-layout",
                   canonical_json({"imageLayoutVersion": "1.0.0"}))
        _add_bytes(tf, "index.json", canonical_json(index))
        for hexd, data in blobs.items():
            _add_bytes(tf, "blobs/sha256/" + hexd, data)

    return {
        "manifest": manifest,
        "config": config,
        "image_ref": image_ref,
        "layer_digests": layer_digests,
    }


# ---------------------------------------------------------------------------
# Fake rootfs / container trees
# ---------------------------------------------------------------------------

_PASSWD = (
    "root:x:0:0:root:/root:/bin/sh\n"
    "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n"
    "tester:x:1000:1000:Tester:/home/tester:/bin/bash\n"
)
_GROUP = (
    "root:x:0:\n"
    "daemon:x:1:\n"
    "staff:x:50:tester\n"
    "tester:x:1000:\n"
)


def make_rootfs(root, *, arch="x86_64", with_passwd=True, shell="/bin/sh"):
    """Populate *root* with a minimal but realistic rootfs."""
    os.makedirs(root, exist_ok=True)
    write_elf(os.path.join(root, shell.lstrip("/")), arch)
    etc = os.path.join(root, "etc")
    os.makedirs(os.path.join(etc, "profile.d"), exist_ok=True)
    os.makedirs(os.path.join(root, "root"), exist_ok=True)
    os.makedirs(os.path.join(root, "home", "tester"), exist_ok=True)
    if with_passwd:
        with open(os.path.join(etc, "passwd"), "w") as fh:
            fh.write(_PASSWD)
        with open(os.path.join(etc, "group"), "w") as fh:
            fh.write(_GROUP)
    return root


def make_container(name, *, arch="x86_64", manifest=None, with_passwd=True,
                   files=None):
    """Create containers/<name>/{rootfs,manifest.json}; return its dir."""
    from proot_distro.paths import (
        container_dir, container_manifest, container_rootfs,
    )
    rootfs = container_rootfs(name)
    make_rootfs(rootfs, arch=arch, with_passwd=with_passwd)
    for rel, data in (files or {}).items():
        full = os.path.join(rootfs, rel.lstrip("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data if isinstance(data, bytes) else data.encode())
    if manifest is not None:
        with open(container_manifest(name), "w") as fh:
            json.dump(manifest, fh)
    return container_dir(name)


def simple_image_manifest(image_ref="test:latest", arch="x86_64",
                          entrypoint=None, cmd=None, env=None, workdir=None):
    """A containers/<name>/manifest.json payload as install would write."""
    config = {}
    if entrypoint is not None:
        config["Entrypoint"] = entrypoint
    if cmd is not None:
        config["Cmd"] = cmd
    if env is not None:
        config["Env"] = env
    if workdir is not None:
        config["WorkingDir"] = workdir
    return {
        "image_ref": image_ref,
        "arch": arch,
        "manifest": {"schemaVersion": 2, "layers": []},
        "image_config": {"config": config},
    }


# ---------------------------------------------------------------------------
# Filesystem assertions
# ---------------------------------------------------------------------------

def tree_snapshot(root):
    """Return {relpath: ('file', bytes) | ('dir',) | ('link', target)}."""
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            full = os.path.join(dirpath, d)
            rel = os.path.relpath(full, root)
            if os.path.islink(full):
                snap[rel] = ("link", os.readlink(full))
            else:
                snap[rel] = ("dir",)
        for f in filenames:
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root)
            if os.path.islink(full):
                snap[rel] = ("link", os.readlink(full))
            else:
                with open(full, "rb") as fh:
                    snap[rel] = ("file", fh.read())
    return snap
