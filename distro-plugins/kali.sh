DISTRO_NAME="Kali Linux (nethunter)"
TARBALL_URL['aarch64']="https://pro.bandor69.workers.dev/6:/kalifs-arm64-nano.tar.xz"
TARBALL_SHA256['aarch64']="d8eaf35d6ea0c7a851b79a1e98c850fa117a6c7ce9f703f20a503f7761cd8a3d"
TARBALL_URL['arm']="https://pro.bandor69.workers.dev/6:/kalifs-armhf-nano.tar.xz"
TARBALL_SHA256['arm']="a3b953c700ed4170be90dc055ed420b31c173623617fc66cf5e17f6a4ea02624"
TARBALL_SHA256["x86_64"]="096290b7229ab81f1ac3b35324a7109dc19f1e2f5bf6aab1ff8254ebc95463ea"
TARBALL_URL["x86_64"]="https://pro.bandor69.workers.dev/6:/kalifs-amd64-minimal.tar.xz"
TARBALL_SHA256["i686"]="e83cd8f57d6128efd64e88b191a1653ff315fffd78c05d536d2b6f63b2e6d49d"
TARBALL_URL["i686"]="https://pro.bandor69.workers.dev/6:/kalifs-i386-minimal.tar.xz"
distro_setup() {
	run_proot_cmd chsh -s /usr/bin/bash
	run_proot_cmd echo "$(getprop persist.sys.timezone)" > /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/kali/etc/timezone
	#run_proot_cmd echo "if [ $(cat /etc/sudoers | grep $USER) = '' ]; then echo '${USER}	ALL=(ALL:ALL) ALL' >> /etc/sudoers;fi" >> /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/kali/home/${USER}/.bashrc
	#run_proot_cmd echo "if [ ! -f /root/.hushlogin ]; then clear && touch /root/.hushlogin && source /root/.bashrc && clear; fi" >> /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/kali/root/.bashrc
	run_proot_cmd rm -rf /usr/bin/kali-motd
	run_proot_cmd echo "deb https://kali.download/kali kali-rolling main contrib  non-free" > /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/kali/etc/apt/sources.list
	#run_proot_cmd sed -i "s/8192/819/1" /usr/sbin/apache2ctl
}
