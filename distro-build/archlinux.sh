dist_name="Arch Linux"
dist_version="2023.03.01"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/archlinux-*.tar.xz

	for arch in aarch64 armv7; do
		curl --fail --location \
			--output "${WORKDIR}/archlinux-${arch}.tar.gz" \
			"http://os.archlinuxarm.org/os/ArchLinuxARM-${arch}-latest.tar.gz"

		sudo rm -rf "${WORKDIR}/archlinux-$(translate_arch "$arch")"
		sudo mkdir -m 755 "${WORKDIR}/archlinux-$(translate_arch "$arch")"
		sudo tar -zxp --acls --xattrs --xattrs-include='*' \
			-f "${WORKDIR}/archlinux-${arch}.tar.gz" \
			-C "${WORKDIR}/archlinux-$(translate_arch "$arch")"

		cat <<- EOF | sudo unshare -mpf bash -e -
		rm -f "${WORKDIR}/archlinux-$(translate_arch "$arch")/etc/resolv.conf"
		echo "nameserver 1.1.1.1" > "${WORKDIR}/archlinux-$(translate_arch "$arch")/etc/resolv.conf"
		mount --bind "${WORKDIR}/archlinux-$(translate_arch "$arch")/" "${WORKDIR}/archlinux-$(translate_arch "$arch")/"
		mount --bind /dev "${WORKDIR}/archlinux-$(translate_arch "$arch")/dev"
		mount --bind /proc "${WORKDIR}/archlinux-$(translate_arch "$arch")/proc"
		mount --bind /sys "${WORKDIR}/archlinux-$(translate_arch "$arch")/sys"
		chroot "${WORKDIR}/archlinux-$(translate_arch "$arch")" pacman-key --init
		chroot "${WORKDIR}/archlinux-$(translate_arch "$arch")" pacman-key --populate archlinuxarm
		if [ "$arch" = "aarch64" ]; then
			chroot "${WORKDIR}/archlinux-$(translate_arch "$arch")" pacman -Rnsc --noconfirm linux-aarch64
		else
			chroot "${WORKDIR}/archlinux-$(translate_arch "$arch")" pacman -Rnsc --noconfirm linux-armv7
		fi
		chroot "${WORKDIR}/archlinux-$(translate_arch "$arch")" pacman -Syu --noconfirm
		EOF

		sudo rm -f "${WORKDIR:?}/archlinux-$(translate_arch "$arch")"/var/cache/pacman/pkg/* || true

		archive_rootfs "${ROOTFS_DIR}/archlinux-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			"archlinux-$(translate_arch "$arch")"
	done
	unset arch

	# Don't build x86(64) for now as there is an issue with keyring.
	return 0

	curl --fail --location \
		--output "${WORKDIR}/archlinux-x86_64.tar.gz" \
		"https://mirror.rackspace.com/archlinux/iso/${dist_version}/archlinux-bootstrap-${dist_version}-x86_64.tar.gz"

	sudo mkdir -m 755 "${WORKDIR}/archlinux-bootstrap"
	sudo tar -zxp --strip-components=1 --acls --xattrs --xattrs-include='*' \
		-f "${WORKDIR}/archlinux-x86_64.tar.gz" \
		-C "${WORKDIR}/archlinux-bootstrap"

	cat <<- EOF | sudo unshare -mpf bash -e -
	rm -f "${WORKDIR}/archlinux-bootstrap/etc/resolv.conf"
	echo "nameserver 1.1.1.1" > "${WORKDIR}/archlinux-bootstrap/etc/resolv.conf"
	mount --bind "${WORKDIR}/archlinux-bootstrap/" "${WORKDIR}/archlinux-bootstrap/"
	mount --bind /dev "${WORKDIR}/archlinux-bootstrap/dev"
	mount --bind /proc "${WORKDIR}/archlinux-bootstrap/proc"
	mount --bind /sys "${WORKDIR}/archlinux-bootstrap/sys"
	mkdir "${WORKDIR}/archlinux-bootstrap/archlinux-i686"
	mkdir "${WORKDIR}/archlinux-bootstrap/archlinux-x86_64"
	echo 'Server = http://mirror.rackspace.com/archlinux/\$repo/os/\$arch' > \
		"${WORKDIR}/archlinux-bootstrap/etc/pacman.d/mirrorlist"
	chroot "${WORKDIR}/archlinux-bootstrap" pacstrap -K /archlinux-x86_64 base
	# chroot "${WORKDIR}/archlinux-bootstrap" pacman -Scc --noconfirm
	sed -i 's|Architecture = auto|Architecture = i686|' \
		"${WORKDIR}/archlinux-bootstrap/etc/pacman.conf"
	sed -i 's|Required DatabaseOptional|Never|' \
		"${WORKDIR}/archlinux-bootstrap/etc/pacman.conf"
	echo 'Server = https://de.mirror.archlinux32.org/\$arch/\$repo' > \
		"${WORKDIR}/archlinux-bootstrap/etc/pacman.d/mirrorlist"
	chroot "${WORKDIR}/archlinux-bootstrap" pacstrap -K /archlinux-i686 base
	EOF

	for arch in i686 x86_64; do
		sudo rm -f "${WORKDIR:?}/archlinux-bootstrap/archlinux-${arch}"/var/cache/pacman/pkg/* || true
		archive_rootfs "${ROOTFS_DIR}/archlinux-${arch}-pd-${CURRENT_VERSION}.tar.xz" \
			"archlinux-${arch}"
	done
	unset arch
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/archlinux.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Arch Linux"
	DISTRO_COMMENT="This is Arch Linux ARM project, not original Arch."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/archlinux-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/archlinux-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['arm']="${GIT_RELEASE_URL}/archlinux-arm-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['arm']="$(sha256sum "${ROOTFS_DIR}/archlinux-arm-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"

	distro_setup() {
	${TAB}# Fix environment variables on login or su.
	${TAB}local f
	${TAB}for f in su su-l system-local-login system-remote-login; do
	${TAB}${TAB}echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"\${f}"
	${TAB}done

	${TAB}# Configure en_US.UTF-8 locale.
	${TAB}sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	${TAB}run_proot_cmd locale-gen
	}
	EOF
}
