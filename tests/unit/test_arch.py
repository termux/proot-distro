# Tests for proot_distro.arch — ELF e_machine detection, arch normalization,
# and emulator argument selection.

import os
import stat

import pytest

from proot_distro import arch


@pytest.mark.parametrize("name", ["i686", "arm", "x86_64", "aarch64", "riscv64"])
def test_elf_arch_detects_each(tmp_path, builders, name):
    p = tmp_path / "bin"
    builders.write_elf(str(p), name)
    assert arch._elf_arch(str(p)) == name


def test_elf_arch_big_endian(tmp_path, builders):
    p = tmp_path / "bin"
    builders.write_elf(str(p), "aarch64", little_endian=False)
    assert arch._elf_arch(str(p)) == "aarch64"


def test_elf_arch_truncated_returns_empty(tmp_path, builders):
    p = tmp_path / "bin"
    builders.write_elf(str(p), "x86_64", truncated=True)
    assert arch._elf_arch(str(p)) == ""


def test_elf_arch_bad_magic_returns_empty(tmp_path, builders):
    p = tmp_path / "bin"
    builders.write_elf(str(p), "x86_64", valid_magic=False)
    assert arch._elf_arch(str(p)) == ""


def test_elf_arch_unknown_machine_returns_empty(tmp_path):
    p = tmp_path / "bin"
    # Valid ELF magic but e_machine = 0 (unmapped).
    p.write_bytes(b"\x7fELF\x02\x01" + b"\x00" * 14)
    assert arch._elf_arch(str(p)) == ""


def test_elf_arch_missing_file(tmp_path):
    assert arch._elf_arch(str(tmp_path / "nope")) == ""


def test_detect_installed_arch_from_rootfs(tmp_path, builders):
    root = tmp_path / "rootfs"
    builders.make_rootfs(str(root), arch="aarch64")
    assert arch.detect_installed_arch(str(root)) == "aarch64"


def test_detect_installed_arch_unknown(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert arch.detect_installed_arch(str(empty)) == "unknown"


def test_detect_installed_arch_by_container_name(builders):
    builders.make_container("box", arch="x86_64")
    # A bare name (no os.sep) resolves through container_rootfs.
    assert arch.detect_installed_arch("box") == "x86_64"


@pytest.mark.parametrize("raw,expected", [
    ("aarch64", "aarch64"),
    ("x86_64", "x86_64"),
    ("riscv64", "riscv64"),
    ("arm64", "aarch64"),
    ("amd64", "x86_64"),
    ("386", "i686"),
    ("arm", "arm"),
    ("arm/v7", "arm"),
    ("linux/arm64", "aarch64"),
    ("linux/amd64", "x86_64"),
    ("  amd64  ", "x86_64"),
])
def test_normalize_arch_accepts(raw, expected):
    assert arch.normalize_arch(raw) == expected


@pytest.mark.parametrize("raw", ["sparc", "", "linux/", "ppc64le", "x86"])
def test_normalize_arch_rejects(raw):
    assert arch.normalize_arch(raw) is None


def test_emulator_args_native_is_empty():
    assert arch.get_emulator_args("x86_64", "x86_64") == []


def test_emulator_args_32_on_64_native(monkeypatch):
    monkeypatch.setattr(arch, "supports_32bit", lambda: True)
    assert arch.get_emulator_args("arm", "aarch64") == []
    assert arch.get_emulator_args("i686", "x86_64") == []


def test_emulator_args_missing_qemu_exits(monkeypatch, capsys):
    monkeypatch.setattr(arch.shutil, "which", lambda _bin: None)
    with pytest.raises(SystemExit) as exc:
        arch.get_emulator_args("aarch64", "x86_64")
    assert exc.value.code == 1
    assert "emulator package" in capsys.readouterr().err


def test_emulator_args_unsupported_arch_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        arch.get_emulator_args("m68k", "x86_64")
    assert exc.value.code == 1
    assert "unsupported architecture" in capsys.readouterr().err


def test_emulator_override_must_exist(capsys):
    with pytest.raises(SystemExit) as exc:
        arch.get_emulator_args("aarch64", "x86_64",
                               emulator_override="/no/such/emu")
    assert exc.value.code == 1
    assert "not found or not executable" in capsys.readouterr().err


def test_emulator_override_used_when_valid(tmp_path):
    emu = tmp_path / "qemu-fake"
    emu.write_text("#!/bin/sh\n")
    emu.chmod(emu.stat().st_mode | stat.S_IXUSR)
    args = arch.get_emulator_args("aarch64", "x86_64",
                                  emulator_override=str(emu))
    assert args[:2] == ["-q", str(emu)]
    # Any extra entries are android system --bind args.
    assert all(a.startswith("--bind=") for a in args[2:])
