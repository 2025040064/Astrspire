; Astrspire per-user installer. Built per version by tools/build_update_release.py.
; Requires: /DAppVersion, /DAppSource, /DLauncherSource, /DAssistantSource, /DOutputDir

#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef AppSource
  #error AppSource is required
#endif
#ifndef LauncherSource
  #error LauncherSource is required
#endif
#ifndef AssistantSource
  #error AssistantSource is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif

[Setup]
AppId={{8C4E2A6D-6F7B-4C31-9A2E-3B1D5F0A7C42}
AppName=Astrspire
AppVersion={#AppVersion}
AppPublisher=Astrspire
DefaultDirName={src}\Astrspire
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
CloseApplications=no
RestartApplications=no
AppMutex=Local\Astrspire.Instance.v1
SetupMutex=Local\Astrspire.Setup
OutputDir={#OutputDir}
OutputBaseFilename=Astrspire-{#AppVersion}-win-x64-Setup
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\AstrspireLauncher.exe
WizardStyle=modern
UsePreviousAppDir=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startmenuicon"; Description: "Create Start Menu shortcut"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}\versions\{#AppVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#LauncherSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#AssistantSource}\*"; DestDir: "{app}\assistant"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Astrspire"; Filename: "{app}\AstrspireLauncher.exe"; WorkingDir: "{app}"; Tasks: startmenuicon
Name: "{autodesktop}\Astrspire"; Filename: "{app}\AstrspireLauncher.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\AstrspireLauncher.exe"; Description: "{cm:LaunchProgram,Astrspire}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\active.json"
Type: filesandordirs; Name: "{app}\versions"

[Code]
var
  PreviousVersion: AnsiString;

function JsonString(Source, Key: AnsiString): AnsiString;
var
  P, Q: Integer;
  Rest: AnsiString;
begin
  Result := '';
  P := Pos('"' + Key + '"', Source);
  if P = 0 then
    exit;
  Rest := Copy(Source, P + Length(Key) + 2, Length(Source));
  Q := Pos(':', Rest);
  if Q = 0 then
    exit;
  Rest := Copy(Rest, Q + 1, Length(Rest));
  Q := Pos('"', Rest);
  if Q = 0 then
    exit;
  Rest := Copy(Rest, Q + 1, Length(Rest));
  Q := Pos('"', Rest);
  if Q = 0 then
    exit;
  Result := Copy(Rest, 1, Q - 1);
end;

function ReadActiveVersion: AnsiString;
var
  Content: AnsiString;
begin
  Result := '';
  if LoadStringFromFile(ExpandConstant('{app}\active.json'), Content) then
    Result := JsonString(Content, 'version');
end;

function AssistantTransactionPending: Boolean;
var
  Content: AnsiString;
begin
  Result := False;
  if not LoadStringFromFile(ExpandConstant('{app}\data\local\update-journal.json'), Content) then
    exit;
  // The assistant switches active.json only after its health check passes.
  Result := (Pos('"stage": "waiting"', Content) > 0) or (Pos('"stage": "installing"', Content) > 0)
    or (Pos('"stage": "verifying"', Content) > 0) or (Pos('"stage":"waiting"', Content) > 0)
    or (Pos('"stage":"installing"', Content) > 0) or (Pos('"stage":"verifying"', Content) > 0);
end;

procedure WriteActiveVersion;
var
  Target, Temporary: String;
  Content: AnsiString;
  Deleted: Boolean;
begin
  Target := ExpandConstant('{app}\active.json');
  Temporary := Target + '.new';
  Content := '{"version":"{#AppVersion}","previous":"' + PreviousVersion + '"}';
  if SaveStringToFile(Temporary, Content, False) then
  begin
    Deleted := DeleteFile(Target);
    if not RenameFile(Temporary, Target) then
      SaveStringToFile(Target, Content, False);
  end
  else
    SaveStringToFile(Target, Content, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    PreviousVersion := ReadActiveVersion;
  if CurStep = ssPostInstall then
    if not AssistantTransactionPending then
      WriteActiveVersion;
end;
