dist_name="deepin"
dist_version="beige"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/deepin-*.tar.xz

	for arch in amd64 arm64; do
		sudo rm -rf "${WORKDIR}/deepin-$(translate_arch "$arch")"
		sudo mkdir -m 755 "${WORKDIR}/deepin-$(translate_arch "$arch")"

		curl --fail --location \
			--output "${WORKDIR}/deepin-keyring.gpg" \
			"https://github.com/deepin-community/deepin-rootfs/raw/master/deepin.gpg"

		sudo mmdebstrap \
			--hook-dir=/usr/share/mmdebstrap/hooks/merged-usr \
			--keyring "${WORKDIR}/deepin-keyring.gpg" \
			--architectures=${arch} \
			--variant=minbase \
			--components="main,commercial,community" \
			--include="apt,base-files,ca-certificates,passwd,locales-all" \
			--format=directory \
			"${dist_version}" \
			"${WORKDIR}/deepin-$(translate_arch "$arch")" \
			"https://community-packages.deepin.com/${dist_version}/"

		cat <<- EOF | sudo unshare -mpf bash -e -
		rm -f "${WORKDIR}"/deepin-$(translate_arch "$arch")/etc/resolv.conf
		echo "nameserver 1.1.1.1" > "${WORKDIR}"/deepin-$(translate_arch "$arch")/etc/resolv.conf
		echo "en_US.UTF-8 UTF-8" > "${WORKDIR}"/deepin-$(translate_arch "$arch")/etc/locale.gen
		#echo "deb     https://community-packages.deepin.com/${dist_version}/ ${dist_version} main commercial community" > ${WORKDIR}/deepin-$(translate_arch "$arch")/etc/apt/sources.list
		#echo "deb-src https://community-packages.deepin.com/${dist_version}/ ${dist_version} main commercial community" >> ${WORKDIR}/deepin-$(translate_arch "$arch")/etc/apt/sources.list
		mount --bind "${WORKDIR}/deepin-$(translate_arch "$arch")/" "${WORKDIR}/deepin-$(translate_arch "$arch")/"
		mount --bind /dev "${WORKDIR}/deepin-$(translate_arch "$arch")/dev"
		mount --bind /proc "${WORKDIR}/deepin-$(translate_arch "$arch")/proc"
		mount --bind /sys "${WORKDIR}/deepin-$(translate_arch "$arch")/sys"
		# configure packages in 2 runs to avoid dependency issues related to base-files and bash.
		env DEBIAN_FRONTEND=noninteractive DEBCONF_NONINTERACTIVE_SEEN=true LC_ALL=C LANGUAGE=C LANG=C chroot "${WORKDIR}/deepin-$(translate_arch "$arch")" dpkg --configure -a || true
		env DEBIAN_FRONTEND=noninteractive DEBCONF_NONINTERACTIVE_SEEN=true LC_ALL=C LANGUAGE=C LANG=C chroot "${WORKDIR}/deepin-$(translate_arch "$arch")" dpkg --configure -a
		EOF

		archive_rootfs "${ROOTFS_DIR}/deepin-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			"deepin-$(translate_arch "$arch")"
	done
	unset arch
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/deepin.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="deepin"

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/deepin-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/deepin-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/deepin-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/deepin-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
