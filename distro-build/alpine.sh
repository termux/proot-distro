dist_name="Alpine Linux"
dist_version="3.20.2"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/alpine-*.tar.xz

	for arch in aarch64 armv7 riscv64 x86 x86_64; do
		curl --fail --location \
			--output "${WORKDIR}/alpine-minirootfs-${dist_version}-${arch}.tar.gz" \
			"https://dl-cdn.alpinelinux.org/alpine/v${dist_version:0:4}/releases/${arch}/alpine-minirootfs-${dist_version}-${arch}.tar.gz"
		curl --fail --location \
			--output "${WORKDIR}/alpine-minirootfs-${dist_version}-${arch}.tar.gz.sha256" \
			"https://dl-cdn.alpinelinux.org/alpine/v${dist_version:0:4}/releases/${arch}/alpine-minirootfs-${dist_version}-${arch}.tar.gz.sha256"
		sha256sum -c "${WORKDIR}/alpine-minirootfs-${dist_version}-${arch}.tar.gz.sha256"

		sudo rm -rf "${WORKDIR}/alpine-$(translate_arch "$arch")"
		sudo mkdir -m 755 "${WORKDIR}/alpine-$(translate_arch "$arch")"
		sudo tar -zxp --acls --xattrs --xattrs-include='*' \
			-f "${WORKDIR}/alpine-minirootfs-${dist_version}-${arch}.tar.gz" \
			-C "${WORKDIR}/alpine-$(translate_arch "$arch")"

		cat <<- EOF | sudo unshare -mpf bash -e -
		rm -f "${WORKDIR}/alpine-$(translate_arch "$arch")/etc/resolv.conf"
		echo "nameserver 1.1.1.1" > "${WORKDIR}/alpine-$(translate_arch "$arch")/etc/resolv.conf"
		mount --bind /dev "${WORKDIR}/alpine-$(translate_arch "$arch")/dev"
		mount --bind /proc "${WORKDIR}/alpine-$(translate_arch "$arch")/proc"
		mount --bind /sys "${WORKDIR}/alpine-$(translate_arch "$arch")/sys"
		echo "http://dl-cdn.alpinelinux.org/alpine/edge/main" > "${WORKDIR}/alpine-$(translate_arch "$arch")/etc/apk/repositories"
		echo "http://dl-cdn.alpinelinux.org/alpine/edge/community" >> "${WORKDIR}/alpine-$(translate_arch "$arch")/etc/apk/repositories"
		chroot "${WORKDIR}/alpine-$(translate_arch "$arch")" apk upgrade
		chroot "${WORKDIR}/alpine-$(translate_arch "$arch")" apk add shadow-login
		chroot "${WORKDIR}/alpine-$(translate_arch "$arch")" ln -sf /var/cache/apk /etc/apk/cache
		EOF

		sudo rm -f "${WORKDIR:?}/alpine-$(translate_arch "$arch")"/var/cache/apk/* || true

		archive_rootfs "${ROOTFS_DIR}/alpine-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			"alpine-$(translate_arch "$arch")"
	done
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/alpine.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Alpine Linux"
	DISTRO_COMMENT="Rolling release branch (edge)."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/alpine-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/alpine-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['arm']="${GIT_RELEASE_URL}/alpine-arm-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['arm']="$(sha256sum "${ROOTFS_DIR}/alpine-arm-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['i686']="${GIT_RELEASE_URL}/alpine-i686-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['i686']="$(sha256sum "${ROOTFS_DIR}/alpine-i686-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['riscv64']="${GIT_RELEASE_URL}/alpine-riscv64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['riscv64']="$(sha256sum "${ROOTFS_DIR}/alpine-riscv64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/alpine-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/alpine-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
