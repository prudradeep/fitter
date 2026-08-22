#define MyAppName "Dr Transition"
#define MyAppVersion "0.1.11"
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
#ifdef OfflineAdminInstaller
#ifdef PrepackageDependenciesInstaller
OutputBaseFilename=DrTransitionOfflineAdminPrepackagedSetup-{#MyAppVersion}
#else
OutputBaseFilename=DrTransitionOfflineAdminOnlineSetup-{#MyAppVersion}
#endif
#else
#ifdef PrepackageDependenciesInstaller
OutputBaseFilename=DrTransitionPrepackagedSetup-{#MyAppVersion}
#else
OutputBaseFilename=DrTransitionOnlineSetup-{#MyAppVersion}
#endif
#endif
Compression=lzma2/ultra64
SolidCompression=yes
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
#ifdef OfflineAdminInstaller
Source: "config\offline-admin.env"; DestDir: "{app}\config"; DestName: ".env"; Flags: ignoreversion
#else
Source: "..\..\.env.client.dev"; DestDir: "{app}\config"; DestName: ".env"; Flags: ignoreversion skipifsourcedoesntexist
#endif
Source: "scripts\Get-ModelRecommendation.ps1"; Flags: dontcopy

[Icons]
Name: "{group}\Dr Transition"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Dr Transition"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{cmd}"; Parameters: "/C if exist ""{app}\config\.env"" if not exist ""{commonappdata}\DrTransition\.env"" copy /Y ""{app}\config\.env"" ""{commonappdata}\DrTransition\.env"""; Flags: runhidden
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Dr Transition"; Flags: nowait postinstall skipifsilent; Check: CanLaunchApp

[Code]
var
  CompatibilityPage: TOutputMsgWizardPage;
  DatabasePage: TInputQueryWizardPage;
  EditDatabaseDefaultsCheck: TNewCheckBox;
  ModelPage: TInputQueryWizardPage;
  ModelRecommendationProgressPage: TOutputProgressWizardPage;
  DependencySetupFailed: Boolean;
  SetupStoppedByCompatibility: Boolean;
  CredentialsForCopy: String;
  CopyCredentialsButton: TNewButton;
  TestMySqlConnectionButton: TNewButton;

procedure SetDatabaseDefaultsEditable(Editable: Boolean);
begin
  DatabasePage.Edits[0].ReadOnly := not Editable;
  DatabasePage.Edits[1].ReadOnly := not Editable;
  DatabasePage.Edits[3].ReadOnly := not Editable;
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

function PowerShellStringLiteral(Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '''', '''''', True);
  Result := '''' + Result + '''';
end;

procedure CopyCredentialsButtonClick(Sender: TObject);
var
  PowerShell: String;
  CredentialsPath: String;
  Params: String;
  ResultCode: Integer;
begin
  CredentialsPath := ExpandConstant('{tmp}\drtransition-admin-credentials-copy.txt');
  SaveStringToFile(CredentialsPath, CredentialsForCopy, False);
  PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  Params :=
    '-NoProfile -ExecutionPolicy Bypass -Command ' +
    '"Add-Type -AssemblyName System.Windows.Forms; ' +
    '[System.Windows.Forms.Clipboard]::SetText((Get-Content -LiteralPath ''' + CredentialsPath + ''' -Raw))"';

  if Exec(PowerShell, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
    MsgBox('Admin credentials copied to the clipboard.', mbInformation, MB_OK)
  else
    MsgBox('Could not copy admin credentials automatically. The credentials are shown in the setup completion message.', mbError, MB_OK);

  DeleteFile(CredentialsPath);
end;

function RunMySqlConnectionTest(AdminUser: String; AdminPassword: String): Integer;
var
  PowerShell: String;
  TestScriptPath: String;
  Script: String;
  Params: String;
  ResultCode: Integer;
begin
  PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  TestScriptPath := ExpandConstant('{tmp}\drtransition-mysql-connection-test.ps1');

  Script :=
    '$ErrorActionPreference = "Stop"' + #13#10 +
    'function Find-CommandPath { param([string]$Name) $command = Get-Command $Name -ErrorAction SilentlyContinue; if ($command) { return $command.Source }; return $null }' + #13#10 +
    'function Find-MySqlExe {' + #13#10 +
    '  $commandPath = Find-CommandPath "mysql.exe"; if ($commandPath) { return $commandPath }' + #13#10 +
    '  $candidates = @(' + #13#10 +
    '    "$env:ProgramFiles\MySQL\MySQL Server 8.4\bin\mysql.exe",' + #13#10 +
    '    "$env:ProgramFiles\MySQL\MySQL Server 8.3\bin\mysql.exe",' + #13#10 +
    '    "$env:ProgramFiles\MySQL\MySQL Server 8.2\bin\mysql.exe",' + #13#10 +
    '    "$env:ProgramFiles\MySQL\MySQL Server 8.1\bin\mysql.exe",' + #13#10 +
    '    "$env:ProgramFiles\MySQL\MySQL Server 8.0\bin\mysql.exe",' + #13#10 +
    '    "${env:ProgramFiles(x86)}\MySQL\MySQL Server 8.0\bin\mysql.exe",' + #13#10 +
    '    "$env:ProgramFiles\MariaDB 11.4\bin\mysql.exe",' + #13#10 +
    '    "$env:ProgramFiles\MariaDB 11.3\bin\mysql.exe",' + #13#10 +
    '    "$env:ProgramFiles\MariaDB 11.2\bin\mysql.exe",' + #13#10 +
    '    "$env:ProgramFiles\MariaDB 11.1\bin\mysql.exe",' + #13#10 +
    '    "$env:ProgramFiles\MariaDB 11.0\bin\mysql.exe",' + #13#10 +
    '    "$env:ProgramFiles\MariaDB 10.11\bin\mysql.exe"' + #13#10 +
    '  )' + #13#10 +
    '  foreach ($candidate in $candidates) { if (Test-Path -LiteralPath $candidate) { return $candidate } }' + #13#10 +
    '  return $null' + #13#10 +
    '}' + #13#10 +
    '$mysql = Find-MySqlExe' + #13#10 +
    'if (-not $mysql) { exit 2 }' + #13#10 +
    '$previous = $env:MYSQL_PWD' + #13#10 +
    'try {' + #13#10 +
    '  $user = ' + PowerShellStringLiteral(AdminUser) + #13#10 +
    '  $password = ' + PowerShellStringLiteral(AdminPassword) + #13#10 +
    '  if ([string]::IsNullOrEmpty($password)) { Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue } else { $env:MYSQL_PWD = $password }' + #13#10 +
    '  & $mysql --protocol=tcp -h 127.0.0.1 -P 3306 -u $user --connect-timeout=5 --batch --skip-column-names -e "SELECT 1; SHOW GRANTS FOR CURRENT_USER();"' + #13#10 +
    '  if ($LASTEXITCODE -eq 0) { exit 0 }' + #13#10 +
    '  exit 1' + #13#10 +
    '} catch {' + #13#10 +
    '  exit 1' + #13#10 +
    '} finally {' + #13#10 +
    '  if ($null -eq $previous) { Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue } else { $env:MYSQL_PWD = $previous }' + #13#10 +
    '}' + #13#10;

  SaveStringToFile(TestScriptPath, Script, False);
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + TestScriptPath + '"';

  if not Exec(PowerShell, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Result := -1
  else
    Result := ResultCode;

  DeleteFile(TestScriptPath);
end;

procedure OpenMySqlRootPasswordResetInstructions();
var
  ResultCode: Integer;
begin
  ShellExec(
    'open',
    'https://dev.mysql.com/doc/refman/8.4/en/resetting-permissions.html',
    '',
    '',
    SW_SHOWNORMAL,
    ewNoWait,
    ResultCode
  );
end;

function ShowMySqlCredentialFailureOptions(): Integer;
var
  Form: TSetupForm;
  MessageLabel: TNewStaticText;
  RetryButton: TNewButton;
  AnotherAccountButton: TNewButton;
  ResetButton: TNewButton;
begin
  Form := CreateCustomForm(ScaleX(360), ScaleY(155), False, True);
  try
    Form.Caption := 'MySQL connection failed';

    MessageLabel := TNewStaticText.Create(Form);
    MessageLabel.Parent := Form;
    MessageLabel.Left := ScaleX(16);
    MessageLabel.Top := ScaleY(14);
    MessageLabel.Width := ScaleX(328);
    MessageLabel.Height := ScaleY(50);
    MessageLabel.WordWrap := True;
    MessageLabel.Caption :=
      'Unable to connect to MySQL.' + #13#10 +
      'The administrator account may be incorrect, or the MySQL service may not be running.';

    RetryButton := TNewButton.Create(Form);
    RetryButton.Parent := Form;
    RetryButton.Left := ScaleX(16);
    RetryButton.Top := ScaleY(72);
    RetryButton.Width := ScaleX(105);
    RetryButton.Height := ScaleY(28);
    RetryButton.Caption := 'Retry password';
    RetryButton.ModalResult := 1;

    AnotherAccountButton := TNewButton.Create(Form);
    AnotherAccountButton.Parent := Form;
    AnotherAccountButton.Left := ScaleX(129);
    AnotherAccountButton.Top := ScaleY(72);
    AnotherAccountButton.Width := ScaleX(215);
    AnotherAccountButton.Height := ScaleY(28);
    AnotherAccountButton.Caption := 'Use another administrator account';
    AnotherAccountButton.ModalResult := 2;

    ResetButton := TNewButton.Create(Form);
    ResetButton.Parent := Form;
    ResetButton.Left := ScaleX(16);
    ResetButton.Top := ScaleY(108);
    ResetButton.Width := ScaleX(328);
    ResetButton.Height := ScaleY(28);
    ResetButton.Caption := 'Reset MySQL root password';
    ResetButton.ModalResult := 3;

    Result := Form.ShowModal;
  finally
    Form.Free;
  end;
end;

function EnsureMySqlAdminCanConnect(ShowSuccess: Boolean): Boolean;
var
  TestResult: Integer;
  Choice: Integer;
begin
  Result := False;
  TestResult := RunMySqlConnectionTest(DatabasePage.Values[1], DatabasePage.Values[2]);

  if TestResult = 2 then
  begin
    if ShowSuccess then
      MsgBox(
        'MySQL is not installed on this computer.' + #13#10#13#10 +
        'Dr Transition Setup will install and configure MySQL automatically.',
        mbInformation,
        MB_OK);
    Result := True;
    Exit;
  end;

  if TestResult = 0 then
  begin
    if ShowSuccess then
      MsgBox(
        'Connection successful.' + #13#10#13#10 +
        'The supplied MySQL administrator credentials have been verified.',
        mbInformation,
        MB_OK);
    Result := True;
    Exit;
  end;

  Choice := ShowMySqlCredentialFailureOptions();
  if Choice = 1 then
  begin
    WizardForm.ActiveControl := DatabasePage.Edits[2];
    DatabasePage.Edits[2].SelectAll;
  end
  else if Choice = 2 then
  begin
    WizardForm.ActiveControl := DatabasePage.Edits[1];
    DatabasePage.Edits[1].SelectAll;
  end
  else if Choice = 3 then
  begin
    OpenMySqlRootPasswordResetInstructions();
    WizardForm.ActiveControl := DatabasePage.Edits[2];
    DatabasePage.Edits[2].SelectAll;
  end;
end;

procedure TestMySqlConnectionButtonClick(Sender: TObject);
begin
  EnsureMySqlAdminCanConnect(True);
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
#ifdef OfflineAdminInstaller
    'Ollama, MySQL, and model setup are handled by the packaging helper scripts for this offline/admin installer.';
#else
    'Ollama, SQLite, and model setup are handled by the packaging helper scripts for this sync-client installer.';
#endif
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
    'Enter the local MySQL credentials. If MySQL is missing, setup will install it automatically.'
  );
  DatabasePage.Add('Database name:', False);
  DatabasePage.Add('MySQL administrator user:', False);
  DatabasePage.Add('MySQL administrator password:', True);
  DatabasePage.Add('Application DB user:', False);
  DatabasePage.Add('Application DB password:', True);
  DatabasePage.Values[0] := 'dr_transition';
  DatabasePage.Values[1] := 'root';
  DatabasePage.Values[3] := 'dr_transition';
  DatabasePage.Values[4] := '';
  SetDatabaseDefaultsEditable(False);

  { Place the advanced checkbox on the bottom navigation row, at the left side. }
  EditDatabaseDefaultsCheck := TNewCheckBox.Create(WizardForm);
  EditDatabaseDefaultsCheck.Parent := WizardForm;
  EditDatabaseDefaultsCheck.Left := ScaleX(24);
  EditDatabaseDefaultsCheck.Top :=
    WizardForm.NextButton.Top +
    ((WizardForm.NextButton.Height - ScaleY(18)) div 2);
  EditDatabaseDefaultsCheck.Width :=
    WizardForm.BackButton.Left - EditDatabaseDefaultsCheck.Left - ScaleX(16);
  EditDatabaseDefaultsCheck.Height := ScaleY(18);
  EditDatabaseDefaultsCheck.Caption := 'Advanced: customize database name and users';
  EditDatabaseDefaultsCheck.Checked := False;
  EditDatabaseDefaultsCheck.Font.Style := [fsBold];
  EditDatabaseDefaultsCheck.Visible := False;
  EditDatabaseDefaultsCheck.OnClick := @EditDatabaseDefaultsCheckClick;

  TestMySqlConnectionButton := TNewButton.Create(DatabasePage);
  TestMySqlConnectionButton.Parent := DatabasePage.Surface;
  DatabasePage.Edits[2].Width := DatabasePage.Edits[2].Width - ScaleX(190);
  TestMySqlConnectionButton.Left := DatabasePage.Edits[2].Left + DatabasePage.Edits[2].Width + ScaleX(8);
  TestMySqlConnectionButton.Top := DatabasePage.Edits[2].Top - ScaleY(1);
  TestMySqlConnectionButton.Width := ScaleX(182);
  TestMySqlConnectionButton.Height := DatabasePage.Edits[2].Height + ScaleY(2);
  TestMySqlConnectionButton.Caption := 'Test MySQL connection';
  TestMySqlConnectionButton.OnClick := @TestMySqlConnectionButtonClick;

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

  ModelRecommendationProgressPage := CreateOutputProgressPage(
    'Model Recommendation',
    'Checking this computer''s RAM and GPU to choose the best local model.'
  );

#ifdef OfflineAdminInstaller
  { Place the credentials button in the bottom-left navigation area. }
  { This prevents it from overlapping the Launch checkbox on the Finished page. }
  CopyCredentialsButton := TNewButton.Create(WizardForm);
  CopyCredentialsButton.Parent := WizardForm;
  CopyCredentialsButton.Left := ScaleX(24);
  CopyCredentialsButton.Top := WizardForm.NextButton.Top;
  CopyCredentialsButton.Width := ScaleX(190);
  CopyCredentialsButton.Height := WizardForm.NextButton.Height;
  CopyCredentialsButton.Caption := 'Copy admin credentials';
  CopyCredentialsButton.Visible := False;
  CopyCredentialsButton.OnClick := @CopyCredentialsButtonClick;
#endif
end;

function JsonEscape(Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

function IsAutoModel(Value: String): Boolean;
begin
  Result := (CompareText(Value, '') = 0) or (CompareText(Value, 'auto') = 0) or (CompareText(Value, 'none') = 0);
end;

procedure ShowModelRecommendationProgress();
begin
  ModelRecommendationProgressPage.SetText(
    'Checking this computer''s RAM and GPU to choose the best local model.' + #13#10#13#10 +
    'This can take a minute; setup is still working.',
    ''
  );
  ModelRecommendationProgressPage.SetProgress(35, 100);
  ModelRecommendationProgressPage.Show;
  WizardForm.Refresh;
end;

procedure HideModelRecommendationProgress();
begin
  ModelRecommendationProgressPage.Hide;
  WizardForm.Refresh;
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

  ShowModelRecommendationProgress();
  try
    ResultCode := RunRecommendedModelCheck(Reason, RecommendedModel);
  finally
    HideModelRecommendationProgress();
  end;

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

  ShowModelRecommendationProgress();
  try
    ResultCode := RunRecommendedModelCheck(Reason, RecommendedModel);
  finally
    HideModelRecommendationProgress();
  end;

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

#ifdef OfflineAdminInstaller
  if CurPageID = DatabasePage.ID then
  begin
    if Trim(DatabasePage.Values[0]) = '' then
    begin
      MsgBox(
        'Please enter a database name.' + #13#10#13#10 +
        'The default database name is "dr_transition".',
        mbError,
        MB_OK);
      WizardForm.ActiveControl := DatabasePage.Edits[0];
      Result := False;
      Exit;
    end;

    if not IsValidIdentifier(DatabasePage.Values[0]) then
    begin
      MsgBox(
        'Invalid database name.' + #13#10#13#10 +
        'Only letters (A-Z), numbers (0-9), and underscores (_) are allowed.',
        mbError,
        MB_OK);
      WizardForm.ActiveControl := DatabasePage.Edits[0];
      DatabasePage.Edits[0].SelectAll;
      Result := False;
      Exit;
    end;

    if Trim(DatabasePage.Values[1]) = '' then
    begin
      MsgBox(
        'Please enter the MySQL administrator user name.' + #13#10#13#10 +
        'The administrator user is usually "root".',
        mbError,
        MB_OK);
      WizardForm.ActiveControl := DatabasePage.Edits[1];
      Result := False;
      Exit;
    end;

    if Trim(DatabasePage.Values[3]) = '' then
    begin
      MsgBox(
        'Please enter an Application Database user name.' + #13#10#13#10 +
        'This account will be used by Dr Transition to access its local database.',
        mbError,
        MB_OK);
      WizardForm.ActiveControl := DatabasePage.Edits[3];
      Result := False;
      Exit;
    end;

    if not IsValidIdentifier(DatabasePage.Values[3]) then
    begin
      MsgBox(
        'Invalid Application Database user name.' + #13#10#13#10 +
        'Only letters (A-Z), numbers (0-9), and underscores (_) are allowed.',
        mbError,
        MB_OK);
      WizardForm.ActiveControl := DatabasePage.Edits[3];
      DatabasePage.Edits[3].SelectAll;
      Result := False;
      Exit;
    end;

    if DatabasePage.Values[4] = '' then
    begin
      MsgBox(
        'Please enter an Application Database password.' + #13#10#13#10 +
        'This password will be used by Dr Transition to connect to the local database.',
        mbError,
        MB_OK);
      WizardForm.ActiveControl := DatabasePage.Edits[4];
      Result := False;
      Exit;
    end;

    if (DatabasePage.Values[4] = 'dr_transition_password') or
       (DatabasePage.Values[4] = 'drtransition_password') then
    begin
      MsgBox(
        'The Application Database password cannot use the sample/default password.' + #13#10#13#10 +
        'Please choose a strong, unique password.',
        mbError,
        MB_OK);
      WizardForm.ActiveControl := DatabasePage.Edits[4];
      DatabasePage.Edits[4].SelectAll;
      Result := False;
      Exit;
    end;

    if not EnsureMySqlAdminCanConnect(False) then
    begin
      Result := False;
      Exit;
    end;
  end;
#endif

  if CurPageID = ModelPage.ID then
  begin
    Result := EnsureRecommendedModelSupported();
    Exit;
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
#ifndef OfflineAdminInstaller
  if PageID = DatabasePage.ID then
    Result := True;
#endif
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if SetupStoppedByCompatibility then
    Confirm := False;
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
    '  "OllamaModel": "' + JsonEscape(ModelPage.Values[0]) + '",' + #13#10 +
    '  "OllamaEmbeddingModel": "' + JsonEscape(ModelPage.Values[1]) + '",' + #13#10 +
    '  "OllamaBaseUrl": "http://127.0.0.1:11434",' + #13#10 +
#ifdef OfflineAdminInstaller
    '  "InstallMySql": true,' + #13#10 +
#else
    '  "InstallMySql": false,' + #13#10 +
#endif
    '  "InstallOllama": true,' + #13#10 +
    '  "PullModels": true,' + #13#10 +
#ifdef OfflineAdminInstaller
    '  "DefaultAppUserEmail": "admin@drtransition.local",' + #13#10 +
    '  "DefaultAppUserName": "Dr Transition Admin",' + #13#10 +
    '  "DefaultAppUserDesignation": "Administrator",' + #13#10 +
    '  "DefaultAppUserOrganisationType": "Local",' + #13#10 +
    '  "DefaultAppUserOrganisationName": "Dr Transition",' + #13#10 +
    '  "DefaultAppUserRole": "admin",' + #13#10 +
    '  "DefaultAppUserCredentialsPath": "' + JsonEscape(ExpandConstant('{tmp}\drtransition-default-admin.txt')) + '",' + #13#10 +
    '  "DisableSync": true,' + #13#10 +
    '  "IncludeBasicData": true,' + #13#10 +
    '  "SeedPromptsFromFiles": true,' + #13#10 +
    '  "ReindexSectorPrompts": true,' + #13#10 +
    '  "SeedMainKbFromFiles": true,' + #13#10 +
    '  "SkipDefaultAppUser": false,' + #13#10 +
    '  "SkipReferenceData": false,' + #13#10 +
#else
    '  "SkipDefaultAppUser": true,' + #13#10 +
    '  "SkipReferenceData": true,' + #13#10 +
#endif
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
  CredentialsPath: String;
  CredentialsText: AnsiString;
begin
  ConfigPath := ExpandConstant('{tmp}\drtransition-dependency-setup.json');
  CredentialsPath := ExpandConstant('{tmp}\drtransition-default-admin.txt');
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
#ifdef OfflineAdminInstaller
    if FileExists(CredentialsPath) and LoadStringFromFile(CredentialsPath, CredentialsText) then
    begin
      CredentialsForCopy := Trim(CredentialsText);
      MsgBox(
        'Dr Transition offline admin setup completed.' + #13#10#13#10 +
        'Seeded admin user:' + #13#10 +
        CredentialsForCopy + #13#10#13#10 +
        'Bundled KB PDFs are being seeded into the local knowledge base in the background.' + #13#10#13#10 +
        'You can also copy these credentials from the final setup screen.',
        mbInformation,
        MB_OK
      );
    end
    else
      MsgBox(
        'Dr Transition offline admin setup completed.' + #13#10#13#10 +
        'The local database was seeded, and bundled KB PDFs are being seeded in the background. Check the setup log for the admin user details.',
        mbInformation,
        MB_OK
      );
#else
    MsgBox(
      'Dr Transition setup completed.' + #13#10#13#10 +
      'No default app user was created. Create your account from the sign-up screen when you first open the app.',
      mbInformation,
      MB_OK
    );
#endif
  end;

  DeleteFile(ConfigPath);
  DeleteFile(CredentialsPath);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { Show the advanced database checkbox only on the Database Setup page. }
  EditDatabaseDefaultsCheck.Visible := (CurPageID = DatabasePage.ID);

#ifdef OfflineAdminInstaller
  if CurPageID = wpFinished then
    CopyCredentialsButton.Visible := (Trim(CredentialsForCopy) <> '')
  else
    CopyCredentialsButton.Visible := False;
#endif
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
