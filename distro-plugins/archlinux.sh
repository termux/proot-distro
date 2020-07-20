##
## Plug-in for installing Arch Linux (original and ARM variants).
##

DISTRO_NAME="Arch Linux"

# x86_64 rootfs is inside subdirectory.
if [ "$(uname -m)" = "x86_64" ]; then
	DISTRO_TARBALL_STRIP_OPT=1
fi

# Returns download URL.
get_download_url() {
	case "$(uname -m)" in
		aarch64)
			echo "https://eu.mirror.archlinuxarm.org/os/ArchLinuxARM-aarch64-latest.tar.gz"
			;;
		armv7l|armv8l)
			echo "https://eu.mirror.archlinuxarm.org/os/ArchLinuxARM-armv7-latest.tar.gz"
			;;
		x86_64)
			# File name of x86_64 tarball is not persistent, so generate URL in hacky way.
			local file_name
			file_name=$(curl --fail --silent "https://mirror.rackspace.com/archlinux/iso/latest/md5sums.txt" | grep bootstrap | awk '{ print $2 }')
			if [ -n "$file_name" ]; then
				echo "http://mirror.rackspace.com/archlinux/iso/latest/${file_name}"
			fi
			;;
	esac
}

# Define here additional steps which should be executed
# for configuration.
distro_setup() {
	# DNS resolver.
	rm -f ./etc/resolv.conf
	cat <<- EOF > ./etc/resolv.conf
	nameserver 1.1.1.1
	nameserver 1.0.0.1
	EOF

	# Pacman keyring initialization.
	run_proot_cmd pacman-key --init
	run_proot_cmd pacman-key --populate archlinux
}
