#ifndef MyAppVersion
  #define MyAppVersion "0.3.1"
#endif
#define MyAppName "OrdenIA"
#define MyAppPublisher "OrdenIA Project"
#define MyAppExeName "OrdenIA.exe"

[Setup]
AppId={{A91CE40E-55EF-4E98-A5AC-7DAD18CE7C75}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\OrdenIA
DefaultGroupName=OrdenIA
DisableProgramGroupPage=yes
OutputDir=..\..\dist\installer
OutputBaseFilename=OrdenIA-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=OrdenIA - Asistente local para organizar archivos
VersionInfoProductName={#MyAppName}

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos adicionales:"; Flags: unchecked

[Files]
Source: "..\..\dist\OrdenIA\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\OrdenIA"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\OrdenIA"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Iniciar OrdenIA"; Flags: nowait postinstall skipifsilent
