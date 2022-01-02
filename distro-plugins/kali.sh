DISTRO_NAME="Kali Linux (nethunter)"
TARBALL_URL['aarch64']="https://kali.download/nethunter-images/current/rootfs/kalifs-arm64-nano.tar.xz"
TARBALL_SHA256['aarch64']="520ba187d3f20883f76d032b5a0ae6d369de55e7115d67d3ad57298b4d7687ec"
TARBALL_URL['arm']="https://kali.download/nethunter-images/current/rootfs/kalifs-armhf-nano.tar.xz"
TARBALL_SHA256['arm']="d7b0b8b91b84a502c44a320c691551a727472657d060af3973517fbadddda7b8"

distro_setup() {
        run_proot_cmd echo "deb [trusted=yes]  http://http.kali.org/kali kali-rolling main contrib non-free" > /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/kali/etc/apt/sources.list
        run_proot_cmd echo "#deb-src [trusted=yes]  http://http.kali.org/kali kali-rolling main non-free contrib"  >> /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/kali/etc/apt/sources.list
        run_proot_cmd echo "alias setup-kali='apt update && apt install sudo gnupg curl locales-all dialog apt-utils -y && curl https://raw.githubusercontent.com/BDhackers009/nethunter-rootfs-termux/main/gpg.key -o gpg.key && apt-key add gpg.key && rm gpg.key'"  >> /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/kali/root/.bashrc 
}
