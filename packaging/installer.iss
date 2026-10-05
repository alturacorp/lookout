; Inno Setup script. Build:  iscc /DAppVersion=1.2.3 packaging\installer.iss   (after PyInstaller has produced dist\Lookout)
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Lookout"
#define AppExe "Lookout.exe"

[Setup]
; AppId identifies this app to Windows so upgrades replace the old install. Never change it after you ship.
AppId={{9DF49C34-8E70-46D2-BB66-17C45373B574}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install by default: no administrator prompt, and silent updates work without UAC.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=Lookout-Setup-{#AppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Updates: close the running app, upgrade in place, relaunch it.
CloseApplications=yes
RestartApplications=yes
; Code signing (strongly recommended: unsigned installers trigger SmartScreen warnings). Define SignTool in the
; Inno IDE or with /S on the command line, then uncomment:
; SignTool=signtool
; SignedUninstaller=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked
Name: "startup"; Description: "Start {#AppName} when I sign in to Windows"; Flags: unchecked

[Files]
Source: "..\dist\Lookout\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[InstallDelete]
; left over from before the app was renamed
Type: files; Name: "{app}\RobloxPlayerTracker.exe"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "RobloxPlayerTracker"; Flags: deletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Lookout"; \
  ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
; Normal install: offer to launch at the end. Silent install (an auto-update): relaunch without asking.
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExe}"; Flags: nowait skipifnotsilent runasoriginaluser

; Uninstalling removes the program but deliberately leaves your data (%APPDATA%\Lookout) in place.
