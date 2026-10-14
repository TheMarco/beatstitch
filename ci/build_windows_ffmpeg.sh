#!/usr/bin/env bash
# Cross-compile self-contained Windows x64 ffmpeg.exe/ffprobe.exe on Ubuntu.
# Replaces the prebuilt Gyan bundle with a pinned, verifiable source build.
# Retains FFmpeg's internal formats/codecs/filters; adds libmp3lame (MP3).
set -euo pipefail

FFMPEG_URL="https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz"
FFMPEG_SHA256="8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e"
FFMPEG_ARCHIVE="ffmpeg-9.0.2.tar.xz"
FFMPEG_SRC_DIR="ffmpeg-9.0.2"

LAME_URL="https://downloads.sourceforge.net/project/lame/lame/3.100/lame-3.100.tar.gz"
LAME_SHA256="ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e"
LAME_ARCHIVE="lame-3.100.tar.gz"
LAME_SRC_DIR="lame-3.100"

CROSS_PREFIX="x86_64-w64-mingw32-"
CROSS_CC="x86_64-w64-mingw32-gcc"
CROSS_AR="x86_64-w64-mingw32-ar"
CROSS_RANLIB="x86_64-w64-mingw32-ranlib"
TARGET_TRIPLE="x86_64-w64-mingw32"

BUILD_ROOT=""
cleanup() {
  if [[ -n "$BUILD_ROOT" && -d "$BUILD_ROOT" ]]; then
    rm -rf -- "$BUILD_ROOT"
  fi
}
trap cleanup EXIT

usage() {
  printf 'Usage: %s <new-destination-directory>\n' "${0##*/}" >&2
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

DESTINATION="$1"
if [[ -z "$DESTINATION" ]]; then
  usage
  exit 2
fi
if [[ -e "$DESTINATION" || -L "$DESTINATION" ]]; then
  printf 'error: destination already exists: %s\n' "$DESTINATION" >&2
  exit 1
fi

for tool in gcc make "$CROSS_CC" nasm pkg-config curl tar sha256sum python3; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    printf 'error: required tool not found: %s\n' "$tool" >&2
    exit 1
  fi
done

# Absolute destination for the recipe/receipts; creation is refused above if
# the path already exists, so mkdir -p only creates this new tree.
DEST_PARENT="$(dirname -- "$DESTINATION")"
DEST_NAME="$(basename -- "$DESTINATION")"
mkdir -p -- "$DEST_PARENT"
DESTINATION_ABS="$(cd -- "$DEST_PARENT" && pwd -P)/$DEST_NAME"
mkdir -p -- "$DESTINATION_ABS/bin" "$DESTINATION_ABS/sources" "$DESTINATION_ABS/licenses"

BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/beatstitch-windows-ffmpeg.XXXXXX")"
case "$BUILD_ROOT" in
  /*) ;;
  *) BUILD_ROOT="$(pwd -P)/$BUILD_ROOT" ;;
esac
PREFIX="$BUILD_ROOT/prefix"
SRC_ROOT="$BUILD_ROOT/src"
mkdir -p -- "$PREFIX" "$SRC_ROOT"

COMPILER_VERSION="$("$CROSS_CC" --version | head -n 1)"
COMPILER_TARGET="$("$CROSS_CC" -dumpmachine)"

LAME_CONFIGURE_ARGS=(
  --host=x86_64-w64-mingw32
  --disable-shared
  --enable-static
  --disable-frontend
  "--prefix=$PREFIX"
)

FFMPEG_CONFIGURE_ARGS=(
  --arch=x86_64
  --target-os=mingw32
  --cross-prefix=x86_64-w64-mingw32-
  --enable-cross-compile
  --disable-autodetect
  --disable-network
  --disable-doc
  --disable-ffplay
  --disable-shared
  --enable-static
  --enable-libmp3lame
  --enable-gpl
  --enable-version3
  "--extra-cflags=-I$PREFIX/include"
  "--extra-ldflags=-L$PREFIX/lib -static"
)

shell_join() {
  local arg
  local first=1
  for arg in "$@"; do
    if [[ $first -eq 1 ]]; then
      printf '%q' "$arg"
      first=0
    else
      printf ' %q' "$arg"
    fi
  done
}

LAME_CONFIGURE_DISPLAY="CC=$(shell_join "$CROSS_CC") AR=$(shell_join "$CROSS_AR") RANLIB=$(shell_join "$CROSS_RANLIB") CFLAGS=$(shell_join "-O2") ./configure $(shell_join "${LAME_CONFIGURE_ARGS[@]}")"
FFMPEG_CONFIGURE_DISPLAY="PKG_CONFIG_PATH=$(shell_join "$PREFIX/lib/pkgconfig") ./configure $(shell_join "${FFMPEG_CONFIGURE_ARGS[@]}")"

verify_sha256() {
  local expected="$1"
  local file="$2"
  local actual
  actual="$(sha256sum -- "$file" | awk '{print $1}')"
  if [[ "$actual" != "$expected" ]]; then
    printf 'error: SHA256 mismatch for %s\n  expected: %s\n  actual:   %s\n' "$file" "$expected" "$actual" >&2
    exit 1
  fi
}

# --- Download pinned sources (verified before any extraction) ---
curl --fail --location --output "$DESTINATION_ABS/sources/$FFMPEG_ARCHIVE" -- "$FFMPEG_URL"
verify_sha256 "$FFMPEG_SHA256" "$DESTINATION_ABS/sources/$FFMPEG_ARCHIVE"

curl --fail --location --output "$DESTINATION_ABS/sources/$LAME_ARCHIVE" -- "$LAME_URL"
verify_sha256 "$LAME_SHA256" "$DESTINATION_ABS/sources/$LAME_ARCHIVE"

# --- Extract only the verified archives into the private build root ---
tar -xJf "$DESTINATION_ABS/sources/$FFMPEG_ARCHIVE" -C "$SRC_ROOT"
tar -xzf "$DESTINATION_ABS/sources/$LAME_ARCHIVE" -C "$SRC_ROOT"

# --- Build LAME (static only, into the private prefix) ---
(
  cd -- "$SRC_ROOT/$LAME_SRC_DIR"
  CC="$CROSS_CC" AR="$CROSS_AR" RANLIB="$CROSS_RANLIB" CFLAGS="-O2" \
    ./configure "${LAME_CONFIGURE_ARGS[@]}"
  make -j2
  make install
)

# --- Build FFmpeg/ffprobe (full internal codec/format/filter support) ---
# No --disable-decoders/--disable-encoders/--disable-muxers etc.: the default
# internal component set is retained. --disable-autodetect only stops
# probing for un-pinned external libraries.
(
  cd -- "$SRC_ROOT/$FFMPEG_SRC_DIR"
  PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig" ./configure "${FFMPEG_CONFIGURE_ARGS[@]}"
  make -j2 ffmpeg.exe ffprobe.exe
)

install -m 0755 -- "$SRC_ROOT/$FFMPEG_SRC_DIR/ffmpeg.exe" "$DESTINATION_ABS/bin/ffmpeg.exe"
install -m 0755 -- "$SRC_ROOT/$FFMPEG_SRC_DIR/ffprobe.exe" "$DESTINATION_ABS/bin/ffprobe.exe"

# --- Licences / notices ---
cp -- "$SRC_ROOT/$FFMPEG_SRC_DIR/COPYING.GPLv3" "$DESTINATION_ABS/licenses/ffmpeg-COPYING.GPLv3.txt"
cp -- "$SRC_ROOT/$FFMPEG_SRC_DIR/LICENSE.md" "$DESTINATION_ABS/licenses/ffmpeg-LICENSE.md"
cp -- "$SRC_ROOT/$LAME_SRC_DIR/COPYING" "$DESTINATION_ABS/licenses/lame-COPYING"
cp -- "$SRC_ROOT/$LAME_SRC_DIR/LICENSE" "$DESTINATION_ABS/licenses/lame-LICENSE"
for toolchain_package in gcc-mingw-w64-base mingw-w64-common; do
  notice="/usr/share/doc/$toolchain_package/copyright"
  if [[ ! -f "$notice" ]]; then
    printf 'error: missing cross-toolchain copyright notice: %s\n' "$notice" >&2
    exit 1
  fi
  cp -- "$notice" "$DESTINATION_ABS/licenses/$toolchain_package-copyright.txt"
done
cp -- /usr/share/common-licenses/GPL-3 "$DESTINATION_ABS/licenses/compiler-GPL-3.txt"

# --- Preserve this script alongside the outputs ---
SCRIPT_PATH="${BASH_SOURCE[0]}"
SCRIPT_ABS="$(cd -- "$(dirname -- "$SCRIPT_PATH")" && pwd -P)/$(basename -- "$SCRIPT_PATH")"
cp -- "$SCRIPT_ABS" "$DESTINATION_ABS/build_windows_ffmpeg.sh"
chmod 0755 "$DESTINATION_ABS/build_windows_ffmpeg.sh"

# --- Standalone human-readable build recipe (exact commands + script) ---
RECIPE="$DESTINATION_ABS/BUILD-RECIPE.txt"
{
  printf '%s\n' 'BeatStitch Windows x64 FFmpeg build recipe'
  printf '%s\n' '=========================================='
  printf '\n'
  printf '%s\n' 'Target: Windows x64 (mingw32), fully static ffmpeg.exe and ffprobe.exe'
  printf 'Cross compiler: %s\n' "$COMPILER_VERSION"
  printf 'Compiler target: %s\n' "$COMPILER_TARGET"
  printf '\n'
  printf '%s\n' 'Pinned sources (SHA256 verified before extraction):'
  printf '  FFmpeg: %s\n' "$FFMPEG_URL"
  printf '          sha256 %s\n' "$FFMPEG_SHA256"
  printf '  LAME:   %s\n' "$LAME_URL"
  printf '          sha256 %s\n' "$LAME_SHA256"
  printf '\n'
  printf '%s\n' 'Private prefix (temporary build directory, absolute path used in this run):'
  printf '  %s\n' "$PREFIX"
  printf '\n'
  printf '%s\n' 'LAME build (in the extracted lame-3.100 directory):'
  printf '  %s\n' "$LAME_CONFIGURE_DISPLAY"
  printf '%s\n' '  make -j2'
  printf '%s\n' '  make install'
  printf '\n'
  printf '%s\n' 'FFmpeg build (in the extracted ffmpeg-9.0.2 directory):'
  printf '  %s\n' "$FFMPEG_CONFIGURE_DISPLAY"
  printf '%s\n' '  make -j2 ffmpeg.exe ffprobe.exe'
  printf '\n'
  printf '%s\n' 'Outputs (relative to the destination directory):'
  printf '%s\n' '  bin/ffmpeg.exe'
  printf '%s\n' '  bin/ffprobe.exe'
  printf '%s\n' '  sources/ffmpeg-9.0.2.tar.xz'
  printf '%s\n' '  sources/lame-3.100.tar.gz'
  printf '%s\n' '  licenses/ffmpeg-COPYING.GPLv3.txt'
  printf '%s\n' '  licenses/ffmpeg-LICENSE.md'
  printf '%s\n' '  licenses/lame-COPYING'
  printf '%s\n' '  licenses/lame-LICENSE'
  printf '%s\n' '  receipt.json, SHA256SUMS, BUILD-RECIPE.txt, build_windows_ffmpeg.sh'
  printf '\n'
  printf '%s\n' 'Notes:'
  printf '%s\n' '  - FFmpeg internal decoders/encoders/muxers/demuxers/filters are not'
  printf '%s\n' '    restricted to a hand-picked list; defaults are retained.'
  printf '%s\n' '  - --disable-autodetect prevents probing for un-pinned external libs.'
  printf '%s\n' '  - To rebuild, re-run the preserved build_windows_ffmpeg.sh with a new'
  printf '%s\n' '    destination directory on Ubuntu with gcc/make, mingw-w64, nasm,'
  printf '%s\n' '    pkg-config, curl, tar, sha256sum and python3 available.'
  printf '\n'
  printf '%s\n' '----- build_windows_ffmpeg.sh (verbatim) -----'
  cat -- "$SCRIPT_ABS"
} > "$RECIPE"

# Arg files let the Python receipt record exact configure arguments without
# dumping the process environment.
printf '%s\n' "${LAME_CONFIGURE_ARGS[@]}" > "$BUILD_ROOT/lame-configure-args.txt"
printf '%s\n' "${FFMPEG_CONFIGURE_ARGS[@]}" > "$BUILD_ROOT/ffmpeg-configure-args.txt"

# --- Source/binary receipts via Python standard library only ---
DESTINATION_ABS="$DESTINATION_ABS" \
RECEIPT_COMPILER_VERSION="$COMPILER_VERSION" \
RECEIPT_COMPILER_TARGET="$COMPILER_TARGET" \
RECEIPT_FFMPEG_URL="$FFMPEG_URL" \
RECEIPT_LAME_URL="$LAME_URL" \
RECEIPT_LAME_ARGS_FILE="$BUILD_ROOT/lame-configure-args.txt" \
RECEIPT_FFMPEG_ARGS_FILE="$BUILD_ROOT/ffmpeg-configure-args.txt" \
RECEIPT_LAME_DISPLAY="$LAME_CONFIGURE_DISPLAY" \
RECEIPT_FFMPEG_DISPLAY="$FFMPEG_CONFIGURE_DISPLAY" \
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

dest = Path(os.environ["DESTINATION_ABS"])

def read_args(name):
    return Path(os.environ[name]).read_text(encoding="utf-8").splitlines()

def entry(rel):
    path = dest / rel
    data = path.read_bytes()
    return {"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}

receipt = {
    "sources": [
        dict(entry("sources/ffmpeg-9.0.2.tar.xz"), url=os.environ["RECEIPT_FFMPEG_URL"]),
        dict(entry("sources/lame-3.100.tar.gz"), url=os.environ["RECEIPT_LAME_URL"]),
    ],
    "binaries": [entry("bin/ffmpeg.exe"), entry("bin/ffprobe.exe")],
    "licenses": [entry(path.relative_to(dest).as_posix()) for path in sorted((dest / "licenses").iterdir()) if path.is_file()],
    "lame_configure_args": read_args("RECEIPT_LAME_ARGS_FILE"),
    "lame_configure": os.environ["RECEIPT_LAME_DISPLAY"],
    "ffmpeg_configure_args": read_args("RECEIPT_FFMPEG_ARGS_FILE"),
    "ffmpeg_configure": os.environ["RECEIPT_FFMPEG_DISPLAY"],
    "compiler_version": os.environ["RECEIPT_COMPILER_VERSION"],
    "compiler_target": os.environ["RECEIPT_COMPILER_TARGET"],
    "target": "x86_64-w64-mingw32 (Windows x64, mingw32, static)",
}
(dest / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
lines = []
for group in ("sources", "binaries", "licenses"):
    for item in receipt[group]:
        lines.append(f'{item["sha256"]}  {item["path"]}')
(dest / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

printf 'Built %s and %s in %s\n' "bin/ffmpeg.exe" "bin/ffprobe.exe" "$DESTINATION_ABS"
