# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Parrot Security OS"
DISTRO_COMMENT="Stable release."

TARBALL_URL['aarch64']="${ROOTFS_FILESERVER_URL}/parrot-aarch64-pd-${CURRENT_VERSION}.tar.xz"
TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/parrot-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
TARBALL_URL['arm']="${ROOTFS_FILESERVER_URL}/parrot-arm-pd-${CURRENT_VERSION}.tar.xz"
TARBALL_SHA256['arm']="$(sha256sum "${ROOTFS_DIR}/parrot-arm-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
TARBALL_URL['riscv64']="${ROOTFS_FILESERVER_URL}/parrot-riscv64-pd-${CURRENT_VERSION}.tar.xz"
TARBALL_SHA256['riscv64']="$(sha256sum "${ROOTFS_DIR}/parrot-riscv64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
TARBALL_URL['x86_64']="${ROOTFS_FILESERVER_URL}/parrot-x86_64-pd-${CURRENT_VERSION}.tar.xz"
TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/parrot-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"

distro_setup() {
	${TAB}# Configure en_US.UTF-8 locale.
	${TAB}sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	${TAB}run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
	${TAB}run_proot_cmd DEBIAN_FRONTEND=noninteractive apt update
	${TAB}run_proot_cmd DEBIAN_FRONTEND=noninteractive apt -y install parrot-core
	${TAB}run_proot_cmd DEBIAN_FRONTEND=noninteractive apt update
	${TAB}run_proot_cmd DEBIAN_FRONTEND=noninteractive apt -y full-upgrade
}