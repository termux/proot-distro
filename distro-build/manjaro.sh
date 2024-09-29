dist_name="Manjaro"
dist_version="20240520"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/manjaro-*.tar.xz

	curl --fail --location \
		--output "${WORKDIR}/manjaro-aarch64.tar.xz" \
		"https://github.com/manjaro-arm/rootfs/releases/download/${dist_version}/Manjaro-ARM-aarch64-latest.tar.gz"

	sudo rm -rf "${WORKDIR}/manjaro-aarch64"
	sudo mkdir -m 755 "${WORKDIR}/manjaro-aarch64"
	sudo tar -xp --acls --xattrs --xattrs-include='*' \
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
	chroot "${WORKDIR}/manjaro-aarch64" pacman-mirrors -c poland
	chroot "${WORKDIR}/manjaro-aarch64" pacman -Syu --noconfirm
	chroot "${WORKDIR}/manjaro-aarch64" pacman -S --noconfirm util-linux
	EOF

	sudo rm -f "${WORKDIR:?}"/manjaro-aarch64/var/cache/pacman/pkg/* || true

	archive_rootfs "${ROOTFS_DIR}/manjaro-aarch64-pd-${CURRENT_VERSION}.tar.xz" \
		"manjaro-aarch64"
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/manjaro.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Manjaro"
	DISTRO_COMMENT="Manjaro ARM64 port."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/manjaro-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/manjaro-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"

	distro_setup() {
	${TAB}# Fix environment variables on login or su.
	${TAB}local f
	${TAB}for f in su su-l system-local-login system-remote-login; do
	${TAB}${TAB}echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"\${f}"
	${TAB}done
	}
	EOF
}
