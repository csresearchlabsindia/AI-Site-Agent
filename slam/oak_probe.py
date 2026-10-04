import depthai as dai
print("depthai", dai.__version__)
with dai.Device() as d:
    gid = getattr(d, "getDeviceId", None) or d.getMxId
    print("id", gid())
    print("usb", d.getUsbSpeed().name)
    for c in d.getConnectedCameraFeatures():
        print("cam", c.socket.name, c.sensorName, c.width, "x", c.height)
    try: print("imu", d.getConnectedIMU())
    except Exception as e: print("imu ?", e)
    try: print("imu fw", d.getIMUFirmwareVersion())
    except Exception as e: print("imu fw ?", e)
    try:
        cal = d.readCalibration()
        print("board", cal.getEepromData().boardName, cal.getEepromData().productName)
        print("baseline_cm", round(cal.getBaselineDistance(), 2))
    except Exception as e: print("calib ?", e)
    try:
        m = d.getDdrMemoryUsage(); print("ddr_MB", round(m.used/1e6), "/", round(m.total/1e6))
        print("chip_C", round(d.getChipTemperature().average, 1))
    except Exception as e: print("mem/temp ?", e)
