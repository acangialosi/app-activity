# App Activity

A lightweight, local-only Windows activity tracker. It records which application
has the foreground window, separates active time from idle time, and provides
hour, day, week, month, and year views in a local browser dashboard.

## Privacy and performance

- All history stays in `%LOCALAPPDATA%\AppActivity\activity.db`.
- Application Product Names, active window titles, and timestamps are stored.
  Window titles can include document names, browser page titles, email subjects,
  or other content displayed in a title bar. Typed text, screenshots, and
  document contents are never collected.
- Historical process-name entries are automatically merged into their resolved
  application display name when that application is next observed.
- Reports aggregate time by Product Name and provide expandable per-window-title
  details beneath each application.
- Non-browser applications group changing titles by their stable Windows window.
  Browsers group by window and active page title so tab usage remains distinct.
- The activity chart uses consistent per-application colors and can switch
  between absolute time and percentage-of-bucket views.
- Previous and next controls navigate by the selected hour, day, week, month,
  or year period without mixing data between reporting windows. **Now** returns
  any selected view to its current period. The hour view uses five-minute
  timeline buckets.
- The collector samples every five seconds and uses Windows APIs plus SQLite.
- You are considered idle after five minutes without keyboard or mouse input.
- The Windows lock screen, sign-in and credential surfaces, Windows desktop,
  display-off periods, and long sampling gaps across sleep or hibernation are
  classified as idle rather than application usage.
- A dedicated chart icon in the Windows system tray shows when tracking is
  running. Click it to open the usage report; right-click it to open or exit.

## Install

Python 3.11 or newer is required. From this folder, run:

```powershell
.\app_activity.cmd install
.\app_activity.cmd dashboard
```

`install` registers App Activity under the current user's Windows sign-in startup
and starts it immediately. The dashboard is available only on this machine at
<http://127.0.0.1:8765>.

## Commands

```powershell
.\app_activity.cmd dashboard
.\app_activity.cmd status
.\app_activity.cmd start
.\app_activity.cmd stop
.\app_activity.cmd install
.\app_activity.cmd uninstall
.\app_activity.cmd uninstall --delete-data
```

Uninstalling preserves history unless `--delete-data` is included.

### Packaged Windows installer

Release builds provide `AppActivity-Setup.exe`, a per-user installer that
includes the Python runtime and requires no separate Python installation. It
installs under `%LOCALAPPDATA%\Programs\AppActivity`, registers sign-in startup,
adds Start Menu shortcuts, and preserves activity history during uninstall.
Every push to `main` replaces the installer in the rolling `nightly` prerelease.
Pushing a `v*` tag creates a permanent versioned GitHub Release. Manual workflow
runs produce a downloadable Actions artifact retained for one day without
creating a release.

To build the installer:

```powershell
winget install JRSoftware.InnoSetup
.\build.ps1
```

The output is `artifacts\AppActivity-Setup.exe`. Set
`SIGNING_CERTIFICATE_PATH` and optionally `SIGNING_CERTIFICATE_PASSWORD` to
Authenticode-sign the application and installer during the build.

## Development

```powershell
python -m unittest discover -s tests -v
```
