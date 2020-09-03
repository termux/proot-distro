##
## Plug-in for installing Ubuntu Bionic.
##

DISTRO_NAME="Ubuntu 18.04"

# Returns download URL.
get_download_url() {
	case "$(uname -m)" in
		aarch64)
			echo "https://partner-images.canonical.com/core/bionic/current/ubuntu-bionic-core-cloudimg-arm64-root.tar.gz"
			;;
		armv7l|armv8l)
			echo "https://partner-images.canonical.com/core/bionic/current/ubuntu-bionic-core-cloudimg-armhf-root.tar.gz"
			;;
		i686)
			echo "https://partner-images.canonical.com/core/bionic/current/ubuntu-bionic-core-cloudimg-i386-root.tar.gz"
			;;
		x86_64)
			echo "https://partner-images.canonical.com/core/bionic/current/ubuntu-bionic-core-cloudimg-amd64-root.tar.gz"
			;;
	esac
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
