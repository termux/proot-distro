# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Fedora"
DISTRO_COMMENT="Version 43. Broken on Android 15+."

TARBALL_URL['aarch64']="https://easycli.sh/proot-distro/fedora-aarch64-pd-v4.37.0.tar.xz"
TARBALL_SHA256['aarch64']="eb86202ef9887dc315e93c627bef3b6a825da871129ab3de91466ab2c2e06019"
TARBALL_URL['x86_64']="https://easycli.sh/proot-distro/fedora-x86_64-pd-v4.37.0.tar.xz"
TARBALL_SHA256['x86_64']="0daac2fe47dbfcdbcc89e8e92c7a59db4a3c78b3c226e4b4a04e6c2ec582bfd4"

distro_setup() {
	# Fix environment variables on login or su.
	run_proot_cmd authselect opt-out
	echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/system-auth
}
