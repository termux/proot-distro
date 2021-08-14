# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Debian (stable)"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v1.10.1/debian-aarch64-pd-v1.10.1.tar.xz"
TARBALL_SHA256['aarch64']="f34802fbb300b4d088a638c638683fd2bfc1c03f4b40fa4cb7d2113231401a21"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v1.10.1/debian-arm-pd-v1.10.1.tar.xz"
TARBALL_SHA256['arm']="a73a61024fc3b75d4d3facbc19b9ab716ddf2bbb011bf5d1269e02f3ca27634f"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v1.10.1/debian-i686-pd-v1.10.1.tar.xz"
TARBALL_SHA256['i686']="dbc6801e73fc7e568633067ff0a29f8cfd6454afd06a6e1ce104a6ab7c984b67"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v1.10.1/debian-x86_64-pd-v1.10.1.tar.xz"
TARBALL_SHA256['x86_64']="d0386073900115c3c4f7b9e858b2773b988ba6bc4be40d99231ee324a1207e44"

distro_setup() {
	# Include security & updates.
	cat <<- EOF > ./etc/apt/sources.list
	deb https://deb.debian.org/debian stable main contrib
	deb https://deb.debian.org/debian-security/ stable/updates main contrib
	deb https://deb.debian.org/debian stable-updates main contrib
	EOF

	# Don't update gvfs-daemons and udisks2
	run_proot_cmd apt-mark hold gvfs-daemons udisks2
}
