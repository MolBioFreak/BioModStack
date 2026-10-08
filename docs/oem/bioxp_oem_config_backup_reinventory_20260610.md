# BioXP OEM Config / Calibration Backup Re-Inventory — 2026-06-10

## Scope

This pass paused further runtime changes and re-examined the workstation backup/source tree for OEM runtime config, calibration, state, and generated files.

Searched roots:

```text
/home/dalab/Desktop/ROBOT
/home/dalab/biomodstack/biomodstack/docs/oem
```

Primary expected OEM runtime files:

```text
config.xml
Operation_parameters.xml
InspectionSettings.xml
config_history.csv
ProcessTime.xml
calreference.xml
```

## Source-proven OEM config paths

`BioXPMainWindow.GetAppDir()` builds the durable application directory from LocalApplicationData:

```text
BioXPMainWindow.cs:4140-4146
appDir = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData)
         + "\\" + AssemblyCompany + "\\" + AssemblyName
Directory.CreateDirectory(appDir)
```

`ClassBioXPSettings` then reads/writes machine config files in either current working directory or `appDir`.

## Source-proven files and semantics

### `config.xml`

Constants:

```text
ClassBioXPSettings.cs:169 CONFIG_FILE = "config.xml"
```

Load order:

```text
ClassBioXPSettings.cs:2847-2854
if File.Exists("config.xml") load cwd config
else if File.Exists(m_appDir + "\\config.xml") load appDir config
else warn "Configuration file not found"
```

Save target:

```text
ClassBioXPSettings.cs:3948-3955
if File.Exists("config.xml") save cwd config
else save m_appDir + "\\config.xml"
```

Saved contents include:

```text
GenBot/SerialNumber
GenBot/Config: Version, GripperVersion, TroughVersion
GenBot/Calibration: Calibrated, cal software/tool/revision/date, liquid/gripper calibration dates
GenBot/CameraInstalled: Camera, Cameracalibrated
CalibrationFactors/Offsets: originOffsetG, gripper positions, TCDoorOpen,
  TCDoorStallGuardThreshold, TC_DOOR_VELOCITY, TC_DOOR_ACCELERATION,
  TC_DOOR_MAX_CURRENT, Z motor current/stall thresholds, etc.
AxisLimits: X/Y/Z/G minSteps/maxSteps
PositionTable: all locationID x/y/zLow/zDelta/inc_factor entries
ScalePort
InspectionSettings.xml save call
```

### `Operation_parameters.xml`

Constants:

```text
ClassBioXPSettings.cs:175 OPERATION_PARAMETERS = "Operation_parameters.xml"
```

Load/generate behavior:

```text
ClassBioXPSettings.cs:2523-2533
load cwd Operation_parameters.xml
else load appDir Operation_parameters.xml
else saveOperationParameters()
```

Save target:

```text
ClassBioXPSettings.cs:2766
val.Save(m_appDir + "\\Operation_parameters.xml")
```

Fields include operation mode, pause behavior, deck inspection, static tip loss checks, inspection logging, self test, extensive log, pressure log, thermal fault, camera check, etc.

### `InspectionSettings.xml`

Generated as part of `saveConfig()`:

```text
ClassBioXPSettings.cs:3956
m_CameraSettings.SaveInspectionSettings(m_appDir + "\\InspectionSettings.xml")
```

This is therefore expected to exist in the durable appDir when OEM config has been saved.

### `config_history.csv`

Constants:

```text
ClassBioXPSettings.cs:173 CONFIG_HISTORY = "config_history.csv"
```

History path:

```text
ClassBioXPSettings.cs:6728-6735
%LOCALAPPDATA%\\<AssemblyCompany>\\Config_History\\config_history.csv
```

Header includes deck/table positions and camera/park/trough/waste values:

```text
TC, MS, OC, RC, BSC, tips, strips, covers, pipette positions,
CAMERA_OFFSET, PARK, TROUGH, WASTE_BIN
```

### `calreference.xml`

`ClassCalibrationReference` reads from `appDir\\calreference.xml` when present, and compares it with current directory `calreference.xml`; otherwise it copies current directory references into appDir.

```text
ClassCalibrationReference.cs:66-99
```

This file is source/default/package-level reference data, and can seed/adjust config.

### `ProcessTime.xml`

`ProcessingTimeCollection.UpdatedProcessingTimeFile()` ingests CSVs from:

```text
c:\\Scripts\\Local Served Jobs
```

and writes updated processing time data when new job timings are found.

## Workstation backup inventory result

Inventory artifact:

```text
/tmp/bioxp_workstation_backup_inventory.json
```

Expected runtime files found in current workstation backup tree:

```text
config.xml: 0
Operation_parameters.xml: 0
InspectionSettings.xml: 0
config_history.csv: 0
ProcessTime.xml: 1
calreference.xml: 1
```

Found default/package files:

```text
/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/wine_probe/GenBotApp_6_3_0_1_flat/ProcessTime.xml
/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/wine_probe/GenBotApp_6_3_0_1_flat/calreference.xml
```

The ClickOnce/deploy tree also includes packaged default deploy variants, e.g.:

```text
BioXP_SSD_Backup/.deploy/Application Files/GenBotApp_6_3_0_1/calreference.xml.deploy
```

But the current searched tree does **not** contain the durable/generated appDir files with machine-specific calibration.

## Answer to the generation question

Yes, source proves the OEM application can generate and update these files.

Specifically:

- `Operation_parameters.xml` is generated if missing.
- `config.xml` is saved into appDir if no current-dir config exists.
- `InspectionSettings.xml` is written by `saveConfig()`.
- `config_history.csv` is written under LocalApplicationData config history.
- calibration workflows mutate `PositionTable`, camera/calibration flags, gripper values, and then call `saveConfig()`.
- `calreference.xml` is copied from current dir to appDir if absent and used to adjust position tables.

This means absence from the current extracted backup does **not** prove the machine never had them; it means the current backup copy likely lacks the OEM user LocalApplicationData/appDir state, or that state has not been mounted/extracted/searched yet.

## Remaining data gap

To close machine-specific Gap B, examine the original SSD or a fuller backup for:

```text
%LOCALAPPDATA%\\<AssemblyCompany>\\GenBotApp\\config.xml
%LOCALAPPDATA%\\<AssemblyCompany>\\GenBotApp\\Operation_parameters.xml
%LOCALAPPDATA%\\<AssemblyCompany>\\GenBotApp\\InspectionSettings.xml
%LOCALAPPDATA%\\<AssemblyCompany>\\GenBotApp\\calreference.xml
%LOCALAPPDATA%\\<AssemblyCompany>\\Config_History\\config_history.csv
C:\\Scripts\\Local Served Jobs\\*.csv
C:\\scripts\\snapshots\\
```

Use Windows-user profile paths on the original SSD; the exact company folder is assembly-derived, with source evidence suggesting Synthetic Genomics-style naming elsewhere in decompiled code.
