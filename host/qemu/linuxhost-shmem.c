/* SPDX-License-Identifier: GPL-2.0-or-later
 * Experimental host/guest shared RAM. No doorbells or device DMA.
 * PCI identity 1234:11f0 is local to LinuxHost, not an assigned public ID.
 */
#include "qemu/osdep.h"
#include "qapi/error.h"
#include "hw/pci/pci.h"
#include "hw/pci/pci_device.h"
#include "hw/qdev-properties.h"
#include "system/hostmem.h"
#include "migration/vmstate.h"
#include "qemu/module.h"
#define TYPE_LH_SHMEM "linuxhost-shmem"
OBJECT_DECLARE_SIMPLE_TYPE(LHShmem, LH_SHMEM)
struct LHShmem { PCIDevice parent_obj; HostMemoryBackend *memdev; bool mapped; };
static void lh_realize(PCIDevice *dev, Error **errp)
{
    LHShmem *s = LH_SHMEM(dev);
    if (!s->memdev || host_memory_backend_is_mapped(s->memdev)) {
        error_setg(errp, "linuxhost-shmem requires an unused memdev");
        return;
    }
    MemoryRegion *mr = host_memory_backend_get_memory(s->memdev);
    uint64_t size = memory_region_size(mr);
    if (size < 16384 || size > 1024ULL*1024*1024 || (size & (size-1))) {
        error_setg(errp, "linuxhost-shmem size must be power of two, 16K..1G");
        return;
    }
    host_memory_backend_set_mapped(s->memdev, true);
    s->mapped = true;
    pci_register_bar(dev, 0, PCI_BASE_ADDRESS_SPACE_MEMORY |
        PCI_BASE_ADDRESS_MEM_PREFETCH | PCI_BASE_ADDRESS_MEM_TYPE_64, mr);
}
static void lh_exit(PCIDevice *dev)
{
    LHShmem *s = LH_SHMEM(dev);
    if (s->mapped) host_memory_backend_set_mapped(s->memdev, false);
}
static const Property lh_props[] = {
    DEFINE_PROP_LINK("memdev", LHShmem, memdev, TYPE_MEMORY_BACKEND, HostMemoryBackend *),
};
static const VMStateDescription lh_vmstate = { .name = TYPE_LH_SHMEM, .unmigratable = 1 };
static void lh_class_init(ObjectClass *klass, void *data)
{
    PCIDeviceClass *k = PCI_DEVICE_CLASS(klass);
    DeviceClass *dc = DEVICE_CLASS(klass);
    k->realize = lh_realize; k->exit = lh_exit;
    k->vendor_id = 0x1234; k->device_id = 0x11f0;
    k->revision = 1; k->class_id = PCI_CLASS_MEMORY_RAM;
    dc->vmsd = &lh_vmstate;
    device_class_set_props(dc, lh_props);
    set_bit(DEVICE_CATEGORY_MISC, dc->categories);
}
static const TypeInfo lh_type = {
    .name = TYPE_LH_SHMEM, .parent = TYPE_PCI_DEVICE,
    .instance_size = sizeof(LHShmem), .class_init = lh_class_init,
    .interfaces = (const InterfaceInfo[]) { { INTERFACE_CONVENTIONAL_PCI_DEVICE }, {} },
};
static void lh_register(void) { type_register_static(&lh_type); }
type_init(lh_register)
