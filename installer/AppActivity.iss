#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#define MyAppName "App Activity"
#define MyAppPublisher "acangialosi"
#define MyAppExeName "AppActivity.exe"

[Setup]
AppId={{97B02586-B4C9-46CB-A04E-517D59383398}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\AppActivity
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\artifacts
OutputBaseFilename=AppActivity-Setup
SetupIconFile=..\assets\app_activity.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Files]
Source: "..\dist\AppActivity\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\App Activity Dashboard"; Filename: "{app}\{#MyAppExeName}"; Parameters: "dashboard"
Name: "{group}\Uninstall App Activity"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "stop"; Flags: runhidden waituntilterminated; StatusMsg: "Stopping an existing App Activity process..."
Filename: "{app}\{#MyAppExeName}"; Parameters: "install"; Flags: runhidden waituntilterminated; StatusMsg: "Starting App Activity..."

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "StopAndUnregisterAppActivity"
