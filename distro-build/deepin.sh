dist_name="deepin"
dist_version="beige"

bootstrap_distribution() {
	for arch in amd64 arm64; do
		mkdir -p ${WORKDIR}/deepin-$(translate_arch "$arch")/etc/apt/trusted.gpg.d
		curl --fail --location \
			--output "${WORKDIR}/deepin-$(translate_arch "$arch")/etc/apt/trusted.gpg.d/deepin.gpg" \
			"https://github.com/deepin-community/deepin-rootfs/raw/master/deepin.gpg"

		echo -e "[General]\n\
		arch=$arch\n\
		directory=${WORKDIR}/deepin-$(translate_arch "$arch")/\n\
		cleanup=true\n\
		noauth=false\n\
		unpack=true\n\
		explicitsuite=false\n\
		multiarch=\n\
		aptsources=Debian\n\
		bootstrap=Deepin\n\
		[Deepin]\n\
		packages=apt ca-certificates passwd locales-all\n\
		source=https://community-packages.deepin.com/${dist_version}/\n\
		suite=${dist_version}\n\
		" >/tmp/${dist_version}.multistrap
		sudo multistrap -f /tmp/${dist_version}.multistrap

		rm -f "${WORKDIR}"/deepin-$(translate_arch "$arch")/etc/resolv.conf
		echo "nameserver 1.1.1.1" > "${WORKDIR}"/deepin-$(translate_arch "$arch")/etc/resolv.conf
		echo "en_US.UTF-8 UTF-8" > "${WORKDIR}"/deepin-$(translate_arch "$arch")/etc/locale.gen
		echo "deb     https://community-packages.deepin.com/${dist_version}/ ${dist_version} main commercial community" > ${WORKDIR}/deepin-$(translate_arch "$arch")/etc/apt/sources.list
		echo "deb-src https://community-packages.deepin.com/${dist_version}/ ${dist_version} main commercial community" >> ${WORKDIR}/deepin-$(translate_arch "$arch")/etc/apt/sources.list

		sudo tar -J -c \
			-f "${ROOTFS_DIR}/deepin-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			-C "$WORKDIR" \
			"deepin-$(translate_arch "$arch")"
		sudo chown $(id -un):$(id -gn) "${ROOTFS_DIR}/deepin-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz"
	done
	unset arch
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/deepin.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="deepin"
	DISTRO_COMMENT="Supports only 64-bit CPUs."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/deepin-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/deepin-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/deepin-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/deepin-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
