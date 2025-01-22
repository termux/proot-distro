dist_name="Adélie Linux"
dist_version="1.0-beta6"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/adelie-*.tar.xz

	for arch in aarch64 armv7 x86_64; do
		curl --fail --location \
			--output "${WORKDIR}/adelie-${dist_version}-${arch}.tar.xz" \
			"https://distfiles.adelielinux.org/adelie/${dist_version}/iso/adelie-rootfs-mini-${arch}-${dist_version}-20241223.txz"

		sudo rm -rf "${WORKDIR}/adelie-$(translate_arch "$arch")"
		sudo mkdir -m 755 "${WORKDIR}/adelie-$(translate_arch "$arch")"
		sudo tar -Jxp --acls --xattrs --xattrs-include='*' \
			-f "${WORKDIR}/adelie-${dist_version}-${arch}.tar.xz" \
			-C "${WORKDIR}/adelie-$(translate_arch "$arch")"

		cat <<- EOF | sudo unshare -mpf bash -e -
		rm -f "${WORKDIR}/adelie-$(translate_arch "$arch")/etc/resolv.conf"
		echo "nameserver 1.1.1.1" > "${WORKDIR}/adelie-$(translate_arch "$arch")/etc/resolv.conf"
		mount --bind /dev "${WORKDIR}/adelie-$(translate_arch "$arch")/dev"
		mount --bind /proc "${WORKDIR}/adelie-$(translate_arch "$arch")/proc"
		mount --bind /sys "${WORKDIR}/adelie-$(translate_arch "$arch")/sys"
		chroot "${WORKDIR}/adelie-$(translate_arch "$arch")" apk upgrade
		EOF

		sudo rm -f "${WORKDIR:?}/adelie-$(translate_arch "$arch")"/var/cache/apk/* || true

		archive_rootfs "${ROOTFS_DIR}/adelie-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			"adelie-$(translate_arch "$arch")"
	done
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/adelie.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Adélie Linux"
	DISTRO_COMMENT="Version '${dist_version}'."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/adelie-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/adelie-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['arm']="${GIT_RELEASE_URL}/adelie-armv7-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['arm']="$(sha256sum "${ROOTFS_DIR}/adelie-arm-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/adelie-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/adelie-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
