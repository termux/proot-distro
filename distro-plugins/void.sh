# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Void Linux"

TARBALL_URL['aarch64']="https://easycli.sh/proot-distro/void-aarch64-pd-v4.29.0.tar.xz"
TARBALL_SHA256['aarch64']="7a7c449b3efe504749e40f556d13812010bccc930a820a56973a0f5fc2f16997"
TARBALL_URL['arm']="https://easycli.sh/proot-distro/void-arm-pd-v4.29.0.tar.xz"
TARBALL_SHA256['arm']="5cb87c0ca8ee91047f3634789314920be6d914ce4f196157cb3949706ce18d03"
TARBALL_URL['i686']="https://easycli.sh/proot-distro/void-i686-pd-v4.29.0.tar.xz"
TARBALL_SHA256['i686']="0ad014426c1e0dc7a0cfe8175157b28b2a8cb75b83d7f44b7bbc35420125a269"
TARBALL_URL['x86_64']="https://easycli.sh/proot-distro/void-x86_64-pd-v4.29.0.tar.xz"
TARBALL_SHA256['x86_64']="2853b9433b9051aa2512e7376a71736196fb3241eb90ba11110c6e867854c666"

distro_setup() {
	# Set default shell to bash.
	run_proot_cmd usermod --shell /bin/bash root
	# Fix issue where come CA certificates links may not be created.
	run_proot_cmd update-ca-certificates --fresh
}
