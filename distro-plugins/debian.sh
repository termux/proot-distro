# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Debian (trixie)"
DISTRO_COMMENT="Stable release."

TARBALL_URL['aarch64']="https://easycli.sh/proot-distro/debian-trixie-aarch64-pd-v4.37.0.tar.xz"
TARBALL_SHA256['aarch64']="9bd3b19ff7cd300c7c7bf33124b726eb199f4bab9a3b1472f34749c6d12c9195"
TARBALL_URL['arm']="https://easycli.sh/proot-distro/debian-trixie-arm-pd-v4.37.0.tar.xz"
TARBALL_SHA256['arm']="af9b22fc1b82ccc665e484342af71c35a86f9f3dd525b0f423649976dded239f"
TARBALL_URL['i686']="https://easycli.sh/proot-distro/debian-trixie-i686-pd-v4.37.0.tar.xz"
TARBALL_SHA256['i686']="61f4c3b55d5defc1e9885efbe3b78d476f30d146eaffe45030916a77341c6768"
TARBALL_URL['x86_64']="https://easycli.sh/proot-distro/debian-trixie-x86_64-pd-v4.37.0.tar.xz"
TARBALL_SHA256['x86_64']="17eec851f40330cb3be77880aedd9e49c87d044f4ee5b02b3568c6aae0a5973b"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}
