# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Fedora"
DISTRO_COMMENT="Version 39. Supports only 64-bit CPUs."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.6.0/fedora-aarch64-pd-v4.6.0.tar.xz"
TARBALL_SHA256['aarch64']="920caf3290ddaf9347de51ccadb0b6391c0244286072a6664fb1600eee360b9c"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.6.0/fedora-x86_64-pd-v4.6.0.tar.xz"
TARBALL_SHA256['x86_64']="49ffa79c24db6a2ee664b2e29268e534c11e1a984b694f8c56551ddb12dde8b3"

distro_setup() {
	# Fix environment variables on login or su.
	run_proot_cmd authselect opt-out
	echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/system-auth
}
