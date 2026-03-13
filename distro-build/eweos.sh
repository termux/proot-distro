dist_name="eweOS"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/eweos-*.tar.xz

	for arch in aarch64 riscv64 x86_64; do
		curl --fail --location \
			--output "${WORKDIR}/eweos-${arch}.tar.xz" \
			"https://os-repo-auto.ewe.moe/eweos-images/eweos-${arch}-tarball.tar.xz"

		sudo rm -rf "${WORKDIR}/eweos-$(translate_arch "$arch")"
		sudo mkdir -m 755 "${WORKDIR}/eweos-$(translate_arch "$arch")"
		sudo tar -Jxp --acls --xattrs --xattrs-include='*' \
			-f "${WORKDIR}/eweos-${arch}.tar.xz" \
			-C "${WORKDIR}/eweos-$(translate_arch "$arch")"

		cat <<- EOF | sudo unshare -mpf bash -e -
		rm -f "${WORKDIR}/eweos-$(translate_arch "$arch")/etc/resolv.conf"
		echo "nameserver 1.1.1.1" > "${WORKDIR}/eweos-$(translate_arch "$arch")/etc/resolv.conf"
		mount --bind "${WORKDIR}/eweos-$(translate_arch "$arch")/" "${WORKDIR}/eweos-$(translate_arch "$arch")/"
		mount --bind /dev "${WORKDIR}/eweos-$(translate_arch "$arch")/dev"
		mount --bind /proc "${WORKDIR}/eweos-$(translate_arch "$arch")/proc"
		mount --bind /sys "${WORKDIR}/eweos-$(translate_arch "$arch")/sys"
		chroot "${WORKDIR}/eweos-$(translate_arch "$arch")" pacman -Syu --noconfirm
		sed -i '/^\[options\]/a DisableSandbox' "${WORKDIR}/eweos-$(translate_arch "$arch")/etc/pacman.conf"
		EOF

		sudo rm -f "${WORKDIR:?}/eweos-$(translate_arch "$arch")/var/cache/pacman/pkg/*" || true

		archive_rootfs "${ROOTFS_DIR}/eweos-$(translate_arch "$arch")-pd.tar.xz" \
			"eweos-$(translate_arch "$arch")"
	done
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/eweos.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="eweOS"

	TARBALL_URL['aarch64']="${ROOTFS_FILESERVER_URL}/eweos-aarch64-pd.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/eweos-aarch64-pd.tar.xz" | awk '{ print $1 }')"
	TARBALL_URL['riscv64']="${ROOTFS_FILESERVER_URL}/eweos-riscv64-pd.tar.xz"
	TARBALL_SHA256['riscv64']="$(sha256sum "${ROOTFS_DIR}/eweos-riscv64-pd.tar.xz" | awk '{ print $1 }')"
	TARBALL_URL['x86_64']="${ROOTFS_FILESERVER_URL}/eweos-x86_64-pd.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/eweos-x86_64-pd.tar.xz" | awk '{ print $1 }')"
	EOF
}
