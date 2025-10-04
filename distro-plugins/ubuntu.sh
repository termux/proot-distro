# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu (25.04)"
DISTRO_COMMENT="Regular release (plucky)."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.29.0/ubuntu-plucky-aarch64-pd-v4.29.0.tar.xz"
TARBALL_SHA256['aarch64']="63cee3aecc0473785ef761ec1127387ed2abbea0b26d74e5187601568fbb335f"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.29.0/ubuntu-plucky-arm-pd-v4.29.0.tar.xz"
TARBALL_SHA256['arm']="f6abc8042c5331392058dc2ec8c296b6d3c93419cb007649c34e35a170901fbb"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.29.0/ubuntu-plucky-x86_64-pd-v4.29.0.tar.xz"
TARBALL_SHA256['x86_64']="fcac0b71a98524e1dd10a3b1fe6753b8e85716b98207940169fe01bbd21b1538"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales

	# Configure Mozilla PPA.
	echo "Configuring PPA repository for Firefox and Thunderbird..."
	run_proot_cmd add-apt-repository --yes --no-update ppa:mozillateam/ppa || true
	cat <<- CONFIG_EOF > ./etc/apt/preferences.d/pin-mozilla-ppa
	Package: *
	Pin: release o=LP-PPA-mozillateam
	Pin-Priority: 9999
	CONFIG_EOF
}
