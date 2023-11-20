dist_name="EndeavourOS"
timestamp="20231006"

bootstrap_distribution() {
	curl --fail --location \
		--output "${WORKDIR}/endeavouros-aarch64.tar.zst" \
		"https://github.com/endeavouros-arm/images/releases/download/rootfs-pbp-${timestamp}/enosLinuxARM-pbp-latest.tar.zst"

	sudo mkdir -m 755 "${WORKDIR}/endeavouros-aarch64-bootstrap"
	sudo mkdir -m 755 "${WORKDIR}/endeavouros-aarch64"
	sudo tar --zstd -xpf "${WORKDIR}/endeavouros-aarch64.tar.zst" \
		-C "${WORKDIR}/endeavouros-aarch64-bootstrap"

	cat <<- EOF | sudo unshare -mpf bash -e -
	rm -f "${WORKDIR}/endeavouros-aarch64-bootstrap/etc/resolv.conf"
	echo "nameserver 1.1.1.1" > "${WORKDIR}/endeavouros-aarch64-bootstrap/etc/resolv.conf"
	mount --bind "${WORKDIR}/endeavouros-aarch64-bootstrap/" "${WORKDIR}/endeavouros-aarch64-bootstrap/"
	mount --bind /dev "${WORKDIR}/endeavouros-aarch64-bootstrap/dev"
	mount --bind /proc "${WORKDIR}/endeavouros-aarch64-bootstrap/proc"
	mount --bind /sys "${WORKDIR}/endeavouros-aarch64-bootstrap/sys"
	mkdir "${WORKDIR}/endeavouros-aarch64-bootstrap/endeavouros-aarch64"
	mount --bind "${WORKDIR}/endeavouros-aarch64" "${WORKDIR}/endeavouros-aarch64-bootstrap/endeavouros-aarch64"
	chroot "${WORKDIR}/endeavouros-aarch64-bootstrap" pacman-key --init
	chroot "${WORKDIR}/endeavouros-aarch64-bootstrap" pacman-key --populate archlinux archlinuxarm endeavouros
	chroot "${WORKDIR}/endeavouros-aarch64-bootstrap" pacstrap /endeavouros-aarch64 base
	rm -f "${WORKDIR}/endeavouros-aarch64-bootstrap/endeavouros-aarch64/etc/resolv.conf"
	echo "nameserver 1.1.1.1" > "${WORKDIR}/endeavouros-aarch64-bootstrap/endeavouros-aarch64/etc/resolv.conf"
	EOF

	sudo tar -J -c \
		-f "${ROOTFS_DIR}/endeavouros-aarch64-pd-${CURRENT_VERSION}.tar.xz" \
		-C "$WORKDIR" \
		"endeavouros-aarch64"
	sudo chown $(id -un):$(id -gn) "${ROOTFS_DIR}/endeavouros-aarch64-pd-${CURRENT_VERSION}.tar.xz"
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/endeavour.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="EndeavourOS"
	DISTRO_COMMENT="Currently available only for AArch64."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/endeavouros-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/endeavouros-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"

	distro_setup() {
	${TAB}# Fix environment variables on login or su.
	${TAB}local f
	${TAB}for f in su su-l system-local-login system-remote-login; do
	${TAB}${TAB}echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"\${f}"
	${TAB}done
	}
	EOF
}
