##
## Plug-in for installing Ubuntu Bionic with i386 emulation via QEMU.
##

DISTRO_NAME="Ubuntu 18.04 QEMU"
DISTRO_COMMENT="QEMU emulation of Ubuntu 18.04 on i386 arch. Will be slower compared to native distro!"

# Append custom parameters before executing proot
declare -a DISTRO_PROOT_PARAMS=("-p" "--qemu=`which qemu-i386`")

# Returns download URL and SHA-256 of file in this format:
# SHA-256|FILE-NAME
get_download_url() {
	local rootfs
	local sha256

	rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-ubuntu-bionic-rootfs/ubuntu-bionic-core-cloudimg-i386-root-2020.12.10.tar.gz"
	sha256="32356912ec3a3c4c2ac19c95107bd7dc01657ab0fd9ef86e7e29dcc167b4eed4"

	echo "${sha256}|${rootfs}"
}

# Define here additional steps which should be executed
# for configuration.
distro_setup() {
	# Hint: $PWD is the distribution rootfs directory.
	#echo "hello world" > ./etc/motd

	# Run command within proot'ed environment with
	# run_proot_cmd function.
	# Uncomment this to do system upgrade during installation.
	#run_proot_cmd apt update
	#run_proot_cmd apt upgrade -yq
	run_proot_cmd echo "Testing qemu ..."
	run_proot_cmd uname -a
	:
}
