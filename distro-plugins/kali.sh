DISTRO_NAME="Kali Linux (nethunter)"
TARBALL_URL['aarch64']="https://github.com/BDhackers009/nethunter-rootfs-termux/releases/download/69.69/kalifs-armhf-minimal.tar.xz"
TARBALL_SHA256['aarch64']="bcd311ae53fc6576881e5f8cbbc007fc7c49a5348894e03fa97cc2397ef24fff"
TARBALL_URL['arm']="https://github.com/BDhackers009/nethunter-rootfs-termux/releases/download/69.69/kalifs-armhf-minimal.tar.xz"
TARBALL_SHA256['arm']="365c4483fbef46624381c2f34a4e49080f8930564ba984971b56b373012fe866"

distro_setup() {
        run_proot_cmd echo "deb [trusted=yes]  http://http.kali.org/kali kali-rolling main contrib non-free" > /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/kali/etc/apt/sources.list
        run_proot_cmd echo "#deb-src [trusted=yes]  http://http.kali.org/kali kali-rolling main non-free contrib"  >> /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/kali/etc/apt/sources.list
        run_proot_cmd echo "alias setup-kali='apt update && apt install sudo gnupg curl locales-all dialog apt-utils -y && curl https://raw.githubusercontent.com/BDhackers009/nethunter-rootfs-termux/main/gpg.key -o gpg.key && apt-key add gpg.key && rm gpg.key'"  >> /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/kali/root/.bashrc 
}
