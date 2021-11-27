# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu (impish)"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v2.8.0/ubuntu-aarch64-pd-v2.8.0.tar.xz"
TARBALL_SHA256['aarch64']="e0bb4a06215d39bb8ff3a4469bb69dd78babc39dad9c82cd01e4ac52eba8c7b6"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v2.8.0/ubuntu-arm-pd-v2.8.0.tar.xz"
TARBALL_SHA256['arm']="e985999097c3dd8692342867c6184b1a922dfee78a575ef25747da98b964a8c9"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v2.8.0/ubuntu-x86_64-pd-v2.8.0.tar.xz"
TARBALL_SHA256['x86_64']="0c8c19908df6eeae2d963fb00e1153c982b81386a480c16bfe683dfcdea404ec"

distro_setup() {
	# Don't update gvfs-daemons and udisks2
	run_proot_cmd apt-mark hold gvfs-daemons udisks2
}
