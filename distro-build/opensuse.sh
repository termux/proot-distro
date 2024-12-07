dist_name="openSUSE"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/opensuse-*.tar.xz

	opensuse_manifest=$(docker manifest inspect opensuse/tumbleweed:latest)
	for arch in arm64 arm 386 amd64; do
		if [ "$arch" = "arm" ]; then
			digest=$(
				echo "$opensuse_manifest" | \
				jq -r ".manifests[]" | \
				jq -r "select(.platform.architecture == \"${arch}\")" | \
				jq -r "select(.platform.variant == \"v7\")" | \
				jq -r ".digest"
			)
		else
			digest=$(
				echo "$opensuse_manifest" | \
				jq -r ".manifests[]" | \
				jq -r "select(.platform.architecture == \"${arch}\")" | \
				jq -r ".digest"
			)
		fi

		docker pull "opensuse/tumbleweed@${digest}"
		docker save --output "${WORKDIR}/opensuse-image-dump-${arch}.tar" \
			"opensuse/tumbleweed@${digest}"

		sudo rm -rf "${WORKDIR}/opensuse-tmp" "${WORKDIR}/opensuse-$(translate_arch "$arch")"
		mkdir "${WORKDIR}/opensuse-tmp"
		sudo mkdir -m 755 "${WORKDIR}/opensuse-$(translate_arch "$arch")"
		sudo tar -x --strip-components=1 \
			-f "${WORKDIR}/opensuse-image-dump-${arch}.tar" \
			-C "${WORKDIR}/opensuse-tmp"
		sudo tar -xpf "${WORKDIR}/opensuse-tmp"/layer.tar \
			-C "${WORKDIR}/opensuse-$(translate_arch "$arch")"
		sudo rm -rf "${WORKDIR}/opensuse-tmp"

		cat <<- EOF | sudo unshare -mpf bash -e -
		rm -f "${WORKDIR}/opensuse-$(translate_arch "$arch")/etc/resolv.conf"
		echo "nameserver 1.1.1.1" > "${WORKDIR}/opensuse-$(translate_arch "$arch")/etc/resolv.conf"
		mount --bind /dev "${WORKDIR}/opensuse-$(translate_arch "$arch")/dev"
		mount --bind /proc "${WORKDIR}/opensuse-$(translate_arch "$arch")/proc"
		mount --bind /sys "${WORKDIR}/opensuse-$(translate_arch "$arch")/sys"
		chroot "${WORKDIR}/opensuse-$(translate_arch "$arch")" zypper dup || true
		chroot "${WORKDIR}/opensuse-$(translate_arch "$arch")" zypper install --no-confirm util-linux || true
		EOF

		archive_rootfs "${ROOTFS_DIR}/opensuse-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			"opensuse-$(translate_arch "$arch")"
	done
	unset opensuse_manifest
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/opensuse.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="OpenSUSE"
	DISTRO_COMMENT="Rolling release (Tumbleweed)."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/opensuse-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/opensuse-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['arm']="${GIT_RELEASE_URL}/opensuse-arm-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['arm']="$(sha256sum "${ROOTFS_DIR}/opensuse-arm-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['i686']="${GIT_RELEASE_URL}/opensuse-i686-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['i686']="$(sha256sum "${ROOTFS_DIR}/opensuse-i686-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/opensuse-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/opensuse-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"

	distro_setup() {
	${TAB}# Lock package filesystem to remove issues regarding zypper dup
	${TAB}run_proot_cmd zypper al filesystem
	}
	EOF
}
