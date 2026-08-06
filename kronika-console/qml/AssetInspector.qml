import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Kr

Rectangle {
    id: root
    color: KrTheme.surface
    border.color: KrTheme.border
    border.width: 1

    property var asset: bridge ? bridge.selectedAsset : ({})

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: KrTheme.sp16
        spacing: KrTheme.sp8

        Text {
            text: "ASSET INSPECTOR"
            font.family: KrTheme.fontFamily
            font.pixelSize: 12
            font.bold: true
            color: KrTheme.textMuted
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: KrTheme.hairline }

        RowLayout {
            spacing: 8
            Rectangle {
                width: 10
                height: 10
                radius: 5
                color: root.asset && root.asset.status === "HALTED" ? KrTheme.nodeHalted :
                       (root.asset && root.asset.status === "OPERATIONAL" ? KrTheme.nodeOperational : KrTheme.nodeDegraded)
            }

            Text {
                text: (root.asset && root.asset.name) ? root.asset.name : "No asset selected"
                font.family: KrTheme.fontFamily
                font.pixelSize: 16
                font.bold: true
                color: KrTheme.textLight
            }
        }

        Text {
            text: (root.asset && root.asset.urn) ? root.asset.urn : ""
            font.family: KrTheme.fontFamily
            font.pixelSize: 10
            color: KrTheme.textMuted
            elide: Text.ElideMiddle
            Layout.fillWidth: true
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: KrTheme.hairline }

        GridLayout {
            columns: 2
            rowSpacing: 6
            columnSpacing: 16

            Text { text: "Domain:"; font.pixelSize: 11; color: KrTheme.textSecondary }
            Text { text: (root.asset && root.asset.domain) ? root.asset.domain : "default"; font.pixelSize: 11; font.bold: true; color: KrTheme.textPrimary }

            Text { text: "Owner:"; font.pixelSize: 11; color: KrTheme.textSecondary }
            Text { text: (root.asset && root.asset.owner) ? root.asset.owner : "Unassigned"; font.pixelSize: 11; color: KrTheme.textPrimary }

            Text { text: "Upstream:"; font.pixelSize: 11; color: KrTheme.textSecondary }
            Text { text: root.asset && root.asset.upstream ? root.asset.upstream.join(", ") : "None"; font.pixelSize: 11; color: KrTheme.textPrimary }

            Text { text: "Downstream:"; font.pixelSize: 11; color: KrTheme.textSecondary }
            Text { text: root.asset && root.asset.downstream ? root.asset.downstream.join(", ") : "None"; font.pixelSize: 11; color: KrTheme.textPrimary }
        }

        Item { Layout.fillHeight: true }
    }
}
