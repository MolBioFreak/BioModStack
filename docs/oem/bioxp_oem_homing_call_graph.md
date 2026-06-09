# BioXP OEM Homing Call Graph (Phase 1 Source Extraction)

Generated from local decompiled OEM source. No live robot calls were made.

## Sources

- ClassControlInterface: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ClassControlInterface.cs`
- ControlLib: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ControlLib.cs`
- BioXPMainWindow: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_genbotapp/GenBotApp/BioXPMainWindow.cs`
- ClassBioXPSettings: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_bioxpcommon/BioXPCommonLib/ClassBioXPSettings.cs`

## Extracted method anchors

- `BioXPMainWindow.initializeEnvironment`: `BioXPMainWindow.cs:973-1027` calls=[]
- `BioXPMainWindow.initializeSystem`: `BioXPMainWindow.cs:1046-1342` calls=['initializeMotion', 'doorOpen', 'parkGantry']
- `BioXPMainWindow.motion_thread_process`: `BioXPMainWindow.cs:2030-2101` calls=[]
- `ControlLib.initialCheck`: `ControlLib.cs:8728-8760` calls=[]
- `ControlLib.rehome`: `ControlLib.cs:8784-8795` calls=['initializeMotors', 'resumeTemperature', 'doorOpen']
- `ControlLib.initializeMotion`: `ControlLib.cs:8797-8856` calls=['initializeMotion', 'initializeMotors']
- `ControlLib.parkGantry`: `ControlLib.cs:7071-7122` calls=['HomeXY', 'parkGantry']
- `ClassControlInterface.initializeMotorsWithoutMotion`: `ClassControlInterface.cs:3181-3265` calls=['initializeMotorsWithoutMotion', 'setSpeedAcc', 'setStallGuardThreshold']
- `ClassControlInterface.initializeMotors`: `ClassControlInterface.cs:3348-3421` calls=['initializeMotors', 'axisSearchHome', 'doorSearchHome', 'setHome', 'setSpeed', 'moveSteps']
- `ClassControlInterface.HomeAxis`: `ClassControlInterface.cs:4997-5052` calls=['axisSearchHome', 'doorSearchHome', 'HomeAxis', 'moveToAbs', 'setStallGuardThreshold']
- `ClassControlInterface.HomeXY`: `ClassControlInterface.cs:5054-5070` calls=['goHome', 'HomeXY', 'setSpeedAcc']
- `ClassControlInterface.MoveZHome`: `ClassControlInterface.cs:4623-4632` calls=['goHome', 'MoveZHome']
- `ClassControlInterface.homeGZ`: `ClassControlInterface.cs:4657-4687` calls=['goHome', 'HomeAxis', 'MoveZHome', 'homeGZ']
- `ClassControlInterface.btnHomeX_Click`: `ClassControlInterface.cs:2262-2285` calls=['goHome']
- `ClassControlInterface.btnHomeY_Click`: `ClassControlInterface.cs:2302-2325` calls=['goHome']
- `ClassControlInterface.btnHomeZ_Click`: `ClassControlInterface.cs:2350-2373` calls=['goHome']
- `ClassControlInterface.btnGripperHome_Click`: `ClassControlInterface.cs:2046-2075` calls=['goHome']
- `ClassControlInterface.btnDHome_Click`: `ClassControlInterface.cs:1224-1246` calls=['doorSearchHome']

## Required call-chain interpretation

- Windows/app path: `BioXPMainWindow.initializeEnvironment()` queues `initializeSystem` when initial check and door/latch conditions require initialization.
- Motion worker path must route queued `initializeSystem` to `BioXPMainWindow.initializeSystem()`.
- `initializeSystem()` calls `ControlLib.initializeMotion()` in the physical initialization path.
- `ControlLib.initializeMotion()` is app-level and must not be reduced to `initializeMotors()` only; it includes machine state / tip-pipette cleanup concerns.
- `ControlLib.rehome()` is distinct: it saves thermal-door state, calls physical initialization, restores door state, then resumes temperature.
- `ClassControlInterface.initializeMotors()` is the physical startup homing sequence and must remain distinct from manual button `goHome`, generic `HomeAxis`, `HomeXY`, `MoveZHome`, and `homeGZ`.
