#!/usr/bin/env bash
set -e
: "${TERMUX_PREFIX:=/data/data/com.termux/files/usr}"

echo "Installing $TERMUX_PREFIX/bin/proot-distro"
install -d -m 700 "$TERMUX_PREFIX"/bin
sed -e "s|@TERMUX_PREFIX@|$TERMUX_PREFIX|g" ./proot-distro.sh > "$TERMUX_PREFIX"/bin/proot-distro
chmod 700 "$TERMUX_PREFIX"/bin/proot-distro

install -d -m 700 "$TERMUX_PREFIX"/share/proot-distro
for script in ./distro-plugins/*.sh; do
	echo "Installing $TERMUX_PREFIX/share/proot-distro/$(basename "$script")"
	install -Dm600 -t "$TERMUX_PREFIX"/share/proot-distro/ "$script"
done
