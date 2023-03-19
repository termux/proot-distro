# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="OpenSUSE"
DISTRO_COMMENT="Rolling release (Tumbleweed)."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v3.3.0/opensuse-aarch64-pd-v3.3.0.tar.xz"
TARBALL_SHA256['aarch64']="d5ed8821bc22fbb02bc90a80f4e2ff47f8af40f6a33807c7fec868c57f427a58"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v3.3.0/opensuse-arm-pd-v3.3.0.tar.xz"
TARBALL_SHA256['arm']="2f74caa443e34bb077b23f92a4902d19dbc5e23b72774f5f4e85e66c16be9f02"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v3.3.0/opensuse-i686-pd-v3.3.0.tar.xz"
TARBALL_SHA256['i686']="134690a6ad87ebeffac23419cbdd4e1963fc63cef580aa50bf4120c2756678fb"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v3.3.0/opensuse-x86_64-pd-v3.3.0.tar.xz"
TARBALL_SHA256['x86_64']="6e3da2d841ccf4cb765b2b4ac7f4a7696281f75e7b95d728a2e267d110ec12cd"

distro_setup() {
        # Lock package filesystem to remove issues regarding zypper dup
        zypper al filesystem
}
