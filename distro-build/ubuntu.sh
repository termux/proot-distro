dist_name="Ubuntu"
dist_version="kinetic"

bootstrap_distribution() {
	for arch in arm64 armhf amd64; do
		sudo mmdebstrap \
			--architectures=${arch} \
			--variant=apt \
			--components="main,universe,multiverse" \
			--format=tar \
			"${dist_version}" \
			"${ROOTFS_DIR}/ubuntu-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar"
		sudo chown $(id -un):$(id -gn) "${ROOTFS_DIR}/ubuntu-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar"
		xz "${ROOTFS_DIR}/ubuntu-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar"
	done
	unset arch
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/ubuntu.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Ubuntu"
	DISTRO_COMMENT="Latest release (${dist_version}). Not available for x86 32-bit (i686) CPUs."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/ubuntu-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/ubuntu-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['arm']="${GIT_RELEASE_URL}/ubuntu-arm-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['arm']="$(sha256sum "${ROOTFS_DIR}/ubuntu-arm-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/ubuntu-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/ubuntu-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
