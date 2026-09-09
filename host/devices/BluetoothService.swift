import CoreBluetooth
import IOBluetooth
import SystemConfiguration

/// Bluetooth management and host audio selection; device traffic stays on macOS.
final class BluetoothService: NSObject, CBCentralManagerDelegate, IOBluetoothDeviceInquiryDelegate, IOBluetoothDevicePairDelegate {
    var central: CBCentralManager?
    var inquiry: IOBluetoothDeviceInquiry?
    var requested = false
    var scanning = false
    var scanGeneration = 0
    var lowEnergy = Set<UUID>()
    var classicCount = 0
    var classicResult: IOReturn = 0
    var devices: [IOBluetoothDevice] = []
    var pairing: IOBluetoothDevicePair?
    var pairingOperationID: String?
    var connectionBusy = false
    let connectionQueue = DispatchQueue(label: "local.linuxhost.bluetooth.connection")
    var operation: [String: Any]?
    var processLock: ServiceProcessLock?
    var server: UnixServer?
    var scanReply: (([String: Any]) -> Void)?
    var lastClassic: [[String: Any]] = []

    func start(directory: URL) throws {
        processLock = try ServiceProcessLock(path: directory.path + "/bluetooth-workbench.lock")
        server = try UnixServer(path: directory.path + "/bluetooth-workbench.sock") { [self] fd, request in
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
        prepareCentral()
    }
    func prepareCentral(requestPermission: Bool = false) {
        guard Self.active(), central == nil else { return }
        if CBManager.authorization == .allowedAlways || (requestPermission && CBManager.authorization == .notDetermined) {
            central = CBCentralManager(delegate: self, queue: .main)
        }
    }
    func stop() { finish(); pairing?.stop() }
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
        prepareCentral()
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
            // Pair only devices discovered by this service or already paired.
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
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        if central.state != .poweredOn && scanning { finish() }
        beginIfReady()
    }
    func beginIfReady() {
        guard requested, let central else { return }
        guard central.state == .poweredOn else {
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
        inquiry = nil
        if let reply = scanReply {
            scanReply = nil
            if Self.active(), CBManager.authorization == .allowedAlways, classicResult == 0 {
                reply(["ok": true, "classicDevices": lastClassic, "lowEnergyCount": lowEnergy.count])
            } else { reply(["ok": false, "error": "Bluetooth scan unavailable"]) }
        }
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
        let result = attempt?.start() ?? kIOReturnError
        if result != 0 { pairing = nil; finishPairOperation(device, result: result) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 60) { [weak attempt] in
            guard let attempt, self.pairing === attempt else { return }
            self.pairing = nil
            self.finishPairOperation(device, result: kIOReturnTimeout)
            attempt.stop()
        }
    }
    func devicePairingUserConfirmationRequest(_ sender: Any!, numericValue: BluetoothNumericValue) {
        guard let attempt = sender as? IOBluetoothDevicePair, pairing === attempt else { return }
        guard pairingOperationID != nil else { attempt.replyUserConfirmation(false); return }
        operation?["confirmation"] = numericValue
    }
    func devicePairingUserPasskeyNotification(_ sender: Any!, passkey: BluetoothPasskey) {
        guard let attempt = sender as? IOBluetoothDevicePair, pairing === attempt else { return }
        if pairingOperationID != nil { operation?["passkey"] = passkey }
    }
    func devicePairingPINCodeRequest(_ sender: Any!) {
        // Do not guess legacy PINs. Keep this case explicit until a PIN UI exists.
        guard let attempt = sender as? IOBluetoothDevicePair, pairing === attempt else { return }
        pairing = nil
        finishPairOperation(attempt.device(), result: kIOReturnUnsupported)
        attempt.stop()
    }
    func devicePairingFinished(_ sender: Any!, error: IOReturn) {
        guard let attempt = sender as? IOBluetoothDevicePair, pairing === attempt else { return }
        let device = attempt.device()
        finishPairOperation(device, result: error)
        pairing = nil
    }
}
