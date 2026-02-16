# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Trisquel GNU/Linux"
DISTRO_COMMENT="Version 'aramo'."

TARBALL_URL['aarch64']="https://easycli.sh/proot-distro/trisquel-aarch64-pd-v4.37.0.tar.xz"
TARBALL_SHA256['aarch64']="6082a0b2e156d391b45f02d4fe97a82669067b357fad2e91fc0623eac9254f34"
TARBALL_URL['arm']="https://easycli.sh/proot-distro/trisquel-arm-pd-v4.37.0.tar.xz"
TARBALL_SHA256['arm']="c45f2024507868161f8a0f349737733a8abe72d49b83511062d7529138de6d0c"
TARBALL_URL['i686']="https://easycli.sh/proot-distro/trisquel-i686-pd-v4.37.0.tar.xz"
TARBALL_SHA256['i686']="cdb8616dc96c07004187338b8d3be5f9f6c4e7b18736e60c1622169151d1a2a0"
TARBALL_URL['x86_64']="https://easycli.sh/proot-distro/trisquel-x86_64-pd-v4.37.0.tar.xz"
TARBALL_SHA256['x86_64']="bbbee10e1793d15e4e1bc6ebbeb23acdd8de165c2bbf83c7a9a8add050b8e1c1"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}
