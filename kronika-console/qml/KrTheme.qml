pragma Singleton
import QtQuick

Item {
    id: theme

    // Core Surfaces & Backgrounds
    readonly property color dark:          "#141414"
    readonly property color surface:       "#191919"
    readonly property color bg:            "#212121"
    readonly property color surfaceRaised: "#242424"
    readonly property color hairline:      "#2C2C2A"
    readonly property color border:        "#383835"

    // Ink / Typography Colors
    readonly property color textPrimary:   "#E0E0E0"
    readonly property color textSecondary: "#9E9E9E"
    readonly property color textMuted:     "#616161"
    readonly property color textLight:     "#FFFFFF"

    // Accents & Signals
    readonly property color accentPrimary:   "#5C6BC0"   // Indigo
    readonly property color accentHover:     "#7986CB"
    readonly property color accentSecondary: "#FFF176"   // Yellow / Warning accent
    readonly property color accentGood:      "#66BB6A"   // Green
    readonly property color accentWarn:      "#FFA726"   // Amber
    readonly property color accentCrit:      "#EF5350"   // Red / Critical

    // Node & Status Colors
    readonly property color nodeOperational: "#66BB6A"
    readonly property color nodeHalted:      "#EF5350"
    readonly property color nodeDegraded:    "#FFA726"
    readonly property color nodeBorder:      "#424242"

    // Spacing
    readonly property int sp2:  2
    readonly property int sp4:  4
    readonly property int sp6:  6
    readonly property int sp8:  8
    readonly property int sp12: 12
    readonly property int sp16: 16
    readonly property int sp24: 24
    readonly property int sp32: 32

    // Motion Durations (ms)
    readonly property int motionHover: 120
    readonly property int motionView:  200
    readonly property int motionData:  300
    readonly property int motionPulse: 600

    // Font Stack
    readonly property string fontFamily: "Titillium Web"
}
