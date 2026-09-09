import Foundation
import IOKit.ps

/// Main-run-loop notification source; no battery polling or hardware capture.
final class PowerObserver {
    private var source: CFRunLoopSource?
    private let changed: () -> Void

    init?(_ changed: @escaping () -> Void) {
        self.changed = changed
        let context = Unmanaged.passUnretained(self).toOpaque()
        guard let source = IOPSNotificationCreateRunLoopSource({ context in
            guard let context else { return }
            Unmanaged<PowerObserver>.fromOpaque(context).takeUnretainedValue().changed()
        }, context)?.takeRetainedValue() else { return nil }
        self.source = source
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
    }

    func stop() {
        if let source {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes)
            CFRunLoopSourceInvalidate(source)
        }
        source = nil
    }

    deinit { stop() }
}
