#define MyAppName "Dr Transition"
#define MyAppVersion "0.1.2"
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

[Files]
Source: "..\..\build\windows-installer\payload\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "scripts\Get-ModelRecommendation.ps1"; Flags: dontcopy

[Icons]
Name: "{group}\Dr Transition"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Dr Transition"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Dr Transition"; Flags: nowait postinstall skipifsilent; Check: CanLaunchApp

[Code]
var
  CompatibilityPage: TOutputMsgWizardPage;
  BackendPage: TInputQueryWizardPage;
  ModelPage: TInputQueryWizardPage;
  DependencySetupFailed: Boolean;
  SetupStoppedByCompatibility: Boolean;

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
    'The installer will install the Dr Transition desktop launcher and local runtime configuration.' + #13#10#13#10 +
    'Recommended minimums:' + #13#10 +
    '- 64-bit Windows' + #13#10 +
    '- 8 GB RAM minimum, 16 GB+ recommended' + #13#10 +
    '- 10 GB free disk space before optional model downloads' + #13#10#13#10 +
    'The hosted backend provides auth, MySQL persistence, knowledge sync, and validated evidence storage. The desktop app uses local Ollama models for LLM and embedding work.';
  CompatibilityPage := CreateOutputMsgPage(
    wpWelcome,
    'System Compatibility',
    'Review the Dr Transition desktop requirements.',
    MessageText
  );

  BackendPage := CreateInputQueryPage(
    CompatibilityPage.ID,
    'Hosted Backend',
    'Connect this desktop app to the hosted Dr Transition backend.',
    'The hosted backend owns authentication, MySQL persistence, knowledge sync, and validated evidence storage.'
  );
  BackendPage.Add('Backend URL:', False);
  BackendPage.Values[0] := 'https://your-hosted-dr-transition.example/';

  ModelPage := CreateInputQueryPage(
    BackendPage.ID,
    'Ollama Model Setup',
    'Install Ollama and pull the required model.',
    'Leave the chat model as auto to let the installer choose from system RAM/GPU conditions.'
  );
  ModelPage.Add('Chat model:', False);
  ModelPage.Add('Embedding model:', False);
  ModelPage.Values[0] := 'auto';
  ModelPage.Values[1] := 'nomic-embed-text';
end;

function EnvEscape(Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, #13, '', True);
  StringChangeEx(Result, #10, '', True);
end;

function PowerShellStringLiteral(Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '''', '''''', True);
  Result := '''' + Result + '''';
end;

function IsAutoModel(Value: String): Boolean;
begin
  Result := (CompareText(Value, '') = 0) or (CompareText(Value, 'auto') = 0) or (CompareText(Value, 'none') = 0);
end;

function IsHttpUrl(Value: String): Boolean;
begin
  Result := (CompareText(Copy(Value, 1, 7), 'http://') = 0) or (CompareText(Copy(Value, 1, 8), 'https://') = 0);
end;

function CleanModelName(Value: String): String;
var
  I: Integer;
  C: Char;
begin
  Result := Trim(Value);
  I := 1;
  while I <= Length(Result) do
  begin
    C := Result[I];
    if (((C >= 'A') and (C <= 'Z')) or ((C >= 'a') and (C <= 'z')) or ((C >= '0') and (C <= '9'))) then
      Break;
    I := I + 1;
  end;
  if I > 1 then
    Result := Copy(Result, I, MaxInt);
end;

function RunRecommendedModelCheck(var Reason: AnsiString; var RecommendedModel: AnsiString): Integer;
var
  PowerShell: String;
  RecommendationScriptPath: String;
  CheckScriptPath: String;
  ReasonPath: String;
  ModelPath: String;
  Script: String;
  Params: String;
  ResultCode: Integer;
begin
  ExtractTemporaryFile('Get-ModelRecommendation.ps1');
  PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  RecommendationScriptPath := ExpandConstant('{tmp}\Get-ModelRecommendation.ps1');
  CheckScriptPath := ExpandConstant('{tmp}\drtransition-model-check.ps1');
  ReasonPath := ExpandConstant('{tmp}\drtransition-model-check.reason.txt');
  ModelPath := ExpandConstant('{tmp}\drtransition-model-check.model.txt');

  DeleteFile(ReasonPath);
  DeleteFile(ModelPath);

  Script :=
    '$ErrorActionPreference = "Stop"' + #13#10 +
    '$recommendationScript = ' + PowerShellStringLiteral(RecommendationScriptPath) + #13#10 +
    '$reasonPath = ' + PowerShellStringLiteral(ReasonPath) + #13#10 +
    '$modelPath = ' + PowerShellStringLiteral(ModelPath) + #13#10 +
    'try {' + #13#10 +
    '  function Convert-DisplayRegistryValueToUInt64 {' + #13#10 +
    '    param([object]$Value)' + #13#10 +
    '    if ($null -eq $Value) { return [uint64]0 }' + #13#10 +
    '    if ($Value -is [byte[]]) {' + #13#10 +
    '      if ($Value.Length -ge 8) { return [BitConverter]::ToUInt64($Value, 0) }' + #13#10 +
    '      if ($Value.Length -ge 4) { return [uint64][BitConverter]::ToUInt32($Value, 0) }' + #13#10 +
    '      return [uint64]0' + #13#10 +
    '    }' + #13#10 +
    '    try { return [uint64]$Value } catch { return [uint64]0 }' + #13#10 +
    '  }' + #13#10 +
    '  function Convert-DisplayRegistryString {' + #13#10 +
    '    param([object]$Value)' + #13#10 +
    '    if ($null -eq $Value) { return "" }' + #13#10 +
    '    if ($Value -is [byte[]]) { return ([Text.Encoding]::Unicode.GetString($Value)).Trim([char]0) }' + #13#10 +
    '    return [string]$Value' + #13#10 +
    '  }' + #13#10 +
    '  function Get-VideoControllerDedicatedVramGb {' + #13#10 +
    '    param([object]$Gpu)' + #13#10 +
    '    if ($null -eq $Gpu) { return 0 }' + #13#10 +
    '    $adapterRamBytes = Convert-DisplayRegistryValueToUInt64 -Value $Gpu.AdapterRAM' + #13#10 +
    '    $registryBytes = [uint64]0' + #13#10 +
    '    $gpuName = [string]$Gpu.Name' + #13#10 +
    '    $displayClassPath = "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"' + #13#10 +
    '    if (Test-Path -LiteralPath $displayClassPath) {' + #13#10 +
    '      foreach ($key in Get-ChildItem -LiteralPath $displayClassPath -ErrorAction SilentlyContinue) {' + #13#10 +
    '        $props = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue' + #13#10 +
    '        if ($null -eq $props) { continue }' + #13#10 +
    '        $driverDesc = [string]$props.DriverDesc' + #13#10 +
    '        $adapterString = Convert-DisplayRegistryString -Value $props.''HardwareInformation.AdapterString''' + #13#10 +
    '        if ($driverDesc -ne $gpuName -and $adapterString -ne $gpuName) { continue }' + #13#10 +
    '        $candidateBytes = Convert-DisplayRegistryValueToUInt64 -Value $props.''HardwareInformation.qwMemorySize''' + #13#10 +
    '        if ($candidateBytes -eq 0) { $candidateBytes = Convert-DisplayRegistryValueToUInt64 -Value $props.''HardwareInformation.MemorySize'' }' + #13#10 +
    '        if ($candidateBytes -gt $registryBytes) { $registryBytes = $candidateBytes }' + #13#10 +
    '      }' + #13#10 +
    '    }' + #13#10 +
    '    $dedicatedBytes = if ($registryBytes -gt $adapterRamBytes) { $registryBytes } else { $adapterRamBytes }' + #13#10 +
    '    if ($dedicatedBytes -eq 0) { return 0 }' + #13#10 +
    '    return [math]::Round($dedicatedBytes / 1GB, 1)' + #13#10 +
    '  }' + #13#10 +
    '  $computer = Get-CimInstance Win32_ComputerSystem' + #13#10 +
    '  $gpu = Get-CimInstance Win32_VideoController | Sort-Object AdapterRAM -Descending | Select-Object -First 1' + #13#10 +
    '  $ramGb = [math]::Round($computer.TotalPhysicalMemory / 1GB, 1)' + #13#10 +
    '  $gpuVramGb = Get-VideoControllerDedicatedVramGb -Gpu $gpu' + #13#10 +
    '  $gpuName = if ($gpu.Name) { [string]$gpu.Name } else { "" }' + #13#10 +
    '  $recommendation = & $recommendationScript -RamGb $ramGb -GpuVramGb $gpuVramGb -GpuName $gpuName | ConvertFrom-Json' + #13#10 +
    '  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)' + #13#10 +
    '  [System.IO.File]::WriteAllText($reasonPath, [string]$recommendation.reason, $utf8NoBom)' + #13#10 +
    '  [System.IO.File]::WriteAllText($modelPath, [string]$recommendation.recommendedModel, $utf8NoBom)' + #13#10 +
    '  if ([string]$recommendation.recommendedModel -eq "none") { exit 42 }' + #13#10 +
    '  exit 0' + #13#10 +
    '} catch {' + #13#10 +
    '  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)' + #13#10 +
    '  [System.IO.File]::WriteAllText($reasonPath, $_.Exception.Message, $utf8NoBom)' + #13#10 +
    '  exit 1' + #13#10 +
    '}' + #13#10;

  SaveStringToFile(CheckScriptPath, Script, False);
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + CheckScriptPath + '"';

  if not Exec(PowerShell, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Result := -1;
    Exit;
  end;

  if FileExists(ReasonPath) then
    LoadStringFromFile(ReasonPath, Reason);
  if FileExists(ModelPath) then
    LoadStringFromFile(ModelPath, RecommendedModel);

  Result := ResultCode;
end;

function EnsureRecommendedModelSupported(): Boolean;
var
  ResultCode: Integer;
  Reason: AnsiString;
  RecommendedModel: AnsiString;
begin
  Result := True;
  if not IsAutoModel(ModelPage.Values[0]) then
    Exit;

  ResultCode := RunRecommendedModelCheck(Reason, RecommendedModel);
  if ResultCode = -1 then
  begin
    MsgBox('Dr Transition could not check this computer for local LLM support. Setup will close without installing the app.', mbError, MB_OK);
    SetupStoppedByCompatibility := True;
    WizardForm.Close;
    Result := False;
    Exit;
  end;

  if ResultCode = 0 then
  begin
    ModelPage.Values[0] := CleanModelName(RecommendedModel);
    Exit;
  end;

  if Trim(Reason) = '' then
    Reason := 'This computer does not meet the minimum hardware requirements for local Dr Transition model inference.';

  if ResultCode = 42 then
    MsgBox('Dr Transition cannot be installed on this computer for local model inference.' + #13#10#13#10 + Trim(Reason) + #13#10#13#10 + 'Setup will close without installing the app.', mbInformation, MB_OK)
  else
    MsgBox('Dr Transition could not complete the local model compatibility check.' + #13#10#13#10 + Trim(Reason) + #13#10#13#10 + 'Setup will close without installing the app.', mbError, MB_OK);

  SetupStoppedByCompatibility := True;
  WizardForm.Close;
  Result := False;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  Reason: AnsiString;
  RecommendedModel: AnsiString;
begin
  Result := '';
  if not IsAutoModel(ModelPage.Values[0]) then
    Exit;

  ResultCode := RunRecommendedModelCheck(Reason, RecommendedModel);
  if ResultCode = 0 then
  begin
    ModelPage.Values[0] := CleanModelName(RecommendedModel);
    Exit;
  end;

  if Trim(Reason) = '' then
    Reason := 'This computer does not meet the minimum hardware requirements for local Dr Transition model inference.';

  if ResultCode = 42 then
    Result := 'Dr Transition cannot be installed on this computer for local model inference.' + #13#10#13#10 + Trim(Reason)
  else
    Result := 'Dr Transition could not complete the local model compatibility check.' + #13#10#13#10 + Trim(Reason);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = BackendPage.ID then
  begin
    if (Trim(BackendPage.Values[0]) = '') or (not IsHttpUrl(Trim(BackendPage.Values[0]))) then
    begin
      MsgBox('Enter the hosted backend URL before continuing. It must start with http:// or https://.', mbError, MB_OK);
      Result := False;
    end;
    Exit;
  end;
  if CurPageID = ModelPage.ID then
  begin
    Result := EnsureRecommendedModelSupported();
    Exit;
  end;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if SetupStoppedByCompatibility then
    Confirm := False;
end;

function RuntimeConfigText(): String;
begin
  Result :=
    'DR_TRANSITION_BACKEND_URL=' + EnvEscape(Trim(BackendPage.Values[0])) + #13#10 +
    'OLLAMA_BASE_URL=http://127.0.0.1:11434' + #13#10 +
    'OLLAMA_MODEL=' + EnvEscape(Trim(ModelPage.Values[0])) + #13#10 +
    'OLLAMA_EMBEDDING_MODEL=' + EnvEscape(Trim(ModelPage.Values[1])) + #13#10;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    DependencySetupFailed := False;
    SaveStringToFile(ExpandConstant('{commonappdata}\DrTransition\.env'), RuntimeConfigText(), False);
  end;
end;

function CanLaunchApp(): Boolean;
begin
  Result := not DependencySetupFailed;
end;
