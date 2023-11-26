dist_name="deepin"
dist_version="beige"

bootstrap_distribution() {
	for arch in amd64 arm64; do
		mkdir -p ${WORKDIR}/deepin-${arch}/etc/apt/trusted.gpg.d
		curl --fail --location \
		--output "${WORKDIR}/deepin-${arch}/etc/apt/trusted.gpg.d/deepin.gpg" \
		"https://github.com/deepin-community/deepin-rootfs/raw/master/deepin.gpg"

		echo -e "[General]\n\
		arch=$arch\n\
		directory=${WORKDIR}/deepin-${arch}/\n\
		cleanup=true\n\
		noauth=false\n\
		unpack=true\n\
		explicitsuite=false\n\
		multiarch=\n\
		aptsources=Debian\n\
		bootstrap=Deepin\n\
		[Deepin]\n\
		packages=apt ca-certificates passwd locales-all\n\
		source=https://community-packages.deepin.com/beige/\n\
		suite=beige\n\
		" >/tmp/beige.multistrap
		sudo multistrap -f /tmp/beige.multistrap

		cat <<- EOF | sudo unshare -mpf bash -e -
		echo "deb     https://community-packages.deepin.com/beige/ beige main commercial community" > ${WORKDIR}/deepin-${arch}/etc/apt/sources.list && \
		echo "deb-src https://community-packages.deepin.com/beige/ beige main commercial community" >> ${WORKDIR}/deepin-${arch}/etc/apt/sources.list
		echo "en_US.UTF-8 UTF-8" > ${WORKDIR}/deepin-${arch}/etc/locale.gen
		EOF

		sudo tar -cf ${ROOTFS_DIR}/deepin-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar -C ${WORKDIR}/deepin-${arch} .
		sudo chown $(id -un):$(id -gn) "${ROOTFS_DIR}/deepin-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar"
		xz "${ROOTFS_DIR}/deepin-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar"
	done
	unset arch
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/deepin.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="deepin"
	DISTRO_COMMENT="Currently available only AArch64 and x86_64 ports."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/deepin-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/deepin-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/deepin-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/deepin-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
