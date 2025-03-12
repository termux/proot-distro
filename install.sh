#!/usr/bin/env bash
set -e
: "${TERMUX_APP_PACKAGE:="com.termux"}"
: "${TERMUX_PREFIX:="/data/data/${TERMUX_APP_PACKAGE}/files/usr"}"
: "${TERMUX_ANDROID_HOME:="/data/data/${TERMUX_APP_PACKAGE}/files/home"}"

echo "Installing $TERMUX_PREFIX/bin/proot-distro"
install -d -m 700 "$TERMUX_PREFIX"/bin
sed -e "s|@TERMUX_APP_PACKAGE@|$TERMUX_APP_PACKAGE|g" \
	-e "s|@TERMUX_PREFIX@|$TERMUX_PREFIX|g" \
	-e "s|@TERMUX_HOME@|$TERMUX_ANDROID_HOME|g" \
	./proot-distro.sh > "$TERMUX_PREFIX"/bin/proot-distro
chmod 700 "$TERMUX_PREFIX"/bin/proot-distro

echo "Symlinking $TERMUX_PREFIX/bin/proot-distro --> $TERMUX_PREFIX/bin/pd"
ln -sfr "$TERMUX_PREFIX"/bin/proot-distro "$TERMUX_PREFIX"/bin/pd

install -d -m 700 "$TERMUX_PREFIX"/etc/proot-distro
for script in ./distro-plugins/*.sh*; do
	echo "Installing $TERMUX_PREFIX/etc/proot-distro/$(basename "$script")"
	install -Dm600 -t "$TERMUX_PREFIX"/etc/proot-distro/ "$script"
done

echo "Installing $TERMUX_PREFIX/share/bash-completion/completions/proot-distro"
install -d -m 700 "$TERMUX_PREFIX"/share/bash-completion/completions
sed -e "s|@TERMUX_APP_PACKAGE@|$TERMUX_APP_PACKAGE|g" \
	-e "s|@TERMUX_PREFIX@|$TERMUX_PREFIX|g" \
	-e "s|@TERMUX_HOME@|$TERMUX_ANDROID_HOME|g" \
	./completions/proot-distro.bash > "$TERMUX_PREFIX"/share/bash-completion/completions/proot-distro

echo "Symlinking $TERMUX_PREFIX/share/bash-completion/completions/proot-distro --> $TERMUX_PREFIX/share/bash-completion/completions/pd"
ln -sfr "$TERMUX_PREFIX"/share/bash-completion/completions/proot-distro "$TERMUX_PREFIX"/share/bash-completion/completions/pd

echo "Installing $TERMUX_PREFIX/share/fish/vendor_completions.d/proot-distro.fish"
install -d -m 700 "$TERMUX_PREFIX"/share/fish/vendor_completions.d
sed -e "s|@TERMUX_APP_PACKAGE@|$TERMUX_APP_PACKAGE|g" \
	-e "s|@TERMUX_PREFIX@|$TERMUX_PREFIX|g" \
	-e "s|@TERMUX_HOME@|$TERMUX_ANDROID_HOME|g" \
	./completions/proot-distro.fish > "$TERMUX_PREFIX"/share/fish/vendor_completions.d/proot-distro.fish

echo "Installing $TERMUX_PREFIX/share/fish/vendor_completions.d/pd.fish"
cat << EOF > "$TERMUX_PREFIX"/share/fish/vendor_completions.d/pd.fish
# Completions for proot-distro
# https://github.com/termux/proot-distro

complete -c pd -w proot-distro
EOF

echo "Installing $TERMUX_PREFIX/share/doc/proot-distro/README.md"
install -Dm600 README.md "$TERMUX_PREFIX"/share/doc/proot-distro/README.md
