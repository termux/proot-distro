# Integrity tests for the OCI layer cache: a blob is used on the strength
# of its content, never its file name.
#
# The cache is writable by more than download_blob(). On Termux it lives
# under the $TERMUX_PREFIX that every non-isolated container gets bound
# read-write, and `install <archive|URL>` deposits blobs under digests a
# remote party chose. A cached blob is therefore re-hashed by every
# consumer before it is applied, pushed, or packaged — these tests plant
# mismatched content and prove nothing downstream believes it.

import hashlib
import io
import json
import os
import shutil
import tarfile
import urllib.error
from types import SimpleNamespace

import pytest

from _builders import install_local_into, pull_image_into
from proot_distro.helpers import oci_writer
from proot_distro.helpers.build_engine import run_step
from proot_distro.helpers.build_engine.engine import BuildEngine
from proot_distro.helpers.build_engine.errors import BuildError
from proot_distro.helpers.docker import pull as pull_mod
from proot_distro.helpers.docker import push as push_mod
from proot_distro.constants import LAYER_CACHE_DIR
from proot_distro.helpers.docker.cache import (
    blob_present, layer_cache_name, layer_cache_path,
    open_required_layer, open_verified_layer, save_manifest_cache,
)
from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA


BENIGN = [{"name": "etc/", "type": "dir"},
          {"name": "etc/os-release", "type": "file", "data": b"ID=real\n"}]
EVIL = [{"name": "etc/", "type": "dir"},
        {"name": "etc/PWNED", "type": "file", "data": b"attacker\n"}]


def _poison(digest, builders, members=EVIL):
    """Replace the cached blob for *digest* with a different layer."""
    gz, _d, _i = builders.make_layer_blob(members)
    path = layer_cache_path(digest)
    with open(path, "wb") as fh:
        fh.write(gz)
    return gz


def _seed_image(builders, image_ref="x:1", arch="x86_64", members=BENIGN):
    """Cache a one-layer manifest plus its (intact) blob."""
    digest, size, diff_id = builders.seed_cached_layer(members)
    manifest = {
        "schemaVersion": 2,
        "layers": [{"digest": digest, "size": size,
                    "mediaType": OCI_LAYER_MEDIA}],
    }
    save_manifest_cache(image_ref, arch, manifest, "library/x",
                        {"rootfs": {"diff_ids": [diff_id]}})
    return digest


# ---------------------------------------------------------------------------
# pull: a poisoned cache entry is discarded, not applied
# ---------------------------------------------------------------------------

def test_pull_refetches_a_poisoned_cached_layer(tmp_path, builders,
                                                monkeypatch):
    digest = _seed_image(builders)
    good = open(layer_cache_path(digest), "rb").read()
    _poison(digest, builders)

    monkeypatch.setattr(pull_mod, "get_auth_token",
                        lambda repo, reg, insecure=False: ("TKN", "https://r"))

    called = []

    def fake_download(repo, dg, token, base, insecure=False):
        called.append(dg)
        with open(layer_cache_path(dg), "wb") as fh:
            fh.write(good)
        # download_blob hands back an open descriptor, not a name.
        return os.open(layer_cache_path(dg), os.O_RDONLY)

    monkeypatch.setattr(pull_mod, "download_blob", fake_download)

    root = tmp_path / "rootfs"
    root.mkdir()
    pull_image_into("x:1", str(root), "x86_64")

    assert called == [digest], "the poisoned blob was not reused"
    assert (root / "etc" / "os-release").read_bytes() == b"ID=real\n"
    assert not (root / "etc" / "PWNED").exists()


def _swap_between_check_and_apply(monkeypatch, swap):
    """Run *swap* just before the first apply, as a live guest could."""
    real_apply = pull_mod.apply_layer
    done = []

    def apply_after_swap(layer_fd, rootfs_dir, **kw):
        if not done:
            done.append(swap())
        return real_apply(layer_fd, rootfs_dir, **kw)

    monkeypatch.setattr(pull_mod, "apply_layer", apply_after_swap)
    return done


def test_pull_applies_the_inode_it_hashed_not_the_name(tmp_path, builders,
                                                       monkeypatch):
    """The blob's *name* is re-pointed after verification, before the apply.

    Verification used to hand back a path, so the extraction opened the
    name a second time and got whatever was standing there by then. On
    Termux that is a live possibility rather than a thought experiment:
    the layer cache sits under the $TERMUX_PREFIX bound read-write into
    every non-isolated container, so a session can be running while an
    install proceeds. Carrying the descriptor settles which inode is
    read, so the good layer still lands and the pull succeeds.
    """
    digest = _seed_image(builders)
    path = layer_cache_path(digest)

    def repoint():
        evil, _d, _i = builders.make_layer_blob(EVIL)
        os.unlink(path)                     # a new inode under the old name
        with open(path, "wb") as fh:
            fh.write(evil)
        return True

    done = _swap_between_check_and_apply(monkeypatch, repoint)

    root = tmp_path / "rootfs"
    root.mkdir()
    pull_image_into("x:1", str(root), "x86_64")

    assert done, "the test did not exercise the swap"
    assert (root / "etc" / "os-release").read_bytes() == b"ID=real\n"
    assert not (root / "etc" / "PWNED").exists()


def test_pull_refuses_a_blob_rewritten_under_its_own_descriptor(
        tmp_path, builders, monkeypatch):
    """The same inode is truncated and rewritten in place.

    A descriptor cannot help here — it names the inode being rewritten —
    so the guarantee has to come from hashing the bytes as they are
    consumed. The attacker's layer is applied and then the digest check
    at the end of the stream refuses it, which is why every caller
    discards the tree on error rather than keeping a partial rootfs.
    """
    digest = _seed_image(builders)

    # _poison opens the existing file "wb": same inode, new content.
    done = _swap_between_check_and_apply(
        monkeypatch, lambda: _poison(digest, builders))

    root = tmp_path / "rootfs"
    root.mkdir()
    with pytest.raises(RuntimeError, match="does not match its digest"):
        pull_image_into("x:1", str(root), "x86_64")

    assert done, "the test did not exercise the swap"


def _open_fds():
    return set(os.listdir("/proc/self/fd"))


def test_pull_leaves_no_open_descriptors(tmp_path, builders):
    # The verified blobs are held open for the whole pull, so the cleanup
    # has to be exact -- a leak here would accumulate one fd per layer per
    # install.
    _seed_image(builders)
    root = tmp_path / "rootfs"
    root.mkdir()

    before = _open_fds()
    pull_image_into("x:1", str(root), "x86_64")
    assert len(_open_fds() - before) == 0


def test_pull_closes_descriptors_when_a_layer_fails(tmp_path, builders,
                                                    monkeypatch):
    digest = _seed_image(builders)
    monkeypatch.setattr(pull_mod, "apply_layer",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("boom")))
    root = tmp_path / "rootfs"
    root.mkdir()

    before = _open_fds()
    with pytest.raises(RuntimeError, match="boom"):
        pull_image_into("x:1", str(root), "x86_64")
    assert len(_open_fds() - before) == 0
    assert os.path.isfile(layer_cache_path(digest))


def test_pull_offline_refuses_a_poisoned_cached_layer(tmp_path, builders,
                                                      monkeypatch):
    # Nothing to refetch with: the pull fails rather than applying the
    # blob that is sitting there.
    digest = _seed_image(builders)
    _poison(digest, builders)

    def _no_net(*a, **k):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(pull_mod, "get_auth_token", _no_net)

    root = tmp_path / "rootfs"
    root.mkdir()
    with pytest.raises(RuntimeError):
        pull_image_into("x:1", str(root), "x86_64")
    assert not (root / "etc" / "PWNED").exists()
    assert os.listdir(str(root)) == []


def test_pull_refuses_an_unhashable_digest(tmp_path, builders, monkeypatch):
    # A cached blob under an algorithm we cannot compute could never be
    # checked, so it is refused instead of trusted by name.
    digest = "sha512:" + "a" * 128
    with open(layer_cache_path(digest), "wb") as fh:
        fh.write(b"whatever")
    manifest = {"schemaVersion": 2,
                "layers": [{"digest": digest, "size": 8,
                            "mediaType": OCI_LAYER_MEDIA}]}
    save_manifest_cache("x:512", "x86_64", manifest, "library/x", {})

    root = tmp_path / "rootfs"
    root.mkdir()
    with pytest.raises(RuntimeError, match="Unsupported digest algorithm"):
        pull_image_into("x:512", str(root), "x86_64")


def test_download_blob_does_not_trust_a_cached_name(builders, monkeypatch):
    # Even called directly, download_blob re-hashes what it already has:
    # a poisoned file is dropped and the blob fetched again.
    from proot_distro.helpers.docker import layers as layers_mod

    digest, _size, _diff = builders.seed_cached_layer(BENIGN)
    good = open(layer_cache_path(digest), "rb").read()
    _poison(digest, builders)

    served = []

    def _open(req):
        served.append(req.full_url)
        return _Resp(good, ct="application/octet-stream")

    monkeypatch.setattr(layers_mod, "opener",
                        lambda insecure=False: SimpleNamespace(open=_open))

    path = layers_mod.download_blob("library/x", digest, "", "https://r")
    assert served, "the cached file was handed back without a check"
    assert open(path, "rb").read() == good


# ---------------------------------------------------------------------------
# pull: content-addressed responses from the registry
# ---------------------------------------------------------------------------

class _Resp(io.BytesIO):
    def __init__(self, body, ct="application/vnd.oci.image.manifest.v1+json"):
        super().__init__(body)
        self.headers = {"Content-Type": ct}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_opener(monkeypatch, body):
    monkeypatch.setattr(
        pull_mod, "opener",
        lambda insecure=False: SimpleNamespace(open=lambda req: _Resp(body)),
    )


def test_manifest_fetched_by_digest_is_verified(monkeypatch):
    body = json.dumps({"schemaVersion": 2, "layers": []}).encode()
    good = "sha256:" + hashlib.sha256(body).hexdigest()
    bad = "sha256:" + "b" * 64

    _fake_opener(monkeypatch, body)
    assert pull_mod._get_manifest("library/x", good, "T", "https://r")

    _fake_opener(monkeypatch, body)
    with pytest.raises(RuntimeError, match="does not match its digest"):
        pull_mod._get_manifest("library/x", bad, "T", "https://r")


def test_manifest_fetched_by_tag_is_not_digest_checked(monkeypatch):
    # A tag says nothing about content; only digests are checkable.
    body = json.dumps({"schemaVersion": 2, "layers": []}).encode()
    _fake_opener(monkeypatch, body)
    assert pull_mod._get_manifest("library/x", "latest", "T", "https://r")


def test_config_blob_is_verified(monkeypatch):
    body = json.dumps({"architecture": "amd64"}).encode()
    good = "sha256:" + hashlib.sha256(body).hexdigest()
    bad = "sha256:" + "c" * 64

    _fake_opener(monkeypatch, body)
    assert pull_mod._fetch_config_blob(
        "library/x", good, "T", "https://r") == {"architecture": "amd64"}

    # Entrypoint/Cmd/Env come from this blob and `run` executes them, so a
    # mismatch is fatal rather than an empty config.
    _fake_opener(monkeypatch, body)
    with pytest.raises(RuntimeError, match="does not match its digest"):
        pull_mod._fetch_config_blob("library/x", bad, "T", "https://r")


# ---------------------------------------------------------------------------
# install <archive|URL>: blobs a remote party chose the digests for
# ---------------------------------------------------------------------------

def _rewrite_member(src, dst, name, data):
    """Copy an OCI archive, replacing one member's bytes."""
    with tarfile.open(src) as tf_in, tarfile.open(dst, "w") as tf_out:
        for member in tf_in.getmembers():
            payload = tf_in.extractfile(member).read() if member.isreg() else b""
            if member.name == name:
                payload = data
            member.size = len(payload)
            tf_out.addfile(member, io.BytesIO(payload))
    return dst


def test_install_oci_layer_digest_mismatch_refused(tmp_path, builders):
    # The archive declares a digest belonging to some other image's layer
    # and ships different bytes under it. Accepting that would plant the
    # attacker's tar in the cache under a name a later pull reuses.
    meta = builders.make_oci_archive(str(tmp_path / "ok.oci.tar"), [BENIGN])
    digest = meta["layer_digests"][0]
    evil_gz, _d, _i = builders.make_layer_blob(EVIL)
    arc = _rewrite_member(
        str(tmp_path / "ok.oci.tar"), str(tmp_path / "evil.oci.tar"),
        "blobs/sha256/" + digest.split(":")[1], evil_gz,
    )

    root = tmp_path / "rootfs"
    root.mkdir()
    with pytest.raises(RuntimeError, match="does not match its digest"):
        install_local_into(arc, str(root), "x86_64")

    assert not os.path.exists(layer_cache_path(digest)), \
        "a mismatched blob must not reach the shared layer cache"
    assert not (root / "etc" / "PWNED").exists()


def test_install_oci_manifest_blob_swapped_refused(tmp_path, builders):
    # index.json is the archive's root of trust; everything below it is
    # addressed by digest and must hash to it.
    builders.make_oci_archive(str(tmp_path / "ok.oci.tar"), [BENIGN])
    with tarfile.open(str(tmp_path / "ok.oci.tar")) as tf:
        index = json.loads(tf.extractfile("index.json").read())
    man_hex = index["manifests"][0]["digest"].split(":")[1]
    forged = json.dumps({"schemaVersion": 2, "config": {"digest": "sha256:" +
                        "0" * 64}, "layers": []}).encode()
    arc = _rewrite_member(
        str(tmp_path / "ok.oci.tar"), str(tmp_path / "evil.oci.tar"),
        "blobs/sha256/" + man_hex, forged,
    )

    root = tmp_path / "rootfs"
    root.mkdir()
    with pytest.raises(RuntimeError, match="does not match its digest"):
        install_local_into(arc, str(root), "x86_64")


def test_install_oci_repairs_a_poisoned_cache_entry(tmp_path, builders):
    # A valid archive is the better copy: the bad blob is replaced rather
    # than reused, and the install proceeds.
    meta = builders.make_oci_archive(str(tmp_path / "ok.oci.tar"), [BENIGN])
    digest = meta["layer_digests"][0]
    evil_gz, _d, _i = builders.make_layer_blob(EVIL)
    os.makedirs(os.path.dirname(layer_cache_path(digest)), exist_ok=True)
    with open(layer_cache_path(digest), "wb") as fh:
        fh.write(evil_gz)

    root = tmp_path / "rootfs"
    root.mkdir()
    install_local_into(
        str(tmp_path / "ok.oci.tar"), str(root), "x86_64")

    assert (root / "etc" / "os-release").read_bytes() == b"ID=real\n"
    assert not (root / "etc" / "PWNED").exists()
    blob = open(layer_cache_path(digest), "rb").read()
    assert "sha256:" + hashlib.sha256(blob).hexdigest() == digest


# ---------------------------------------------------------------------------
# Locally built layers: refuse, but never delete what cannot be refetched
# ---------------------------------------------------------------------------

def test_push_refuses_a_poisoned_layer_blob(builders, monkeypatch):
    digest, size, _diff = builders.seed_cached_layer(BENIGN)
    _poison(digest, builders)

    cfg_bytes = b"CFG"
    cfg_digest = "sha256:" + hashlib.sha256(cfg_bytes).hexdigest()
    manifest = {"config": {"digest": cfg_digest},
                "layers": [{"digest": digest, "size": size}]}

    monkeypatch.setattr(push_mod, "load_manifest_cache",
                        lambda ref, arch: (manifest, "me/app", {"k": "v"}))
    monkeypatch.setattr(push_mod, "canonical_json", lambda d: cfg_bytes)
    monkeypatch.setattr(push_mod, "parse_image_ref",
                        lambda ref: ("reg.example", "me/app", "latest"))
    monkeypatch.setattr(push_mod, "get_auth_token",
                        lambda *a, **k: ("TKN", "https://reg.example"))
    monkeypatch.setattr(push_mod, "_blob_exists", lambda *a, **k: False)

    uploaded = []
    monkeypatch.setattr(push_mod, "_upload_blob_fd",
                        lambda *a, **k: uploaded.append(a))

    with pytest.raises(RuntimeError, match="does not match its digest"):
        push_mod.push_image("reg.example/me/app:latest", "x86_64")
    assert uploaded == []
    # Nothing deleted: a built layer exists nowhere else.
    assert os.path.isfile(layer_cache_path(digest))


def test_write_oci_archive_refuses_a_poisoned_layer_blob(tmp_path, builders):
    digest, size, diff_id = builders.seed_cached_layer(BENIGN)
    manifest, config = oci_writer.build_manifest_and_config(
        {"config": {}, "history": [{"created": "x"}]},
        [{"digest": digest, "size": size, "diff_id": diff_id}], "amd64",
    )
    _poison(digest, builders)

    out = tmp_path / "img.oci.tar"
    with pytest.raises(RuntimeError, match="does not match its digest"):
        oci_writer.write_oci_archive(str(out), manifest, config, "x:1")
    assert not out.exists()
    assert os.path.isfile(layer_cache_path(digest))


def test_build_stage_inherit_refuses_a_poisoned_layer(tmp_path, builders):
    digest, size, diff_id = builders.seed_cached_layer(BENIGN)
    _poison(digest, builders)

    parent = SimpleNamespace(
        image_config={"config": {}}, layers=[{"digest": digest, "size": size,
                                              "diff_id": diff_id}],
        name="base", index=0, parent_layer_digest=digest,
    )
    child_root = tmp_path / "child"
    child_root.mkdir()
    child = SimpleNamespace(rootfs_dir=str(child_root), rootfs_fd=None,
                            image_config={}, layers=[],
                            parent_layer_digest="")
    engine = BuildEngine.__new__(BuildEngine)

    with pytest.raises(BuildError, match="does not match its digest"):
        engine._inherit_from_stage(child, parent)
    assert not (child_root / "etc" / "PWNED").exists()


# ---------------------------------------------------------------------------
# Build cache: a poisoned recorded layer is a miss, not a hit
# ---------------------------------------------------------------------------

def _run_engine(tmp_path):
    stage = SimpleNamespace(
        index=0, name="", rootfs_dir=str(tmp_path / "rootfs"),
        dir_fd=None, rootfs_fd=None, layers=[],
        parent_layer_digest="", shell=["/bin/sh", "-c"], workdir="/",
    )
    os.makedirs(stage.rootfs_dir, exist_ok=True)
    engine = SimpleNamespace(
        current=stage, no_cache=False, tmp_root=str(tmp_path),
        expansion_scope=lambda: {}, log=lambda *a, **k: None, quiet=True,
    )
    instr = {"name": "RUN", "value": "true", "exec_form": False,
             "lineno": 1, "heredocs": []}
    return engine, stage, instr


def test_run_cache_hit_is_applied_when_blob_is_intact(tmp_path, builders,
                                                      monkeypatch):
    digest, size, diff_id = builders.seed_cached_layer(BENIGN)
    engine, stage, instr = _run_engine(tmp_path)
    monkeypatch.setattr(run_step, "cache_lookup", lambda recipe: {
        "layer_digest": digest, "size": size, "diff_id": diff_id})

    def _never(*a, **k):
        raise AssertionError("proot was executed on a cache hit")

    monkeypatch.setattr(run_step, "_exec_proot", _never)

    run_step.do_run(engine, instr)
    assert stage.layers == [{"digest": digest, "size": size,
                             "diff_id": diff_id}]
    assert os.path.exists(os.path.join(stage.rootfs_dir, "etc", "os-release"))


def test_run_cache_hit_ignored_when_blob_is_poisoned(tmp_path, builders,
                                                     monkeypatch):
    digest, size, diff_id = builders.seed_cached_layer(BENIGN)
    _poison(digest, builders)
    engine, stage, instr = _run_engine(tmp_path)
    monkeypatch.setattr(run_step, "cache_lookup", lambda recipe: {
        "layer_digest": digest, "size": size, "diff_id": diff_id})

    class _Ran(Exception):
        pass

    def _ran(*a, **k):
        raise _Ran()

    monkeypatch.setattr(run_step, "_exec_proot", _ran)

    # The step is re-run instead of trusting the recorded layer.
    with pytest.raises(_Ran):
        run_step.do_run(engine, instr)
    assert stage.layers == []
    assert not os.path.exists(os.path.join(stage.rootfs_dir, "etc", "PWNED"))


# ---------------------------------------------------------------------------
# The blob's *directory* is a name too.
# ---------------------------------------------------------------------------
#
# O_NOFOLLOW on a composed LAYER_CACHE_DIR/<blob> covers the final
# component and nothing above it. `oci_layers` sits below BASE_CACHE_DIR,
# which on Termux is under the $TERMUX_PREFIX bound read-write into every
# non-isolated container, so a guest can leave it behind as a symlink --
# and the blob was then read out of whatever it named, and, on a digest
# mismatch, unlinked from there.

@pytest.fixture
def hijacked_layer_cache(tmp_path):
    """Point LAYER_CACHE_DIR at a host directory holding a decoy blob.

    The decoy carries the *name* a real digest maps to and content that
    does not hash to it, which is what makes it both a candidate to read
    and a candidate to evict.
    """
    digest = "sha256:" + "0" * 64
    host_dir = tmp_path / "host-dir"
    host_dir.mkdir()
    decoy = host_dir / layer_cache_name(digest)
    decoy.write_bytes(b"a host file that happens to sit under that name\n")
    shutil.rmtree(LAYER_CACHE_DIR)
    os.symlink(str(host_dir), LAYER_CACHE_DIR)
    return digest, decoy


def test_verified_layer_does_not_read_through_a_symlinked_cache(
        hijacked_layer_cache):
    digest, decoy = hijacked_layer_cache
    assert open_verified_layer(digest) is None
    assert decoy.exists()


def test_eviction_does_not_unlink_through_a_symlinked_cache(
        hijacked_layer_cache):
    digest, decoy = hijacked_layer_cache
    open_verified_layer(digest, evict=True)
    assert decoy.exists(), "eviction deleted a host file"


def test_required_layer_does_not_read_through_a_symlinked_cache(
        hijacked_layer_cache):
    digest, decoy = hijacked_layer_cache
    with pytest.raises(RuntimeError):
        open_required_layer(digest)
    assert decoy.exists()


def test_blob_present_does_not_answer_for_a_symlinked_cache(
        hijacked_layer_cache):
    digest, _decoy = hijacked_layer_cache
    assert blob_present(digest) is False


def test_a_planted_blob_name_is_not_a_blob(builders):
    """A symlink under the blob's own name is refused, not followed."""
    digest, _size, _diff = builders.seed_cached_layer(BENIGN)
    path = layer_cache_path(digest)
    real = path + ".moved"
    os.rename(path, real)
    os.symlink(real, path)
    assert open_verified_layer(digest, evict=False) is None
    assert blob_present(digest) is False
    # Refused, not deleted: the link is not ours to remove by hashing it.
    assert os.path.lexists(path)
