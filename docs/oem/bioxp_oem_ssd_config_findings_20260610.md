# BioXP OEM SSD Config Findings — 2026-06-10

## Acquisition mode

Christian connected the original BioXP SSD to the workstation. Hermes tools run in a Docker sandbox that did not expose `/dev/sda*` directly, so a short-lived root Docker helper was used with explicit `/dev/sda*` device mappings.

Mount/search mode was read-only:

```text
/dev/sda2 mounted as NTFS read-only inside helper container
```

No writes were made to the SSD.

Evidence folder on workstation:

```text
/home/dalab/Desktop/ROBOT/oem_ssd_reinventory_20260610/
```

Key generated artifacts:

```text
inventory.json
inventory_summary.txt
extracted_expected_files_manifest.json
parsed_active_config_summary_v2.json
extracted_expected_files/
```

## Main result

The missing OEM machine-specific files were found on the SSD exactly where source predicted:

```text
/Users/BioXp PC/AppData/Local/Synthetic Genomics/GenBotApp/
/Users/BioXp PC/AppData/Local/Synthetic Genomics/Config_History/
```

This confirms the prior workstation backup/deploy extraction missed the durable OEM app data directory.

## Active machine config files found

### `config.xml`

```text
source: /Users/BioXp PC/AppData/Local/Synthetic Genomics/GenBotApp/config.xml
size: 4473
sha256: 33aadf87f631cf33f2e0b4c86948c92be3b21412ca5477ea8fa8bc7848cbf475
```

Sensitive values redacted in summaries. File contains machine-specific values:

```text
SerialNumber: present [REDACTED]
Config Version: 3
GripperVersion: 1
TroughVersion: 1
Calibrated: 1
m_cal_software: 5.0.0.1
m_cal_tool: 50010-01A
m_cal_reversion: 0
m_cal_date: 9/26/2018 11:50:43 PM
m_Liquid_Cal: True
m_Liquid_Cal_date: 1/8/2019 12:23:02 PM
Camera: 1
Cameracalibrated: True
```

Important machine constants:

```text
m_originOffsetG: 4450
m_GripperClosePOS: 27350
m_GripperOpenPOS: 31400
m_GripperOpenWide: 32400
m_TCDoorOpen: 18500
m_TCDoorStallGuardThreshold: 6
m_TC_DOOR_VELOCITY: 50
m_TC_DOOR_ACCELERATION: 20
m_TC_DOOR_MAX_CURRENT: 31
m_Z_MOTOR_MAX_CURRENT_DOWN: 25
m_Z_MOTOR_MAX_CURRENT_UP: 31
m_Z_MOTOR_STALL_GUARD_THRESHOLD: 3
OutPutBufferatMS_Zlow: 0
OutlierRangeFactor: 4
```

Axis limits:

```text
X: 0..90263
Y: 0..102956
Z: 0..160000
G: 0..15000
```

Position table count:

```text
29 positions
```

Notable position examples:

```text
LOC_MS: x=26213 y=9241 zLow=83407 zDelta=37400
LOC_OC: x=26213 y=42413 zLow=90540 zDelta=31150
LOC_TC: x=67606 y=9241 zLow=71565 zDelta=35000
LOC_RC: x=67677 y=45256 zLow=87671 zDelta=43100
LOC_BSCS: x=82868 y=61772 zLow=108317 zDelta=112564
WASTE_BIN: x=92049 y=93211 zLow=0 zDelta=0
LOC_PARK: x=1506 y=71 zLow=114092 zDelta=114092
CAMERA_OFFSET: x=3499 y=-7744 zLow=3145 zDelta=6842
```

### `Operation_parameters.xml`

```text
source: /Users/BioXp PC/AppData/Local/Synthetic Genomics/GenBotApp/Operation_parameters.xml
size: 471
sha256: f18722da4fba87a0b123ee4ba91f83eac7ca20ec9d8f11f0e485d3fb5db857b1
```

### `InspectionSettings.xml`

```text
source: /Users/BioXp PC/AppData/Local/Synthetic Genomics/GenBotApp/InspectionSettings.xml
size: 20395
sha256: d38220177e7e01b3d6d50892e0ffbbe27b1eb46087c4623cd6ca4757cc80b2d7
```

### Active `processtime.xml`

```text
source: /Users/BioXp PC/AppData/Local/Synthetic Genomics/GenBotApp/processtime.xml
size: 1414
sha256: 4ed472e39626cbdef3af013532a9233326c4c913bf7b11e222e9104f0511d186
```

### `config_history.csv`

```text
source: /Users/BioXp PC/AppData/Local/Synthetic Genomics/Config_History/config_history.csv
size: 2477
sha256: ba715995d2fc63a1bcce97d50b85d9d6928b015aaf8cd93a5ddfa47a317ad57b
line_count: 4
last visible calibration row date: 9/26/2018 10:22:51 AM
cal tool: 50010-01A
revision: 0
```

The last row includes a full calibration snapshot for TC/MS/OC/RC/BSC/BSCS, tip racks, strips, covers, pipette positions, camera offset, park, trough, and waste bin.

### Active `calreference.xml`

```text
source: /Users/BioXp PC/AppData/Local/Synthetic Genomics/GenBotApp/calreference.xml
size: 840
sha256: f941cb252028a1ee649ffb4185f9b0314f7b483e7f482e926ba10efe32a94e20
```

## Additional historical/deleted config copies

The SSD also contained older/deleted copies under `$Recycle.Bin`, including several `config.xml`, `ProcessTime.xml`, and `calreference.xml` variants with different sizes/hashes. These may be useful for calibration drift/history analysis, but the active appDir files above should be treated as primary current machine config unless later evidence says otherwise.

Examples:

```text
$Recycle.Bin/.../$RD7QSAR.orig/config.xml sha256=6de163023deca9844fd709efc5d7bd70a3de63346dd0733929a372ab07332152
$Recycle.Bin/.../$RF9HABT/config.xml sha256=2e3e679f5e6669a76419c1c54d20c41fb5284af103fb30f0c656a9a10991c897
$Recycle.Bin/.../$RVZFSYY/config.xml sha256=f5c7ca326de51ec8b126e0237cbf2e31f50ac65f93dba8698f55b8986326813a
```

## Impact on prior gap assessment

This closes the major “machine-specific config not found” blocker at the data-discovery level.

Updated gap status:

```text
Gap B data location: CLOSED — files found on original SSD
Gap B runtime binding: OPEN — Linux/BMS runtime still needs to ingest/bind these values read-only first
Gap B parity proof: OPEN — after binding, compare live defaults/current runtime values to OEM SSD values
```

Do **not** treat existing Linux constants as machine-specific until they are replaced/validated against these extracted SSD files.

## Immediate next technical steps

1. Build a read-only parser for the extracted active OEM files.
2. Add a runtime endpoint like:

```text
GET /motion/oem/machine_config
```

3. Return provenance for every value:

```text
source_path
sha256
source_type=original_ssd_appdata
machine_calibrated=true
```

4. Do a source-vs-Linux diff for:

```text
axis limits
position table
G/gripper offsets
Z current/stall thresholds
thermal door constants
camera offset
process timings
inspection settings presence
```

5. Only after read-only binding/diff should motion/homing logic consume these values.
