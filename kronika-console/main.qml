import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Kr
import "qml"

ApplicationWindow {
    id: root
    visible: true
    width: 1280
    height: 900
    minimumWidth: 1024
    minimumHeight: 700
    title: "kronika | operator console"
    color: KrTheme.bg

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // Header Bar
        HeaderBar {
            Layout.fillWidth: true
            z: 10
        }

        // Main Workspace SplitView
        SplitView {
            id: mainSplit
            orientation: Qt.Vertical
            Layout.fillWidth: true
            Layout.fillHeight: true

            // Top Area: Graph + Right Panels
            SplitView {
                SplitView.fillWidth: true
                SplitView.fillHeight: true
                SplitView.preferredHeight: root.height * 0.65
                orientation: Qt.Horizontal

                // Center Canvas Graph View
                MetadataTopologyGraph {
                    SplitView.fillWidth: true
                    SplitView.fillHeight: true
                    SplitView.preferredWidth: root.width * 0.68
                }

                // Right Panel (Reasoning + Inspector)
                SplitView {
                    SplitView.preferredWidth: root.width * 0.32
                    SplitView.fillHeight: true
                    orientation: Qt.Vertical

                    ReasoningPanel {
                        SplitView.fillWidth: true
                        SplitView.fillHeight: true
                        SplitView.preferredHeight: parent.height * 0.58
                    }

                    AssetInspector {
                        SplitView.fillWidth: true
                        SplitView.fillHeight: true
                        SplitView.preferredHeight: parent.height * 0.42
                    }
                }
            }

            // Bottom Area: Timeline + Logs
            SplitView {
                SplitView.fillWidth: true
                SplitView.preferredHeight: root.height * 0.35
                orientation: Qt.Horizontal

                DecisionTimeline {
                    SplitView.preferredWidth: root.width * 0.38
                    SplitView.fillHeight: true
                }

                ActivityLog {
                    SplitView.fillWidth: true
                    SplitView.fillHeight: true
                }
            }
        }
    }
}
