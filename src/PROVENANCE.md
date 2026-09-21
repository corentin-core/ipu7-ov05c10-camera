# Where the three drivers come from

Taken on 18 September 2026. Two carry local patches, flagged below: that is where to look first when a kernel update breaks the camera.

## ov05c10

`drivers/media/i2c/ov05c10.c` from [intel/ipu6-drivers](https://github.com/intel/ipu6-drivers), commit `71bddb5`. **Unmodified.**

Not in the mainline kernel, and absent from Ubuntu's `linux-modules-ipu7-*` packages, which only carry `ov02c10`. Watch for it landing in mainline `drivers/media/i2c/`, which would make this part redundant.

## intel_cvs

`drivers/media/i2c/cvs/` from the mainline kernel, master branch, landed after 7.0. Driver for the *Intel Computer Vision Sensing* device, ACPI `INTC10DE`, here a Synaptics SVP7500.

**Two local patches.**

`v4l2.c` calls `v4l2_device_register_subdev_nodes()` at the end of `cvs_csi_notify_bound()`. Without it the sensor never gets a `/dev/v4l-subdev` node and libcamera cannot drive it. The IPU ISYS only creates nodes from its own notifier callbacks; the sensor binds to the CVS instead, and the ISYS notifier never completes on this machine.

A first attempt put the call in a `.complete` callback on the CVS notifier. That cannot work: `v4l2-async` only fires `.complete` on the root notifier, never on a sub-notifier.

`core.c` drops `ICVS_HOST_PRIV_CTRL` from the quirk table for `0x06CB:0x0701`. That flag tells the firmware the host owns privacy LED gating, which this driver then never exercises, so the LED stayed dark with the camera running. Without it the chip drives the LED itself, as under Windows. **Verified 21 September 2026: the LED lights up.**

## ipu-bridge

`drivers/media/pci/intel/ipu-bridge.c` from mainline master. **Replaces the module shipped by the distribution**, which only knows `INTC1059`, the previous IVSC generation, and therefore never builds the port graph `intel_cvs` expects.

**One patch**, taken from `intel/ipu6-drivers`, `patch/v7.0/0004`:

```c
IPU_SENSOR_CONFIG("OVTI05C1", 2, 480000000, 900000000)
```

instead of a single frequency. The sensor driver refuses to probe unless both are declared.

Exported symbols were checked identical to the distribution's module, so `intel_ipu7` links against it unchanged.
