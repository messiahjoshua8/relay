#!/bin/sh
# Installs the ellict command onto ~/.local/bin. Python stays inside the command.
#   From the internet: curl -fsSL https://eliolabs.xyz/install.sh | sh
#   From a checkout:   sh install.sh
set -eu
SOURCE="${ELLICT_SOURCE:-https://github.com/messiahjoshua8/relay/archive/refs/heads/main.tar.gz}"
SHARE="${ELLICT_HOME:-${HOME}/.local/share/ellict}"
DEST="${ELLICT_BIN:-${HOME}/.local/bin}"

fail() {
  echo "ellict install: $*" >&2
  exit 1
}

PYTHON=""
for candidate in python3 python3.13 python3.12 python3.11; do
  if command -v "$candidate" >/dev/null 2>&1 &&
    "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PYTHON=$(command -v "$candidate")
    break
  fi
done
[ -n "$PYTHON" ] || fail "needs Python 3.11 or newer on PATH"

HERE=""
case "$0" in
  install.sh | */install.sh) HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) ;;
esac

if [ -n "$HERE" ] && [ -d "$HERE/src/ellict" ]; then
  SRC="$HERE/src"
else
  command -v curl >/dev/null 2>&1 || fail "needs curl to download $SOURCE"
  TMP=$(mktemp -d)
  trap 'rm -rf "$TMP"' EXIT
  curl -fsSL "$SOURCE" -o "$TMP/ellict.tar.gz" || fail "could not download $SOURCE"
  tar -xzf "$TMP/ellict.tar.gz" -C "$TMP" || fail "$SOURCE is not a tar.gz archive"
  FOUND=$(find "$TMP" -type d -path '*/src/ellict' | head -n 1)
  [ -n "$FOUND" ] || fail "no src/ellict inside $SOURCE"
  mkdir -p "$SHARE"
  rm -rf "$SHARE/src"
  cp -R "$(dirname "$FOUND")" "$SHARE/src"
  SRC="$SHARE/src"
fi

mkdir -p "$DEST"
cat > "$DEST/ellict" <<EOF
#!/bin/sh
set -eu
PYTHONPATH="$SRC\${PYTHONPATH:+:\$PYTHONPATH}"
export PYTHONPATH
exec "$PYTHON" -m ellict "\$@"
EOF
chmod +x "$DEST/ellict"
"$DEST/ellict" --version >/dev/null || fail "installed $DEST/ellict but it does not run"

echo "Installed ${DEST}/ellict ($("$DEST/ellict" --version))"
case ":${PATH}:" in
  *":${DEST}:"*) ;;
  *) echo "Add it to PATH: export PATH=\"${DEST}:\$PATH\"" ;;
esac
echo "Agent line: ellict run . --publish-to ~/ellict-skills"
