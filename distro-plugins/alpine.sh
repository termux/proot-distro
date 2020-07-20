##
## Plug-in for installing Alpine Linux.
##

DISTRO_NAME="Alpine Linux 3.12.0"

# Returns download URL.
get_download_url() {
	case "$(uname -m)" in
		aarch64)
			echo "http://dl-cdn.alpinelinux.org/alpine/v3.12/releases/aarch64/alpine-minirootfs-3.12.0-aarch64.tar.gz"
			;;
		armv7l|armv8l)
			echo "http://dl-cdn.alpinelinux.org/alpine/v3.12/releases/armv7/alpine-minirootfs-3.12.0-armv7.tar.gz"
			;;
		i686)
			echo "http://dl-cdn.alpinelinux.org/alpine/v3.12/releases/x86/alpine-minirootfs-3.12.0-x86.tar.gz"
			;;
		x86_64)
			echo "http://dl-cdn.alpinelinux.org/alpine/v3.12/releases/x86_64/alpine-minirootfs-3.12.0-x86_64.tar.gz"
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
	# Uncomment this to run 'apk upgrade' during installation.
	#run_proot_cmd apk upgrade
	:
}
