"""The desktop's theme: colours derived from its palette, and following a change (a light/dark switch, another theme)."""
from common import A, check, finish, home_path as P, setup_app, spin
from PyQt6.QtCore import QObject, Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QLabel

from kestrel import thumbs, util
from kestrel.overview import OVERVIEW, Card


def palette_of(window, highlight):
    """Light and dark palettes, with different highlight colours."""
    p = QPalette(QColor(window))
    p.setColor(QPalette.ColorRole.Highlight, QColor(highlight))
    return p


app = setup_app()
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
