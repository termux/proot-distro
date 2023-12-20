# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Void Linux"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.6.0/void-aarch64-pd-v4.6.0.tar.xz"
TARBALL_SHA256['aarch64']="423c73d0b3767477da5d763f01dfb1a8e5f8148468bcb0c86ca365a15dfeadc1"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.6.0/void-arm-pd-v4.6.0.tar.xz"
TARBALL_SHA256['arm']="728af450f28e4a562c8f7f57890aa0417b749ab5766c1107a9b57f075781f141"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.6.0/void-i686-pd-v4.6.0.tar.xz"
TARBALL_SHA256['i686']="8fa3b582ebf6c06603b975f1f7a95bac0d0c971ce79caae4c68fd9b9dc39fd1e"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.6.0/void-x86_64-pd-v4.6.0.tar.xz"
TARBALL_SHA256['x86_64']="12deb4ca4d9bfc7e612c8a4f4f6b719d9f6ab258c54db600aea31ab24e61a140"

distro_setup() {
	# Set default shell to bash.
	run_proot_cmd usermod --shell /bin/bash root
	# Fix issue where come CA certificates links may not be created.
	run_proot_cmd update-ca-certificates --fresh
}
