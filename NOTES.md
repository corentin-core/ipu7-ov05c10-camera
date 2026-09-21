# Notes

Background for [the README](README.md): what was missing, why the design looks like this, and what is still broken.

## What was missing

The sensor is an OV05C10 (ACPI `OVTI05C1`) behind the IPU7. Nothing reaches it directly. A Synaptics SVP7500 (`06cb:0701`), an *Intel Computer Vision Sensing* device declared as `INTC10DE`, owns the CSI-2 link and must hand it over.

The ACPI tables say so plainly: the sensor at `\_SB.LNK1` declares a dependency on `\_SB.PC00.CVSS`. Until something drives the CVS, that dependency is never cleared and Linux does not even enumerate the sensor.

Three kernel pieces are missing from distributions:

| Driver | Source | Patched |
|---|---|---|
| `ov05c10` | [intel/ipu6-drivers](https://github.com/intel/ipu6-drivers), not upstream | no |
| `intel_cvs` | mainline, landed after 7.0 | twice |
| `ipu-bridge` | mainline, replaces the distribution's | once |

All camera I2C traffic goes through the Synaptics USB bridge, which is where this hardware's fragility lives.

## Why the supervisor works this way

Three constraints shaped it.

**`v4l2sink` is unusable.** Its allocator cannot queue v4l2loopback's buffers (`buffer 0 was not queued`), and its `rw` mode stops the node from advertising capture. Frames are written raw instead. This is also why `v4l2-relayd` does not work here: its output pipeline goes through `v4l2sink`.

**`exclusive_caps=1` is needed for direct V4L2 consumers.** Chrome skips any device that also advertises the output capability. Firefox in PipeWire mode does not care, since PipeWire lists the node either way. With that setting the capture capability only appears after the first write, hence the black priming frame at startup.

**v4l2loopback accepts a single writer.** The supervisor holds the descriptor, so GStreamer cannot write alongside it: it writes to its standard output and the supervisor relays.

## Known defects

### The 30-second power-up

Starting the sensor takes 31 seconds, measured repeatedly to the second. The exception is the first start after a full power off, which takes 3.

This is a driver defect, not slow hardware. An I2C trace of a cold start shows **28 seconds of complete silence on the bus**, beginning 0.2 s in. Nothing is being programmed. The mode table is only 149 registers and a transfer costs 6.4 ms, so the whole thing should take a second.

`intel_cvs` dispatches each firmware command with its own 5-second timeout. Five or six of them expiring in sequence accounts for the 28 seconds: the handshake does not complete on this firmware, the driver exhausts every timeout, then proceeds anyway and the sensor streams.

Nothing appears in the log because `cvs_schedule_and_wait()` returns `-ETIMEDOUT` without logging, which is a defect of its own.

Two things worth reporting to the `intel_cvs` maintainers: the silent timeouts, and a handshake that fails on an SVP7500 while succeeding once after a power cycle, which suggests firmware state that is not reset between cycles.

### The USB bridge does not survive suspend

After resume every I2C transfer fails with `-110` and the sensor is unreachable. Nothing short of a full power off brings it back: not a reboot, not a USB re-enumeration, not a module reload.

```
usbio-bridge 3-3:1.0: Bulk out failed: -110
ov05c10 i2c-OVTI05C1:00: failed to read VTS
```

A hard teardown of the stream used to cause the same lockup. The supervisor now stops the pipeline with `SIGINT`, which `gst-launch` tears down cleanly where `SIGTERM` did not, and never kills it outright. The unit sets `KillMode=mixed` so systemd does not signal the pipeline behind the supervisor's back. A camera left running beats a dead bridge.

A failed teardown shows up as `stream stop time out` in `dmesg`.

### Transient errors that mean nothing

Two `Bulk` errors show up at every normal power-up, with a `failed to read VTS` and a `Connection timed out` on the sensor format. The camera still works, so `-110` alone is not a signal. What matters is whether `Debayer processed` lines follow.

### Firefox crashes on the V4L2 path

Observed in 155 and 156, snap and deb alike, with no upstream bug filed. Hence the PipeWire setting in the README.

## Colours

The shipped tuning profile enables the colour correction matrix that libcamera's uncalibrated profile leaves out. Mean channel spread went from 18 to 27 out of 255, on one frame of one scene.

The matrix is not measured against a colour target. It is a reasoned boost whose rows sum to 1, so it does not move the white balance, which measured neutral. Upstream leaves the CCM out because it costs CPU, and this is a software ISP: expect a few milliseconds more per frame.
