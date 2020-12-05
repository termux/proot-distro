##
## Plug-in for installing Debian 10 (Buster).
##

DISTRO_NAME="Debian 10 (Buster)"

# Returns download URL.
get_download_url() {
	local deb_arch

	case "$(uname -m)" in
		aarch64)
			deb_arch="arm64"
			;;
		armv7l|armv8l)
			deb_arch="armhf"
			;;
		i686)
			deb_arch="i386"
			;;
		x86_64)
			deb_arch="amd64"
			;;
	esac

	echo "https://github.com/termux/proot-distro/releases/download/v1.1-debian-rootfs/debian-buster-${deb_arch}-2020.12.05.tar.gz"
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
