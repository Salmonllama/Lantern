#!/bin/sh
# Builds lantern_<version>_all.deb in ./dist
# Requires only dpkg-deb (preinstalled on Debian).
set -eu

cd "$(dirname "$0")"
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/lantern/__init__.py)
PKG=lantern
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
chmod 0755 "$STAGE"   # mktemp makes it 0700; the package root must be 0755

# --- files ---------------------------------------------------------------
install -d "$STAGE/usr/share/lantern/lantern"
install -m 0644 src/lantern/*.py "$STAGE/usr/share/lantern/lantern/"
install -D -m 0755 packaging/lantern "$STAGE/usr/bin/lantern"
install -D -m 0644 packaging/lantern.desktop \
    "$STAGE/usr/share/applications/io.github.lantern.Lantern.desktop"
install -D -m 0644 packaging/io.github.lantern.Lantern.svg \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/io.github.lantern.Lantern.svg"

DOC="$STAGE/usr/share/doc/$PKG"
install -d "$DOC"
cat > "$DOC/copyright" <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: lantern

Files: *
Copyright: $(date +%Y) Lantern contributors
License: MIT
EOF
printf '%s (%s) unstable; urgency=low\n\n  * Release %s.\n\n -- Lantern <lantern@localhost>  %s\n' \
    "$PKG" "$VERSION" "$VERSION" "$(date -R)" | gzip -9n > "$DOC/changelog.gz"

# --- control -------------------------------------------------------------
install -d "$STAGE/DEBIAN"
SIZE=$(du -sk "$STAGE/usr" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Installed-Size: $SIZE
Depends: python3 (>= 3.9), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1
Maintainer: Lantern <lantern@localhost>
Description: Private AI chat that runs on your own computer
 Lantern is a GTK4/libadwaita chat app for local language models.
 It talks to a local Ollama server, so conversations never leave
 your machine. Chats are stored in ~/.local/share/lantern.
EOF

mkdir -p dist
dpkg-deb --root-owner-group --build "$STAGE" "dist/${PKG}_${VERSION}_all.deb"
echo "Built dist/${PKG}_${VERSION}_all.deb"
