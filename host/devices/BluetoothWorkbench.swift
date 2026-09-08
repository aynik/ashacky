import AppKit
import CoreBluetooth
import IOBluetooth
import SystemConfiguration

final class NativeSDPProbe: NSObject {
    let id = UUID()
    weak var owner: BluetoothWorkbench?
    @objc func sdpQueryComplete(_ device: IOBluetoothDevice!, status: IOReturn) {
        DispatchQueue.main.async { self.owner?.nativeSDPComplete(self.id, device: device, result: status) }
    }
}

// Host discovery and explicitly selected pairing experiment. No radio power changes.
final class BluetoothWorkbench: NSObject, NSApplicationDelegate, CBCentralManagerDelegate, IOBluetoothDeviceInquiryDelegate, IOBluetoothDevicePairDelegate, IOBluetoothL2CAPChannelDelegate {
    var central: CBCentralManager?
    var inquiry: IOBluetoothDeviceInquiry?
    var window: NSWindow?
    var requested = false
    var scanning = false
    var scanGeneration = 0
    var lowEnergy = Set<UUID>()
    var classicCount = 0
    var classicResult: IOReturn = 0
    var devices: [IOBluetoothDevice] = []
    let choices = NSPopUpButton()
    var pairing: IOBluetoothDevicePair?
    var pairingOperationID: String?
    var connectionBusy = false
    let connectionQueue = DispatchQueue(label: "local.linuxhost.bluetooth.connection")
    var operation: [String: Any]?
    var processLock: WorkbenchProcessLock?
    var server: UnixServer?
    var scanReply: (([String: Any]) -> Void)?
    var sdpChannel: IOBluetoothL2CAPChannel?
    var sdpGeneration = 0
    var nativeProbe: NativeSDPProbe?
    var lastClassic: [[String: Any]] = []
    let status = NSTextField(wrappingLabelWithString: "Scan for Bluetooth devices, then select the device you want to pair.")
    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            let directory = CommandLine.arguments.dropFirst().first(where: { !$0.hasPrefix("--") }) ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Ashacky/devices").path
            var info = stat()
            guard lstat(directory, &info) == 0, info.st_uid == getuid(),
                  info.st_mode & 0o077 == 0, info.st_mode & S_IFMT == S_IFDIR else { throw IPCError.message("Invalid private directory") }
            processLock = try WorkbenchProcessLock(path: directory + "/bluetooth-workbench.lock")
            server = try UnixServer(path: directory + "/bluetooth-workbench.sock") { [self] fd, request in
                var uid: uid_t = 0, gid: gid_t = 0
                guard getpeereid(fd, &uid, &gid) == 0, uid == getuid() else { return ["ok": false, "error": "Unauthorized peer"] }
                let done = DispatchSemaphore(value: 0)
                var reply: [String: Any] = [:]
                DispatchQueue.main.async {
                    self.handle(request) { value in reply = value; done.signal() }
                }
                guard done.wait(timeout: .now() + 15) == .success else { return ["ok": false, "error": "Bluetooth operation timed out"] }
                return reply
            }
        } catch { NSApp.terminate(nil); return }
        if CommandLine.arguments.contains("--service") {
            if CBManager.authorization == .allowedAlways || CBManager.authorization == .notDetermined {
                central = CBCentralManager(delegate: self, queue: .main)
            }
            return
        }
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 530, height: 380), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        win.title = "LinuxHost Bluetooth test"
        status.frame = NSRect(x: 24, y: 230, width: 482, height: 130)
        let button = NSButton(title: "Allow Bluetooth and scan", target: self, action: #selector(scan))
        button.frame = NSRect(x: 24, y: 182, width: 280, height: 36)
        win.contentView?.addSubview(status); win.contentView?.addSubview(button)
        choices.frame = NSRect(x: 24, y: 138, width: 482, height: 30)
        win.contentView?.addSubview(choices)
        let pairButton = NSButton(title: "Pair selected device", target: self, action: #selector(pairSelected))
        pairButton.frame = NSRect(x: 24, y: 74, width: 170, height: 36)
        win.contentView?.addSubview(pairButton)
        let connectButton = NSButton(title: "Connect", target: self, action: #selector(connectSelected))
        connectButton.frame = NSRect(x: 200, y: 74, width: 140, height: 36)
        win.contentView?.addSubview(connectButton)
        let disconnectButton = NSButton(title: "Disconnect", target: self, action: #selector(disconnectSelected))
        disconnectButton.frame = NSRect(x: 346, y: 74, width: 160, height: 36)
        win.contentView?.addSubview(disconnectButton)
        let sdpButton = NSButton(title: "Test HID service discovery", target: self, action: #selector(testSDP))
        sdpButton.frame = NSRect(x: 24, y: 24, width: 280, height: 36)
        win.contentView?.addSubview(sdpButton)
        let nativeButton = NSButton(title: "Native SDP query", target: self, action: #selector(testNativeSDP))
        nativeButton.frame = NSRect(x: 310, y: 24, width: 196, height: 36)
        win.contentView?.addSubview(nativeButton)
        window = win; win.center(); win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        if CBManager.authorization == .allowedAlways { central = CBCentralManager(delegate: self, queue: .main) }
    }
    static func serviceUUIDs(_ device: IOBluetoothDevice) -> [String] {
        // Report only service records present in macOS's actual SDP cache.
        // These describe the remote device, not guest profile transport support.
        let profiles: [UInt16] = [0x1108, 0x110a, 0x110b, 0x110c, 0x110e, 0x1112, 0x111e, 0x111f, 0x1124]
        return profiles.compactMap { uuid in
            guard device.getServiceRecord(for: IOBluetoothSDPUUID(uuid16: uuid)) != nil else { return nil }
            return String(format: "0000%04x-0000-1000-8000-00805f9b34fb", uuid)
        }
    }
    static func active() -> Bool {
        var uid: uid_t = 0
        _ = SCDynamicStoreCopyConsoleUser(nil, &uid, nil)
        return uid == getuid()
    }
    func handle(_ request: [String: Any], completion: @escaping ([String: Any]) -> Void) {
        guard Self.active() else { completion(["ok": false, "error": "Active session required"]); return }
        if let action = request["action"] as? String, action.hasPrefix("audio-") {
            do {
                if action == "audio-devices", request.count == 1 {
                    completion(try AudioBackend.snapshot())
                } else if action == "audio-select", request.count == 3,
                          let uid = request["uid"] as? String, !uid.isEmpty, uid.utf8.count <= 1024,
                          let direction = request["direction"] as? String, ["input", "output"].contains(direction) {
                    completion(try AudioBackend.select(uid: uid, input: direction == "input"))
                } else { completion(["ok": false, "error": "Invalid audio request"]) }
            } catch let failure as AudioBackend.Failure {
                completion(["ok": false, "error": "Audio device operation failed", "status": failure.status])
            } catch { completion(["ok": false, "error": "Audio device operation failed"]) }
            return
        }
        guard CBManager.authorization == .allowedAlways else { completion(["ok": false, "error": "Active authorized session required"]); return }
        guard let action = request["action"] as? String else { completion(["ok": false, "error": "Invalid request"]); return }
        if action == "bluetooth-confirm" {
            guard request.count == 3, let id = request["operationID"] as? String,
                  let accepted = request["accepted"] as? NSNumber,
                  CFGetTypeID(accepted) == CFBooleanGetTypeID() else {
                completion(["ok": false, "error": "Invalid confirmation"]); return
            }
            guard id == pairingOperationID, operation?["confirmation"] != nil, let attempt = pairing else {
                completion(["ok": false, "error": "Pairing confirmation is no longer pending"]); return
            }
            operation?.removeValue(forKey: "confirmation")
            attempt.replyUserConfirmation(accepted.boolValue)
            completion(["ok": true]); return
        }
        if action == "bluetooth-pair" {
            guard request.count == 2, let address = request["address"] as? String,
                  address.range(of: "^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$", options: .regularExpression) != nil else {
                completion(["ok": false, "error": "Invalid request"]); return
            }
            guard !scanning, pairing == nil, !connectionBusy, central?.state == .poweredOn else {
                completion(["ok": false, "error": "Bluetooth busy or unavailable"]); return
            }
            let normalized = address.replacingOccurrences(of: "-", with: ":").uppercased()
            // Pair only devices discovered by this workbench or already paired.
            let known = devices + (IOBluetoothDevice.pairedDevices() as? [IOBluetoothDevice] ?? [])
            guard let device = known.first(where: { $0.addressString?.replacingOccurrences(of: "-", with: ":").uppercased() == normalized }) else {
                completion(["ok": false, "error": "Scan for the device first"]); return
            }
            let id = UUID().uuidString
            operation = ["id": id, "state": "pending"]
            pairingOperationID = id
            completion(["ok": true, "operation": operation!])
            if device.isPaired() { finishPairOperation(device, result: 0) }
            else { startPair(device) }
            return
        }
        if action == "bluetooth-connect" || action == "bluetooth-disconnect" {
            guard request.count == 2, let address = request["address"] as? String,
                  address.range(of: "^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$", options: .regularExpression) != nil else {
                completion(["ok": false, "error": "Invalid request"]); return
            }
            guard !scanning, pairing == nil, !connectionBusy, central?.state == .poweredOn else {
                completion(["ok": false, "error": "Bluetooth busy or unavailable"]); return
            }
            let normalized = address.replacingOccurrences(of: "-", with: ":").uppercased()
            // Resolve only actual paired host devices; never create an arbitrary target.
            guard let device = (IOBluetoothDevice.pairedDevices() as? [IOBluetoothDevice] ?? []).first(where: {
                $0.addressString?.replacingOccurrences(of: "-", with: ":").uppercased() == normalized
            }) else { completion(["ok": false, "error": "Paired device required"]); return }
            let id = UUID().uuidString
            operation = ["id": id, "state": "pending"]
            connectionBusy = true
            completion(["ok": true, "operation": operation!])
            connectionQueue.async {
                let connect = action == "bluetooth-connect"
                let result = connect ? device.openConnection(nil, withPageTimeout: 8192, authenticationRequired: true) : device.closeConnection()
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                    self.connectionBusy = false
                    let connected = device.isConnected()
                    self.operation = ["id": id, "state": "complete", "success": result == 0 && connected == connect,
                                      "result": result, "paired": device.isPaired(), "connected": connected]
                    self.status.stringValue = "Linux management request finished. Result: \(result)\nPaired: \(device.isPaired())\nConnected: \(connected)"
                }
            }
            return
        }
        guard request.count == 1 else { completion(["ok": false, "error": "Invalid request"]); return }
        if action == "bluetooth-devices" {
            var known = IOBluetoothDevice.pairedDevices() as? [IOBluetoothDevice] ?? []
            for device in devices where !known.contains(where: { $0.addressString == device.addressString }) { known.append(device) }
            let records: [[String: Any]] = known.prefix(64).compactMap { device in
                guard let address = device.addressString else { return nil }
                return ["address": address.replacingOccurrences(of: "-", with: ":").uppercased(),
                        "name": String(decoding: (device.name ?? "").utf8.prefix(80), as: UTF8.self),
                        "classOfDevice": device.classOfDevice, "paired": device.isPaired(), "connected": device.isConnected(),
                        "uuids": Self.serviceUUIDs(device)]
            }
            completion(["ok": true, "devices": records, "powered": central?.state == .poweredOn,
                        "scanning": scanning, "operation": operation ?? [:]])
        } else if action == "bluetooth-status" {
            completion(["ok": true, "powered": central?.state == .poweredOn,
                        "scanning": scanning, "cachedClassicCount": lastClassic.count])
        } else if action == "bluetooth-scan" {
            guard !scanning, pairing == nil, !connectionBusy, central?.state == .poweredOn else { completion(["ok": false, "error": "Bluetooth busy or unavailable"]); return }
            scanReply = completion
            requested = true
            beginIfReady()
        } else { completion(["ok": false, "error": "Unsupported Bluetooth operation"]) }
    }
    @objc func scan() {
        guard !scanning, pairing == nil, !connectionBusy else { return }
        requested = true
        if central == nil { central = CBCentralManager(delegate: self, queue: .main) }
        else { beginIfReady() }
    }
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        if central.state != .poweredOn && scanning { finish() }
        beginIfReady()
    }
    func beginIfReady() {
        guard requested, let central else { return }
        guard central.state == .poweredOn else {
            status.stringValue = "Bluetooth state: \(central.state.rawValue). Authorization: \(CBManager.authorization.rawValue). Enable access in macOS Privacy & Security > Bluetooth if denied."
            return
        }
        requested = false; scanning = true; lowEnergy.removeAll(); classicCount = 0
        scanGeneration += 1
        let generation = scanGeneration
        central.scanForPeripherals(withServices: nil)
        let search = IOBluetoothDeviceInquiry(delegate: self)
        search?.inquiryLength = scanReply == nil ? 8 : 4
        search?.updateNewDeviceNames = false
        inquiry = search
        classicResult = search?.start() ?? kIOReturnError
        status.stringValue = "Scanning Classic and Low Energy…"
        DispatchQueue.main.asyncAfter(deadline: .now() + (scanReply == nil ? 10 : 8)) {
            if self.scanGeneration == generation { self.finish() }
        }
    }
    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String: Any], rssi RSSI: NSNumber) {
        guard scanning else { return }
        lowEnergy.insert(peripheral.identifier)
    }
    func deviceInquiryDeviceFound(_ sender: IOBluetoothDeviceInquiry!, device: IOBluetoothDevice!) {
        guard scanning, sender === inquiry else { return }
        classicCount = sender.foundDevices().count
    }
    func deviceInquiryComplete(_ sender: IOBluetoothDeviceInquiry!, error: IOReturn, aborted: Bool) {
        guard sender === inquiry else { return }
        classicResult = error
        // Linux requests an 8-unit (10.24 second) inquiry. Return as soon as
        // the bounded Classic inquiry finishes, leaving transport margin.
        if scanning, scanReply != nil { finish() }
    }
    func finish() {
        guard scanning else { return }
        scanning = false
        central?.stopScan(); _ = inquiry?.stop()
        devices = inquiry?.foundDevices() as? [IOBluetoothDevice] ?? []
        lastClassic = devices.prefix(32).compactMap { device in
            guard let address = device.addressString else { return nil }
            return ["address": address,
                    "name": String(decoding: (device.name ?? "").utf8.prefix(80), as: UTF8.self),
                    "classOfDevice": device.classOfDevice,
                    "paired": device.isPaired(), "connected": device.isConnected()]
        }
        // Connected devices may no longer be discoverable; keep them selectable.
        for device in IOBluetoothDevice.pairedDevices() as? [IOBluetoothDevice] ?? [] {
            if !devices.contains(where: { $0.addressString == device.addressString }) { devices.append(device) }
        }
        choices.removeAllItems()
        for (index, device) in devices.enumerated() {
            choices.addItem(withTitle: "\(index + 1). \(device.name ?? "Unnamed Classic device")")
        }
        let paired = IOBluetoothDevice.pairedDevices()?.count ?? 0
        status.stringValue = "Scan stopped.\nLow Energy devices: \(lowEnergy.count)\nClassic devices: \(classicCount)\nClassic status: \(classicResult)\nKnown paired devices: \(paired)"
        inquiry = nil
        if let reply = scanReply {
            scanReply = nil
            if Self.active(), CBManager.authorization == .allowedAlways, classicResult == 0 {
                reply(["ok": true, "classicDevices": lastClassic, "lowEnergyCount": lowEnergy.count])
            } else { reply(["ok": false, "error": "Bluetooth scan unavailable"]) }
        }
    }
    @objc func pairSelected() {
        guard !scanning, pairing == nil, !connectionBusy, devices.indices.contains(choices.indexOfSelectedItem) else { return }
        startPair(devices[choices.indexOfSelectedItem])
    }
    func finishPairOperation(_ device: IOBluetoothDevice?, result: IOReturn) {
        guard let id = pairingOperationID else { return }
        pairingOperationID = nil
        operation = ["id": id, "state": "complete", "success": result == 0 && device?.isPaired() == true,
                     "result": result, "paired": device?.isPaired() ?? false, "connected": device?.isConnected() ?? false]
    }
    func startPair(_ device: IOBluetoothDevice) {
        let attempt = IOBluetoothDevicePair(device: device)
        attempt?.delegate = self
        pairing = attempt
        status.stringValue = "Pairing selected device…"
        let result = attempt?.start() ?? kIOReturnError
        if result != 0 { status.stringValue = "Pairing could not start: \(result)"; pairing = nil; finishPairOperation(device, result: result) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 60) { [weak attempt] in
            guard let attempt, self.pairing === attempt else { return }
            self.pairing = nil
            self.finishPairOperation(device, result: kIOReturnTimeout)
            attempt.stop()
            self.status.stringValue = "Pairing timed out."
        }
    }
    @objc func connectSelected() { changeConnection(connect: true) }
    @objc func disconnectSelected() { changeConnection(connect: false) }
    func changeConnection(connect: Bool) {
        guard !scanning, pairing == nil, !connectionBusy, devices.indices.contains(choices.indexOfSelectedItem) else { return }
        let device = devices[choices.indexOfSelectedItem]
        guard device.isPaired() else { status.stringValue = "Pair this device first."; return }
        connectionBusy = true
        status.stringValue = connect ? "Connecting selected device…" : "Disconnecting selected device…"
        connectionQueue.async {
            // Keep synchronous host operations away from the application UI.
            let result = connect ? device.openConnection(nil, withPageTimeout: 8192, authenticationRequired: true) : device.closeConnection()
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                self.connectionBusy = false
                self.status.stringValue = "\(connect ? "Connect" : "Disconnect") result: \(result)\nPaired: \(device.isPaired())\nConnected: \(device.isConnected())"
            }
        }
    }
    @objc func testSDP() {
        guard Self.active(), !scanning, pairing == nil, !connectionBusy,
              devices.indices.contains(choices.indexOfSelectedItem) else { return }
        let device = devices[choices.indexOfSelectedItem]
        guard device.isPaired() else { status.stringValue = "Pair this device first."; return }
        connectionBusy = true; sdpGeneration += 1
        let generation = sdpGeneration
        status.stringValue = "Opening read-only service-discovery channel…"
        let result = device.openL2CAPChannelAsync(&sdpChannel, withPSM: 1, delegate: self)
        if result != 0 { finishSDP("L2CAP open failed: \(result)") }
        DispatchQueue.main.asyncAfter(deadline: .now() + 12) {
            if generation == self.sdpGeneration, self.sdpChannel != nil { self.finishSDP("Service-discovery exchange timed out.") }
        }
    }
    @objc func testNativeSDP() {
        guard Self.active(), !scanning, pairing == nil, !connectionBusy, nativeProbe == nil,
              devices.indices.contains(choices.indexOfSelectedItem) else { return }
        let device = devices[choices.indexOfSelectedItem]
        guard device.isPaired() else { status.stringValue = "Pair this device first."; return }
        let probe = NativeSDPProbe(); probe.owner = self; nativeProbe = probe
        connectionBusy = true
        status.stringValue = "Querying services through Apple's SDP API…"
        let result = device.performSDPQuery(probe)
        if result != 0 { nativeSDPComplete(probe.id, device: device, result: result) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 12) {
            if self.nativeProbe === probe {
                // The API offers no cancellation. Retain the delegate and
                // prevent another query until its actual callback arrives.
                self.status.stringValue = "Native SDP has not completed within 12 seconds. Still waiting for macOS."
            }
        }
    }
    func nativeSDPComplete(_ id: UUID, device: IOBluetoothDevice?, result: IOReturn) {
        guard nativeProbe?.id == id else { return }
        nativeProbe = nil; connectionBusy = false
        let records = device?.services?.count ?? 0
        let hid = device?.getServiceRecord(for: IOBluetoothSDPUUID(uuid16: 0x1124)) != nil
        status.stringValue = "Native SDP result: \(result)\nService records: \(records)\nHID record present: \(hid)\nConnected: \(device?.isConnected() ?? false)"
    }
    func finishSDP(_ message: String) {
        let channel = sdpChannel; sdpChannel = nil
        _ = channel?.close()
        connectionBusy = false; status.stringValue = message
    }
    func l2capChannelOpenComplete(_ channel: IOBluetoothL2CAPChannel!, status error: IOReturn) {
        guard channel === sdpChannel else { return }
        guard error == 0 else { finishSDP("L2CAP open completion failed: \(error)"); return }
        // SDP ServiceSearchAttributeRequest, transaction 1: HID service UUID
        // 0x1124, all attributes, no continuation. No HID input/output writes.
        var request: [UInt8] = [6,0,1,0,15,0x35,3,0x19,0x11,0x24,0xff,0xff,0x35,5,0x0a,0,0,0xff,0xff,0]
        let result = request.withUnsafeMutableBytes { channel.writeSync($0.baseAddress, length: UInt16($0.count)) }
        if result != 0 { finishSDP("L2CAP write failed: \(result)") }
        else if channel === sdpChannel { status.stringValue = "Service-discovery request sent; waiting for response…" }
    }
    func l2capChannelData(_ channel: IOBluetoothL2CAPChannel!, data pointer: UnsafeMutableRawPointer!, length: Int) {
        guard channel === sdpChannel else { return }
        guard let pointer, length >= 5, length <= 16384 else { finishSDP("Invalid service-discovery response size."); return }
        let bytes = Array(UnsafeBufferPointer(start: pointer.assumingMemoryBound(to: UInt8.self), count: length))
        guard bytes[1] == 0, bytes[2] == 1, Int(bytes[3]) * 256 + Int(bytes[4]) == length - 5 else { finishSDP("Invalid service-discovery response header."); return }
        if bytes[0] == 7, length >= 8 {
            let attributes = Int(bytes[5]) * 256 + Int(bytes[6])
            guard attributes <= length - 8 else { finishSDP("Invalid service-discovery attribute length."); return }
            finishSDP("L2CAP payload exchange succeeded.\nSDP response: \(length) bytes\nAttribute bytes: \(attributes)\nChannel closed; device pairing retained.")
        } else { finishSDP("SDP returned response type \(bytes[0]); \(length) bytes received.") }
    }
    func l2capChannelClosed(_ channel: IOBluetoothL2CAPChannel!) {
        if channel === sdpChannel { finishSDP("Service-discovery channel closed before response.") }
    }
    func devicePairingUserConfirmationRequest(_ sender: Any!, numericValue: BluetoothNumericValue) {
        guard let attempt = sender as? IOBluetoothDevicePair, pairing === attempt, let window else { return }
        if pairingOperationID != nil {
            operation?["confirmation"] = numericValue
            status.stringValue = "Waiting for pairing confirmation in Linux…"
            return
        }
        let alert = NSAlert()
        alert.messageText = String(format: "Confirm pairing code %06u", numericValue)
        alert.informativeText = "Confirm only for the device you selected."
        alert.addButton(withTitle: "Confirm"); alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { response in
            if self.pairing === attempt { attempt.replyUserConfirmation(response == .alertFirstButtonReturn) }
        }
    }
    func devicePairingUserPasskeyNotification(_ sender: Any!, passkey: BluetoothPasskey) {
        guard let attempt = sender as? IOBluetoothDevicePair, pairing === attempt else { return }
        if pairingOperationID != nil { operation?["passkey"] = passkey }
        status.stringValue = String(format: "Enter %06u on the selected Bluetooth device.", passkey)
    }
    func devicePairingPINCodeRequest(_ sender: Any!) {
        // Do not guess legacy PINs. Keep this case explicit until a PIN UI exists.
        guard let attempt = sender as? IOBluetoothDevicePair, pairing === attempt else { return }
        pairing = nil
        finishPairOperation(attempt.device(), result: kIOReturnUnsupported)
        attempt.stop()
        status.stringValue = "This device requires a legacy PIN; this test does not yet support PIN entry."
    }
    func devicePairingFinished(_ sender: Any!, error: IOReturn) {
        guard let attempt = sender as? IOBluetoothDevicePair, pairing === attempt else { return }
        let device = attempt.device()
        finishPairOperation(device, result: error)
        status.stringValue = "Pairing result: \(error)\nPaired: \(device?.isPaired() ?? false)\nConnected: \(device?.isConnected() ?? false)"
        pairing = nil
    }
    func applicationWillTerminate(_ notification: Notification) { finish(); _ = sdpChannel?.close(); pairing?.stop() }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { !CommandLine.arguments.contains("--service") }
}
@main enum BluetoothMain {
    static func main() {
        let app = NSApplication.shared, delegate = BluetoothWorkbench()
        app.setActivationPolicy(CommandLine.arguments.contains("--service") ? .accessory : .regular); app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
