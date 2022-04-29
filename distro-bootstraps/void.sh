dist_name="Void Linux"
dist_version="20210316"

bootstrap_distribution() {
	for arch in aarch64 armv7l i686 x86_64; do
		curl --fail --location \
			--output "${WORKDIR}/void-${arch}.tar.xz" \
			"https://alpha.de.repo.voidlinux.org/live/${dist_version}/void-${arch}-ROOTFS-${dist_version}.tar.xz"

		sudo mkdir -m 755 "${WORKDIR}/void-$(translate_arch "$arch")"
		sudo tar -Jxp \
			-f "${WORKDIR}/void-${arch}.tar.xz" \
			-C "${WORKDIR}/void-$(translate_arch "$arch")"

		cat <<- EOF | sudo unshare -mpf bash -e -
		rm -f "${WORKDIR}/void-$(translate_arch "$arch")/etc/resolv.conf"
		echo "nameserver 1.1.1.1" > "${WORKDIR}/void-$(translate_arch "$arch")/etc/resolv.conf"
		mount --bind /dev "${WORKDIR}/void-$(translate_arch "$arch")/dev"
		mount --bind /proc "${WORKDIR}/void-$(translate_arch "$arch")/proc"
		mount --bind /sys "${WORKDIR}/void-$(translate_arch "$arch")/sys"
		chroot "${WORKDIR}/void-$(translate_arch "$arch")" env SSL_NO_VERIFY_PEER=1 xbps-install -Suy xbps
		chroot "${WORKDIR}/void-$(translate_arch "$arch")" env SSL_NO_VERIFY_PEER=1 xbps-install -uy
		chroot "${WORKDIR}/void-$(translate_arch "$arch")" env SSL_NO_VERIFY_PEER=1 xbps-install -y base-minimal
		chroot "${WORKDIR}/void-$(translate_arch "$arch")" env SSL_NO_VERIFY_PEER=1 xbps-remove -y base-voidstrap
		chroot "${WORKDIR}/void-$(translate_arch "$arch")" env SSL_NO_VERIFY_PEER=1 xbps-reconfigure -fa
		EOF

		sudo rm -f "${WORKDIR}/void-$(translate_arch "$arch")"/var/cache/xbps/* || true

		sudo tar -J -c \
			-f "${ROOTFS_DIR}/void-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			-C "$WORKDIR" \
			"void-$(translate_arch "$arch")"
			sudo chown $(id -un):$(id -gn) "${ROOTFS_DIR}/void-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz"
	done
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/void.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Void Linux"

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/void-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/void-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['arm']="${GIT_RELEASE_URL}/void-arm-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['arm']="$(sha256sum "${ROOTFS_DIR}/void-arm-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['i686']="${GIT_RELEASE_URL}/void-i686-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['i686']="$(sha256sum "${ROOTFS_DIR}/void-i686-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/void-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/void-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"

	distro_setup() {
	${TAB}# Set default shell to bash.
	${TAB}run_proot_cmd usermod --shell /bin/bash root
	}
	EOF
}
