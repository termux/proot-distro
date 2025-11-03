dist_name="Devuan"

# Put only current stable version here!
dist_version="excalibur"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/devuan-"${dist_version}"-*.tar.xz

	for arch in arm64 armhf i386 amd64; do
		sudo rm -rf "${WORKDIR}/devuan-${dist_version}-$(translate_arch "$arch")"
		sudo mmdebstrap \
			--architectures=${arch} \
			--variant=minbase \
			--components="main,contrib" \
			--include="ca-certificates,locales" \
			--format=directory \
			"${dist_version}" \
			"${WORKDIR}/devuan-${dist_version}-$(translate_arch "$arch")" \
			http://deb.devuan.org/merged
		archive_rootfs "${ROOTFS_DIR}/devuan-${dist_version}-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			"devuan-${dist_version}-$(translate_arch "$arch")"
	done
	unset arch
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/devuan.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Devuan (${dist_version})"
	DISTRO_COMMENT="Stable release."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/devuan-${dist_version}-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/devuan-${dist_version}-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['arm']="${GIT_RELEASE_URL}/devuan-${dist_version}-arm-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['arm']="$(sha256sum "${ROOTFS_DIR}/devuan-${dist_version}-arm-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['i686']="${GIT_RELEASE_URL}/devuan-${dist_version}-i686-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['i686']="$(sha256sum "${ROOTFS_DIR}/devuan-${dist_version}-i686-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/devuan-${dist_version}-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/devuan-${dist_version}-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"

	distro_setup() {
	${TAB}# Configure en_US.UTF-8 locale.
	${TAB}sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	${TAB}run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
	${TAB}curl -L -o /tmp/devuankey.deb http://deb.devuan.org/merged/pool/DEVUAN/main/d/devuan-keyring/devuan-keyring_2025.08.09_all.deb
    ${TAB}apt install -yq /tmp/devuankey.deb
	}

	EOF
}
