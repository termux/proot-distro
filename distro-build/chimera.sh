dist_name="Chimera Linux"
dist_version="20240707"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/chimera-*.tar.xz

	for arch in aarch64 riscv64 x86_64; do
		curl --fail --location \
			--output "${WORKDIR}/chimera-${dist_version}-${arch}.tar.gz" \
			"https://repo.chimera-linux.org/live/${dist_version}/chimera-linux-${arch}-ROOTFS-${dist_version}-bootstrap.tar.gz"

		sudo rm -rf "${WORKDIR}/chimera-$(translate_arch "$arch")"
		sudo mkdir -m 755 "${WORKDIR}/chimera-$(translate_arch "$arch")"
		sudo tar -zxp --acls --xattrs --xattrs-include='*' \
			-f "${WORKDIR}/chimera-${dist_version}-${arch}.tar.gz" \
			-C "${WORKDIR}/chimera-$(translate_arch "$arch")"

		cat <<- EOF | sudo unshare -mpf bash -e -
		rm -f "${WORKDIR}/chimera-$(translate_arch "$arch")/etc/resolv.conf"
		echo "nameserver 1.1.1.1" > "${WORKDIR}/chimera-$(translate_arch "$arch")/etc/resolv.conf"
		mount --bind /dev "${WORKDIR}/chimera-$(translate_arch "$arch")/dev"
		mount --bind /proc "${WORKDIR}/chimera-$(translate_arch "$arch")/proc"
		mount --bind /sys "${WORKDIR}/chimera-$(translate_arch "$arch")/sys"
		chroot "${WORKDIR}/chimera-$(translate_arch "$arch")" apk upgrade
		EOF

		sudo rm -f "${WORKDIR:?}/chimera-$(translate_arch "$arch")"/var/cache/apk/* || true

		archive_rootfs "${ROOTFS_DIR}/chimera-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			"chimera-$(translate_arch "$arch")"
	done
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/chimera.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Chimera Linux"
	DISTRO_COMMENT="Version '${dist_version}'."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/chimera-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/chimera-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['riscv64']="${GIT_RELEASE_URL}/chimera-riscv64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['riscv64']="$(sha256sum "${ROOTFS_DIR}/chimera-riscv64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/chimera-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/chimera-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
