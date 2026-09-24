#!/usr/bin/env bash
# Install (or update) yt-dlp-gui on Arch Linux and derivatives (EndeavourOS, Manjaro, CachyOS, ...).
#
#   ./install-arch.sh              install, or update to the version in this folder
#   ./install-arch.sh --uninstall  remove it again (your settings are kept)
#
# What it does:
#   * installs python, tk, ffmpeg and deno with pacman (only the ones that are missing)
#   * creates a private Python environment in ~/.local/share/yt-dlp-gui with the latest yt-dlp
#   * adds a `yt-dlp-gui` command in ~/.local/bin and an entry in your application menu
#
# Nothing is installed system-wide except the pacman packages above.
set -euo pipefail

APP=yt-dlp-gui
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
INSTALL_DIR="$DATA_HOME/$APP"
VENV="$INSTALL_DIR/venv"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/$APP"
DESKTOP_FILE="$DATA_HOME/applications/$APP.desktop"
PACKAGES=(python tk ffmpeg deno)

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==> WARNING:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m==> ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

refresh_menu() {
    if command -v update-desktop-database >/dev/null; then
        update-desktop-database -q "$DATA_HOME/applications" 2>/dev/null || true
    fi
}

uninstall() {
    info "Removing $APP"
    rm -rf -- "$INSTALL_DIR"
    rm -f -- "$LAUNCHER" "$DESKTOP_FILE"
    refresh_menu
    info "Done. Your settings are still in ${XDG_CONFIG_HOME:-$HOME/.config}/$APP (delete it if you like)."
}

[[ ${EUID:-$(id -u)} -ne 0 ]] || die "Run this as your normal user, not as root. It uses sudo only for pacman."

case "${1:-}" in
    --uninstall) uninstall; exit 0 ;;
    '') ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
esac

command -v pacman >/dev/null || die "pacman not found. This script is for Arch Linux; see README.md for other systems."
[[ -f "$SRC_DIR/pyproject.toml" && -d "$SRC_DIR/yt_dlp_gui" ]] ||
    die "Run this script from inside the yt-dlp-gui folder (pyproject.toml not found next to it)."

# 1. System packages ---------------------------------------------------------------------------
# `pacman -T` prints only the dependencies that are not installed yet.
mapfile -t missing < <(pacman -T "${PACKAGES[@]}" || true)
if ((${#missing[@]})); then
    info "Installing system packages: ${missing[*]}"
    sudo pacman -S --needed "${missing[@]}"
else
    info "System packages already installed: ${PACKAGES[*]}"
fi

python -c 'import tkinter' 2>/dev/null ||
    die "Python cannot load Tk even though the 'tk' package is installed. Try: sudo pacman -Syu python tk"

# 2. Copy the app and create its Python environment -------------------------------------------
info "Installing $APP into $INSTALL_DIR"
mkdir -p -- "$INSTALL_DIR"
rm -rf -- "$INSTALL_DIR/src"
mkdir -p -- "$INSTALL_DIR/src"
cp -r -- "$SRC_DIR/pyproject.toml" "$SRC_DIR/README.md" "$SRC_DIR/yt_dlp_gui" "$INSTALL_DIR/src/"
find "$INSTALL_DIR/src" -name '__pycache__' -type d -prune -exec rm -rf {} +

# The launcher below reuses this script to repair the environment after a Python upgrade.
cat > "$INSTALL_DIR/setup-venv.sh" <<'EOF'
#!/usr/bin/env bash
# (Re)build yt-dlp-gui's private Python environment. Called by the installer and by the launcher.
set -euo pipefail
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$INSTALL_DIR/venv"
# After a major Python update (e.g. 3.13 -> 3.14) an old environment no longer works: rebuild it.
want=$(python -c 'import sys; print(sys.version_info[:2])')
have=$("$VENV/bin/python" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || true)
if [[ "$want" != "$have" ]]; then
    rm -rf -- "$VENV"
    python -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade "yt-dlp[default]" "$INSTALL_DIR/src"
"$VENV/bin/python" -c 'import tkinter, yt_dlp, yt_dlp_gui'
date +%s > "$INSTALL_DIR/last-update"
EOF
chmod +x "$INSTALL_DIR/setup-venv.sh"

info "Setting up Python environment with the latest yt-dlp (this can take a minute)"
"$INSTALL_DIR/setup-venv.sh"

# 3. Launcher ---------------------------------------------------------------------------------
mkdir -p -- "$BIN_DIR"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Starts yt-dlp-gui. Installed by install-arch.sh.
INSTALL_DIR="$INSTALL_DIR"
EOF
cat >> "$LAUNCHER" <<'EOF'
PY="$INSTALL_DIR/venv/bin/python"
LOG="$INSTALL_DIR/update.log"

notify() {
    command -v notify-send >/dev/null && notify-send -a "yt-dlp GUI" "yt-dlp GUI" "$1"
    echo "$1" >&2
}

# Repair the environment if a Python upgrade broke it.
if ! "$PY" -c 'import tkinter, yt_dlp, yt_dlp_gui' 2>/dev/null; then
    notify "Updating yt-dlp GUI for the new Python version, one moment…"
    "$INSTALL_DIR/setup-venv.sh" >"$LOG" 2>&1 || { notify "Repair failed, see $LOG. Re-running install-arch.sh usually fixes it."; exit 1; }
fi

"$PY" -m yt_dlp_gui "$@"
status=$?

# Sites change often and old yt-dlp versions stop working, so refresh it once a day,
# after the window is closed so a running download is never disturbed.
last=$(cat "$INSTALL_DIR/last-update" 2>/dev/null || echo 0)
if (( $(date +%s) - last > 86400 )); then
    ( "$PY" -m pip install --quiet --disable-pip-version-check --upgrade "yt-dlp[default]" >"$LOG" 2>&1 \
        && date +%s > "$INSTALL_DIR/last-update" ) </dev/null &
    disown
fi
exit $status
EOF
chmod +x "$LAUNCHER"

# 4. Application menu entry -------------------------------------------------------------------
mkdir -p -- "$(dirname "$DESKTOP_FILE")"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=yt-dlp GUI
GenericName=Video Downloader
Comment=Download videos and audio with yt-dlp
Exec="$LAUNCHER"
Icon=folder-download
Terminal=false
Categories=Network;AudioVideo;
Keywords=youtube;video;download;yt-dlp;
StartupWMClass=Yt-dlp-gui
EOF
refresh_menu

# 5. Report -------------------------------------------------------------------------------------
info "Installed yt-dlp $("$VENV/bin/python" -c 'import yt_dlp.version as v; print(v.__version__)')"
browsers=$("$VENV/bin/python" - <<'EOF'
from yt_dlp_gui.browsers import detect_browser_profiles
labels = sorted({p.label for p in detect_browser_profiles()})
print('\n'.join('    ' + label for label in labels))
EOF
)
if [[ -n "$browsers" ]]; then
    info "Browser profiles it can take cookies from:"
    printf '%s\n' "$browsers"
else
    warn "No browser profiles with cookies were found. Sign in to the site in your browser once, then try."
fi
case ":$PATH:" in
    *":$BIN_DIR:"*) info "Done! Start it from your application menu (\"yt-dlp GUI\") or run: $APP" ;;
    *) info "Done! Start it from your application menu (\"yt-dlp GUI\") or run: $LAUNCHER"
       warn "$BIN_DIR is not in your PATH, so the short command '$APP' won't work in a terminal yet." ;;
esac
