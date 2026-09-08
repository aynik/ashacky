import CoreWLAN
import Foundation

// Discover the Wi-Fi hardware interface without assuming en0 or changing it.
guard let name = CWWiFiClient.shared().interface()?.interfaceName, !name.isEmpty else {
    fputs("No Wi-Fi interface found\n", stderr)
    exit(1)
}
print(name)
