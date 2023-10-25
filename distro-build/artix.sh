dist_name="Artix Linux"
dist_version="2023.10.25"

bootstrap_distribution() {
	curl --fail --location \
		--output "${WORKDIR}/artix-aarch64.tar.xz" \
			"https://armtixlinux.org/images/armtix-runit-20230401.tar.xz"

	sudo mkdir -m 755 "${WORKDIR}/artix-aarch64"
	sudo tar -Jxpf "${WORKDIR}/artix-aarch64.tar.xz" \
		-C "${WORKDIR}/artix-aarch64"

	cat <<- EOF | sudo unshare -mpf bash -e -
	rm -f "${WORKDIR}/artix-aarch64/etc/resolv.conf"
	echo "nameserver 1.1.1.1" > "${WORKDIR}/artix-aarch64/etc/resolv.conf"
	mount --bind "${WORKDIR}/artix-aarch64/" "${WORKDIR}/artix-aarch64/"
	mount --bind /dev "${WORKDIR}/artix-aarch64/dev"
	mount --bind /proc "${WORKDIR}/artix-aarch64/proc"
	mount --bind /sys "${WORKDIR}/artix-aarch64/sys"
	chroot "${WORKDIR}/artix-aarch64" pacman -Rnsc --noconfirm linux-aarch64
	EOF

	sudo rm -f "${WORKDIR:?}/artix-aarch64"/var/cache/pacman/pkg/* || true

	sudo tar -J -c \
		-f "${ROOTFS_DIR}/artix-aarch64-pd-${CURRENT_VERSION}.tar.xz" \
		-C "$WORKDIR" \
		"artix-aarch64"
	sudo chown $(id -un):$(id -gn) "${ROOTFS_DIR}/artix-aarch64-pd-${CURRENT_VERSION}.tar.xz"
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/artix.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Artix Linux"
	DISTRO_COMMENT="Currently available only for AArch64."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/artix-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/artix-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
