dist_name="Pardus"
dist_version="yirmibir"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/pardus-*.tar.xz

	curl -LO https://depo.pardus.org.tr/pardus/pool/main/p/pardus-archive-keyring/pardus-archive-keyring_2021.1_all.deb
	sudo dpkg -i pardus-archive-keyring_2021.1_all.deb
	rm pardus-archive-keyring_2021.1_all.deb

	for arch in arm64 i386 amd64; do
		sudo rm -rf "${WORKDIR}/pardus-$(translate_arch "$arch")"
		sudo mmdebstrap \
			--architectures=${arch} \
			--variant=minbase \
			--components="main,contrib,non-free" \
			--include="ca-certificates,libsystemd0,pardus-archive-keyring,systemd-sysv" \
			--format=directory \
			"${dist_version}" \
			"${WORKDIR}/pardus-$(translate_arch "$arch")" \
			https://depo.pardus.org.tr/pardus
		archive_rootfs "${ROOTFS_DIR}/pardus-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			"pardus-$(translate_arch "$arch")"
	done
	unset arch
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/pardus.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Pardus"
	DISTRO_COMMENT="Version '${dist_version}'."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/pardus-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/pardus-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['i686']="${GIT_RELEASE_URL}/pardus-i686-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['i686']="$(sha256sum "${ROOTFS_DIR}/pardus-i686-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/pardus-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/pardus-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
