DISTRO_NAME="Debian"
TARBALL_URL['aarch64']="https://github.com/BDhackers009/nethunter-rootfs-termux/releases/download/69.69/kalifs-arm64-minimal.tar.xz"
TARBALL_SHA256['aarch64']="ec849f556768b9d9bdb541a32760ad2c00d73bf3aff8436e203cadb33d14bea3"
TARBALL_URL['arm']="https://github.com/BDhackers009/nethunter-rootfs-termux/releases/download/69.69/kalifs-armhf-minimal.tar.xz"
TARBALL_SHA256['arm']="365c4483fbef46624381c2f34a4e49080f8930564ba984971b56b373012fe866"

distro_setup() {
        run_proot_cmd echo "deb [trusted=yes]  http://http.kali.org/kali kali-rolling main contrib non-free" > /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/kali/etc/apt/sources.list
        run_proot_cmd echo "#deb-src [trusted=yes]  http://http.kali.org/kali kali-rolling main non-free contrib"  >> /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/kali/etc/apt/sources.list
        run_proot_cmd echo "apt update && apt install gnupg dialog apt-utils -y && apt-key adv --keyserver  http://keyserver.ubuntu.com --recv-keys ED444FF07D8D0BF6"  > /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/kali/root/fix.sh	
}
