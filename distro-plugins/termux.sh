# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Termux"
DISTRO_TYPE="termux"
DISTRO_COMMENT="Official Termux environment sandbox."

TARBALL_URL['aarch64']="https://github.com/termux/termux-packages/releases/download/bootstrap-2025.11.23-r1%2Bapt.android-7/bootstrap-aarch64.zip"
TARBALL_SHA256['aarch64']="bec3e2b674b6efee7ff0e2a12824eb376e3fe182cc424d3357dad72c7cdd20d5"
TARBALL_URL['arm']="https://github.com/termux/termux-packages/releases/download/bootstrap-2025.11.23-r1%2Bapt.android-7/bootstrap-arm.zip"
TARBALL_SHA256['arm']="8c0487ed2e9a5a43af8347646b93641ed64939c532136cd2bc8df57eed5430b0"
TARBALL_URL['i686']="https://github.com/termux/termux-packages/releases/download/bootstrap-2025.11.23-r1%2Bapt.android-7/bootstrap-i686.zip"
TARBALL_SHA256['i686']="043df622bc3ce19583a18d1ec89f78ed990d0a0297d24e631141817e6a17a31c"
TARBALL_URL['x86_64']="https://github.com/termux/termux-packages/releases/download/bootstrap-2025.11.23-r1%2Bapt.android-7/bootstrap-x86_64.zip"
TARBALL_SHA256['x86_64']="8b36eafb6bf25ae32dd1646ddd5fe5b614510b68509df4eecf5a3e66409fc7f6"

distro_setup() {
	# Run bootstrap second stage if exist.
	if [ -e ./data/data/com.termux/files/usr/etc/termux/termux-bootstrap/second-stage/termux-bootstrap-second-stage.sh ]; then
		run_proot_cmd bash ./data/data/com.termux/files/usr/etc/termux/termux-bootstrap/second-stage/termux-bootstrap-second-stage.sh
	fi
}
