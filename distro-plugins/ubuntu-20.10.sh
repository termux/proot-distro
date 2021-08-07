##
## Plug-in for installing Ubuntu Groovy.
##

DISTRO_NAME="Ubuntu 20.10"

# You can override a CPU architecture to let distribution
# be executed by QEMU (user-mode).
#
# You can specify the following values here:
#
#  * aarch64: AArch64 (ARM64, 64bit ARM)
#  * armv7l:  ARM (32bit)
#  * i686:    x86 (32bit)
#  * x86_64:  x86 (64bit)
#
# Default value is set by proot-distro script and is equal
# to the CPU architecture of your device (uname -m).
#DISTRO_ARCH=$(uname -m)

# Returns download URL and SHA-256 of file in this format:
# SHA-256|FILE-NAME
get_download_url() {
	local rootfs
	local sha256

	case "$DISTRO_ARCH" in
		aarch64)
			rootfs="https://cdimage.ubuntu.com/ubuntu-base/releases/20.10/release/ubuntu-base-20.10-base-arm64.tar.gz"
			sha256="8b6043eb3a5e4b59f2535a760601a1f599360fb8669dc2af5dc08cb4ad0ffe0a"
			;;
		armv7l|armv8l)
			rootfs="https://cdimage.ubuntu.com/ubuntu-base/releases/20.10/release/ubuntu-base-20.10-base-armhf.tar.gz"
			sha256="bcdec3ebca1144e2beb3feabd442c31ceafd3d488a61c3be6ec9c54774b6c3df"
			;;
		i686)
			# Ubuntu Groovy does not provide tarballs for x86 32bit.
			return
			;;
		x86_64)
			rootfs="https://cdimage.ubuntu.com/ubuntu-base/releases/20.10/release/ubuntu-base-20.10-base-amd64.tar.gz"
			sha256="a2989a03b141083bfb2843069819e0781e212000efad0925888ac16f69249840"
			;;
	esac

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
	:
  }
