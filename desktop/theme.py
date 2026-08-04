"""
The look: one place, so the desktop app and the web page stay recognisably the
same product.

Colours are the hypsometric palette the web UI already uses — deep slate
ground, glacier for finished work, amber reserved for whatever is happening
right now. Amber never decorates: if something is amber, it is live.
"""

from PySide6 import QtGui

# ── Palette ──────────────────────────────────────────────────────────────────

# Blender's own palette: orange #EA7601, cyan #266C87, white.
#
# #266C87 is dark — as small text on this near-black ground it is close to
# unreadable, so it is used where it belongs (fills, bars, borders, the tile
# map) and a lighter tint of the same hue carries text. That keeps the identity
# Blender's without making the app hard to read.
BL_ORANGE   = "#EA7601"   # Blender orange
BL_CYAN     = "#266C87"   # Blender cyan
BL_CYAN_LIT = "#7FBBD6"   # same hue, lifted for text on a dark ground
WHITE       = "#FFFFFF"

BG        = "#0B0F14"   # window ground
PANEL     = "#121A23"   # cards
PANEL_2   = "#0F161E"   # insets, inputs
LINE      = "#22323D"   # borders — a hint of the cyan
LINE_SOFT = "#1A2830"   # separators inside a card

INK       = "#EEF4F8"   # primary text, near white
DIM       = "#8AA2B0"   # labels
FAINT     = "#5F7480"   # captions

GLACIER   = BL_CYAN_LIT # done / primary action
GLACIER_D = BL_CYAN     # its dark partner: borders, bar troughs, hovers
AMBER     = BL_ORANGE   # happening right now
ROCK      = "#22323D"   # pending
ALERT     = "#E5534B"   # wrong
GOOD      = "#68C08A"   # finished well

# One hue per kind of render. Tiled gets Blender's cyan because it is the
# flagship; the other two stay clear of orange and red so a kind can never be
# misread as a state.
KIND = {
    "tiled":     BL_CYAN_LIT,   # Blender cyan — the 120k renders
    "animation": "#B0A7E6",     # periwinkle
    "still":     "#86BE86",     # moss
}

# What each state is CALLED on screen. The stored value stays "running": it is
# written into every job file and every history entry already on disk, and
# renaming it would mean migrating all of them to change one word in a table.
STATE_LABEL = {
    "running": "rendering",
}


def state_label(state):
    return STATE_LABEL.get(state, state or "?")


STATE_COLOUR = {
    "running":     AMBER,
    "done":        GOOD,
    "failed":      ALERT,
    "interrupted": ALERT,
    "stopped":     DIM,
    "queued":      DIM,
}

# ── Type ─────────────────────────────────────────────────────────────────────
#
# Bahnschrift is Windows' DIN-derived engineering face: instrumentation, not
# decoration. Cascadia Mono for anything numeric, so columns of figures line up
# and a changing digit does not shift the ones beside it.

DISPLAY = "Bahnschrift"
BODY    = "Segoe UI"
MONO    = "Cascadia Mono"


def font(family=BODY, size=10, weight=QtGui.QFont.Normal, letter_spacing=0.0):
    f = QtGui.QFont(family, size)
    f.setWeight(weight)
    if letter_spacing:
        f.setLetterSpacing(QtGui.QFont.PercentageSpacing, 100 + letter_spacing)
    return f


def label_font():
    """
    Small spaced-out section labels.

    Text is uppercased by the caller rather than with setCapitalization():
    combined with letter spacing, Qt renders the space between words with no
    advance, so "CURRENT RENDER" came out as "CURRENTRENDER".
    """
    f = QtGui.QFont(DISPLAY, 8)
    f.setLetterSpacing(QtGui.QFont.PercentageSpacing, 116)
    f.setWordSpacing(5.0)
    return f


# ── Stylesheet ───────────────────────────────────────────────────────────────

def stylesheet():
    return f"""
    QWidget {{
        background: {BG};
        color: {INK};
        font-family: "{BODY}";
        font-size: 13px;
    }}

    QFrame#card {{
        background: {PANEL};
        border: 1px solid {LINE};
        border-radius: 10px;
    }}
    /* No font-family here on purpose: setting one in the stylesheet rebuilds
       the QFont and discards the letter and word spacing set in code. */
    QLabel#cardTitle {{ color: {DIM}; }}
    QLabel#brand {{
        font-family: "{DISPLAY}";
        font-size: 17px;
        letter-spacing: 2px;
        font-weight: 600;
    }}
    QLabel#jobName {{
        font-family: "{DISPLAY}";
        font-size: 25px;
        font-weight: 600;
    }}
    QLabel#mono, QLabel#meta {{
        font-family: "{MONO}";
        font-size: 11px;
        color: {FAINT};
    }}

    QPushButton {{
        background: {PANEL};
        border: 1px solid {LINE};
        border-radius: 7px;
        padding: 8px 15px;
        color: {INK};
        font-family: "{DISPLAY}";
        letter-spacing: 1px;
    }}
    QPushButton:hover:!disabled  {{ background: #16202B; border-color: {GLACIER_D}; }}
    QPushButton:disabled         {{ color: {FAINT}; border-color: {LINE_SOFT}; }}
    QPushButton#primary {{
        background: {GLACIER}; color: #08131A; border-color: {GLACIER};
        font-weight: 600;
    }}
    QPushButton#primary:hover:!disabled {{ background: #B6E0F0; }}
    QPushButton#primary:disabled {{ background: {ROCK}; color: {FAINT};
                                    border-color: {LINE}; }}
    /* Sits inline with a checkbox or a caption in a panel header row, where
       the standard 8x15 padding would tower over the text beside it. */
    QPushButton#mini {{
        padding: 3px 9px;
        font-size: 11px;
        letter-spacing: .5px;
        border-radius: 5px;
    }}
    QPushButton#danger {{ color: #F2A19C; border-color: rgba(229,83,75,0.5); }}
    QPushButton#danger:hover:!disabled {{ background: rgba(229,83,75,0.14);
                                          border-color: {ALERT}; }}

    QTableWidget {{
        background: {PANEL};
        border: none;
        gridline-color: transparent;
        selection-background-color: #16202B;
        selection-color: {INK};
        outline: none;
    }}
    QTableWidget::item {{ padding: 7px 8px; border-bottom: 1px solid {LINE_SOFT}; }}
    QTableWidget::item:selected {{ background: #16202B; }}
    QHeaderView::section {{
        background: {PANEL};
        color: {DIM};
        border: none;
        border-bottom: 1px solid {LINE};
        padding: 8px;
        font-family: "{DISPLAY}";
        font-size: 10px;
    }}

    QLineEdit, QSpinBox, QComboBox {{
        background: {PANEL_2};
        border: 1px solid {LINE};
        border-radius: 7px;
        padding: 8px 10px;
        color: {INK};
        font-family: "{MONO}";
        selection-background-color: {GLACIER_D};
    }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {GLACIER_D}; }}

    /* Drop-down and spin arrows are left to Qt.
       Qt's stylesheets do not support the CSS border-triangle trick — it
       renders as a small square — and the moment drop-down/up-button is styled
       at all, Qt stops drawing its own arrow. Fusion's arrows already suit the
       dark palette, so the least-bad option is not to touch them. */
    QComboBox QAbstractItemView {{
        background: {PANEL};
        border: 1px solid {LINE};
        selection-background-color: #16202B;
        color: {INK};
        padding: 4px;
    }}
    QCheckBox {{ color: {DIM}; }}
    QDialog {{ background: {BG}; }}

    QPlainTextEdit {{
        background: #080C11;
        border: 1px solid {LINE_SOFT};
        border-radius: 8px;
        color: {DIM};
        font-family: "{MONO}";
        font-size: 11px;
        padding: 8px;
    }}

    /* Panels. The title bar is the grab handle, so it has to look like one. */
    QDockWidget {{
        color: {DIM};
        font-family: "{DISPLAY}";
        font-size: 10px;
        titlebar-close-icon: none;
        titlebar-normal-icon: none;
    }}
    /* The title bar is a real widget (see CollapsibleDock), so it is styled
       here rather than through QDockWidget::title. */
    QWidget#dockTitle {{
        background: {PANEL};
        border: 1px solid {LINE};
        border-top-left-radius: 9px;
        border-top-right-radius: 9px;
    }}
    QWidget#dockTitle QToolButton {{
        background: transparent; border: none; border-radius: 4px;
        color: {DIM};
    }}
    QWidget#dockTitle QToolButton:hover {{ background: {ROCK}; color: {INK}; }}
    QDockWidget > QWidget {{
        background: {PANEL};
        border: 1px solid {LINE};
        border-top: none;
        border-bottom-left-radius: 9px;
        border-bottom-right-radius: 9px;
    }}
    QDockWidget::close-button, QDockWidget::float-button {{
        background: transparent; border: none; padding: 2px;
        subcontrol-position: top right; subcontrol-origin: margin;
    }}
    QDockWidget::close-button:hover, QDockWidget::float-button:hover {{
        background: {ROCK}; border-radius: 4px;
    }}
    QMainWindow::separator {{ background: {BG}; width: 6px; height: 6px; }}
    QMainWindow::separator:hover {{ background: {GLACIER_D}; }}

    /* Tabs, for panels docked on top of each other. */
    QTabBar::tab {{
        background: {PANEL_2};
        color: {DIM};
        border: 1px solid {LINE};
        border-bottom: none;
        border-top-left-radius: 7px;
        border-top-right-radius: 7px;
        padding: 7px 14px;
        margin-right: 2px;
        font-family: "{DISPLAY}";
        font-size: 10px;
        /* Without a minimum, Qt shrinks dock tabs until the label elides to
           "G…" and the panel becomes unidentifiable. */
        min-width: 76px;
    }}
    QTabBar::tab:selected {{ background: {PANEL}; color: {INK}; }}
    QTabBar::tab:hover:!selected {{ color: {INK}; }}

    QSplitter::handle {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {ROCK}; border-radius: 5px;
                                   min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {GLACIER_D}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QToolBar#headerbar {{
        background: {BG};
        border: none;
        border-bottom: 1px solid {LINE};
        spacing: 0;
    }}

    QMenuBar {{
        background: {BG};
        color: {DIM};
        border-bottom: 1px solid {LINE};
        padding: 2px 4px;
    }}
    QMenuBar::item {{ background: transparent; padding: 6px 11px;
                      border-radius: 5px; }}
    QMenuBar::item:selected {{ background: {PANEL}; color: {INK}; }}
    QMenuBar::item:pressed  {{ background: #16202B; color: {INK}; }}

    QMenu {{
        background: {PANEL};
        border: 1px solid {LINE};
        border-radius: 8px;
        padding: 5px;
        color: {INK};
    }}
    QMenu::item {{ padding: 7px 26px 7px 24px; border-radius: 5px; }}
    QMenu::item:selected {{ background: #16202B; }}
    QMenu::item:disabled {{ color: {FAINT}; }}
    QMenu::separator {{ height: 1px; background: {LINE_SOFT}; margin: 5px 10px; }}

    QStatusBar {{ color: {FAINT}; font-family: "{MONO}"; font-size: 11px; }}
    QToolTip {{ background: {PANEL}; color: {INK}; border: 1px solid {LINE};
                padding: 5px; }}
    """


def apply(app):
    """Dark palette plus the stylesheet. Fusion so it looks the same anywhere."""
    app.setStyle("Fusion")
    p = QtGui.QPalette()
    p.setColor(QtGui.QPalette.Window,          QtGui.QColor(BG))
    p.setColor(QtGui.QPalette.WindowText,      QtGui.QColor(INK))
    p.setColor(QtGui.QPalette.Base,            QtGui.QColor(PANEL_2))
    p.setColor(QtGui.QPalette.AlternateBase,   QtGui.QColor(PANEL))
    p.setColor(QtGui.QPalette.Text,            QtGui.QColor(INK))
    p.setColor(QtGui.QPalette.Button,          QtGui.QColor(PANEL))
    p.setColor(QtGui.QPalette.ButtonText,      QtGui.QColor(INK))
    p.setColor(QtGui.QPalette.Highlight,       QtGui.QColor(GLACIER))
    p.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#08131A"))
    p.setColor(QtGui.QPalette.ToolTipBase,     QtGui.QColor(PANEL))
    p.setColor(QtGui.QPalette.ToolTipText,     QtGui.QColor(INK))
    app.setPalette(p)
    app.setStyleSheet(stylesheet())
