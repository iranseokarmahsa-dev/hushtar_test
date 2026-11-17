#define AppName "Hooshyar"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{BA41494C-424D-487B-9B33-25792C2A5537}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Hooshyar
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=..\installer-output
OutputBaseFilename=HOOSHYAR-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Hooshyar.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Files]
Source: "..\dist\Hooshyar\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Hooshyar"; Filename: "{app}\Hooshyar.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Hooshyar"; Filename: "{app}\Hooshyar.exe"; WorkingDir: "{app}"; Tasks: desktopicon
