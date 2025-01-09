# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu (24.04)"
DISTRO_COMMENT="LTS release (noble)."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.18.0/ubuntu-noble-aarch64-pd-v4.18.0.tar.xz"
TARBALL_SHA256['aarch64']="91acaa786b8e2fbba56a9fd0f8a1188cee482b5c7baeed707b29ddaa9a294daa"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.18.0/ubuntu-noble-arm-pd-v4.18.0.tar.xz"
TARBALL_SHA256['arm']="2afb7e1ff17983fa2cf4c57edeea6be427ffb0359d8628b24a147b4c8aa276d5"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.18.0/ubuntu-noble-x86_64-pd-v4.18.0.tar.xz"
TARBALL_SHA256['x86_64']="3a769bce23985effb504140d43b6ddd73dac9e261d1932894afa31de81e45414"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales

	# Configure Firefox PPA.
	echo "Configuring PPA repository for Firefox..."
	run_proot_cmd add-apt-repository --yes ppa:mozillateam/firefox-next || true
	cat <<- CONFIG_EOF > ./etc/apt/preferences.d/pin-mozilla-ppa
	Package: *
	Pin: release o=LP-PPA-mozillateam-firefox-next
	Pin-Priority: 9999
	CONFIG_EOF

	# Configure Thunderbird PPA.
	echo "Configuring PPA repository for Thunderbird..."
	run_proot_cmd add-apt-repository --yes ppa:mozillateam/thunderbird-next || true
	cat <<- CONFIG_EOF > ./etc/apt/preferences.d/pin-thunderbird-ppa
	Package: *
	Pin: release o=LP-PPA-mozillateam-thunderbird-next
	Pin-Priority: 9999
	CONFIG_EOF
}
