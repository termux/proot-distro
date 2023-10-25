# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="OpenSUSE"
DISTRO_COMMENT="Rolling release (Tumbleweed)."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v3.18.1/opensuse-aarch64-pd-v3.18.1.tar.xz"
TARBALL_SHA256['aarch64']="586bcfb765abdfcbc22a3124f666e2edc2c70ded060092d2adf4a7c71fbad5ea"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v3.18.1/opensuse-arm-pd-v3.18.1.tar.xz"
TARBALL_SHA256['arm']="cfa8fdca2b734fdf3b9dd5ef3b3213609f8e692c6f82270acb858af1ba5e6b5a"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v3.18.1/opensuse-i686-pd-v3.18.1.tar.xz"
TARBALL_SHA256['i686']="971a6346af4e4f6e8e91344662f96af52b50dc7a62ab62e8d6d3c21ea11f7c75"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v3.18.1/opensuse-x86_64-pd-v3.18.1.tar.xz"
TARBALL_SHA256['x86_64']="5454b5defcbacb6327c6acc6193022b1281d6995661f7c4ebdc2bae8b895f77e"

distro_setup() {
	# Lock package filesystem to remove issues regarding zypper dup
	run_proot_cmd zypper al filesystem
}
