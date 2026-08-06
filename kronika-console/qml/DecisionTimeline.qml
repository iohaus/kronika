import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Kr

Rectangle {
    id: root
    color: KrTheme.surface
    border.color: KrTheme.border
    border.width: 1

    property var ep: bridge ? bridge.currentEpisode : ({})

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: KrTheme.sp12
        spacing: KrTheme.sp8

        RowLayout {
            spacing: KrTheme.sp8
            Text {
                text: "DECISION TIMELINE & AUDIT TRAIL"
                font.family: KrTheme.fontFamily
                font.pixelSize: 11
                font.bold: true
                color: KrTheme.textMuted
            }

            Item { Layout.fillWidth: true }

            Rectangle {
                implicitHeight: 18
                implicitWidth: auditTxt.implicitWidth + 10
                color: KrTheme.dark
                radius: 3
                border.color: KrTheme.hairline
                Text {
                    id: auditTxt
                    anchors.centerIn: parent
                    text: "AUDIT: CLEAN"
                    font.pixelSize: 9
                    font.bold: true
                    color: KrTheme.accentGood
                }
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: KrTheme.hairline }

        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            Rectangle {
                width: 12
                height: 12
                radius: 6
                color: root.ep && root.ep.status === "CONTAINMENT_RECOMMENDED" ? KrTheme.accentCrit :
                       (root.ep && root.ep.status === "APPROVED" ? KrTheme.accentWarn : KrTheme.accentGood)
            }

            ColumnLayout {
                spacing: 2
                Text {
                    text: (root.ep && root.ep.event_id) ? root.ep.event_id : "ep-idle"
                    font.family: KrTheme.fontFamily
                    font.pixelSize: 12
                    font.bold: true
                    color: KrTheme.textLight
                }
                Text {
                    text: (root.ep && root.ep.status) ? root.ep.status : "IDLE"
                    font.pixelSize: 10
                    color: root.ep && root.ep.status === "CONTAINMENT_RECOMMENDED" ? KrTheme.accentCrit :
                           (root.ep && root.ep.status === "APPROVED" ? KrTheme.accentWarn : KrTheme.accentGood)
                }
            }

            Item { Layout.fillWidth: true }

            Text {
                text: root.ep && root.ep.status === "APPROVED" ? "Halted (Approved)" :
                      (root.ep && root.ep.status === "REJECTED" ? "Overridden (Rejected)" :
                      (root.ep && root.ep.halt_set && root.ep.halt_set.length > 0 ? "Halted: " + root.ep.halt_set.length + " pipeline(s)" : "Nominal"))
                font.pixelSize: 11
                font.bold: true
                color: root.ep && root.ep.status === "APPROVED" ? KrTheme.accentCrit :
                       (root.ep && root.ep.status === "REJECTED" ? KrTheme.accentWarn : KrTheme.accentGood)
            }
        }
    }
}
