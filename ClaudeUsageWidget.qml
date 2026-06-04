import QtQuick
import Quickshell
import Quickshell.Io
import qs.Common
import qs.Services
import qs.Widgets
import qs.Modules.Plugins

PluginComponent {
    id: root

    layerNamespacePlugin: "claude-usage"

    // Resolve the bundled Python scripts from the plugin's own directory, so a
    // bare `git clone` into ~/.config/DankMaterialShell/plugins/ just works.
    // Falls back to ~/.local/share/claude-usage for the legacy manual install.
    readonly property string pluginDir: {
        var p = pluginService ? pluginService.getPluginPath(pluginId) : ""
        if (p && p.indexOf("file://") === 0)
            p = p.substring(7)
        return p
    }
    readonly property string baseDir: pluginDir !== ""
        ? (pluginDir + "/scripts")
        : (Quickshell.env("HOME") + "/.local/share/claude-usage")
    readonly property string scriptPath: baseDir + "/usage_data.py"
    readonly property string windowsScript: baseDir + "/fetch_windows.py"
    readonly property int refreshMs: 30000          // local token counts + burn rate
    readonly property int windowsRefreshMs: 600000  // plan windows: 10 min (avoid 429)

    // Local token-count fields (from logs).
    property string todayHuman: "…"
    property real todayCost: 0
    property var weekSeries: []
    property real weekTotal: 0
    property var weekLabels: []
    property var models: []

    // Burn-rate trend (from logs): recent vs typical tokens/min.
    property real rateRatio: -1      // -1 = unknown
    property int recentTpm: 0
    property int baselineTpm: 0

    // Plan rate-limit windows (live API, same as /usage).
    property bool windowsOk: false
    property bool windowsStale: false
    property string windowsError: ""
    property var windows: []          // [{label, pct, resets}]
    property var credits: null        // {used, limit, currency} or null
    property string plan: ""          // e.g. "Max 20×"

    // Convenience: the 5h-session window % for the bar pill.
    property int sessionPct: {
        for (var i = 0; i < windows.length; i++)
            if (windows[i].key === "five_hour")
                return windows[i].pct
        return -1
    }

    function refresh() {
        usageProc.running = true
        windowsProc.running = true
    }

    Component.onCompleted: refresh()

    Timer {
        interval: root.refreshMs
        running: true
        repeat: true
        onTriggered: usageProc.running = true
    }

    Timer {
        interval: root.windowsRefreshMs
        running: true
        repeat: true
        onTriggered: windowsProc.running = true
    }

    Process {
        id: usageProc
        command: ["python3", root.scriptPath, "--json"]
        stdout: StdioCollector {
            onStreamFinished: {
                const t = text.trim()
                if (!t)
                    return
                try {
                    const d = JSON.parse(t)
                    root.todayHuman = d.today.human
                    root.todayCost = d.today.cost
                    root.weekSeries = d.week.series
                    root.weekTotal = d.week.total
                    root.weekLabels = d.week.labels
                    root.models = d.models
                    if (d.rate) {
                        root.rateRatio = (d.rate.ratio === null) ? -1 : d.rate.ratio
                        root.recentTpm = d.rate.recent_tpm || 0
                        root.baselineTpm = d.rate.baseline_tpm || 0
                    }
                } catch (e) {
                    console.warn("claudeUsage: parse error", e)
                }
            }
        }
    }

    Process {
        id: windowsProc
        command: ["python3", root.windowsScript]
        stdout: StdioCollector {
            onStreamFinished: {
                const t = text.trim()
                if (!t)
                    return
                try {
                    const d = JSON.parse(t)
                    root.windowsOk = d.ok === true
                    root.windowsStale = d.stale === true
                    // Keep the prior good error cleared unless this is a hard
                    // failure (ok=false). Stale-but-ok keeps showing data.
                    root.windowsError = d.ok ? "" : (d.error || "")
                    if (d.ok) {
                        root.windows = d.windows || []
                        root.credits = d.credits || null
                        root.plan = d.plan || ""
                    }
                } catch (e) {
                    root.windowsOk = false
                    root.windowsError = "parse error"
                }
            }
        }
    }

    function human(n) {
        n = Number(n)
        if (Math.abs(n) >= 1e9)
            return (n / 1e9).toFixed(1) + "B"
        if (Math.abs(n) >= 1e6)
            return (n / 1e6).toFixed(1) + "M"
        if (Math.abs(n) >= 1e3)
            return (n / 1e3).toFixed(1) + "K"
        return String(Math.round(n))
    }

    // ---- Trend: spike arrow (burn rate now vs typical) ----
    // Balanced thresholds: ↗ 1.5×, ↑ 2×, ↑↑ 4×.
    function trendArrow() {
        const r = root.rateRatio
        if (r < 0 || root.recentTpm <= 0)
            return ""            // unknown or idle → no arrow
        if (r >= 4) return "⇈"
        if (r >= 2) return "↑"
        if (r >= 1.5) return "↗"
        return ""                // normal → no arrow (keeps pill quiet)
    }
    function trendColor() {
        const r = root.rateRatio
        if (r >= 4) return Theme.error
        if (r >= 2) return Theme.warning
        return Theme.surfaceVariantText
    }

    // ---- Pace: will current burn exhaust the 5h window before reset? ----
    // The 5h window is 300 min long. Its average fill rate so far is
    // pct / elapsed_min. If recent burn is R× that average, projected end-of-
    // window pct = pct + (avg_rate * R) * minutes_left. We approximate R with
    // the log-derived rateRatio (recent vs typical), clamped to >=1 so a quiet
    // baseline doesn't predict a drop.
    function fiveHourWindow() {
        for (var i = 0; i < root.windows.length; i++)
            if (root.windows[i].key === "five_hour")
                return root.windows[i]
        return null
    }
    function projectedPct() {
        const w = fiveHourWindow()
        if (!w || w.resets_in_min === null || w.resets_in_min === undefined)
            return -1
        const minsLeft = w.resets_in_min
        const elapsed = Math.max(1, 300 - minsLeft)   // 5h = 300 min
        const avgRatePerMin = w.pct / elapsed         // %/min so far
        const burnMult = root.rateRatio > 0 ? Math.max(1, root.rateRatio) : 1
        return w.pct + avgRatePerMin * burnMult * minsLeft
    }

    // Headline shown in the pill: session % when windows are available,
    // else today's token count. Color blends pace risk + spike.
    function pillText() {
        if (root.sessionPct >= 0)
            return root.sessionPct + "%"
        return root.todayHuman
    }
    function pillColor() {
        // Pace risk (projection) takes priority for color.
        const proj = projectedPct()
        if (proj >= 100) return Theme.error
        if (proj >= 80) return Theme.warning
        // Fall back to current fill level.
        if (root.sessionPct >= 90) return Theme.error
        if (root.sessionPct >= 70) return Theme.warning
        return Theme.surfaceText
    }

    // ---- bar pill (horizontal bar) ----
    horizontalBarPill: Component {
        Row {
            spacing: 4
            DankIcon {
                name: "smart_toy"
                size: Theme.iconSize - 8
                color: root.pillColor()
                anchors.verticalCenter: parent.verticalCenter
            }
            StyledText {
                text: root.pillText()
                font.pixelSize: Theme.fontSizeSmall
                font.weight: Font.Medium
                color: root.pillColor()
                anchors.verticalCenter: parent.verticalCenter
            }
            StyledText {
                text: root.trendArrow()
                visible: text !== ""
                font.pixelSize: Theme.fontSizeSmall + 1
                font.weight: Font.Bold
                color: root.trendColor()
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }

    // ---- bar pill (vertical bar) ----
    verticalBarPill: Component {
        Column {
            spacing: 1
            DankIcon {
                name: "smart_toy"
                size: Theme.iconSize - 8
                color: root.pillColor()
                anchors.horizontalCenter: parent.horizontalCenter
            }
            StyledText {
                text: root.pillText()
                font.pixelSize: Theme.fontSizeSmall
                color: root.pillColor()
                anchors.horizontalCenter: parent.horizontalCenter
            }
            StyledText {
                text: root.trendArrow()
                visible: text !== ""
                font.pixelSize: Theme.fontSizeSmall
                font.weight: Font.Bold
                color: root.trendColor()
                anchors.horizontalCenter: parent.horizontalCenter
            }
        }
    }

    // ---- popout ----
    popoutWidth: 380
    popoutHeight: 640

    popoutContent: Component {
        PopoutComponent {
            id: pop
            headerText: root.plan !== "" ? ("Claude Usage · " + root.plan) : "Claude Usage"
            detailsText: "Token counts are usage, not billing"
            showCloseButton: true

            Column {
                width: parent.width
                spacing: Theme.spacingM

                // ---- Plan limit windows (same data as /usage) ----
                StyledText {
                    text: root.windowsStale ? "Plan limits  (cached)" : "Plan limits"
                    font.pixelSize: Theme.fontSizeSmall
                    font.weight: Font.Bold
                    color: root.windowsStale ? Theme.warning : Theme.surfaceVariantText
                }
                StyledText {
                    visible: !root.windowsOk
                    width: parent.width
                    text: root.windowsError !== ""
                          ? ("unavailable — " + root.windowsError)
                          : "loading…"
                    font.pixelSize: Theme.fontSizeSmall
                    color: Theme.surfaceVariantText
                    wrapMode: Text.WordWrap
                }
                Repeater {
                    model: root.windowsOk ? root.windows : []
                    Column {
                        width: parent.width
                        spacing: 2
                        Row {
                            width: parent.width
                            StyledText {
                                text: modelData.label
                                font.pixelSize: Theme.fontSizeSmall
                                color: Theme.surfaceText
                                width: parent.width * 0.5
                            }
                            StyledText {
                                text: modelData.pct + "%"
                                font.pixelSize: Theme.fontSizeSmall
                                font.weight: Font.Medium
                                color: modelData.pct >= 90 ? Theme.error
                                       : modelData.pct >= 70 ? Theme.warning
                                       : Theme.surfaceText
                                horizontalAlignment: Text.AlignRight
                                width: parent.width * 0.2
                            }
                            StyledText {
                                text: modelData.resets ? ("↺ " + modelData.resets) : ""
                                font.pixelSize: Theme.fontSizeSmall - 1
                                color: Theme.surfaceVariantText
                                horizontalAlignment: Text.AlignRight
                                width: parent.width * 0.3
                            }
                        }
                        // progress bar
                        Rectangle {
                            width: parent.width
                            height: 5
                            radius: 3
                            color: Theme.surfaceContainerHighest
                            Rectangle {
                                width: parent.width * Math.min(1, modelData.pct / 100)
                                height: parent.height
                                radius: 3
                                color: modelData.pct >= 90 ? Theme.error
                                       : modelData.pct >= 70 ? Theme.warning
                                       : Theme.primary
                            }
                        }
                    }
                }
                StyledText {
                    visible: root.windowsOk && root.credits !== null
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: root.credits
                          ? ("Usage credits: $" + root.credits.used.toFixed(2)
                             + " of $" + root.credits.limit + " monthly cap"
                             + (root.credits.used <= 0 ? " used" : ""))
                          : ""
                    font.pixelSize: Theme.fontSizeSmall - 1
                    color: Theme.surfaceVariantText
                }

                // ---- Burn rate / trend ----
                Row {
                    width: parent.width
                    spacing: Theme.spacingS
                    visible: root.recentTpm > 0
                    StyledText {
                        text: "Burn rate " + root.trendArrow()
                        font.pixelSize: Theme.fontSizeSmall
                        font.weight: Font.Medium
                        color: root.trendColor()
                    }
                    StyledText {
                        text: {
                            var now = root.human(root.recentTpm) + "/min"
                            if (root.baselineTpm > 0 && root.rateRatio > 0)
                                return now + " · " + root.rateRatio.toFixed(1)
                                       + "× your typical (" + root.human(root.baselineTpm) + "/min)"
                            return now
                        }
                        font.pixelSize: Theme.fontSizeSmall - 1
                        color: Theme.surfaceVariantText
                        elide: Text.ElideRight
                        width: parent.width - 120
                    }
                }
                StyledText {
                    visible: root.projectedPct() >= 80
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: root.projectedPct() >= 100
                          ? "⚠ At this pace you'll exhaust the 5h window before it resets."
                          : "At this pace the 5h window reaches ~"
                            + Math.round(root.projectedPct()) + "% by reset."
                    font.pixelSize: Theme.fontSizeSmall - 1
                    color: root.projectedPct() >= 100 ? Theme.error : Theme.warning
                }

                Rectangle {
                    width: parent.width
                    height: 1
                    color: Theme.outlineMedium
                }

                // ---- Local token counts (from logs) ----
                // Big today number
                Row {
                    spacing: Theme.spacingS
                    StyledText {
                        text: root.todayHuman
                        font.pixelSize: Theme.fontSizeXLarge * 1.4
                        font.weight: Font.Bold
                        color: Theme.surfaceText
                        anchors.bottom: parent.bottom
                    }
                    StyledText {
                        text: "tokens today"
                        font.pixelSize: Theme.fontSizeSmall
                        color: Theme.surfaceVariantText
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: 4
                    }
                }

                // 7-day bar chart
                StyledText {
                    text: "Last 7 days · " + root.human(root.weekTotal) + " total"
                    font.pixelSize: Theme.fontSizeSmall
                    color: Theme.surfaceVariantText
                }
                Row {
                    width: parent.width
                    height: 70
                    spacing: 6
                    Repeater {
                        model: root.weekSeries
                        Column {
                            width: (pop.width - Theme.spacingL * 2 - 6 * 6) / 7
                            height: 70
                            spacing: 2
                            property real maxV: {
                                var m = 1
                                for (var i = 0; i < root.weekSeries.length; i++)
                                    m = Math.max(m, root.weekSeries[i])
                                return m
                            }
                            Item {
                                width: parent.width
                                height: 52
                                StyledRect {
                                    width: parent.width
                                    height: Math.max(2, modelData / parent.parent.maxV * 52)
                                    anchors.bottom: parent.bottom
                                    radius: Theme.cornerRadius
                                    color: index === root.weekSeries.length - 1
                                           ? Theme.primary : Theme.surfaceContainerHighest
                                }
                            }
                            StyledText {
                                text: root.weekLabels.length > index
                                      ? root.weekLabels[index].slice(8) : ""
                                font.pixelSize: Theme.fontSizeSmall - 2
                                color: Theme.surfaceVariantText
                                anchors.horizontalCenter: parent.horizontalCenter
                            }
                        }
                    }
                }

                // Per-model breakdown
                Rectangle {
                    width: parent.width
                    height: 1
                    color: Theme.outlineMedium
                }
                Repeater {
                    model: root.models
                    Row {
                        width: parent.width
                        StyledText {
                            text: modelData.name
                            font.pixelSize: Theme.fontSizeMedium
                            color: Theme.surfaceText
                            width: parent.width * 0.5
                        }
                        StyledText {
                            text: modelData.human + " tok"
                            font.pixelSize: Theme.fontSizeMedium
                            color: Theme.surfaceText
                            horizontalAlignment: Text.AlignRight
                            width: parent.width * 0.5
                        }
                    }
                }

                StyledText {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: "Real plan usage = the limit windows above. Token "
                          + "counts are informational; you're billed a flat "
                          + "monthly subscription, not per token."
                    font.pixelSize: Theme.fontSizeSmall - 2
                    color: Theme.surfaceVariantText
                    opacity: 0.7
                }
            }

            Component.onCompleted: root.refresh()
        }
    }
}
