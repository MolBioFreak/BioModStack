# BioXP OEM Motion Constants / Settings Source Notes

No live robot calls were made. Values are source-extracted or prior source-audit facts and must be verified against recovered machine config before physical parity claims.

## Board mapping

- head_board_index_0: CAN 4 (Y/Z/gripper)
- deck_board_index_1: CAN 5 (X/IO)
- thermal_board_index_2: CAN 6 (door/thermal)
- chiller_board_index_3: CAN 7

## Axis mapping

- x: board5 motor0
- y: board4 motor0
- z: board4 motor1
- g: board4 motor2
- door: board6 motor0

## Observed OEM motion values

- manual_x_home: `{'command': 'goHome', 'speed': 500}`
- manual_y_home: `{'command': 'goHome', 'speed': 500}`
- manual_z_home: `{'command': 'goHome', 'speed': 1791}`
- manual_g_home: `{'command': 'goHome', 'speed': '600 if GripperVersion==0 else 200'}`
- startup_x_y_axisSearchHome: `{'speed': 250}`
- startup_x_park: `{'setSpeed': 1700, 'moveX': 6000}`
- home_xy: `{'setSpeedAcc': '200/200 before; restore X 1700/350 Y 1800/400 after'}`

## Settings source anchors

- `ClassBioXPSettings.cs`: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_bioxpcommon/BioXPCommonLib/ClassBioXPSettings.cs`
- `CONFIG_FILE`: line 169, `config.xml`
- Default axis limits: X 0..80000 (line 250), Y 0..80000 (line 254), Z 0..160000 (line 258), G 0..15000 (line 262).
- Door defaults: `TCDoorStallGuardThreshold=6` (line 308), `TC_DOOR_VELOCITY=50` (line 310), `TC_DOOR_ACCELERATION=20` (line 312).
- Z defaults: `Z_MOTOR_MAX_CURRENT_DOWN=25` (line 316), `Z_MOTOR_MAX_CURRENT_UP=31` (line 318), `Z_MOTOR_STALL_GUARD_THRESHOLD=3` (line 320).
- Serial-dependent door branch: lines 777-785 set older serial door velocity/open differently.

## Config gap

Real field `config.xml` is still not recovered in this phase; treat OEM defaults as source defaults, not proven live calibration.
