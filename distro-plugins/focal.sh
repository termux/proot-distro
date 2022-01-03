# Added By MechaLabs
DISTRO_NAME="Ubuntu (focal)"

TARBALL_URL['aarch64']="https://mirrors.tuna.tsinghua.edu.cn/lxc-images/images/ubuntu/focal/arm64/default/20211227_08%3A52/rootfs.tar.xz"
TARBALL_SHA256['aarch64']="758ee8ec6ca58cd696a188e7710a07a442ca06be7a72c9df5266483cefe7ce77"
TARBALL_URL['arm']="https://mirrors.tuna.tsinghua.edu.cn/lxc-images/images/ubuntu/focal/armhf/default/20211226_21%3A24/rootfs.tar.xz"
TARBALL_SHA256['arm']="ac6e1381392af5d1f16823fac310c90efdd24ea80a58bc580e429d8cceabde13"
TARBALL_URL['x86_64']="https://mirrors.tuna.tsinghua.edu.cn/lxc-images/images/ubuntu/focal/amd64/default/20211228_07%3A42/rootfs.tar.xz"
TARBALL_SHA256['x86_64']="795eb7e3ebb0377375bfd65027ecbf28843a61343785b2d386e57db91b4ac6c9"

distro_setup() {
        # Apt Update && Full Upgrade
        run_proot_cmd apt-get update -y && apt-get full-upgrade -y
}
