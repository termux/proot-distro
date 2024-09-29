dist_name="Fedora"
dist_version="40-1.14"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/fedora-*.tar.xz

	for arch in aarch64 x86_64; do
		curl --fail --location \
			--output "${WORKDIR}/Fedora-Container-Base-Generic.${arch}-${dist_version}.oci.tar.xz" \
			"https://mirror.de.leaseweb.net/fedora/linux/releases/${dist_version%%-*}/Container/${arch}/images/Fedora-Container-Base-Generic.${arch}-${dist_version}.oci.tar.xz"
		sudo rm -rf "${WORKDIR}/fedora-tmp" "${WORKDIR}/fedora-$(translate_arch "$arch")"
		mkdir "${WORKDIR}/fedora-tmp"
		tar -C "${WORKDIR}/fedora-tmp" -Jxf "${WORKDIR}/Fedora-Container-Base-Generic.${arch}-${dist_version}.oci.tar.xz"
		oci_manifest=$(jq -r '.manifests[0].digest' "${WORKDIR}/fedora-tmp"/index.json | cut -d ':' -f 2)
		oci_layers=$(jq -r '.layers[].digest' "${WORKDIR}/fedora-tmp/blobs/sha256/${oci_manifest}" | cut -d ':' -f 2)

		sudo mkdir -m 755 "${WORKDIR}/fedora-$(translate_arch "$arch")"
		for layer in ${oci_layers}; do
			sudo tar -zxp --acls --xattrs --xattrs-include='*' \
				-f "${WORKDIR}/fedora-tmp/blobs/sha256/${layer}" \
				-C "${WORKDIR}/fedora-$(translate_arch "$arch")"
		done
		sudo rm -rf "${WORKDIR}/fedora-tmp"

		cat <<- EOF | sudo unshare -mpf bash -e -
		rm -f "${WORKDIR}/fedora-$(translate_arch "$arch")/etc/resolv.conf"
		echo "nameserver 1.1.1.1" > "${WORKDIR}/fedora-$(translate_arch "$arch")/etc/resolv.conf"
		echo "excludepkgs=*selinux*" >> "${WORKDIR}/fedora-$(translate_arch "$arch")/etc/dnf/dnf.conf"
		mount --bind /dev "${WORKDIR}/fedora-$(translate_arch "$arch")/dev"
		mount --bind /proc "${WORKDIR}/fedora-$(translate_arch "$arch")/proc"
		mount --bind /sys "${WORKDIR}/fedora-$(translate_arch "$arch")/sys"
		chroot "${WORKDIR}/fedora-$(translate_arch "$arch")" yum upgrade -y
		chroot "${WORKDIR}/fedora-$(translate_arch "$arch")" yum install -y passwd util-linux
		chroot "${WORKDIR}/fedora-$(translate_arch "$arch")" yum clean all
		chmod 4755 "${WORKDIR}/fedora-$(translate_arch "$arch")"/usr/bin/sudo
		EOF

		archive_rootfs "${ROOTFS_DIR}/fedora-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			"fedora-$(translate_arch "$arch")"
	done
	unset arch
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/fedora.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Fedora"
	DISTRO_COMMENT="Version ${dist_version%%-*}."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/fedora-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/fedora-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/fedora-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/fedora-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"

	distro_setup() {
	${TAB}# Fix environment variables on login or su.
	${TAB}run_proot_cmd authselect opt-out
	${TAB}echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/system-auth
	}
	EOF
}
