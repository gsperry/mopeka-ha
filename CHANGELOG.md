# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/).

This project is a fork of [MedVedZot/mopeka-ha](https://github.com/MedVedZot/mopeka-ha).
The history below starts from the fork; release 1.2.5 is the upstream release it was
forked from.

## [Unreleased]

## [2.0.0] - 2026-11-03

Version 2.0.0, the first release of the fork.

### Added

- Support for accounts from the Mopeka Co-Pilot app, on Mopeka's current cloud platform.
  The integration detects whether an account is on the current platform or the older
  Tank Check platform, and remembers it.
- Sensors defined with Home Assistant entity descriptions: level, fill level, volume,
  temperature, battery, and last reading, plus diagnostic battery voltage and reading
  quality sensors that are disabled by default. Units follow the Home Assistant unit
  system.
- Re-authentication when Mopeka stops accepting the saved password, and a reconfigure
  step for updating sign-in details.
- An option to leave out tanks, for example ones the built-in Mopeka Bluetooth
  integration already provides.
- A diagnostics download with credentials and identifiers redacted.
- Debug logging of every request to Amazon Cognito and Mopeka Cloud, with tokens and
  email addresses redacted.
- Spanish translation of the new setup and options screens.
- `scripts/live_check.py` for checking an account outside Home Assistant.

### Changed

- Sign-in now uses Amazon Cognito directly over Home Assistant's HTTP client and caches
  and refreshes tokens, instead of signing in again on every poll.
- The default update interval is 5 minutes (configurable from 1 to 60).
- Setup no longer asks which measurements to create. Existing entities are migrated to
  the new sensors, keeping their entity IDs, history, and units.
- Devices without a name in the cloud are named `Mopeka` and the end of the sensor's
  address.
- Requires Home Assistant 2026.9 or newer.

### Fixed

- Setup showed "Failed to connect" for every failure, including a wrong password, and
  logged nothing. Each failure now shows a specific error and is logged with its cause.
- Accounts created for the Co-Pilot app could not sign in.
- A sensor with missing or empty fields in the cloud response no longer stops setup.
- Removed code that renamed users' entity IDs and used a device registry call that
  stopped working in current Home Assistant.

### Removed

- The dependency on the `mopeka-api` package. The integration needs only `pycognito`,
  which Home Assistant already ships.
- The password field in the options. Use re-authentication or reconfigure instead.
- The `tank_type`, `tank_height`, `vertical`, `propaneButaneRatio`, and
  `volume_original_unit` entities, and the separate entities for each unit.
- Donation and sponsorship links.

## [1.2.5] - 2026-04-18

Upstream release by Aleksandr Zhuravlev that this fork starts from. See the
[upstream repository](https://github.com/MedVedZot/mopeka-ha) for its history.
