import Foundation
import CoreAudio

// Host endpoint control only: audio samples continue through UTM/SPICE.
enum AudioBackend {
    struct Failure: Error { let status: OSStatus }
    static func address(_ selector: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    }
    static func ids(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) throws -> [UInt32] {
        var property = address(selector, scope), size: UInt32 = 0
        var result = AudioObjectGetPropertyDataSize(object, &property, 0, nil, &size)
        guard result == noErr else { throw Failure(status: result) }
        guard size <= 65536, size % 4 == 0 else { throw Failure(status: kAudioHardwareUnspecifiedError) }
        if size == 0 { return [] }
        var values = [UInt32](repeating: 0, count: Int(size / 4))
        result = values.withUnsafeMutableBytes { AudioObjectGetPropertyData(object, &property, 0, nil, &size, $0.baseAddress!) }
        guard result == noErr else { throw Failure(status: result) }
        return Array(values.prefix(Int(size / 4)))
    }
    static func string(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) throws -> String {
        var property = address(selector)
        var value: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        let result = AudioObjectGetPropertyData(object, &property, 0, nil, &size, &value)
        guard result == noErr else { throw Failure(status: result) }
        guard let value else { throw Failure(status: kAudioHardwareUnspecifiedError) }
        return value.takeRetainedValue() as String
    }
    static func snapshot() throws -> [String: Any] {
        let system = AudioObjectID(kAudioObjectSystemObject)
        let output = try ids(system, kAudioHardwarePropertyDefaultOutputDevice).first
        let input = try ids(system, kAudioHardwarePropertyDefaultInputDevice).first
        let devices = try ids(system, kAudioHardwarePropertyDevices).map { id -> [String: Any] in
            ["uid": try string(id, kAudioDevicePropertyDeviceUID),
             "name": try string(id, kAudioObjectPropertyName),
             "input": !(try ids(id, kAudioDevicePropertyStreams, kAudioDevicePropertyScopeInput)).isEmpty,
             "output": !(try ids(id, kAudioDevicePropertyStreams, kAudioDevicePropertyScopeOutput)).isEmpty,
             "defaultInput": id == input, "defaultOutput": id == output]
        }
        return ["ok": true, "devices": devices]
    }
    static func select(uid: String, input: Bool) throws -> [String: Any] {
        let system = AudioObjectID(kAudioObjectSystemObject)
        var target: AudioObjectID?
        for id in try ids(system, kAudioHardwarePropertyDevices) {
            if try string(id, kAudioDevicePropertyDeviceUID) == uid { target = id; break }
        }
        guard var device = target,
              !(try ids(device, kAudioDevicePropertyStreams, input ? kAudioDevicePropertyScopeInput : kAudioDevicePropertyScopeOutput)).isEmpty
        else { throw Failure(status: kAudioHardwareBadDeviceError) }
        var property = address(input ? kAudioHardwarePropertyDefaultInputDevice : kAudioHardwarePropertyDefaultOutputDevice)
        var writable: DarwinBoolean = false
        let check = AudioObjectIsPropertySettable(system, &property, &writable)
        guard check == noErr, writable.boolValue else { throw Failure(status: check == noErr ? kAudioHardwareUnsupportedOperationError : check) }
        let result = AudioObjectSetPropertyData(system, &property, 0, nil, UInt32(MemoryLayout.size(ofValue: device)), &device)
        guard result == noErr else { throw Failure(status: result) }
        guard try ids(system, property.mSelector).first == device else { throw Failure(status: kAudioHardwareUnspecifiedError) }
        return try snapshot()
    }
}
