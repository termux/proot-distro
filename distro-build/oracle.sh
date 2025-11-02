dist_name="Oracle Linux"
dist_version="10"

bootstrap_distribution() {
  sudo rm -f "${ROOTFS_DIR}"/oraclelinux-*.tar.xz
	mkdir -p $GITHUB_WORKSPACE/rootfs
	curl --fail --location --output "$GITHUB_WORKSPACE/rootfs/oraclelinux-aarch64-pd-${CURRENT_VERSION}.tar.xz" "https://github.com/oracle/container-images/blob/dist-arm64v8/${dist_version}/oraclelinux-${dist_version}-arm64v8-rootfs.tar.xz"
	curl --fail --location --output "$GITHUB_WORKSPACE/rootfs/oraclelinux-x86_64-pd-${CURRENT_VERSION}.tar.xz" "https://github.com/oracle/container-images/blob/dist-amd64/${dist_version}/oraclelinux-${dist_version}-amd64-rootfs.tar.xz"
  mkdir -p $GITHUB_WORKSPACE/rootfs/oraclelinux-aarch64-pd-${CURRENT_VERSION}
	mkdir -p $GITHUB_WORKSPACE/rootfs/oraclelinux-x86_64-pd-${CURRENT_VERSION}
	tar -xJf $GITHUB_WORKSPACE/rootfs/oraclelinux-aarch64-pd-${CURRENT_VERSION}.tar.xz -C $GITHUB_WORKSPACE/rootfs/oraclelinux-aarch64-pd-${CURRENT_VERSION}
	tar -xJf $GITHUB_WORKSPACE/rootfs/oraclelinux-x86_64-pd-${CURRENT_VERSION}.tar.xz. -C $GITHUB_WORKSPACE/rootfs/oraclelinux-x86_64-pd-${CURRENT_VERSION}
	rm $GITHUB_WORKSPACE/rootfs/oraclelinux-aarch64-pd-${CURRENT_VERSION}.tar.xz
	rm $GITHUB_WORKSPACE/rootfs/oraclelinux-x86_64-pd-${CURRENT_VERSION}.tar.xz
	cd $GITHUB_WORKSPACE/rootfs
	sudo tar -cJf $GITHUB_WORKSPACE/rootfs/oraclelinux-aarch64-pd-${CURRENT_VERSION}.tar.xz oraclelinux-aarch64-pd-${CURRENT_VERSION}
	sudo tar -cJf $GITHUB_WORKSPACE/rootfs/oraclelinux-x86_64-pd-${CURRENT_VERSION}.tar.xz oraclelinux-x86_64-pd-${CURRENT_VERSION}
	
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/oracle.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Oracle Linux"
	DISTRO_COMMENT="Version ${dist_version}"

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/oraclelinux-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/oraclelinux-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/oraclelinux-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/oraclelinux-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
