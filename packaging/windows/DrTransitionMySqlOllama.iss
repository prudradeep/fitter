#ifndef MyAppVersion
#define MyAppVersion "0.1.8"
#endif

#define MyAppName "Dr Transition Database and Model Setup"
#define MyAppPublisher "Dr Transition"

[Setup]
AppId={{5E69DAE7-6E4C-4F2A-9C1B-46BD6972733E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Dr Transition Dependencies
DefaultGroupName=Dr Transition Dependencies
DisableProgramGroupPage=yes
OutputDir=..\..\build\windows-dependencies-installer
#ifdef PrepackageDependenciesInstaller
OutputBaseFilename=DrTransitionDatabaseModelPrepackagedSetup-{#MyAppVersion}
#else
OutputBaseFilename=DrTransitionDatabaseModelOnlineSetup-{#MyAppVersion}
#endif
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
SetupLogging=yes
Uninstallable=yes

[Files]
Source: "..\..\build\windows-dependencies-installer\payload\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Code]
var
  DependencyPage: TInputQueryWizardPage;
  DependencySetupFailed: Boolean;

function InitializeSetup(): Boolean;
begin
  Result := IsWin64;
  if not Result then
    MsgBox('This installer requires 64-bit Windows.', mbCriticalError, MB_OK);
end;

procedure InitializeWizard();
begin
  DependencyPage := CreateInputQueryPage(
    wpWelcome,
    'Database and Model Setup',
    'Install or verify the local runtime dependencies, app database, and Ollama models.',
    'Enter the local database and model settings. Setup will install/check MySQL and Ollama, create the app database and user, and pull the selected Ollama models.'
  );
  DependencyPage.Add('Database name:', False);
  DependencyPage.Add('MySQL administrator user:', False);
  DependencyPage.Add('MySQL administrator password:', True);
  DependencyPage.Add('Application DB user:', False);
  DependencyPage.Add('Application DB password:', True);
  DependencyPage.Add('Chat model:', False);
  DependencyPage.Add('Embedding model:', False);
  DependencyPage.Values[0] := 'dr_transition';
  DependencyPage.Values[1] := 'root';
  DependencyPage.Values[3] := 'dr_transition';
  DependencyPage.Values[5] := 'auto';
  DependencyPage.Values[6] := 'nomic-embed-text';
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

function DependencyConfigJson(): String;
begin
  Result :=
    '{' + #13#10 +
    '  "DbName": "' + JsonEscape(DependencyPage.Values[0]) + '",' + #13#10 +
    '  "MySqlAdminUser": "' + JsonEscape(DependencyPage.Values[1]) + '",' + #13#10 +
    '  "MySqlAdminPassword": "' + JsonEscape(DependencyPage.Values[2]) + '",' + #13#10 +
    '  "AppDbUser": "' + JsonEscape(DependencyPage.Values[3]) + '",' + #13#10 +
    '  "AppDbPassword": "' + JsonEscape(DependencyPage.Values[4]) + '",' + #13#10 +
    '  "OllamaModel": "' + JsonEscape(DependencyPage.Values[5]) + '",' + #13#10 +
    '  "OllamaEmbeddingModel": "' + JsonEscape(DependencyPage.Values[6]) + '",' + #13#10 +
    '  "InstallMySql": true,' + #13#10 +
    '  "InstallOllama": true,' + #13#10 +
    '  "PullModels": true,' + #13#10 +
    '  "IncludeBasicData": true,' + #13#10 +
    '  "SeedPromptsFromFiles": true,' + #13#10 +
    '  "SkipDefaultAppUser": true,' + #13#10 +
    '  "SkipReferenceData": false,' + #13#10 +
    '  "SkipDatabaseSeed": false' + #13#10 +
    '}';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;

  if CurPageID = DependencyPage.ID then
  begin
    if Trim(DependencyPage.Values[0]) = '' then
    begin
      MsgBox('Please enter the database name.', mbError, MB_OK);
      WizardForm.ActiveControl := DependencyPage.Edits[0];
      Result := False;
      Exit;
    end;

    if not IsValidIdentifier(DependencyPage.Values[0]) then
    begin
      MsgBox('Database name can contain only letters, numbers, and underscores.', mbError, MB_OK);
      WizardForm.ActiveControl := DependencyPage.Edits[0];
      DependencyPage.Edits[0].SelectAll;
      Result := False;
      Exit;
    end;

    if Trim(DependencyPage.Values[1]) = '' then
    begin
      MsgBox('Please enter the MySQL administrator user name.', mbError, MB_OK);
      WizardForm.ActiveControl := DependencyPage.Edits[1];
      Result := False;
      Exit;
    end;

    if DependencyPage.Values[2] = '' then
    begin
      MsgBox(
        'Please enter the MySQL administrator password.' + #13#10#13#10 +
        'A fresh local MySQL Server install needs this password so setup can finish root account initialization.',
        mbError,
        MB_OK
      );
      WizardForm.ActiveControl := DependencyPage.Edits[2];
      Result := False;
      Exit;
    end;

    if Trim(DependencyPage.Values[3]) = '' then
    begin
      MsgBox('Please enter the application DB user.', mbError, MB_OK);
      WizardForm.ActiveControl := DependencyPage.Edits[3];
      Result := False;
      Exit;
    end;

    if not IsValidIdentifier(DependencyPage.Values[3]) then
    begin
      MsgBox('Application DB user can contain only letters, numbers, and underscores.', mbError, MB_OK);
      WizardForm.ActiveControl := DependencyPage.Edits[3];
      DependencyPage.Edits[3].SelectAll;
      Result := False;
      Exit;
    end;

    if DependencyPage.Values[4] = '' then
    begin
      MsgBox('Please enter the application DB password.', mbError, MB_OK);
      WizardForm.ActiveControl := DependencyPage.Edits[4];
      Result := False;
      Exit;
    end;

    if (DependencyPage.Values[4] = 'dr_transition_password') or
       (DependencyPage.Values[4] = 'drtransition_password') then
    begin
      MsgBox('Application DB password must not use the sample local-only password from older documentation.', mbError, MB_OK);
      WizardForm.ActiveControl := DependencyPage.Edits[4];
      DependencyPage.Edits[4].SelectAll;
      Result := False;
      Exit;
    end;

    if Trim(DependencyPage.Values[5]) = '' then
      DependencyPage.Values[5] := 'auto';

    if Trim(DependencyPage.Values[6]) = '' then
      DependencyPage.Values[6] := 'nomic-embed-text';
  end;
end;

procedure RunDependencySetup();
var
  ConfigPath: String;
  PowerShell: String;
  ScriptPath: String;
  Params: String;
  ResultCode: Integer;
begin
  ConfigPath := ExpandConstant('{tmp}\drtransition-mysql-ollama-setup.json');
  ScriptPath := ExpandConstant('{app}\scripts\Install-DrTransitionDependencies.ps1');
  PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');

  SaveStringToFile(ConfigPath, DependencyConfigJson(), False);
  Params :=
    '-NoProfile -ExecutionPolicy Bypass -File "' + ScriptPath + '" ' +
    '-InstallDir "' + ExpandConstant('{app}') + '" ' +
    '-ConfigPath "' + ConfigPath + '"';

  WizardForm.StatusLabel.Caption := 'Installing MySQL/Ollama, creating the app database, and pulling models. A live setup log window is open...';
  if not Exec(PowerShell, Params, '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
  begin
    DependencySetupFailed := True;
    MsgBox('Database and model setup could not be started. Check the installer log and rerun setup.', mbError, MB_OK);
  end
  else if ResultCode <> 0 then
  begin
    DependencySetupFailed := True;
    MsgBox(
      'Database and model setup failed. Check this log and rerun setup:' + #13#10#13#10 +
      ExpandConstant('{localappdata}\DrTransition\logs\installer-setup.log'),
      mbError,
      MB_OK
    );
  end
  else
    MsgBox('Database and model setup completed successfully.', mbInformation, MB_OK);

  DeleteFile(ConfigPath);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RunDependencySetup();
end;
