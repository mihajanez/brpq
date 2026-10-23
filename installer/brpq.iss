#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
[Setup]
AppName=BRPQ
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\BRPQ
DefaultGroupName=BRPQ
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=brpq-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\brpq\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\BRPQ"; Filename: "{app}\brpq.exe"
Name: "{autodesktop}\BRPQ"; Filename: "{app}\brpq.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"

[Run]
Filename: "{app}\brpq.exe"; Description: "Launch BRPQ"; Flags: nowait postinstall skipifsilent
