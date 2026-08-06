import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Kr

Rectangle {
    id: root
    color: KrTheme.dark
    border.color: KrTheme.border
    border.width: 1
    clip: true

    property var nodePositions: ({})
    property real animProgress: 0.0

    NumberAnimation on animProgress {
        id: flowAnim
        from: 0.0
        to: 1.0
        duration: 1200
        loops: Animation.Infinite
        running: bridge ? bridge.systemStatus !== "HEALTHY" : false
    }

    Connections {
        target: bridge
        function onGraphUpdated() {
            root.updatePositions()
            canvas.requestPaint()
        }
        function onPropagationStep(src, dst, impact) {
            flowAnim.restart()
            canvas.requestPaint()
        }
    }

    Component.onCompleted: updatePositions()
    onWidthChanged: updatePositions()
    onHeightChanged: updatePositions()

    function updatePositions() {
        var nodes = bridge ? bridge.graphNodes : []
        var edges = bridge ? bridge.graphEdges : []
        var pos = {}

        if (!nodes || nodes.length === 0) return

        // Compute tier per node based on lineage
        var inDegree = {}
        for (var i = 0; i < nodes.length; i++) {
            inDegree[nodes[i].name] = 0
        }
        for (var j = 0; j < edges.length; j++) {
            var dstName = edges[j].dst_name
            inDegree[dstName] = (inDegree[dstName] || 0) + 1
        }

        var tier1 = [], tier2 = [], tier3 = []
        for (var k = 0; k < nodes.length; k++) {
            var n = nodes[k]
            if (n.name.indexOf("raw") !== -1 || inDegree[n.name] === 0) {
                tier1.push(n)
            } else if (n.name.indexOf("staging") !== -1) {
                tier2.push(n)
            } else {
                tier3.push(n)
            }
        }

        var w = Math.max(width, 700)
        var h = Math.max(height, 400)

        var t1X = w * 0.18
        var t2X = w * 0.50
        var t3X = w * 0.82

        function distributeY(list, xVal) {
            for (var idx = 0; idx < list.length; idx++) {
                var yVal = h * (idx + 1) / (list.length + 1)
                pos[list[idx].name] = { x: xVal, y: yVal, node: list[idx] }
            }
        }

        distributeY(tier1, t1X)
        distributeY(tier2, t2X)
        distributeY(tier3, t3X)

        nodePositions = pos
        canvas.requestPaint()
    }

    // Grid Background
    Canvas {
        id: bgGrid
        anchors.fill: parent
        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            ctx.strokeStyle = "#1A1A1A"
            ctx.lineWidth = 1

            var gridSize = 40
            for (var x = 0; x < width; x += gridSize) {
                ctx.beginPath()
                ctx.moveTo(x, 0)
                ctx.lineTo(x, height)
                ctx.stroke()
            }
            for (var y = 0; y < height; y += gridSize) {
                ctx.beginPath()
                ctx.moveTo(0, y)
                ctx.lineTo(width, y)
                ctx.stroke()
            }
        }
    }

    // Lineage Edges Canvas
    Canvas {
        id: canvas
        anchors.fill: parent

        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)

            var edges = bridge ? bridge.graphEdges : []
            var pos = root.nodePositions
            if (!edges || !pos) return

            for (var i = 0; i < edges.length; i++) {
                var e = edges[i]
                var srcPos = pos[e.src_name]
                var dstPos = pos[e.dst_name]

                if (!srcPos || !dstPos) continue

                var sx = srcPos.x + 80
                var sy = srcPos.y
                var dx = dstPos.x - 80
                var dy = dstPos.y

                var isHaltedEdge = e.dst_name.indexOf("billing") !== -1 && bridge && bridge.systemStatus === "CONTAINED"

                ctx.lineWidth = isHaltedEdge ? 3 : 2
                ctx.strokeStyle = isHaltedEdge ? KrTheme.accentCrit : KrTheme.accentPrimary

                ctx.beginPath()
                var cp1x = sx + (dx - sx) * 0.5
                var cp1y = sy
                var cp2x = sx + (dx - sx) * 0.5
                var cp2y = dy

                ctx.moveTo(sx, sy)
                ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, dx, dy)
                ctx.stroke()

                // Draw Column Annotation
                if (e.columns && e.columns.length > 0) {
                    var midX = (sx + dx) / 2
                    var midY = (sy + dy) / 2 - 8
                    ctx.font = "10px sans-serif"
                    ctx.fillStyle = isHaltedEdge ? KrTheme.accentCrit : KrTheme.textSecondary
                    ctx.fillText(e.columns.join(", "), midX - 25, midY)
                }

                // Draw Animated Signal Particle along edge
                if (flowAnim.running && isHaltedEdge) {
                    var t = root.animProgress
                    var px = (1-t)*(1-t)*(1-t)*sx + 3*(1-t)*(1-t)*t*cp1x + 3*(1-t)*t*t*cp2x + t*t*t*dx
                    var py = (1-t)*(1-t)*(1-t)*sy + 3*(1-t)*(1-t)*t*cp1y + 3*(1-t)*t*t*cp2y + t*t*t*dy

                    ctx.fillStyle = KrTheme.accentCrit
                    ctx.beginPath()
                    ctx.arc(px, py, 5, 0, 2 * Math.PI)
                    ctx.fill()
                }
            }
        }
    }

    // Graph Nodes
    Repeater {
        model: bridge ? bridge.graphNodes : []

        delegate: Item {
            id: nodeDelegate
            property var pos: root.nodePositions[modelData.name] || { x: 100, y: 100 }

            x: pos.x - 80
            y: pos.y - 36
            width: 160
            height: 72

            property bool isSelected: bridge && bridge.selectedAsset ? bridge.selectedAsset.urn === modelData.urn : false
            property bool isHalted: modelData.is_halted

            // Pulsing glow halo for halted nodes
            Rectangle {
                anchors.fill: parent
                anchors.margins: -6
                radius: 10
                color: "transparent"
                border.color: isHalted ? KrTheme.accentCrit : (isSelected ? KrTheme.accentPrimary : "transparent")
                border.width: 2
                opacity: isHalted ? glowAnim.opacityVal : (isSelected ? 0.8 : 0.0)

                Item {
                    id: glowAnim
                    property real opacityVal: 0.4
                    SequentialAnimation on opacityVal {
                        running: isHalted
                        loops: Animation.Infinite
                        PropertyAnimation { to: 1.0; duration: 500 }
                        PropertyAnimation { to: 0.3; duration: 500 }
                    }
                }
            }

            Rectangle {
                anchors.fill: parent
                radius: 6
                color: KrTheme.surface
                border.color: isHalted ? KrTheme.accentCrit : (isSelected ? KrTheme.accentPrimary : KrTheme.nodeBorder)
                border.width: isSelected ? 2 : 1

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: KrTheme.sp8
                    spacing: 4

                    RowLayout {
                        spacing: 6
                        Rectangle {
                            width: 8
                            height: 8
                            radius: 4
                            color: modelData.status === "HALTED" ? KrTheme.nodeHalted :
                                   (modelData.status === "OPERATIONAL" ? KrTheme.nodeOperational : KrTheme.nodeDegraded)
                        }

                        Text {
                            text: modelData.name
                            font.family: KrTheme.fontFamily
                            font.pixelSize: 13
                            font.bold: true
                            color: KrTheme.textLight
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                    }

                    RowLayout {
                        spacing: 4
                        Rectangle {
                            implicitHeight: 16
                            implicitWidth: domainTxt.implicitWidth + 8
                            color: KrTheme.surfaceRaised
                            radius: 3
                            Text {
                                id: domainTxt
                                anchors.centerIn: parent
                                text: modelData.domain
                                font.pixelSize: 9
                                color: KrTheme.textSecondary
                            }
                        }

                        Repeater {
                            model: modelData.tags.slice(0, 2)
                            Rectangle {
                                implicitHeight: 16
                                implicitWidth: tagTxt.implicitWidth + 8
                                color: modelData === "critical" ? "#3A1B1B" : KrTheme.surfaceRaised
                                radius: 3
                                border.color: modelData === "critical" ? KrTheme.accentCrit : KrTheme.border
                                border.width: 1
                                Text {
                                    id: tagTxt
                                    anchors.centerIn: parent
                                    text: modelData
                                    font.pixelSize: 9
                                    color: modelData === "critical" ? KrTheme.accentCrit : KrTheme.textMuted
                                }
                            }
                        }
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: bridge.selectAsset(modelData.urn)
                }
            }
        }
    }
}
