# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu (20.04)"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v2.2.0/ubuntu-aarch64-pd-v2.2.0.tar.xz"
TARBALL_SHA256['aarch64']="486de37668963c1b6a0d131e33b91486be8eb1919b0813ad03726885753feba6"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v2.2.0/ubuntu-arm-pd-v2.2.0.tar.xz"
TARBALL_SHA256['arm']="b29f6d89c5d19056b297125a1a8222f641579bd2c70d2174ac91080100ec634a"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v2.2.0/ubuntu-x86_64-pd-v2.2.0.tar.xz"
TARBALL_SHA256['x86_64']="799b2b1eaf8c2a97a57663a7a0acc568ab2714dedbd5c35b6f465f0aa9a5c407"

distro_setup() {
	# Don't update gvfs-daemons and udisks2
	run_proot_cmd apt-mark hold gvfs-daemons udisks2
}
