# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu (25.10)"
DISTRO_COMMENT="Regular release (questing)."

TARBALL_URL['aarch64']="https://easycli.sh/proot-distro/ubuntu-questing-aarch64-pd-v4.30.1.tar.xz"
TARBALL_SHA256['aarch64']="5ab35b90cd9a9f180656261ba400a135c4c01c2da4b74522118342f985c2d328"
TARBALL_URL['arm']="https://easycli.sh/proot-distro/ubuntu-questing-arm-pd-v4.30.1.tar.xz"
TARBALL_SHA256['arm']="b074efe535b565f426219f20b35af0c4a7b3d0bc18ebd4fa11ccbd7370315b53"
TARBALL_URL['x86_64']="https://easycli.sh/proot-distro/ubuntu-questing-x86_64-pd-v4.30.1.tar.xz"
TARBALL_SHA256['x86_64']="74f7c8492a2f3e720d5aa89de6572cbb90b14c4b21dee87ab33416b6fb1088c3"

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
