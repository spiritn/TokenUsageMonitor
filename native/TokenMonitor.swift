import AppKit
import SwiftUI
import Combine

func readMonitorConfig() -> [String:Any] {
    let root = ProcessInfo.processInfo.environment["TOKEN_MONITOR_DATA_DIR"] ?? NSHomeDirectory()+"/Library/Application Support/TokenMonitor"
    guard let bytes=try? Data(contentsOf:URL(fileURLWithPath:root+"/config.json")),
          let settings=try? JSONSerialization.jsonObject(with:bytes) as? [String:Any] else { return [:] }
    return settings
}
let monitorConfig=readMonitorConfig()
let monitorTimeZone=ProcessInfo.processInfo.environment["TOKEN_MONITOR_TIMEZONE"] ?? (monitorConfig["timezone"] as? String ?? "Asia/Shanghai")

// Swift 5.3 兼容：AppKit 管菜单栏和窗口，SwiftUI 负责内容。
let canvasColor = Color(red: 0.063, green: 0.086, blue: 0.145)
let surface = Color(red: 0.098, green: 0.133, blue: 0.208)
let accent = Color(red: 0.612, green: 0.565, blue: 1.0)
let muted = Color(red: 0.67, green: 0.72, blue: 0.80)
func value(_ d: [String: Any], _ k: String) -> Double { (d[k] as? NSNumber)?.doubleValue ?? 0 }
func format(_ n: Double) -> String {
    if n >= 1e9 { return String(format: "%.2fB", n/1e9) }
    if n >= 1e6 { return String(format: "%.2fM", n/1e6) }
    if n >= 1000 { return String(format: "%.1fk", n/1000) }
    return String(format: "%.0f", n)
}
func stamp(_ n: Double) -> String {
    if n <= 0 { return "暂无记录" }
    let f = DateFormatter(); f.dateFormat = "MM-dd HH:mm"; f.timeZone = TimeZone(identifier: monitorTimeZone)
    return f.string(from: Date(timeIntervalSince1970: n/1000))
}

final class Monitor: ObservableObject {
    @Published var data: [String: Any] = [:]
    @Published var period = "day"
    @Published var monthStart = ""
    @Published var monthEnd = ""
    @Published var tool = ""
    @Published var error = ""
    @Published var updated = "连接中"
    var busy = false
    var queued = false
    var timer: Timer?
    var onTitle: ((String) -> Void)?
    let api = ProcessInfo.processInfo.environment["TOKEN_MONITOR_URL"] ?? ("http://127.0.0.1:"+String((monitorConfig["port"] as? Int) ?? 8787))
    func start() {
        if let at=CommandLine.arguments.firstIndex(of:"--period"),CommandLine.arguments.count>at+1 {
            let selected=CommandLine.arguments[at+1]
            if ["day","week","month","last_month"].contains(selected) { period=selected }
        }
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.refresh() }
    }
    func queryURL() -> URL {
        var url = URLComponents(string: api + "/api/v2/overview")!
        url.queryItems = [URLQueryItem(name: "period", value: period)]
        if !tool.isEmpty { url.queryItems!.append(URLQueryItem(name: "tool", value: tool)) }
        if !monthStart.isEmpty {
            url.queryItems!.append(URLQueryItem(name: "start", value: monthStart))
            url.queryItems!.append(URLQueryItem(name: "end", value: monthEnd))
        }
        return url.url!
    }
    func shiftMonth(_ step: Int) {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: monitorTimeZone)!
        let formatter = DateFormatter(); formatter.dateFormat = "yyyy-MM-dd"; formatter.timeZone = calendar.timeZone
        let anchor = formatter.date(from: monthStart) ?? Date()
        let offset = step + (monthStart.isEmpty && period == "last_month" ? -1 : 0)
        let start = calendar.date(from: calendar.dateComponents([.year,.month], from: anchor))!
        let first = calendar.date(byAdding: .month, value: offset, to: start)!
        let next = calendar.date(byAdding: .month, value: 1, to: first)!
        period = "month"
        monthStart = formatter.string(from: first)
        monthEnd = formatter.string(from: calendar.date(byAdding: .day, value: -1, to: next)!)
        refresh()
    }
    var rangeLabel: String {
        let range = data["range"] as? [String:Any] ?? [:]
        guard value(range,"start") > 0 else { return "加载中" }
        let formatter = DateFormatter(); formatter.dateFormat = "yyyy-MM-dd"; formatter.timeZone = TimeZone(identifier: monitorTimeZone)
        return formatter.string(from: Date(timeIntervalSince1970: value(range,"start")/1000)) + " — " + formatter.string(from: Date(timeIntervalSince1970: (value(range,"end")-1)/1000))
    }
    func refresh() {
        if busy { queued = true; return }; busy = true; queued = false
        let requested = queryURL()
        var request = URLRequest(url: requested); request.timeoutInterval = 8
        URLSession.shared.dataTask(with: request) { [weak self] bytes, response, failure in
            DispatchQueue.main.async {
                guard let self = self else { return }; self.busy = false
                if requested != self.queryURL() { self.refresh(); return }
                defer { if self.queued { self.refresh() } }
                guard failure == nil, (response as? HTTPURLResponse)?.statusCode == 200,
                      let bytes = bytes, let decoded = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any] else {
                    self.error = "无法连接采集服务，保留上次范围的数据"; self.updated = "连接异常"; self.onTitle?("⬢ ⏸"); return
                }
                self.data = decoded; self.error = ""
                self.updated = "更新 " + stamp(Date().timeIntervalSince1970*1000)
                let total = decoded["total"] as? [String: Any] ?? [:]
                self.onTitle?("⬢ " + format(value(total,"tokens")))
            }
        }.resume()
    }
    var total: [String: Any] { data["total"] as? [String: Any] ?? [:] }
    var sources: [[String: Any]] { data["sources"] as? [[String: Any]] ?? [] }
}

struct Panel: View {
    @ObservedObject var monitor: Monitor
    var detail: () -> Void
    var compact: Bool = true
    func statistic(_ label: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(text).font(.system(size: 26, weight: .semibold, design: .rounded)).foregroundColor(.white)
            Text(label).font(.system(size: 11)).foregroundColor(muted)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    func heading(_ title: String) -> some View {
        Text(title).font(.system(size: 13, weight: .semibold)).foregroundColor(.white)
    }
    func row(_ title: String, _ subtitle: String, _ right: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.system(size: 13, weight: .medium)).lineLimit(2)
                if !subtitle.isEmpty { Text(subtitle).font(.system(size: 10)).foregroundColor(muted).fixedSize(horizontal: false, vertical: true) }
            }
            Spacer(minLength: 8)
            Text(right).font(.system(size: 13, weight: .medium, design: .monospaced)).foregroundColor(accent).multilineTextAlignment(.trailing)
        }.padding(.vertical, 7)
    }
    func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 7, content: content).padding(15).frame(maxWidth: .infinity, alignment: .leading).background(surface).cornerRadius(11)
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                headerView
                filterView
                metricView
                chartView
                sourceView
                detailsView
                card { quotaView }
                footerView
            }.padding(20)
        }.background(canvasColor).foregroundColor(.white).colorScheme(.dark)
    }
    var headerView: some View {
        VStack(alignment: .leading, spacing: 12) {
                HStack {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("Token Monitor").font(.system(size: 23, weight: .semibold))
                        Text((monitor.data["demo"] as? Bool) == true ? "演示数据 · 不代表真实消费" : "本地计量").font(.system(size: 11)).foregroundColor(muted)
                    }
                    Spacer()
                    Text(monitor.updated).font(.system(size: 10)).foregroundColor(muted)
                    Button(action: { monitor.refresh() }) { Text("↻") }.buttonStyle(BorderlessButtonStyle())
                    if compact { Button("详情", action: detail).buttonStyle(BorderlessButtonStyle()) }
                }
        }
    }
    var filterView: some View {
        VStack(alignment: .leading, spacing: 12) {
                Picker("周期", selection: Binding(get: { monitor.period }, set: { monitor.period = $0; monitor.monthStart = ""; monitor.monthEnd = ""; monitor.refresh() })) {
                    Text("今日").tag("day"); Text("本周").tag("week"); Text(monitor.monthStart.isEmpty ? "本月" : "历史月份").tag("month"); Text("上月").tag("last_month")
                }.pickerStyle(SegmentedPickerStyle())
                HStack {
                    Picker("工具", selection: Binding(get: { monitor.tool }, set: { monitor.tool = $0; monitor.refresh() })) {
                        Text("全部工具").tag("")
                        ForEach(monitor.sources.indices, id: \.self) { i in
                            Text(monitor.sources[i]["name"] as? String ?? "").tag(monitor.sources[i]["tool"] as? String ?? "")
                        }
                    }
                }.font(.system(size: 11))
                HStack {
                    Button("← 上一月") { monitor.shiftMonth(-1) }.buttonStyle(BorderlessButtonStyle())
                    Spacer()
                    Button("下一月 →") { monitor.shiftMonth(1) }.buttonStyle(BorderlessButtonStyle())
                }.font(.system(size:11))
                Text(monitor.rangeLabel).font(.system(size:11)).foregroundColor(muted)
        }
    }
    var metricView: some View {
        VStack(alignment: .leading, spacing: 12) {
                if !monitor.error.isEmpty { Text(monitor.error).foregroundColor(.orange).font(.system(size: 12)) }
                HStack {
                    statistic("输入 Token",format(value(monitor.total,"input_tokens")))
                    statistic("输出 Token",format(value(monitor.total,"output_tokens")))
                    let rate = value(monitor.total,"cache_hit_rate")
                    statistic("缓存命中率", (monitor.total["cache_hit_rate"] as? NSNumber) == nil ? "—" : String(format: "%.2f%%", rate * 100))
                }.padding(.vertical, 6)
                if let coverage=monitor.total["cache_coverage"] as? NSNumber, coverage.doubleValue < 1 {
                    Text(String(format:"命中率仅覆盖 %.1f%% 的输入 Token",coverage.doubleValue*100)).font(.system(size:10)).foregroundColor(muted)
                }
        }
    }
    var chartView: some View {
        VStack(alignment: .leading, spacing: 12) {
                card {
                    heading("Token 趋势")
                    Bars(points: monitor.data["trend"] as? [[String: Any]] ?? []).frame(height: 110)
                    Text("灰线表示当天未采到用量；停留可查看日期和数值").font(.system(size:10)).foregroundColor(muted)
                    if !compact {
                        Divider()
                        HStack {
                            statistic("输入（含缓存）",format(value(monitor.total,"input_tokens")))
                            statistic("输出",format(value(monitor.total,"output_tokens")))
                        }
                        Text("缓存命中 " + format(value(monitor.total,"cache_read")) + " · 已包含在输入中").font(.system(size: 10)).foregroundColor(muted)
                    }
                }
        }
    }
    var sourceView: some View {
        VStack(alignment: .leading, spacing: 12) {
                card {
                    heading("工具与数据源")
                    ForEach(monitor.sources.indices, id: \.self) { i in sourceRow(monitor.sources[i]) }
                }
        }
    }
    var detailsView: some View {
        VStack(alignment: .leading, spacing: 12) {
                if !compact {
                    card {
                        heading("模型")
                        let models = monitor.data["models"] as? [String: [String: Any]] ?? [:]
                        ForEach(models.keys.sorted(), id: \.self) { k in row(k,"",format(value(models[k] ?? [:],"tokens"))) }
                    }
                    card {
                        heading("最近用量")
                        let rows = monitor.data["recent"] as? [[String: Any]] ?? []
                        ForEach(rows.indices, id: \.self) { i in
                            row((rows[i]["tool"] as? String ?? "") + " · " + (rows[i]["model"] as? String ?? ""),stamp(value(rows[i],"ts")),format(value(rows[i],"input_tokens")+value(rows[i],"output_tokens")))
                        }
                    }
                }
        }
    }
    var footerView: some View {
        VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Spacer()
                    Button("历史查询") { NSWorkspace.shared.open(URL(string: monitor.api)!) }.buttonStyle(BorderlessButtonStyle()).font(.system(size: 11))
                }
                Text("仅统计可观测来源。金额仅在配置价格表后估算，积分与账单分列。千问仅覆盖 Agent 模式。").font(.system(size: 10)).foregroundColor(muted).fixedSize(horizontal: false, vertical: true)
        }
    }
    func sourceRow(_ s: [String: Any]) -> some View {
        let key=s["tool"] as? String ?? ""
        let tools=monitor.data["tools"] as? [String: [String: Any]] ?? [:]
        let usage=tools[key]
        let states=["ready":"扫描正常","error":"读取异常","stale":"数据过期","no_source":"暂无日志","unsupported":"暂不支持","starting":"初始化"]
        let state=states[s["status"] as? String ?? ""] ?? "未知"
        var right = usage == nil ? "—" : format(value(usage!,"tokens"))
        if let credit=usage?["credits"] as? NSNumber { right += "\n" + String(format:"%.2f 积分",credit.doubleValue) }
        let costs=usage?["costs"] as? [String:Double] ?? [:]
        if let cost=costs["CNY"] { right += "\n约 ¥"+String(format:"%.2f",cost)+(value(usage!,"unknown_cost_calls")>0 ? "（部分）":"") }
        else if usage != nil { right += "\n费用未知" }
        let warning = s["coverage_warning"] as? String ?? ""
        let detail = s["detail"] as? String ?? ""
        var subtitle = state + " · 最近扫描 " + stamp(value(s,"last_scan"))
        subtitle += "\n最近有效用量 " + stamp(value(s,"last_event"))
        if !detail.isEmpty { subtitle += "\n"+detail }
        if !warning.isEmpty { subtitle += "\n"+warning }
        return row(s["name"] as? String ?? key,subtitle,right)
    }
    func quotaItem(_ quota: [String:Any]) -> some View {
        let data=quota["data"] as? [String:Any] ?? [:]
        return VStack(alignment: .leading, spacing: 7) {
            ForEach(["primary","secondary"],id: \.self) { key in
                self.quotaWindow(data[key] as? [String:Any] ?? [:], key)
            }
            Text("更新 "+stamp(value(quota,"ts"))+" · 当前快照，不随历史日期或工具变化").font(.system(size:10)).foregroundColor(muted)
        }
    }
    func quotaWindow(_ window: [String:Any], _ key: String) -> some View {
        row("Codex · "+(key=="primary" ? "短周期":"长周期"),"重置 "+stamp(value(window,"resets_at")*1000),window.isEmpty ? "—" : format(value(window,"used_percent"))+"% 已用")
    }
    var quotaView: some View {
        VStack(alignment: .leading, spacing: 7) {
            heading("当前账户额度快照")
            let quotas=monitor.data["quota"] as? [[String:Any]] ?? []
            if quotas.isEmpty { Text("暂无额度快照").font(.system(size:12)).foregroundColor(muted) }
            ForEach(quotas.indices,id: \.self) { i in quotaItem(quotas[i]) }

        }
    }
}

struct HoverHint: NSViewRepresentable {
    let text: String
    func makeNSView(context: Context) -> NSView { let view=NSView(); view.toolTip=text; return view }
    func updateNSView(_ view: NSView, context: Context) { view.toolTip=text }
}

struct Bars: View {
    var points: [[String: Any]]
    func column(_ i: Int, _ height: CGFloat) -> some View {
        let maximum=max(1,points.map { value($0,"tokens") }.max() ?? 1)
        let amount=value(points[i],"tokens")
        let date=points[i]["date"] as? String ?? ""
        let day=Int(date.suffix(2)) ?? 0
        let visible=points.count<=7 || i==0 || i==points.count-1 || day%5==0
        let label=points.count<=7 ? String(date.suffix(5)).replacingOccurrences(of:"-",with:"/") : String(day)
        let tooltip = date+" · "+format(amount)+" token"+(amount==0 ? "（未采到用量）":"")
        return VStack(spacing:6) {
            Spacer(minLength:0)
            RoundedRectangle(cornerRadius:2).fill(amount>0 ? accent : muted.opacity(0.55))
                .frame(height:max(3,CGFloat(amount/maximum)*max(1,height-24)))
            Text(visible ? label : " ").font(.system(size:8)).foregroundColor(muted).lineLimit(1).minimumScaleFactor(0.6)
        }.frame(maxWidth:.infinity).background(HoverHint(text:tooltip))
    }
    var body: some View {
        GeometryReader { g in
            HStack(alignment:.bottom,spacing:3) {
                ForEach(points.indices,id:\.self) { i in self.column(i,g.size.height) }
            }.frame(maxWidth:.infinity,maxHeight:.infinity,alignment:.bottom)
        }
    }
}

final class Delegate: NSObject, NSApplicationDelegate {
    let monitor=Monitor()
    var item: NSStatusItem!
    var popover=NSPopover()
    var window: NSWindow?
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        item=NSStatusBar.system.statusItem(withLength:NSStatusItem.variableLength)
        item.button?.title="⬢ …"; item.button?.target=self; item.button?.action=#selector(toggle)
        item.button?.sendAction(on:[.leftMouseUp,.rightMouseUp])
        let view=Panel(monitor:monitor,detail:{ [weak self] in self?.showDetails() })
        popover.contentViewController=NSHostingController(rootView:view)
        popover.contentSize=NSSize(width:470,height:720);popover.behavior = .transient
        monitor.onTitle={ [weak self] title in self?.item.button?.title=title };monitor.start()
        if CommandLine.arguments.contains("--show") { showDetails() }
        if let at=CommandLine.arguments.firstIndex(of:"--snapshot"),CommandLine.arguments.count>at+1 {
            let path=CommandLine.arguments[at+1]
            showDetails()
            DispatchQueue.main.asyncAfter(deadline:.now()+4) { [weak self] in
                if let view=self?.window?.contentView,let bitmap=view.bitmapImageRepForCachingDisplay(in:view.bounds) {
                    view.cacheDisplay(in:view.bounds,to:bitmap)
                    if let png=bitmap.representation(using:.png,properties:[:]) { try? png.write(to:URL(fileURLWithPath:path)) }
                };NSApp.terminate(nil)
            }
        }
    }
    @objc func toggle() {
        if NSApp.currentEvent?.type == .rightMouseUp {
            let menu=NSMenu()
            let detail=NSMenuItem(title:"打开详情",action:#selector(showDetails),keyEquivalent:"");detail.target=self;menu.addItem(detail)
            let quitItem=NSMenuItem(title:"退出面板",action:#selector(quit),keyEquivalent:"");quitItem.target=self;menu.addItem(quitItem)
            item.menu=menu;item.button?.performClick(nil);item.menu=nil
        } else if popover.isShown { popover.performClose(nil) }
        else if let button=item.button { monitor.refresh();popover.show(relativeTo:button.bounds,of:button,preferredEdge:.minY) }
    }
    @objc func showDetails() {
        if window == nil {
            let w=NSWindow(contentRect:NSRect(x:0,y:0,width:760,height:820),styleMask:[.titled,.closable,.miniaturizable,.resizable],backing:.buffered,defer:false)
            w.title="Token Monitor";w.isReleasedWhenClosed=false
            w.contentView=NSHostingView(rootView:Panel(monitor:monitor,detail:{},compact:false));w.center();window=w
        }
        window?.makeKeyAndOrderFront(nil);NSApp.activate(ignoringOtherApps:true)
    }
    @objc func quit() { NSApp.terminate(nil) }
}
let delegate=Delegate()
NSApplication.shared.delegate=delegate
NSApplication.shared.run()
