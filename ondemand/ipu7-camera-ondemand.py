#!/usr/bin/env python3
"""Power the built-in camera up while an application has /dev/video42 open.

Subscribes to V4L2_EVENT_PRI_CLIENT_USAGE, v4l2loopback's private event
carrying the number of capture clients.

The descriptor stays open for the process lifetime: that is what makes the
node advertise the capture capability with the camera off, and therefore
what makes it visible to browsers.
"""

import ctypes
import fcntl
import logging
import os
import select
import signal
import subprocess
import sys
import threading
import time

DEVICE = "/dev/video42"
WIDTH, HEIGHT, FPS = 1280, 720, 30

# Browsers open and close the device several times while negotiating, and
# this sensor copes badly with rapid cycles.
STOP_DELAY = 4.0

# Teardown must be clean: SIGKILL leaves the camera streaming, the ISYS stage
# goes into "stream stop time out", and the Synaptics USB bridge locks up
# until the machine is fully powered off.
STOP_TIMEOUT = 20.0

PIPELINE = [
    "/usr/bin/gst-launch-1.0", "-q",
    "libcamerasrc",
    "!", f"video/x-raw,width={WIDTH},height={HEIGHT}",
    "!", "videoconvert",
    "!", "video/x-raw,format=YUY2",
    # fdsink rather than filesink on the node: v4l2loopback accepts a single
    # writer and we hold the descriptor for priming, so the pipeline writes to
    # its standard output and we relay.
    # One token per argument: gst-launch does not re-join its arguments.
    "!", "fdsink", "fd=1",
]

FRAME_SIZE = WIDTH * HEIGHT * 2
# The environment is inherited as-is: it is up to the systemd unit to point
# GST_PLUGIN_PATH at a libcamera outside the system paths, on releases too old
# to ship one that handles the IPU7.
PIPELINE_ENV = os.environ


class Timespec(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]


class V4l2Event(ctypes.Structure):
    # The union is 64 bytes and aligns on 8 because it holds an __s64. A byte
    # array would align on 1 and shift everything after it, hence the u64s.
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("u", ctypes.c_uint64 * 8),
        ("pending", ctypes.c_uint32),
        ("sequence", ctypes.c_uint32),
        ("timestamp", Timespec),
        ("id", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 8),
    ]


class V4l2EventSubscription(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("id", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 5),
    ]


def _ioc(direction, letter, number, size):
    return (direction << 30) | (size << 16) | (ord(letter) << 8) | number


VIDIOC_DQEVENT = _ioc(2, "V", 89, ctypes.sizeof(V4l2Event))
VIDIOC_SUBSCRIBE_EVENT = _ioc(1, "V", 90, ctypes.sizeof(V4l2EventSubscription))

# v4l2loopback.c: V4L2_EVENT_PRIVATE_START + 0x08E00000 + 1
V4L2_EVENT_PRI_CLIENT_USAGE = 0x08000000 + 0x08E00000 + 1

log = logging.getLogger("ondemand")


class Camera:
    def __init__(self, fd):
        self.fd = fd
        self.proc = None
        self.pump = None

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if self.running:
            return
        log.info("client present, starting camera")
        self.proc = subprocess.Popen(PIPELINE, env=PIPELINE_ENV,
                                     stdout=subprocess.PIPE)
        self.pump = threading.Thread(target=self._pump, daemon=True)
        self.pump.start()

    def _pump(self):
        """Relay frames from the pipeline to the node, one whole frame at a time."""
        stream = self.proc.stdout
        while True:
            frame = stream.read(FRAME_SIZE)
            if len(frame) < FRAME_SIZE:
                return
            try:
                os.write(self.fd, frame)
            except OSError as err:
                log.error("cannot write to %s: %s", DEVICE, err)
                return

    def stop(self):
        if not self.running:
            self.proc = None
            return
        log.info("no client left, stopping camera")
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            # Never kill: a camera left running beats a wedged USB bridge,
            # which only recovers from a full power off.
            log.error("pipeline will not stop, leaving it running")
            return
        if self.pump is not None:
            self.pump.join(timeout=2.0)
            self.pump = None
        log.info("camera stopped cleanly")
        self.proc = None


def set_output_format():
    """Set the node format, while no producer holds it locked."""
    subprocess.run(
        ["/usr/bin/v4l2-ctl", "-d", DEVICE,
         f"--set-fmt-video-out=width={WIDTH},height={HEIGHT},pixelformat=YUYV"],
        check=True,
    )


def prime(fd):
    """Prime the node: the first write is what makes it advertise the capture
    capability, without which no browser lists it."""
    # YUY2 packs each pixel pair as Y0 U Y1 V. Black is Y 16, U and V 128.
    os.write(fd, bytes([16, 128, 16, 128]) * (WIDTH * HEIGHT // 2))


def main():
    logging.basicConfig(format="%(message)s", level=logging.INFO,
                        stream=sys.stdout)

    fd = os.open(DEVICE, os.O_RDWR)
    set_output_format()
    prime(fd)

    sub = V4l2EventSubscription(type=V4L2_EVENT_PRI_CLIENT_USAGE)
    fcntl.ioctl(fd, VIDIOC_SUBSCRIBE_EVENT, sub)
    log.info("primed and subscribed on %s, camera idle", DEVICE)

    camera = Camera(fd)
    poller = select.poll()
    poller.register(fd, select.POLLPRI)

    # When clients go away we do not cut immediately: note the deadline and
    # cancel it if a client comes back meanwhile.
    stop_at = None

    stopping = False

    def on_signal(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    # Bounded timeout, otherwise poll never returns, the stop flag is never
    # re-read, and systemd eventually resorts to SIGKILL.
    while not stopping:
        timeout = 1000.0
        if stop_at is not None:
            timeout = min(timeout, max(0.0, stop_at - time.monotonic()) * 1000)

        events = poller.poll(timeout)

        for _fd, mask in events:
            if not mask & select.POLLPRI:
                continue
            event = V4l2Event()
            try:
                fcntl.ioctl(fd, VIDIOC_DQEVENT, event)
            except OSError as err:
                log.warning("cannot dequeue event: %s", err)
                continue
            if event.type != V4L2_EVENT_PRI_CLIENT_USAGE:
                continue
            count = ctypes.cast(event.u, ctypes.POINTER(ctypes.c_uint32))[0]
            log.info("capture clients: %d", count)
            if count:
                stop_at = None
                camera.start()
            elif camera.running:
                stop_at = time.monotonic() + STOP_DELAY

        if stop_at is not None and time.monotonic() >= stop_at:
            stop_at = None
            camera.stop()

    camera.stop()
    os.close(fd)


if __name__ == "__main__":
    main()
