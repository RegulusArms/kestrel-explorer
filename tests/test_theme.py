"""The desktop's theme: colours derived from its palette, and following a change (a light/dark switch, another theme);
and the desktop's own settings (Cinnamon's on Linux Mint)."""
import os
import shutil
import subprocess

CINNAMON_SCHEMAS = """<schemalist>
  <schema id="org.cinnamon.desktop.background" path="/org/cinnamon/desktop/background/">
    <key name="picture-uri" type="s"><default>''</default></key>
  </schema>
  <schema id="org.cinnamon.desktop.privacy" path="/org/cinnamon/desktop/privacy/">
    <key name="remember-recent-files" type="b"><default>true</default></key>
  </schema>
  <schema id="org.cinnamon.desktop.interface" path="/org/cinnamon/desktop/interface/">
    <key name="icon-theme" type="s"><default>'Mint-Y'</default></key>
  </schema>
</schemalist>
"""


def cinnamon_schemas():
    """Stand-ins for Cinnamon's settings (Linux Mint), compiled before GLib first reads the schemas. False if the
    schema compiler is missing."""
    d = os.path.join(os.path.expanduser("~"), "schemas")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "org.cinnamon.test.gschema.xml"), "w") as f:
        f.write(CINNAMON_SCHEMAS)
    compiler = "/usr/lib/x86_64-linux-gnu/glib-2.0/glib-compile-schemas"
    if not os.path.isfile(compiler):
        compiler = shutil.which("glib-compile-schemas")
    if not compiler or subprocess.run([compiler, d]).returncode != 0:
        return False
    os.environ["GSETTINGS_SCHEMA_DIR"] = d
    return True


schemas = cinnamon_schemas()

from common import A, check, finish, home_path as P, setup_app, skip, spin  # noqa: E402
from PyQt6.QtCore import QObject, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QPalette  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

from kestrel import thumbs, util  # noqa: E402
from kestrel.overview import OVERVIEW, Card  # noqa: E402


def palette_of(window, highlight):
    """Light and dark palettes, with different highlight colours."""
    p = QPalette(QColor(window))
    p.setColor(QPalette.ColorRole.Highlight, QColor(highlight))
    return p


app = setup_app()

# -- the desktop's own settings
desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
if schemas:
    os.environ["XDG_CURRENT_DESKTOP"] = "X-Cinnamon"
    check(util.desktop_schema("org.gnome.desktop.background") == "org.cinnamon.desktop.background"
          and util.desktop_schema("org.gnome.desktop.privacy") == "org.cinnamon.desktop.privacy"
          and util.desktop_schema("org.gnome.desktop.interface") == "org.cinnamon.desktop.interface",
          "on Cinnamon, its own settings are used (wallpaper, file history, icon theme)")
    check(util.has_schema_key("org.cinnamon.desktop.background", "picture-uri")
          and not util.has_schema_key("org.cinnamon.desktop.background", "picture-uri-dark"),
          "Cinnamon's wallpaper has no dark picture, so only the one is set")
    check(util.desktop_schema("org.gnome.desktop.a11y") == "org.gnome.desktop.a11y",
          "a setting Cinnamon has no copy of stays GNOME's")
    os.environ["XDG_CURRENT_DESKTOP"] = "ubuntu:GNOME"
    check(util.desktop_schema("org.gnome.desktop.background") == "org.gnome.desktop.background",
          "other desktops use GNOME's settings")
else:
    skip("the desktop's own settings (no glib-compile-schemas)")
os.environ["XDG_CURRENT_DESKTOP"] = desktop

# -- the GTK theme's colours (Qt before 6.5)
check(util.needs_gtk_palette("6.4.2") and util.needs_gtk_palette("6.4.0") and not util.needs_gtk_palette("6.5.0")
      and not util.needs_gtk_palette("6.10.2"),
      "Qt before 6.5 needs Kestrel to read the GTK theme's colours; 6.5 and newer do it themselves")
gp = util.gtk_palette_from({"theme_bg_color": "#2b2b2b", "theme_fg_color": "#dadada", "theme_base_color": "#323232",
                            "theme_text_color": "#ffffff", "theme_selected_bg_color": "#35a854",
                            "theme_selected_fg_color": "#ffffff", "insensitive_fg_color": "#888888"})
R = QPalette.ColorRole
check(gp is not None and gp.color(R.Window) == QColor("#2b2b2b") and gp.color(R.WindowText) == QColor("#dadada")
      and gp.color(R.Base) == QColor("#323232") and gp.color(R.Text) == QColor("#ffffff")
      and gp.color(R.Highlight) == QColor("#35a854")
      and gp.color(QPalette.ColorGroup.Disabled, R.Text) == QColor("#888888"),
      "a GTK theme's colours become the palette (window, text, selection, disabled text)")
check(util.gtk_palette_from({"theme_fg_color": "#dadada"}) is None, "a theme without the basic colours changes nothing")

LIGHT, DARK = palette_of("#fafafa", "#e95420"), palette_of("#2a2a2a", "#3584e4")
QApplication.setPalette(LIGHT)
w = A.open_window([util.HOME])
w.navigate(OVERVIEW)
spin(300)

# -- colours from the palette
light_ok = not util.dark_theme()
light_card, light_error = util.card_color(), util.error_color()
QApplication.setPalette(DARK)
check(light_ok and util.dark_theme(), "a light palette is recognised as light and a dark one as dark")
check(light_card != LIGHT.color(QPalette.ColorRole.Window) and util.card_color() != DARK.color(QPalette.ColorRole.Window),
      "cards stand out from the window background in light and dark themes")
check(util.error_color().lightness() > light_error.lightness(), "error text is a lighter red on a dark background")
QApplication.setPalette(LIGHT)
spin(100)

# -- following a change
calls = [0]
owner = QObject()
util.on_palette_change(owner, lambda: calls.__setitem__(0, calls[0] + 1))
label = QLabel("x")
label.setStyleSheet("QLabel { color: palette(highlight); }")
label.show()
spin(100)
dark2 = QPalette(DARK)  # a theme switch can change the palette more than once
dark2.setColor(QPalette.ColorRole.Link, QColor("#3584e4"))
QApplication.setPalette(DARK)
QApplication.setPalette(dark2)
spin(300)
check(calls[0] == 1, "a palette change runs the registered callbacks once")
check(label.palette().color(QPalette.ColorRole.WindowText) == QColor("#3584e4"),
      "stylesheets that use palette() take the new colours")
header = None
for i in range(w.sidebar.count()):
    if w.sidebar.item(i).data(Qt.ItemDataRole.UserRole)[0] == "header":
        header = w.sidebar.item(i).foreground().color()
check(header == DARK.color(QPalette.ColorRole.PlaceholderText), "the sidebar's headers take the new colours")
cards = w.findChildren(Card)
check(bool(cards) and util.card_color().name() in cards[0].styleSheet(), "the Overview's cards take the new colours")
check(A._thumbs.folder_color == "#3584e4" and A._thumbs.color_for(P("any")) == "#3584e4",
      "the default folder colour follows the accent, also in folder previews")
check(thumbs.follows_accent("accent") and thumbs.follows_accent("") and thumbs.follows_accent("#D9652F")
      and not thumbs.follows_accent("#33d17a"),
      "“accent”, no setting and the old fixed default follow the accent; a chosen colour doesn't")
A._settings.setValue("folder_color", "#33d17a")
A.apply_thumb_settings(A._thumbs, A._settings)
owner.deleteLater()
spin(100)
QApplication.setPalette(LIGHT)
spin(300)
check(calls[0] == 1, "a deleted owner's callback is dropped")
check(A._thumbs.folder_color == "#33d17a", "a chosen folder colour stays when the accent changes")

finish()
