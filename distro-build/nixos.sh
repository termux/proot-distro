dist_name="NixOS"
dist_version="22.05"

bootstrap_distribution() {
	for arch in aarch64 i686 x86_64; do
		curl --fail --location \
			--output "${WORKDIR}/nixos-system-${arch}-linux.tar.xz" \
			"https://hydra.nixos.org/job/nixos/release-${dist_version}/nixos.containerTarball.${arch}-linux/latest/download/1"

		sudo mkdir -m 755 "${WORKDIR}/nixos-$(translate_arch "$arch")"
		sudo tar -Jxp \
			-f "${WORKDIR}/nixos-system-${arch}-linux.tar.xz" \
			-C "${WORKDIR}/nixos-$(translate_arch "$arch")"

		system_dir=$(find "${WORKDIR}/nixos-$(translate_arch "$arch")/nix/store" -name "*nixos-system-nixos-${dist_version}.*")

		cat <<- EOF | sudo unshare -mpf bash -e -
		mount --bind /dev "${WORKDIR}/nixos-$(translate_arch "$arch")/dev"
		mount --bind /proc "${WORKDIR}/nixos-$(translate_arch "$arch")/proc"
		mount --bind /sys "${WORKDIR}/nixos-$(translate_arch "$arch")/sys"
		mkdir "${WORKDIR}/nixos-$(translate_arch "$arch")/etc"
		chroot "${WORKDIR}/nixos-$(translate_arch "$arch")" ${system_dir#"${WORKDIR}/nixos-$(translate_arch "$arch")"}/activate
		ln -s "$system_dir/sw/bin/su" "${WORKDIR}/nixos-$(translate_arch "$arch")/bin/su"
		EOF

		sudo tar -J -c \
			-f "${ROOTFS_DIR}/nixos-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			-C "$WORKDIR" \
			"nixos-$(translate_arch "$arch")"
		sudo chown $(id -un):$(id -gn) "${ROOTFS_DIR}/nixos-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz"
	done
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/nixos.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="NixOS (${dist_version})"

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/nixos-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/nixos-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['i686']="${GIT_RELEASE_URL}/nixos-i686-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['i686']="$(sha256sum "${ROOTFS_DIR}/nixos-i686-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/ni-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/nixos-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
