# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Fedora"
DISTRO_COMMENT="Version 42. Broken on Android 15+."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.27.0/fedora-aarch64-pd-v4.27.0.tar.xz"
TARBALL_SHA256['aarch64']="55409069cbe314cb8e42f9509a89eb865b81a2010be88aa1ead27db29dbf03ee"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.27.0/fedora-x86_64-pd-v4.27.0.tar.xz"
TARBALL_SHA256['x86_64']="84389fcbf6a1aea6ab52a1b3909bfd84293b58937afacd6150164757558ad2b3"

distro_setup() {
	# Fix environment variables on login or su.
	run_proot_cmd authselect opt-out
	echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/system-auth
}
