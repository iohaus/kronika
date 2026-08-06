import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Kr

Rectangle {
    id: root
    implicitHeight: 52
    color: KrTheme.surface
    border.color: KrTheme.border
    border.width: 1

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: KrTheme.sp16
        anchors.rightMargin: KrTheme.sp16
        spacing: KrTheme.sp16

        // Brand & Title
        RowLayout {
            spacing: KrTheme.sp8
            Rectangle {
                width: 12
                height: 12
                radius: 6
                color: (!bridge || bridge.systemStatus === "HEALTHY") ? KrTheme.accentGood :
                       (bridge.systemStatus === "CONTAINED" ? KrTheme.accentCrit : KrTheme.accentWarn)

                SequentialAnimation on opacity {
                    running: bridge && bridge.systemStatus !== "HEALTHY"
                    loops: Animation.Infinite
                    PropertyAnimation { to: 0.3; duration: 400 }
                    PropertyAnimation { to: 1.0; duration: 400 }
                }
            }

            Text {
                text: "KRONIKA"
                font.family: KrTheme.fontFamily
                font.pixelSize: 15
                font.bold: true
                color: KrTheme.textLight
            }

            Text {
                text: "|  AUTONOMOUS OPERATIONS CENTER"
                font.family: KrTheme.fontFamily
                font.pixelSize: 12
                color: KrTheme.textSecondary
            }
        }

        Item { Layout.fillWidth: true }

        // Metrics pill
        Rectangle {
            implicitHeight: 28
            implicitWidth: metricsLayout.implicitWidth + 24
            color: KrTheme.surfaceRaised
            radius: 14
            border.color: KrTheme.hairline

            RowLayout {
                id: metricsLayout
                anchors.centerIn: parent
                spacing: KrTheme.sp16

                RowLayout {
                    spacing: 4
                    Text { text: "Assets:"; font.pixelSize: 11; color: KrTheme.textMuted }
                    Text { text: bridge ? bridge.assetCount : 0; font.pixelSize: 11; font.bold: true; color: KrTheme.textPrimary }
                }

                Rectangle { width: 1; height: 12; color: KrTheme.hairline }

                RowLayout {
                    spacing: 4
                    Text { text: "Pending Actions:"; font.pixelSize: 11; color: KrTheme.textMuted }
                    Text {
                        text: bridge && bridge.pendingActions ? bridge.pendingActions.length : 0
                        font.pixelSize: 11
                        font.bold: true
                        color: bridge && bridge.pendingActions && bridge.pendingActions.length > 0 ? KrTheme.accentWarn : KrTheme.textPrimary
                    }
                }

                Rectangle { width: 1; height: 12; color: KrTheme.hairline }

                RowLayout {
                    spacing: 4
                    Text { text: "Status:"; font.pixelSize: 11; color: KrTheme.textMuted }
                    Text {
                        text: bridge ? bridge.systemStatus : "HEALTHY"
                        font.pixelSize: 11
                        font.bold: true
                        color: (!bridge || bridge.systemStatus === "HEALTHY") ? KrTheme.accentGood :
                               (bridge.systemStatus === "CONTAINED" ? KrTheme.accentCrit : KrTheme.accentWarn)
                    }
                }
            }
        }

        // Action Buttons
        Button {
            text: "⚡ Inject Negative Billing Anomaly"
            font.family: KrTheme.fontFamily
            font.pixelSize: 12
            font.bold: true

            contentItem: Text {
                text: parent.text
                font: parent.font
                color: KrTheme.textLight
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }

            background: Rectangle {
                color: parent.down ? KrTheme.accentCrit : (parent.hovered ? "#D32F2F" : "#C62828")
                radius: 4
            }

            onClicked: {
                if (bridge) bridge.triggerQualityAnomaly("urn:li:dataset:(urn:li:dataPlatform:hive,raw_patients,PROD)", "billing_amount")
            }
        }

        Button {
            text: "↺ Reset World Model"
            font.family: KrTheme.fontFamily
            font.pixelSize: 12

            contentItem: Text {
                text: parent.text
                font: parent.font
                color: KrTheme.textPrimary
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }

            background: Rectangle {
                color: parent.down ? KrTheme.surfaceRaised : (parent.hovered ? KrTheme.border : KrTheme.surface)
                border.color: KrTheme.border
                radius: 4
            }

            onClicked: {
                if (bridge) bridge.resetDemo()
            }
        }
    }
}
