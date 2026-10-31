# throwaway-keychain.sh — sourced by run-arm.sh and smoke-kill-guard.sh.
#
# make_throwaway_keychain <isolated-home>
#   macOS only (a no-op elsewhere). A HOME with no login keychain makes any tool that stores a credential pop a
#   "Keychain Not Found" dialog on the maintainer's screen. This gives the isolated HOME an empty throwaway one:
#   with no keychain preferences in that HOME, macOS takes <home>/Library/Keychains/login.keychain-db as the
#   default, unlocked from creation and, with no timeout and no lock on sleep, for the whole run
#   (`list-keychains -s` does not persist in a bare HOME, and `unlock-keychain` wants the user's login password
#   for that name). Every `security` call runs with HOME set to <home>, so the real keychain is never touched.
#   Returns non-zero, with a message, when <home> is the real home or the keychain is not the default after.
make_throwaway_keychain() {
  local home="$1" real dir keychain
  [ "$(uname -s)" = "Darwin" ] && command -v security >/dev/null 2>&1 || return 0
  # the account's real home from the directory service, not $HOME (the caller may already have isolated it)
  real="$(dscl . -read "/Users/$(id -un)" NFSHomeDirectory 2>/dev/null | sed 's/^NFSHomeDirectory: //')"
  if [ -z "$home" ] || [ -z "$real" ] || [ "$(cd "$home" && pwd -P)" = "$(cd "$real" && pwd -P)" ]; then
    echo "make_throwaway_keychain: '$home' is not an isolated HOME; refusing to touch keychains" >&2
    return 1
  fi
  mkdir -p "$home/Library/Keychains"
  dir="$(cd "$home/Library/Keychains" && pwd -P)"
  keychain="$dir/login.keychain-db"
  if ! { HOME="$home" security create-keychain -p "" "$keychain" \
      && HOME="$home" security set-keychain-settings "$keychain" \
      && [ "$(HOME="$home" security default-keychain -d user 2>/dev/null | sed -e 's/^ *"//' -e 's/"$//')" = "$keychain" ]; }
  then
    echo "make_throwaway_keychain: cannot make $keychain the default keychain of $home" >&2
    return 1
  fi
}
