# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Fedora"
DISTRO_COMMENT="Version 42. Broken on Android 15+."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.25.0/fedora-aarch64-pd-v4.25.0.tar.xz"
TARBALL_SHA256['aarch64']="9ca78013e8d51d634c0906e389e6174a816487491534385df01bee4752a5a9d8"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.25.0/fedora-x86_64-pd-v4.25.0.tar.xz"
TARBALL_SHA256['x86_64']="b1a64e2bd37947cc8364ab7030409775748c666d2c2c4d5f331cddba653f2a97"

distro_setup() {
	# Fix environment variables on login or su.
	run_proot_cmd authselect opt-out
	echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/system-auth
}
