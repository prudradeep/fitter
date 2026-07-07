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

[Files]
Source: "..\..\build\windows-installer\payload\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\.env"; DestDir: "{app}\config"; DestName: "runtime.env"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\Dr Transition"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Dr Transition"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{cmd}"; Parameters: "/C if exist ""{app}\config\runtime.env"" if not exist ""{commonappdata}\DrTransition\.env"" copy /Y ""{app}\config\runtime.env"" ""{commonappdata}\DrTransition\.env"""; Flags: runhidden
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Dr Transition"; Flags: nowait postinstall skipifsilent; Check: CanLaunchApp

[Code]
var
  CompatibilityPage: TOutputMsgWizardPage;
  DatabasePage: TInputQueryWizardPage;
  EditDatabaseDefaultsCheck: TNewCheckBox;
  ModelPage: TInputQueryWizardPage;
  DependencySetupFailed: Boolean;

procedure SetDatabaseDefaultsEditable(Editable: Boolean);
begin
  DatabasePage.Edits[0].ReadOnly := not Editable;
  DatabasePage.Edits[1].ReadOnly := not Editable;
  DatabasePage.Edits[3].ReadOnly := not Editable;
  DatabasePage.Edits[4].ReadOnly := not Editable;
end;

procedure EditDatabaseDefaultsCheckClick(Sender: TObject);
begin
  SetDatabaseDefaultsEditable(EditDatabaseDefaultsCheck.Checked);
end;

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

  DatabasePage := CreateInputQueryPage(
    CompatibilityPage.ID,
    'Database Setup',
    'Create or reuse the local Dr Transition database.',
    'Enter the MySQL administrator credentials and the application database to create. If MySQL is missing, the installer will attempt to install it first.'
  );
  DatabasePage.Add('Database name:', False);
  DatabasePage.Add('MySQL administrator user:', False);
  DatabasePage.Add('MySQL administrator password:', True);
  DatabasePage.Add('Application DB user:', False);
  DatabasePage.Add('Application DB password:', True);
  DatabasePage.Values[0] := 'dr_transition';
  DatabasePage.Values[1] := 'root';
  DatabasePage.Values[3] := 'dr_transition';
  DatabasePage.Values[4] := 'dr_transition_password';
  SetDatabaseDefaultsEditable(False);

  EditDatabaseDefaultsCheck := TNewCheckBox.Create(DatabasePage);
  EditDatabaseDefaultsCheck.Parent := DatabasePage.Surface;
  EditDatabaseDefaultsCheck.Left := DatabasePage.Edits[0].Left;
  EditDatabaseDefaultsCheck.Top := DatabasePage.Edits[0].Top - ScaleY(32);
  EditDatabaseDefaultsCheck.Width := DatabasePage.SurfaceWidth;
  EditDatabaseDefaultsCheck.Height := ScaleY(17);
  EditDatabaseDefaultsCheck.Caption := 'Edit default database settings';
  EditDatabaseDefaultsCheck.Checked := False;
  EditDatabaseDefaultsCheck.OnClick := @EditDatabaseDefaultsCheckClick;

  ModelPage := CreateInputQueryPage(
    DatabasePage.ID,
    'Ollama Model Setup',
    'Install Ollama and pull the required model.',
    'Leave the chat model as auto to let the installer choose from system RAM/GPU conditions.'
  );
  ModelPage.Add('Chat model:', False);
  ModelPage.Add('Embedding model:', False);
  ModelPage.Values[0] := 'auto';
  ModelPage.Values[1] := 'nomic-embed-text';
end;

function JsonEscape(Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

function IsValidIdentifier(Value: String): Boolean;
var
  I: Integer;
  C: Char;
begin
  Result := Length(Value) > 0;
  for I := 1 to Length(Value) do
  begin
    C := Value[I];
    if not (((C >= 'A') and (C <= 'Z')) or ((C >= 'a') and (C <= 'z')) or ((C >= '0') and (C <= '9')) or (C = '_')) then
    begin
      Result := False;
      Exit;
    end;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = DatabasePage.ID then
  begin
    if not IsValidIdentifier(DatabasePage.Values[0]) then
    begin
      MsgBox('Database name may only contain letters, numbers, and underscores.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if not IsValidIdentifier(DatabasePage.Values[3]) then
    begin
      MsgBox('Application DB user may only contain letters, numbers, and underscores.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if DatabasePage.Values[2] = '' then
    begin
      MsgBox('Enter the MySQL administrator password. If MySQL has no password, set one first, then rerun setup.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if DatabasePage.Values[4] = '' then
    begin
      MsgBox('Enter an application DB password.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
end;

function DependencyConfigJson(): String;
begin
  Result :=
    '{' + #13#10 +
    '  "DbName": "' + JsonEscape(DatabasePage.Values[0]) + '",' + #13#10 +
    '  "MySqlAdminUser": "' + JsonEscape(DatabasePage.Values[1]) + '",' + #13#10 +
    '  "MySqlAdminPassword": "' + JsonEscape(DatabasePage.Values[2]) + '",' + #13#10 +
    '  "AppDbUser": "' + JsonEscape(DatabasePage.Values[3]) + '",' + #13#10 +
    '  "AppDbPassword": "' + JsonEscape(DatabasePage.Values[4]) + '",' + #13#10 +
    '  "DefaultAppUserEmail": "admin@drtransition.local",' + #13#10 +
    '  "DefaultAppUserPassword": "DrTransition@123",' + #13#10 +
    '  "DefaultAppUserName": "Dr Transition Admin",' + #13#10 +
    '  "DefaultAppUserDesignation": "Administrator",' + #13#10 +
    '  "DefaultAppUserOrganisationType": "Local",' + #13#10 +
    '  "DefaultAppUserOrganisationName": "Dr Transition",' + #13#10 +
    '  "OllamaModel": "' + JsonEscape(ModelPage.Values[0]) + '",' + #13#10 +
    '  "OllamaEmbeddingModel": "' + JsonEscape(ModelPage.Values[1]) + '",' + #13#10 +
    '  "OllamaBaseUrl": "http://127.0.0.1:11434",' + #13#10 +
    '  "InstallMySql": true,' + #13#10 +
    '  "InstallOllama": true,' + #13#10 +
    '  "PullModels": true,' + #13#10 +
    '  "SkipDatabaseSeed": false' + #13#10 +
    '}';
end;

procedure RunDependencySetup();
var
  ConfigPath: String;
  PowerShell: String;
  ScriptPath: String;
  Params: String;
  ResultCode: Integer;
begin
  ConfigPath := ExpandConstant('{tmp}\drtransition-dependency-setup.json');
  ScriptPath := ExpandConstant('{app}\scripts\Install-DrTransitionDependencies.ps1');
  PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');

  SaveStringToFile(ConfigPath, DependencyConfigJson(), False);
  Params :=
    '-NoProfile -ExecutionPolicy Bypass -File "' + ScriptPath + '" ' +
    '-InstallDir "' + ExpandConstant('{app}') + '" ' +
    '-ConfigPath "' + ConfigPath + '"';

  WizardForm.StatusLabel.Caption := 'Installing and configuring MySQL, Ollama, database, and models. A live setup log window is open...';
  if not Exec(PowerShell, Params, '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
  begin
    DependencySetupFailed := True;
    MsgBox('Dependency setup could not be started. Check the installer log and rerun setup.', mbError, MB_OK);
  end
  else if ResultCode <> 0 then
  begin
    DependencySetupFailed := True;
    MsgBox(
      'Dependency setup failed. Check this log and rerun setup:' + #13#10#13#10 +
      ExpandConstant('{localappdata}\DrTransition\logs\installer-setup.log'),
      mbError,
      MB_OK
    );
  end
  else
  begin
    MsgBox(
      'Dr Transition setup completed.' + #13#10#13#10 +
      'Default app login:' + #13#10 +
      'Email: admin@drtransition.local' + #13#10 +
      'Password: DrTransition@123' + #13#10#13#10 +
      'Change this password after first login.',
      mbInformation,
      MB_OK
    );
  end;

  DeleteFile(ConfigPath);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RunDependencySetup();
end;

function CanLaunchApp(): Boolean;
begin
  Result := not DependencySetupFailed;
end;
