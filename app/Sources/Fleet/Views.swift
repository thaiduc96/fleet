import SwiftUI
import AppKit
import CoreImage.CIFilterBuiltins
import FleetCore

// MARK: - Onboarding

struct OnboardingView: View {
    let backend: Backend
    var onDone: () -> Void

    @State private var mode = 0   // 0 = create, 1 = join
    @State private var inviteText = ""
    @State private var machine = Env.configuredMachine ?? Env.suggestedMachineID
    @State private var label = Host.current().localizedName ?? "Mac"
    @State private var keepAwake = true
    @State private var busy = false
    @State private var status = ""
    @State private var error: String?
    @State var tailscale = Tailscale.Status(running: false, dnsName: nil)
    var checkTailscaleOnAppear = true

    private var machineValid: Bool {
        machine.range(of: #"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$"#, options: .regularExpression) != nil
    }

    private var invite: InviteCode? { try? InviteCode.decode(inviteText) }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Chào mừng đến với Fleet").font(.title2).bold()
            Text("Xem và điều khiển các session Claude Code trên mọi máy Mac của bạn.")
                .foregroundStyle(.secondary)

            GroupBox("1. Tailscale") {
                HStack {
                    Image(systemName: tailscale.running ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .foregroundStyle(tailscale.running ? .green : .orange)
                    if tailscale.running, let dns = tailscale.dnsName {
                        Text("Đang chạy · \(dns)").textSelection(.enabled)
                    } else if Tailscale.installed {
                        Text("Tailscale chưa chạy. Mở app Tailscale và đăng nhập.")
                    } else {
                        Text("Chưa cài Tailscale (tailscale.com/download).")
                    }
                    Spacer()
                    if !tailscale.running {
                        Button("Mở Tailscale") { NSWorkspace.shared.open(URL(fileURLWithPath: "/Applications/Tailscale.app")) }
                    }
                    Button("Kiểm tra lại") { Task { tailscale = await Shell.background { Tailscale.status() } } }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }

            GroupBox("2. Fleet") {
                VStack(alignment: .leading, spacing: 8) {
                    Picker("", selection: $mode) {
                        Text("Tạo Fleet mới (máy đầu tiên)").tag(0)
                        Text("Tham gia Fleet có sẵn (dán mã mời)").tag(1)
                    }.pickerStyle(.radioGroup).labelsHidden()
                    if mode == 1 {
                        TextField("fleet1.…", text: $inviteText, axis: .vertical)
                            .lineLimit(2...4).font(.system(.body, design: .monospaced))
                        Text(inviteText.isEmpty ? "Trên máy đang chạy Fleet: menu Fleet → Mời máy mới… → Sao chép mã."
                                                : (invite == nil ? "Mã mời không hợp lệ." : "✓ Mời từ \(invite!.url)"))
                            .font(.caption).foregroundStyle(invite == nil && !inviteText.isEmpty ? .red : .secondary)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }

            GroupBox("3. Máy này") {
                Grid(alignment: .leading, horizontalSpacing: 8, verticalSpacing: 8) {
                    GridRow { Text("Tên (id)"); TextField("home", text: $machine) }
                    GridRow { Text("Nhãn"); TextField("🏠 Nhà", text: $label) }
                    GridRow { Text(""); Toggle("Giữ máy thức khi cắm sạc", isOn: $keepAwake) }
                }
                if !machineValid {
                    Text("Tên chỉ gồm chữ, số, '-' hoặc '_' (tối đa 40 ký tự).").font(.caption).foregroundStyle(.red)
                }
            }

            if let error {
                Text(error).foregroundStyle(.red).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                if busy { ProgressView().controlSize(.small); Text(status).foregroundStyle(.secondary) }
                Spacer()
                Button(mode == 0 ? "Tạo Fleet" : "Tham gia") { start() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(busy || !tailscale.running || !machineValid || label.isEmpty || (mode == 1 && invite == nil))
            }
        }
        .padding(20)
        .frame(width: 560)
        .task { if checkTailscaleOnAppear { tailscale = await Shell.background { Tailscale.status() } } }
    }

    private func start() {
        busy = true
        error = nil
        let choice: Setup.Choice = mode == 0 ? .create : .join(invite!)
        Task {
            do {
                try await Setup.run(choice: choice, machine: machine, label: label, keepAwakeOnAC: keepAwake,
                                    backend: backend) { msg in Task { @MainActor in status = msg } }
                await MainActor.run { busy = false; onDone() }
            } catch {
                await MainActor.run { busy = false; self.error = error.localizedDescription }
            }
        }
    }
}

// MARK: - Invite

struct InviteView: View {
    let code: InviteCode?
    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Mời máy mới").font(.title2).bold()
            if let code {
                Text("Trên máy mới: cài Fleet, chọn \"Tham gia Fleet có sẵn\" rồi dán mã này.")
                    .foregroundStyle(.secondary)
                HStack(alignment: .top, spacing: 16) {
                    if let qr = qrImage(code.encoded()) {
                        Image(nsImage: qr).interpolation(.none).resizable().frame(width: 180, height: 180)
                    }
                    VStack(alignment: .leading, spacing: 10) {
                        Text(code.encoded()).font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled).lineLimit(6)
                        Button(copied ? "Đã sao chép ✓" : "Sao chép mã") {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(code.encoded(), forType: .string)
                            copied = true
                        }
                    }
                }
                Label("Mã mời chứa token của Fleet. Chỉ dán vào máy của chính bạn.", systemImage: "lock.shield")
                    .font(.callout).foregroundStyle(.orange)
            } else {
                Text("Chưa tạo được mã: máy này cần Tailscale đang chạy và 'tailscale serve' đã bật (Cài đặt).")
                    .foregroundStyle(.red)
            }
        }
        .padding(20)
        .frame(width: 520)
    }

    private func qrImage(_ text: String) -> NSImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage?.transformed(by: CGAffineTransform(scaleX: 6, y: 6)) else { return nil }
        let rep = NSCIImageRep(ciImage: output)
        let image = NSImage(size: rep.size)
        image.addRepresentation(rep)
        return image
    }
}

// MARK: - Settings

@MainActor
final class SettingsModel: ObservableObject {
    @Published var label = ""
    @Published var awake = "off"
    @Published var url: String?
    @Published var machines: [(id: String, label: String, online: Bool, version: String?)] = []
    @Published var message: String?
    @Published var autoUpdate = UserDefaults.standard.object(forKey: "autoUpdate") as? Bool ?? true {
        didSet { UserDefaults.standard.set(autoUpdate, forKey: "autoUpdate") }
    }
    var selfMachine = ""

    private var client: FleetClient { FleetClient(token: TokenStore.read()) }

    func load() async {
        do {
            let local = try await client.local()
            label = local["label"] as? String ?? ""
            url = local["url"] as? String
            selfMachine = local["machine"] as? String ?? ""
            awake = ((local["health"] as? [String: Any])?["awake"] as? [String: Any])?["mode"] as? String ?? "off"
            let fleet = try await client.fleet()
            machines = fleet.machines.filter { !$0.isSelf }.map { ($0.machine, $0.label, $0.online, $0.appVersion) }
        } catch { message = error.localizedDescription }
    }

    func saveLabel() async {
        do { try await client.settings(label: label); message = "Đã lưu nhãn" } catch { message = error.localizedDescription }
    }

    func saveAwake() async {
        do { try await client.setAwake(awake) } catch { message = error.localizedDescription }
    }

    func remove(_ id: String) async {
        let alert = NSAlert()
        alert.messageText = "Xoá máy \(id) khỏi Fleet?"
        alert.informativeText = "Máy này sẽ bị xoá khỏi danh sách trên tất cả các máy. Muốn thêm lại thì dùng mã mời."
        alert.addButton(withTitle: "Xoá")
        alert.addButton(withTitle: "Huỷ")
        alert.buttons.first?.hasDestructiveAction = true
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        do { try await client.removePeerEverywhere(id); message = "Đã xoá \(id) trên tất cả các máy"; await load() }
        catch { message = error.localizedDescription }
    }
}

struct SettingsView: View {
    @StateObject var model: SettingsModel
    var onInvite: () -> Void
    var onCheckUpdate: () -> Void
    var loadOnAppear = true

    var body: some View {
        Form {
            Section("Máy này") {
                HStack {
                    TextField("Nhãn", text: $model.label)
                    Button("Lưu") { Task { await model.saveLabel() } }
                }
                LabeledContent("Tên (id)", value: model.selfMachine)
                LabeledContent("Địa chỉ tailnet", value: model.url ?? "chưa có (cần tailscale serve)")
                Picker("Giữ máy thức", selection: $model.awake) {
                    Text("Tắt").tag("off"); Text("Khi cắm sạc").tag("ac"); Text("Luôn luôn").tag("always")
                }.onChange(of: model.awake) { Task { await model.saveAwake() } }
            }
            Section("Các máy khác") {
                if model.machines.isEmpty { Text("Chưa có máy nào. Bấm \"Mời máy mới…\".").foregroundStyle(.secondary) }
                ForEach(model.machines, id: \.id) { m in
                    HStack {
                        Circle().fill(m.online ? .green : .red).frame(width: 8, height: 8)
                        Text(m.label)
                        Text(m.version.map { "Fleet \($0)" } ?? "chưa cài app").foregroundStyle(.secondary).font(.caption)
                        Spacer()
                        Button("Xoá", role: .destructive) { Task { await model.remove(m.id) } }
                    }
                }
                Button("Mời máy mới…", action: onInvite)
            }
            Section("Cập nhật") {
                LabeledContent("Phiên bản", value: Env.appVersion)
                Toggle("Tự cập nhật", isOn: $model.autoUpdate)
                Button("Kiểm tra cập nhật", action: onCheckUpdate)
            }
            if let msg = model.message { Text(msg).foregroundStyle(.secondary) }
        }
        .formStyle(.grouped)
        .frame(width: 520, height: 560)
        .task { if loadOnAppear { await model.load() } }
    }
}
