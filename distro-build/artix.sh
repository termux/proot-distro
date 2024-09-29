dist_name="Artix Linux"
dist_version="2023.10.31"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/artix-*.tar.xz

	curl --fail --location \
		--output "${WORKDIR}/artix-aarch64.tar.xz" \
			"https://armtixlinux.org/images/armtix-runit-20231031.tar.xz"

	sudo rm -rf "${WORKDIR}/artix-aarch64"
	sudo mkdir -m 755 "${WORKDIR}/artix-aarch64"
	sudo tar -Jxp --acls --xattrs --xattrs-include='*' \
		-f "${WORKDIR}/artix-aarch64.tar.xz" \
		-C "${WORKDIR}/artix-aarch64"

	cat <<- EOF | sudo unshare -mpf bash -e -
	rm -f "${WORKDIR}/artix-aarch64/etc/resolv.conf"
	echo "nameserver 1.1.1.1" > "${WORKDIR}/artix-aarch64/etc/resolv.conf"
	mount --bind "${WORKDIR}/artix-aarch64/" "${WORKDIR}/artix-aarch64/"
	mount --bind /dev "${WORKDIR}/artix-aarch64/dev"
	mount --bind /proc "${WORKDIR}/artix-aarch64/proc"
	mount --bind /sys "${WORKDIR}/artix-aarch64/sys"
	chroot "${WORKDIR}/artix-aarch64" pacman -Rnsc --noconfirm linux-aarch64 linux-aarch64-lts linux-firmware
	chroot "${WORKDIR}/artix-aarch64" pacman -Syu --noconfirm
	EOF

	sudo rm -f "${WORKDIR:?}/artix-aarch64"/var/cache/pacman/pkg/* || true

	archive_rootfs "${ROOTFS_DIR}/artix-aarch64-pd-${CURRENT_VERSION}.tar.xz" \
		"artix-aarch64"
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/artix.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Artix Linux"

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/artix-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/artix-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"

	distro_setup() {
	${TAB}# Fix environment variables on login or su.
	${TAB}local f
	${TAB}for f in su su-l system-local-login system-remote-login; do
	${TAB}${TAB}echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"\${f}"
	${TAB}done
	}
	EOF
}
