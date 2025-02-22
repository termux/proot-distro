# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="OpenSUSE"
DISTRO_COMMENT="Rolling release (Tumbleweed)."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.21.0/opensuse-aarch64-pd-v4.21.0.tar.xz"
TARBALL_SHA256['aarch64']="23cdd0ba0f85e261da2560e3fcf7ac19cd1bcf7afdc2144b694ea9229058fa2a"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.21.0/opensuse-arm-pd-v4.21.0.tar.xz"
TARBALL_SHA256['arm']="a02caa5a17fd90399d5864772eb1317727abe0ffe5b088cf496e496e0f33fc76"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.21.0/opensuse-i686-pd-v4.21.0.tar.xz"
TARBALL_SHA256['i686']="e7e2f24928bd44dd270cd503257697517b1284ae57ea18aa5a6f66cd1857d5fa"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.21.0/opensuse-x86_64-pd-v4.21.0.tar.xz"
TARBALL_SHA256['x86_64']="cfb70fe5acd74928f5573a59ac038eff91f1bd4c6a95cc00e69424e6f3ae89b3"

distro_setup() {
	# Lock package filesystem to remove issues regarding zypper dup
	run_proot_cmd zypper al filesystem
}
