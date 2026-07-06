#define MyAppName "Dr Transition"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Dr Transition"
#define MyAppExeName "DrTransition.exe"

[Setup]
AppId={{9A34E5CC-A8AA-40E4-9F81-6E20E3E3A8D8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Dr Transition
DefaultGroupName=Dr Transition
DisableProgramGroupPage=yes
OutputDir=..\..\build\windows-installer
OutputBaseFilename=DrTransitionSetup-{#MyAppVersion}
Compression=zip
SolidCompression=no
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
SetupLogging=yes
UninstallDisplayIcon={app}\{#MyAppExeName}

[Tasks]
Name: "desktopicon"; Description: "Create a Desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: checkedonce

[Dirs]
Name: "{commonappdata}\DrTransition"; Permissions: users-modify
Name: "{commonappdata}\DrTransition\uploads"; Permissions: users-modify
Name: "{localappdata}\DrTransition\logs"; Permissions: users-modify

[Files]
Source: "..\..\build\windows-installer\payload\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Dr Transition"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Dr Transition"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Dr Transition"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\DrTransition\logs"

[Code]
var
  CompatibilityPage: TOutputMsgWizardPage;

function InitializeSetup(): Boolean;
begin
  Result := IsWin64;
  if not Result then
    MsgBox('Dr Transition requires 64-bit Windows.', mbCriticalError, MB_OK);
end;

procedure InitializeWizard();
var
  MessageText: String;
begin
  MessageText :=
    'The installer will install the Dr Transition desktop launcher, bundled backend executables, and local runtime configuration.' + #13#10#13#10 +
    'Recommended minimums:' + #13#10 +
    '- 64-bit Windows' + #13#10 +
    '- 8 GB RAM minimum, 16 GB+ recommended' + #13#10 +
    '- 10 GB free disk space before optional model downloads' + #13#10#13#10 +
    'Ollama, MySQL, and model setup are handled by the packaging helper scripts for this first installer layer.';
  CompatibilityPage := CreateOutputMsgPage(
    wpWelcome,
    'System Compatibility',
    'Review the Dr Transition desktop requirements.',
    MessageText
  );
end;
