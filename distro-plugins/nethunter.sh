##
## Plug-in for installing Kali Nethunter Rootless.
##

DISTRO_NAME="Kali Nethunter"
DISTRO_COMMENT="Long installation time, uses a lot of space."

# Rootfs is in subdirectory.
DISTRO_TARBALL_STRIP_OPT=1

# Returns download URL.
get_download_url() {
	case "$(uname -m)" in
		aarch64)
			echo "https://build.nethunter.com/kalifs/kalifs-latest/kalifs-arm64-full.tar.xz"
			;;
		armv7l|armv8l)
			echo "https://build.nethunter.com/kalifs/kalifs-latest/kalifs-armhf-full.tar.xz"
			;;
		i686)
			echo "https://build.nethunter.com/kalifs/kalifs-latest/kalifs-i386-full.tar.xz"
			;;
		x86_64)
			echo "https://build.nethunter.com/kalifs/kalifs-latest/kalifs-amd64-full.tar.xz"
			;;
	esac
}

# Define here additional steps which should be executed
# for configuration.
distro_setup() {
	# Create KeX launcher.
	cat <<- EOF > ./usr/bin/kex
	#!/bin/bash

	function start-kex() {
		if [ ! -f ~/.vnc/passwd ]; then
			passwd-kex
		fi
		USR=\$(whoami)
		if [ \$USR == "root" ]; then
			SCREEN=":2"
		else
			SCREEN=":1"
		fi
		export HOME=\${HOME}; export USER=\${USR}; nohup vncserver \$SCREEN >/dev/null 2>&1 </dev/null
		starting_kex=1
		return 0
	}

	function stop-kex() {
		vncserver -kill :1 | sed s/"Xtigervnc"/"NetHunter KeX"/
		vncserver -kill :2 | sed s/"Xtigervnc"/"NetHunter KeX"/
		return $?
	}

	function passwd-kex() {
		vncpasswd
		return $?
	}

	function status-kex() {
		sessions=\$(vncserver -list | sed s/"TigerVNC"/"NetHunter KeX"/)
		if [[ \$sessions == *"590"* ]]; then
			printf "\n\${sessions}\n"
			printf "\nYou can use the KeX client to connect to any of these displays.\n\n"
		else
			if [ ! -z \$starting_kex ]; then
				printf '\nError starting the KeX server.\nPlease try "nethunter kex kill" or restart your termux session and try again.\n\n'
			fi
		fi
		return 0
	}

	function kill-kex() {
		pkill Xtigervnc
		return \$?
	}

	case \$1 in
		start) start-kex;;
		stop) stop-kex;;
		status) status-kex;;
		passwd) passwd-kex;;
		kill) kill-kex;;
		*) stop-kex; start-kex; status-kex;;
	esac
	EOF
	chmod 700 ./usr/bin/kex

	# Fix ~/.bash_profile.
	cat <<- EOF > ./root/.bash_profile
	. /root/.bashrc
	. /root/.profile
	EOF
}
