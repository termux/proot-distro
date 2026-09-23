# Tests for proot_distro.helpers.build_cache — the recipe-hash keyed build
# layer cache index.

from proot_distro.helpers import build_cache


def _instr(name="RUN", value="echo hi", flags=None, heredocs=None):
    return {
        "name": name,
        "value": value,
        "flags": flags or {},
        "heredocs": heredocs or [],
    }


def test_recipe_hash_deterministic():
    a = build_cache.compute_recipe_hash("sha256:parent", _instr())
    b = build_cache.compute_recipe_hash("sha256:parent", _instr())
    assert a == b
    assert isinstance(a, str) and len(a) == 64


def test_recipe_hash_sensitive_to_each_input():
    base = build_cache.compute_recipe_hash("p", _instr())
    assert base != build_cache.compute_recipe_hash("q", _instr())
    assert base != build_cache.compute_recipe_hash(
        "p", _instr(name="COPY"))
    assert base != build_cache.compute_recipe_hash(
        "p", _instr(value="echo bye"))
    assert base != build_cache.compute_recipe_hash(
        "p", _instr(flags={"a": "b"}))
    assert base != build_cache.compute_recipe_hash(
        "p", _instr(heredocs=[{"body": "x"}]))
    assert base != build_cache.compute_recipe_hash(
        "p", _instr(), extra_inputs="abc")


def test_record_and_lookup_roundtrip():
    h = build_cache.compute_recipe_hash("p", _instr())
    assert build_cache.lookup(h) is None
    build_cache.record(h, "sha256:layer", "sha256:diff", 123,
                       {"config": {"User": "root"}})
    entry = build_cache.lookup(h)
    assert entry["layer_digest"] == "sha256:layer"
    assert entry["diff_id"] == "sha256:diff"
    assert entry["size"] == 123
    assert entry["image_config_patch"] == {"config": {"User": "root"}}
    assert "created" in entry


def test_lookup_empty_hash_returns_none():
    assert build_cache.lookup("") is None
    assert build_cache.lookup(None) is None


def test_corrupt_index_is_recovered(tmp_path):
    # Write garbage into the index, then lookup/record must not blow up.
    with open(build_cache._INDEX_PATH, "w") as fh:
        fh.write("this is not json {{{")
    assert build_cache.lookup("anyhash") is None
    build_cache.record("h1", "sha256:l", "sha256:d", 1)
    assert build_cache.lookup("h1")["layer_digest"] == "sha256:l"


def test_non_dict_index_is_recovered(tmp_path):
    import json
    with open(build_cache._INDEX_PATH, "w") as fh:
        json.dump(["not", "a", "dict"], fh)
    assert build_cache.lookup("h") is None


# ----- a byte that is not UTF-8 ------------------------------------------

def _strict_recipe_hash(parent, instr, extra):
    """The derivation as it was, strict UTF-8, for text that allowed it."""
    import hashlib
    h = hashlib.sha256()
    for part in (parent, instr["name"], "", instr["value"]):
        h.update(part.encode())
        h.update(b"\x00")
    for hd in instr["heredocs"]:
        h.update(b"<<" + hd["body"].encode() + b">>")
    h.update(b"\x00")
    h.update(extra.encode())
    return h.hexdigest()


def test_recipe_hash_is_unchanged_for_text_utf8_can_encode():
    # surrogatepass must not move a recorded recipe: every entry already
    # in an index was keyed by the strict derivation.
    instr = _instr(value="echo héllo €",
                   heredocs=[{"body": "echo ю"}])
    extra = "V=über\nX=1"
    assert (build_cache.compute_recipe_hash("sha256:p", instr, extra)
            == _strict_recipe_hash("sha256:p", instr, extra))


def test_recipe_hash_accepts_a_lone_surrogate():
    # A --build-arg byte that is not UTF-8 is a lone surrogate, and it is
    # in the ARG state every RUN after it hashes, used or not. Strict
    # UTF-8 raised out of the cache lookup, a traceback before the step.
    a = build_cache.compute_recipe_hash(
        "p", _instr(value="echo $V", heredocs=[{"body": "x\udcff"}]),
        extra_inputs="V=a\udcffb")
    b = build_cache.compute_recipe_hash(
        "p", _instr(value="echo $V", heredocs=[{"body": "x\udcff"}]),
        extra_inputs="V=a\udcfeb")
    assert len(a) == 64 and a != b
    # Every surrogate, not only the escape range argv produces.
    assert len(build_cache.compute_recipe_hash(
        "p", _instr(), extra_inputs="X=\ud800")) == 64
