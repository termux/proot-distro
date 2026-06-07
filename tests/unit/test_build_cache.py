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
