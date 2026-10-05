"""Optional system-tray icon (pip install pystray). With it, the window's close button can hide to the tray."""
import logging

from PIL import Image, ImageDraw

log = logging.getLogger("tracker")


def available():
    try:
        import pystray  # noqa: F401
        return True
    except Exception:
        return False


def make_icon():
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((4, 4, 60, 60), fill=(46, 125, 50, 255))
    d.ellipse((22, 22, 42, 42), fill=(255, 255, 255, 255))
    return im


class Tray:
    def __init__(self, app):
        self.app, self.icon, self.running = app, None, False

    def start(self):
        if self.running:
            return True
        try:
            import pystray
            root = self.app.root
            menu = pystray.Menu(
                pystray.MenuItem("Show window", lambda icon, item: root.event_generate("<<ShowWindow>>", when="tail"), default=True),
                pystray.MenuItem("Scan now", lambda icon, item: self.app.trigger.set()),
                pystray.MenuItem("Quit", lambda icon, item: root.event_generate("<<QuitApp>>", when="tail")))
            self.icon = pystray.Icon("Lookout", make_icon(), "Lookout", menu)
            self.icon.run_detached()
            self.running = True
        except Exception:
            log.exception("tray icon failed to start")
            self.icon, self.running = None, False
        return self.running

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
        self.icon, self.running = None, False
