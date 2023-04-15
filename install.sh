#!/usr/bin/env bash
set -e
: "${TERMUX_APP_PACKAGE:="com.termux"}"
: "${TERMUX_PREFIX:="/data/data/${TERMUX_APP_PACKAGE}/files/usr"}"
: "${TERMUX_ANDROID_HOME:="/data/data/${TERMUX_APP_PACKAGE}/files/home"}"

echo "Installing $TERMUX_PREFIX/bin/proot-distro"
install -d -m 700 "$TERMUX_PREFIX"/bin
if [ -f "$TERMUX_PREFIX"/bin/proot-distro ]; then
  echo "Removing existing file: $TERMUX_PREFIX/bin/proot-distro"
  rm "$TERMUX_PREFIX"/bin/proot-distro
fi
sed -e "s|@TERMUX_APP_PACKAGE@|$TERMUX_APP_PACKAGE|g" \
	-e "s|@TERMUX_PREFIX@|$TERMUX_PREFIX|g" \
	-e "s|@TERMUX_HOME@|$TERMUX_ANDROID_HOME|g" \
	./proot-distro.sh > "$TERMUX_PREFIX"/bin/proot-distro
chmod 700 "$TERMUX_PREFIX"/bin/proot-distro

install -d -m 700 "$TERMUX_PREFIX"/etc/proot-distro
for script in ./distro-plugins/*.sh*; do
	filename=$(basename "$script")
	if [ -f "$TERMUX_PREFIX"/etc/proot-distro/"$filename" ]; then
  		echo "Removing existing file: $TERMUX_PREFIX/etc/proot-distro/$filename"
  		rm "$TERMUX_PREFIX"/etc/proot-distro/"$filename"
  	fi
	echo "Installing $TERMUX_PREFIX/etc/proot-distro/$filename"
	install -Dm600 -t "$TERMUX_PREFIX"/etc/proot-distro/ "$script"
done

if [ -d "$PREFIX/etc/prootdir" ]; then
  rm -rf "$PREFIX/etc/prootdir"
fi
mkdir -p $PREFIX/etc/prootdir
cp ./scripts/user.sh $PREFIX/etc/prootdir
cp ./scripts/distro $PREFIX/etc/prootdir
echo "Installing $TERMUX_PREFIX/share/doc/proot-distro/README.md"
install -Dm600 README.md "$TERMUX_PREFIX"/share/doc/proot-distro/README.md
