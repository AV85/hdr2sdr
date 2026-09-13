#!/usr/bin/env bash
# hdr2sdr installer for Ubuntu / Debian-based systems.
#
#   ./install.sh                  install (or update) the app
#   ./install.sh --static-ffmpeg  additionally download a static ffmpeg build with libplacebo
#   ./install.sh --uninstall      remove the app (keeps ~/.config/hdr2sdr)
#
# Everything is installed for the current user only (no sudo for the app itself);
# sudo is used only for apt packages.
set -euo pipefail

APP=hdr2sdr
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$APP"
VENV="$DATA_DIR/venv"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

STATIC_FFMPEG=0
UNINSTALL=0
for a in "$@"; do
  case "$a" in
    --static-ffmpeg) STATIC_FFMPEG=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option: $a"; exit 1 ;;
  esac
done

msg() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

if [ "$UNINSTALL" = 1 ]; then
  msg "Removing $APP"
  rm -rf "$DATA_DIR" "$BIN_DIR/$APP" "$BIN_DIR/$APP-cli" "$APPS_DIR/$APP.desktop" "$ICON_DIR/$APP.svg"
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
  msg "Done. Settings kept in ~/.config/$APP (delete manually if not needed)."
  exit 0
fi

# ---------------------------------------------------------------- system packages
if command -v apt-get >/dev/null 2>&1; then
  msg "Installing system packages (sudo apt)"
  PKGS="ffmpeg python3 python3-venv python3-pip libxcb-cursor0 libegl1 libgl1"
  # GPU stack for AMD/Intel: VAAPI (hardware encode) + Vulkan (libplacebo tone mapping)
  PKGS="$PKGS mesa-va-drivers libva-drm2 libva2 vainfo mesa-vulkan-drivers vulkan-tools"
  # A broken third-party PPA makes "apt-get update" exit non-zero; that must not
  # skip the install step, so the two commands are deliberately not chained.
  sudo apt-get update -qq || warn "apt-get update reported errors (usually an unrelated broken PPA); trying to install anyway"
  # shellcheck disable=SC2086
  sudo apt-get install -y -qq $PKGS || warn "some packages failed to install (continuing)"
  if ! dpkg -s libxcb-cursor0 >/dev/null 2>&1; then
    warn "libxcb-cursor0 is missing: the GUI will not start until you run: sudo apt-get install libxcb-cursor0"
  fi
else
  warn "apt-get not found: make sure ffmpeg, python3 (>=3.10) and python3-venv are installed"
fi

# ---------------------------------------------------------------- python venv + app
msg "Creating virtual environment in $VENV"
mkdir -p "$DATA_DIR" "$BIN_DIR" "$APPS_DIR" "$ICON_DIR"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
msg "Installing $APP and PySide6 (this can take a minute)"
"$VENV/bin/pip" install --quiet --upgrade "$SRC_DIR"

# ---------------------------------------------------------------- optional static ffmpeg
if [ "$STATIC_FFMPEG" = 1 ]; then
  msg "Downloading static ffmpeg build (BtbN, GPL, includes libplacebo)"
  URL="https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-linux64-gpl.tar.xz"
  TMP="$(mktemp -d)"
  if curl -fL --progress-bar "$URL" -o "$TMP/ffmpeg.tar.xz"; then
    tar -xJf "$TMP/ffmpeg.tar.xz" -C "$TMP"
    rm -rf "$DATA_DIR/ffmpeg"
    mkdir -p "$DATA_DIR/ffmpeg/bin"
    cp "$TMP"/ffmpeg-*/bin/ffmpeg "$TMP"/ffmpeg-*/bin/ffprobe "$DATA_DIR/ffmpeg/bin/"
    chmod +x "$DATA_DIR/ffmpeg/bin/"*
    msg "Static ffmpeg installed to $DATA_DIR/ffmpeg/bin (auto-detected by the app)"
  else
    warn "download failed; system ffmpeg will be used"
  fi
  rm -rf "$TMP"
fi

# ---------------------------------------------------------------- launchers
cat > "$BIN_DIR/$APP" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/python" -m hdr2sdr "\$@"
EOF
cat > "$BIN_DIR/$APP-cli" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/python" -m hdr2sdr.cli "\$@"
EOF
chmod +x "$BIN_DIR/$APP" "$BIN_DIR/$APP-cli"

cp "$SRC_DIR/assets/$APP.svg" "$ICON_DIR/$APP.svg"
sed "s|@EXEC@|$BIN_DIR/$APP|g; s|@ICON@|$ICON_DIR/$APP.svg|g" "$SRC_DIR/$APP.desktop" > "$APPS_DIR/$APP.desktop"
chmod +x "$APPS_DIR/$APP.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not in PATH; log out/in or run: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# ---------------------------------------------------------------- check
msg "Checking ffmpeg capabilities"
"$BIN_DIR/$APP-cli" --caps || true

echo
msg "Installed. Run:  $APP        (GUI, also in the applications menu)"
msg "          or:  $APP-cli --help   (command line)"
msg "Update later:  cd \"$SRC_DIR\" && git pull && ./install.sh"
