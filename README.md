# Built-in camera on Intel IPU7 laptops with an OV05C10 sensor

Makes the built-in webcam work on Linux for laptops pairing an **Intel Lunar Lake IPU7** with an **OmniVision OV05C10** sensor behind a **Synaptics SVP7500** arbitration chip.

Verified on a **Dell Pro 16 Plus (PB16250)**, Ubuntu 24.04, HWE kernel 7.0. That is the only machine this has run on. Other laptops with the same three chips should work, but nobody has checked.

The camera appears as a normal V4L2 device that browsers and conferencing apps accept. The sensor powers up only while an application is using it, and the privacy LED works.

## Does this apply to you?

```bash
lspci -nn | grep -i '8086:645d'               # an Intel IPU7?
lsusb | grep 06cb:0701                        # the Synaptics CVS bridge?
ls /sys/bus/acpi/devices/ | grep OVTI05C1     # an OV05C10 sensor?
```

Three hits means yes. If your sensor differs (`OVTI02C1`, `OVTI08x4`…) the kernel part will not help, but the rest of the design will.

## Install

Grab the two `.deb` files from the [latest release](../../releases/latest), or build them:

```bash
sudo apt install -y devscripts debhelper dh-dkms
git clone https://github.com/corentin-core/ipu7-ov05c10-camera.git
cd ipu7-ov05c10-camera
dpkg-buildpackage -us -uc -b
sudo apt install ../ipu7-ov05c10-camera-dkms_*.deb ../ipu7-camera-ondemand_*.deb
```

You also need a **v4l2loopback that builds against your kernel**. Ubuntu's 0.12.7 fails on 7.0 (`too few arguments to function 'v4l2_fh_add'`), so it is deliberately not a dependency:

```bash
git clone --depth 1 --branch v0.15.4 https://github.com/v4l2loopback/v4l2loopback.git
sudo cp -r v4l2loopback /usr/src/v4l2loopback-0.15.4
sudo dkms install v4l2loopback/0.15.4
```

Then, depending on your release:

| Release | libcamera | What to do |
|---|---|---|
| 26.04 and later | 0.7 in the archive | nothing, apt pulls `gstreamer1.0-libcamera` |
| 25.10 and earlier | 0.2 to 0.5, too old | build libcamera, see below |

IPU7 support in libcamera's *simple* pipeline, with its software ISP, landed in **0.6.0**.

With Secure Boot on, enrol the DKMS signing key, or the modules are refused at load:

```bash
sudo mokutil --import /var/lib/shim-signed/mok/MOK.der
```

It asks for a one-time password. On the next boot a blue *MokManager* screen appears: choose **Enroll MOK**, **Continue**, then type that password. Skipping that screen leaves you with unsigned modules and no clue why nothing works.

Finally, **in Firefox**, set `media.webrtc.camera.allow-pipewire` to `true` in `about:config`. Its V4L2 path crashed on this device in 155 and 156, snap and deb alike, observed here with no upstream bug filed. Chrome uses V4L2 directly and is fine.

## Building libcamera, for releases before 26.04

```bash
sudo apt install -y git build-essential meson ninja-build pkg-config python3-yaml python3-ply \
  python3-jinja2 libyaml-dev libudev-dev libevent-dev libdrm-dev libjpeg-dev \
  libgnutls28-dev openssl libtiff-dev libexif-dev \
  libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev

# from the root of this repository
git clone --depth 1 --branch v0.7.1 https://git.libcamera.org/libcamera/libcamera.git
cd libcamera
git apply ../libcamera-patches/0001-ov05c10-sensor-helper.patch
meson setup build --buildtype=release --prefix=/usr/local \
  -Dpipelines=simple -Dipas=simple -Dcam=enabled -Dgstreamer=enabled \
  -Dv4l2=true -Dqcam=disabled -Dtest=false -Ddocumentation=disabled
ninja -C build -j4 && sudo ninja -C build install && sudo ldconfig
```

Do **not** install the distribution's `gstreamer1.0-libcamera` on these releases: it is built against libcamera 0.2 and clashes with the one you just built. The packaging only pulls it from 0.6 onwards, so apt will leave it alone by itself.

Then point the service at both the plugin and the tuning profile, since a build under `/usr/local` searches its own prefix for each:

```bash
sudo sed -i 's|^#GST_PLUGIN_PATH|GST_PLUGIN_PATH|; s|^#LIBCAMERA_IPA|LIBCAMERA_IPA|' \
  /etc/default/ipu7-camera-ondemand
sudo systemctl restart ipu7-camera-ondemand
```

Check the journal says `Using tuning file`. If instead it warns `falling back to` `uncalibrated.yaml`, the profile was not found and the colours stay washed out.

The patch adds a `CameraSensorHelper` for the OV05C10. Without it libcamera reads a gain code of 30 as a gain of 30x, stops raising it, and the picture comes out nearly black. **It belongs upstream** and has not been submitted yet.

The colour profile `ov05c10.yaml`, shipped by the package, enables the colour correction matrix that the uncalibrated profile leaves out. Mean channel spread went from 18 to 27 out of 255, measured on one frame of one scene. The matrix is **not measured against a colour target**: it is a reasoned boost whose rows sum to 1, so it does not move the white balance, which measured neutral. Upstream leaves the CCM out for a reason it states plainly, that it costs significant CPU, and this is a software ISP: expect a few milliseconds per frame more.

## What was missing, and why

The sensor is an **OV05C10** (ACPI `OVTI05C1`) behind the **IPU7**. Nothing reaches it directly: a **Synaptics SVP7500** (`06cb:0701`), an *Intel Computer Vision Sensing* device declared as `INTC10DE`, owns the CSI-2 link and must hand it over. The ACPI tables say so plainly — the sensor at `\_SB.LNK1` declares a dependency on `\_SB.PC00.CVSS`. Until something drives the CVS, that dependency is never cleared and Linux does not even enumerate the sensor.

Three kernel pieces are missing from distributions:

| Driver | Where it comes from | Patched? |
|---|---|---|
| `ov05c10` | [intel/ipu6-drivers](https://github.com/intel/ipu6-drivers), not upstream | no |
| `intel_cvs` | mainline, landed after 7.0 | **yes**, twice |
| `ipu-bridge` | mainline, replaces the distribution's | **yes**, once |

All three patches are documented in [`src/PROVENANCE.md`](src/PROVENANCE.md), and none of them is upstream: that file is where to look first when a kernel update breaks the camera.

Also worth knowing: all camera I2C traffic goes through the Synaptics USB bridge. That is where this hardware's fragility lives.

## Hardware fragility

**The USB bridge does not survive suspend.** After resume every I2C transfer fails with `-110`, the sensor becomes unreachable, and nothing short of a **full power off** brings it back. Not a reboot, not a USB re-enumeration, not a module reload.

```
usbio-bridge 3-3:1.0: Bulk out failed: -110
ov05c10: failed to read VTS
```

A hard teardown of the stream used to cause the same lockup. That is handled: the supervisor stops the pipeline with `SIGINT`, which `gst-launch` tears down cleanly where a `SIGTERM` did not here, and never kills it outright. The unit sets `KillMode=mixed` so systemd does not signal the pipeline behind the supervisor's back. A camera left running beats a dead bridge. The signature of a failed teardown is `stream stop time out` in `dmesg`.

Two `Bulk` errors also show up at **every** normal power-up, along with a `failed to read VTS` and a `Connection timed out` on the sensor format. Those are transient and the camera still works, so `-110` alone is not the signal. The discriminator is whether frames arrive: if the journal shows `Debayer processed` lines, you are fine; if the errors repeat and no frames ever come, the bridge is wedged.

## Why this design and not a simpler one

Three non-obvious constraints shaped the supervisor.

**`v4l2sink` is unusable.** Its allocator cannot queue v4l2loopback's buffers (`buffer 0 was not queued`), and its `rw` mode stops the node from advertising capture. Frames are therefore written raw. This is also why **`v4l2-relayd` does not work here**: its output pipeline goes through `v4l2sink`.

**`exclusive_caps=1` is needed for direct V4L2 consumers.** Chrome skips any device that also advertises the output capability; Firefox in PipeWire mode does not care, since PipeWire lists the node either way. With that setting the capture capability only appears after the *first write*, hence the black priming frame at startup.

**v4l2loopback accepts a single writer.** The supervisor holds the descriptor, so GStreamer cannot write alongside it: it writes to its standard output and the supervisor relays.

## Troubleshooting

```bash
journalctl -u ipu7-camera-ondemand -n 20        # what the supervisor is doing
v4l2-ctl -d /dev/video42 --info                 # must advertise Video Capture
sudo dmesg | grep -iE "usbio-bridge|ov05c10"    # bridge health
```

Camera missing from an application's list: check that PipeWire has a source node with `wpctl status`, and `systemctl --user restart wireplumber` if not. WirePlumber does not re-examine a device whose capabilities change after it took inventory.

`-110` errors **and no `Debayer processed` line ever appearing**: the bridge is wedged, full power off.

## Licence

Kernel drivers: GPL-2.0, copyright Intel. Supervisor, packaging and configuration: GPL-2.0-or-later.
