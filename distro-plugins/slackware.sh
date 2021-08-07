##
## Plug-in for installing Slackware Arm
##

DISTRO_NAME="Slackware"

# You can override a CPU architecture to let distribution
# be executed by QEMU (user-mode).
#
# You can specify the following values here:
#
#  * aarch64: AArch64 (ARM64, 64bit ARM)
#  * armv7l:  ARM (32bit)
#  * i686:    x86 (32bit)
#  * x86_64:  x86 (64bit)
#
# Default value is set by proot-distro script and is equal
# to the CPU architecture of your device (uname -m).
#DISTRO_ARCH=$(uname -m)

# Returns download URL and SHA-256 of file in this format:
# SHA-256|FILE-NAME
get_download_url() {
        local rootfs
        local sha256
        
        case "$DISTRO_ARCH" in
                aarch64)
                        rootfs="ftp://ftp.arm.slackware.com/slackwarearm/slackwarearm-devtools/minirootfs/roots/slackarm-14.2-miniroot_01Jul16.tar.xz"
                        sha256="ce9533c6920f621e23f4e379a2f7c92568807187ac88e93cbd53f9ecee2d7899"
                        ;;
                armv7l|armv8l)
                        rootfs="ftp://ftp.arm.slackware.com/slackwarearm/slackwarearm-devtools/minirootfs/roots/slackarm-14.2-miniroot_01Jul16.tar.xz"
                        sha256="ce9533c6920f621e23f4e379a2f7c92568807187ac88e93cbd53f9ecee2d7899"
                        ;;
                i686)
                        # Slackware does not provide tarballs for x86 32bit.
                        return
                        ;;
                x86_64)
                        # Slackware does not provide tarballs for x86 64bit.
                        return
                        ;;
        esac

        echo "${sha256}|${rootfs}"
}

# Define here additional steps which should be executed
# for configuration.
distro_setup() {
        # Hint: $PWD is the distribution rootfs directory.
        #echo "hello world" > ./etc/motd

        # Run command within proot'ed environment with
        # run_proot_cmd function.
        # Uncomment this to do system upgrade during installation.
        #run_proot_cmd slackpkg update
        #run_proot_cmd slackpkg upgrade
        :
}
