# BioXP OEM Source Oracle — 2026-06-09

Phase 1 artifact. No robot calls, no USB, no motion.

Total records: 27
Missing source files: 0

## BioXPMainWindow.initializeEnvironment

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_genbotapp/GenBotApp/BioXPMainWindow.cs`
- Lines: `973-1027`
- Exists: `True`
- SHA256-16: `3d33c1e894046b6e`
- Note: app startup environment; queues/conditions initializeSystem

```csharp
	private void initializeEnvironment()
	{
		//IL_0135: Unknown result type (might be due to invalid IL or missing references)
		if (m_control.m_canControl.CAN_READY)
		{
			m_control.initialCheck();
			if (!m_control.MachineStatus.EnclosureDoorClosed && !m_machinestatus.LatchClosed)
			{
				showScreen("Warning", "_warning", "_msg63", null, null, WarningSituation.ENCLOSURE_OPEN.ToString());
			}
			else if (!m_control.MachineStatus.EnclosureDoorClosed)
			{
```

## BioXPMainWindow.initializeSystem

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_genbotapp/GenBotApp/BioXPMainWindow.cs`
- Lines: `1046-1342`
- Exists: `True`
- SHA256-16: `acaf79c48cea0a0a`
- Note: app-level initializeSystem orchestration

```csharp
	private void initializeSystem(bool skipInitializeMotion = false)
	{
		//IL_0050: Unknown result type (might be due to invalid IL or missing references)
		//IL_0055: Unknown result type (might be due to invalid IL or missing references)
		//IL_0056: Unknown result type (might be due to invalid IL or missing references)
		//IL_0818: Unknown result type (might be due to invalid IL or missing references)
		//IL_005c: Unknown result type (might be due to invalid IL or missing references)
		//IL_005e: Unknown result type (might be due to invalid IL or missing references)
		//IL_0060: Invalid comparison between Unknown and I4
		//IL_07bb: Unknown result type (might be due to invalid IL or missing references)
		//IL_07c1: Invalid comparison between Unknown and I4
		//IL_0251: Unknown result type (might be due to invalid IL or missing references)
```

## BioXPMainWindow.motion_thread_process

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_genbotapp/GenBotApp/BioXPMainWindow.cs`
- Lines: `2030-2101`
- Exists: `True`
- SHA256-16: `c79bc5d3a6b0d278`
- Note: serialized motion command worker

```csharp
	private void motion_thread_process()
	{
		//IL_00c8: Unknown result type (might be due to invalid IL or missing references)
		//IL_00ce: Invalid comparison between Unknown and I4
		//IL_00d6: Unknown result type (might be due to invalid IL or missing references)
		//IL_00dc: Invalid comparison between Unknown and I4
		//IL_0110: Unknown result type (might be due to invalid IL or missing references)
		//IL_0119: Unknown result type (might be due to invalid IL or missing references)
		//IL_0107: Unknown result type (might be due to invalid IL or missing references)
		while (true)
		{
			motionCommands motionCommands = m_commandQueue.Take();
```

## ControlLib.initialCheck

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ControlLib.cs`
- Lines: `8728-8760`
- Exists: `True`
- SHA256-16: `2f6f44502d2e53dd`
- Note: initial hardware/application checks

```csharp
	public bool initialCheck()
	{
		bool result = false;
		int num = 0;
		while (!m_canControl.CAN_READY)
		{
			Thread.Sleep(200);
			if (num > 10)
			{
				return result;
			}
			num++;
```

## ControlLib.rehome

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ControlLib.cs`
- Lines: `8784-8795`
- Exists: `True`
- SHA256-16: `367364cb1cc4ad30`
- Note: save thermal door state, initializeMotors, restore/resume

```csharp
	public void rehome()
	{
		bool thermalDoorOpen = m_machineStatus.ThermalDoorOpen;
		m_ControlInterface.initializeMotors();
		Thread.Sleep(40);
		if (thermalDoorOpen)
		{
			m_machineStatus.ThermalDoorOpen = false;
		}
		doorOpen(thermalDoorOpen);
		m_ControlInterface.resumeTemperature();
	}
```

## ControlLib.initializeMotion

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ControlLib.cs`
- Lines: `8797-8856`
- Exists: `True`
- SHA256-16: `c5448efb5a5bb7a1`
- Note: initializeMotors plus pipette/tip cleanup

```csharp
	public void initializeMotion()
	{
		m_stopScripts = true;
		forceabort = false;
		try
		{
			m_ControlInterface.initializeMotors();
			m_machineStatus.ThermalDoorOpen = false;
			m_PipetteControl.queryTipStatus(-1);
			Thread.Sleep(500);
			if (m_PipetteControl.TipExist)
			{
```

## ControlLib.parkGantry

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ControlLib.cs`
- Lines: `7071-7122`
- Exists: `True`
- SHA256-16: `c8be2807bdfd2373`
- Note: post-init/job gantry parking

```csharp
	public void parkGantry(bool rehome = false)
	{
		//IL_0006: Unknown result type (might be due to invalid IL or missing references)
		//IL_000d: Invalid comparison between Unknown and I4
		//IL_018a: Unknown result type (might be due to invalid IL or missing references)
		//IL_0195: Unknown result type (might be due to invalid IL or missing references)
		//IL_00cb: Unknown result type (might be due to invalid IL or missing references)
		//IL_00d6: Unknown result type (might be due to invalid IL or missing references)
		if ((int)m_machineStatus.CurrentLocation == 28)
		{
			return;
		}
```

## ClassControlInterface.initializeMotorsWithoutMotion

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ClassControlInterface.cs`
- Lines: `3181-3265`
- Exists: `True`
- SHA256-16: `a39c9846a854567d`
- Note: no-homing hardware setup; still mutates currents/switch masks/chiller/LED

```csharp
	public void initializeMotorsWithoutMotion()
	{
		waitForBoard();
		turnOffHeater();
		setChillerPWM();
		Thread.Sleep(1);
		if (m_Boards[m_AxisIODesignater["MotorX"].board] != null)
		{
			m_Boards[m_AxisIODesignater["MotorX"].board].setSpeedAcc(m_AxisIODesignater["MotorX"].axis, 1700, 350);
			Thread.Sleep(2);
			m_Boards[m_AxisIODesignater["MotorX"].board].setMaxCurrent(m_AxisIODesignater["MotorX"].axis, 31);
			Thread.Sleep(2);
```

## ClassControlInterface.initializeMotors

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ClassControlInterface.cs`
- Lines: `3348-3421`
- Exists: `True`
- SHA256-16: `c99f0220a0c07572`
- Note: physical OEM startup homing order

```csharp
	public void initializeMotors()
	{
		if (m_Boards[m_AxisIODesignater["MotorZ"].board] != null)
		{
			m_Boards[m_AxisIODesignater["MotorZ"].board].axisSearchHome(m_AxisIODesignater["MotorZ"].axis, 1791);
		}
		setGripperCurrent(31);
		m_Boards[m_AxisIODesignater["MotorGrip"].board].moveSteps(m_AxisIODesignater["MotorGrip"].axis, 10000, true);
		if (m_Boards[m_AxisIODesignater["MotorGrip"].board] != null)
		{
			if (m_settingsWindow.GripperVersion == 0)
			{
```

## ClassControlInterface.HomeAxis

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ClassControlInterface.cs`
- Lines: `4997-5052`
- Exists: `True`
- SHA256-16: `acd5ff0e05e13361`
- Note: generic axis-specific HomeAxis

```csharp
	internal int HomeAxis(string axis)
	{
		int result = 0;
		switch (axis)
		{
		case "x":
		case "X":
			if (m_Boards[m_AxisIODesignater["MotorX"].board] != null)
			{
				result = m_Boards[m_AxisIODesignater["MotorX"].board].axisSearchHome(m_AxisIODesignater["MotorX"].axis, 250);
			}
			break;
```

## ClassControlInterface.HomeXY

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ClassControlInterface.cs`
- Lines: `5054-5070`
- Exists: `True`
- SHA256-16: `875310cf163035ad`
- Note: parallel X/Y home, speed restore

```csharp
	internal int[] HomeXY()
	{
		if (m_Boards[m_AxisIODesignater["MotorX"].board] != null && m_Boards[m_AxisIODesignater["MotorY"].board] != null)
		{
			m_Boards[m_AxisIODesignater["MotorX"].board].setSpeedAcc(m_AxisIODesignater["MotorX"].axis, 200, 200);
			m_Boards[m_AxisIODesignater["MotorY"].board].setSpeedAcc(m_AxisIODesignater["MotorY"].axis, 200, 200);
			int x = 0;
			int y = 0;
			Task task = Task.Run(() => x = m_Boards[m_AxisIODesignater["MotorX"].board].goHome(false, m_AxisIODesignater["MotorX"].axis, 200, true));
			Task task2 = Task.Run(() => y = m_Boards[m_AxisIODesignater["MotorY"].board].goHome(false, m_AxisIODesignater["MotorY"].axis, 200, true));
			Task.WaitAll(task, task2);
			m_Boards[m_AxisIODesignater["MotorX"].board].setSpeedAcc(m_AxisIODesignater["MotorX"].axis, 1700, 350);
```

## ClassControlInterface.MoveZHome

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ClassControlInterface.cs`
- Lines: `4623-4632`
- Exists: `True`
- SHA256-16: `7f7e6eb5280760f1`
- Note: distinct Z home routine

```csharp
	public int MoveZHome(bool rehome = true)
	{
		if (m_Boards[m_AxisIODesignater["MotorZ"].board] != null)
		{
			m_Boards[m_AxisIODesignater["MotorZ"].board].setMaxCurrent(m_AxisIODesignater["MotorZ"].axis, 31);
			m_zCurrent = m_Boards[m_AxisIODesignater["MotorZ"].board].readMaxCurrent(m_AxisIODesignater["MotorZ"].axis);
			return m_Boards[m_AxisIODesignater["MotorZ"].board].goHome(rehome, m_AxisIODesignater["MotorZ"].axis, 1791, true);
		}
		return 0;
	}
```

## ClassControlInterface.homeGZ

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ClassControlInterface.cs`
- Lines: `4657-4687`
- Exists: `True`
- SHA256-16: `19887d5f0fd94284`
- Note: composite G/Z caught-plate recovery routine

```csharp
	public void homeGZ(int delay)
	{
		if (m_Boards[m_AxisIODesignater["MotorZ"].board] == null)
		{
			HomeAxis("G");
			return;
		}
		if (m_Boards[m_AxisIODesignater["MotorGrip"].board] == null)
		{
			MoveZHome();
			return;
		}
```

## ClassControlInterface.btnGripperHome_Click

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ClassControlInterface.cs`
- Lines: `2046-2075`
- Exists: `True`
- SHA256-16: `8f8d4fa063f55cce`
- Note: manual gripper home button

```csharp
	private void btnGripperHome_Click(object sender, RoutedEventArgs e)
	{
		//IL_00f0: Unknown result type (might be due to invalid IL or missing references)
		if (m_Boards[m_AxisIODesignater["MotorGrip"].board] == null)
		{
			return;
		}
		try
		{
			setGripperCurrent(31);
			PageMotionControl motionControl = m_controlLib.m_diagnosticPanel.m_MotionControl;
			if (m_settingsWindow.GripperVersion == 0)
```

## ClassControlInterface.btnHomeX_Click

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ClassControlInterface.cs`
- Lines: `2262-2285`
- Exists: `True`
- SHA256-16: `e755ebacaf661706`
- Note: manual X home button

```csharp
	private void btnHomeX_Click(object sender, RoutedEventArgs e)
	{
		//IL_00c6: Unknown result type (might be due to invalid IL or missing references)
		e.Handled = true;
		if (m_Boards[m_AxisIODesignater["MotorX"].board] == null)
		{
			return;
		}
		try
		{
			PageMotionControl mc = m_controlLib.m_diagnosticPanel.m_MotionControl;
			m_Boards[m_AxisIODesignater["MotorX"].board].goHome(true, m_AxisIODesignater["MotorX"].axis, 500, true);
```

## ClassControlInterface.btnHomeY_Click

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ClassControlInterface.cs`
- Lines: `2302-2325`
- Exists: `True`
- SHA256-16: `cf264fa305eac1b3`
- Note: manual Y home button

```csharp
	private void btnHomeY_Click(object sender, RoutedEventArgs e)
	{
		//IL_00c6: Unknown result type (might be due to invalid IL or missing references)
		e.Handled = true;
		if (m_Boards[m_AxisIODesignater["MotorY"].board] == null)
		{
			return;
		}
		try
		{
			PageMotionControl mc = m_controlLib.m_diagnosticPanel.m_MotionControl;
			m_Boards[m_AxisIODesignater["MotorY"].board].goHome(true, m_AxisIODesignater["MotorY"].axis, 500, true);
```

## ClassControlInterface.btnHomeZ_Click

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ClassControlInterface.cs`
- Lines: `2350-2373`
- Exists: `True`
- SHA256-16: `7099e310d91fc713`
- Note: manual Z home button

```csharp
	private void btnHomeZ_Click(object sender, RoutedEventArgs e)
	{
		//IL_00c8: Unknown result type (might be due to invalid IL or missing references)
		e.Handled = true;
		if (m_Boards[m_AxisIODesignater["MotorZ"].board] == null)
		{
			return;
		}
		try
		{
			PageMotionControl mc = m_controlLib.m_diagnosticPanel.m_MotionControl;
			int num = m_Boards[m_AxisIODesignater["MotorZ"].board].goHome(true, m_AxisIODesignater["MotorZ"].axis, 1791, true);
```

## ClassPipetteCollection.initiateGroup

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ClassPipetteCollection.cs`
- Lines: `677-693`
- Exists: `True`
- SHA256-16: `5ce937bd6cad249f`
- Note: pipette group init

```csharp
	public void initiateGroup()
	{
		for (int i = 0; i < 4; i++)
		{
			if (m_pipette[i].CommandCompleted)
			{
				m_pipette[i].CommandCompleted = false;
				((AutoResetEvent)m_waitforcompletion[i]).Reset();
				m_pipette[i].initiate(false);
			}
		}
		waitforcompletion("Reinitialize pipette", 10000);
```

## ClassPipetteCollection.checkedPipetteStatus

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ClassPipetteCollection.cs`
- Lines: `726-748`
- Exists: `True`
- SHA256-16: `f7774aa9984a4d43`
- Note: pipette status check

```csharp
	public bool checkedPipetteStatus()
	{
		if (ControlLib.forceabort)
		{
			throw new Exception("Stopped by user or force abort");
		}
		bool result = true;
		for (int i = 0; i < 4; i++)
		{
			m_pipette[i].QueryStatus();
			Thread.Sleep(30);
		}
```

## ClassPipetteCollection.ejectAllTips

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src/BioXPControlLib/ClassPipetteCollection.cs`
- Lines: `1176-1235`
- Exists: `True`
- SHA256-16: `a10573bc36939b3b`
- Note: eject tips semantics

```csharp
	public void ejectAllTips(bool checkMissingTip = true, bool wait = true)
	{
		//IL_0110: Unknown result type (might be due to invalid IL or missing references)
		bool[] array = new bool[4];
		if (m_machinestatus.TipLocation != -1)
		{
			array[m_machinestatus.TipLocation] = true;
		}
		else
		{
			array = new bool[4] { true, true, true, true };
		}
```

## ClassPipette.QueryTipStatus

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_can/BioXPControlLib/ClassPipette.cs`
- Lines: `571-589`
- Exists: `True`
- SHA256-16: `b36cb1e6ef0dfb84`
- Note: ?31 tip status

```csharp
	public int QueryTipStatus()
	{
		int num = 0;
		byte[] cmd = new byte[3] { 63, 51, 49 };
		m_LastCMD = PipetteCommands.Query_Tip_Status;
		byte[] array = SendCommand(m_CANReportid, cmd, checkInitialized: true, "QueryTipStatus");
		if (array != null)
		{
			if (array[2] == 49)
			{
				m_tipLoaded = true;
			}
```

## ClassPipette.QueryPressure

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_can/BioXPControlLib/ClassPipette.cs`
- Lines: `622-628`
- Exists: `True`
- SHA256-16: `128c4301c6b8a417`
- Note: ?57 pressure

```csharp
	public double QueryPressure()
	{
		byte[] cmd = new byte[3] { 63, 53, 55 };
		m_LastCMD = PipetteCommands.Query_info;
		byte[] bytes = SendCommand(m_CANReportid, cmd, checkInitialized: true, "QueryPressure");
		return Convert.ToDouble(Encoding.Default.GetString(bytes).Substring(2));
	}
```

## ClassFrameGrabber.ScanBarcode

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_vision/CVisionLib/ClassFrameGrabber.cs`
- Lines: `125-170`
- Exists: `True`
- SHA256-16: `3d8f56b7b5af71ce`
- Note: barcode scan anchor

```csharp
	public unsafe void ScanBarcode()
	{
		uint num = 0u;
		Unsafe.SkipInit(out ImageScanner imageScanner);
		*(int*)(&imageScanner) = (int)global::_003CModule_003E.zbar_image_scanner_create();
		try
		{
			global::_003CModule_003E.zbar_image_scanner_set_config((zbar_image_scanner_s*)(int)(*(uint*)(&imageScanner)), (zbar_symbol_type_e)0, (zbar_config_e)0, 2);
			if (m_barcodestring != null)
			{
				m_barcodestring = null;
			}
```

## ClassFrameGrabber.CamCalibration

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_vision/CVisionLib/ClassFrameGrabber.cs`
- Lines: `4292-4350`
- Exists: `True`
- SHA256-16: `331e82e0cc1bcec6`
- Note: camera calibration anchor

```csharp
	public unsafe int[] CamCalibration(int thre, string fname)
	{
		Unsafe.SkipInit(out basic_string_003Cchar_002Cstd_003A_003Achar_traits_003Cchar_003E_002Cstd_003A_003Aallocator_003Cchar_003E_0020_003E obj);
		global::_003CModule_003E.std_002Ebasic_string_003Cchar_002Cstd_003A_003Achar_traits_003Cchar_003E_002Cstd_003A_003Aallocator_003Cchar_003E_0020_003E_002E_007Bctor_007D(&obj, (sbyte*)Marshal.StringToHGlobalAnsi(fname).ToPointer());
		int[] array;
		try
		{
			Unsafe.SkipInit(out Mat mat);
			global::_003CModule_003E.cv_002EMat_002E_007Bctor_007D(&mat);
			try
			{
				Unsafe.SkipInit(out Mat mat2);
```

## ClassFrameGrabber.locateCover

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_vision/CVisionLib/ClassFrameGrabber.cs`
- Lines: `4578-4635`
- Exists: `True`
- SHA256-16: `906132c5af6ab947`
- Note: cover location anchor

```csharp
	public unsafe bool locateCover(int corner, int xPos, int yPos, int cx, int cy, string fname, double* cDif, short* xDif, short* yDif, short* othereLines1, short* highFound)
	{
		uint num = 0u;
		bool result = true;
		Unsafe.SkipInit(out basic_string_003Cchar_002Cstd_003A_003Achar_traits_003Cchar_003E_002Cstd_003A_003Aallocator_003Cchar_003E_0020_003E obj);
		global::_003CModule_003E.std_002Ebasic_string_003Cchar_002Cstd_003A_003Achar_traits_003Cchar_003E_002Cstd_003A_003Aallocator_003Cchar_003E_0020_003E_002E_007Bctor_007D(&obj, (sbyte*)Marshal.StringToHGlobalAnsi(fname).ToPointer());
		try
		{
			Unsafe.SkipInit(out Mat mat);
			global::_003CModule_003E.cv_002EMat_002E_007Bctor_007D(&mat);
			try
			{
```

## ClassFrameGrabber.checkPoolPlate

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_vision/CVisionLib/ClassFrameGrabber.cs`
- Lines: `6485-6545`
- Exists: `True`
- SHA256-16: `c28d00212d89e269`
- Note: pool plate check anchor

```csharp
	public unsafe int checkPoolPlate(string fname, string templateImage)
	{
		uint num = 0u;
		Unsafe.SkipInit(out basic_string_003Cchar_002Cstd_003A_003Achar_traits_003Cchar_003E_002Cstd_003A_003Aallocator_003Cchar_003E_0020_003E obj);
		global::_003CModule_003E.std_002Ebasic_string_003Cchar_002Cstd_003A_003Achar_traits_003Cchar_003E_002Cstd_003A_003Aallocator_003Cchar_003E_0020_003E_002E_007Bctor_007D(&obj, (sbyte*)Marshal.StringToHGlobalAnsi(fname).ToPointer());
		int result;
		try
		{
			Unsafe.SkipInit(out Mat mat);
			global::_003CModule_003E.cv_002EMat_002E_007Bctor_007D(&mat);
			try
			{
```

## ClassFrameGrabber.checkBioSecurityCover

- Path: `/home/dalab/Desktop/ROBOT/BioXP 3200 Development Work/BioXP_SSD_Backup/decompiled_src_vision/CVisionLib/ClassFrameGrabber.cs`
- Lines: `11918-11980`
- Exists: `True`
- SHA256-16: `1fb95334ec4e2b6b`
- Note: biosecurity cover check anchor

```csharp
	public unsafe bool checkBioSecurityCover(string fname)
	{
		uint num = 0u;
		bool result = false;
		Unsafe.SkipInit(out basic_string_003Cchar_002Cstd_003A_003Achar_traits_003Cchar_003E_002Cstd_003A_003Aallocator_003Cchar_003E_0020_003E obj);
		global::_003CModule_003E.std_002Ebasic_string_003Cchar_002Cstd_003A_003Achar_traits_003Cchar_003E_002Cstd_003A_003Aallocator_003Cchar_003E_0020_003E_002E_007Bctor_007D(&obj, (sbyte*)Marshal.StringToHGlobalAnsi(fname).ToPointer());
		try
		{
			Unsafe.SkipInit(out Mat mat);
			global::_003CModule_003E.cv_002EMat_002E_007Bctor_007D(&mat);
			try
			{
```
