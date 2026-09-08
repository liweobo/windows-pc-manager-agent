#ifndef MyAppVersion
  #error MyAppVersion must be supplied by scripts/build-installer.ps1
#endif
#ifndef MyAppNumericVersion
  #error MyAppNumericVersion must be supplied by scripts/build-installer.ps1
#endif

#define MyAppName "Windows PC Manager Agent"
#define MyAppExeName "pc-manager-agent.exe"
#define MyPublisher "liweobo"

[Setup]
AppId={{54A2FD72-E427-49B8-9FA5-B8C04CE81D36}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyPublisher}
DefaultDirName={autopf}\Windows PC Manager Agent
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist\installer
OutputBaseFilename=windows-pc-manager-agent-{#MyAppVersion}-private-rc-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
Uninstallable=yes
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#MyAppNumericVersion}
VersionInfoCompany={#MyPublisher}
VersionInfoDescription={#MyAppName} private RC installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppNumericVersion}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\Unofficial\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式"; Flags: unchecked

[Files]
Source: "..\..\dist\pc-manager-agent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\dist\pc-manager-privileged-broker\*"; DestDir: "{app}\broker"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent runasoriginaluser

// User state under LocalAppData is intentionally absent from [Files] and [UninstallDelete].
// Upgrade, safe reinstall and uninstall therefore preserve audit, recovery and preferences by default.
