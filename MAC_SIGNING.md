# Developer ID signing and Apple notarization

The Mac release copy is signed with Marco's Developer ID Application certificate,
Team ID `3ML6V62AF5`, secure timestamps and hardened runtime. The original working
app and its audio caches are not modified by signing the release copy.

`tools/sign_macos.py` signs every native executable/library, then nested frameworks
and the app, and verifies the completed bundle. Its minimal entitlements permit
Numba/LLVM JIT code. No debugger or library-validation bypass entitlement is set.
Run the package and optional-worker checks after signing: signing is a real runtime
change, not just a different icon or archive name.

```sh
.venv/bin/python tools/sign_macos.py \
  --app '/path/to/BeatStitch.app' \
  --identity 'Developer ID Application: Your Name (YOURTEAMID)'
codesign --verify --deep --strict '/path/to/BeatStitch.app'
```

## One-time notarization credentials

Signing and notarization are separate. Apple notarization needs an Apple Account
app-specific password or an App Store Connect API credential. A signing certificate
alone cannot authenticate the submission. No saved notarization profile was found
in this Mac's login, synchronized/Local Items or system Keychain during preparation.

Create an app-specific password in your own Apple Account at
https://account.apple.com/ (Sign-In and Security → App-Specific Passwords), then run:

```sh
xcrun notarytool store-credentials BeatStitch --team-id 3ML6V62AF5
```

Enter the Apple Account and app-specific password in the local prompts, not in chat,
source files or shell command arguments. The tool validates and saves the credential
in Keychain. Only the profile name is needed for subsequent releases. Apple's guide:
https://support.apple.com/en-us/102654

## Submit, staple and verify

Make a ZIP from the signed app with ditto. Submit that ZIP using the stored profile:

```sh
xcrun notarytool submit '/path/to/BeatStitch-mac-arm64.zip' \
  --keychain-profile BeatStitch --wait --output-format json
```

Only an `Accepted` result confirms notarization. For a rejection, obtain the log
with `xcrun notarytool log REQUEST_ID --keychain-profile BeatStitch` and fix the
reported issue. Do not describe a submitted/pending/rejected build as notarized.

After acceptance, staple the original signed app and validate it:

```sh
xcrun stapler staple '/path/to/BeatStitch.app'
xcrun stapler validate '/path/to/BeatStitch.app'
spctl --assess --type execute --verbose=2 '/path/to/BeatStitch.app'
```

Recreate the downloadable ZIP from that stapled app. Update the app checksum and
source manifest's native-binary identities to match the final release. The GPL/LGPL
source companion still contains the matching source; Apple signing does not alter
those licenses or require users to obtain BeatStitch's approval to rebuild.
