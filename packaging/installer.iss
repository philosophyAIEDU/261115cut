; SophiCut installer (Inno Setup 6). Built by packaging/build.ps1.
; Per-user install: no administrator rights needed. Re-running a newer setup upgrades in place.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8F0C5E21-7B3A-4C55-9C2E-5A1D2F3B6E90}
AppName=소피컷
AppVersion={#AppVersion}
AppVerName=소피컷 {#AppVersion}
AppPublisher=필로소피 AI 교육
AppPublisherURL=https://github.com/philosophyAIEDU/261115cut
AppUpdatesURL=https://github.com/philosophyAIEDU/261115cut/releases/latest
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\SophiCut
DefaultGroupName=소피컷
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\build\installer
OutputBaseFilename=SophiCut-Setup
SetupIconFile=..\build\sophicut.ico
UninstallDisplayIcon={app}\SophiCut.exe
UninstallDisplayName=소피컷
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 소피컷 아이콘 만들기"; GroupDescription: "바로가기:"

[InstallDelete]
; remove libraries of the previous version before copying the new ones
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\build\dist\SophiCut\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\소피컷"; Filename: "{app}\SophiCut.exe"
Name: "{autodesktop}\소피컷"; Filename: "{app}\SophiCut.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SophiCut.exe"; Description: "지금 소피컷 실행하기"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
procedure StopSophiCut();
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM SophiCut.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopSophiCut();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopSophiCut();
  Result := True;
end;
