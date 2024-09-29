# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Fedora"
DISTRO_COMMENT="Version 40."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.15.0/fedora-aarch64-pd-v4.15.0.tar.xz"
TARBALL_SHA256['aarch64']="0aa0da1860db99a26b2ecb3996e1c39020395e882658c3e33ea5d748811b3271"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.15.0/fedora-x86_64-pd-v4.15.0.tar.xz"
TARBALL_SHA256['x86_64']="78948fba2ba86734eb0080fdd78cb1292902df5073241e30e18313e73bfe3841"

distro_setup() {
	# Fix environment variables on login or su.
	run_proot_cmd authselect opt-out
	echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/system-auth
}
