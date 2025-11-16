dist_name="Trisquel"

# Put only current stable version here!
dist_version="aramo"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/trisquel-"${dist_version}"-*.tar.xz

	for arch in i386 arm64 armhf amd64; do
		sudo rm -rf "${WORKDIR}/trisquel-${dist_version}-$(translate_arch "$arch")"
		sudo mmdebstrap \
			--architectures=${arch} \
			--variant=apt \
			--components="main" \
			--include="ca-certificates,locales,trisquel-keyring,software-properties-common,passwd" \
			--format=directory \
			"${dist_version}" \
			"${WORKDIR}/trisquel-${dist_version}-$(translate_arch "$arch")" \
	        "deb http://archive.trisquel.org/trisquel ${dist_version} main" \
            "deb http://archive.trisquel.org/trisquel ${dist_version}-updates main" \
            "deb http://archive.trisquel.org/trisquel ${dist_version}-security main" \
            "deb http://archive.trisquel.org/trisquel ${dist_version}-backports main"
		archive_rootfs "${ROOTFS_DIR}/trisquel-${dist_version}-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			"trisquel-${dist_version}-$(translate_arch "$arch")"
	done
	unset arch
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/trisquel.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Trisquel GNU/Linux Libre ${dist_version}"
	DISTRO_COMMENT="Stable release ${dist_version}"

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/trisquel-${dist_version}-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/trisquel-${dist_version}-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['arm']="${GIT_RELEASE_URL}/trisquel-${dist_version}-arm-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['arm']="$(sha256sum "${ROOTFS_DIR}/trisquel-${dist_version}-arm-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['i686']="${GIT_RELEASE_URL}/trisquel-${dist_version}-i686-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['i686']="$(sha256sum "${ROOTFS_DIR}/trisquel-${dist_version}-i686-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/trisquel-${dist_version}-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/trisquel-${dist_version}-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"

	distro_setup() {
	${TAB}# Configure en_US.UTF-8 locale.
	${TAB}sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	${TAB}run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
	}

	EOF
}
