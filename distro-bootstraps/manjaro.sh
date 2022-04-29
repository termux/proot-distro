dist_name="Manjaro (aarch64)"

bootstrap_distribution() {
	curl --fail --location \
		--output "${WORKDIR}/manjaro-aarch64.tar.xz" \
		"https://osdn.net/projects/manjaro-arm/storage/.rootfs/Manjaro-ARM-aarch64-latest.tar.gz"

	mkdir "${WORKDIR}/manjaro-aarch64"
	sudo tar -xp \
		-f "${WORKDIR}/manjaro-aarch64.tar.xz" \
		-C "${WORKDIR}/manjaro-aarch64"

	cat <<- EOF | sudo unshare -mpf bash -e -
	rm -f "${WORKDIR}/manjaro-aarch64/etc/resolv.conf"
	echo "nameserver 1.1.1.1" > "${WORKDIR}/manjaro-aarch64/etc/resolv.conf"
	mount --bind "${WORKDIR}/manjaro-aarch64/" "${WORKDIR}/manjaro-aarch64/"
	mount --bind /dev "${WORKDIR}/manjaro-aarch64/dev"
	mount --bind /proc "${WORKDIR}/manjaro-aarch64/proc"
	mount --bind /sys "${WORKDIR}/manjaro-aarch64/sys"
	chroot "${WORKDIR}/manjaro-aarch64" pacman-key --init
	chroot "${WORKDIR}/manjaro-aarch64" pacman-key --populate manjaro
	chroot "${WORKDIR}/manjaro-aarch64" pacman-key --populate archlinuxarm
	chroot "${WORKDIR}/manjaro-aarch64" pacman-key --populate archlinux
	chroot "${WORKDIR}/manjaro-aarch64" pacman-mirrors -c poland
	chroot "${WORKDIR}/manjaro-aarch64" pacman -Syu --noconfirm
	chroot "${WORKDIR}/manjaro-aarch64" pacman -S --noconfirm util-linux
	EOF

	sudo rm -f "${WORKDIR:?}"/manjaro-aarch64/var/cache/pacman/pkg/* || true

	sudo tar -Jcf "${ROOTFS_DIR}/manjaro-aarch64-pd-${CURRENT_VERSION}.tar.xz" \
		-C "${WORKDIR}/manjaro-aarch64" ./
	sudo chown $(id -un):$(id -gn) "${ROOTFS_DIR}/manjaro-aarch64-pd-${CURRENT_VERSION}.tar.xz"
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/manjaro-aarch64.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Manjaro AArch64"
	DISTRO_COMMENT="Only for AArch64 hosts."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/manjaro-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/manjaro-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
