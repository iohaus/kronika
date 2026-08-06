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
    property var pending: bridge ? bridge.pendingActions : []

    property int stepProgress: 4
    property bool isDeliberating: false

    property bool hasActiveEpisode: Boolean(root.ep && root.ep.event_id && root.ep.event_id !== "ep-idle" && root.ep.status !== "IDLE" && root.ep.status !== "ep-reset")

    function shortUrn(urn) {
        if (!urn) return "N/A"
        if (urn.indexOf(",") !== -1 && urn.indexOf(")") !== -1) {
            var parts = urn.split(",")
            if (parts.length >= 2) return parts[1]
        }
        var parts2 = urn.split(":")
        return parts2[parts2.length - 1]
    }

    // Deliberation Staggered Step Fade-In Timer
    Timer {
        id: deliberationTimer
        interval: 220
        repeat: true
        onTriggered: {
            var total = (root.ep && root.ep.deliberation_steps) ? root.ep.deliberation_steps.length : 0
            root.stepProgress += 1
            if (root.stepProgress >= total) {
                deliberationTimer.stop()
                root.isDeliberating = false
            }
        }
    }

    Connections {
        target: bridge
        function onEpisodeUpdated() {
            if (root.ep && (root.ep.status === "CONTAINMENT_RECOMMENDED" || root.ep.status === "ANALYZED")) {
                root.stepProgress = 0
                root.isDeliberating = true
                deliberationTimer.restart()
            } else if (root.ep && (root.ep.status === "IDLE" || root.ep.status === "ep-reset")) {
                root.stepProgress = 0
                root.isDeliberating = false
                deliberationTimer.stop()
            }
        }
    }

    ScrollView {
        anchors.fill: parent
        anchors.margins: KrTheme.sp12
        clip: true
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        ColumnLayout {
            width: parent.width
            spacing: KrTheme.sp12

            // Section Title Header
            RowLayout {
                Layout.fillWidth: true
                spacing: KrTheme.sp8

                Text {
                    text: "AGENT REASONING & DELIBERATION"
                    font.family: KrTheme.fontFamily
                    font.pixelSize: 12
                    font.bold: true
                    color: KrTheme.textMuted
                }

                Item { Layout.fillWidth: true }

                // Deliberating pulse badge
                Rectangle {
                    visible: Boolean(root.isDeliberating)
                    implicitHeight: 18
                    implicitWidth: delibTxt.implicitWidth + 10
                    color: "#3A2B1B"
                    radius: 4
                    border.color: KrTheme.accentWarn

                    Text {
                        id: delibTxt
                        anchors.centerIn: parent
                        text: "DELIBERATING..."
                        font.pixelSize: 9
                        font.bold: true
                        color: KrTheme.accentWarn
                    }

                    SequentialAnimation on opacity {
                        running: Boolean(root.isDeliberating)
                        loops: Animation.Infinite
                        PropertyAnimation { to: 0.3; duration: 300 }
                        PropertyAnimation { to: 1.0; duration: 300 }
                    }
                }

                Rectangle {
                    visible: Boolean(root.hasActiveEpisode)
                    implicitHeight: 20
                    implicitWidth: epIdTxt.implicitWidth + 12
                    color: KrTheme.surfaceRaised
                    radius: 4
                    border.color: KrTheme.hairline
                    Text {
                        id: epIdTxt
                        anchors.centerIn: parent
                        text: root.ep && root.ep.event_id ? root.ep.event_id : "ep-idle"
                        font.pixelSize: 10
                        font.bold: true
                        color: KrTheme.textSecondary
                    }
                }
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: KrTheme.hairline }

            // DEFAULT NOMINAL / IDLE STATE PLACEHOLDER (Only shown when system is HEALTHY and no active episode)
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 180
                visible: !root.hasActiveEpisode
                color: KrTheme.surface
                border.color: KrTheme.hairline
                border.width: 1
                radius: 6

                ColumnLayout {
                    anchors.centerIn: parent
                    spacing: 10

                    Rectangle {
                        Layout.alignment: Qt.AlignHCenter
                        width: 40
                        height: 40
                        radius: 20
                        color: "#182E1C"
                        border.color: KrTheme.accentGood
                        border.width: 1

                        Text {
                            anchors.centerIn: parent
                            text: "✓"
                            font.pixelSize: 18
                            font.bold: true
                            color: KrTheme.accentGood
                        }
                    }

                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: "SYSTEM NOMINAL"
                        font.family: KrTheme.fontFamily
                        font.pixelSize: 13
                        font.bold: true
                        color: KrTheme.textLight
                    }

                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: "No active decision episodes or pending actions.\nStanding by for telemetry and metadata events."
                        font.family: KrTheme.fontFamily
                        font.pixelSize: 11
                        color: KrTheme.textMuted
                        horizontalAlignment: Text.AlignHCenter
                    }
                }
            }

            // ACTIVE EPISODE CONTENT (Deliberation Steps, Recommended Action, Evidence Path)
            ColumnLayout {
                Layout.fillWidth: true
                spacing: KrTheme.sp12
                visible: Boolean(root.hasActiveEpisode)

                // Deliberation Checklist with Sequential Fade-In
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 6

                    Repeater {
                        model: root.ep && root.ep.deliberation_steps ? root.ep.deliberation_steps : []

                        delegate: RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            opacity: index < root.stepProgress ? 1.0 : 0.0
                            Behavior on opacity {
                                NumberAnimation { duration: 200 }
                            }

                            Text {
                                text: index < root.stepProgress ? "✓" : "○"
                                font.bold: true
                                font.pixelSize: 12
                                color: index < root.stepProgress ? KrTheme.accentGood : KrTheme.textMuted
                            }

                            Text {
                                text: modelData.step
                                font.family: KrTheme.fontFamily
                                font.pixelSize: 12
                                color: index < root.stepProgress ? KrTheme.textPrimary : KrTheme.textMuted
                            }
                        }
                    }
                }

                // Recommended Action Banner (Shown when containment recommendation exists or actions pending/resolved)
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: actionLayout.implicitHeight + 24
                    visible: Boolean(root.ep && ((root.ep.halt_set && root.ep.halt_set.length > 0) || (root.pending && root.pending.length > 0) || root.ep.status === "APPROVED" || root.ep.status === "REJECTED"))
                    color: root.ep && root.ep.halt_set && root.ep.halt_set.length > 0 ? "#2A1515" : KrTheme.surfaceRaised
                    border.color: root.ep && root.ep.halt_set && root.ep.halt_set.length > 0 ? KrTheme.accentCrit : KrTheme.border
                    border.width: 1
                    radius: 6

                    ColumnLayout {
                        id: actionLayout
                        anchors.fill: parent
                        anchors.margins: KrTheme.sp12
                        spacing: 8

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Text {
                                text: "RECOMMENDED ACTION"
                                font.family: KrTheme.fontFamily
                                font.pixelSize: 10
                                font.bold: true
                                color: KrTheme.textMuted
                            }

                            Item { Layout.fillWidth: true }

                            Text {
                                text: "CONFIDENCE: " + Math.round(((root.ep && root.ep.confidence) || 1.0) * 100) + "%"
                                font.family: KrTheme.fontFamily
                                font.pixelSize: 10
                                font.bold: true
                                color: KrTheme.accentGood
                            }
                        }

                        Text {
                            text: root.ep && root.ep.halt_set && root.ep.halt_set.length > 0 ?
                                  "HALT PIPELINE: " + root.shortUrn(root.ep.target_urn || root.ep.halt_set[0]) :
                                  "NO CONTAINMENT REQUIRED"
                            font.family: KrTheme.fontFamily
                            font.pixelSize: 15
                            font.bold: true
                            color: root.ep && root.ep.halt_set && root.ep.halt_set.length > 0 ? KrTheme.accentCrit : KrTheme.accentGood
                        }

                        Text {
                            text: (root.ep && root.ep.rationale) || "System nominal."
                            font.family: KrTheme.fontFamily
                            font.pixelSize: 11
                            color: KrTheme.textSecondary
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }

                        // Action Approval Buttons — STRICTLY VISIBLE ONLY WHEN PENDING ACTIONS > 0
                        RowLayout {
                            Layout.fillWidth: true
                            Layout.topMargin: 4
                            spacing: 8

                            visible: Boolean(root.pending && root.pending.length > 0)
                            opacity: visible ? 1.0 : 0.0
                            Behavior on opacity {
                                NumberAnimation { duration: 200 }
                            }

                            Button {
                                text: "✓ Approve Pipeline Halt"
                                Layout.fillWidth: true
                                implicitHeight: 32
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
                                    color: parent.down ? "#2E7D32" : (parent.hovered ? "#388E3C" : "#43A047")
                                    radius: 4
                                }

                                onClicked: {
                                    var actionId = (root.pending && root.pending.length > 0) ? root.pending[0].action_id : ""
                                    if (bridge) bridge.approveAction(actionId)
                                }
                            }

                            Button {
                                text: "✗ Reject"
                                implicitWidth: 80
                                implicitHeight: 32
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
                                    var actionId = (root.pending && root.pending.length > 0) ? root.pending[0].action_id : ""
                                    if (bridge) bridge.rejectAction(actionId)
                                }
                            }
                        }

                        // Resolution Status Banner when 0 pending actions remain after approval/rejection
                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 28
                            visible: Boolean((!root.pending || root.pending.length === 0) && root.ep && (root.ep.status === "APPROVED" || root.ep.status === "REJECTED"))
                            color: root.ep && root.ep.status === "APPROVED" ? "#1B381E" : "#381B1B"
                            border.color: root.ep && root.ep.status === "APPROVED" ? KrTheme.accentGood : KrTheme.accentCrit
                            radius: 4

                            Text {
                                anchors.centerIn: parent
                                text: root.ep && root.ep.status === "APPROVED" ?
                                      "✓ CONTAINMENT ACTION APPROVED & EXECUTED" :
                                      "✗ CONTAINMENT ACTION REJECTED BY OPERATOR"
                                font.family: KrTheme.fontFamily
                                font.pixelSize: 11
                                font.bold: true
                                color: root.ep && root.ep.status === "APPROVED" ? KrTheme.accentGood : KrTheme.accentCrit
                            }
                        }
                    }
                }

                // Propagation Path Evidence (Shown when evidence path exists)
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    visible: Boolean(root.ep && root.ep.evidence_path && root.ep.evidence_path.length > 0)

                    Text {
                        text: "EVIDENCE PROPAGATION PATH"
                        font.family: KrTheme.fontFamily
                        font.pixelSize: 10
                        font.bold: true
                        color: KrTheme.textMuted
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 32
                        color: KrTheme.dark
                        border.color: KrTheme.hairline
                        radius: 4

                        RowLayout {
                            anchors.centerIn: parent
                            spacing: 6

                            Repeater {
                                model: root.ep && root.ep.evidence_path ? root.ep.evidence_path : []
                                RowLayout {
                                    spacing: 6
                                    Text {
                                        text: modelData
                                        font.family: KrTheme.fontFamily
                                        font.pixelSize: 11
                                        font.bold: index === parent.Repeater.count - 1
                                        color: index === parent.Repeater.count - 1 ? KrTheme.accentCrit : KrTheme.textPrimary
                                    }
                                    Text {
                                        text: "→"
                                        font.pixelSize: 11
                                        color: KrTheme.textMuted
                                        visible: index < parent.Repeater.count - 1
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
