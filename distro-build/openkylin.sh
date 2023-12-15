dist_name="openkylin"
dist_version="yangtze"

bootstrap_distribution() {
	for arch in amd64 arm64; do
		echo -e "[General]\n\
		arch=$arch\n\
		directory=${WORKDIR}/openkylin-$(translate_arch "$arch")/\n\
		cleanup=true\n\
		noauth=false\n\
		unpack=true\n\
		explicitsuite=false\n\
		multiarch=\n\
		aptsources=Debian\n\
		bootstrap=openKylin\n\
		[openKylin]\n\
		packages=apt ca-certificates passwd locales-all\n\
		source=http://archive.build.openkylin.top/openkylin/\n\
		keyring=openkylin-keyring\n\
		suite=${dist_version}\n\
		" >/tmp/${dist_version}.multistrap
		sudo multistrap -f /tmp/${dist_version}.multistrap

		rm -f "${WORKDIR}"/openkylin-$(translate_arch "$arch")/etc/resolv.conf
		echo "nameserver 1.1.1.1" > "${WORKDIR}"/openkylin-$(translate_arch "$arch")/etc/resolv.conf
		echo "en_US.UTF-8 UTF-8" > "${WORKDIR}"/openkylin-$(translate_arch "$arch")/etc/locale.gen
		echo "deb http://archive.build.openkylin.top/openkylin ${dist_version} main" > ${WORKDIR}/openkylin-$(translate_arch "$arch")/etc/apt/sources.list
		echo "deb http://archive.build.openkylin.top/openkylin yangtze-security main" >> ${WORKDIR}/openkylin-$(translate_arch "$arch")/etc/apt/sources.list
		echo "deb http://archive.build.openkylin.top/openkylin yangtze-updates main" >> ${WORKDIR}/openkylin-$(translate_arch "$arch")/etc/apt/sources.list
		echo "deb http://archive.build.openkylin.top/openkylin yangtze-proposed main" >> ${WORKDIR}/openkylin-$(translate_arch "$arch")/etc/apt/sources.list

		sudo tar -J -c \
			-f "${ROOTFS_DIR}/openkylin-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			-C "$WORKDIR" \
			"openkylin-$(translate_arch "$arch")"
		sudo chown $(id -un):$(id -gn) "${ROOTFS_DIR}/openkylin-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz"
	done
	unset arch
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/openkylin.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="openKylin"
	DISTRO_COMMENT="A stable release (${dist_version})."
	TARBALL_URL['arm64']="${GIT_RELEASE_URL}/openkylin-arm64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['arm64']="$(sha256sum "${ROOTFS_DIR}/openkylin-arm64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/openkylin-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/openkylin-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}