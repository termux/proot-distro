dist_name="Ubuntu"
dist_version="jammy"

bootstrap_distribution() {
	for arch in arm64 armhf amd64; do
		sudo mmdebstrap \
			--architectures=${arch} \
			--variant=apt \
			--components="main,universe,multiverse" \
			--format=tar \
			"${dist_version}" \
			"${ROOTFS_DIR}/ubuntu-lts-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar"
		sudo chown $(id -un):$(id -gn) "${ROOTFS_DIR}/ubuntu-lts-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar"
		xz "${ROOTFS_DIR}/ubuntu-lts-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar"
	done
	unset arch
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/ubuntu-lts.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Ubuntu"
	DISTRO_COMMENT="Current LTS release (${dist_version}). Not available for x86 32-bit (i686) CPUs."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/ubuntu-lts-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/ubuntu-lts-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['arm']="${GIT_RELEASE_URL}/ubuntu-lts-arm-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['arm']="$(sha256sum "${ROOTFS_DIR}/ubuntu-lts-arm-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/ubuntu-lts-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/ubuntu-lts-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
