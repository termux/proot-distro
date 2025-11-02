dist_name="AlmaLinux"
dist_version="10"

bootstrap_distribution() {
    sudo rm -f "${ROOTFS_DIR}"/almalinux-*.tar.xz
	mkdir -p $GITHUB_WORKSPACE/rootfs
	curl --fail --location --output "$GITHUB_WORKSPACE/rootfs/almalinux-aarch64-pd-${CURRENT_VERSION}.tar.xz" "https://github.com/AlmaLinux/container-images/raw/refs/heads/${dist_version}/default/arm64/almalinux-${dist_version}-default-arm64.tar.xz"
	curl --fail --location --output "$GITHUB_WORKSPACE/rootfs/almalinux-x86_64-pd-${CURRENT_VERSION}.tar.xz" "https://github.com/AlmaLinux/container-images/raw/refs/heads/${dist_version}/default/amd64/almalinux-${dist_version}-default-amd64.tar.xz"
    mkdir -p $GITHUB_WORKSPACE/rootfs/almalinux-aarch64-pd-${CURRENT_VERSION}
	mkdir -p $GITHUB_WORKSPACE/rootfs/almalinux-x86_64-pd-${CURRENT_VERSION}
	tar -xJf $GITHUB_WORKSPACE/rootfs/almalinux-aarch64-pd-${CURRENT_VERSION}.tar.xz -C $GITHUB_WORKSPACE/rootfs/almalinux-aarch64-pd-${CURRENT_VERSION}
	tar -xJf $GITHUB_WORKSPACE/rootfs/almalinux-x86_64-pd-${CURRENT_VERSION}.tar.xz -C $GITHUB_WORKSPACE/rootfs/almalinux-x86_64-pd-${CURRENT_VERSION}
	rm $GITHUB_WORKSPACE/rootfs/almalinux-aarch64-pd-${CURRENT_VERSION}.tar.xz
	rm $GITHUB_WORKSPACE/rootfs/almalinux-x86_64-pd-${CURRENT_VERSION}.tar.xz
	cd $GITHUB_WORKSPACE/rootfs
	sudo tar -cJf $GITHUB_WORKSPACE/rootfs/almalinux-aarch64-pd-${CURRENT_VERSION}.tar.xz almalinux-aarch64-pd-${CURRENT_VERSION}
	sudo tar -cJf $GITHUB_WORKSPACE/rootfs/almalinux-x86_64-pd-${CURRENT_VERSION}.tar.xz almalinux-x86_64-pd-${CURRENT_VERSION}
	
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/almalinux.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="AlmaLinux"
	DISTRO_COMMENT="Version ${dist_version}"

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/almalinux-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/almalinux-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/almalinux-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/almalinux-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
