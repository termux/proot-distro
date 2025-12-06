dist_name="openSUSE"
dist_version="16.0"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/opensuse-*.tar.xz

	opensuse_manifest=$(docker manifest inspect opensuse/leap:"${dist_version}")
	for arch in arm64 amd64; do
		digest=$(
			echo "$opensuse_manifest" | \
			jq -r ".manifests[]" | \
			jq -r "select(.platform.architecture == \"${arch}\")" | \
			jq -r ".digest"
		)

		docker pull "opensuse/leap@${digest}"
		docker export --output "${WORKDIR}/opensuse-dump-${arch}.tar" \
			$(docker create "opensuse/leap@${digest}")

		sudo rm -rf "${WORKDIR}/opensuse-$(translate_arch "$arch")"
		sudo mkdir -m 755 "${WORKDIR}/opensuse-$(translate_arch "$arch")"
		sudo tar -xpf "${WORKDIR}/opensuse-dump-${arch}.tar" \
			-C "${WORKDIR}/opensuse-$(translate_arch "$arch")"

		cat <<- EOF | sudo unshare -mpf bash -e -
		rm -f "${WORKDIR}/opensuse-$(translate_arch "$arch")/etc/resolv.conf"
		echo "nameserver 1.1.1.1" > "${WORKDIR}/opensuse-$(translate_arch "$arch")/etc/resolv.conf"
		sed -i -E 's/^(rpm\.install\.excludedocs)/# \1/g' "${WORKDIR}/opensuse-$(translate_arch "$arch")/etc/zypp/zypp.conf"
		mount --bind /dev "${WORKDIR}/opensuse-$(translate_arch "$arch")/dev"
		mount --bind /proc "${WORKDIR}/opensuse-$(translate_arch "$arch")/proc"
		mount --bind /sys "${WORKDIR}/opensuse-$(translate_arch "$arch")/sys"
		chroot "${WORKDIR}/opensuse-$(translate_arch "$arch")" zypper refresh && zypper dup --no-confirm
		chroot "${WORKDIR}/opensuse-$(translate_arch "$arch")" rpm -qa --qf '%{NAME} ' | xargs -n 1 | grep -Pv '(filesystem|gpg-pubkey)' > /tmp/opensuse-pkgs.txt
		cat /tmp/opensuse-pkgs.txt | xargs chroot "${WORKDIR}/opensuse-$(translate_arch "$arch")" zypper install --no-confirm --force
		chroot "${WORKDIR}/opensuse-$(translate_arch "$arch")" zypper install --no-confirm util-linux
		chroot "${WORKDIR}/opensuse-$(translate_arch "$arch")" zypper clean -a
		EOF
		sudo rm -f /tmp/opensuse-pkgs.txt

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
	DISTRO_COMMENT="Leap release (${dist_version}). No support for ARM and x86 32bit."

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
