# Built-in camera on Intel IPU7 laptops with an OV05C10 sensor

Makes the built-in webcam work under Linux on laptops pairing an Intel Lunar Lake IPU7 with an OmniVision OV05C10 sensor behind a Synaptics SVP7500 chip.

It appears as `IPU7-Camera`, a plain V4L2 device on `/dev/video42`. The sensor only runs while an application is using it, and the privacy LED works.

Tested on one machine: a Dell Pro 16 Plus (PB16250), Ubuntu 24.04, HWE kernel 7.0.

## Does this apply to you?

```bash
lspci -nn | grep -i '8086:645d'               # Intel IPU7
lsusb | grep 06cb:0701                        # Synaptics CVS bridge
ls /sys/bus/acpi/devices/ | grep OVTI05C1     # OV05C10 sensor
```

Three hits, and you are in the right place.

## Install

**1. The packages.** From the [latest release](../../releases/latest), or built locally:

```bash
sudo apt install -y devscripts debhelper dh-dkms
git clone https://github.com/corentin-core/ipu7-ov05c10-camera.git
cd ipu7-ov05c10-camera && dpkg-buildpackage -us -uc -b
sudo apt install ../ipu7-ov05c10-camera-dkms_*.deb ../ipu7-camera-ondemand_*.deb
```

**2. v4l2loopback.** Ubuntu's 0.12.7 does not build on kernel 7.0, so it is not a dependency:

```bash
git clone --depth 1 --branch v0.15.4 https://github.com/v4l2loopback/v4l2loopback.git
sudo cp -r v4l2loopback /usr/src/v4l2loopback-0.15.4
sudo dkms install v4l2loopback/0.15.4
```

**3. libcamera**, 0.6 or later, for the software ISP:

| Release | Action |
| --- | --- |
| 26.04 and later | nothing, apt pulls it |
| 25.10 and earlier | [build it](#building-libcamera) |

**4. Secure Boot**, if enabled. Enrol the signing key, or the modules are refused:

```bash
sudo mokutil --import /var/lib/shim-signed/mok/MOK.der
```

It asks for a one-time password. On the next boot, a blue *MokManager* screen appears: **Enroll MOK**, **Continue**, then that password. Skip that screen and nothing will work.

**5. Firefox**, if you use it. Set `media.webrtc.camera.allow-pipewire` to `true` in `about:config`: its V4L2 path crashes on this device. Chrome needs nothing.

**6. Reboot.**

## Daily use

Nothing to do. Pick `IPU7-Camera` in any application.

One caveat: powering the sensor up takes about 30 seconds, except on the first use after a full power off. You get a black image until then, not a freeze. See [known defects](NOTES.md#the-30-second-power-up).

Two ways around it. Warm the camera up before a call:

```bash
gst-launch-1.0 -q v4l2src device=/dev/video42 num-buffers=1 ! fakesink &
```

Or keep it warm longer, at the cost of about 40% of one core while idle. Set `IPU7_STOP_DELAY`, in seconds, in `/etc/default/ipu7-camera-ondemand`. Default is 300.

## Troubleshooting

```bash
journalctl -u ipu7-camera-ondemand -n 20        # what the supervisor is doing
v4l2-ctl -d /dev/video42 --info                 # must advertise Video Capture
sudo dmesg | grep -iE "usbio-bridge|ov05c10"    # bridge health
```

**Camera missing from an application's list.** Check `wpctl status` for a PipeWire source node, and `systemctl --user restart wireplumber` if there is none.

**No image, `-110` errors in `dmesg`, and no `Debayer processed` line.** The USB bridge is wedged, which happens after every suspend. Full power off, not a reboot.

**Washed-out colours.** The journal should say `Using tuning file`. If it warns `falling back to`, the tuning profile was not found.

## Building libcamera

Needed on 25.10 and earlier. Ubuntu ships 0.2 to 0.5; IPU7 support in the *simple* pipeline landed in 0.6.

```bash
sudo apt install -y git build-essential meson ninja-build pkg-config python3-yaml \
  python3-ply python3-jinja2 libyaml-dev libudev-dev libevent-dev libdrm-dev \
  libjpeg-dev libgnutls28-dev openssl libtiff-dev libexif-dev \
  libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev

# from the root of this repository
git clone --depth 1 --branch v0.7.1 https://git.libcamera.org/libcamera/libcamera.git
cd libcamera
git apply ../libcamera-patches/0001-ov05c10-sensor-helper.patch
meson setup build --buildtype=release --prefix=/usr/local \
  -Dpipelines=simple -Dipas=simple -Dcam=enabled -Dgstreamer=enabled \
  -Dv4l2=true -Dqcam=disabled -Dtest=false -Ddocumentation=disabled
ninja -C build -j4 && sudo ninja -C build install && sudo ldconfig

sudo sed -i 's|^#GST_PLUGIN_PATH|GST_PLUGIN_PATH|; s|^#LIBCAMERA_IPA|LIBCAMERA_IPA|' \
  /etc/default/ipu7-camera-ondemand
sudo systemctl restart ipu7-camera-ondemand
```

Do not install the distribution's `gstreamer1.0-libcamera` on these releases: it is built against libcamera 0.2 and clashes with the one you just built.

The patch teaches libcamera this sensor's gain model. Without it the picture comes out nearly black. It is not upstream yet.

## More

[NOTES.md](NOTES.md) covers what was missing and why, the design constraints behind the supervisor, and the known defects. [src/PROVENANCE.md](src/PROVENANCE.md) documents each kernel patch, and is where to look first when a kernel update breaks the camera.

## Licence

Kernel drivers: GPL-2.0, copyright Intel. Supervisor, packaging and configuration: GPL-2.0-or-later.
