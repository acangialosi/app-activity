# App Activity

[![Download latest installer](https://img.shields.io/badge/download-latest%20installer-2ea44f?logo=windows)](https://github.com/acangialosi/app-activity/releases/download/nightly/AppActivity-Setup.exe)
[![Windows installer](https://github.com/acangialosi/app-activity/actions/workflows/windows-installer.yml/badge.svg?branch=main)](https://github.com/acangialosi/app-activity/actions/workflows/windows-installer.yml)
[![Tests](https://github.com/acangialosi/app-activity/actions/workflows/test-status.yml/badge.svg?branch=main)](https://github.com/acangialosi/app-activity/actions/workflows/test-status.yml)

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
- Select an application in the Applications list to filter the chart. Select it
  again or choose **Show all** to clear the filter. Window-title details expand
  separately, and report totals remain unchanged. The selection stays
  in place when navigating periods or refreshing data; percentage mode shows
  the application's share of all active time in each bucket.
- On desktop, the dashboard fits the browser height down to 720 pixels, with
  the application list scrolling within its panel. Shorter windows scroll the
  page; narrow screens retain the stacked layout.
- The dashboard supports light and dark themes or can follow the Windows theme.
  The selected preference is saved in the browser.
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

### Windows installer

**[Download the latest App Activity installer](https://github.com/acangialosi/app-activity/releases/download/nightly/AppActivity-Setup.exe)**

The installer includes the Python runtime, installs for the current user, and
starts App Activity automatically at sign-in.

### Run from source

Python 3.11 or newer is required. From the repository folder, run:

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
creating a release. Every push and pull request must pass the test suite before
the installer is built, and publishing occurs only after both jobs succeed.

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

When Node.js is available, the suite also runs the dashboard's JavaScript
behavior tests.

### Repository security protections

No local security-tool installation or Git hook is required. GitHub Actions
scans fetched Git history with Betterleaks and audits committed Python
dependencies with pip-audit on Windows/Python 3.11-3.14. Required checks block
merging when findings or scanner failures occur. GitHub secret push protection,
secret scanning, Dependabot alerts, and CodeQL remain enabled.

These CI checks run after a push, not on your workstation. GitHub push
protection can reject supported secret patterns during a push.
See [Security checks](docs/security-checks.md) for coverage, remediation,
limitations, and cleanup instructions for older clones.
