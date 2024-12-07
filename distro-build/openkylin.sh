dist_name="openKylin"
dist_version="nile"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/openkylin-*.tar.xz

	curl -LO "http://archive.build.openkylin.top/openkylin/pool/main/o/openkylin-keyring/openkylin-keyring_2022.05.12-ok1_all.deb"
	sudo dpkg -i openkylin-keyring_2022.05.12-ok1_all.deb
	rm -f openkylin-keyring_2022.05.12-ok1_all.deb

	for arch in amd64 arm64; do
		sudo rm -rf "${WORKDIR}/openkylin-$(translate_arch "$arch")"
		sudo mkdir -m 755 "${WORKDIR}/openkylin-$(translate_arch "$arch")"
		sudo mmdebstrap \
			--architectures=${arch} \
			--variant=minbase \
			--components="main,commercial,community" \
			--include="apt,ca-certificates,passwd,locales-all" \
			--format=directory \
			"${dist_version}" \
			"${WORKDIR}/openkylin-$(translate_arch "$arch")" \
			"http://archive.build.openkylin.top/openkylin/"

		cat <<- EOF | sudo unshare -mpf bash -e -
		rm -f "${WORKDIR}"/openkylin-$(translate_arch "$arch")/etc/resolv.conf
		echo "nameserver 1.1.1.1" > "${WORKDIR}"/openkylin-$(translate_arch "$arch")/etc/resolv.conf
		echo "en_US.UTF-8 UTF-8" > "${WORKDIR}"/openkylin-$(translate_arch "$arch")/etc/locale.gen
  		mount -t proc none "${WORKDIR}/openkylin-$(translate_arch "$arch")/proc"  
        	mount --bind /sys "${WORKDIR}/openkylin-$(translate_arch "$arch")/sys"  
        	mount --bind /dev "${WORKDIR}/openkylin-$(translate_arch "$arch")/dev"
	 	echo "deb http://archive.build.openkylin.top/openkylin/ nile main cross pty" > "${WORKDIR}"/openkylin-$(translate_arch "$arch")/etc/apt/sources.list
		echo deb http://archive.build.openkylin.top/openkylin/ nile-security main cross pty >> "${WORKDIR}"/openkylin-$(translate_arch "$arch")/etc/apt/sources.list
                echo deb http://archive.build.openkylin.top/openkylin/ nile-updates main cross pty >> "${WORKDIR}"/openkylin-$(translate_arch "$arch")/etc/apt/sources.list
                echo deb http://archive.build.openkylin.top/openkylin/ nile-proposed main cross pty >> "${WORKDIR}"/openkylin-$(translate_arch "$arch")/etc/apt/sources.list
		EOF

		archive_rootfs "${ROOTFS_DIR}/openkylin-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			"openkylin-$(translate_arch "$arch")"
	done
	unset arch
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/openkylin.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="OpenKylin"
	DISTRO_COMMENT="Version '${dist_version}'."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/openkylin-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/openkylin-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/openkylin-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/openkylin-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
