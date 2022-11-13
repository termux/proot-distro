dist_name="Pardus"
dist_version="yirmibir"

bootstrap_distribution() {
	for arch in arm64 i386 amd64; do
		wget https://depo.pardus.org.tr/pardus/pool/main/p/pardus-archive-keyring/pardus-archive-keyring_2021.1_all.deb
		sudo dpkg -i pardus-archive-keyring_2021.1_all.deb
		rm pardus-archive-keyring_2021.1_all.deb
		sudo mmdebstrap \
			--architectures=${arch} \
			--variant=minbase \
			--components="main,contrib,non-free" \
			--include="dbus-user-session,ca-certificates,gvfs-daemons,libsystemd0,pardus-archive-keyring,systemd-sysv,udisks2" \
			--format=tar \
			"${dist_version}" \
			"${ROOTFS_DIR}/pardus-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar" \
			https://depo.pardus.org.tr/pardus
		sudo chown $(id -un):$(id -gn) "${ROOTFS_DIR}/pardus-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar"
		xz "${ROOTFS_DIR}/pardus-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar"
	done
	unset arch
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/pardus.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Pardus (${dist_version})"

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/pardus-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/pardus-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['i686']="${GIT_RELEASE_URL}/pardus-i686-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['i686']="$(sha256sum "${ROOTFS_DIR}/pardus-i686-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/pardus-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/pardus-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"

	distro_setup() {
	${TAB}# Don't update gvfs-daemons and udisks2
	${TAB}run_proot_cmd apt-mark hold gvfs-daemons udisks2
	}
	EOF
}
