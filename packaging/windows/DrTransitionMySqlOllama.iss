#ifndef MyAppVersion
#define MyAppVersion "0.1.8"
#endif

#define MyAppName "Dr Transition Offline Dependencies"
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
OutputBaseFilename=DrTransitionOfflineDependenciesPrepackagedSetup-{#MyAppVersion}
#else
OutputBaseFilename=DrTransitionOfflineDependenciesOnlineSetup-{#MyAppVersion}
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
    'Offline Model Setup',
    'Install or verify Ollama and configure the local SQLite client.',
    'MySQL is not required. The offline/client version uses SQLite only.'
  );
  DependencyPage.Add('Chat model:', False);
  DependencyPage.Add('Embedding model:', False);
  DependencyPage.Values[0] := 'auto';
  DependencyPage.Values[1] := 'nomic-embed-text';
end;

function JsonEscape(Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

function DependencyConfigJson(): String;
begin
  Result :=
    '{' + #13#10 +
    '  "OllamaModel": "' + JsonEscape(DependencyPage.Values[0]) + '",' + #13#10 +
    '  "OllamaEmbeddingModel": "' + JsonEscape(DependencyPage.Values[1]) + '",' + #13#10 +
    '  "InstallMySql": false,' + #13#10 +
    '  "InstallOllama": true,' + #13#10 +
    '  "PullModels": true,' + #13#10 +
    '  "DisableSync": true,' + #13#10 +
    '  "IncludeBasicData": true,' + #13#10 +
    '  "SeedPromptsFromFiles": true,' + #13#10 +
    '  "SeedMainKbFromFiles": true,' + #13#10 +
    '  "SkipDefaultAppUser": true,' + #13#10 +
    '  "SkipReferenceData": false,' + #13#10 +
    '  "SkipDatabaseSeed": false' + #13#10 +
    '}';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigPath, PowerShell, ScriptPath, Params: String;
  ResultCode: Integer;
begin
  if CurStep <> ssPostInstall then
    Exit;

  ConfigPath := ExpandConstant('{tmp}\drtransition-dependency-setup.json');
  ScriptPath := ExpandConstant('{app}\scripts\Install-DrTransitionDependencies.ps1');
  PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  SaveStringToFile(ConfigPath, DependencyConfigJson(), False);
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ScriptPath + '" -InstallDir "' + ExpandConstant('{app}') + '" -ConfigPath "' + ConfigPath + '"';

  WizardForm.StatusLabel.Caption := 'Configuring SQLite, Ollama, and local models...';
  if not Exec(PowerShell, Params, '', SW_SHOW, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
  begin
    DependencySetupFailed := True;
    MsgBox('Offline dependency setup failed. Check the Dr Transition installer log.', mbError, MB_OK);
  end;
end;
