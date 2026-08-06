import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Kr

Rectangle {
    id: root
    color: KrTheme.surface
    border.color: KrTheme.border
    border.width: 1

    property var logs: bridge ? bridge.activityLogs : []

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: KrTheme.sp12
        spacing: KrTheme.sp8

        RowLayout {
            spacing: KrTheme.sp8
            Text {
                text: "AGENT ACTIVITY & DECISION LOG"
                font.family: KrTheme.fontFamily
                font.pixelSize: 11
                font.bold: true
                color: KrTheme.textMuted
            }

            Item { Layout.fillWidth: true }

            Text {
                text: (root.logs ? root.logs.length : 0) + " entries"
                font.pixelSize: 10
                color: KrTheme.textMuted
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: KrTheme.hairline }

        ListView {
            id: listView
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: root.logs

            onCountChanged: listView.positionViewAtEnd()

            delegate: RowLayout {
                width: listView.width
                spacing: 8

                Text {
                    text: modelData.timestamp
                    font.family: "Monospace"
                    font.pixelSize: 10
                    color: KrTheme.textMuted
                }

                Text {
                    text: "[" + modelData.level + "]"
                    font.family: "Monospace"
                    font.pixelSize: 10
                    font.bold: true
                    color: modelData.level === "WARN" ? KrTheme.accentWarn :
                           (modelData.level === "ERROR" ? KrTheme.accentCrit : KrTheme.accentPrimary)
                }

                Text {
                    text: modelData.message
                    font.family: KrTheme.fontFamily
                    font.pixelSize: 11
                    color: KrTheme.textPrimary
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
        }
    }
}
