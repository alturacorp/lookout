# Building and releasing

## One-off: build on your own Windows machine
1. Install Python **3.11** (rapidocr-onnxruntime hasn't always supported the newest Python) and [Inno Setup 6](https://jrsoftware.org/isinfo.php).
2. `pip install -r requirements.txt -r requirements-build.txt`
3. `packaging\build.bat 1.0.0`

This stamps the version, runs PyInstaller, **self-tests the built exe** (it loads the bundled OCR models, so a
missing-model problem fails here instead of on a user's PC), and builds `dist\Lookout-Setup-1.0.0.exe`.
Pass a feed URL as a second argument to enable update checks in that build.

## Releasing with GitHub Actions
1. Push the project to a GitHub repo (**public**, so the app can fetch updates without a token).
2. `git tag v1.0.0 && git push origin v1.0.0`
3. The `build` workflow builds, self-tests, creates the installer, and publishes a release containing the installer and
   `latest.json`. Installed copies poll `releases/latest/download/latest.json`, so publishing a release *is* shipping the update.

Run the workflow by hand (Actions tab) to get the installer as a downloadable artifact without publishing.

## How updates reach people
On launch (at most once every ~20 hours) the app fetches `latest.json`. If it lists a newer version, the user chooses
Install now / Later / Skip this version. "Install now" downloads the installer, checks its SHA-256 against the feed, runs
it silently (it closes the app, upgrades in place, relaunches it), and the user's data is untouched.

The checksum catches corrupt downloads, not a compromised release page, so:
- keep the repo's release permissions tight, and
- **sign the installer** (uncomment `SignTool` in `installer.iss`). It is also the real fix for SmartScreen warnings and most antivirus false positives on a screen-capturing, hotkey-registering app.

## Things to know
- **Data survives updates and uninstalls** (`%APPDATA%\Lookout`). Delete that folder yourself for a full wipe.
- **The installer build has no YOLO detector** (PyTorch is over a gigabyte); it uses the nametag-relative box instead. Source installs can still `pip install ultralytics`.
- **Name:** the app is called Lookout: `APP_NAME` in `roblox_tracker/paths.py` plus the names in `packaging/`. The Python package keeps
  the folder name `roblox_tracker` (renaming it is cosmetic). Data from the old `RobloxPlayerTracker` folder is moved over on first run.
- `AppId` in `installer.iss` identifies the app to Windows for upgrades. Never change it once people have installed.
- Unpinned dependencies can break a build when a library updates. After a good build, `pip freeze` and pin what works.

## Shared watchlist (optional)
The app can sync with the Firebase backend in `firebase/` (see its README). Builds only know about it if you pass the
project to `write_build_info.py` (`--cloud-api-key`, `--cloud-project`; CI reads the repository variables
`FIREBASE_API_KEY` and `FIREBASE_PROJECT`). The web API key is an identifier, not a secret; access is controlled by the
backend's roles. Without them the "Shared list…" window says the build isn't connected. For testing against another project,
put `{"apiKey": "...", "projectId": "..."}` in `cloud.json` in the data folder (optionally `"dashboardUrl": "https://..."` if the dashboard is hosted somewhere other than `<project>.web.app`).

## Icons and theme
Button icons are Google Material Symbols (https://fonts.google.com/icons). `python packaging/fetch_icons.py` downloads them and
writes PNGs to `roblox_tracker/assets/icons/` (CI and `build.bat` run it first; the build still works if it fails, the buttons
just show text). The list of icons is at the top of that script. `pip install sv-ttk` gives the Sun Valley look; without it
the app uses a built-in dark/light style. Theme and "Avatar box size" are in Settings.
