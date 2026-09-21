Makes the built-in camera work on laptops pairing an Intel Lunar Lake IPU7 with an OmniVision OV05C10 sensor behind a Synaptics SVP7500 arbitration chip. Verified on a Dell Pro 16 Plus (PB16250), Ubuntu 24.04, HWE kernel 7.0.

Install both packages, then read the README: on releases before 26.04 you also need to build libcamera 0.6+, and a v4l2loopback that compiles against your kernel.

**ipu7-ov05c10-camera-dkms** carries three drivers no distribution ships: the `ov05c10` sensor, `intel_cvs` for the chip that owns the CSI-2 link, and a replacement `ipu-bridge` that knows about it. Three patches, none upstream, all documented in `src/PROVENANCE.md`.

**ipu7-camera-ondemand** exposes the camera as `/dev/video42`, powering the sensor up only while an application is using it.

Two things to know before you start. The Synaptics USB bridge does not survive suspend: after resume nothing short of a full power off brings the camera back. And powering the sensor up takes about 30 seconds, spent writing mode registers over a 100 kHz I2C bus, so the service keeps it warm for five minutes after the last client by default.
