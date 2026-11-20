import subprocess
import sys


def test_session_helpers_import_before_operations_package():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import proot_distro.session_command; "
            "import proot_distro.session_runtime",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode(errors="replace")
