# App Activity

A lightweight, local-only Windows activity tracker. It records which application
has the foreground window, separates active time from idle time, and provides
day, week, month, and year views in a local browser dashboard.

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
- The activity chart uses consistent per-application colors and can switch
  between absolute time and percentage-of-bucket views.
- The collector samples every five seconds and uses Windows APIs plus SQLite.
- You are considered idle after five minutes without keyboard or mouse input.
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

## Development

```powershell
python -m unittest discover -s tests -v
```
